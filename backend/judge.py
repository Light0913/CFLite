"""判题分发：run_judge_stage 统一入口（default/interactive/communication 走 judge.exe，
submit_answer 走 Python 侧解压比对）。"""
from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import threading

from . import config, repos, state, storage
from .utils import truncate


def _mingw_dirs() -> list[str]:
    return [str(config.MINGW_HOME / d) for d in ("include", "lib", "bin", "libexec")]


def _attachments(prob: dict, contest: dict | None) -> list[str]:
    dirs = []
    adir = pathlib.Path(prob["problem_dir"]) / "attachments"
    if adir.is_dir():
        dirs.append(str(adir))
    if contest:
        cdir = pathlib.Path(contest.get("contest_dir", "")) / "attachments"
        if cdir.is_dir():
            dirs.append(str(cdir))
    return dirs


def build_job(sub: dict, run_stage: str) -> dict:
    prob = state.problem_repo.get(sub["problem_id"])
    contest = state.contest_repo.current()
    cases = prob["cases"].get(run_stage, [])
    temp_dir = config.DIRS["temp"] / f"oj-{sub['id']}-{sub['attempt_id']}"
    return {
        "submission_id": sub["id"],
        "attempt_id": sub["attempt_id"],
        "problem_id": sub["problem_id"],
        "type": prob["type"],
        "language": sub["language"],
        "problem_dir": prob["problem_dir"],
        "testdata_dir": prob["testdata_dir"],
        "source_path": sub["source_path"],
        "time_limit_ms": prob["time_ms"],
        "memory_limit_mb": prob["memory_mb"],
        "compile_time_limit_ms": prob["compile_time_ms"],
        "gxx": str(config.GXX),
        "mingw_dirs": _mingw_dirs(),
        "attachments": _attachments(prob, contest),
        "checker": {
            "type": prob["checker_type"],
            "bin": repos.ensure_tool(prob, "checker"),
            "time_limit_ms": 3000,
            "memory_limit_mb": 512,
        },
        "interactor": {"bin": repos.ensure_tool(prob, "interactor")},
        "cases": {run_stage: [{"name": c["name"], "in": c["in"], "out": c["out"]}
                              for c in cases]},
        "run_stage": run_stage,
        "temp_dir": str(temp_dir),
    }


async def dispatch_judge_streaming(job: dict, on_event) -> dict:
    """启动 judge.exe 并流式读取 NDJSON。

    用 subprocess.Popen + 读取线程：Windows 上 asyncio.create_subprocess_exec 会走
    双工“命名管道”，在受限环境可能被拒绝；匿名管道更稳，且仍可流式读取。
    """
    payload = json.dumps(job, ensure_ascii=False).encode("utf-8")
    proc = subprocess.Popen(
        [str(config.JUDGE_EXE)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(config.ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _reader():
        try:
            proc.stdin.write(payload)
            proc.stdin.close()
        except Exception:
            pass
        try:
            for raw in iter(proc.stdout.readline, b""):
                loop.call_soon_threadsafe(q.put_nowait, ("line", raw))
        finally:
            try:
                err = proc.stderr.read()
            except Exception:
                err = b""
            try:
                rc = proc.wait()
            except Exception:
                rc = -1
            loop.call_soon_threadsafe(q.put_nowait, ("done", (rc, err)))

    threading.Thread(target=_reader, daemon=True).start()

    final_ev = None
    rc, err = -1, b""
    while True:
        kind, data = await q.get()
        if kind == "done":
            rc, err = data
            break
        line = data.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if on_event:
            await on_event(ev)
        if ev.get("event") == "final":
            final_ev = ev

    stderr = (err or b"").decode("utf-8", "replace")[:2000]
    if final_ev is None or rc != 0:
        return {
            "event": "se", "verdict": "SE", "stage": job.get("run_stage"),
            "error": f"judge exited with {rc}", "stderr": stderr,
            "tests": [], "passed": 0, "total": 0,
        }
    return final_ev


async def run_judge_stage(sub: dict, run_stage: str, on_event=None) -> dict:
    """统一判题分发。run_stage ∈ {pretests, system_tests}。"""
    if sub["problem_type"] == "submit_answer":
        return await run_submit_answer_judge(sub, run_stage, on_event)
    try:
        job = build_job(sub, run_stage)
    except Exception as e:
        return {"event": "se", "verdict": "SE", "stage": run_stage,
                "error": str(e), "tests": [], "passed": 0, "total": 0}
    return await dispatch_judge_streaming(job, on_event)


# ---------------- submit_answer ----------------
def _tokenize(s: str) -> list[str]:
    import re
    s = s.replace("\r", "")
    return re.findall(r"\S+", s)


def _strict_compare(out: str, ans: str) -> str:
    return "AC" if _tokenize(out) == _tokenize(ans) else "WA"


def _run_checker_py(checker_type: str, checker_bin: str, input_path: pathlib.Path,
                    contestant_out: pathlib.Path, answer_path: pathlib.Path) -> tuple[str, str]:
    if checker_type != "testlib" or not checker_bin:
        out = contestant_out.read_text("utf-8", errors="replace")
        ans = answer_path.read_text("utf-8", errors="replace")
        return _strict_compare(out, ans), ""
    try:
        p = subprocess.run(
            [checker_bin, str(input_path), str(contestant_out), str(answer_path)],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "SE", "checker 超时"
    mapping = {0: "AC", 1: "WA", 2: "PE"}
    verdict = mapping.get(p.returncode, "SE")
    comment = (p.stderr or p.stdout or "").strip()[:4096]
    return verdict, comment


async def run_submit_answer_judge(sub: dict, run_stage: str, on_event=None) -> dict:
    prob = state.problem_repo.get(sub["problem_id"])
    cases = prob["cases"].get(run_stage, [])
    if not cases:
        final = {
            "event": "final", "stage": run_stage,
            "verdict": "PRETESTS_PASSED" if run_stage == "pretests" else "AC",
            "tests": [], "passed": 0, "total": 0,
        }
        if on_event:
            await on_event(final)
        return final

    answer_dir = config.DIRS["temp"] / f"sa-{sub['id']}-{sub['attempt_id']}"
    storage.extract_answer_zip(pathlib.Path(sub["source_path"]), answer_dir)

    tests = []
    for i, case in enumerate(cases):
        if on_event:
            await on_event({"event": "test_start", "name": case["name"], "index": i})
        answer_file = answer_dir / f"{case['name']}.out"
        if not answer_file.exists():
            verdict, comment = "WA", f"缺失 {case['name']}.out"
        else:
            verdict, comment = _run_checker_py(
                prob["checker_type"], repos.ensure_tool(prob, "checker"),
                pathlib.Path(prob["testdata_dir"]) / case["in"],
                answer_file,
                pathlib.Path(prob["testdata_dir"]) / case["out"],
            )
        t = {"name": case["name"], "verdict": verdict, "time_ms": 0, "memory_kb": 0}
        if comment:
            t["checker_comment"] = truncate(comment)
        tests.append(t)
        if on_event:
            await on_event({"event": "test_done", **t})
        if verdict != "AC":
            final = {"event": "final", "stage": run_stage, "verdict": verdict,
                     "failed_test": case["name"], "tests": tests,
                     "passed": len(tests) - 1, "total": len(cases),
                     "checker_comment": truncate(comment)}
            if on_event:
                await on_event(final)
            return final

    final_verdict = "PRETESTS_PASSED" if run_stage == "pretests" else "AC"
    final = {"event": "final", "stage": run_stage, "verdict": final_verdict,
             "tests": tests, "passed": len(cases), "total": len(cases)}
    if on_event:
        await on_event(final)
    return final

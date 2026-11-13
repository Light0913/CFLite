"""锁题、Hack 提交与判定、成功并入 config.yaml。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import subprocess

from . import config, judge as judge_mod, repos, services, state, storage, yamlutil
from .utils import ACLIKE, now_iso, truncate

LOCK_KEYS = ("hack",)


def hack_cfg(contest: dict) -> dict:
    return contest.get("hack") or {}


def is_locked(contest_id: str, user_id: int, problem_id: int) -> bool:
    return state.db.query_one(
        "SELECT 1 FROM problem_locks WHERE contest_id=? AND user_id=? AND problem_id=?",
        (contest_id, user_id, problem_id)) is not None


def lock_problem(contest_id: str, user_id: int, problem_id: int) -> None:
    if not is_locked(contest_id, user_id, problem_id):
        state.db.execute(
            "INSERT INTO problem_locks (contest_id,user_id,problem_id,locked_at) VALUES (?,?,?,?)",
            (contest_id, user_id, problem_id, now_iso()))


def _safe_tool(problem, kind) -> str:
    try:
        return repos.ensure_tool(problem, kind)
    except Exception:
        return ""


def _valid_hack_input(problem: dict, content: bytes) -> bool:
    vb = _safe_tool(problem, "validator")
    if not vb:
        return True
    try:
        p = subprocess.run([vb], input=content, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return p.returncode == 0


def _run_reference(problem: dict, hack_in: pathlib.Path, hack_out: pathlib.Path) -> bool:
    sol = _safe_tool(problem, "solution")
    if not sol:
        return False
    try:
        with open(hack_in, "rb") as fin, open(hack_out, "wb") as fout:
            p = subprocess.run([sol], stdin=fin, stdout=fout, stderr=subprocess.DEVNULL,
                               timeout=max(30, problem["time_ms"] // 500 + 10))
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _build_hack_job(sub: dict, problem: dict, hack_in: pathlib.Path, hack_out: pathlib.Path) -> dict:
    temp_dir = config.DIRS["temp"] / f"hack-{sub['id']}-{sub['attempt_id']}"
    return {
        "submission_id": sub["id"], "attempt_id": sub["attempt_id"],
        "problem_id": problem["id"], "type": "default", "language": sub["language"],
        "problem_dir": problem["problem_dir"], "testdata_dir": problem["testdata_dir"],
        "source_path": sub["source_path"],
        "time_limit_ms": problem["time_ms"], "memory_limit_mb": problem["memory_mb"],
        "compile_time_limit_ms": problem["compile_time_ms"],
        "gxx": str(config.GXX),
        "mingw_dirs": judge_mod._mingw_dirs(),
        "attachments": judge_mod._attachments(problem, state.contest_repo.current()),
        "checker": {"type": problem["checker_type"], "bin": repos.ensure_tool(problem, "checker"),
                    "time_limit_ms": 3000, "memory_limit_mb": 512},
        "interactor": {"bin": ""},
        "cases": {"pretests": [{"name": "hack", "in": str(hack_in), "out": str(hack_out)}],
                  "system_tests": []},
        "run_stage": "pretests",
        "temp_dir": str(temp_dir),
    }


async def judge_hack(hack_id: int) -> None:
    hack = state.db.query_one("SELECT * FROM hacks WHERE id=?", (hack_id,))
    if not hack:
        return
    state.db.execute("UPDATE hacks SET status='judging' WHERE id=?", (hack_id,))
    problem = state.problem_repo.get(hack["problem_id"])
    contest = state.contest_repo.current()
    hcfg = hack_cfg(contest) if contest else {}

    hack_in = pathlib.Path(hack["input_path"])
    content = hack_in.read_bytes()

    # 1. validator
    if not _valid_hack_input(problem, content):
        _finish_hack(hack_id, "INVALID_INPUT", points=0)
        return

    # 2. 参考解生成 hack.out
    hack_out = hack_in.with_suffix(".out")
    if not _run_reference(problem, hack_in, hack_out):
        _finish_hack(hack_id, "SE", points=0, comment="参考解运行失败")
        return

    # 3. 用目标提交的 language 重跑目标 + checker
    target = state.db.query_one("SELECT * FROM submissions WHERE id=?", (hack["target_submission_id"],))
    if not target:
        _finish_hack(hack_id, "SE", points=0, comment="目标提交不存在")
        return

    async def on_event(ev):
        if ev.get("event") == "final":
            return  # 最终事件在 _finish_hack 写库后再发布
        services.sse.publish_hack(hack_id, {**ev, "hack": True})

    job = _build_hack_job(target, problem, hack_in, hack_out)
    final = await judge_mod.dispatch_judge_streaming(job, on_event)
    verdict = final.get("verdict", "SE")

    points = 0
    target_verdict = verdict
    if verdict in ("TLE", "MLE", "RE", "OLE", "WA", "PE"):
        points = int(hcfg.get("successful_hack_points", 100))
        hack_verdict = "SUCCESS"
    elif verdict in ("AC", "PRETESTS_PASSED"):
        points = -int(hcfg.get("unsuccessful_hack_penalty", 50))
        hack_verdict = "FAIL"
    else:
        points = 0
        hack_verdict = "SE"

    comment = final.get("checker_comment") or final.get("error") or ""
    _finish_hack(hack_id, hack_verdict, points=points, target_verdict=target_verdict, comment=comment)

    # 成功 Hack：标记目标提交 HACKED + 通知 victim + 并入 config.yaml
    if hack_verdict == "SUCCESS":
        state.db.execute(
            "UPDATE submissions SET hacked_by_hack_id=?, hacked_verdict=?, verdict='HACKED' WHERE id=?",
            (hack_id, target_verdict, target["id"]))
        hacker = state.user_store.get(hack["hacker_id"]) or {"id": 0, "username": "?"}
        if (contest and (contest.get("notifications") or {}).get("hack_notify", True)
                and hack["victim_id"] != hack["hacker_id"]):
            services.notify_hacked(hack["victim_id"], hack, target, hacker)
        if hcfg.get("merge_successful_into_system_tests", True):
            merge_hack_into_config(hack_id)


def _finish_hack(hack_id: int, verdict: str, points: int = 0,
                 target_verdict: str | None = None, comment: str = "") -> None:
    state.db.execute("""
        UPDATE hacks SET status='done', verdict=?, target_verdict=?, points_delta=?,
            checker_comment=?, judged_at=? WHERE id=?
    """, (verdict, target_verdict, points, truncate(comment), now_iso(), hack_id))
    services.sse.publish_hack(hack_id, {"event": "final", "hack": True,
                                        "verdict": verdict, "target_verdict": target_verdict})


# ---------------- 并入 config.yaml ----------------
def merge_hack_into_config(hack_id: int) -> None:
    hack = state.db.query_one("SELECT * FROM hacks WHERE id=?", (hack_id,))
    if not hack:
        return
    state.db.execute("UPDATE hacks SET status='merging', merge_status='merging' WHERE id=?", (hack_id,))

    def _finish(merge_status: str, error: str | None = None) -> None:
        state.db.execute(
            "UPDATE hacks SET status='done', merge_status=?, merge_error=? WHERE id=?",
            (merge_status, truncate(error or ""), hack_id))

    try:
        problem = state.problem_repo.get(hack["problem_id"])
        if not problem:
            _finish("merge_failed", "题目不存在")
            return
        testdata = pathlib.Path(problem["testdata_dir"])
        cfg_path = testdata / "config.yaml"

        hack_in = pathlib.Path(hack["input_path"])
        hack_bytes = hack_in.read_bytes()
        hack_hash = hashlib.sha256(hack_bytes).hexdigest()

        cfg = yamlutil.load(cfg_path) or {}
        cfg.setdefault("cases", {}).setdefault("system_tests", [])
        for case in cfg["cases"]["system_tests"]:
            if case.get("source") == "hack":
                p = testdata / case.get("input", "")
                if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == hack_hash:
                    _finish("duplicate")
                    state.db.execute(
                        "UPDATE hacks SET merged_into_system_tests=1, merged_at=? WHERE id=?",
                        (now_iso(), hack_id))
                    return

        in_name = f"hack_{hack_id:06d}.in"
        out_name = f"hack_{hack_id:06d}.out"
        (testdata / in_name).write_bytes(hack_bytes)
        hack_out_src = hack_in.with_suffix(".out")
        if hack_out_src.is_file():
            (testdata / out_name).write_bytes(hack_out_src.read_bytes())
        else:
            if not _run_reference(problem, testdata / in_name, testdata / out_name):
                (testdata / in_name).unlink(missing_ok=True)
                (testdata / out_name).unlink(missing_ok=True)
                _finish("solution_failed", "参考解失败")
                return

        cfg.setdefault("cases", {}).setdefault("system_tests", []).append({
            "input": in_name, "output": out_name, "source": "hack",
            "hack_id": hack_id, "created_at": now_iso(),
        })
        yamlutil.dump_rt(cfg, cfg_path)

        problem["cases"]["system_tests"].append({
            "name": f"hack_{hack_id:06d}", "in": in_name, "out": out_name,
            "source": "hack", "hack_id": hack_id,
        })
        _finish("merged")
        state.db.execute(
            "UPDATE hacks SET merged_into_system_tests=1, merged_system_test_name=?, merged_at=? WHERE id=?",
            (in_name, now_iso(), hack_id))
    except Exception as e:
        _finish("merge_failed", str(e))

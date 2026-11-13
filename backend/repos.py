"""题库 / 比赛仓库：扫描目录、解析配置、预编译工具程序。"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess

from . import config, yamlutil
from .utils import parse_dt, now_iso


def parse_time(s) -> int:
    """'1s' / '1000ms' / '1' -> 毫秒。"""
    s = str(s).strip().lower()
    if s.endswith("ms"):
        return int(float(s[:-2]))
    if s.endswith("s"):
        return int(float(s[:-1]) * 1000)
    return int(float(s) * 1000)


def parse_memory(s) -> int:
    """'256m' / '1g' -> MB。"""
    s = str(s).strip().lower()
    if s.endswith("m"):
        return int(float(s[:-1]))
    if s.endswith("g"):
        return int(float(s[:-1]) * 1024)
    if s.endswith("k"):
        return int(float(s[:-1]) / 1024)
    return int(float(s))


def _sha1(path: pathlib.Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _compile_tool(src: pathlib.Path, out: pathlib.Path) -> tuple[bool, str]:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run(
            [str(config.GXX), "-O2", "-std=c++23", "-static", "-static-libgcc",
             "-static-libstdc++", str(src), "-o", str(out)],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "编译超时"
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or "编译失败")[:2000]
    return True, ""


def ensure_tool(problem: dict, kind: str) -> str:
    """按需编译工具程序并返回可执行文件路径；无该工具时返回空串。"""
    out = problem.get(f"{kind}_bin") or ""
    src = (problem.get("_tool_src") or {}).get(kind) or ""
    if not out or not src:
        return ""
    p = pathlib.Path(out)
    if p.is_file():
        return out
    ok, err = _compile_tool(pathlib.Path(src), p)
    if not ok:
        raise ValueError(f"{kind} 编译失败: {err}")
    return out


class ProblemRepo:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.problems: dict[int, dict] = {}

    def get(self, pid: int) -> dict | None:
        return self.problems.get(int(pid))

    def all(self) -> list[dict]:
        return list(self.problems.values())

    def scan(self) -> list[str]:
        """扫描 problems/<pid>/，解析 config.yaml，预编译工具。返回错误列表。"""
        errors: list[str] = []
        self.problems.clear()
        if not self.root.is_dir():
            return errors
        for pdir in sorted(self.root.iterdir()):
            if not pdir.is_dir():
                continue
            cfg_path = pdir / "testdata" / "config.yaml"
            if not cfg_path.is_file():
                continue
            try:
                pid = int(pdir.name)
            except ValueError:
                errors.append(f"题目目录名非法（应为数字 id）: {pdir.name}")
                continue
            try:
                prob = self._load(pid, pdir, cfg_path)
            except Exception as e:
                errors.append(f"题目 {pid} 加载失败: {e}")
                continue
            self.problems[pid] = prob
        return errors

    def _load(self, pid: int, pdir: pathlib.Path, cfg_path: pathlib.Path) -> dict:
        cfg = yamlutil.load(cfg_path) or {}
        title = cfg.get("title") or pdir.name
        ptype = cfg.get("type", "default")
        if ptype not in ("default", "interactive", "communication", "submit_answer"):
            raise ValueError(f"未知题型: {ptype}")
        language = cfg.get("language", "cpp23")
        if language not in ("cpp11", "cpp14", "cpp17", "cpp20", "cpp23"):
            raise ValueError(f"未知 language: {language}")

        testdata = pdir / "testdata"
        checker_type = cfg.get("checker_type", "default")
        checker_src = cfg.get("checker", "")
        interactor_src = cfg.get("interactor", "")
        validator_src = cfg.get("validator", "")

        # 工具（checker/validator/interactor/solution）：只记录源文件与输出路径，
        # 提交/评测时才按需编译（懒编译），避免启动时逐个编译拖慢速度。
        def _out(kind_dir: str, src) -> str:
            return str(config.DIRS["build"] / kind_dir / f"{pid}-{_sha1(src)}.exe") if src else ""

        checker_bin = ""
        checker_src_path = ""
        if checker_type == "testlib" and checker_src:
            cpath = pdir / "testdata" / checker_src
            if cpath.is_file():
                checker_bin = _out("checkers", cpath)
                checker_src_path = str(cpath)
        interactor_bin = ""
        interactor_src_path = ""
        if ptype in ("interactive", "communication") and interactor_src:
            ipath = pdir / "testdata" / interactor_src
            if ipath.is_file():
                interactor_bin = _out("interactors", ipath)
                interactor_src_path = str(ipath)
        validator_bin = ""
        validator_src_path = ""
        if validator_src:
            vpath = pdir / "testdata" / validator_src
            if vpath.is_file():
                validator_bin = _out("validators", vpath)
                validator_src_path = str(vpath)
        solution_bin = ""
        solution_src_path = ""
        sol_src = pdir / "solution.cpp"
        if sol_src.is_file() and ptype != "submit_answer":
            solution_bin = _out("solutions", sol_src)
            solution_src_path = str(sol_src)

        # cases
        cases = cfg.get("cases") or {}
        pretests = self._normalize_cases(cases.get("pretests"), testdata)
        system_tests = self._normalize_cases(cases.get("system_tests"), testdata)

        attachments = []
        adir = pdir / "attachments"
        if adir.is_dir():
            attachments = sorted(f.name for f in adir.iterdir() if f.is_file())

        return {
            "id": pid, "title": title, "type": ptype, "language": language,
            "time_ms": parse_time(cfg.get("time", "1s")),
            "memory_mb": parse_memory(cfg.get("memory", "256m")),
            "compile_time_ms": parse_time(cfg.get("compile_time", "10s")),
            "checker_type": checker_type,
            "checker_bin": checker_bin,
            "interactor_bin": interactor_bin,
            "validator_bin": validator_bin,
            "solution_bin": solution_bin,
            "_tool_src": {
                "checker": checker_src_path,
                "interactor": interactor_src_path,
                "validator": validator_src_path,
                "solution": solution_src_path,
            },
            "problem_dir": str(pdir),
            "testdata_dir": str(testdata),
            "cases": {"pretests": pretests, "system_tests": system_tests},
            "attachments": attachments,
            "has_hack": ptype == "default",
        }

    @staticmethod
    def _normalize_cases(raw, testdata: pathlib.Path) -> list[dict]:
        if not raw:
            return []
        out = []
        for i, c in enumerate(raw):
            if not isinstance(c, dict):
                continue
            inp = c.get("input")
            outp = c.get("output")
            if not inp or not outp:
                continue
            name = c.get("name") or pathlib.Path(str(inp)).stem
            out.append({
                "name": str(name),
                "in": str(inp),
                "out": str(outp),
                "source": c.get("source", "static"),
                "hack_id": c.get("hack_id"),
            })
        return out


class ContestRepo:
    def __init__(self, root: pathlib.Path):
        self.root = root
        self.contests: dict[str, dict] = {}
        self.current_id: str | None = None

    def current(self) -> dict | None:
        if not self.current_id:
            return None
        return self.contests.get(self.current_id)

    def all(self) -> list[dict]:
        return list(self.contests.values())

    def scan(self) -> list[str]:
        errors: list[str] = []
        self.contests.clear()
        if not self.root.is_dir():
            return errors
        for cdir in sorted(self.root.iterdir()):
            if not cdir.is_dir():
                continue
            cfg_path = cdir / "contest.yaml"
            if not cfg_path.is_file():
                continue
            cid = cdir.name
            try:
                self.contests[cid] = self._load(cid, cdir, cfg_path)
            except Exception as e:
                errors.append(f"比赛 {cid} 加载失败: {e}")
                continue
        # 选择当前比赛：取 start_at 最晚（未结束优先）
        if self.contests:
            def key(c):
                return c.get("_start_dt")
            best = max(self.contests.values(), key=lambda c: c.get("_start_dt") or parse_dt("1970-01-01T00:00:00"))
            self.current_id = best["id"]
        return errors

    def _load(self, cid: str, cdir: pathlib.Path, cfg_path: pathlib.Path) -> dict:
        from datetime import date, datetime

        def _iso(v):
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            return str(v).strip() if v is not None else None

        cfg = yamlutil.load(cfg_path) or {}
        start_at = _iso(cfg.get("start_at"))
        end_at = _iso(cfg.get("end_at"))
        if not start_at or not end_at:
            raise ValueError("缺少 start_at / end_at")
        desc = ""
        dpath = cdir / "description.md"
        if dpath.is_file():
            desc = dpath.read_text("utf-8")
        announcements = []
        adir = cdir / "announcements"
        if adir.is_dir():
            for f in sorted(adir.glob("*.md")):
                announcements.append({"title": f.stem, "content": f.read_text("utf-8")})
        return {
            "id": cid,
            "title": cfg.get("title", cid),
            "mode": cfg.get("mode", "score"),
            "start_at": start_at,
            "end_at": end_at,
            "_start_dt": parse_dt(start_at),
            "_end_dt": parse_dt(end_at),
            "registration": cfg.get("registration") or {},
            "score": cfg.get("score") or {},
            "rejudge": cfg.get("rejudge") or {},
            "hack": cfg.get("hack") or {},
            "notifications": cfg.get("notifications") or {},
            "description": desc,
            "announcements": announcements,
            "problem_ids": [int(p.name) for p in config.DIRS["problems"].iterdir()
                            if p.is_dir() and (p / "testdata" / "config.yaml").is_file()]
            if config.DIRS["problems"].is_dir() else [],
            "contest_dir": str(cdir),
        }

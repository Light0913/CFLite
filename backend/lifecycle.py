"""比赛生命周期状态机 + 赛后自动重测。"""
from __future__ import annotations

import asyncio
import uuid

from . import config, judge as judge_mod, services, state
from .utils import ACLIKE, now_iso, parse_dt

_REJUDGE_LOCK = asyncio.Lock()


def contest_status(contest: dict) -> str:
    if not contest:
        return "finalized"
    now = parse_dt(now_iso())
    row = state.db.query_one(
        "SELECT status FROM contest_runtime WHERE contest_id=?", (contest["id"],))
    rt = row["status"] if row else None
    if now < contest["_start_dt"]:
        return "scheduled"
    if rt in ("draining", "rejudging", "finalized"):
        return rt  # 管理员提前结束比赛
    if now < contest["_end_dt"]:
        return "running"
    # 时间已过结束点：进入收尾（tick_lifecycle 会接着触发赛后重测）
    return "draining"


def ensure_runtime(contest: dict) -> None:
    row = state.db.query_one(
        "SELECT 1 FROM contest_runtime WHERE contest_id=?", (contest["id"],))
    if not row:
        state.db.execute(
            "INSERT INTO contest_runtime (contest_id,status,entered_current_at,updated_at) VALUES (?,?,?,?)",
            (contest["id"], "scheduled", now_iso(), now_iso()))


def set_status(contest_id: str, status: str, reason: str | None = None) -> None:
    state.db.execute(
        "UPDATE contest_runtime SET status=?, finalize_reason=?, updated_at=? WHERE contest_id=?",
        (status, reason, now_iso(), contest_id))


async def tick_lifecycle() -> None:
    """周期检查并推进状态机（scheduled→running→draining→rejudging→finalized）。"""
    while True:
        try:
            contest = state.contest_repo.current()
            if contest:
                ensure_runtime(contest)
                status = contest_status(contest)
                if status == "running" and parse_dt(now_iso()) >= contest["_end_dt"]:
                    set_status(contest["id"], "draining")
                    await _maybe_start_rejudge(contest)
                elif status == "draining":
                    await _maybe_start_rejudge(contest)
        except Exception:
            pass
        await asyncio.sleep(3)


async def _maybe_start_rejudge(contest: dict) -> None:
    if not (contest.get("rejudge") or {}).get("auto_trigger_after_contest", True):
        set_status(contest["id"], "finalized", "auto_rejudge_disabled")
        return
    async with _REJUDGE_LOCK:
        row = state.db.query_one("SELECT status FROM contest_runtime WHERE contest_id=?", (contest["id"],))
        if row and row["status"] == "rejudging":
            return
        if not _queue_empty(contest["id"]):
            return
        set_status(contest["id"], "rejudging")
        try:
            await run_rejudge(contest["id"])
        finally:
            set_status(contest["id"], "finalized", "rejudge_done")


def _queue_empty(cid: str) -> bool:
    # system_status='pending' 是「等待赛后重测」的积压，不算正在评测
    sub_pending = state.db.query_scalar(
        "SELECT COUNT(*) FROM submissions WHERE contest_id=? AND "
        "(pretest_status IN ('pending','judging') OR system_status = 'judging')",
        (cid,))
    hack_pending = state.db.query_scalar(
        "SELECT COUNT(*) FROM hacks WHERE contest_id=? AND status IN ('pending','judging','merging')",
        (cid,))
    return (sub_pending or 0) == 0 and (hack_pending or 0) == 0


async def run_rejudge(cid: str) -> None:
    subs = state.db.query(
        "SELECT id FROM submissions WHERE contest_id=? AND superseded=0 "
        "AND pretest_verdict IN ('AC','PRETESTS_PASSED') "
        "AND (system_status IN ('pending','skipped') OR system_verdict='AC')", (cid,))
    contest = state.contest_repo.current()
    cfg_con = (contest.get("rejudge") or {}).get("concurrency") if contest else None
    concurrency = int(cfg_con) if cfg_con else config.CPU_CORES
    sem = asyncio.Semaphore(concurrency)

    async def one(sid):
        async with sem:
            await rejudge_system(sid)

    await asyncio.gather(*(one(s["id"]) for s in subs))


async def rejudge_system(sid: int) -> None:
    old = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
    if not old:
        return
    if old.get("pretest_verdict") not in ACLIKE:
        return
    new_attempt = uuid.uuid4().hex
    state.db.execute(
        "UPDATE submissions SET system_status='judging', attempt_id=? WHERE id=?",
        (new_attempt, sid))
    await services.run_and_track(sid, new_attempt, "system_tests")
    row = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
    if row:
        services.notify_system_rejudged(row["user_id"], sid, row["verdict"] or "")

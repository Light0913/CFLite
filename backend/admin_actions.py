"""管理员操作：单提交重测 / 取消 / 恢复 / 整题重测。"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import HTTPException

from . import config, services, state
from .utils import ACLIKE, V_CANCELLED, now_iso


async def admin_rejudge_submission(sid: int, run_pretests: bool, admin: dict):
    sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
    if not sub:
        raise HTTPException(404, "提交不存在")
    if sub["status"] == "judging":
        raise HTTPException(409, "该提交正在评测中")
    new_attempt = uuid.uuid4().hex
    if run_pretests:
        state.db.execute("""
            UPDATE submissions SET status='pending', pretest_status='pending',
                system_status='pending', pretest_verdict=NULL, system_verdict=NULL,
                attempt_id=?, judged_at=NULL WHERE id=?
        """, (new_attempt, sid))
    else:
        if sub["pretest_verdict"] not in ACLIKE:
            raise HTTPException(400, "赛时未通过 pretests，无法只重测 system_tests")
        state.db.execute("""
            UPDATE submissions SET status='pending', system_status='pending',
                system_verdict=NULL, attempt_id=?, judged_at=NULL WHERE id=?
        """, (new_attempt, sid))
    state.db.write_audit_log(admin["id"], "rejudge_submission", f"submission:{sid}",
                             None, f"run_pretests={run_pretests}")
    asyncio.create_task(_run_admin_rejudge(sid, new_attempt, run_pretests))
    return {"ok": True, "attempt_id": new_attempt}


async def _run_admin_rejudge(sid: int, attempt_id: str, run_pretests: bool):
    if run_pretests:
        state.db.execute("UPDATE submissions SET pretest_status='judging' WHERE id=?", (sid,))
        await services.run_full(sid, attempt_id)
    else:
        state.db.execute("UPDATE submissions SET system_status='judging' WHERE id=?", (sid,))
        await services.run_and_track(sid, attempt_id, "system_tests")


async def admin_cancel_submission(sid: int, admin: dict):
    sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
    if not sub:
        raise HTTPException(404, "提交不存在")
    if sub["verdict"] == V_CANCELLED:
        raise HTTPException(409, "该提交已取消")
    detail = json.loads(sub["detail_json"] or "{}")
    detail["original_verdict"] = sub["verdict"]
    state.db.execute("UPDATE submissions SET verdict='CANCELLED', detail_json=? WHERE id=?",
                     (json.dumps(detail, ensure_ascii=False), sid))
    state.db.write_audit_log(admin["id"], "cancel_submission", f"submission:{sid}",
                             sub["verdict"], "CANCELLED")
    return {"ok": True}


async def admin_uncancel_submission(sid: int, admin: dict):
    sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
    if not sub or sub["verdict"] != V_CANCELLED:
        raise HTTPException(400, "该提交未被取消")
    detail = json.loads(sub["detail_json"] or "{}")
    original = detail.get("original_verdict", "AC")
    state.db.execute("UPDATE submissions SET verdict=?, detail_json=? WHERE id=?",
                     (original, json.dumps(detail, ensure_ascii=False), sid))
    state.db.write_audit_log(admin["id"], "uncancel_submission", f"submission:{sid}",
                             "CANCELLED", original)
    return {"ok": True, "restored_verdict": original}


async def admin_rejudge_problem(pid: int, run_pretests: bool, admin: dict):
    contest = state.contest_repo.current()
    if not contest:
        raise HTTPException(404, "无当前比赛")
    if run_pretests:
        subs = state.db.query(
            "SELECT id FROM submissions WHERE contest_id=? AND problem_id=? AND status != 'judging'",
            (contest["id"], pid))
        for s in subs:
            state.db.execute("""
                UPDATE submissions SET status='pending', pretest_status='pending',
                    system_status='pending', pretest_verdict=NULL, system_verdict=NULL,
                    attempt_id=? WHERE id=?
            """, (uuid.uuid4().hex, s["id"]))
    else:
        subs = state.db.query(
            "SELECT id FROM submissions WHERE contest_id=? AND problem_id=? "
            "AND pretest_verdict IN ('AC','PRETESTS_PASSED') AND system_status != 'judging'",
            (contest["id"], pid))
        for s in subs:
            state.db.execute("""
                UPDATE submissions SET status='pending', system_status='pending',
                    system_verdict=NULL, attempt_id=? WHERE id=?
            """, (uuid.uuid4().hex, s["id"]))
    state.db.write_audit_log(admin["id"], "rejudge_problem", f"problem:{pid}",
                             None, f"run_pretests={run_pretests}, count={len(subs)}")
    asyncio.create_task(_run_admin_rejudge_problem(contest["id"], pid, run_pretests))
    return {"ok": True, "count": len(subs)}


async def _run_admin_rejudge_problem(cid: str, pid: int, run_pretests: bool):
    sem = asyncio.Semaphore(config.CPU_CORES)

    if run_pretests:
        subs = state.db.query(
            "SELECT id FROM submissions WHERE contest_id=? AND problem_id=? AND status='pending'",
            (cid, pid))

        async def one(sid):
            async with sem:
                sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
                if sub:
                    await _run_admin_rejudge(sid, sub["attempt_id"], run_pretests=True)
    else:
        subs = state.db.query(
            "SELECT id FROM submissions WHERE contest_id=? AND problem_id=? AND system_status='pending'",
            (cid, pid))

        async def one(sid):
            async with sem:
                sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
                if sub:
                    state.db.execute("UPDATE submissions SET system_status='judging' WHERE id=?", (sid,))
                    await services.run_and_track(sid, sub["attempt_id"], "system_tests")

    await asyncio.gather(*(one(s["id"]) for s in subs))

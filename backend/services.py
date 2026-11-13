"""服务层：提交流水线、分数榜、通知。"""
from __future__ import annotations

import asyncio
import json
import uuid

from . import config, judge as judge_mod, scoring, sse, state
from .utils import ACLIKE, V_AC, V_CANCELLED, V_CE, V_HACKED, V_PRETESTS_PASSED, now_iso, truncate

_sem = asyncio.Semaphore(config.CPU_CORES)


# ---------------- 提交流水线 ----------------
async def run_and_track(sid: int, attempt_id: str, run_stage: str,
                        publish_final: bool = True) -> dict:
    sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
    if not sub:
        return {"verdict": "SE", "stage": run_stage}
    sub["attempt_id"] = attempt_id

    def _stage_detail() -> dict:
        row = state.db.query_one("SELECT detail_json FROM submissions WHERE id=?", (sid,))
        d = json.loads(row["detail_json"] or "{}")
        return d.get(run_stage) or {}

    async def on_event(ev):
        if ev.get("event") == "final":
            # 最终事件在 finalize 之后再统一发布，确保前端拿到时 status 已是 done
            return
        sse.publish(sid, {**ev, "stage": run_stage})
        if ev.get("event") == "test_start":
            d = json.loads(state.db.query_one("SELECT detail_json FROM submissions WHERE id=?", (sid,))["detail_json"] or "{}")
            st = d.setdefault(run_stage, {})
            st["current_test"] = ev.get("name")
            state.db.execute("UPDATE submissions SET detail_json=? WHERE id=?", (json.dumps(d, ensure_ascii=False), sid))
        elif ev.get("event") == "test_done":
            d = json.loads(state.db.query_one("SELECT detail_json FROM submissions WHERE id=?", (sid,))["detail_json"] or "{}")
            st = d.setdefault(run_stage, {})
            st.setdefault("running", []).append({
                k: ev.get(k) for k in ("name", "verdict", "time_ms", "memory_kb", "checker_comment")
            })
            st["current_test"] = None
            state.db.execute("UPDATE submissions SET detail_json=? WHERE id=?", (json.dumps(d, ensure_ascii=False), sid))

    async with _sem:
        final = await judge_mod.run_judge_stage(sub, run_stage, on_event)
    finalize_stage(sid, attempt_id, run_stage, final)
    if publish_final:
        sse.publish(sid, {**final, "stage": run_stage})
    return final


async def run_full(sid: int, attempt_id: str) -> dict:
    """比赛内完整评测：先 pretests，通过则继续 system tests，得到真正的 AC/WA。"""
    final = await run_and_track(sid, attempt_id, "pretests", publish_final=False)
    row = state.db.query_one("SELECT problem_id FROM submissions WHERE id=?", (sid,))
    prob = state.problem_repo.get(row["problem_id"]) if row else None
    has_system = bool(prob and prob["cases"].get("system_tests"))
    if final.get("verdict") in ACLIKE and has_system:
        new_attempt = uuid.uuid4().hex
        state.db.execute("UPDATE submissions SET status='pending', system_status='judging', "
                         "attempt_id=? WHERE id=?", (new_attempt, sid))
        return await run_and_track(sid, new_attempt, "system_tests")
    sse.publish(sid, {**final, "stage": "pretests"})
    return final


def finalize_stage(sid: int, attempt_id: str, run_stage: str, final: dict) -> None:
    verdict = final.get("verdict", "SE")
    passed = final.get("passed", 0)
    total = final.get("total", 0)
    t_ms = final.get("time_ms", 0)
    m_kb = final.get("memory_kb", 0)
    failed = final.get("failed_test")
    comment = truncate(final.get("checker_comment") or "")

    row = state.db.query_one("SELECT * FROM submissions WHERE id=? AND attempt_id=?", (sid, attempt_id))
    if not row:
        return
    detail = json.loads(row["detail_json"] or "{}")
    st = detail.setdefault(run_stage, {})
    st.update({
        "verdict": verdict, "passed_tests": passed, "total_tests": total,
        "time_ms": t_ms, "memory_kb": m_kb,
        "tests": final.get("tests", []), "failed_test": failed,
        "checker_comment": comment, "judged_at": now_iso(),
    })

    if run_stage == "pretests":
        system_status = "pending" if verdict in ACLIKE else "skipped"
        state.db.execute("""
            UPDATE submissions SET
                status='done', verdict=?, pretest_status='done', pretest_verdict=?,
                pretest_passed=?, pretest_total=?, system_status=?,
                time_ms=?, memory_kb=?, passed_tests=?, total_tests=?,
                failed_test=?, checker_comment=?, detail_json=?, judged_at=?
            WHERE id=? AND attempt_id=?
        """, (verdict, verdict, passed, total, system_status, t_ms, m_kb, passed, total,
              failed, comment, json.dumps(detail, ensure_ascii=False), now_iso(), sid, attempt_id))
    else:
        prev_passed = row["pretest_passed"] or 0
        prev_total = row["pretest_total"] or 0
        state.db.execute("""
            UPDATE submissions SET
                status='done', verdict=?, system_status='done', system_verdict=?,
                system_passed=?, system_total=?,
                time_ms=?, memory_kb=?, passed_tests=?, total_tests=?,
                failed_test=?, checker_comment=?, detail_json=?, judged_at=?
            WHERE id=? AND attempt_id=?
        """, (verdict, verdict, passed, total, t_ms, m_kb, prev_passed + passed,
              prev_total + total, failed, comment, json.dumps(detail, ensure_ascii=False),
              now_iso(), sid, attempt_id))

    on_submission_judged(sid)


def on_submission_judged(sid: int) -> None:
    sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
    if not sub or sub["status"] != "done":
        return
    state.db.execute("""
        UPDATE submissions SET superseded=1
        WHERE contest_id=? AND user_id=? AND problem_id=? AND id != ? AND superseded=0
    """, (sub["contest_id"], sub["user_id"], sub["problem_id"], sid))
    state.db.execute("UPDATE submissions SET superseded=0 WHERE id=?", (sid,))


# ---------------- 分数榜 ----------------
def compute_scoreboard(contest: dict) -> list[dict]:
    subs = state.db.query(
        "SELECT * FROM submissions WHERE contest_id=? ORDER BY created_at ASC", (contest["id"],))
    hacks = state.db.query(
        "SELECT hacker_id, "
        "COALESCE(SUM(points_delta),0) AS pts, "
        "COALESCE(SUM(CASE WHEN verdict='SUCCESS' THEN 1 ELSE 0 END),0) AS succ, "
        "COALESCE(SUM(CASE WHEN verdict='FAIL' THEN 1 ELSE 0 END),0) AS failcnt "
        "FROM hacks WHERE contest_id=? AND status='done' GROUP BY hacker_id", (contest["id"],))
    hack_points = {h["hacker_id"]: int(h["pts"]) for h in hacks}
    hack_success = {h["hacker_id"]: int(h["succ"]) for h in hacks}
    hack_fail = {h["hacker_id"]: int(h["failcnt"]) for h in hacks}

    score_cfg = contest.get("score") or {}
    base_scores = score_cfg.get("base_scores") or {}
    start_at = contest.get("start_at")

    users = {u["id"]: u for u in state.user_store.all()}
    locks = state.db.query("SELECT user_id, problem_id FROM problem_locks WHERE contest_id=?", (contest["id"],))
    locked_problems: dict[int, set] = {}
    for l in locks:
        locked_problems.setdefault(l["user_id"], set()).add(l["problem_id"])
    by_user: dict[int, list[dict]] = {}
    for s in subs:
        by_user.setdefault(s["user_id"], []).append(s)

    # 有提交或有 Hack 的用户都进榜
    all_uids = set(by_user.keys()) | set(hack_points.keys())

    rows = []
    for uid in all_uids:
        if users.get(uid, {}).get("type") == "banned":
            continue
        usubs = by_user.get(uid, [])
        total = int(hack_points.get(uid, 0))
        problems = {}
        first_ac = None
        by_problem: dict[int, list[dict]] = {}
        for s in usubs:
            by_problem.setdefault(s["problem_id"], []).append(s)
        for pid, psubs in by_problem.items():
            base = int(base_scores.get(pid, 0)) if base_scores else 0
            score = scoring.compute_problem_score(
                score_cfg=score_cfg, base_score=base, start_at=start_at,
                end_at=contest.get("end_at"), subs=psubs)
            total += score
            acs = [s for s in psubs
                   if s.get("verdict") in ACLIKE and not s.get("is_pretest_1_failure")]
            status = "unattempted"
            if acs:
                status = "ac"
                ac = min(acs, key=lambda s: s.get("id", 0))
                if ac.get("created_at") and (first_ac is None or ac["created_at"] < first_ac):
                    first_ac = ac["created_at"]
            else:
                current = [s for s in psubs if not s["superseded"]]
                cur = max(current, key=lambda s: s["id"]) if current else None
                if cur:
                    if cur["verdict"] == V_CANCELLED:
                        status = "cancelled"
                    elif cur["verdict"] == V_HACKED:
                        status = "hacked"
                    else:
                        status = "failed"
            problems[pid] = {
                "score": score, "status": status,
                "attempts": len([s for s in psubs if s.get("verdict") != V_CE]),
                "locked": pid in locked_problems.get(uid, set()),
            }
        rows.append({
            "user_id": uid, "username": users.get(uid, {}).get("username", f"#{uid}"),
            "total": max(0, total), "hack_points": int(hack_points.get(uid, 0)),
            "hack_success": int(hack_success.get(uid, 0)),
            "hack_fail": int(hack_fail.get(uid, 0)),
            "problems": problems, "first_ac": first_ac,
        })

    rows.sort(key=lambda r: (-r["total"], _ac_ts(r["first_ac"])))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def _ac_ts(s):
    if not s:
        return 10 ** 18
    try:
        from .utils import parse_dt
        return parse_dt(s).timestamp()
    except Exception:
        return 10 ** 18


# ---------------- 通知 ----------------
def add_notification(user_id: int, type_: str, payload: dict) -> int:
    cur = state.db.execute(
        "INSERT INTO notifications (user_id,type,payload_json,is_read,created_at) VALUES (?,?,?,0,?)",
        (user_id, type_, json.dumps(payload, ensure_ascii=False), now_iso()))
    return cur.lastrowid


def notify_hacked(victim_uid: int, hack: dict, sub: dict, hacker: dict) -> None:
    payload = {
        "type": "hacked", "submission_id": sub["id"], "problem_id": sub["problem_id"],
        "hack_id": hack["id"], "hacker_id": hacker["id"], "hacker_name": hacker["username"],
        "target_verdict": hack.get("target_verdict"), "created_at": now_iso(),
    }
    nid = add_notification(victim_uid, "hacked", payload)
    sse.publish_user(victim_uid, {
        "event": "notification", "id": nid, "type": "hacked",
        "payload": payload, "created_at": now_iso(),
    })


def notify_system_rejudged(uid: int, sid: int, verdict: str) -> None:
    payload = {"type": "system_rejudged", "submission_id": sid, "verdict": verdict,
               "created_at": now_iso()}
    nid = add_notification(uid, "system_rejudged", payload)
    sse.publish_user(uid, {
        "event": "notification", "id": nid, "type": "system_rejudged",
        "payload": payload, "created_at": now_iso(),
    })

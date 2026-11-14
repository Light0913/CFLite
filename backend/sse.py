"""SSE 发布/订阅与流式端点。"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict

_sub_subscribers: dict[int, list[asyncio.Queue]] = defaultdict(list)
_hack_subscribers: dict[int, list[asyncio.Queue]] = defaultdict(list)
_user_subscribers: dict[int, list[asyncio.Queue]] = defaultdict(list)


def _publish_to(d, key: int, ev: dict) -> None:
    for q in d.get(key, []):
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            pass


def publish(sid: int, ev: dict) -> None:
    _publish_to(_sub_subscribers, sid, ev)


def publish_hack(hid: int, ev: dict) -> None:
    _publish_to(_hack_subscribers, hid, ev)


def publish_user(uid: int, ev: dict) -> None:
    _publish_to(_user_subscribers, uid, ev)


def close_all() -> None:
    """应用关闭时向所有 SSE 订阅推送哨兵，让流正常结束（避免 graceful shutdown 超时被取消）。"""
    sentinel = {"event": "shutdown"}
    for d in (_sub_subscribers, _hack_subscribers, _user_subscribers):
        for qs in list(d.values()):
            for q in list(qs):
                try:
                    q.put_nowait(sentinel)
                except asyncio.QueueFull:
                    pass


def _sse(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def gen_submission_stream(sid: int, request, sub: dict):
    from . import state

    detail = json.loads(sub.get("detail_json") or "{}")
    for stage in ("pretests", "system_tests"):
        st = detail.get(stage) or {}
        for t in st.get("running", []):
            yield _sse("test_done", {**t, "stage": stage})
        if st.get("current_test"):
            yield _sse("test_start", {"name": st["current_test"], "stage": stage})

    if sub.get("status") == "done":
        yield _sse("final", _build_final(sub))
        return

    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _sub_subscribers[sid].append(q)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                ev = await asyncio.wait_for(q.get(), timeout=20)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if ev.get("event") == "shutdown":
                break
            yield _sse(ev.get("event", "message"), ev)
            if ev.get("event") == "final" and ev.get("stage") == "system_tests":
                break
    finally:
        try:
            _sub_subscribers[sid].remove(q)
        except ValueError:
            pass


def _build_final(sub: dict) -> dict:
    detail = json.loads(sub.get("detail_json") or "{}")
    return {
        "event": "final",
        "stage": "system_tests",
        "verdict": sub.get("verdict"),
        "tests": (detail.get("system") or {}).get("tests", []),
        "passed": sub.get("passed_tests"),
        "total": sub.get("total_tests"),
        "time_ms": sub.get("time_ms"),
        "memory_kb": sub.get("memory_kb"),
    }


async def gen_notifications_stream(uid: int, request):
    from . import state

    unread = state.db.query(
        "SELECT * FROM notifications WHERE user_id=? AND is_read=0 ORDER BY created_at DESC LIMIT 20",
        (uid,))
    for n in unread:
        yield _sse("notification", _notif_payload(n))
    yield _sse("unread_count", {"count": len(unread)})

    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _user_subscribers[uid].append(q)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                ev = await asyncio.wait_for(q.get(), timeout=30)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if ev.get("event") == "shutdown":
                break
            yield _sse(ev.get("event", "notification"), ev)
    finally:
        try:
            _user_subscribers[uid].remove(q)
        except ValueError:
            pass


def _notif_payload(n: dict) -> dict:
    try:
        payload = json.loads(n["payload_json"])
    except Exception:
        payload = {}
    return {
        "event": "notification", "id": n["id"], "type": n["type"],
        "payload": payload, "created_at": n["created_at"], "is_read": n["is_read"],
    }

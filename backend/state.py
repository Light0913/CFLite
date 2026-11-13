"""进程内共享单例状态。"""
from __future__ import annotations

from .db import Database
from .users import UserStore

db: Database | None = None
user_store: UserStore | None = None
problem_repo = None
contest_repo = None

# session_id -> user_id
sessions: dict[str, int] = {}

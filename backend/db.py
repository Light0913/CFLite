"""SQLite 数据层：标准库 sqlite3 + WAL + 线程锁。

所有写操作串行化，读操作也走同一把锁（单机规模足够快）。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import threading

from . import utils

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contest_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  problem_id INTEGER NOT NULL,
  problem_type TEXT NOT NULL,
  language TEXT NOT NULL,
  source_path TEXT NOT NULL,

  status TEXT NOT NULL DEFAULT 'pending',
  verdict TEXT,

  pretest_status TEXT NOT NULL DEFAULT 'pending',
  pretest_verdict TEXT,
  pretest_passed INTEGER,
  pretest_total INTEGER,

  system_status TEXT NOT NULL DEFAULT 'pending',
  system_verdict TEXT,
  system_passed INTEGER,
  system_total INTEGER,

  time_ms INTEGER, memory_kb INTEGER,
  passed_tests INTEGER, total_tests INTEGER,

  failed_test TEXT, failed_test_source TEXT, failed_test_hack_id INTEGER,
  checker_comment TEXT,

  detail_json TEXT,
  attempt_id TEXT NOT NULL,
  superseded INTEGER DEFAULT 0,
  hacked_by_hack_id INTEGER, hacked_verdict TEXT,

  created_at TEXT NOT NULL, judged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sub_contest_user ON submissions(contest_id, user_id, problem_id);
CREATE INDEX IF NOT EXISTS idx_sub_problem ON submissions(problem_id);
CREATE INDEX IF NOT EXISTS idx_sub_problem_created ON submissions(problem_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sub_user_created ON submissions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sub_pending ON submissions(status)
  WHERE status='pending' OR pretest_status IN ('pending','judging') OR system_status IN ('pending','judging');
CREATE INDEX IF NOT EXISTS idx_sub_contest_superseded ON submissions(contest_id, superseded);
CREATE INDEX IF NOT EXISTS idx_sub_rejudge ON submissions(contest_id, problem_id, pretest_verdict, system_status)
  WHERE superseded=0 AND pretest_verdict IN ('AC','PRETESTS_PASSED') AND system_status IN ('pending','skipped');

CREATE TABLE IF NOT EXISTS problem_locks (
  contest_id TEXT NOT NULL, user_id INTEGER NOT NULL, problem_id INTEGER NOT NULL,
  locked_at TEXT NOT NULL,
  PRIMARY KEY (contest_id, user_id, problem_id)
);
CREATE INDEX IF NOT EXISTS idx_locks_user ON problem_locks(contest_id, user_id);

CREATE TABLE IF NOT EXISTS hacks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contest_id TEXT NOT NULL,
  hacker_id INTEGER NOT NULL, victim_id INTEGER NOT NULL,
  problem_id INTEGER NOT NULL, target_submission_id INTEGER NOT NULL,
  input_path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  verdict TEXT, target_verdict TEXT, checker_comment TEXT, points_delta INTEGER,
  merged_into_system_tests INTEGER DEFAULT 0,
  merge_status TEXT,
  merged_system_test_name TEXT, merged_at TEXT, merge_error TEXT,
  attempt_id TEXT NOT NULL, created_at TEXT NOT NULL, judged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_hack_contest ON hacks(contest_id, problem_id, hacker_id);
CREATE INDEX IF NOT EXISTS idx_hack_problem ON hacks(problem_id);
CREATE INDEX IF NOT EXISTS idx_hack_target ON hacks(contest_id, target_submission_id);
CREATE INDEX IF NOT EXISTS idx_hack_hacker ON hacks(hacker_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hack_victim ON hacks(victim_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hack_pending ON hacks(status) WHERE status IN ('pending','judging','merging');
CREATE INDEX IF NOT EXISTS idx_hack_merge ON hacks(merged_into_system_tests);

CREATE TABLE IF NOT EXISTS contest_runtime (
  contest_id          TEXT PRIMARY KEY,
  status              TEXT NOT NULL DEFAULT 'scheduled',
  entered_current_at  TEXT NOT NULL,
  rejudge_started_at  TEXT,
  rejudge_total       INTEGER,
  rejudge_done        INTEGER DEFAULT 0,
  rejudge_failed      INTEGER DEFAULT 0,
  finalize_reason     TEXT,
  manual_actions_json TEXT,
  updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  is_read INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_user_unread ON notifications(user_id, is_read, created_at DESC);

CREATE TABLE IF NOT EXISTS runtime_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by INTEGER
);

CREATE TABLE IF NOT EXISTS admin_audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_id    INTEGER NOT NULL,
  action      TEXT NOT NULL,
  target      TEXT,
  old_value   TEXT,
  new_value   TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_admin ON admin_audit_log(admin_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON admin_audit_log(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target ON admin_audit_log(target, created_at DESC);

CREATE TABLE IF NOT EXISTS contest_registrations (
  contest_id TEXT NOT NULL,
  user_id    INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (contest_id, user_id)
);
"""


class Database:
    def __init__(self, path: pathlib.Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- generic ----------
    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def executemany(self, sql: str, seq) -> None:
        with self._lock:
            self._conn.executemany(sql, seq)
            self._conn.commit()

    def query(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def query_scalar(self, sql: str, params=()):
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def last_insert_id(self) -> int:
        return self.query_scalar("SELECT last_insert_rowid()")

    def transaction(self):
        return _Tx(self)

    # ---------- runtime settings ----------
    def get_runtime_setting(self, key: str, default=None):
        row = self.query_one("SELECT value FROM runtime_settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def get_runtime_setting_meta(self, key: str) -> dict:
        row = self.query_one(
            "SELECT updated_at, updated_by FROM runtime_settings WHERE key=?", (key,))
        return dict(row) if row else {}

    def set_runtime_setting(self, key: str, value, updated_by=None) -> None:
        now = utils.now_iso()
        self.execute("""
            INSERT INTO runtime_settings (key, value, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value, updated_at=excluded.updated_at,
                updated_by=excluded.updated_by
        """, (key, json.dumps(value, ensure_ascii=False), now, updated_by))

    def write_audit_log(self, admin_id, action, target, old_value, new_value) -> None:
        self.execute("""
            INSERT INTO admin_audit_log
                (admin_id, action, target, old_value, new_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (admin_id, action, target, old_value, new_value, utils.now_iso()))


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN IMMEDIATE")
        return self.db._conn

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.db._conn.commit()
            else:
                self.db._conn.rollback()
        finally:
            self.db._lock.release()
        return False

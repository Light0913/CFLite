"""通用工具：时间、verdict 常量、安全文件名等。"""
from __future__ import annotations

import re
from datetime import date, datetime

# ---------------- Verdict ----------------
V_AC = "AC"
V_PRETESTS_PASSED = "PRETESTS_PASSED"
V_WA = "WA"
V_PE = "PE"
V_RE = "RE"
V_OLE = "OLE"
V_TLE = "TLE"
V_MLE = "MLE"
V_CE = "CE"
V_SE = "SE"
V_HACKED = "HACKED"
V_CANCELLED = "CANCELLED"
V_WAITING = "WAITING"
V_JUDGING = "JUDGING"
V_SKIPPED = "SKIPPED"

# 通过类 verdict（视为 AC）
ACLIKE = {V_AC, V_PRETESTS_PASSED}
# 失败类 verdict（赛时/赛后均可出现）
FAIL_VERDICTS = {V_WA, V_PE, V_RE, V_OLE, V_TLE, V_MLE, V_CE, V_SE, V_HACKED}

VERDICT_CLASS = {
    V_AC: "verdict-ac",
    V_PRETESTS_PASSED: "verdict-ac",
    V_WA: "verdict-fail",
    V_PE: "verdict-fail",
    V_RE: "verdict-fail",
    V_OLE: "verdict-fail",
    V_HACKED: "verdict-fail",
    V_TLE: "verdict-tle",
    V_MLE: "verdict-tle",
    V_CE: "verdict-ce",
    V_SE: "verdict-se",
    V_CANCELLED: "verdict-gray",
    V_SKIPPED: "verdict-gray",
    V_WAITING: "verdict-waiting",
    V_JUDGING: "verdict-waiting",
}


def verdict_class(v: str | None) -> str:
    return VERDICT_CLASS.get(v or "", "verdict-gray")


# verdict 的可读名称（不显示缩写/宏）
VERDICT_LABEL = {
    V_AC: "Accepted",
    V_PRETESTS_PASSED: "Pretests Passed",
    V_WA: "Wrong Answer",
    V_PE: "Presentation Error",
    V_RE: "Runtime Error",
    V_OLE: "Output Limit Exceeded",
    V_TLE: "Time Limit Exceeded",
    V_MLE: "Memory Limit Exceeded",
    V_CE: "Compilation Error",
    V_SE: "System Error",
    V_HACKED: "Hacked",
    V_CANCELLED: "Cancelled",
    V_WAITING: "Waiting",
    V_JUDGING: "Judging",
    V_SKIPPED: "Skipped",
    "SUCCESS": "Successful Hack",
    "FAIL": "Unsuccessful Hack",
    "INVALID_INPUT": "Invalid Input",
}


def verdict_label(v: str | None) -> str:
    if not v:
        return "—"
    return VERDICT_LABEL.get(v, v)


# ---------------- Time ----------------
def now_local() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    """本地时间 ISO 字符串（秒级），用于 SQLite 存储。"""
    return now_local().strftime("%Y-%m-%dT%H:%M:%S")


def parse_dt(s) -> datetime:
    """解析带/不带时区的时间，返回本地 aware datetime。

    兼容 pyyaml 直接把时间解析成 datetime / date 对象的情况。
    """
    if isinstance(s, datetime):
        dt = s
    elif isinstance(s, date):
        dt = datetime(s.year, s.month, s.day)
    else:
        s = str(s).strip()
        if not s:
            raise ValueError("空时间")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # 视为本地时间
        dt = dt.astimezone()
    else:
        dt = dt.astimezone()
    return dt


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------- Filename ----------------
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,200}$")
_DENY_CHARS = set(r'/\:*?"<>|')


def safe_basename(name: str) -> bool:
    """附件/文件名合法性：纯 basename + 无路径分隔符。"""
    if not name or name in ("", ".", ".."):
        return False
    if any(c in name for c in _DENY_CHARS):
        return False
    if name != name.replace("\\", "").replace("/", ""):
        return False
    if "\x00" in name:
        return False
    return True


def truncate(s: str, limit: int = 4096) -> str:
    if s is None:
        return ""
    if len(s) > limit:
        return s[:limit] + f"\n...[截断，共 {len(s)} 字符]"
    return s

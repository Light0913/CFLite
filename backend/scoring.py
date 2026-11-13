"""CF Div.2 分数制。

CF 官方公式：分数 = max(round(0.3*M), M − M*T/Ctime − 50*W)
  M      = 初始满分
  T      = 距离开赛分钟数
  Ctime  = D*25/12（D = 比赛时长）
  W      = 首次 AC 之前的错误提交数
"""
from __future__ import annotations

from . import utils

# 不计入错误次数的 verdict（CE / HACKED / CANCELLED）
_IGNORE_FOR_WRONG = {utils.V_CE, utils.V_HACKED, utils.V_CANCELLED}


def is_passed(s: dict) -> bool:
    return s.get("verdict") in utils.ACLIKE


def _minutes_since(start_at, at) -> int:
    try:
        start_dt = utils.parse_dt(start_at) if isinstance(start_at, str) else start_at
        at_dt = utils.parse_dt(at) if isinstance(at, str) else (at or utils.parse_dt(utils.now_iso()))
        return int((at_dt - start_dt).total_seconds() // 60)
    except Exception:
        return 0


def _duration_minutes(score_cfg: dict, start_at, end_at) -> int:
    """比赛时长 D：优先实际窗口（end - start），否则 duration_minutes。"""
    try:
        s = utils.parse_dt(start_at) if isinstance(start_at, str) else start_at
        e = utils.parse_dt(end_at) if isinstance(end_at, str) else end_at
        if s and e and e > s:
            return max(1, int((e - s).total_seconds() // 60))
    except Exception:
        pass
    return max(1, int(score_cfg.get("duration_minutes") or 120))


def decayed_full_score(*, score_cfg: dict, base_score: int, start_at, end_at=None, at=None) -> int:
    """CF 衰减：M − M*T/Ctime，向下由 floor 兜底。"""
    x = int(base_score)
    if x <= 0:
        return 0
    decay = score_cfg.get("decay") or {}
    if not decay.get("enabled"):
        return x
    d = _duration_minutes(score_cfg, start_at, end_at)
    floor = int(float(decay.get("floor_ratio", 0.3)) * x)
    t = max(_minutes_since(start_at, at), 0)
    # M − M*T/(D*25/12) == M − (12*M*T)/(25*D)
    decayed = x - (120 * x * t) // (250 * d)
    return max(floor, decayed)


def compute_problem_score(*, score_cfg: dict, base_score: int, start_at, end_at=None,
                          subs: list[dict]) -> int:
    """以首次 AC 为准计分，扣掉首 AC 前的错误次数 * 罚分，下限为 floor。"""
    acs = [s for s in subs
           if s.get("verdict") in utils.ACLIKE and not s.get("is_pretest_1_failure")]
    if not acs:
        return 0
    ac = min(acs, key=lambda s: s.get("id", 0))
    ac_id = ac.get("id", 0)
    wrong = sum(1 for s in subs
                if s.get("id", 0) < ac_id and s.get("verdict") not in _IGNORE_FOR_WRONG)

    x = int(base_score)
    w = int(score_cfg.get("wrong_penalty") or 50)
    decay = score_cfg.get("decay") or {}
    if decay.get("enabled"):
        full = decayed_full_score(score_cfg=score_cfg, base_score=x, start_at=start_at,
                                  end_at=end_at, at=ac.get("created_at"))
        floor = int(float(decay.get("floor_ratio", 0.3)) * x)
        return max(floor, full - w * wrong)
    return max(0, x - w * wrong)


def compute_total_score(contest: dict, user_id: int, subs: list[dict], hack_points: int) -> int:
    """总分 = Σ 各题得分 + Hack 净得分。"""
    score_cfg = contest.get("score") or {}
    base_scores = score_cfg.get("base_scores") or {}
    start_at = contest.get("start_at")
    end_at = contest.get("end_at")
    by_problem: dict[int, list[dict]] = {}
    for s in subs:
        if s.get("user_id") != user_id:
            continue
        by_problem.setdefault(s.get("problem_id"), []).append(s)
    total = int(hack_points or 0)
    for pid, psubs in by_problem.items():
        base = int(base_scores.get(pid, 0)) if base_scores else 0
        total += compute_problem_score(
            score_cfg=score_cfg, base_score=base, start_at=start_at, end_at=end_at, subs=psubs)
    return total

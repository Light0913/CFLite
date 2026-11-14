"""FastAPI 应用与全部路由。"""
from __future__ import annotations

import asyncio
import base64
import html
import io
import json
import os
import pathlib
import secrets
import shutil
import time
import uuid
import zipfile

from fastapi import (BackgroundTasks, Depends, FastAPI, File, Form, HTTPException,
                     Request, UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import (codeimage, config, hack, judge as judge_mod, lifecycle, minimd,
               repos, scoring, services, sse, state, storage)
from .db import Database
from .users import USERNAME_RE, UserStore
from .utils import (ACLIKE, V_AC, V_CANCELLED, V_CE, V_HACKED,
                    V_PRETESTS_PASSED, now_iso, parse_dt, verdict_class, verdict_label)

templates = Jinja2Templates(directory=str(config.DIRS["templates"]))
templates.env.filters["verdict_class"] = verdict_class
templates.env.filters["verdict_name"] = verdict_label
_BADGE_CLASS = {"verdict-ac": "badge-ac", "verdict-fail": "badge-fail",
                "verdict-tle": "badge-tle", "verdict-ce": "badge-ce", "verdict-se": "badge-se"}
templates.env.filters["badge_class"] = lambda v: _BADGE_CLASS.get(verdict_class(v), "badge-gray")
templates.env.filters["markdown"] = lambda s: Markup(minimd.render(s or ""))

_PROC_START = time.time()
_CODE_SUFFIXES = (".py", ".html", ".css", ".js")
EXIT_RESTART = 42  # run.bat 看到该退出码会自动拉起新进程


def _code_changed(since: float) -> bool:
    """检查 backend/*.py、main.py、web/ 下代码是否有晚于进程启动的修改。"""
    candidates = list((config.ROOT / "backend").glob("*.py"))
    candidates.append(config.ROOT / "main.py")
    for p in config.DIRS["web"].rglob("*"):
        if p.is_file() and p.suffix in _CODE_SUFFIXES:
            candidates.append(p)
    for p in candidates:
        try:
            if p.stat().st_mtime > since:
                return True
        except Exception:
            pass
    return False


def _restart_now() -> None:
    """由 BackgroundTasks 调用：先让响应发出，再退出进程由 run.bat 重启。"""
    time.sleep(0.8)
    os._exit(EXIT_RESTART)

_bg_tasks: set = set()


def _spawn(coro):
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


# ---------------- 认证 ----------------
async def current_user(request: Request) -> dict | None:
    sid = request.cookies.get("session_id")
    if not sid:
        return None
    uid = state.sessions.get(sid)
    if not uid:
        return None
    u = state.user_store.get(uid)
    if not u:
        state.sessions.pop(sid, None)
        return None
    return u


async def require_login(u: dict | None = Depends(current_user)):
    if not u:
        raise HTTPException(401, "请先登录")
    return u


async def require_writable(u: dict = Depends(require_login)):
    if u["type"] == "banned":
        raise HTTPException(403, "账号已被封禁")
    return u


async def require_admin(u: dict = Depends(require_login)):
    if u["type"] != "admin":
        raise HTTPException(403, "需要管理员权限")
    return u


# ---------------- 注册开关 ----------------
def registration_open(contest: dict | None) -> tuple[bool, str]:
    if not contest:
        return False, "无当前比赛"
    reg = contest.get("registration") or {}
    enabled = state.db.get_runtime_setting("registration.enabled", reg.get("enabled", False))
    meta = state.db.get_runtime_setting_meta("registration.enabled") or {}
    if meta.get("updated_by") is not None:
        # 管理员在设置页手动开关过：以手动开关为准，可越过 close_at 截止时间
        return (True, "") if enabled else (False, "注册通道已关闭")
    close_at = reg.get("close_at")
    if close_at:
        try:
            if parse_dt(now_iso()) >= parse_dt(close_at):
                return False, "注册通道已关闭（已到截止时间）"
        except Exception:
            pass
    if not enabled:
        return False, "注册通道已关闭"
    return True, ""


def _asset_ver() -> int:
    """静态资源版本号（按 css/js 的修改时间），用于浏览器缓存刷新。"""
    v = 0
    for name in ("css/style.css", "js/app.js"):
        try:
            v = max(v, int((config.DIRS["static"] / name).stat().st_mtime))
        except Exception:
            pass
    return v


def _ctx(request: Request, u: dict | None = None, **kw) -> dict:
    contest = state.contest_repo.current()
    status = lifecycle.contest_status(contest) if contest else "finalized"
    reg_ok, reg_reason = registration_open(contest)
    ctx = {
        "request": request, "current_user": u, "contest": contest,
        "contest_status": status, "registration_enabled": reg_ok,
        "registration_reason": reg_reason, "show_contest_nav": True,
        "asset_ver": _asset_ver(),
    }
    ctx.update(kw)
    return ctx


def is_registered(contest_id: str, uid: int) -> bool:
    return state.db.query_one(
        "SELECT 1 FROM contest_registrations WHERE contest_id=? AND user_id=?",
        (contest_id, uid)) is not None


def can_access_problems(viewer, contest) -> bool:
    status = lifecycle.contest_status(contest) if contest else "finalized"
    if status == "finalized":
        return True
    if not viewer:
        return False
    if viewer["type"] == "admin":
        return True
    if status == "scheduled":
        return False  # 比赛开始前，普通用户（即使已报名）也看不到题目
    return is_registered(contest["id"], viewer["id"])


def _set_session(resp: Response, uid: int) -> None:
    sid = secrets.token_urlsafe(32)
    state.sessions[sid] = uid
    resp.set_cookie("session_id", sid, httponly=True, samesite="lax", max_age=86400)


# ---------------- 权限 ----------------
def can_view_source(viewer, sub, contest) -> bool:
    status = lifecycle.contest_status(contest) if contest else "finalized"
    if status in ("draining", "rejudging", "finalized"):
        return True
    if viewer and (viewer["type"] == "admin" or viewer["id"] == sub["user_id"]):
        return True
    # 比赛进行中：锁题者可查看他人该题的通过代码
    if viewer and contest and status == "running":
        try:
            if (hack.is_locked(contest["id"], viewer["id"], sub["problem_id"])
                    and sub.get("verdict") in ACLIKE
                    and not sub.get("superseded")
                    and not sub.get("hacked_by_hack_id")):
                return True
        except Exception:
            pass
    return False


def can_view_hack(viewer, hack_row, contest) -> bool:
    if viewer:
        return True
    # 匿名用户在比赛结束后（含结束后评测期）可查看
    status = lifecycle.contest_status(contest) if contest else "finalized"
    return status in ("draining", "rejudging", "finalized")


def _sub_view(sid: int) -> dict | None:
    sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
    if not sub:
        return None
    detail = json.loads(sub["detail_json"] or "{}")
    sub["pretest"] = detail.get("pretests")
    sub["system"] = detail.get("system_tests")
    sub["history"] = detail.get("history", [])
    prob = state.problem_repo.get(sub["problem_id"])
    sub["problem_title"] = prob["title"] if prob else f"#{sub['problem_id']}"
    u = state.user_store.get(sub["user_id"])
    sub["username"] = u["username"] if u else f"#{sub['user_id']}"
    return sub


def create_app() -> FastAPI:
    config.ensure_dirs()
    state.db = Database(config.DB_FILE)
    state.user_store = UserStore(config.USERS_FILE)
    state.problem_repo = repos.ProblemRepo(config.DIRS["problems"])
    state.contest_repo = repos.ContestRepo(config.DIRS["contests"])

    problems_errors = state.problem_repo.scan()
    contest_errors = state.contest_repo.scan()
    for e in problems_errors + contest_errors:
        print(f"[warn] {e}")

    contest = state.contest_repo.current()
    if contest:
        lifecycle.ensure_runtime(contest)
        if state.db.query_one("SELECT 1 FROM runtime_settings WHERE key='registration.enabled'") is None:
            state.db.set_runtime_setting("registration.enabled",
                                         contest.get("registration", {}).get("enabled", False))

    app = FastAPI(title="CFLite", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(config.DIRS["static"])), name="static")

    @app.middleware("http")
    async def banned_guard(request: Request, call_next):
        """被封禁的用户直接拒绝访问（登录、登出、静态资源除外）。"""
        sid = request.cookies.get("session_id")
        if sid:
            uid = state.sessions.get(sid)
            u = state.user_store.get(uid) if uid else None
            if u and u.get("type") == "banned":
                path = request.url.path
                if path not in ("/login", "/api/login", "/api/logout") and not path.startswith("/static/"):
                    return HTMLResponse(
                        "<!doctype html><meta charset='utf-8'><title>403</title>"
                        "<h2>403 · 账号已被封禁</h2><p>如有疑问请联系管理员。</p>"
                        "<p><a href='/login'>返回登录</a></p>", status_code=403)
        return await call_next(request)

    _register_routes(app)

    @app.on_event("startup")
    async def _startup():
        _spawn(lifecycle.tick_lifecycle())

    return app


# ---------------- 路由 ----------------
def _register_routes(app: FastAPI) -> None:
    # ========== 认证 ==========
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: str = ""):
        return templates.TemplateResponse(request, "login.html", _ctx(request, None, error=error))

    @app.post("/api/login")
    async def login(username: str = Form(...), password: str = Form(...)):
        u = state.user_store.authenticate(username, password)
        if not u:
            from urllib.parse import quote
            return RedirectResponse("/login?error=" + quote("用户名或密码错误"), status_code=303)
        if u.get("type") == "banned":
            from urllib.parse import quote
            return RedirectResponse("/login?error=" + quote("账号已被封禁"), status_code=303)
        resp = RedirectResponse("/", status_code=303)
        _set_session(resp, u["id"])
        return resp

    @app.get("/register", response_class=HTMLResponse)
    async def register_page(request: Request, error: str = ""):
        return templates.TemplateResponse(request, "register.html", _ctx(request, None, error=error))

    @app.post("/api/register")
    async def register(request: Request, username: str = Form(...),
                       password: str = Form(...), password_confirm: str = Form(...)):
        from urllib.parse import quote
        contest = state.contest_repo.current()
        ok, reason = registration_open(contest)
        if not ok:
            return RedirectResponse("/register?error=" + quote(reason), status_code=303)
        if not USERNAME_RE.match(username):
            return RedirectResponse("/register?error=" + quote("用户名只能包含字母数字 _ -，长度 1-32"), status_code=303)
        reg = contest.get("registration") or {}
        pmin = int(reg.get("min_password_length", 6))
        pmax = int(reg.get("max_password_length", 64))
        if not (pmin <= len(password) <= pmax):
            return RedirectResponse("/register?error=" + quote(f"密码长度 {pmin}-{pmax}"), status_code=303)
        if password != password_confirm:
            return RedirectResponse("/register?error=" + quote("两次密码不一致"), status_code=303)
        if state.user_store.get_by_name(username):
            return RedirectResponse("/register?error=" + quote("用户名已存在"), status_code=303)
        try:
            u = state.user_store.create(username, password, "default", min_len=pmin, max_len=pmax)
        except ValueError as e:
            return RedirectResponse("/register?error=" + quote(str(e)), status_code=303)
        resp = RedirectResponse("/", status_code=303)
        _set_session(resp, u["id"])
        return resp

    @app.get("/api/registration/status")
    async def registration_status():
        contest = state.contest_repo.current()
        ok, reason = registration_open(contest)
        return {"enabled": ok, "reason": reason}

    @app.post("/api/logout")
    async def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("session_id")
        return resp

    # ========== 首页 ==========
    @app.get("/")
    async def index(request: Request, u: dict | None = Depends(current_user)):
        if not u:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "home.html",
                                          _ctx(request, u, show_contest_nav=False))

    # ========== 账号设置 ==========
    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request, u: dict = Depends(require_login)):
        return templates.TemplateResponse(request, "settings.html", _ctx(request, u))

    @app.post("/api/settings/username")
    async def change_username(new_username: str = Form(...), current_password: str = Form(...),
                              u: dict = Depends(require_login)):
        try:
            state.user_store.update_username(u["id"], new_username, current_password)
        except PermissionError:
            raise HTTPException(400, "当前密码错误")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse("/settings?ok=username", status_code=303)

    @app.post("/api/settings/password")
    async def change_password(current_password: str = Form(...), new_password: str = Form(...),
                              new_password_confirm: str = Form(...), u: dict = Depends(require_login)):
        if new_password != new_password_confirm:
            raise HTTPException(400, "两次新密码不一致")
        try:
            state.user_store.update_password(u["id"], current_password, new_password)
        except PermissionError:
            raise HTTPException(400, "当前密码错误")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse("/settings?ok=password", status_code=303)

    # ========== 比赛/题目 ==========
    @app.get("/contests/{cid}", response_class=HTMLResponse)
    async def contest_page(request: Request, cid: str, u: dict | None = Depends(current_user)):
        contest = state.contest_repo.current()
        if not contest or contest["id"] != cid:
            raise HTTPException(404)
        status = lifecycle.contest_status(contest)
        problems = state.problem_repo.all() if can_access_problems(u, contest) else []
        score_cfg = contest.get("score") or {}
        base_scores = score_cfg.get("base_scores") or {}
        for p in problems:
            p["base_score"] = int(base_scores.get(p["id"], 0))
            p["full_score"] = scoring.decayed_full_score(
                score_cfg=score_cfg, base_score=p["base_score"],
                start_at=contest.get("start_at"), end_at=contest.get("end_at"))
        registered = bool(u and is_registered(contest["id"], u["id"]))
        return templates.TemplateResponse(request, "contest.html", _ctx(request, u,
            problems=problems, status=status, registered=registered))

    @app.post("/api/contests/{cid}/register")
    async def register_contest(cid: str, u: dict = Depends(require_login)):
        contest = state.contest_repo.current()
        if not contest or contest["id"] != cid:
            raise HTTPException(404)
        if lifecycle.contest_status(contest) == "finalized":
            raise HTTPException(400, "比赛已结束")
        state.db.execute(
            "INSERT OR IGNORE INTO contest_registrations (contest_id,user_id,created_at) VALUES (?,?,?)",
            (cid, u["id"], now_iso()))
        return RedirectResponse(f"/contests/{cid}", status_code=303)

    @app.get("/contests/{cid}/problems", response_class=HTMLResponse)
    async def problems_page(request: Request, cid: str, u: dict | None = Depends(current_user)):
        contest = state.contest_repo.current()
        if not contest or contest["id"] != cid:
            raise HTTPException(404)
        if not can_access_problems(u, contest):
            return RedirectResponse("/login" if not u else f"/contests/{contest['id']}", status_code=303)
        problems = state.problem_repo.all()
        score_cfg = contest.get("score") or {}
        base_scores = score_cfg.get("base_scores") or {}
        for p in problems:
            p["base_score"] = int(base_scores.get(p["id"], 0))
            p["full_score"] = scoring.decayed_full_score(
                score_cfg=score_cfg, base_score=p["base_score"],
                start_at=contest.get("start_at"), end_at=contest.get("end_at"))
        return templates.TemplateResponse(request, "problems.html", _ctx(request, u,
            problems=problems))

    @app.get("/problems/{pid}", response_class=HTMLResponse)
    async def problem_page(request: Request, pid: int, u: dict | None = Depends(current_user)):
        prob = state.problem_repo.get(pid)
        if not prob:
            raise HTTPException(404)
        contest = state.contest_repo.current()
        status = lifecycle.contest_status(contest) if contest else "finalized"
        if not can_access_problems(u, contest):
            return RedirectResponse("/login" if not u else f"/contests/{contest['id']}", status_code=303)
        locked = (u is not None and hack.is_locked(contest["id"], u["id"], pid)) if (u and contest) else False
        my_ac = False
        if u and contest:
            row = state.db.query_one(
                "SELECT verdict FROM submissions WHERE contest_id=? AND user_id=? AND problem_id=? "
                "AND superseded=0 ORDER BY id DESC LIMIT 1", (contest["id"], u["id"], pid))
            my_ac = bool(row and row["verdict"] in ACLIKE)
        sp = pathlib.Path(prob["problem_dir"]) / "statement.md"
        statement = sp.read_text("utf-8", errors="replace") if sp.is_file() else ""
        return templates.TemplateResponse(request, "problem.html", _ctx(request, u,
            prob=prob, status=status, locked=locked, my_ac=my_ac, statement=statement))

    @app.get("/contests/{cid}/scoreboard", response_class=HTMLResponse)
    async def scoreboard_page(request: Request, cid: str, u: dict | None = Depends(current_user)):
        contest = state.contest_repo.current()
        if not contest or contest["id"] != cid:
            raise HTTPException(404)
        rows = services.compute_scoreboard(contest)
        problems = state.problem_repo.all()
        base_scores = (contest.get("score") or {}).get("base_scores") or {}
        for p in problems:
            p["base_score"] = int(base_scores.get(p["id"], 0))
        return templates.TemplateResponse(request, "scoreboard.html", _ctx(request, u,
            rows=rows, problems=problems))

    # ========== 提交 ==========
    @app.post("/api/submit")
    async def submit(request: Request, pid: int = Form(...), code: str = Form(""),
                     file: UploadFile | None = File(None), u: dict = Depends(require_writable)):
        contest = state.contest_repo.current()
        status = lifecycle.contest_status(contest) if contest else "finalized"
        if not contest or status == "scheduled":
            raise HTTPException(403, "比赛未开始")
        prob = state.problem_repo.get(pid)
        if not prob:
            raise HTTPException(404, "题目不存在")
        if status == "running" and hack.is_locked(contest["id"], u["id"], pid):
            raise HTTPException(403, "该题已锁定，只能提交 Hack，不能再提交解答")

        now = now_iso()
        attempt = uuid.uuid4().hex
        if prob["type"] == "submit_answer":
            if not file:
                raise HTTPException(400, "请上传 answer.zip")
            content = await file.read()
            if not content or len(content) > 100 * 1024 * 1024:
                raise HTTPException(400, "文件为空或超过 100MB")
            cur = state.db.execute(
                "INSERT INTO submissions (contest_id,user_id,problem_id,problem_type,language,source_path,"
                "status,attempt_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (contest["id"], u["id"], pid, "submit_answer", "zip", "", "pending", attempt, now))
            sid = cur.lastrowid
            sp = storage.save_submission_source(pid, sid, "answer.zip", content)
            state.db.execute("UPDATE submissions SET source_path=? WHERE id=?", (str(sp), sid))
        else:
            if not code.strip():
                raise HTTPException(400, "代码不能为空")
            cur = state.db.execute(
                "INSERT INTO submissions (contest_id,user_id,problem_id,problem_type,language,source_path,"
                "status,attempt_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (contest["id"], u["id"], pid, prob["type"], prob["language"], "", "pending", attempt, now))
            sid = cur.lastrowid
            sp = storage.save_submission_source(pid, sid, "main.cpp", code.encode("utf-8"))
            state.db.execute("UPDATE submissions SET source_path=? WHERE id=?", (str(sp), sid))

        # 比赛进行中只跑 pretests；赛后为练习模式，跑完整测试（pretests + system），但不计入排行榜
        if status == "running":
            _spawn(services.run_and_track(sid, attempt, "pretests"))
        else:
            _spawn(services.run_full(sid, attempt))
        return RedirectResponse(f"/submissions/{sid}", status_code=303)

    @app.get("/submissions/{sid}", response_class=HTMLResponse)
    async def submission_page(request: Request, sid: int, u: dict | None = Depends(current_user)):
        sub = _sub_view(sid)
        if not sub:
            raise HTTPException(404)
        contest = state.contest_repo.current()
        if not can_view_source(u, sub, contest):
            raise HTTPException(403, "无权查看该提交")
        source_code = ""
        source_img = ""
        sp = pathlib.Path(sub["source_path"]) if sub["source_path"] else None
        if sp and sp.is_file():
            raw = sp.read_text("utf-8", errors="replace")
            if u and (u["type"] == "admin" or u["id"] == sub["user_id"]):
                source_code = raw
            else:
                png = codeimage.render_png(raw)
                if png:
                    source_img = base64.b64encode(png).decode()
                else:
                    source_code = raw
        hacked_by = state.db.query(
            "SELECT id, verdict, points_delta FROM hacks "
            "WHERE target_submission_id=? AND status='done' ORDER BY id", (sid,))
        return templates.TemplateResponse(request, "submission.html", _ctx(request, u, sub=sub,
                                                                         source_code=source_code,
                                                                         source_img=source_img,
                                                                         hacked_by=hacked_by))

    @app.get("/api/submissions/{sid}/stream")
    async def submission_stream(request: Request, sid: int):
        sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
        if not sub:
            raise HTTPException(404)
        return StreamingResponse(sse.gen_submission_stream(sid, request, sub),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    @app.get("/api/submissions/{sid}/final-fragment", response_class=HTMLResponse)
    async def final_fragment(request: Request, sid: int, u: dict | None = Depends(current_user)):
        sub = _sub_view(sid)
        if not sub or sub["status"] != "done":
            raise HTTPException(409)
        contest = state.contest_repo.current()
        if not can_view_source(u, sub, contest):
            raise HTTPException(403)
        return templates.TemplateResponse(request, "partials/submission_status.html",
                                          _ctx(request, u, sub=sub))

    @app.get("/submissions", response_class=HTMLResponse)
    async def my_submissions(request: Request, u: dict = Depends(require_login)):
        subs = state.db.query(
            "SELECT * FROM submissions WHERE user_id=? ORDER BY id DESC LIMIT 200", (u["id"],))
        for s in subs:
            s["problem_title"] = (state.problem_repo.get(s["problem_id"]) or {}).get("title", f"#{s['problem_id']}")
        return templates.TemplateResponse(request, "submissions.html", _ctx(request, u, subs=subs))

    @app.get("/contests/{cid}/submissions", response_class=HTMLResponse)
    async def all_submissions(request: Request, cid: str, viewer: dict | None = Depends(current_user)):
        contest = state.contest_repo.current()
        status = lifecycle.contest_status(contest) if contest else "finalized"
        is_admin = bool(viewer and viewer.get("type") == "admin")
        if status not in ("draining", "rejudging", "finalized") and not is_admin:
            raise HTTPException(403, "比赛结束后公开")
        subs = state.db.query(
            "SELECT * FROM submissions WHERE contest_id=? ORDER BY id DESC LIMIT 500", (cid,))
        for s in subs:
            s["problem_title"] = (state.problem_repo.get(s["problem_id"]) or {}).get("title", "")
            u = state.user_store.get(s["user_id"])
            s["username"] = u["username"] if u else f"#{s['user_id']}"
        return templates.TemplateResponse(request, "submissions.html", _ctx(request, viewer, subs=subs))

    # ========== 锁题 / Hack ==========
    @app.post("/api/problems/{pid}/lock")
    async def lock_problem(pid: int, u: dict = Depends(require_writable)):
        contest = state.contest_repo.current()
        if not contest or lifecycle.contest_status(contest) != "running":
            raise HTTPException(403, "比赛未进行")
        prob = state.problem_repo.get(pid)
        if not prob or prob["type"] != "default":
            raise HTTPException(400, "该题不支持 Hack")
        if not hack.hack_cfg(contest).get("enabled", True):
            raise HTTPException(400, "Hack 未开启")
        row = state.db.query_one(
            "SELECT verdict FROM submissions WHERE contest_id=? AND user_id=? AND problem_id=? "
            "AND superseded=0 ORDER BY id DESC LIMIT 1", (contest["id"], u["id"], pid))
        if not row or row["verdict"] not in ACLIKE:
            raise HTTPException(400, "需先通过该题（Pretests）")
        if hack.is_locked(contest["id"], u["id"], pid):
            raise HTTPException(400, "已锁定")
        hack.lock_problem(contest["id"], u["id"], pid)
        return RedirectResponse(f"/problems/{pid}", status_code=303)

    @app.get("/problems/{pid}/sources", response_class=HTMLResponse)
    async def sources_page(request: Request, pid: int, u: dict = Depends(require_login)):
        contest = state.contest_repo.current()
        if not contest:
            raise HTTPException(404)
        if lifecycle.contest_status(contest) != "running":
            raise HTTPException(403, "比赛已结束")
        if u["type"] == "banned":
            raise HTTPException(403)
        if not hack.is_locked(contest["id"], u["id"], pid):
            raise HTTPException(403, "未锁定该题")
        subs = state.db.query(
            "SELECT id,user_id,source_path,created_at,verdict FROM submissions "
            "WHERE contest_id=? AND problem_id=? AND verdict IN ('AC','PRETESTS_PASSED') "
            "AND superseded=0 AND hacked_by_hack_id IS NULL AND user_id!=? "
            "ORDER BY user_id, created_at DESC", (contest["id"], pid, u["id"]))
        for s in subs:
            usr = state.user_store.get(s["user_id"])
            s["username"] = usr["username"] if usr else f"#{s['user_id']}"
            s["code_text"] = ""
            s["code_img"] = ""
            sp = pathlib.Path(s["source_path"]) if s["source_path"] else None
            if sp and sp.is_file():
                raw = sp.read_text("utf-8", errors="replace")
                if u["type"] == "admin":
                    s["code_text"] = raw
                else:
                    png = codeimage.render_png(raw)
                    if png:
                        s["code_img"] = base64.b64encode(png).decode()
                    else:
                        s["code_text"] = raw
        return templates.TemplateResponse(request, "sources.html", _ctx(request, u, subs=subs, pid=pid))

    @app.get("/problems/{pid}/hack/{sid}", response_class=HTMLResponse)
    async def hack_form(request: Request, pid: int, sid: int, u: dict = Depends(require_writable)):
        contest = state.contest_repo.current()
        if not contest or lifecycle.contest_status(contest) != "running":
            raise HTTPException(403, "比赛未进行")
        if not hack.is_locked(contest["id"], u["id"], pid):
            raise HTTPException(403, "需先锁定该题")
        target = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
        if not target or target["problem_id"] != pid or target["verdict"] not in ACLIKE:
            raise HTTPException(400, "目标不是该题的通过提交")
        return templates.TemplateResponse(request, "hack_form.html", _ctx(request, u, target=target, pid=pid))

    @app.post("/api/problems/{pid}/hack")
    async def submit_hack(pid: int, sid: int = Form(...), input_text: str = Form(""),
                          file: UploadFile | None = File(None), u: dict = Depends(require_writable)):
        contest = state.contest_repo.current()
        if not contest or lifecycle.contest_status(contest) != "running":
            raise HTTPException(403, "比赛未进行")
        if not hack.is_locked(contest["id"], u["id"], pid):
            raise HTTPException(403, "需先锁定该题")
        target = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
        if not target or target["user_id"] == u["id"]:
            raise HTTPException(400, "不能 Hack 自己或目标无效")
        if target["problem_id"] != pid or target["verdict"] not in ACLIKE:
            raise HTTPException(400, "目标不是通过提交")

        content = b""
        if file is not None:
            data = await file.read()
            if data:
                content = data
        if not content and input_text:
            content = input_text.encode("utf-8")
        if not content:
            raise HTTPException(400, "请提供输入（在文本框粘贴，或上传输入文件）")
        if len(content) > 1024 * 1024:
            raise HTTPException(400, "输入超过 1MB")

        now = now_iso()
        attempt = uuid.uuid4().hex
        cur = state.db.execute(
            "INSERT INTO hacks (contest_id,hacker_id,victim_id,problem_id,target_submission_id,"
            "input_path,status,attempt_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (contest["id"], u["id"], target["user_id"], pid, sid, "", "pending", attempt, now))
        hid = cur.lastrowid
        hp = storage.save_hack_input(pid, hid, content, {
            "hacker_id": u["id"], "victim_id": target["user_id"],
            "target_submission_id": sid, "problem_id": pid, "created_at": now,
        })
        state.db.execute("UPDATE hacks SET input_path=? WHERE id=?", (str(hp), hid))
        _spawn(hack.judge_hack(hid))
        return RedirectResponse(f"/hacks/{hid}", status_code=303)

    @app.get("/hacks/{hid}", response_class=HTMLResponse)
    async def hack_page(request: Request, hid: int, u: dict | None = Depends(current_user)):
        h = state.db.query_one("SELECT * FROM hacks WHERE id=?", (hid,))
        if not h:
            raise HTTPException(404)
        contest = state.contest_repo.current()
        if not can_view_hack(u, h, contest):
            raise HTTPException(403)
        status = lifecycle.contest_status(contest) if contest else "finalized"
        is_admin = bool(u and u.get("type") == "admin")
        is_hacker = bool(u and u["id"] == h["hacker_id"])
        show_detail = bool(is_admin or is_hacker or status in ("draining", "rejudging", "finalized"))
        hacker = state.user_store.get(h["hacker_id"])
        victim = state.user_store.get(h["victim_id"])
        h["hacker_name"] = hacker["username"] if hacker else f"#{h['hacker_id']}"
        h["victim_name"] = victim["username"] if victim else f"#{h['victim_id']}"
        hack_input = ""
        if show_detail:
            ip = pathlib.Path(h["input_path"]) if h["input_path"] else None
            if ip and ip.is_file():
                hack_input = ip.read_text("utf-8", errors="replace")
        return templates.TemplateResponse(request, "hack.html", _ctx(request, u, hack=h,
                                                                     hack_input=hack_input,
                                                                     show_detail=show_detail))

    @app.get("/api/hacks/{hid}/stream")
    async def hack_stream(request: Request, hid: int):
        h = state.db.query_one("SELECT * FROM hacks WHERE id=?", (hid,))
        if not h:
            raise HTTPException(404)

        async def gen():
            q: asyncio.Queue = asyncio.Queue(maxsize=64)
            if h["status"] == "done":
                yield sse._sse("final", {"verdict": h["verdict"], "target_verdict": h["target_verdict"]})
                return
            sse._hack_subscribers[hid].append(q)
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=20)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield sse._sse(ev.get("event", "message"), ev)
                    if ev.get("event") == "final":
                        break
            finally:
                try:
                    sse._hack_subscribers[hid].remove(q)
                except ValueError:
                    pass

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    @app.get("/contests/{cid}/hacks", response_class=HTMLResponse)
    async def all_hacks(request: Request, cid: str, u: dict | None = Depends(current_user)):
        contest = state.contest_repo.current()
        status = lifecycle.contest_status(contest) if contest else "finalized"
        is_admin = bool(u and u.get("type") == "admin")
        if not u and status not in ("draining", "rejudging", "finalized"):
            raise HTTPException(403)
        hacks = state.db.query("SELECT * FROM hacks WHERE contest_id=? ORDER BY id DESC LIMIT 500", (cid,))
        show_all = True
        for h in hacks:
            hu = state.user_store.get(h["hacker_id"])
            vu = state.user_store.get(h["victim_id"])
            h["hacker_name"] = hu["username"] if hu else f"#{h['hacker_id']}"
            h["victim_name"] = vu["username"] if vu else f"#{h['victim_id']}"
        return templates.TemplateResponse(request, "hacks.html", _ctx(request, u, hacks=hacks,
                                                                     show_all=show_all))

    # ========== 通知 ==========
    @app.get("/api/notifications")
    async def notifications_list(u: dict = Depends(require_login)):
        rows = state.db.query(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (u["id"],))
        return [sse._notif_payload(r) for r in rows]

    @app.get("/api/notifications/unread-count")
    async def unread_count(u: dict = Depends(require_login)):
        return {"count": state.db.query_scalar(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (u["id"],))}

    @app.post("/api/notifications/read")
    async def mark_notifications_read(u: dict = Depends(require_login)):
        state.db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (u["id"],))
        return {"ok": True}

    @app.get("/api/notifications/stream")
    async def notifications_stream(request: Request, u: dict = Depends(require_login)):
        return StreamingResponse(sse.gen_notifications_stream(u["id"], request),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    @app.post("/api/notifications/{nid}/read")
    async def mark_read(nid: int, u: dict = Depends(require_login)):
        state.db.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (nid, u["id"]))
        return {"ok": True}

    @app.post("/api/notifications/read-all")
    async def mark_all_read(u: dict = Depends(require_login)):
        state.db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (u["id"],))
        return {"ok": True}

    # ========== 附件 ==========
    @app.get("/api/problems/{pid}/files/attachments/{fn}")
    async def problem_attachment(pid: int, fn: str):
        prob = state.problem_repo.get(pid)
        if not prob or fn not in prob.get("attachments", []):
            raise HTTPException(404)
        return FileResponse(str(pathlib.Path(prob["problem_dir"]) / "attachments" / fn))

    @app.get("/api/contests/{cid}/files/attachments/{fn}")
    async def contest_attachment(cid: str, fn: str):
        contest = state.contest_repo.current()
        if not contest or contest["id"] != cid:
            raise HTTPException(404)
        p = pathlib.Path(contest["contest_dir"]) / "attachments" / fn
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(str(p))

    @app.get("/api/problems/{pid}/files/inputs/all.zip")
    async def inputs_zip(pid: int):
        prob = state.problem_repo.get(pid)
        if not prob or prob["type"] != "submit_answer":
            raise HTTPException(404)
        cases = prob["cases"].get("system_tests") or prob["cases"].get("pretests") or []
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for c in cases:
                p = pathlib.Path(prob["testdata_dir"]) / c["in"]
                if p.is_file():
                    zf.write(p, arcname=c["in"])
        buf.seek(0)
        return Response(buf.read(), media_type="application/zip",
                        headers={"Content-Disposition": "attachment; filename=inputs.zip"})

    @app.get("/api/submissions/{sid}/source")
    async def submission_source(request: Request, sid: int, u: dict | None = Depends(current_user)):
        sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
        if not sub:
            raise HTTPException(404)
        contest = state.contest_repo.current()
        if not can_view_source(u, sub, contest):
            raise HTTPException(403)
        sp = pathlib.Path(sub["source_path"])
        if not sp.is_file():
            raise HTTPException(404)
        code = sp.read_text("utf-8", errors="replace")

        # 管理员 / 提交本人：可复制的代码块
        if u and (u["type"] == "admin" or u["id"] == sub["user_id"]):
            if "text/plain" in (request.headers.get("accept") or ""):
                return Response(code, media_type="text/plain; charset=utf-8")
            page = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>提交 #%d 代码</title>"
                "<style>body{margin:0;background:#0d1117;color:#e6edf3;"
                "font:14px/1.5 Consolas,Menlo,monospace}"
                "pre{margin:0;padding:16px;overflow:auto;white-space:pre}"
                ".bar{padding:8px 16px;background:#161b22;border-bottom:1px solid #30363d;"
                "font-family:system-ui,sans-serif}"
                ".bar button{background:#238636;color:#fff;border:0;padding:4px 12px;"
                "border-radius:6px;cursor:pointer;margin-left:12px}"
                "</style></head><body>"
                "<div class='bar'>提交 #%d"
                "<button onclick=\"navigator.clipboard.writeText("
                "document.getElementById('code').textContent)\">复制代码</button></div>"
                "<pre id='code'>%s</pre></body></html>"
            ) % (sid, sid, html.escape(code))
            return HTMLResponse(page)

        # 选手查看他人代码：返回图片，防止直接复制
        png = codeimage.render_png(code)
        if png is None:
            return Response(code, media_type="text/plain; charset=utf-8")
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    # ========== 管理员 ==========
    _register_admin_routes(app)


def _register_admin_routes(app: FastAPI) -> None:
    @app.get("/admin", response_class=HTMLResponse)
    async def admin_index(request: Request, u: dict = Depends(require_admin)):
        return RedirectResponse("/admin/users", status_code=303)

    @app.get("/admin/users", response_class=HTMLResponse)
    async def admin_users(request: Request, u: dict = Depends(require_admin)):
        return templates.TemplateResponse(request, "admin/users.html", _ctx(request, u,
            users=state.user_store.all()))

    @app.post("/api/admin/users")
    async def admin_create_user(username: str = Form(...), password: str = Form(...),
                                type_: str = Form("default"), u: dict = Depends(require_admin)):
        try:
            nu = state.user_store.create(username, password, type_ if type_ in ("default", "admin", "banned") else "default")
        except ValueError as e:
            raise HTTPException(400, str(e))
        state.db.write_audit_log(u["id"], "create_user", f"user:{nu['id']}",
                                 None, f"{nu['username']}({nu['type']})")
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/api/admin/users/{uid}/type")
    async def admin_set_type(uid: int, type_: str = Form(...), u: dict = Depends(require_admin)):
        if uid == u["id"]:
            raise HTTPException(400, "不能修改自己的用户类型")
        old = state.user_store.get(uid)
        try:
            state.user_store.update_type(uid, type_)
        except ValueError as e:
            raise HTTPException(400, str(e))
        state.db.write_audit_log(u["id"], "set_user_type", f"user:{uid}",
                                 (old or {}).get("type"), type_)
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/api/admin/users/{uid}/password")
    async def admin_reset_password(uid: int, password: str = Form(...), u: dict = Depends(require_admin)):
        try:
            state.user_store.reset_password(uid, password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        state.db.write_audit_log(u["id"], "reset_password", f"user:{uid}", None, "***")
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/api/admin/users/{uid}/delete")
    async def admin_delete_user(uid: int, u: dict = Depends(require_admin)):
        if uid == u["id"]:
            raise HTTPException(400, "不能删除自己")
        old = state.user_store.get(uid)
        try:
            state.user_store.delete(uid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        state.db.write_audit_log(u["id"], "delete_user", f"user:{uid}",
                                 (old or {}).get("username"), None)
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/api/admin/reload")
    async def admin_reload(background: BackgroundTasks, u: dict = Depends(require_admin)):
        """重载数据/模板；若检测到代码有更新，则自动重启进程（由 run.bat 拉起）。"""
        errs: list[str] = []
        try:
            errs += state.problem_repo.scan()
        except Exception as e:
            errs.append("problems: " + str(e))
        try:
            errs += state.contest_repo.scan()
        except Exception as e:
            errs.append("contests: " + str(e))
        try:
            state.user_store.reload()
        except Exception as e:
            errs.append("users: " + str(e))
        try:
            templates.env.cache.clear()
        except Exception:
            pass
        changed = _code_changed(_PROC_START)
        state.db.write_audit_log(u["id"], "reload", "system", None,
                                 "restart" if changed else "data")
        if changed:
            background.add_task(_restart_now)
        return {"ok": not errs, "errors": errs, "restart": changed}

    @app.post("/api/admin/restart")
    async def admin_restart(background: BackgroundTasks, u: dict = Depends(require_admin)):
        """强制重启服务（run.bat 守护循环会自动拉起）。"""
        state.db.write_audit_log(u["id"], "restart", "system", None, "restart")
        background.add_task(_restart_now)
        return {"ok": True, "restart": True}

    @app.get("/admin/submissions", response_class=HTMLResponse)
    async def admin_submissions(request: Request, u: dict = Depends(require_admin)):
        subs = state.db.query("SELECT * FROM submissions ORDER BY id DESC LIMIT 500")
        for s in subs:
            s["problem_title"] = (state.problem_repo.get(s["problem_id"]) or {}).get("title", "")
            usr = state.user_store.get(s["user_id"])
            s["username"] = usr["username"] if usr else f"#{s['user_id']}"
        return templates.TemplateResponse(request, "admin/submissions.html", _ctx(request, u, subs=subs))

    @app.post("/api/admin/submissions/{sid}/delete")
    async def admin_delete_submission(sid: int, u: dict = Depends(require_admin)):
        sub = state.db.query_one("SELECT * FROM submissions WHERE id=?", (sid,))
        if not sub:
            raise HTTPException(404, "提交不存在")
        sp = pathlib.Path(sub["source_path"]) if sub["source_path"] else None
        if sp and sp.is_file():
            parent = sp.parent
            if parent.name == f"sub-{sid:06d}" and parent.parent.name == str(sub["problem_id"]):
                shutil.rmtree(parent, ignore_errors=True)
            else:
                sp.unlink(missing_ok=True)
        state.db.execute("DELETE FROM submissions WHERE id=?", (sid,))
        state.db.write_audit_log(u["id"], "delete_submission", f"submission:{sid}",
                                 sub["verdict"], None)
        return RedirectResponse("/admin/submissions", status_code=303)

    @app.post("/api/admin/submissions/{sid}/rejudge")
    async def admin_rejudge(sid: int, run_pretests: str = Form("true"), u: dict = Depends(require_admin)):
        from .admin_actions import admin_rejudge_submission
        run = run_pretests.lower() in ("1", "true", "yes")
        return await admin_rejudge_submission(sid, run, u)

    @app.post("/api/admin/submissions/{sid}/cancel")
    async def admin_cancel(sid: int, u: dict = Depends(require_admin)):
        from .admin_actions import admin_cancel_submission
        return await admin_cancel_submission(sid, u)

    @app.post("/api/admin/submissions/{sid}/uncancel")
    async def admin_uncancel(sid: int, u: dict = Depends(require_admin)):
        from .admin_actions import admin_uncancel_submission
        return await admin_uncancel_submission(sid, u)

    @app.get("/admin/problems", response_class=HTMLResponse)
    async def admin_problems(request: Request, u: dict = Depends(require_admin)):
        return templates.TemplateResponse(request, "admin/problems.html", _ctx(request, u,
            problems=state.problem_repo.all()))

    @app.post("/api/admin/problems/{pid}/rejudge")
    async def admin_rejudge_problem(pid: int, run_pretests: str = Form("false"),
                                    u: dict = Depends(require_admin)):
        from .admin_actions import admin_rejudge_problem
        run = run_pretests.lower() in ("1", "true", "yes")
        return await admin_rejudge_problem(pid, run, u)

    @app.get("/admin/settings", response_class=HTMLResponse)
    async def admin_settings(request: Request, u: dict = Depends(require_admin)):
        contest = state.contest_repo.current()
        reg_ok, reg_reason = registration_open(contest)
        meta = state.db.get_runtime_setting_meta("registration.enabled")
        audits = state.db.query("SELECT * FROM admin_audit_log ORDER BY id DESC LIMIT 20")
        for a in audits:
            au = state.user_store.get(a["admin_id"])
            a["admin_name"] = au["username"] if au else f"#{a['admin_id']}"
        setting = {"enabled": reg_ok,
                   "reason": reg_reason,
                   "close_at": (contest or {}).get("registration", {}).get("close_at"),
                   "updated_at": meta.get("updated_at"),
                   "updated_by_name": (state.user_store.get(meta["updated_by"]) or {}).get("username")
                                      if meta.get("updated_by") else None}
        return templates.TemplateResponse(request, "admin/settings.html", _ctx(request, u,
            setting=setting, audits=audits))

    @app.post("/api/admin/settings/registration")
    async def set_registration(payload: dict, u: dict = Depends(require_admin)):
        contest = state.contest_repo.current()
        enabled = bool(payload.get("enabled"))
        default = contest.get("registration", {}).get("enabled", False) if contest else False
        old = state.db.get_runtime_setting("registration.enabled", default)
        state.db.set_runtime_setting("registration.enabled", enabled, updated_by=u["id"])
        state.db.write_audit_log(u["id"], "toggle_registration", "registration.enabled",
                                 str(old), str(enabled))
        return {"ok": True, "enabled": enabled}

    @app.post("/api/admin/contest/end")
    async def admin_end_contest(u: dict = Depends(require_admin)):
        contest = state.contest_repo.current()
        if not contest:
            raise HTTPException(404, "无当前比赛")
        if lifecycle.contest_status(contest) in ("draining", "rejudging", "finalized"):
            raise HTTPException(400, "比赛已结束")
        lifecycle.set_status(contest["id"], "draining", "admin_ended_early")
        state.db.write_audit_log(u["id"], "end_contest", contest["id"], None, "draining")
        return RedirectResponse(f"/contests/{contest['id']}", status_code=303)

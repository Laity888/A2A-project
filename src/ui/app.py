"""Web UI BFF（工单 06，票 07；工单：多用户隔离加登录；工单：多会话历史列表）：Starlette 应用，兼 A2A client。

- POST /api/chat：{message, context_id?} → 调接待流 → 翻译成浏览器 SSE 事件（token/status/trace/error/done）。
  context_id 优先取 body（前端当前会话），否则回退会话 cookie/新建（票 06 §9 向后兼容）；
  user_id 按登录 cookie 维持，随请求带给接待。登录且注入了 conversation_store 时，chat 会 upsert 会话索引
  （首句自动生成标题、每轮刷新 updated_at）。
- 多会话（config 提供 conversation_store 时启用，测试可注入假 store；不自动连库）：
  GET /api/conversations、GET /api/messages?context_id=、POST /api/conversations/rename、POST /api/conversations/delete。
- 多用户隔离身份层（config.auth.enabled=true 时启用）：GET /login / POST /api/register / /api/login / /api/logout；
  未登录访问 / 或会话类接口会被拦下。enabled=false 时透明放行（user_id=None），供开发与离线测试沿用。
- GET /：前端页（左历史列表 + 对话主区 + trace 面板）。
provider_factory / auth / conversation_store 可注入（测试用 fake，离线不连真接待/DB）。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.routing import Route

from dotenv import load_dotenv

from common.config import DEFAULT_ENV_PATH, REPO_ROOT
from ui.auth import AuthContext, make_auth_context
from ui.config import load_ui_config
from ui.provider import A2AReceptionProvider
from ui.sse import format_sse

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(config: dict | None = None, *, provider_factory=None,
               auth: AuthContext | None = None, conversation_store=None,
               profile_store=None) -> Starlette:
    ui_cfg = config if config is not None else load_ui_config()
    cookie_name = ui_cfg.get("session_cookie", "a2a_cs_sid")
    prefix = ui_cfg.get("context_prefix", "ui-")

    if auth is None:
        auth = make_auth_context(
            ui_cfg.get("auth", {}) or {},
            data_dir=REPO_ROOT / "data",
            repo_root=REPO_ROOT,
        )

    if provider_factory is None:
        provider = A2AReceptionProvider(ui_cfg)
        provider_factory = provider.stream

    def _read_user(request: Request) -> str | None:
        return auth.user_id_from_cookies(dict(request.cookies))

    async def index(request: Request):
        if auth.enabled and _read_user(request) is None:
            return RedirectResponse("/login", status_code=302)
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    async def login_page(_request: Request) -> HTMLResponse:
        html = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    async def me(request: Request) -> JSONResponse:
        uid = _read_user(request)
        name = auth.store.username(uid) if (auth.enabled and auth.store and uid) else None
        return JSONResponse({"authenticated": bool(uid), "user_id": uid, "username": name})

    async def _auth_form(request: Request) -> tuple[str, str]:
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001 - 坏 JSON 当空表单
                body = {}
            return str(body.get("username") or ""), str(body.get("password") or "")
        form = await request.form()
        return str(form.get("username") or ""), str(form.get("password") or "")

    async def register(request: Request) -> JSONResponse:
        if not auth.enabled or auth.store is None:
            return JSONResponse({"ok": False, "error": "登录未启用"}, status_code=400)
        if not auth.registration_enabled:
            return JSONResponse({"ok": False, "error": "注册暂未开放，请使用内置账号登录"}, status_code=403)
        username, password = await _auth_form(request)
        ok, result = auth.store.create_user(username, password)
        if not ok:
            return JSONResponse({"ok": False, "error": result}, status_code=400)
        resp = JSONResponse({"ok": True, "username": username.strip()})
        resp.set_cookie(auth.cookie_name, auth.issue_token(result), httponly=True, samesite="lax", max_age=int(auth.token_ttl_seconds))
        return resp

    async def login(request: Request) -> JSONResponse:
        if not auth.enabled or auth.store is None:
            return JSONResponse({"ok": False, "error": "登录未启用"}, status_code=400)
        username, password = await _auth_form(request)
        user_id = auth.store.verify(username, password)
        if not user_id:
            return JSONResponse({"ok": False, "error": "用户名或密码不正确"}, status_code=401)
        resp = JSONResponse({"ok": True, "username": username.strip()})
        resp.set_cookie(auth.cookie_name, auth.issue_token(user_id), httponly=True, samesite="lax", max_age=int(auth.token_ttl_seconds))
        return resp

    async def logout(_request: Request) -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.cookie_name)
        return resp

    async def chat(request: Request) -> StreamingResponse:
        user_id = _read_user(request)
        if auth.enabled and user_id is None:
            return JSONResponse({"error": "未登录"}, status_code=401)

        body = await request.json()
        message = str(body.get("message", ""))
        existing = request.cookies.get(cookie_name)
        context_id = str(body.get("context_id") or "").strip() or existing or (prefix + uuid.uuid4().hex)

        # 会话索引 upsert（首句自动标题 / 刷新 updated_at）；尽力而为，失败不打断答复
        if conversation_store is not None and user_id and message:
            try:
                conversation_store.touch_or_create(user_id, context_id, message)
            except Exception as exc:  # noqa: BLE001
                logger.warning("会话索引更新失败（忽略）err=%s", exc)

        async def event_stream():
            try:
                async for ev in provider_factory(message, context_id, user_id=user_id):
                    kind = ev.get("kind", "message")
                    payload = {k: v for k, v in ev.items() if k != "kind"}
                    yield format_sse(kind, payload)
            except Exception as exc:  # noqa: BLE001 - 兜底给前端一个 error 事件
                logger.error("SSE 生成失败 err=%s", exc)
                yield format_sse("error", {"message": "服务暂时不可用，请稍后再试"})

        resp = StreamingResponse(event_stream(), media_type="text/event-stream")
        if not existing:
            resp.set_cookie(cookie_name, context_id, httponly=True, samesite="lax")
        return resp

    async def conversations(request: Request) -> JSONResponse:
        user_id = _read_user(request)
        if user_id is None:
            return JSONResponse({"error": "未登录"}, status_code=401)
        if conversation_store is None:
            return JSONResponse({"conversations": []})
        try:
            items = conversation_store.list(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("列会话失败 err=%s", exc)
            items = []
        return JSONResponse({"conversations": list(items)})

    async def messages(request: Request) -> JSONResponse:
        user_id = _read_user(request)
        if user_id is None:
            return JSONResponse({"error": "未登录"}, status_code=401)
        cid = request.query_params.get("context_id", "").strip()
        if not cid or conversation_store is None:
            return JSONResponse({"messages": []})
        try:
            msgs = conversation_store.get_messages(user_id, cid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读会话消息失败 err=%s", exc)
            msgs = []
        return JSONResponse({"messages": list(msgs)})

    async def rename(request: Request) -> JSONResponse:
        user_id = _read_user(request)
        if user_id is None:
            return JSONResponse({"error": "未登录"}, status_code=401)
        if conversation_store is None:
            return JSONResponse({"ok": False, "error": "会话功能未启用"}, status_code=501)
        body = await request.json()
        cid = str(body.get("context_id") or "").strip()
        title = str(body.get("title") or "")
        if not cid:
            return JSONResponse({"ok": False, "error": "缺少 context_id"}, status_code=400)
        try:
            ok = conversation_store.rename(user_id, cid, title)
        except Exception as exc:  # noqa: BLE001
            logger.warning("重命名失败 err=%s", exc)
            return JSONResponse({"ok": False, "error": "操作失败"}, status_code=500)
        return JSONResponse({"ok": bool(ok)}, status_code=200 if ok else 404)

    async def delete_conversation(request: Request) -> JSONResponse:
        user_id = _read_user(request)
        if user_id is None:
            return JSONResponse({"error": "未登录"}, status_code=401)
        if conversation_store is None:
            return JSONResponse({"ok": False, "error": "会话功能未启用"}, status_code=501)
        body = await request.json()
        cid = str(body.get("context_id") or "").strip()
        if not cid:
            return JSONResponse({"ok": False, "error": "缺少 context_id"}, status_code=400)
        try:
            ok = conversation_store.delete(user_id, cid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除会话失败 err=%s", exc)
            return JSONResponse({"ok": False, "error": "操作失败"}, status_code=500)
        return JSONResponse({"ok": bool(ok)}, status_code=200 if ok else 404)

    async def profile(request: Request) -> JSONResponse:
        user_id = _read_user(request)
        if user_id is None:
            return JSONResponse({"error": "未登录"}, status_code=401)
        if profile_store is None:
            return JSONResponse({"ok": False, "error": "偏好功能未启用"}, status_code=501)
        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001 - 坏 JSON 当空
                body = {}
            raw = body.get("profile") or {}
            if not isinstance(raw, dict):
                return JSONResponse({"ok": False, "error": "格式错误"}, status_code=400)
            updates = {str(k): str(v) for k, v in raw.items() if str(v).strip()}
            deletes = [str(k) for k, v in raw.items() if not str(v).strip()]
            try:
                if updates:
                    profile_store.update_profile(user_id, updates)
                if deletes:
                    profile_store.delete_profile(user_id, deletes)
                prof = profile_store.get_profile(user_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("偏好更新失败 err=%s", exc)
                return JSONResponse({"ok": False, "error": "操作失败"}, status_code=500)
            return JSONResponse({"ok": True, "profile": prof})
        try:
            prof = profile_store.get_profile(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("偏好读取失败 err=%s", exc)
            prof = {}
        return JSONResponse({"profile": prof})

    async def memory_history(request: Request) -> JSONResponse:
        user_id = _read_user(request)
        if user_id is None:
            return JSONResponse({"error": "未登录"}, status_code=401)
        if profile_store is None:
            return JSONResponse({"history": []})
        try:
            limit = int(request.query_params.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        try:
            items = profile_store.get_recent_history(user_id, limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读历史失败 err=%s", exc)
            items = []
        return JSONResponse({"history": list(items)})

    async def memory_clear(request: Request) -> JSONResponse:
        user_id = _read_user(request)
        if user_id is None:
            return JSONResponse({"error": "未登录"}, status_code=401)
        if profile_store is None:
            return JSONResponse({"ok": False, "error": "记忆功能未启用"}, status_code=501)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - 坏 JSON 当空
            body = {}
        scope = str(body.get("scope") or "all").strip().lower()
        try:
            if scope == "history":
                profile_store.clear_history(user_id)
            elif scope == "profile":
                profile_store.clear_profile(user_id)
            else:
                profile_store.clear(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("清空记忆失败 err=%s", exc)
            return JSONResponse({"ok": False, "error": "操作失败"}, status_code=500)
        return JSONResponse({"ok": True})

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/login", login_page, methods=["GET"]),
        Route("/api/me", me, methods=["GET"]),
        Route("/api/register", register, methods=["POST"]),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/conversations", conversations, methods=["GET"]),
        Route("/api/messages", messages, methods=["GET"]),
        Route("/api/conversations/rename", rename, methods=["POST"]),
        Route("/api/conversations/delete", delete_conversation, methods=["POST"]),
        Route("/api/memory/profile", profile, methods=["GET", "POST"]),
        Route("/api/memory/history", memory_history, methods=["GET"]),
        Route("/api/memory/clear", memory_clear, methods=["POST"]),
    ]
    return Starlette(routes=routes)


def build_app(config: dict | None = None) -> Starlette:
    """真·启动装配：建 auth（可能连 MySQL 建库/表/种子）+ 会话索引 store。离线测试不要用这个，
    用 create_app(config, auth=..., conversation_store=...) 注入 fake，避免连库。"""
    ui_cfg = config if config is not None else load_ui_config()
    load_dotenv(DEFAULT_ENV_PATH, override=False)  # 让 .env 里的 A2A_MYSQL_PASSWORD 等在任意进程可见
    auth_cfg = ui_cfg.get("auth", {}) or {}
    try:
        auth = make_auth_context(auth_cfg, data_dir=REPO_ROOT / "data", repo_root=REPO_ROOT)
    except Exception as exc:  # noqa: BLE001 - MySQL 登录层建不起来不让 UI 崩，回退本地 SQLite
        logger.warning("MySQL 登录层初始化失败，回退本地 SQLite 登录（内置账号仍可用）：%s", exc)
        auth = make_auth_context({**auth_cfg, "backend": "sqlite"},
                                 data_dir=REPO_ROOT / "data", repo_root=REPO_ROOT)
        for d in auth_cfg.get("seed_users", []) or []:
            try:
                auth.store.create_user(d.get("username"), d.get("password"))
            except Exception:  # noqa: BLE001 - 种子已存在等忽略
                pass

    conversation_store = None
    profile_store = None
    if auth.enabled:
        try:
            from ui.conversation_store import make_conversation_store

            conversation_store = make_conversation_store(ui_cfg)
        except Exception as exc:  # noqa: BLE001 - 会话索引连不上不阻断起服，列表降级为空
            logger.warning("会话索引初始化失败，会话列表功能关闭：%s", exc)
        try:
            # 偏好读写：复用 reception 的 MemoryStore（同一 a2a_cs 库、同一 user_profiles 表）
            from agents.reception.memory import MemoryStore
            from agents.reception.session import make_mysql_connection_factory

            mm = (ui_cfg.get("auth", {}) or {}).get("mysql", {}) or {}
            profile_store = MemoryStore(connection_factory=make_mysql_connection_factory(mm))
        except Exception as exc:  # noqa: BLE001 - 偏好存储连不上不阻断起服，设置面板降级
            logger.warning("偏好存储初始化失败，偏好设置功能关闭：%s", exc)
    return create_app(ui_cfg, auth=auth, conversation_store=conversation_store, profile_store=profile_store)


def __getattr__(name: str):
    """懒建模块级 app：避免仅 import（含测试）就触发建库/写密钥；保留 `ui.app:app` 供 ASGI 起服。"""
    if name == "app":
        built = build_app()
        globals()["app"] = built
        return built
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

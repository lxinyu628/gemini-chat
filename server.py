"""FastAPI 后端服务，提供 OpenAI 兼容 API 和前端页面"""
import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote_plus
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Header, Form, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from biz_gemini.auth import JWTManager, check_session_status, ensure_jwt_valid, request_getoxsrf, GETOXSRF_URL, on_cookie_refreshed
from biz_gemini.biz_client import BizGeminiClient
from biz_gemini.config import (
    cookies_age_seconds,
    cookies_expired,
    get_proxy,
    is_cookie_expired,
    load_config,
    reload_config,
    save_config,
    mark_cookie_valid,
    mark_cookie_expired,
)
from biz_gemini.openai_adapter import OpenAICompatClient
from biz_gemini.anthropic_adapter import AnthropicCompatClient
from biz_gemini.web_login import get_login_service
from biz_gemini.remote_browser import get_browser_service, BrowserSessionStatus
from biz_gemini.keep_alive import get_keep_alive_service, notify_auth_error
from biz_gemini.browser_keep_alive import (
    get_browser_keep_alive_service,
    try_refresh_cookie_via_browser,
)
from biz_gemini.logger import get_logger
from biz_gemini.api_keys import (
    generate_api_key,
    list_api_keys,
    get_api_key_by_id,
    validate_api_key,
    delete_api_key,
)
from config_watcher import start_config_watcher, stop_config_watcher
from version import VERSION, get_version_info, GITHUB_REPO

# 模块级 logger
logger = get_logger("server")

app = FastAPI(title="Gemini Chat API", version=VERSION)


# OpenAI 兼容的错误响应处理器
@app.exception_handler(HTTPException)
async def openai_error_handler(request: Request, exc: HTTPException):
    """将 HTTPException 转换为 OpenAI 标准错误格式"""
    # 只对 /v1/ 路径下的 API 返回 OpenAI 格式错误
    if request.url.path.startswith("/v1/"):
        error_type = "api_error"
        if exc.status_code == 400:
            error_type = "invalid_request_error"
        elif exc.status_code == 401:
            error_type = "authentication_error"
        elif exc.status_code == 403:
            error_type = "permission_error"
        elif exc.status_code == 404:
            error_type = "not_found_error"
        elif exc.status_code == 429:
            error_type = "rate_limit_error"

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": exc.detail,
                    "type": error_type,
                    "param": None,
                    "code": str(exc.status_code)
                }
            }
        )
    # 非 /v1/ 路径保持 FastAPI 默认格式
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )


# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件目录
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# 图片存储目录
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "biz_gemini_images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# 会话管理：存储多个对话
sessions: dict[str, dict] = {}

# 配置热重载回调
def on_config_changed(new_config: dict) -> None:
    """配置变更时的回调函数"""
    logger.info("配置已更新，部分配置将在下次请求时生效")
    # 可以在这里添加其他配置变更处理逻辑
    # 例如：更新代理配置、刷新客户端等

# 用于标记是否是第一个 worker（避免多 worker 重复启动浏览器）
_is_primary_worker = False


def _check_primary_worker() -> bool:
    """使用文件锁确保后台服务只启动一份（无论是否多 worker）"""
    import os
    import tempfile

    lock_file = os.path.join(tempfile.gettempdir(), "gemini_chat_browser_keep_alive.lock")

    try:
        # Windows 使用 msvcrt，Unix 使用 fcntl
        if os.name == "nt":
            import msvcrt

            lock_fd = open(lock_file, "w")
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                globals()["_lock_fd"] = lock_fd
                return True
            except (IOError, OSError):
                lock_fd.close()
                return False
        else:
            import fcntl

            lock_fd = open(lock_file, "w")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                globals()["_lock_fd"] = lock_fd
                return True
            except (IOError, OSError, BlockingIOError):
                lock_fd.close()
                return False

    except Exception as e:
        # 任何异常都回退到允许运行（单 worker 模式或无法获取锁）
        logger.warning(f"检查主 worker 时出错，默认允许运行: {e}")
        return True


def _build_account_chooser_url(config: dict) -> Optional[str]:
    """构造 account-chooser URL，用于已有 session 需要复用的场景。

    注意：account-chooser 仅用于已有登录记录需要复用 session 的情况。
    首次登录应该直接访问 https://business.gemini.google/

    官网的 URL 格式：
    https://auth.business.gemini.google/account-chooser?autoSelectSession=false&continueUrl=
    https://business.gemini.google/home/cid/{group_id}?mods=&e=&project={project_id}&refCid={group_id}&csesidx={csesidx}
    """
    try:
        group_id = config.get("group_id")
        csesidx = config.get("csesidx")

        # 只有同时有 group_id 和 csesidx 时才构造 account-chooser URL
        # 否则应该让用户直接访问 business.gemini.google 进行首次登录
        if not group_id or not csesidx:
            return None

        # 构造 continueUrl，与官网格式一致
        # 完整格式：包含 mods, e, refCid, csesidx 参数
        base_continue = f"https://business.gemini.google/home/cid/{group_id}?mods=&e=&refCid={group_id}&csesidx={csesidx}"

        # 添加 autoSelectSession=false 参数，与官网一致
        return f"https://auth.business.gemini.google/account-chooser?autoSelectSession=false&continueUrl={quote_plus(base_continue)}"
    except Exception:
        return None


# 应用生命周期事件
@app.on_event("startup")
async def startup_event() -> None:
    """应用启动时执行"""
    global _is_primary_worker

    logger.info("启动配置文件监控...")
    start_config_watcher(callback=on_config_changed)
    logger.info("配置监控已启动")

    # 加载配置
    config = load_config()
    browser_keep_alive_config = config.get("browser_keep_alive", {})

    # 检查是否是主 worker
    _is_primary_worker = _check_primary_worker()

    if _is_primary_worker:
        # 启动 Session 保活服务（只在主 worker 中运行）
        logger.info("启动 Session 保活服务...")
        # 注意：如果 BrowserKeepAliveService 启用，则由它负责浏览器刷新和自动登录
        # KeepAliveService 不需要再调用 _try_browser_refresh，避免重复触发
        browser_keep_alive_enabled = browser_keep_alive_config.get("enabled", False)
        auto_browser_refresh = False  # BrowserKeepAliveService 会处理，这里不再重复
        keep_alive = get_keep_alive_service(
            interval_minutes=10,
            auto_browser_refresh=auto_browser_refresh,
        )
        await keep_alive.start()
        logger.info(f"Session 保活服务已启动（每 10 分钟检查一次，自动浏览器刷新: {auto_browser_refresh}，浏览器保活服务: {'启用' if browser_keep_alive_enabled else '禁用'}）")

        # 启动浏览器保活服务（如果启用，只在主 worker 中运行）
        if browser_keep_alive_config.get("enabled", False):
            logger.info("启动浏览器保活服务...")
            browser_keep_alive = get_browser_keep_alive_service(
                interval_minutes=browser_keep_alive_config.get("interval_minutes", 60),
                headless=browser_keep_alive_config.get("headless", True),
            )
            await browser_keep_alive.start()
            logger.info(f"浏览器保活服务已启动（每 {browser_keep_alive_config.get('interval_minutes', 60)} 分钟刷新一次）")
    else:
        logger.info("非主 worker，跳过后台服务启动")

@app.on_event("shutdown")
async def shutdown_event() -> None:
    """应用关闭时执行"""
    global _is_primary_worker

    if _is_primary_worker:
        logger.info("停止 Session 保活服务...")
        keep_alive = get_keep_alive_service()
        await keep_alive.stop()
        logger.info("Session 保活服务已停止")

        # 停止浏览器保活服务
        config = load_config()
        if config.get("browser_keep_alive", {}).get("enabled", False):
            logger.info("停止浏览器保活服务...")
            browser_keep_alive = get_browser_keep_alive_service()
            await browser_keep_alive.stop()
            logger.info("浏览器保活服务已停止")

        # 释放文件锁
        if "_lock_fd" in globals():
            try:
                globals()["_lock_fd"].close()
            except Exception:
                pass

    logger.info("停止配置文件监控...")
    stop_config_watcher()
    logger.info("配置监控已停止")


class Message(BaseModel):
    role: str
    # 兼容 OpenAI 标准格式的字符串与 content parts 两种形式
    content: Union[str, List[Dict[str, Any]]]


class ChatRequest(BaseModel):
    model: str = "business-gemini"
    messages: List[Message]
    stream: bool = False
    # OpenAI 标准参数（接收以保证兼容性，Gemini API 不一定支持）
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None
    n: Optional[int] = None
    user: Optional[str] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    # 自定义扩展参数
    session_id: Optional[str] = None
    session_name: Optional[str] = None
    file_ids: Optional[List[str]] = None
    include_image_data: bool = True
    include_thoughts: bool = False  # 是否返回思考链
    embed_images_in_content: bool = True  # 是否将图片内嵌到 content（OpenAI 兼容模式）


# Anthropic Messages API 请求模型
class AnthropicContentBlock(BaseModel):
    type: str
    text: Optional[str] = None
    # 图片支持
    source: Optional[dict] = None


class AnthropicMessage(BaseModel):
    role: str
    content: Union[str, List[AnthropicContentBlock]]


class AnthropicRequest(BaseModel):
    """Anthropic Messages API 请求格式"""
    model: str = "gemini-2.5-pro"
    max_tokens: int = 4096
    messages: List[AnthropicMessage]
    # system 可以是字符串或 content blocks 数组
    # 字符串: "You are a helpful assistant"
    # 数组: [{"type": "text", "text": "You are...", "cache_control": {...}}]
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    stop_sequences: Optional[List[str]] = None
    metadata: Optional[dict] = None


# Anthropic 接口的会话管理
# 使用单独的字典存储，key 可以是 session_id 或默认的 "default"
anthropic_sessions: dict[str, dict] = {}


class SessionInfo(BaseModel):
    session_id: str
    title: str
    created_at: float
    message_count: int


def get_or_create_client(session_id: str, session_name: Optional[str] = None) -> tuple[OpenAICompatClient, dict, str]:
    """获取或创建指定会话的客户端

    Returns:
        (client, session_data, canonical_session_id) 元组
        canonical_session_id 是真实的 Google session ID（可能与传入的 session_id 不同）
    """
    # 如果 session_id 已存在，直接返回
    if session_id in sessions:
        return sessions[session_id]["client"], sessions[session_id], session_id

    # 检查是否有其他会话的 session_name 以此 session_id 结尾（用于 UUID 迁移场景）
    # 这种情况不太可能发生，但保留兼容性

    # 创建新会话
    config = load_config()
    jwt_manager = JWTManager(config=config)
    biz_client = BizGeminiClient(config, jwt_manager)
    client = OpenAICompatClient(biz_client)

    # 如果提供了 session_name（来自历史会话），直接复用该会话，而不是创建新会话
    if session_name:
        biz_client._session_name = session_name  # 直接指定已有的 session name 以保持上下文
    else:
        session_name = biz_client.session_name  # 创建新会话并获取 session_name

    # 从 session_name 提取真实的 session_id
    real_session_id = session_name.split("/")[-1] if "/" in session_name else session_name

    # 判断传入的 session_id 是否是临时 UUID（格式：xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx）
    is_temp_uuid = len(session_id) == 36 and session_id.count("-") == 4

    # 优先使用真实的 session_id 作为 canonical（避免重复创建会话）
    if real_session_id and (is_temp_uuid or real_session_id != session_id):
        canonical_id = real_session_id
    else:
        canonical_id = session_id

    sessions[canonical_id] = {
        "client": client,
        "biz_client": biz_client,
        "session_name": session_name,
        "messages": [],
        "title": "新对话",
        "created_at": time.time(),
    }

    return sessions[canonical_id]["client"], sessions[canonical_id], canonical_id


def get_or_create_anthropic_client(session_id: str = "default") -> tuple[AnthropicCompatClient, dict]:
    """获取或创建指定会话的 Anthropic 兼容客户端

    Args:
        session_id: 会话 ID，默认为 "default"（单会话模式）

    Returns:
        (AnthropicCompatClient, session_data) 元组
    """
    if session_id not in anthropic_sessions:
        config = load_config()
        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)
        # 获取 session_name（这会触发会话创建）
        session_name = biz_client.session_name
        client = AnthropicCompatClient(biz_client, session_name=session_name)
        anthropic_sessions[session_id] = {
            "client": client,
            "biz_client": biz_client,
            "session_name": session_name,
            "messages": [],
            "created_at": time.time(),
        }
    return anthropic_sessions[session_id]["client"], anthropic_sessions[session_id]


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """返回前端页面"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>请先创建 static/index.html</h1>")


@app.get("/api/status")
async def get_status() -> dict:
    """获取登录状态（通过 list-sessions 检查真实的 session 状态）"""
    try:
        config = load_config()
        account_chooser_url = _build_account_chooser_url(config)

        has_credentials = all([
            config.get("secure_c_ses"),
            config.get("csesidx"),
            config.get("group_id"),
        ])

        if not has_credentials:
            return {
                "logged_in": False,
                "message": "未登录，请运行 python app.py login",
                "account_chooser_url": account_chooser_url,
            }

        # 获取保活服务的状态（如果可用）
        keep_alive = get_keep_alive_service()
        keep_alive_status = keep_alive.get_status()

        # 如果保活服务已经检查过 session，使用缓存的结果（超过 5 分钟或标记失效则重新检查）
        if keep_alive_status.get("last_check") and keep_alive_status.get("session_valid") is not None:
            session_valid = keep_alive_status["session_valid"]
            session_username = keep_alive_status.get("session_username")
            last_check_ts = None
            try:
                last_check_ts = datetime.fromisoformat(keep_alive_status["last_check"])
            except Exception:
                pass
            is_stale = False
            if last_check_ts:
                is_stale = (datetime.now() - last_check_ts).total_seconds() > 300

            if session_valid and not is_stale:
                # 多 Worker 模式下，非主 Worker 的 session_username 可能为空
                # 使用 config 中的 username 作为备选
                effective_username = session_username or config.get("username")
                return {
                    "logged_in": True,
                    "session_valid": True,
                    "username": effective_username,
                    "last_check": keep_alive_status.get("last_check"),
                    "message": f"已登录: {effective_username}" if effective_username else "已登录",
                    "account_chooser_url": account_chooser_url,
                }

        # 主动检查一次（缓存过期或标记失效）
        session_status = check_session_status(config)

        # 同步更新保活服务缓存，减少短期重复误判
        if session_status.get("valid", False):
            try:
                keep_alive._session_valid = True
                keep_alive._session_username = session_status.get("username")
                keep_alive._last_check = datetime.now()
                keep_alive._last_error = None
                keep_alive._cookie_expired = False
            except Exception:
                pass
            mark_cookie_valid()
            
            # 保存 username 到 config.json（供自动登录使用）
            username = session_status.get("username")
            if username:
                try:
                    save_config({"username": username})
                except Exception:
                    pass
        else:
            # 会话无效或过期，强制更新保活服务缓存状态
            try:
                keep_alive._session_valid = False
                keep_alive._last_check = datetime.now()
                keep_alive._last_error = session_status.get("error")
                if session_status.get("expired", False):
                    keep_alive._cookie_expired = True
                    mark_cookie_expired(session_status.get("error", "session check expired"))
            except Exception:
                pass

        if session_status.get("warning", False) and session_status.get("valid", False):
            username = session_status.get("username") or config.get("username")
            return {
                "logged_in": True,
                "warning": True,
                "expired": False,
                "username": username,
                "message": f"已登录: {username}" if username else "已登录",
                "account_chooser_url": account_chooser_url,
            }

        if session_status.get("expired", False):
            return {
                "logged_in": False,
                "expired": True,
                "message": "登录已过期，请重新运行 python app.py login",
                "account_chooser_url": account_chooser_url,
                "debug": {
                    "error": session_status.get("error"),
                    "raw_response": session_status.get("raw_response"),
                },
            }

        if session_status.get("error"):
            # 关键修复：如果 valid=False，即使有 error 也应该返回 logged_in=False
            is_valid = session_status.get("valid", False)
            return {
                "logged_in": is_valid,
                "warning": True,
                "expired": not is_valid,  # valid=False 意味着实际上已过期/无效
                "message": f"检查状态失败: {session_status['error']}" if is_valid else "登录已失效，请重新登录",
                "account_chooser_url": account_chooser_url,
                "debug": {
                    "error": session_status.get("error"),
                    "raw_response": session_status.get("raw_response"),
                },
            }

        return {
            "logged_in": True,
            "session_valid": session_status.get("valid", True),
            "username": session_status.get("username"),
            "signout_url": session_status.get("signout_url"),
            "message": f"已登录: {session_status.get('username')}" if session_status.get("username") else "已登录",
            "account_chooser_url": account_chooser_url,
        }

    except Exception as e:
        return {
            "logged_in": False,
            "error": str(e),
            "account_chooser_url": None,
        }


@app.get("/api/debug/session-status")
async def debug_session_status() -> dict:
    """调试端点：查看 list-sessions 原始返回

    返回信息包括：
    - cookie_lengths: 各 cookie 的长度，帮助确认 cookie 是否完整
    - cookie_header_preview: 实际使用的 cookie header 预览（前100字符）
    - cookie_header_length: 实际使用的 cookie header 长度
    - cookie_source: cookie 来源（cookie_raw 或 fields）
    - cookies_saved_at: cookie 保存时间
    - cookie_profile_dir: cookie 来源的浏览器用户数据目录（如果有）
    - 多账号检测提示
    """
    import httpx
    from biz_gemini.auth import _build_cookie_header

    config = load_config()
    secure_c_ses = config.get("secure_c_ses")
    host_c_oses = config.get("host_c_oses")
    nid = config.get("nid")
    csesidx = config.get("csesidx")
    cookie_raw = config.get("cookie_raw")

    # Cookie 长度信息（帮助确认 cookie 是否完整）
    cookie_lengths = {
        "secure_c_ses_len": len(secure_c_ses) if secure_c_ses else 0,
        "host_c_oses_len": len(host_c_oses) if host_c_oses else 0,
        "nid_len": len(nid) if nid else 0,
        "cookie_raw_len": len(cookie_raw) if cookie_raw else 0,
    }

    # Cookie 保存时间和来源目录
    cookies_saved_at = config.get("cookies_saved_at")
    cookie_profile_dir = config.get("cookie_profile_dir")

    if not secure_c_ses or not csesidx:
        return {
            "error": "缺少凭证",
            "cookie_lengths": cookie_lengths,
            "cookies_saved_at": cookies_saved_at,
            "cookie_profile_dir": cookie_profile_dir,
        }

    # 使用 _build_cookie_header 获取实际使用的 cookie 和调试信息
    cookie_str, cookie_debug = _build_cookie_header(config)

    url = f"https://auth.business.gemini.google/list-sessions?csesidx={csesidx}&rt=json"
    proxy = get_proxy(config)

    try:
        client_kwargs = {"verify": False, "timeout": 30.0}
        if proxy:
            client_kwargs["proxy"] = proxy

        with httpx.Client(**client_kwargs) as client:
            resp = client.get(
                url,
                headers={
                    "accept": "*/*",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "origin": "https://business.gemini.google",
                    "referer": "https://business.gemini.google/",
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-site",
                    "cookie": cookie_str,
                },
            )

        text = resp.text
        raw_text = text[:500]  # 保存原始文本前500字符用于调试
        if text.startswith(")]}'"):
            text = text[4:].strip()

        data = json.loads(text)

        # 检查 session 状态
        sessions_list = data.get("sessions", [])
        matched_session = None
        csesidx_str = str(csesidx)
        for sess in sessions_list:
            if str(sess.get("csesidx", "")) == csesidx_str:
                matched_session = sess
                break

        # 多账号检测
        multi_account_warning = None
        if len(sessions_list) > 1:
            # 检查是否有多个不同的 csesidx
            unique_csesidx = set(str(sess.get("csesidx", "")) for sess in sessions_list)
            if len(unique_csesidx) > 1:
                multi_account_warning = "检测到多账号 cookie，建议清空浏览器数据后重新登录"
                logger.warning(f"检测到多账号 cookie: {unique_csesidx}")

        result = {
            "http_status": resp.status_code,
            "proxy_used": proxy,
            "csesidx_in_config": csesidx,
            "csesidx_type": type(csesidx).__name__,
            "raw_text_preview": raw_text,
            "raw_response": data,
            "sessions_count": len(sessions_list),
            "matched_session": matched_session,
            "first_session_csesidx": sessions_list[0].get("csesidx") if sessions_list else None,
            "first_session_csesidx_type": type(sessions_list[0].get("csesidx")).__name__ if sessions_list else None,
            "check_result": check_session_status(config),
            # Cookie 调试字段
            "cookie_lengths": cookie_lengths,
            "cookies_saved_at": cookies_saved_at,
            "cookie_profile_dir": cookie_profile_dir,
            # 新增：实际使用的 cookie header 信息
            "cookie_header_preview": cookie_debug.get("cookie_header_preview"),
            "cookie_header_length": cookie_debug.get("cookie_header_length"),
            "cookie_source": cookie_debug.get("cookie_source"),
        }

        if multi_account_warning:
            result["multi_account_warning"] = multi_account_warning

        return result
    except Exception as e:
        import traceback
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "cookie_lengths": cookie_lengths,
            "cookies_saved_at": cookies_saved_at,
            "cookie_profile_dir": cookie_profile_dir,
            "cookie_header_preview": cookie_debug.get("cookie_header_preview") if 'cookie_debug' in dir() else None,
            "cookie_header_length": cookie_debug.get("cookie_header_length") if 'cookie_debug' in dir() else None,
            "cookie_source": cookie_debug.get("cookie_source") if 'cookie_debug' in dir() else None,
        }


@app.post("/api/config/reload")
async def reload_config_endpoint() -> dict:
    """手动重载配置"""
    try:
        new_config = reload_config()
        logger.info("配置手动重载成功")
        return {
            "success": True,
            "message": "配置重载成功",
            "config": {
                "server": new_config.get("server", {}),
                "proxy_enabled": new_config.get("proxy", {}).get("enabled", False) if isinstance(new_config.get("proxy"), dict) else bool(new_config.get("proxy")),
            },
        }
    except Exception as e:
        logger.warning(f"配置重载失败: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@app.post("/api/login/start")
async def start_login(headless: bool = False) -> dict:
    """启动浏览器登录流程"""
    try:
        login_service = get_login_service()
        task = await login_service.start_login(headless=headless)
        return {
            "success": True,
            "task": task.to_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/login/status")
async def get_login_status(task_id: Optional[str] = None) -> dict:
    """获取登录状态"""
    login_service = get_login_service()
    
    if task_id:
        task = await login_service.get_task(task_id)
    else:
        # 获取最新任务
        task = await login_service.get_latest_task()
    
    if not task:
        return {
            "success": False,
            "message": "未找到登录任务",
        }
    
    return {
        "success": True,
        "task": task.to_dict(),
    }


@app.post("/api/login/cancel")
async def cancel_login(task_id: str) -> dict:
    """取消登录流程"""
    login_service = get_login_service()
    success = await login_service.cancel_task(task_id)

    if success:
        return {
            "success": True,
            "message": "登录已取消",
        }
    else:
        return {
            "success": False,
            "message": "任务不存在或无法取消",
        }


@app.post("/api/logout")
async def logout() -> dict:
    """退出登录

    调用 Google 的 singleSessionSignoutUrl 进行登出，
    并清除本地配置中的 session 信息。
    """
    import httpx

    try:
        config = load_config()

        # 先检查 session 状态获取 signout_url
        session_status = check_session_status(config)
        signout_url = session_status.get("signout_url")

        # 清除本地 session 配置
        clear_config = {
            "secure_c_ses": "",
            "host_c_oses": "",
            "nid": "",
            "csesidx": "",
            "cookie_raw": "",
            "cookies_saved_at": "",
        }
        save_config(clear_config)

        # 清除内存中的会话缓存
        sessions.clear()
        anthropic_sessions.clear()

        # 如果有 signout_url，尝试调用（可选，不影响本地登出）
        signout_called = False
        if signout_url:
            try:
                proxy = get_proxy(config)
                client_kwargs = {"verify": False, "timeout": 10.0, "follow_redirects": True}
                if proxy:
                    client_kwargs["proxy"] = proxy

                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.get(signout_url)
                    signout_called = resp.status_code in (200, 302, 303)
            except Exception as e:
                logger.warning(f"调用 signout URL 失败: {e}")

        return {
            "success": True,
            "message": "已退出登录",
            "signout_called": signout_called,
            "signout_url": signout_url,
        }

    except Exception as e:
        logger.error(f"退出登录失败: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# ==================== IMAP 配置端点 ====================

class ImapConfigRequest(BaseModel):
    enabled: bool = False
    host: str = ""
    port: int = 993
    user: str = ""
    password: str = ""
    use_ssl: bool = True
    folder: str = "INBOX"
    sender_filter: str = "noreply-googlecloud@google.com"
    code_pattern: str = 'class="x_verification-code">([A-Z0-9]{6})</span>'
    max_age_seconds: int = 300
    timeout_seconds: int = 180
    poll_interval: int = 5
    auto_login: bool = True


class KeepAliveConfigRequest(BaseModel):
    enabled: bool = False
    interval_minutes: int = 60
    headless: bool = True


@app.get("/api/config/imap")
async def get_imap_config() -> dict:
    """获取 IMAP 配置（密码脱敏）"""
    try:
        config = load_config()
        imap_config = config.get("imap", {})
        keep_alive_config = config.get("browser_keep_alive", {})
        
        # 脱敏密码和邮箱
        safe_config = {**imap_config}
        if safe_config.get("password"):
            safe_config["password"] = "******"
        
        # 脱敏邮箱地址（保护隐私，防止垃圾邮件）
        if safe_config.get("user"):
            user = safe_config["user"]
            if "@" in user:
                local_part, domain = user.split("@", 1)
                # 脱敏本地部分：保留首字符，中间用*替代
                if len(local_part) > 2:
                    masked_local = local_part[0] + "*" * (len(local_part) - 2) + local_part[-1]
                elif len(local_part) > 0:
                    masked_local = local_part[0] + "*" * (len(local_part) - 1)
                else:
                    masked_local = "*"
                # 脱敏域名部分：保留首字符和后缀
                domain_parts = domain.rsplit(".", 1)
                if len(domain_parts) == 2:
                    domain_name, tld = domain_parts
                    if len(domain_name) > 2:
                        masked_domain = domain_name[0] + "*" * (len(domain_name) - 2) + domain_name[-1] + "." + tld
                    else:
                        masked_domain = domain_name[0] + "*." + tld
                else:
                    masked_domain = domain[0] + "*" * (len(domain) - 1)
                safe_config["user"] = f"{masked_local}@{masked_domain}"
        
        return {
            "success": True,
            "imap": safe_config,
            "browser_keep_alive": keep_alive_config,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


@app.post("/api/config/imap")
async def save_imap_config(request: ImapConfigRequest) -> dict:
    """保存 IMAP 配置"""
    try:
        config = load_config()
        current_imap = config.get("imap", {})
        
        # 如果密码是脱敏的占位符，保留原密码
        new_config = request.model_dump()
        if new_config.get("password") == "******":
            new_config["password"] = current_imap.get("password", "")
        
        # 如果邮箱是脱敏的值（包含*），保留原邮箱
        if new_config.get("user") and "*" in new_config.get("user", ""):
            new_config["user"] = current_imap.get("user", "")
        
        save_config({"imap": new_config})
        
        return {
            "success": True,
            "message": "IMAP 配置已保存",
        }
    except Exception as e:
        logger.error(f"保存 IMAP 配置失败: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@app.post("/api/config/imap/test")
async def test_imap_config(request: ImapConfigRequest) -> dict:
    """测试 IMAP 连接"""
    from biz_gemini.imap_reader import test_imap_connection
    
    try:
        config = load_config()
        current_imap = config.get("imap", {})
        
        # 如果密码是脱敏的占位符，使用当前配置的密码
        test_config = request.model_dump()
        if test_config.get("password") == "******":
            test_config["password"] = current_imap.get("password", "")
        
        # 如果邮箱是脱敏的值（包含*），使用当前配置的邮箱
        if test_config.get("user") and "*" in test_config.get("user", ""):
            test_config["user"] = current_imap.get("user", "")
        
        result = await test_imap_connection(test_config)
        return result
        
    except Exception as e:
        logger.error(f"测试 IMAP 连接失败: {e}")
        return {
            "success": False,
            "message": f"测试失败: {str(e)}",
        }


@app.post("/api/config/keep-alive")
async def save_keep_alive_config(request: KeepAliveConfigRequest) -> dict:
    """保存保活配置"""
    try:
        save_config({"browser_keep_alive": request.model_dump()})
        
        return {
            "success": True,
            "message": "保活配置已保存，重启服务后生效",
        }
    except Exception as e:
        logger.error(f"保存保活配置失败: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# ==================== API Key 管理端点 ====================

class PasswordRequest(BaseModel):
    password: str


class ApiKeyCreateRequest(BaseModel):
    password: str
    name: str = ""


@app.post("/api/auth/verify-password")
async def verify_password(request: PasswordRequest) -> dict:
    """验证管理密码
    
    首次调用时设置密码，后续调用验证密码
    """
    config = load_config()
    security = config.get("security", {})
    stored_password = security.get("admin_password", "")
    
    if not stored_password:
        # 首次设置密码
        if len(request.password) < 4:
            raise HTTPException(status_code=400, detail="密码长度至少4位")
        save_config({"security": {"admin_password": request.password}})
        return {"success": True, "message": "密码设置成功", "is_new": True}
    
    # 验证密码
    if request.password == stored_password:
        return {"success": True, "message": "密码验证成功", "is_new": False}
    else:
        raise HTTPException(status_code=401, detail="密码错误")


@app.get("/api/auth/has-password")
async def has_password() -> dict:
    """检查是否已设置管理密码"""
    config = load_config()
    security = config.get("security", {})
    has_pwd = bool(security.get("admin_password", ""))
    return {"has_password": has_pwd}


@app.get("/api/keys")
async def get_api_keys(password: str) -> dict:
    """获取所有 API Key（脱敏显示）"""
    # 验证密码
    config = load_config()
    security = config.get("security", {})
    stored_password = security.get("admin_password", "")
    
    if not stored_password or password != stored_password:
        raise HTTPException(status_code=401, detail="密码错误")
    
    keys = list_api_keys(include_full_key=False)
    return {"success": True, "keys": keys}


@app.post("/api/keys")
async def create_api_key(request: ApiKeyCreateRequest) -> dict:
    """生成新的 API Key"""
    # 验证密码
    config = load_config()
    security = config.get("security", {})
    stored_password = security.get("admin_password", "")
    
    if not stored_password or request.password != stored_password:
        raise HTTPException(status_code=401, detail="密码错误")
    
    key_data = generate_api_key(name=request.name)
    return {"success": True, "key": key_data}


@app.get("/api/keys/{key_id}")
async def get_full_api_key(key_id: int, password: str) -> dict:
    """获取完整的 API Key（用于复制）"""
    # 验证密码
    config = load_config()
    security = config.get("security", {})
    stored_password = security.get("admin_password", "")
    
    if not stored_password or password != stored_password:
        raise HTTPException(status_code=401, detail="密码错误")
    
    key_data = get_api_key_by_id(key_id)
    if not key_data:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    
    return {"success": True, "key": key_data}


@app.delete("/api/keys/{key_id}")
async def remove_api_key(key_id: int, password: str) -> dict:
    """删除 API Key"""
    # 验证密码
    config = load_config()
    security = config.get("security", {})
    stored_password = security.get("admin_password", "")
    
    if not stored_password or password != stored_password:
        raise HTTPException(status_code=401, detail="密码错误")
    
    success = delete_api_key(key_id)
    if success:
        return {"success": True, "message": "API Key 已删除"}
    else:
        raise HTTPException(status_code=404, detail="API Key 不存在")


@app.get("/api/sessions")
async def list_sessions() -> list:
    """列出所有会话 - 对接 Google 官方接口"""
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            # 未登录时直接返回空列表
            return []

        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)

        # 调用 Google 官方接口
        api_response = biz_client.list_sessions()
        sessions_response = api_response.get("listSessionsResponse", {})
        google_sessions = sessions_response.get("sessions", [])

        result = []
        for sess in google_sessions:
            # 从 session name 中提取 session_id
            # 格式: collections/default_collection/engines/agentspace-engine/sessions/11281810546812518810
            session_name = sess.get("name", "")
            session_id = session_name.split("/")[-1] if "/" in session_name else session_name

            # 获取 displayName 作为标题
            title = sess.get("displayName", "")
            if not title:
                # 如果没有 displayName，使用第一条消息的文本
                turns = sess.get("turns", [])
                if turns:
                    first_query = turns[0].get("query", {})
                    title = first_query.get("text", "新对话")[:30]

            # 解析时间
            start_time = sess.get("startTime", "")
            created_at = 0
            if start_time:
                try:
                    from datetime import datetime
                    # 处理 ISO 格式时间
                    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    created_at = dt.timestamp()
                except Exception:
                    pass

            # 获取消息数
            turns = sess.get("turns", [])
            message_count = len(turns) * 2  # 每个 turn 包含问和答

            result.append({
                "session_id": session_id,
                "session_name": session_name,  # 完整的 session name，删除时需要
                "title": title,
                "created_at": created_at,
                "message_count": message_count,
                "state": sess.get("state", ""),
                "labels": sess.get("labels", []),
            })

        return result

    except RuntimeError as e:
        error_msg = str(e)
        # 检查是否是权限错误（403）- 这种情况需要告知前端
        if "403" in error_msg or "PERMISSION_DENIED" in error_msg:
            logger.error(f"获取会话列表权限错误: {error_msg}")
            raise HTTPException(status_code=403, detail=f"权限不足，请检查 group_id 配置是否正确: {error_msg}")
        # 其他错误返回本地缓存
        logger.warning(f"获取会话列表失败，返回本地缓存: {e}")
        result = []
        for sid, data in sessions.items():
            result.append({
                "session_id": sid,
                "title": data.get("title", "新对话"),
                "created_at": data.get("created_at", 0),
                "message_count": len(data.get("messages", [])),
            })
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result
    except Exception as e:
        # 出错时返回本地缓存
        logger.warning(f"获取会话列表失败，返回本地缓存: {e}")
        result = []
        for sid, data in sessions.items():
            result.append({
                "session_id": sid,
                "title": data.get("title", "新对话"),
                "created_at": data.get("created_at", 0),
                "message_count": len(data.get("messages", [])),
            })
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result


@app.post("/api/sessions")
async def create_session() -> dict:
    """创建新会话 - 直接创建 Google 会话并返回真实 session_id"""
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            # 未登录时返回临时 UUID（后续首次发消息时会创建真实会话）
            temp_id = str(uuid.uuid4())
            return {"session_id": temp_id, "is_temp": True}

        # 创建 BizGeminiClient 并获取真实的 Google session
        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)
        # 触发会话创建
        session_name = biz_client.session_name
        # 从 session_name 提取真实的 session_id（name 最后一段）
        # 格式: collections/default_collection/engines/agentspace-engine/sessions/11281810546812518810
        real_session_id = session_name.split("/")[-1] if "/" in session_name else session_name

        # 预先存储到 sessions 字典
        client = OpenAICompatClient(biz_client)
        sessions[real_session_id] = {
            "client": client,
            "biz_client": biz_client,
            "session_name": session_name,
            "messages": [],
            "title": "新对话",
            "created_at": time.time(),
        }

        logger.debug(f"创建新会话: session_id={real_session_id}, session_name={session_name}")
        return {
            "session_id": real_session_id,
            "session_name": session_name,
            "is_temp": False,
        }
    except Exception as e:
        logger.warning(f"创建 Google 会话失败，使用临时 UUID: {e}")
        temp_id = str(uuid.uuid4())
        return {"session_id": temp_id, "is_temp": True}


@app.delete("/api/sessions/{session_id:path}")
async def delete_session(session_id: str) -> dict:
    """删除会话 - 支持 session_id 或完整的 session_name"""
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]

        if not missing:
            jwt_manager = JWTManager(config=config)
            biz_client = BizGeminiClient(config, jwt_manager)

            # 判断是完整的 session_name 还是简短的 session_id
            # 完整格式: collections/default_collection/engines/agentspace-engine/sessions/xxx
            if "/" in session_id:
                session_name = session_id
            else:
                # 需要从会话列表中找到完整的 session_name
                # 或者尝试构造（如果知道格式）
                session_name = session_id

            try:
                biz_client.delete_session(session_name)
            except Exception as e:
                logger.warning(f"删除 Google 会话失败: {e}")

        # 同时删除本地缓存
        # 从本地 sessions 中查找匹配的会话
        to_delete = []
        for sid, data in sessions.items():
            if sid == session_id or data.get("session_name") == session_id:
                to_delete.append(sid)
            elif session_id.endswith(sid):  # session_name 以 session_id 结尾
                to_delete.append(sid)

        for sid in to_delete:
            del sessions[sid]

        return {"success": True}
    except Exception as e:
        logger.error(f"删除会话时出错: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, session_name: Optional[str] = None) -> dict:
    """获取会话消息历史 - 始终从 Google API 拉取，避免本地缓存丢失信息"""
    def _parse_dt(ts: Optional[str]) -> Optional[datetime]:
        if not ts:
            return None
        try:
            # 兼容末尾 Z
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None

    def _to_iso_z(dt_obj: Optional[datetime]) -> str:
        if not dt_obj:
            return ""
        return dt_obj.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    # 仅依赖 Google 接口返回的消息（避免本地缓存造成信息缺失）
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            return {"messages": []}

        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)

        # 确定 session_name：可能是完整路径或简短 ID
        # 如果传入了 session_name 参数则直接使用
        # 否则尝试从会话列表中查找
        target_session_name = session_name
        if not target_session_name:
            # 尝试用 session_id 直接作为 name（适用于简短 ID）
            target_session_name = session_id

        # 调用 Google API 获取会话详情
        api_response = biz_client.get_session(target_session_name)
        session_data = api_response.get("session", {})
        turns = session_data.get("turns", [])

        # 获取完整的 session name（用于构造图片下载 URL）
        full_session_name = session_data.get("name", "")

        def build_skipped_message(detailed_answer: dict) -> Optional[dict]:
            """构造跳过回复时的提示信息（返回结构化数据）"""
            state = detailed_answer.get("state")
            reasons = detailed_answer.get("assistSkippedReasons") or []
            if state != "SKIPPED":
                return None

            # 组织自定义策略阻断
            if "CUSTOMER_POLICY_VIOLATION" in reasons:
                violation_detail = None
                policy_result = detailed_answer.get("customerPolicyEnforcementResult") or {}
                for pr in policy_result.get("policyResults") or []:
                    armor_result = pr.get("modelArmorEnforcementResult") or {}
                    violation_detail = armor_result.get("modelArmorViolation")
                    if violation_detail:
                        break

                return {
                    "type": "CUSTOMER_POLICY_VIOLATION",
                    "title": "由于提示违反了您组织定义的安全政策，因此 Gemini Enterprise 无法回复。",
                    "detail": violation_detail or ""
                }

            if reasons:
                return {
                    "type": "SKIPPED",
                    "title": "Gemini Enterprise 未能生成回复",
                    "detail": f"原因: {', '.join(reasons)}"
                }

            return {
                "type": "SKIPPED",
                "title": "Gemini Enterprise 未能生成回复",
                "detail": "状态: SKIPPED"
            }

        messages = []
        # 收集所有图片的 fileId，稍后批量获取元数据
        all_file_ids = []
        # 收集所有用户上传文件的 sessionContextFileId
        all_context_file_ids = []

        for turn in turns:
            diagnostic_info = (
                turn.get("diagnosticInfo")
                or turn.get("detailedAssistAnswer", {}).get("diagnosticInfo")
                or {}
            )
            planner_steps = diagnostic_info.get("plannerSteps") or []

            # 时间线提取：用户时间、助手时间、思考耗时
            query_ts = None
            plan_times = []
            plan_steps_detail = []
            for step in planner_steps:
                create_time = _parse_dt(step.get("createTime"))
                if not create_time:
                    continue
                if step.get("queryStep") and not query_ts:
                    query_ts = create_time
                if step.get("planStep"):
                    plan_times.append(create_time)
                    # 收集 planStep 文本，用于思考耗时匹配
                    parts = step.get("planStep", {}).get("parts") or []
                    part_texts = []
                    for p in parts:
                        if isinstance(p, dict) and p.get("text"):
                            part_texts.append(p["text"])
                    combined_text = "\n".join(part_texts).strip()
                    plan_steps_detail.append({
                        "ts": create_time,
                        "text": combined_text,
                    })

            user_ts = _to_iso_z(query_ts) or turn.get("createdAt", "")
            assistant_ts = _to_iso_z(plan_times[-1]) if plan_times else turn.get("createdAt", "")
            thinking_duration_ms = None
            if len(plan_times) >= 2:
                thinking_duration_ms = int((plan_times[-1] - plan_times[0]).total_seconds() * 1000)

            # 解析用户消息
            query = turn.get("query", {})
            query_text = query.get("text", "")

            # 收集用户消息中的 contextFiles
            context_files = query.get("contextFiles", [])
            turn_context_file_ids = []
            for cf in context_files:
                file_id = cf.get("sessionContextFileId")
                if file_id:
                    turn_context_file_ids.append(file_id)
                    all_context_file_ids.append(file_id)

            if query_text or turn_context_file_ids:
                user_msg = {
                    "role": "user",
                    "content": query_text,
                    "timestamp": user_ts,
                }
                # 暂存 contextFileIds，稍后填充完整元数据
                if turn_context_file_ids:
                    user_msg["_context_file_ids"] = turn_context_file_ids
                messages.append(user_msg)

            # 解析 AI 回复 - 优先使用 detailedAssistAnswer
            detailed_answer = turn.get("detailedAssistAnswer", {})
            replies = detailed_answer.get("replies", [])

            # 如果 detailedAssistAnswer 没有 replies，尝试从 diagnosticInfo.plannerSteps 提取回复
            # 这种情况可能发生在 Google API 只返回最近一条的详细信息时
            if not replies and planner_steps:
                # 从 plannerSteps 中提取非思考内容作为回复
                fallback_texts = []
                fallback_thoughts = []
                for step in planner_steps:
                    plan_step = step.get("planStep", {})
                    if not plan_step:
                        continue
                    parts = plan_step.get("parts") or []
                    for p in parts:
                        if isinstance(p, dict) and p.get("text"):
                            text = p["text"].strip()
                            if text:
                                # 检查是否是思考内容（通常以 ** 开头或很短）
                                if text.startswith("**") and text.endswith("**\n"):
                                    fallback_thoughts.append(text)
                                else:
                                    fallback_texts.append(text)

                if fallback_texts:
                    # 合并所有文本片段
                    combined_text = "".join(fallback_texts)
                    msg_data = {
                        "role": "assistant",
                        "content": combined_text,
                        "timestamp": assistant_ts,
                    }
                    if fallback_thoughts:
                        msg_data["thoughts"] = fallback_thoughts
                    if thinking_duration_ms is not None:
                        msg_data["thinking_duration_ms"] = thinking_duration_ms
                    messages.append(msg_data)
                    # 跳过后续的 replies 处理
                    replies = None

            skipped_info = None if replies else build_skipped_message(detailed_answer) if replies is not None else None

            if replies:
                # 分别收集文本、思考和图片
                reply_texts = []
                thoughts = []
                turn_file_ids = []
                matched_plan_ts = []

                for reply in replies:
                    grounded_content = reply.get("groundedContent", {})
                    content = grounded_content.get("content", {})
                    text = content.get("text", "")
                    is_thought = content.get("thought", False)

                    # 检查是否有图片文件
                    file_info = content.get("file")
                    if file_info:
                        logger.debug(f"历史消息中的图片信息: {file_info}")
                        if file_info.get("fileId"):
                            file_id = file_info["fileId"]
                            turn_file_ids.append({
                                "fileId": file_id,
                                "mimeType": file_info.get("mimeType", "image/png"),
                                "fileName": file_info.get("name", ""),
                            })
                            all_file_ids.append(file_id)

                    if text:
                        if is_thought:
                            thoughts.append(text)
                            # 将 planStep 文本与思考内容匹配，提取更精确的思考耗时
                            if plan_steps_detail:
                                normalized_thought = text.strip()
                                for ps in plan_steps_detail:
                                    if not ps["ts"] or not ps["text"]:
                                        continue
                                    if ps["text"] and (ps["text"] in normalized_thought or normalized_thought in ps["text"]):
                                        matched_plan_ts.append(ps["ts"])
                        else:
                            reply_texts.append(text)

                if reply_texts or turn_file_ids:
                    msg_data = {
                        "role": "assistant",
                        "content": "\n\n".join(reply_texts) if reply_texts else "",
                        "timestamp": assistant_ts,
                    }

                    # 添加思考链
                    if thoughts:
                        msg_data["thoughts"] = thoughts
                        # 优先使用与思考文本匹配的 planStep 时间
                        if len(matched_plan_ts) >= 2:
                            matched_plan_ts = sorted(matched_plan_ts)
                            msg_data["thinking_duration_ms"] = (matched_plan_ts[-1] - matched_plan_ts[0]).total_seconds() * 1000
                        elif len(matched_plan_ts) == 1 and plan_steps_detail:
                            # 仅匹配到一个步骤时，尝试使用它之后的下一个 planStep 作为结束时间
                            matched_ts = matched_plan_ts[0]
                            next_ts = None
                            sorted_steps = sorted(plan_steps_detail, key=lambda x: x["ts"] or datetime.min)
                            for idx, ps in enumerate(sorted_steps):
                                if ps["ts"] == matched_ts and idx + 1 < len(sorted_steps):
                                    next_ts = sorted_steps[idx + 1]["ts"]
                                    break
                            if next_ts:
                                msg_data["thinking_duration_ms"] = (next_ts - matched_ts).total_seconds() * 1000
                            elif thinking_duration_ms is not None:
                                msg_data["thinking_duration_ms"] = thinking_duration_ms
                        elif thinking_duration_ms is not None:
                            msg_data["thinking_duration_ms"] = thinking_duration_ms

                    # 添加图片信息（稍后填充完整元数据）
                    if turn_file_ids:
                        msg_data["images"] = turn_file_ids

                    messages.append(msg_data)
            elif skipped_info:
                messages.append({
                    "role": "assistant",
                    "content": "",  # 内容为空，由前端根据 error_info 渲染
                    "timestamp": assistant_ts,
                    "skipped": True,
                    "error_info": skipped_info,  # 结构化错误信息
                    "skipped_reasons": detailed_answer.get("assistSkippedReasons") or [],
                })

        # 批量获取文件元数据并更新 messages（包括图片和用户上传的附件）
        if (all_file_ids or all_context_file_ids) and full_session_name:
            try:
                file_metadata: dict = {}
                # 优先获取 AI 生成的文件（通常是图片）
                if all_file_ids:
                    file_metadata.update(biz_client._get_session_file_metadata(full_session_name, filter_str="file_origin_type = AI_GENERATED"))
                # 用户上传的上下文文件需要 USER_UPLOADED
                if all_context_file_ids:
                    file_metadata.update(biz_client._get_session_file_metadata(full_session_name, filter_str="file_origin_type = USER_UPLOADED"))
                logger.debug(f"[get_session_messages] full_session_name={full_session_name}")
                logger.debug(f"[get_session_messages] all_file_ids={all_file_ids}")
                logger.debug(f"[get_session_messages] file_metadata keys={list(file_metadata.keys())}")

                # 从 full_session_name 提取 session_id 用于构造 URL
                # 格式: projects/.../sessions/17970885850102128104
                api_session_id = full_session_name.split("/")[-1] if "/" in full_session_name else session_id

                # 更新每条消息中的图片信息和附件信息
                for msg in messages:
                    # 处理 AI 回复中的图片
                    if "images" in msg:
                        enriched_images = []
                        for img_info in msg["images"]:
                            file_id = img_info["fileId"]
                            meta = file_metadata.get(file_id, {})

                            # 构建完整的图片信息
                            local_filename = meta.get("name") or img_info.get("fileName", "") or f"gemini_{file_id}.png"
                            # byteSize 在 API 响应中是字符串，需要转换为整数
                            byte_size_str = meta.get("byteSize")
                            byte_size = int(byte_size_str) if byte_size_str else None
                            token_count_str = meta.get("tokenCount")
                            token_count = int(token_count_str) if token_count_str else None
                            quota_percentage = meta.get("quotaPercentage")
                            download_uri = meta.get("downloadUri")
                            upload_time = meta.get("uploadTime")
                            file_origin_type = meta.get("fileOriginType")
                            # 解析缩略图
                            thumbnails = {}
                            for view_name, view_data in (meta.get("views") or {}).items():
                                img_chars = view_data.get("imageCharacteristics") or {}
                                thumbnails[view_name] = {
                                    "view_id": view_data.get("viewId"),
                                    "uri": view_data.get("uri"),
                                    "mime_type": view_data.get("mimeType"),
                                    "byte_size": int(view_data["byteSize"]) if view_data.get("byteSize") else None,
                                    "width": img_chars.get("width"),
                                    "height": img_chars.get("height"),
                                }
                            enriched_img = {
                                "file_id": file_id,
                                "file_name": local_filename,
                                "mime_type": meta.get("mimeType") or img_info.get("mimeType", "image/png"),
                                "byte_size": byte_size,
                                "token_count": token_count,
                                "quota_percentage": quota_percentage,
                                "download_uri": download_uri,
                                "upload_time": upload_time,
                                "file_origin_type": file_origin_type,
                                "session": meta.get("session") or full_session_name,
                            }
                            if thumbnails:
                                enriched_img["thumbnails"] = thumbnails

                            # 检查本地是否有缓存
                            local_path = os.path.join(IMAGES_DIR, local_filename)
                            if os.path.exists(local_path):
                                enriched_img["local_path"] = local_path
                                enriched_img["url"] = f"/api/images/{local_filename}"
                            else:
                                # 本地没有缓存，提供按需下载的 URL
                                enriched_img["url"] = f"/api/sessions/{api_session_id}/images/{file_id}?session_name={full_session_name}"

                            enriched_images.append(enriched_img)

                        msg["images"] = enriched_images

                    # 处理用户消息中的附件（contextFiles）
                    if "_context_file_ids" in msg:
                        attachments = []
                        for file_id in msg["_context_file_ids"]:
                            meta = file_metadata.get(file_id, {})
                            if meta:
                                attachments.append({
                                    "file_id": file_id,
                                    "file_name": meta.get("name") or file_id,
                                    "mime_type": meta.get("mimeType"),
                                    "byte_size": meta.get("byteSize"),
                                    "token_count": meta.get("tokenCount"),
                                })
                            else:
                                # 元数据中没有找到，只保留 file_id
                                attachments.append({
                                    "file_id": file_id,
                                    "file_name": file_id,
                                })
                        if attachments:
                            msg["attachments"] = attachments
                        # 删除临时字段
                        del msg["_context_file_ids"]

            except Exception as e:
                logger.warning(f"获取文件元数据失败: {e}")
                # 清理临时字段
                for msg in messages:
                    if "_context_file_ids" in msg:
                        del msg["_context_file_ids"]

        # 清理可能残留的临时字段（如果没有进入上面的 if 块）
        for msg in messages:
            if "_context_file_ids" in msg:
                del msg["_context_file_ids"]

        return {"messages": messages}

    except Exception as e:
        logger.warning(f"从 Google API 获取会话消息失败: {e}")
        return {"messages": []}


@app.put("/api/sessions/{session_id}/title")
async def update_session_title(session_id: str, title: str) -> dict:
    """更新会话标题"""
    if session_id in sessions:
        sessions[session_id]["title"] = title
    return {"success": True}


async def _chat_completions_handler(
    request: ChatRequest,
    x_session_id: Optional[str],
    conversation_id: Optional[str],
    response_obj: Response,
    strict_openai: bool = False,
) -> Union[dict, StreamingResponse]:
    """共享的 ChatCompletion 处理逻辑。

    strict_openai=True 时：
    - 只返回/流出 OpenAI 官方字段，不混入自定义 images/thoughts/final_metadata
    - 使用响应头传递 session 信息，避免破坏协议
    """
    response_obj = response_obj or Response()

    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail=f"配置缺失: {', '.join(missing)}，请先登录")

        # 检查保活服务的 session 状态（如果可用）
        keep_alive = get_keep_alive_service()
        keep_alive_status = keep_alive.get_status()
        if keep_alive_status.get("last_check") and not keep_alive_status.get("session_valid", True):
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    # 会话 ID 优先级: X-Session-Id header > Conversation-Id header > body.session_id > 新建
    session_id = x_session_id or conversation_id or request.session_id or str(uuid.uuid4())
    try:
        client, session_data, canonical_session_id = get_or_create_client(session_id, request.session_name)
    except RuntimeError as e:
        error_msg = str(e)
        # 检查是否是权限错误（403）
        if "403" in error_msg or "PERMISSION_DENIED" in error_msg:
            raise HTTPException(status_code=403, detail=f"权限不足，请检查 group_id 配置是否正确: {error_msg}")
        # 检查是否是认证错误
        if "401" in error_msg or "过期" in error_msg or "cookie" in error_msg.lower():
            raise HTTPException(status_code=401, detail=f"登录已过期，请重新登录: {error_msg}")
        # 其他错误
        raise HTTPException(status_code=500, detail=f"创建会话失败: {error_msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建会话失败: {str(e)}")

    # 将真实 session id 通过 header 返回（符合协议且方便第三方前端复用）
    if canonical_session_id:
        response_obj.headers["X-Session-Id"] = canonical_session_id
    if session_data.get("session_name"):
        response_obj.headers["X-Session-Name"] = session_data["session_name"]

    # 获取待发送的文件 ID
    pending_file_ids = session_data.get("pending_file_ids", [])
    # 合并前端显式传入的 file_ids，避免因本地缓存丢失而未携带文件
    combined_file_ids: list[str] = []
    if pending_file_ids:
        combined_file_ids.extend(pending_file_ids)
    if request.file_ids:
        for fid in request.file_ids:
            if fid and fid not in combined_file_ids:
                combined_file_ids.append(fid)
    logger.debug(f"聊天请求: session_id={session_id}, canonical_session_id={canonical_session_id}, session_name={session_data.get('session_name')}, pending_file_ids={pending_file_ids}")

    # 获取文件元数据用于渲染
    attachment_metadata: List[Dict[str, Any]] = []
    if combined_file_ids:
        try:
            session_name_for_files = session_data.get("session_name")
            metadata_map = session_data["biz_client"]._get_session_file_metadata(session_name_for_files, filter_str="") if session_name_for_files else {}
            for fid in combined_file_ids:
                meta = metadata_map.get(fid, {}) if metadata_map else {}
                attachment_metadata.append({
                    "file_id": fid,
                    "file_name": meta.get("name") or meta.get("fileId") or fid,
                    "mime_type": meta.get("mimeType"),
                    "byte_size": meta.get("byteSize"),
                    "token_count": meta.get("tokenCount"),
                    "download_uri": meta.get("downloadUri"),
                })
        except Exception as e:
            logger.warning(f"获取文件元数据失败: {e}")
            attachment_metadata = [{"file_id": fid} for fid in pending_file_ids]

    # 保存用户消息
    user_message = request.messages[-1] if request.messages else None
    if user_message:
        # 仅记录标题，不缓存整段消息，避免本地存储占用及信息不一致
        if not session_data.get("title"):
            if isinstance(user_message.content, str):
                preview = user_message.content
            elif isinstance(user_message.content, list):
                # 文本块拼接作为标题预览
                preview_parts = []
                for part in user_message.content:
                    if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                        preview_parts.append(str(part["text"]))
                preview = "\n".join(preview_parts) if preview_parts else ""
            else:
                preview = ""
            title = preview[:30]
            if len(preview) > 30:
                title += "..."
            session_data["title"] = title or "新对话"

    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    full_session_name = session_data.get("session_name")
    api_session_id = full_session_name.split("/")[-1] if full_session_name and "/" in full_session_name else canonical_session_id
    encoded_session_name = quote_plus(full_session_name) if full_session_name else None
    biz_client = session_data.get("biz_client")

    def _parse_dt(ts: Optional[str]) -> Optional[datetime]:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None

    def _to_iso_z(dt_obj: Optional[datetime]) -> str:
        if not dt_obj:
            return ""
        return dt_obj.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def fetch_latest_turn_meta() -> Optional[dict]:
        """从 Google 接口获取最新一条回复的时间和思考耗时"""
        if not biz_client or not full_session_name:
            return None
        try:
            api_resp = biz_client.get_session(full_session_name)
            session_obj = api_resp.get("session", {})
            turns = session_obj.get("turns", [])
            if not turns:
                return None
            last_turn = turns[-1]

            # 获取 detailedAssistAnswer
            detailed_answer = last_turn.get("detailedAssistAnswer", {})
            diagnostic_info = (
                last_turn.get("diagnosticInfo")
                or detailed_answer.get("diagnosticInfo")
                or {}
            )
            planner_steps = diagnostic_info.get("plannerSteps") or []

            # 构建 planStep 详情列表（文本和时间）
            plan_steps_detail: list[dict] = []
            for step in planner_steps:
                ct = _parse_dt(step.get("createTime"))
                plan_step = step.get("planStep")
                if ct and plan_step:
                    parts = plan_step.get("parts") or []
                    step_text = ""
                    for p in parts:
                        if isinstance(p, dict) and p.get("text"):
                            step_text += p["text"]
                    plan_steps_detail.append({"ts": ct, "text": step_text.strip()})

            # 从 replies 中提取思考链文本
            replies = detailed_answer.get("replies") or []
            thought_texts: list[str] = []
            for reply in replies:
                grounded_content = reply.get("groundedContent", {})
                content = grounded_content.get("content", {})
                text = content.get("text", "")
                is_thought = content.get("thought", False)
                if text and is_thought:
                    thought_texts.append(text.strip())

            # 匹配思考文本与 planStep，提取精确的思考耗时
            matched_plan_ts: list[datetime] = []
            if thought_texts and plan_steps_detail:
                for thought in thought_texts:
                    for ps in plan_steps_detail:
                        if not ps["ts"] or not ps["text"]:
                            continue
                        # 检查 planStep 文本是否包含在思考文本中，或思考文本包含在 planStep 中
                        if ps["text"] in thought or thought in ps["text"]:
                            if ps["ts"] not in matched_plan_ts:
                                matched_plan_ts.append(ps["ts"])

            thinking_duration_ms = None
            if len(matched_plan_ts) >= 2:
                matched_plan_ts = sorted(matched_plan_ts)
                thinking_duration_ms = int((matched_plan_ts[-1] - matched_plan_ts[0]).total_seconds() * 1000)
            elif len(matched_plan_ts) == 1 and plan_steps_detail:
                # 仅匹配到一个步骤时，尝试使用它之后的下一个 planStep 作为结束时间
                matched_ts = matched_plan_ts[0]
                sorted_steps = sorted(plan_steps_detail, key=lambda x: x["ts"] or datetime.min)
                for idx, ps in enumerate(sorted_steps):
                    if ps["ts"] == matched_ts and idx + 1 < len(sorted_steps):
                        next_ts = sorted_steps[idx + 1]["ts"]
                        thinking_duration_ms = int((next_ts - matched_ts).total_seconds() * 1000)
                        break

            # 如果没有匹配到思考文本，回退到使用所有 planStep 时间
            if thinking_duration_ms is None and len(plan_steps_detail) >= 2:
                all_times = sorted([ps["ts"] for ps in plan_steps_detail if ps["ts"]])
                if len(all_times) >= 2:
                    thinking_duration_ms = int((all_times[-1] - all_times[0]).total_seconds() * 1000)

            assistant_ts = _parse_dt(last_turn.get("createdAt"))
            if plan_steps_detail:
                # 使用最后一个 planStep 的时间作为助手回复时间
                sorted_steps = sorted(plan_steps_detail, key=lambda x: x["ts"] or datetime.min)
                assistant_ts = sorted_steps[-1]["ts"]
            assistant_iso = _to_iso_z(assistant_ts) if assistant_ts else None
            return {
                "assistant_timestamp": assistant_iso,
                "thinking_duration_ms": thinking_duration_ms,
            }
        except Exception as e:
            logger.warning(f"获取实时思考耗时失败: {e}")
            return None

    include_thoughts_flag = request.include_thoughts and (not strict_openai)

    if request.stream:
        async def generate():
            try:
                # 传递 file_ids 并在发送后清空
                file_ids_to_send = combined_file_ids.copy()
                session_data["pending_file_ids"] = []

                response = client.chat.completions.create(
                    model=request.model,
                    messages=messages,
                    stream=True,
                    include_image_data=request.include_image_data,
                    include_thoughts=include_thoughts_flag,
                    embed_images_in_content=request.embed_images_in_content,
                    file_ids=file_ids_to_send,
                )
                full_content = ""
                thoughts_data = []
                for chunk in response:
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    delta_content = delta.get("content", "")
                    delta_thought = delta.get("thought", "")
                    if delta_content:
                        full_content += delta_content
                    if delta_thought:
                        thoughts_data.append(delta_thought)

                    if strict_openai:
                        # strict 模式：移除非标准字段，只保留 OpenAI 标准字段
                        clean_chunk = {
                            "id": chunk.get("id"),
                            "object": chunk.get("object"),
                            "created": chunk.get("created"),
                            "model": chunk.get("model"),
                            "choices": chunk.get("choices", []),
                        }
                        # 移除 delta 中的非标准字段（如 thought）
                        for choice in clean_chunk.get("choices", []):
                            if "delta" in choice and "thought" in choice["delta"]:
                                del choice["delta"]["thought"]
                        yield f"data: {json.dumps(clean_chunk, ensure_ascii=False)}\n\n"
                    else:
                        # 非 strict 模式：保留自定义字段，增强图片信息
                        if "images" in chunk:
                            raw_images = chunk.get("images") or []
                            enriched_images = []
                            for img in raw_images:
                                img_copy = dict(img)
                                file_obj = img.get("file") or {}
                                file_id = (
                                    img.get("file_id")
                                    or img.get("fileId")
                                    or file_obj.get("file_id")
                                    or file_obj.get("fileId")
                                )
                                if file_id:
                                    img_copy.setdefault("file_id", file_id)
                                mime_type = img.get("mime_type") or img.get("mimeType") or file_obj.get("mimeType")
                                if mime_type:
                                    img_copy.setdefault("mime_type", mime_type)
                                if file_id and api_session_id:
                                    download_url = f"/api/sessions/{api_session_id}/images/{file_id}"
                                    if encoded_session_name:
                                        download_url += f"?session_name={encoded_session_name}"
                                    img_copy.setdefault("download_uri", download_url)
                                    img_copy.setdefault("url", download_url)
                                enriched_images.append(img_copy)
                            chunk["images"] = enriched_images
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                # 仅在非 strict 模式下附加额外元数据
                if not strict_openai:
                    meta = fetch_latest_turn_meta()
                    if meta:
                        yield f"data: {json.dumps({'final_metadata': meta}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            except Exception as e:
                error_data = {"error": {"message": str(e)}}
                yield f"data: {json.dumps(error_data)}\n\n"

        stream_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        if canonical_session_id:
            stream_headers["X-Session-Id"] = canonical_session_id
        if session_data.get("session_name"):
            stream_headers["X-Session-Name"] = session_data["session_name"]

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers=stream_headers,
        )
    else:
        try:
            # 传递 file_ids 并在发送后清空
            file_ids_to_send = combined_file_ids.copy()
            session_data["pending_file_ids"] = []

            response = client.chat.completions.create(
                model=request.model,
                messages=messages,
                stream=False,
                include_image_data=request.include_image_data,
                include_thoughts=include_thoughts_flag,
                embed_images_in_content=request.embed_images_in_content,
                file_ids=file_ids_to_send,
            )

            # 调试日志：打印响应内容
            logger.debug(f"Chat response keys: {response.keys()}")
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            logger.debug(f"Content type: {type(content)}, empty: {not content}")
            if "images" in response:
                logger.debug(f"Images count: {len(response.get('images', []))}")
            if "thoughts" in response:
                logger.debug(f"Thoughts count: {len(response.get('thoughts', []))}")

            if strict_openai:
                # 返回标准字段，避免混入自定义 keys
                clean_resp = {
                    "id": response.get("id"),
                    "object": response.get("object"),
                    "created": response.get("created"),
                    "model": response.get("model"),
                    "choices": response.get("choices", []),
                    "usage": response.get("usage"),
                }
                if response.get("system_fingerprint"):
                    clean_resp["system_fingerprint"] = response.get("system_fingerprint")
                return clean_resp

            # 返回 canonical session_id 和 session_name，供前端更新状态
            response["session_id"] = canonical_session_id
            response["session_name"] = session_data.get("session_name")
            meta = fetch_latest_turn_meta()
            if meta:
                response.update(meta)
            return response
        except Exception as e:
            import traceback
            logger.error(f"Chat completion failed: {e}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))


def verify_request_auth(
    authorization: Optional[str],
    config: dict,
    raise_on_failure: bool = True
) -> tuple[bool, Optional[str]]:
    """智能验证请求身份 - 支持 Cookie 和 API Key 双模式
    
    验证优先级：
    1. Cookie 验证：检查配置中是否已有有效的 Gemini Cookie (secure_c_ses, csesidx, group_id)
       - 如果有效，说明是从已登录的前端网页访问，直接放行
    2. API Key 验证：如果启用了 require_api_key 且没有有效 Cookie
       - 检查 Authorization header，验证 API Key
    
    Args:
        authorization: Authorization header 的值
        config: 当前配置字典
        raise_on_failure: 验证失败时是否抛出异常（默认 True）
    
    Returns:
        (验证是否通过, 错误消息)
        
    Raises:
        HTTPException: 如果 raise_on_failure=True 且验证失败
    """
    security = config.get("security", {})
    
    # 第一优先级：检查是否有有效的 Cookie（说明是从前端网页访问）
    # 如果 config 中有这些关键字段，说明用户已经通过网页登录
    has_valid_cookie = all([
        config.get("secure_c_ses"),
        config.get("csesidx"),
        config.get("group_id")
    ])
    
    if has_valid_cookie:
        # Cookie 验证通过，无需检查 API Key
        logger.debug("请求通过 Cookie 验证（前端网页访问）")
        return True, None
    
    # 第二优先级：如果没有 Cookie 且启用了 API Key 验证
    if security.get("require_api_key", False):
        # 没有 Authorization header
        if not authorization:
            error_msg = "缺少 Authorization header。前端用户请先登录，第三方客户端请提供 API Key"
            if raise_on_failure:
                raise HTTPException(status_code=401, detail=error_msg)
            return False, error_msg
        
        # 解析 Bearer token
        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            error_msg = "Authorization 格式错误，应为 Bearer sk-xxx"
            if raise_on_failure:
                raise HTTPException(status_code=401, detail=error_msg)
            return False, error_msg
        
        api_key = parts[1]
        
        # 验证 API Key
        if not validate_api_key(api_key):
            error_msg = "无效的 API Key"
            if raise_on_failure:
                raise HTTPException(status_code=401, detail=error_msg)
            return False, error_msg
        
        logger.debug("请求通过 API Key 验证（第三方客户端访问）")
        return True, None
    
    # 如果没有启用 API Key 验证且没有 Cookie，拒绝访问
    error_msg = "未登录，请先登录或配置 API Key"
    if raise_on_failure:
        raise HTTPException(status_code=401, detail=error_msg)
    return False, error_msg


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    request: ChatRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    conversation_id: Optional[str] = Header(None, alias="Conversation-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    response: Response = None,
) -> Union[dict, StreamingResponse]:
    """OpenAI 兼容的聊天接口（支持 Cookie 和 API Key 双模式验证）。
    
    验证模式：
    1. 前端网页访问：通过 Cookie 验证（已登录用户），无需 API Key
    2. 第三方客户端：通过 API Key 验证（Authorization: Bearer sk-xxx）
    """
    # 智能双模式验证
    config = load_config()
    verify_request_auth(authorization, config, raise_on_failure=True)
    
    return await _chat_completions_handler(
        request=request,
        x_session_id=x_session_id,
        conversation_id=conversation_id,
        response_obj=response,
        strict_openai=False,
    )


@app.post("/v1/openai/chat/completions", response_model=None)
async def chat_completions_strict(
    request: ChatRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    conversation_id: Optional[str] = Header(None, alias="Conversation-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    response: Response = None,
) -> Union[dict, StreamingResponse]:
    """严格遵循 OpenAI Chat Completions 协议的接口（支持 Cookie 和 API Key 双模式验证）。

    - 不返回自定义字段（images/thoughts/final_metadata/session_id）
    - 会话信息通过响应头 X-Session-Id / X-Session-Name 传递
    - 适配 ChatWebUI / Lobe Chat 等严格校验第三方前端
    
    验证模式：
    1. 前端网页访问：通过 Cookie 验证（已登录用户），无需 API Key
    2. 第三方客户端：通过 API Key 验证（Authorization: Bearer sk-xxx）
    """
    # 智能双模式验证
    config = load_config()
    verify_request_auth(authorization, config, raise_on_failure=True)
    
    return await _chat_completions_handler(
        request=request,
        x_session_id=x_session_id,
        conversation_id=conversation_id,
        response_obj=response,
        strict_openai=True,
    )


@app.get("/v1/models")
async def list_models() -> dict:
    """列出可用模型（OpenAI 兼容格式）"""
    # 使用固定时间戳以保持一致性
    created_ts = 1700000000
    return {
        "object": "list",
        "data": [
            {"id": "auto", "object": "model", "created": created_ts, "owned_by": "google", "name": "自动", "description": "Gemini Enterprise 会选择最合适的选项"},
            {"id": "gemini-2.5-flash", "object": "model", "created": created_ts, "owned_by": "google","name": "Gemini 2.5 Flash", "description": "适用于执行日常任务"},
            {"id": "gemini-2.5-pro", "object": "model", "created": created_ts, "owned_by": "google",  "name": "Gemini 2.5 Pro", "description": "最适用于执行复杂任务"},
            {"id": "gemini-3-pro-preview", "object": "model", "created": created_ts, "owned_by": "google", "name": "Gemini 3 Pro Preview", "description": "先进的推理模型"},
            {"id": "business-gemini", "object": "model", "created": created_ts, "owned_by": "google", "name": "Business Gemini", "description": "默认自动选择"},
        ]
    }


# ==================== Anthropic Messages API ====================

@app.post("/v1/messages", response_model=None)
async def anthropic_messages(
    request: AnthropicRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
) -> Union[dict, StreamingResponse]:
    """Anthropic Messages API 兼容接口

    支持 Claude Code (Claude CLI) 等使用 Anthropic API 格式的工具。
    请求和响应格式遵循 Anthropic Messages API 规范。

    会话管理：
    - 通过 X-Session-Id header 传递会话 ID
    - 或通过 metadata.session_id 传递会话 ID
    - 如果都没有，使用默认会话（保持上下文连续）
    """
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail=f"配置缺失: {', '.join(missing)}，请先登录")

        # 检查保活服务的 session 状态
        keep_alive = get_keep_alive_service()
        keep_alive_status = keep_alive.get_status()
        if keep_alive_status.get("last_check") and not keep_alive_status.get("session_valid", True):
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    # 确定会话 ID（优先使用 header，其次使用 metadata，最后使用默认）
    session_id = x_session_id
    if not session_id and request.metadata:
        session_id = request.metadata.get("session_id")
    if not session_id:
        session_id = "default"

    # 使用会话管理获取或创建客户端
    client, session_data = get_or_create_anthropic_client(session_id)

    # 转换消息格式
    messages = []
    for msg in request.messages:
        if isinstance(msg.content, str):
            messages.append({"role": msg.role, "content": msg.content})
        else:
            # content blocks 格式
            content_blocks = []
            for block in msg.content:
                block_dict = {"type": block.type}
                if block.text is not None:
                    block_dict["text"] = block.text
                if block.source is not None:
                    block_dict["source"] = block.source
                content_blocks.append(block_dict)
            messages.append({"role": msg.role, "content": content_blocks})

    if request.stream:
        async def generate():
            try:
                response_gen = client.messages.create(
                    model=request.model,
                    max_tokens=request.max_tokens,
                    messages=messages,
                    system=request.system,
                    stream=True,
                    temperature=request.temperature,
                    top_p=request.top_p,
                    top_k=request.top_k,
                    stop_sequences=request.stop_sequences,
                )
                for event in response_gen:
                    event_type = event.get("type", "unknown")
                    yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                error_event = {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": str(e)
                    }
                }
                yield f"event: error\ndata: {json.dumps(error_event)}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    else:
        try:
            response = client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                messages=messages,
                system=request.system,
                stream=False,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k,
                stop_sequences=request.stop_sequences,
            )
            return response
        except Exception as e:
            import traceback
            logger.error(f"Anthropic messages failed: {e}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    session_name: Optional[str] = Form(None),
) -> dict:
    """上传文件到当前会话

    Args:
        file: 上传的文件
        session_id: 可选的会话 ID，如果不提供则使用默认会话
    """
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail="未登录")

        # 读取文件内容
        file_content = await file.read()

        logger.debug(f"文件上传请求: filename={file.filename}, size={len(file_content)}, session_id={session_id}")

        # 获取客户端
        if session_id:
            _, session_data, canonical_session_id = get_or_create_client(session_id, session_name)
            biz_client = session_data["biz_client"]
            session_name = session_data["session_name"]
            logger.debug(f"使用 OpenAI 会话: session_id={session_id}, canonical_session_id={canonical_session_id}, session_name={session_name}")
        else:
            # 使用 Anthropic 默认会话
            _, session_data = get_or_create_anthropic_client("default")
            biz_client = session_data["biz_client"]
            session_name = session_data["session_name"]
            logger.debug(f"使用 Anthropic 默认会话: session_name={session_name}")

        # 确定 MIME 类型
        mime_type = file.content_type or "application/octet-stream"

        # 上传文件
        logger.debug(f"正在上传文件到 session: {session_name}")
        result = biz_client.add_context_file(
            file_name=file.filename,
            file_content=file_content,
            mime_type=mime_type,
            session_name=session_name,
        )
        logger.debug(f"文件上传结果: {result}")

        # 保存文件 ID 到会话数据，以便在发送消息时使用
        file_id = result.get("file_id")
        if file_id:
            if "pending_file_ids" not in session_data:
                session_data["pending_file_ids"] = []
            session_data["pending_file_ids"].append(file_id)
            logger.debug(f"已添加 file_id 到待发送列表: {file_id}, 当前列表: {session_data['pending_file_ids']}")

        return {
            "success": True,
            "filename": file.filename,
            "content_type": mime_type,
            "file_id": result.get("file_id"),
            "token_count": result.get("token_count"),
            "session_name": session_name,
            "message": "文件上传成功",
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"File upload failed: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Anthropic 会话管理 ====================

@app.post("/v1/messages/sessions")
async def create_anthropic_session() -> dict:
    """创建新的 Anthropic 会话

    返回新会话的 ID，用于后续请求的 X-Session-Id header
    """
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail="未登录")

        # 生成新的会话 ID
        new_session_id = str(uuid.uuid4())

        # 创建客户端（这会触发 Gemini 会话创建）
        client, session_data = get_or_create_anthropic_client(new_session_id)

        return {
            "session_id": new_session_id,
            "gemini_session": session_data["session_name"],
            "created_at": session_data["created_at"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/v1/messages/sessions/{session_id}")
async def delete_anthropic_session(session_id: str) -> dict:
    """删除 Anthropic 会话"""
    if session_id in anthropic_sessions:
        del anthropic_sessions[session_id]
        return {"success": True, "message": f"会话 {session_id} 已删除"}
    return {"success": False, "message": "会话不存在"}


@app.post("/v1/messages/sessions/reset")
async def reset_default_anthropic_session() -> dict:
    """重置默认 Anthropic 会话

    这会删除默认会话并在下次请求时创建新会话，
    用于开始全新的对话上下文。
    """
    if "default" in anthropic_sessions:
        del anthropic_sessions["default"]
    return {"success": True, "message": "默认会话已重置"}


@app.get("/v1/messages/sessions")
async def list_anthropic_sessions() -> list:
    """列出所有 Anthropic 会话"""
    result = []
    for sid, data in anthropic_sessions.items():
        result.append({
            "session_id": sid,
            "gemini_session": data.get("session_name"),
            "created_at": data.get("created_at"),
            "message_count": len(data.get("messages", [])),
        })
    return result


@app.get("/api/sessions/{session_id}/files")
async def list_session_files(session_id: str) -> dict:
    """列出会话中的所有文件（用于调试）

    Args:
        session_id: 会话 ID
    """
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail="未登录")

        # 查找会话
        session_data = None
        session_name = None

        # 先在 OpenAI sessions 中查找
        if session_id in sessions:
            session_data = sessions[session_id]
            session_name = session_data.get("session_name")
        # 再在 Anthropic sessions 中查找
        elif session_id in anthropic_sessions:
            session_data = anthropic_sessions[session_id]
            session_name = session_data.get("session_name")
        else:
            # 尝试直接作为 session_name 使用
            session_name = session_id

        if not session_name:
            raise HTTPException(status_code=404, detail="会话不存在")

        # 获取文件列表
        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)
        files = biz_client.list_session_files(session_name)

        return {
            "session_id": session_id,
            "session_name": session_name,
            "files": files,
            "count": len(files),
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/images/{filename}")
async def get_image(filename: str) -> FileResponse:
    """提供本地生成图片的访问"""
    # 安全检查：防止路径遍历
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(IMAGES_DIR, safe_filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="图片不存在")

    # 根据文件扩展名确定 MIME 类型
    ext = os.path.splitext(safe_filename)[1].lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(file_path, media_type=media_type)


@app.get("/api/sessions/{session_id}/images/{file_id}")
async def download_session_image(session_id: str, file_id: str, session_name: Optional[str] = None) -> FileResponse:
    """通过 session 和 fileId 下载图片（用于历史对话中的图片）

    优先返回本地缓存，如果没有则从 Google 下载并缓存
    """
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail="未登录")

        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)

        # 确定 session_name（用于查询文件元数据）
        # 首先尝试使用传入的 session_name
        if session_name:
            # 如果 session_name 已经包含 projects/ 前缀，直接使用
            if session_name.startswith("projects/"):
                query_session_name = session_name
            else:
                # 尝试添加 project_id 前缀
                project_id = config.get("project_id")
                if project_id:
                    # 确保 session_name 以 collections/ 开头
                    if session_name.startswith("collections/"):
                        query_session_name = f"projects/{project_id}/locations/global/{session_name}"
                    else:
                        query_session_name = session_name
                else:
                    query_session_name = session_name
        else:
            # 没有传入 session_name，使用 session_id 构造
            project_id = config.get("project_id")
            base_session = f"collections/default_collection/engines/agentspace-engine/sessions/{session_id}"
            if project_id:
                query_session_name = f"projects/{project_id}/locations/global/{base_session}"
            else:
                query_session_name = base_session

        logger.debug(f"下载图片请求: session_id={session_id}, file_id={file_id}, query_session_name={query_session_name}")

        # 先检查本地缓存（按 file_id 匹配文件名）
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
            # 检查 gemini_{file_id} 格式
            alt_path = os.path.join(IMAGES_DIR, f"gemini_{file_id}{ext}")
            if os.path.exists(alt_path):
                logger.debug(f"找到本地缓存: {alt_path}")
                alt_mime = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }.get(ext, "image/png")
                return FileResponse(alt_path, media_type=alt_mime)

        # 获取文件元数据 - 使用 AI_GENERATED filter 获取生成的图片
        file_metadata = biz_client._get_session_file_metadata(query_session_name, filter_str="file_origin_type = AI_GENERATED")
        logger.debug(f"获取到的文件元数据 keys: {list(file_metadata.keys())}")

        meta = file_metadata.get(file_id, {})
        logger.debug(f"file_id={file_id} 的元数据: {meta}")

        if not meta:
            # 如果没有找到，尝试不带 filter
            file_metadata_all = biz_client._get_session_file_metadata(query_session_name, filter_str="")
            logger.debug(f"所有文件元数据 keys: {list(file_metadata_all.keys())}")
            meta = file_metadata_all.get(file_id, {})

        file_name = meta.get("name", f"gemini_{file_id}.png")
        mime_type = meta.get("mimeType", "image/png")
        # 使用元数据中的完整 session 路径（包含 project_id）
        full_session = meta.get("session") or query_session_name

        logger.debug(f"使用完整 session 路径: {full_session}")

        # 检查本地是否已有缓存（使用实际文件名）
        local_path = os.path.join(IMAGES_DIR, file_name)
        if os.path.exists(local_path):
            logger.debug(f"找到本地缓存（通过文件名）: {local_path}")
            return FileResponse(local_path, media_type=mime_type)

        # 本地没有缓存，从 Google 下载
        logger.debug(f"本地没有缓存，尝试从 Google 下载: file_id={file_id}, session={full_session}")
        image_data = biz_client._download_file_with_jwt(
            download_uri="",
            session_name=full_session,
            file_id=file_id
        )

        # 保存到本地缓存
        os.makedirs(IMAGES_DIR, exist_ok=True)
        local_path = os.path.join(IMAGES_DIR, file_name)
        with open(local_path, "wb") as f:
            f.write(image_data)

        return FileResponse(local_path, media_type=mime_type)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 远程浏览器登录 ====================

@app.websocket("/ws/browser")
async def browser_websocket(websocket: WebSocket) -> None:
    """远程浏览器 WebSocket 端点"""
    await websocket.accept()

    browser_service = get_browser_service()
    # 解析可选参数
    start_url = websocket.query_params.get("start_url")
    use_profile = websocket.query_params.get("use_profile", "0").lower() in ("1", "true", "yes", "on")

    session = await browser_service.create_session(start_url=start_url, use_profile_dir=use_profile)

    # 消息发送回调
    async def send_message(message: dict) -> None:
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.warning(f"WebSocket 发送消息失败: {e}")

    session.subscribe(send_message)

    try:
        # 只有当会话是 IDLE 状态时才启动浏览器
        # 如果已经在运行，只需订阅消息即可
        from biz_gemini.remote_browser import BrowserSessionStatus
        if session.status == BrowserSessionStatus.IDLE:
            success = await session.start()
            if not success:
                await websocket.close()
                return
        elif session.status == BrowserSessionStatus.RUNNING:
            # 已经在运行，发送当前状态
            await send_message({
                "type": "status",
                "status": session.status.value,
                "message": session.message,
            })
        elif session.status == BrowserSessionStatus.LOGIN_SUCCESS:
            # 已经登录成功，发送状态和配置
            await send_message({
                "type": "status",
                "status": session.status.value,
                "message": session.message,
            })
            config = session.get_login_config()
            if config:
                await send_message({
                    "type": "login_success",
                    "config": config,
                })

        # 处理客户端消息
        while True:
            try:
                data = await websocket.receive_json()
                action = data.get("action")

                if action == "click":
                    x = data.get("x", 0)
                    y = data.get("y", 0)
                    await session.click(x, y)

                elif action == "type":
                    text = data.get("text", "")
                    await session.type_text(text)

                elif action == "key":
                    key = data.get("key", "")
                    await session.press_key(key)

                elif action == "scroll":
                    delta_x = data.get("deltaX", 0)
                    delta_y = data.get("deltaY", 0)
                    await session.scroll(delta_x, delta_y)

                elif action == "navigate":
                    url = data.get("url", "")
                    await session.navigate(url)

                elif action == "stop":
                    break

                elif action == "save_config":
                    # 保存登录配置
                    config = session.get_login_config()
                    if config:
                        save_config(config)
                        # 清除 JWT 缓存和过期标记，使新 Cookie 生效
                        on_cookie_refreshed()
                        logger.info("登录配置已保存，JWT 缓存和过期标记已清除")
                        await send_message({
                            "type": "config_saved",
                            "success": True,
                            "message": "配置已保存，正在关闭浏览器..."
                        })
                        # 保存成功后自动停止浏览器
                        await session.stop()
                        break
                    else:
                        await send_message({
                            "type": "config_saved",
                            "success": False,
                            "message": "没有可保存的配置"
                        })

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.warning(f"处理 WebSocket 消息错误: {e}")

    finally:
        session.unsubscribe(send_message)

        async def _delayed_stop():
            # 给前端留出短时间重连，避免一直占用 Chrome 进程
            await asyncio.sleep(30)
            if session.has_subscribers():
                return
            try:
                await session.stop()
                logger.info("WebSocket 已断开，自动停止远程浏览器以释放资源")
            except Exception as e:
                logger.warning(f"自动停止远程浏览器失败: {e}")

        asyncio.create_task(_delayed_stop())


@app.post("/api/browser/start")
async def start_browser(use_profile: bool = False, start_url: Optional[str] = None) -> dict:
    """启动远程浏览器（REST API 方式）"""
    browser_service = get_browser_service()
    session = await browser_service.get_active_session()

    if session and session.status in (BrowserSessionStatus.STARTING, BrowserSessionStatus.RUNNING):
        return {
            "success": True,
            "session_id": session.session_id,
            "status": session.status.value,
            "message": "浏览器已在运行"
        }

    session = await browser_service.create_session(start_url=start_url, use_profile_dir=use_profile)
    return {
        "success": True,
        "session_id": session.session_id,
        "message": "请通过 WebSocket 连接 /ws/browser 进行操作"
    }


@app.post("/api/browser/stop")
async def stop_browser() -> dict:
    """停止远程浏览器"""
    browser_service = get_browser_service()
    session = await browser_service.get_active_session()

    if session:
        await session.stop()
        return {"success": True, "message": "浏览器已停止"}

    return {"success": False, "message": "没有运行中的浏览器"}


@app.get("/api/browser/status")
async def browser_status() -> dict:
    """获取远程浏览器状态"""
    browser_service = get_browser_service()
    session = await browser_service.get_active_session()

    if session:
        return {
            "active": True,
            "session_id": session.session_id,
            "status": session.status.value,
            "message": session.message
        }

    return {
        "active": False,
        "message": "没有活跃的浏览器会话"
    }


@app.post("/api/session/update")
async def update_session_config(config: dict) -> dict:
    """手动更新登录配置（用于手动粘贴 Cookie）

    只需要提供 secure_c_ses 和 group_id，csesidx 会自动获取
    """
    import httpx

    try:
        # 只需要 secure_c_ses 和 group_id
        required_fields = ["secure_c_ses", "group_id"]
        missing = [f for f in required_fields if not config.get(f)]

        if missing:
            return {
                "success": False,
                "error": f"缺少必要字段: {', '.join(missing)}"
            }

        secure_c_ses = config.get("secure_c_ses")
        host_c_oses = config.get("host_c_oses", "")
        nid = config.get("nid", "")

        # 如果没有提供 csesidx，自动从 list-sessions 获取
        csesidx = config.get("csesidx", "")
        if not csesidx:
            # 构造 cookie 字符串
            cookie_str = f"__Secure-C_SES={secure_c_ses}"
            if host_c_oses:
                cookie_str += f"; __Host-C_OSES={host_c_oses}"
            if nid:
                cookie_str += f"; NID={nid}"

            # 调用 list-sessions API 获取 csesidx
            proxy = get_proxy(load_config())
            client_kwargs = {"verify": False, "timeout": 30.0}
            if proxy:
                client_kwargs["proxy"] = proxy

            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    # 先尝试不带 csesidx 参数
                    resp = await client.get(
                        "https://auth.business.gemini.google/list-sessions?rt=json",
                        headers={
                            "accept": "*/*",
                            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            "origin": "https://business.gemini.google",
                            "referer": "https://business.gemini.google/",
                            "cookie": cookie_str,
                        },
                    )

                    if resp.status_code == 200:
                        text = resp.text
                        if text.startswith(")]}'"):
                            text = text[4:].strip()

                        data = json.loads(text)
                        sessions_list = data.get("sessions", [])

                        if sessions_list:
                            # 使用第一个（通常是当前活跃的）session 的 csesidx
                            csesidx = str(sessions_list[0].get("csesidx", ""))
                            # 同时尝试获取 NID（如果响应头中有 Set-Cookie）
                            # 注意：NID 通常不在 list-sessions 响应中

                        if not csesidx:
                            return {
                                "success": False,
                                "error": "无法自动获取 csesidx，请手动输入"
                            }
                    else:
                        return {
                            "success": False,
                            "error": f"验证凭证失败: HTTP {resp.status_code}，请检查 Cookie 是否正确"
                        }

            except Exception as e:
                return {
                    "success": False,
                    "error": f"自动获取 csesidx 失败: {str(e)}，请手动输入"
                }

        # 添加保存时间
        from datetime import datetime
        final_config = {
            "secure_c_ses": secure_c_ses,
            "host_c_oses": host_c_oses,
            "nid": nid,
            "csesidx": csesidx,
            "group_id": config.get("group_id"),
            "cookies_saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        save_config(final_config)

        # 清除现有的会话缓存，强制使用新配置
        sessions.clear()

        return {
            "success": True,
            "message": "配置已更新",
            "csesidx": csesidx  # 返回获取到的 csesidx
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ==================== Session 保活服务 ====================

@app.get("/api/keep-alive/status")
async def keep_alive_status() -> dict:
    """获取保活服务状态"""
    service = get_keep_alive_service()
    return service.get_status()


@app.post("/api/keep-alive/refresh")
async def keep_alive_refresh() -> dict:
    """手动触发一次 Session 刷新"""
    service = get_keep_alive_service()
    result = await service.refresh_now()
    return result


@app.post("/api/keep-alive/start")
async def keep_alive_start() -> dict:
    """启动保活服务"""
    service = get_keep_alive_service()
    if service._running:
        return {"success": True, "message": "服务已在运行"}
    await service.start()
    return {"success": True, "message": "服务已启动"}


@app.post("/api/keep-alive/stop")
async def keep_alive_stop() -> dict:
    """停止保活服务"""
    service = get_keep_alive_service()
    if not service._running:
        return {"success": True, "message": "服务未运行"}
    await service.stop()
    return {"success": True, "message": "服务已停止"}


# ==================== 浏览器保活服务 ====================

@app.get("/api/browser-keep-alive/status")
async def browser_keep_alive_status() -> dict:
    """获取浏览器保活服务状态"""
    config = load_config()
    browser_config = config.get("browser_keep_alive", {})

    if not browser_config.get("enabled", False):
        return {
            "enabled": False,
            "message": "浏览器保活服务未启用，可在 config.json 中设置 browser_keep_alive.enabled = true",
        }

    service = get_browser_keep_alive_service()
    status = service.get_status()
    status["enabled"] = True
    return status


@app.post("/api/browser-keep-alive/start")
async def browser_keep_alive_start() -> dict:
    """启动浏览器保活服务"""
    config = load_config()
    browser_config = config.get("browser_keep_alive", {})

    service = get_browser_keep_alive_service(
        interval_minutes=browser_config.get("interval_minutes", 60),
        headless=browser_config.get("headless", True),
    )

    if service._running:
        return {"success": True, "message": "服务已在运行"}

    await service.start()
    return {"success": True, "message": "浏览器保活服务已启动"}


@app.post("/api/browser-keep-alive/stop")
async def browser_keep_alive_stop() -> dict:
    """停止浏览器保活服务"""
    service = get_browser_keep_alive_service()
    if not service._running:
        return {"success": True, "message": "服务未运行"}
    await service.stop()
    return {"success": True, "message": "浏览器保活服务已停止"}


@app.post("/api/browser-keep-alive/refresh")
async def browser_keep_alive_refresh_now() -> dict:
    """立即执行一次浏览器保活刷新"""
    service = get_browser_keep_alive_service()
    result = await service.refresh_now()
    return result


# ==================== Cookie 刷新 ====================

@app.post("/api/cookie/refresh")
async def refresh_cookie(headless: bool = True) -> dict:
    """手动刷新 Cookie（通过浏览器）

    Args:
        headless: 是否无头模式，默认 True

    Returns:
        {
            "success": bool,
            "message": str,
            "needs_manual_login": bool,  # 是否需要手动登录
        }
    """
    import asyncio
    
    try:
        # 添加 5 分钟超时保护，防止服务器挂起
        result = await asyncio.wait_for(
            try_refresh_cookie_via_browser(headless=headless),
            timeout=300  # 5 分钟
        )
        return result
    except asyncio.TimeoutError:
        logger.error("刷新 Cookie 超时（5分钟）")
        return {
            "success": False,
            "message": "刷新 Cookie 超时（5分钟），请检查网络或手动登录",
            "needs_manual_login": True,
        }
    except Exception as e:
        import traceback
        logger.error(f"刷新 Cookie 失败: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "message": str(e),
            "needs_manual_login": False,
        }


@app.get("/api/cookie/status")
async def cookie_status() -> dict:
    """获取 Cookie 状态"""
    from biz_gemini.config import get_account_state, is_in_cooldown

    config = load_config()
    account_state = get_account_state()
    in_cooldown, cooldown_remaining = is_in_cooldown()

    # 获取 Cookie 保存时间
    cookies_saved_at = config.get("cookies_saved_at") or config.get("session", {}).get("cookies_saved_at")
    age_seconds = cookies_age_seconds(config)

    return {
        "has_credentials": bool(config.get("secure_c_ses") and config.get("csesidx")),
        "cookie_expired": is_cookie_expired(),
        "cookies_saved_at": cookies_saved_at,
        "age_seconds": age_seconds,
        "age_hours": round(age_seconds / 3600, 2) if age_seconds else None,
        "available": account_state.get("available", True),
        "in_cooldown": in_cooldown,
        "cooldown_remaining_seconds": round(cooldown_remaining, 0) if in_cooldown else 0,
        "cooldown_reason": account_state.get("cooldown_reason", ""),
        "jwt_cached": bool(account_state.get("jwt")),
        "jwt_expires_at": account_state.get("jwt_expires_at", 0),
    }


@app.post("/api/cookie/mark-valid")
async def mark_cookie_valid_endpoint() -> dict:
    """手动标记 Cookie 为有效（用于调试）"""
    from biz_gemini.config import mark_cookie_valid as _mark_valid

    _mark_valid()
    return {"success": True, "message": "Cookie 已标记为有效"}


@app.get("/api/debug/getoxsrf")
async def debug_getoxsrf() -> dict:
    """调试端点：测试 getoxsrf 接口"""
    config = load_config()
    csesidx = config.get("csesidx")

    if not csesidx:
        return {"error": "缺少 csesidx"}

    url = f"{GETOXSRF_URL}?csesidx={csesidx}"

    try:
        resp, debug_info = request_getoxsrf(config, allow_minimal_retry=True)

        return {
            "url": url,
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "response_preview": resp.text[:500] if resp.text else "",
            "cookie_debug": debug_info,
            "proxy_used": debug_info.get("proxy_used"),
        }
    except Exception as e:
        from biz_gemini.auth import _build_cookie_header

        cookie_str, cookie_debug = _build_cookie_header(config)
        return {
            "error": str(e),
            "cookie_debug": cookie_debug,
        }


# ==================== 版本管理 ====================

@app.get("/api/version")
async def get_version_endpoint() -> dict:
    """获取当前版本信息"""
    return get_version_info()


@app.get("/api/version/check")
async def check_version_update() -> dict:
    """检查是否有新版本

    从 GitHub API 获取最新 release 信息并与当前版本对比。
    """
    import httpx

    if not GITHUB_REPO:
        return {
            "current_version": VERSION,
            "check_enabled": False,
            "message": "版本检测未配置 (GITHUB_REPO 为空)",
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"},
            )

            if resp.status_code == 404:
                return {
                    "current_version": VERSION,
                    "has_update": False,
                    "message": "暂无 Release 发布",
                }

            if resp.status_code != 200:
                return {
                    "current_version": VERSION,
                    "error": f"GitHub API 错误: {resp.status_code}",
                }

            data = resp.json()
            latest_version = data.get("tag_name", "").lstrip("v")
            release_url = data.get("html_url", "")
            release_notes = data.get("body", "")
            published_at = data.get("published_at", "")

            # 简单版本比较
            has_update = _compare_versions(VERSION, latest_version)

            return {
                "current_version": VERSION,
                "latest_version": latest_version,
                "has_update": has_update,
                "release_url": release_url,
                "release_notes": release_notes[:500] if release_notes else "",
                "published_at": published_at,
            }

    except Exception as e:
        return {
            "current_version": VERSION,
            "error": str(e),
        }


def _compare_versions(current: str, latest: str) -> bool:
    """比较版本号，返回 True 表示有更新可用"""
    try:
        def parse_version(v: str) -> tuple:
            parts = v.split(".")
            return tuple(int(p) for p in parts[:3])

        current_parts = parse_version(current)
        latest_parts = parse_version(latest)
        return latest_parts > current_parts
    except (ValueError, IndexError):
        return False


# 挂载静态文件
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    logger.info("=" * 50)
    logger.info("Gemini Chat 服务器启动")
    logger.info("访问地址: http://localhost:8000")
    logger.info("API 端点:")
    logger.info("  - POST /v1/chat/completions (OpenAI 兼容)")
    logger.info("  - POST /v1/messages (Anthropic 兼容, Claude Code)")
    logger.info("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)

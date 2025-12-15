"""认证模块，提供 JWT 生成、Cookie 管理和登录功能。

该模块提供：
- JWT 创建和验证（HMAC-SHA256 签名）
- Cookie 管理（Google 会话 Cookie 的提取、保存、过期检测）
- XSRF 令牌处理
- 浏览器自动登录
"""
import base64
import hashlib
import hmac
import json
import time
from http.cookies import SimpleCookie
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Any

import httpx

from .config import (
    TIME_FMT,
    clear_conversation_sessions,
    clear_jwt_cache,
    clear_redis_session_cache,
    cookies_expired,
    get_cached_jwt,
    get_proxy,
    is_cookie_expired,
    load_config,
    mark_cookie_expired,
    mark_cookie_valid,
    sanitize_group_id,
    save_config,
    set_cached_jwt,
)
from .logger import get_logger

# 模块级 logger
logger = get_logger("auth")

GETOXSRF_URL = "https://business.gemini.google/auth/getoxsrf"


def url_safe_b64encode(data: bytes) -> str:
    """将字节数据编码为 URL 安全的 Base64 字符串（无 padding）。

    Args:
        data: 要编码的字节数据。

    Returns:
        URL 安全的 Base64 编码字符串，不包含 padding 字符（=）。

    Example:
        >>> url_safe_b64encode(b"hello")
        'aGVsbG8'
    """
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def kq_encode(s: str) -> str:
    """将字符串编码为 Gemini API 所需的特殊 Base64 格式。

    模拟原 JavaScript 的 kQ 函数，处理双字节字符。
    该函数将字符串转换为字节数组，其中：
    - 单字节字符（ASCII）直接转换
    - 双字节字符拆分为低字节和高字节

    Args:
        s: 要编码的字符串。

    Returns:
        经过特殊处理后的 URL 安全 Base64 字符串。
    """
    byte_arr = bytearray()
    for ch in s:
        val = ord(ch)
        if val > 255:
            byte_arr.append(val & 255)
            byte_arr.append(val >> 8)
        else:
            byte_arr.append(val)
    return url_safe_b64encode(bytes(byte_arr))


def decode_xsrf_token(xsrf_token: str) -> bytes:
    """将 XSRF 令牌解码为字节数组，用作 HMAC 签名密钥。

    Args:
        xsrf_token: Base64 编码的 XSRF 令牌字符串。

    Returns:
        解码后的字节数组，可用于 JWT 签名。

    Note:
        会自动补齐 Base64 padding。
    """
    padding = 4 - len(xsrf_token) % 4
    if padding != 4:
        xsrf_token += "=" * padding
    return base64.urlsafe_b64decode(xsrf_token)


def create_jwt(
    key_bytes: bytes,
    key_id: str,
    csesidx: str,
    lifetime: int = 300,
) -> tuple[str, float]:
    """创建用于 Gemini API 认证的 JWT 令牌。

    使用 HMAC-SHA256 算法签名，生成符合 Gemini Business API 要求的 JWT。

    Args:
        key_bytes: HMAC 签名密钥（从 XSRF token 解码获得）。
        key_id: 密钥 ID，包含在 JWT header 的 kid 字段中。
        csesidx: 会话索引，用于构造 JWT 的 subject 声明。
        lifetime: JWT 有效期（秒），默认 300 秒（5 分钟）。

    Returns:
        元组 (token, expires_at)，其中：
        - token: 完整的 JWT 字符串
        - expires_at: 过期时间的 Unix 时间戳

    Example:
        >>> key = decode_xsrf_token(xsrf_token)
        >>> jwt, exp = create_jwt(key, "key123", "session456")
    """
    now = int(time.time())
    header = {
        "alg": "HS256",
        "typ": "JWT",
        "kid": key_id,
    }
    payload = {
        "iss": "https://business.gemini.google",
        "aud": "https://biz-discoveryengine.googleapis.com",
        "sub": f"csesidx/{csesidx}",
        "iat": now,
        "exp": now + lifetime,
        "nbf": now,
    }

    header_b64 = kq_encode(json.dumps(header, separators=(",", ":")))
    payload_b64 = kq_encode(json.dumps(payload, separators=(",", ":")))
    message = f"{header_b64}.{payload_b64}"

    signature = hmac.new(key_bytes, message.encode("utf-8"), hashlib.sha256).digest()
    signature_b64 = url_safe_b64encode(signature)
    token = f"{message}.{signature_b64}"
    return token, float(now + lifetime)


def _build_cookie_header(config: dict) -> tuple[str, dict]:
    """构造 HTTP Cookie 请求头字符串。

    优先使用完整的 cookie_raw，否则从拆分字段拼接。

    Args:
        config: 包含 Cookie 相关配置的字典，支持以下字段：
            - cookie_raw: 完整的原始 Cookie 字符串（优先使用）
            - secure_c_ses: __Secure-C_SES Cookie 值
            - host_c_oses: __Host-C_OSES Cookie 值
            - nid: NID Cookie 值

    Returns:
        元组 (cookie_str, debug_info)，其中：
        - cookie_str: 构造的 Cookie 字符串
        - debug_info: 调试信息字典，包含 cookie_source、cookie_header_length、
          cookie_header_preview 等
    """
    cookie_raw = config.get("cookie_raw")

    if cookie_raw:
        # 优先使用 cookie_raw（完整的 raw cookie header）
        cookie_str = cookie_raw
        debug_info = {
            "cookie_source": "cookie_raw",
            "cookie_header_length": len(cookie_str),
            "cookie_header_preview": cookie_str[:100] + "..." if len(cookie_str) > 100 else cookie_str,
        }
    else:
        # 回退：使用拆分字段拼接
        secure_c_ses = config.get("secure_c_ses")
        host_c_oses = config.get("host_c_oses")
        nid = config.get("nid")

        cookie_str = f"__Secure-C_SES={secure_c_ses}"
        if host_c_oses:
            cookie_str += f"; __Host-C_OSES={host_c_oses}"
        if nid:
            cookie_str += f"; NID={nid}"

        debug_info = {
            "cookie_source": "fields",
            "cookie_header_length": len(cookie_str),
            "cookie_header_preview": cookie_str[:100] + "..." if len(cookie_str) > 100 else cookie_str,
        }

    return cookie_str, debug_info


def _parse_cookie_str(cookie_str: str) -> Dict[str, str]:
    """将原始 Cookie 字符串解析为键值对字典。

    Args:
        cookie_str: Cookie 字符串，格式如 "name1=value1; name2=value2"。

    Returns:
        Cookie 名称到值的映射字典。
    """
    jar = SimpleCookie()
    jar.load(cookie_str)
    return {k: morsel.value for k, morsel in jar.items()}


def check_session_status(config: Optional[dict] = None) -> dict:
    """通过 getoxsrf 接口检查 session 是否有效。

    返回:
        {
            "valid": bool,          # session 是否有效
            "expired": bool,        # session 是否已过期
            "warning": bool,        # 是否有警告
            "username": str,        # 用户名/邮箱（getoxsrf 不返回此信息，始终为 None）
            "error": str | None,    # 错误信息
            "raw_response": dict,   # 原始响应（用于调试）
            "cookie_debug": dict,   # cookie 调试信息
        }
    """
    if config is None:
        config = load_config()

    secure_c_ses = config.get("secure_c_ses")
    csesidx = config.get("csesidx")

    if not secure_c_ses or not csesidx:
        return {
            "valid": False,
            "expired": True,
            "warning": False,
            "username": None,
            "error": "缺少凭证信息",
            "raw_response": None,
            "cookie_debug": None,
        }

    # 构造 Cookie 字符串，优先使用 cookie_raw
    cookie_str, cookie_debug = _build_cookie_header(config)

    try:
        # 使用 getoxsrf 验证 session 是否有效
        result = _get_jwt_via_api(config)
        # 如果成功获取 JWT，说明 session 有效
        return {
            "valid": True,
            "expired": False,
            "warning": False,
            "username": None,
            "error": None,
            "raw_response": {"keyId": result.get("key_id", "")[:20] + "..."},
            "cookie_debug": cookie_debug,
        }
    except Exception as e:
        error_msg = str(e)
        # 检查是否是 302 重定向到 refreshcookies（可能需要刷新 Cookie）
        if "302" in error_msg or "refreshcookies" in error_msg.lower():
            return {
                "valid": False,
                "expired": False,
                "warning": True,
                "username": None,
                "error": f"需要刷新 Cookie: {error_msg}",
                "raw_response": None,
                "cookie_debug": cookie_debug,
            }
        # 其他错误视为 session 过期
        return {
            "valid": False,
            "expired": True,
            "warning": False,
            "username": None,
            "error": error_msg,
            "raw_response": None,
            "cookie_debug": cookie_debug,
        }


def _get_jwt_via_api(config: Optional[dict] = None) -> dict:
    """通过 getoxsrf 接口生成一次 JWT。"""
    if config is None:
        config = load_config()

    secure_c_ses = config.get("secure_c_ses")
    csesidx = config.get("csesidx")
    if not secure_c_ses or not csesidx:
        raise ValueError("缺少 secure_c_ses / csesidx，请先运行 `python app.py login`")

    proxy = get_proxy(config)

    # 调用 getoxsrf（带 refreshcookies 跟随与精简 cookie 回退）
    resp, _debug = request_getoxsrf(config, allow_minimal_retry=True)

    # 检查 HTTP 状态码
    if resp.status_code != 200:
        location = resp.headers.get("location", "")
        raise ValueError(f"getoxsrf 请求失败: HTTP {resp.status_code}, location: {location}")

    text = resp.text
    if text.startswith(")]}'"):
        text = text[4:].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"getoxsrf 返回非 JSON 数据: {text[:500]}") from e

    if "keyId" not in data or "xsrfToken" not in data:
        raise ValueError(f"getoxsrf 返回数据缺少必要字段，可能是 Cookie 已过期，请重新登录。返回内容: {data}")

    key_id = data["keyId"]
    xsrf_token = data["xsrfToken"]
    key_bytes = decode_xsrf_token(xsrf_token)

    jwt, exp_ts = create_jwt(key_bytes, key_id, csesidx)

    return {
        "jwt": jwt,
        "key_id": key_id,
        "expires_at_ts": exp_ts,
    }


def request_getoxsrf(config: Optional[dict] = None, allow_minimal_retry: bool = True) -> tuple[httpx.Response, dict]:
    """执行 getoxsrf 请求，内建 refreshcookies 跟随与精简 cookie 回退。"""
    if config is None:
        config = load_config()

    secure_c_ses = config.get("secure_c_ses")
    csesidx = config.get("csesidx")
    if not secure_c_ses or not csesidx:
        raise ValueError("缺少 secure_c_ses / csesidx，请先运行 `python app.py login`")

    proxy = get_proxy(config)

    # 使用 _build_cookie_header 构造 Cookie（优先使用 cookie_raw）
    cookie_str, cookie_debug = _build_cookie_header(config)

    # 精简版 cookie，只使用核心认证 Cookie
    minimal_cookie_str = None
    if secure_c_ses:
        minimal_cookie_str = f"__Secure-C_SES={secure_c_ses}"
        if config.get("host_c_oses"):
            minimal_cookie_str += f"; __Host-C_OSES={config['host_c_oses']}"
        # 注意：不包含 NID，因为它可能触发 refreshcookies

    url = f"{GETOXSRF_URL}?csesidx={csesidx}"

    client_kwargs = {
        "verify": False,
        "follow_redirects": False,
        "timeout": 30.0,
    }
    if proxy:
        client_kwargs["proxy"] = proxy

    headers_base = {
        "accept": "*/*",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "origin": "https://business.gemini.google",
        "referer": "https://business.gemini.google/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }

    def _send_with_refresh(client: httpx.Client, cookie_header: str) -> httpx.Response:
        headers = {**headers_base, "cookie": cookie_header}
        resp = client.get(url, headers=headers)
        if resp.status_code == 302:
            location = resp.headers.get("location", "")
            if "refreshcookies" in location.lower():
                logger.info("检测到 refreshcookies 重定向，尝试跟随")
                # 重要：refreshcookies 请求也需要携带 Cookie
                resp_refresh = client.get(location, headers=headers, follow_redirects=True)
                if resp_refresh.status_code in (200, 204, 302, 303):
                    # 关键：从 refreshcookies 响应中提取新的 Cookie，合并到原有 Cookie 中
                    new_cookies = {}
                    for cookie_item in resp_refresh.headers.get_list("set-cookie"):
                        # 解析 Set-Cookie 头，提取 name=value
                        if "=" in cookie_item:
                            cookie_part = cookie_item.split(";")[0].strip()
                            if "=" in cookie_part:
                                name, value = cookie_part.split("=", 1)
                                new_cookies[name.strip()] = value.strip()

                    # 如果有新 Cookie，合并到请求头中
                    if new_cookies:
                        logger.info(f"refreshcookies 返回了新 Cookie: {list(new_cookies.keys())}")
                        # 解析原有 Cookie
                        existing_cookies = {}
                        for part in cookie_header.split(";"):
                            part = part.strip()
                            if "=" in part:
                                name, value = part.split("=", 1)
                                existing_cookies[name.strip()] = value.strip()
                        # 合并新 Cookie（新的覆盖旧的）
                        existing_cookies.update(new_cookies)
                        # 重新构造 Cookie 字符串
                        updated_cookie_header = "; ".join(f"{k}={v}" for k, v in existing_cookies.items())
                        headers = {**headers_base, "cookie": updated_cookie_header}
                        logger.info(f"使用更新后的 Cookie 重试 getoxsrf")

                    resp = client.get(url, headers=headers)
                else:
                    logger.warning(f"refreshcookies 请求失败: HTTP {resp_refresh.status_code}")
        return resp

    used_cookie_header = minimal_cookie_str if minimal_cookie_str else cookie_str
    used_variant = "minimal" if minimal_cookie_str else cookie_debug.get("cookie_source", "cookie_raw")

    with httpx.Client(**client_kwargs) as client:
        # 优先使用精简 Cookie
        resp = _send_with_refresh(client, used_cookie_header)

        # 若精简 Cookie 仍是 302，尝试完整 cookie_raw（作为备用）
        if (
            allow_minimal_retry
            and resp.status_code == 302
            and cookie_str
            and cookie_str != used_cookie_header
        ):
            logger.info("getoxsrf 精简 cookie 返回 302，改用完整 cookie_raw 再试")
            alt_resp = _send_with_refresh(client, cookie_str)
            if alt_resp.status_code != 302:
                resp = alt_resp
                used_cookie_header = cookie_str
                used_variant = cookie_debug.get("cookie_source", "cookie_raw")

    debug_info = {
        "cookie_source": cookie_debug.get("cookie_source"),
        "cookie_header_length": len(used_cookie_header),
        "cookie_header_preview": used_cookie_header[:100] + "..." if len(used_cookie_header) > 100 else used_cookie_header,
        "used_cookie_variant": used_variant,
        "status_code": resp.status_code,
        "location": resp.headers.get("location"),
        "proxy_used": proxy,
    }

    return resp, debug_info


# JWT 有效期阈值（秒），超过此时间需要刷新
JWT_REFRESH_THRESHOLD = 240  # 4 分钟


@dataclass
class JWTManager:
    """管理 JWT，自动在过期前刷新。

    支持Redis共享缓存（多worker模式）和全局内存缓存（单worker模式）。
    当 Cookie 刷新后，需要调用 invalidate() 清除缓存。
    """

    config: dict
    _jwt: Optional[str] = None
    _expires_at_ts: float = 0.0
    use_global_cache: bool = True  # 是否使用全局缓存
    _redis_manager: Optional[Any] = None  # Redis管理器实例

    def __post_init__(self):
        """初始化时创建 Redis 管理器。"""
        try:
            from .redis_manager import get_redis_manager
            self._redis_manager = get_redis_manager(self.config)
            if self._redis_manager.is_redis_enabled():
                logger.info("JWTManager: 使用 Redis 存储 JWT")
            else:
                logger.debug("JWTManager: Redis 未启用，使用内存存储 JWT")
        except Exception as e:
            logger.warning(f"Redis 初始化失败，降级到内存存储: {e}")
            self._redis_manager = None

    def _get_cached_jwt_from_redis(self) -> tuple[Optional[str], float]:
        """从 Redis 获取缓存的 JWT。"""
        if not self._redis_manager or not self._redis_manager.is_redis_enabled():
            return None, 0.0

        try:
            jwt_data = self._redis_manager.get_json("jwt_token")
            if jwt_data and isinstance(jwt_data, dict):
                return jwt_data.get("token"), jwt_data.get("expires_at", 0.0)
        except Exception as e:
            logger.debug(f"从 Redis 读取 JWT 失败: {e}")
        return None, 0.0

    def _set_cached_jwt_to_redis(self, jwt: str, expires_at: float) -> None:
        """将 JWT 保存到 Redis。"""
        if not self._redis_manager or not self._redis_manager.is_redis_enabled():
            return

        try:
            ttl = int(expires_at - time.time())
            if ttl > 0:
                self._redis_manager.set_json(
                    "jwt_token",
                    {"token": jwt, "expires_at": expires_at},
                    ex=ttl + 60  # 额外 60 秒容错
                )
        except Exception as e:
            logger.debug(f"保存 JWT 到 Redis 失败: {e}")

    def _clear_jwt_from_redis(self) -> None:
        """从 Redis 清除 JWT。"""
        if not self._redis_manager or not self._redis_manager.is_redis_enabled():
            return

        try:
            self._redis_manager.delete("jwt_token")
        except Exception as e:
            logger.debug(f"从 Redis 删除 JWT 失败: {e}")

    def get_jwt(self) -> str:
        """获取有效的 JWT，必要时自动刷新。"""
        now = time.time()

        # 优先从Redis获取（如果启用）
        if self._redis_manager and self._redis_manager.is_redis_enabled():
            cached_jwt, cached_expires = self._get_cached_jwt_from_redis()
            if cached_jwt and cached_expires > now + 60:
                self._jwt = cached_jwt
                self._expires_at_ts = cached_expires
                return cached_jwt

        # 回退到全局内存缓存
        if self.use_global_cache:
            cached_jwt, cached_expires = get_cached_jwt()
            if cached_jwt and cached_expires > now + 60:
                self._jwt = cached_jwt
                self._expires_at_ts = cached_expires
                # 同步到Redis
                if self._redis_manager and self._redis_manager.is_redis_enabled():
                    self._set_cached_jwt_to_redis(cached_jwt, cached_expires)
                return cached_jwt

        # 检查实例缓存，提前 60s 刷新
        if not self._jwt or now > self._expires_at_ts - 60:
            self.refresh()
        return self._jwt  # type: ignore[return-value]

    def refresh(self) -> None:
        """刷新 JWT。"""
        result = _get_jwt_via_api(self.config)
        self._jwt = result["jwt"]
        self._expires_at_ts = result["expires_at_ts"]

        # 更新 Redis 缓存
        if self._redis_manager and self._redis_manager.is_redis_enabled():
            self._set_cached_jwt_to_redis(self._jwt, self._expires_at_ts)

        # 更新全局内存缓存
        if self.use_global_cache:
            set_cached_jwt(self._jwt, self._expires_at_ts)

        logger.debug(f"JWT 已刷新，过期时间: {datetime.fromtimestamp(self._expires_at_ts).strftime('%H:%M:%S')}")

    def invalidate(self) -> None:
        """使 JWT 缓存失效（Cookie 刷新后调用）。"""
        self._jwt = None
        self._expires_at_ts = 0.0

        # 清除 Redis 缓存
        if self._redis_manager and self._redis_manager.is_redis_enabled():
            self._clear_jwt_from_redis()

        # 清除全局内存缓存
        if self.use_global_cache:
            clear_jwt_cache()

        logger.debug("JWT 缓存已清除")


def ensure_jwt_valid(config: Optional[dict] = None, threshold_seconds: int = JWT_REFRESH_THRESHOLD) -> dict:
    """确保 JWT 有效，必要时刷新。

    这是所有对外请求入口应该调用的函数。

    Args:
        config: 配置字典，如果为 None 则自动加载
        threshold_seconds: JWT 刷新阈值（秒），默认 240s

    Returns:
        包含 jwt 和状态信息的字典:
        {
            "valid": bool,
            "jwt": str | None,
            "refreshed": bool,  # 是否进行了刷新
            "error": str | None,
        }
    """
    if config is None:
        config = load_config()

    # 检查 Cookie 是否已标记为过期
    if is_cookie_expired():
        return {
            "valid": False,
            "jwt": None,
            "refreshed": False,
            "error": "Cookie 已过期，需要重新登录或刷新",
        }

    # 检查必要的凭证
    if not config.get("secure_c_ses") or not config.get("csesidx"):
        return {
            "valid": False,
            "jwt": None,
            "refreshed": False,
            "error": "缺少凭证信息",
        }

    now = time.time()

    # 检查全局缓存
    cached_jwt, cached_expires = get_cached_jwt()
    if cached_jwt and cached_expires > now + threshold_seconds:
        return {
            "valid": True,
            "jwt": cached_jwt,
            "refreshed": False,
            "error": None,
        }

    # 需要刷新 JWT
    try:
        result = _get_jwt_via_api(config)
        jwt = result["jwt"]
        expires_at = result["expires_at_ts"]

        # 更新全局缓存
        set_cached_jwt(jwt, expires_at)

        # 标记 Cookie 有效
        mark_cookie_valid()

        logger.info(f"JWT 已刷新，过期时间: {datetime.fromtimestamp(expires_at).strftime('%H:%M:%S')}")

        return {
            "valid": True,
            "jwt": jwt,
            "refreshed": True,
            "error": None,
        }
    except Exception as e:
        error_msg = str(e)

        # 检查是否是认证错误
        if "401" in error_msg or "expired" in error_msg.lower() or "过期" in error_msg:
            mark_cookie_expired(error_msg)

        logger.warning(f"JWT 刷新失败: {error_msg}")

        return {
            "valid": False,
            "jwt": None,
            "refreshed": False,
            "error": error_msg,
        }


def on_cookie_refreshed() -> None:
    """Cookie 刷新后的回调，清理 JWT 和 session 缓存"""
    clear_jwt_cache()
    clear_conversation_sessions()
    clear_redis_session_cache()  # 清除 Redis 中的旧 session 缓存，避免 403 错误
    mark_cookie_valid()
    logger.info("Cookie 已刷新，JWT 和 session 缓存已清除")


def _parse_csesidx_from_url(url: str) -> Optional[str]:
    if "csesidx=" in url:
        return url.split("csesidx=", 1)[1].split("&", 1)[0]
    return None


def _parse_group_id_from_url(url: str) -> Optional[str]:
    """
    从类似：
    https://business.gemini.google/home/cid/51518926-c5e4-4372-b9c1-b4e6f2afa7ed/r/research/...
    中提取 GROUP_ID（原 CONFIG_ID）。
    """
    marker = "/cid/"
    if marker not in url:
        return None
    after = url.split(marker, 1)[1]
    if not after:
        return None
    for sep in ("/", "?", "#"):
        after = after.split(sep, 1)[0]
    return sanitize_group_id(after)


async def login_via_browser() -> dict:
    """打开浏览器登录 Business Gemini，自动保存 cookie + GROUP_ID。"""
    from playwright.async_api import async_playwright

    config = load_config()
    proxy = get_proxy(config)

    # Playwright 不支持 socks5h://，转换为 socks5://
    playwright_proxy = proxy
    if proxy and proxy.startswith("socks5h://"):
        playwright_proxy = proxy.replace("socks5h://", "socks5://", 1)

    async with async_playwright() as p:
        logger.info(f"启动浏览器 (代理: {playwright_proxy})...")
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",  # 用本机 Chrome
        )

        context_kwargs = {}
        if playwright_proxy:
            context_kwargs["proxy"] = {"server": playwright_proxy}
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        logger.info("访问 Business Gemini...")
        try:
            await page.goto("https://business.gemini.google/", timeout=60000)
            await page.wait_for_load_state("networkidle")
        except Exception as e:  # noqa: BLE001
            logger.error(f"访问失败: {e}")
            logger.info("请检查代理是否正常运行")
            await browser.close()
            raise

        current_url = page.url

        login_hosts = [
            "auth.business.gemini.google",
            "accounts.google.com",
            "accountverification.business.gemini.google",
        ]
        intermediate_paths = ["/admin/create", "/admin/setup"]

        def is_main_page(url: str) -> bool:
            if "business.gemini.google" not in url:
                return False
            for host in login_hosts:
                if host in url:
                    return False
            for pth in intermediate_paths:
                if pth in url:
                    return False
            return "/home/" in url

        if not is_main_page(current_url):
            logger.info("请在浏览器中完成登录，等待进入 /home/ 页面...")
            try:
                await page.wait_for_url(is_main_page, timeout=300000)
                logger.info("登录成功！")
                await page.wait_for_load_state("networkidle")
                current_url = page.url
            except Exception as e:  # noqa: BLE001
                logger.warning(f"等待主页面超时: {e}")
                current_url = page.url

        csesidx = _parse_csesidx_from_url(current_url)
        group_id = _parse_group_id_from_url(current_url)

        cookies = await context.cookies()
        secure_c_ses = None
        host_c_oses = None
        nid = None
        for cookie in cookies:
            if cookie["name"] == "__Secure-C_SES":
                secure_c_ses = cookie["value"]
            elif cookie["name"] == "__Host-C_OSES":
                host_c_oses = cookie["value"]
            elif cookie["name"] == "NID":
                nid = cookie["value"]

        logger.info(f"csesidx: {csesidx}")
        logger.info(f"GROUP_ID: {group_id}")
        logger.info(f"NID: {'已获取' if nid else '未获取'}")
        logger.info("Cookies 已获取")

        now_str = datetime.now().strftime(TIME_FMT)
        new_cfg = {
            "secure_c_ses": secure_c_ses,
            "host_c_oses": host_c_oses,
            "nid": nid,
            "csesidx": csesidx,
            "group_id": group_id,
            "proxy": proxy,
            "cookies_saved_at": now_str,
        }
        merged = save_config(new_cfg)

        logger.info("配置已写入 config.json")
        logger.info("关闭浏览器...")
        await browser.close()

        return merged


def ensure_biz_config(max_cookie_age_hours: int = 24) -> dict:
    """CLI 辅助函数：确保 cookie + GROUP_ID 可用。

    此函数主要用于 CLI 工具启动时的前置检查，在 Web 服务中不推荐使用。
    Web 服务应使用 check_session_status() 或 ensure_jwt_valid() 进行实时验证。
    """
    cfg = load_config()
    missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            f"配置缺失字段: {', '.join(missing)}，请先运行 `python app.py login` 登录一次。"
        )

    if cookies_expired(cfg, max_cookie_age_hours):
        raise RuntimeError(
            "Business Gemini 登录信息疑似超过 24 小时，请重新运行 `python app.py login`。"
        )

    return cfg

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

import httpx

from .config import (
    TIME_FMT,
    cookies_expired,
    get_proxy,
    load_config,
    sanitize_group_id,
    save_config,
)
from .logger import get_logger

# 模块级 logger
logger = get_logger("auth")

GETOXSRF_URL = "https://business.gemini.google/auth/getoxsrf"
LIST_SESSIONS_URL = "https://auth.business.gemini.google/list-sessions"


def url_safe_b64encode(data: bytes) -> str:
    """URL 安全的 Base64（无 padding）。"""
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def kq_encode(s: str) -> str:
    """模拟原 JS 的 kQ 函数。"""
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
    """将 xsrfToken 解码为字节数组（HMAC key）。"""
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
    """创建 JWT，并返回 (token, 过期时间戳)。"""
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


def check_session_status(config: Optional[dict] = None) -> dict:
    """通过 list-sessions 接口检查 session 是否过期。

    返回:
        {
            "valid": bool,          # session 是否有效
            "expired": bool,        # session 是否已过期
            "username": str,        # 用户名/邮箱
            "error": str | None,    # 错误信息
        }
    """
    if config is None:
        config = load_config()

    secure_c_ses = config.get("secure_c_ses")
    csesidx = config.get("csesidx")
    nid = config.get("nid")

    if not secure_c_ses or not csesidx:
        return {
            "valid": False,
            "expired": True,
            "username": None,
            "error": "缺少凭证信息",
        }

    proxy = get_proxy(config)

    # 使用 __Secure-C_SES 和 NID
    cookie_str = f"__Secure-C_SES={secure_c_ses}"
    if nid:
        cookie_str += f"; NID={nid}"

    url = f"{LIST_SESSIONS_URL}?csesidx={csesidx}&rt=json"

    client_kwargs = {
        "verify": False,
        "follow_redirects": False,
        "timeout": 30.0,
    }
    if proxy:
        client_kwargs["proxy"] = proxy

    try:
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

        # 如果 list-sessions 返回 401，尝试用 getoxsrf 验证
        if resp.status_code == 401:
            # 尝试通过 getoxsrf 验证 session 是否有效
            try:
                _get_jwt_via_api(config)
                # 如果 getoxsrf 成功，说明 session 有效
                return {
                    "valid": True,
                    "expired": False,
                    "username": None,
                    "error": None,
                }
            except Exception:
                return {
                    "valid": False,
                    "expired": True,
                    "username": None,
                    "error": "HTTP 401",
                }

        if resp.status_code != 200:
            return {
                "valid": False,
                "expired": True,
                "username": None,
                "error": f"HTTP {resp.status_code}",
            }

        text = resp.text
        # 处理可能的前缀
        if text.startswith(")]}'"):
            text = text[4:].strip()

        data = json.loads(text)
        sessions = data.get("sessions", [])

        # 查找当前 session（注意：csesidx 可能是字符串或数字，统一转为字符串比较）
        current_session = None
        csesidx_str = str(csesidx)
        for sess in sessions:
            if str(sess.get("csesidx", "")) == csesidx_str:
                current_session = sess
                break

        if not current_session and sessions:
            # 如果没找到匹配的，使用第一个（通常只有一个登录 session）
            current_session = sessions[0]

        if current_session:
            is_expired = current_session.get("expired", False)
            return {
                "valid": not is_expired,
                "expired": is_expired,
                "username": current_session.get("subject") or current_session.get("displayName"),
                "error": None,
            }

        return {
            "valid": False,
            "expired": True,
            "username": None,
            "error": "未找到 session 信息",
        }

    except json.JSONDecodeError as e:
        return {
            "valid": False,
            "expired": True,
            "username": None,
            "error": f"JSON 解析失败: {e}",
        }
    except Exception as e:
        return {
            "valid": False,
            "expired": True,
            "username": None,
            "error": str(e),
        }


def _get_jwt_via_api(config: Optional[dict] = None) -> dict:
    """通过 getoxsrf 接口生成一次 JWT。"""
    if config is None:
        config = load_config()

    secure_c_ses = config.get("secure_c_ses")
    host_c_oses = config.get("host_c_oses")
    nid = config.get("nid")
    csesidx = config.get("csesidx")
    if not secure_c_ses or not csesidx:
        raise ValueError("缺少 secure_c_ses / csesidx，请先运行 `python app.py login`")

    proxy = get_proxy(config)
    cookie_str = f"__Secure-C_SES={secure_c_ses}"
    if host_c_oses:
        cookie_str += f"; __Host-C_OSES={host_c_oses}"
    if nid:
        cookie_str += f"; NID={nid}"

    url = f"{GETOXSRF_URL}?csesidx={csesidx}"

    # 构建 httpx 客户端参数，proxy=None 时不传该参数
    client_kwargs = {
        "verify": False,
        "follow_redirects": False,
        "timeout": 30.0,
    }
    if proxy:
        client_kwargs["proxy"] = proxy

    with httpx.Client(**client_kwargs) as client:
        resp = client.get(
            url,
            headers={
                "accept": "*/*",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "cookie": cookie_str,
            },
        )

    # 检查 HTTP 状态码
    if resp.status_code != 200:
        raise ValueError(f"getoxsrf 请求失败: HTTP {resp.status_code}, 响应: {resp.text[:500]}")

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


@dataclass
class JWTManager:
    """管理 JWT，自动在过期前刷新。"""

    config: dict
    _jwt: Optional[str] = None
    _expires_at_ts: float = 0.0

    def get_jwt(self) -> str:
        now = time.time()
        # 提前 60s 刷新
        if not self._jwt or now > self._expires_at_ts - 60:
            self.refresh()
        return self._jwt  # type: ignore[return-value]

    def refresh(self) -> None:
        result = _get_jwt_via_api(self.config)
        self._jwt = result["jwt"]
        self._expires_at_ts = result["expires_at_ts"]


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
    """用于 CLI / 业务代码启动时，确保 cookie + GROUP_ID 可用。"""
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

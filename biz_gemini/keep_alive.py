"""Session 保活服务 - 定期检查 session 状态并刷新 JWT

增强功能：
- Cookie 过期检测
- 自动触发浏览器刷新（如果启用）
- 401/403 错误处理
"""
import asyncio
from datetime import datetime
from typing import Callable, List, Optional

from .auth import JWTManager, _get_jwt_via_api, check_session_status, on_cookie_refreshed
from .config import (
    is_cookie_expired,
    load_config,
    mark_cookie_expired,
    mark_cookie_valid,
    save_config,
    set_cooldown,
)
from .logger import get_logger

# 模块级 logger
logger = get_logger("keep_alive")


class KeepAliveService:
    """Session 保活服务

    增强功能：
    - 检测 Cookie 过期并触发刷新事件
    - 支持与浏览器保活服务联动
    - 401/403 错误自动标记过期
    """

    def __init__(self, interval_minutes: int = 10, auto_browser_refresh: bool = False):
        """
        初始化保活服务

        Args:
            interval_minutes: 刷新间隔（分钟），默认 10 分钟
            auto_browser_refresh: 是否在检测到过期时自动触发浏览器刷新
        """
        self.interval_minutes = interval_minutes
        self.auto_browser_refresh = auto_browser_refresh
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_refresh: Optional[datetime] = None
        self._last_check: Optional[datetime] = None
        self._refresh_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._session_valid: bool = True
        self._session_username: Optional[str] = None
        self._callbacks: List[Callable] = []
        self._cookie_expired: bool = False
        self._pending_refresh: bool = False  # 是否有待处理的刷新请求

    def add_callback(self, callback: Callable) -> None:
        """添加状态变更回调"""
        self._callbacks.append(callback)

    def _notify(self, event: str, data: Optional[dict] = None) -> None:
        """通知所有回调"""
        for cb in self._callbacks:
            try:
                cb(event, data or {})
            except Exception as e:
                logger.warning(f"回调执行失败: {e}")

    async def start(self) -> None:
        """启动保活服务"""
        if self._running:
            logger.info("服务已在运行")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"服务已启动，刷新间隔: {self.interval_minutes} 分钟")
        self._notify("started", {"interval_minutes": self.interval_minutes})

    async def stop(self) -> None:
        """停止保活服务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("服务已停止")
        self._notify("stopped")

    async def _run_loop(self) -> None:
        """保活循环"""
        # 启动后立即执行一次刷新
        await self._do_refresh()

        while self._running:
            try:
                # 等待指定间隔
                await asyncio.sleep(self.interval_minutes * 60)

                if not self._running:
                    break

                await self._do_refresh()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"循环异常: {e}")
                self._error_count += 1
                self._last_error = str(e)
                # 出错后等待 1 分钟再重试
                await asyncio.sleep(60)

    async def _do_refresh(self) -> None:
        """执行一次刷新检查"""
        try:
            config = load_config()

            # 检查是否有必要的凭证
            if not config.get("secure_c_ses") or not config.get("csesidx"):
                logger.debug("未登录，跳过刷新")
                self._last_check = datetime.now()
                self._session_valid = False
                self._cookie_expired = True
                self._last_error = "缺少登录凭证（secure_c_ses/csesidx）"
                return

            # 检查 Cookie 是否已标记为过期
            if is_cookie_expired():
                logger.warning("Cookie 已标记为过期，跳过 JWT 刷新")
                self._cookie_expired = True
                self._session_valid = False
                self._last_check = datetime.now()
                self._last_error = "Cookie 已标记为过期"

                # 如果启用了自动浏览器刷新，尝试刷新
                if self.auto_browser_refresh and self._pending_refresh:
                    await self._try_browser_refresh()
                    self._pending_refresh = False
                return

            logger.info(f"正在检查 Session 状态... ({datetime.now().strftime('%H:%M:%S')})")

            # 1. 首先检查 session 是否有效（通过 list-sessions 接口）
            session_status = check_session_status(config)
            self._last_check = datetime.now()
            self._session_valid = session_status.get("valid", False)
            self._session_username = session_status.get("username")

            if session_status.get("expired", False):
                logger.warning("Session 已过期")
                self._last_error = "Session 已过期"
                self._cookie_expired = True
                mark_cookie_expired("Session 已过期")
                self._notify("expired", {"error": "Session 已过期"})

                # 触发浏览器刷新
                if self.auto_browser_refresh:
                    await self._try_browser_refresh()
                return

            if session_status.get("error"):
                error_msg = session_status["error"]
                logger.warning(f"检查 Session 状态失败: {error_msg}")
                self._error_count += 1
                self._last_error = error_msg

                # 检查是否是认证错误
                if "401" in error_msg or "403" in error_msg:
                    self._cookie_expired = True
                    mark_cookie_expired(error_msg)
                    self._notify("expired", {"error": error_msg})

                    if self.auto_browser_refresh:
                        await self._try_browser_refresh()
                return

            # 2. Session 有效，刷新 JWT
            result = _get_jwt_via_api(config)

            self._last_refresh = datetime.now()
            self._refresh_count += 1
            self._last_error = None
            self._cookie_expired = False
            mark_cookie_valid()

            logger.info(f"刷新成功 (第 {self._refresh_count} 次) - 用户: {self._session_username}")
            self._notify("refreshed", {
                "count": self._refresh_count,
                "time": self._last_refresh.isoformat(),
                "username": self._session_username,
            })

        except Exception as e:
            self._error_count += 1
            self._last_error = str(e)
            error_msg = str(e)

            # 检查是否是 session 过期或需要刷新 cookie
            if (
                "expired" in error_msg.lower()
                or "401" in error_msg
                or "403" in error_msg
                or "302" in error_msg
                or "refreshcookies" in error_msg.lower()
            ):
                logger.warning("Session 已过期，需要重新登录")
                self._session_valid = False
                self._cookie_expired = True
                mark_cookie_expired(error_msg)
                self._notify("expired", {"error": error_msg})

                if self.auto_browser_refresh:
                    await self._try_browser_refresh()
            else:
                logger.warning(f"刷新失败: {error_msg}")
                self._notify("error", {"error": error_msg})

    async def _try_browser_refresh(self) -> bool:
        """尝试通过浏览器刷新 Cookie"""
        try:
            from .browser_keep_alive import try_refresh_cookie_via_browser

            logger.info("尝试通过浏览器刷新 Cookie...")
            result = await try_refresh_cookie_via_browser(headless=True)

            if result.get("success"):
                logger.info("浏览器刷新 Cookie 成功")
                self._cookie_expired = False
                self._session_valid = True
                on_cookie_refreshed()
                self._notify("cookie_refreshed", {"method": "browser"})
                return True
            elif result.get("needs_manual_login"):
                logger.warning("需要手动登录")
                self._notify("needs_manual_login", {"message": result.get("message")})
                return False
            else:
                logger.warning(f"浏览器刷新失败: {result.get('message')}")
                return False

        except ImportError:
            logger.warning("浏览器保活模块不可用")
            return False
        except Exception as e:
            logger.error(f"浏览器刷新异常: {e}")
            return False

    def trigger_refresh(self) -> None:
        """触发一次刷新（用于外部调用，如检测到 401/403 时）"""
        self._pending_refresh = True
        logger.info("已触发刷新请求")

    def on_auth_error(self, status_code: int, error_msg: str = "") -> None:
        """处理认证错误（401/403）

        Args:
            status_code: HTTP 状态码
            error_msg: 错误信息
        """
        if status_code in (401, 403):
            logger.warning(f"检测到认证错误 {status_code}: {error_msg}")
            self._cookie_expired = True
            self._session_valid = False
            mark_cookie_expired(f"HTTP {status_code}: {error_msg}")
            self._pending_refresh = True
            self._notify("auth_error", {"status_code": status_code, "error": error_msg})
        elif status_code == 429:
            # 速率限制，设置冷却
            logger.warning(f"检测到速率限制 429: {error_msg}")
            set_cooldown(300, "速率限制 429")  # 5 分钟冷却
            self._notify("rate_limited", {"error": error_msg})

    async def refresh_now(self) -> dict:
        """立即执行一次刷新"""
        try:
            await self._do_refresh()
            success = (
                self._session_valid
                and not self._cookie_expired
                and not self._last_error
            )
            return {
                "success": success,
                "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
                "last_check": self._last_check.isoformat() if self._last_check else None,
                "refresh_count": self._refresh_count,
                "session_valid": self._session_valid,
                "cookie_expired": self._cookie_expired,
                "error": self._last_error,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def get_status(self) -> dict:
        """获取保活服务状态"""
        return {
            "running": self._running,
            "interval_minutes": self.interval_minutes,
            "auto_browser_refresh": self.auto_browser_refresh,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "refresh_count": self._refresh_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "session_valid": self._session_valid,
            "session_username": self._session_username,
            "cookie_expired": self._cookie_expired,
            "pending_refresh": self._pending_refresh,
        }


# 全局服务实例
_keep_alive_service: Optional[KeepAliveService] = None


def get_keep_alive_service(
    interval_minutes: int = 10,
    auto_browser_refresh: bool = False,
) -> KeepAliveService:
    """获取全局保活服务实例

    Args:
        interval_minutes: 刷新间隔（分钟）
        auto_browser_refresh: 是否在检测到过期时自动触发浏览器刷新
    """
    global _keep_alive_service
    if _keep_alive_service is None:
        _keep_alive_service = KeepAliveService(
            interval_minutes=interval_minutes,
            auto_browser_refresh=auto_browser_refresh,
        )
    return _keep_alive_service


def notify_auth_error(status_code: int, error_msg: str = "") -> None:
    """通知保活服务发生认证错误（供外部调用）"""
    service = get_keep_alive_service()
    service.on_auth_error(status_code, error_msg)

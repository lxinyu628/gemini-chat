"""Session 保活服务 - 定期刷新 JWT 以保持 session 活跃"""
import asyncio
from datetime import datetime
from typing import Optional, Callable

from .auth import JWTManager, _get_jwt_via_api
from .config import load_config, save_config


class KeepAliveService:
    """Session 保活服务"""

    def __init__(self, interval_minutes: int = 20):
        """
        初始化保活服务

        Args:
            interval_minutes: 刷新间隔（分钟），默认 20 分钟
        """
        self.interval_minutes = interval_minutes
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_refresh: Optional[datetime] = None
        self._refresh_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._callbacks: list[Callable] = []

    def add_callback(self, callback: Callable):
        """添加状态变更回调"""
        self._callbacks.append(callback)

    def _notify(self, event: str, data: dict = None):
        """通知所有回调"""
        for cb in self._callbacks:
            try:
                cb(event, data or {})
            except Exception as e:
                print(f"[KeepAlive] 回调执行失败: {e}")

    async def start(self):
        """启动保活服务"""
        if self._running:
            print("[KeepAlive] 服务已在运行")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        print(f"[KeepAlive] 服务已启动，刷新间隔: {self.interval_minutes} 分钟")
        self._notify("started", {"interval_minutes": self.interval_minutes})

    async def stop(self):
        """停止保活服务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        print("[KeepAlive] 服务已停止")
        self._notify("stopped")

    async def _run_loop(self):
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
                print(f"[KeepAlive] 循环异常: {e}")
                self._error_count += 1
                self._last_error = str(e)
                # 出错后等待 1 分钟再重试
                await asyncio.sleep(60)

    async def _do_refresh(self):
        """执行一次刷新"""
        try:
            config = load_config()

            # 检查是否有必要的凭证
            if not config.get("secure_c_ses") or not config.get("csesidx"):
                print("[KeepAlive] 未登录，跳过刷新")
                return

            print(f"[KeepAlive] 正在刷新 Session... ({datetime.now().strftime('%H:%M:%S')})")

            # 调用 getoxsrf 接口刷新 JWT
            # 这个调用会验证并刷新 session
            result = _get_jwt_via_api(config)

            self._last_refresh = datetime.now()
            self._refresh_count += 1
            self._last_error = None

            print(f"[KeepAlive] 刷新成功 (第 {self._refresh_count} 次)")
            self._notify("refreshed", {
                "count": self._refresh_count,
                "time": self._last_refresh.isoformat()
            })

        except Exception as e:
            self._error_count += 1
            self._last_error = str(e)
            error_msg = str(e)

            # 检查是否是 session 过期
            if "expired" in error_msg.lower() or "401" in error_msg:
                print(f"[KeepAlive] Session 已过期，需要重新登录")
                self._notify("expired", {"error": error_msg})
            else:
                print(f"[KeepAlive] 刷新失败: {error_msg}")
                self._notify("error", {"error": error_msg})

    async def refresh_now(self) -> dict:
        """立即执行一次刷新"""
        try:
            await self._do_refresh()
            return {
                "success": True,
                "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
                "refresh_count": self._refresh_count
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
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "refresh_count": self._refresh_count,
            "error_count": self._error_count,
            "last_error": self._last_error
        }


# 全局服务实例
_keep_alive_service: Optional[KeepAliveService] = None


def get_keep_alive_service(interval_minutes: int = 20) -> KeepAliveService:
    """获取全局保活服务实例"""
    global _keep_alive_service
    if _keep_alive_service is None:
        _keep_alive_service = KeepAliveService(interval_minutes)
    return _keep_alive_service

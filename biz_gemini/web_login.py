"""Web 端登录服务模块，支持浏览器自动登录。

该模块提供：
- 异步登录任务管理
- 登录进度追踪
- 任务状态通知
"""
import asyncio
import uuid
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from biz_gemini.auth import login_via_browser
from biz_gemini.config import save_config


class LoginStatus(str, Enum):
    """登录状态枚举。"""

    IDLE = "idle"
    STARTING = "starting"
    WAITING = "waiting"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LoginTask:
    """登录任务。"""

    def __init__(self, task_id: str, headless: bool = False):
        """初始化登录任务。

        Args:
            task_id: 任务唯一标识
            headless: 是否使用无头模式
        """
        self.task_id = task_id
        self.headless = headless
        self.status = LoginStatus.IDLE
        self.message = ""
        self.progress = 0  # 0-100
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.config: Optional[dict] = None
        self.error: Optional[str] = None
        self.task: Optional[asyncio.Task] = None

    def update(self, status: LoginStatus, message: str = "", progress: int = None) -> None:
        """更新任务状态。"""
        self.status = status
        self.message = message
        if progress is not None:
            self.progress = progress
        self.updated_at = datetime.now()

    def to_dict(self) -> dict:
        """转换为字典。"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "message": self.message,
            "progress": self.progress,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "error": self.error,
        }


class WebLoginService:
    """Web 端登录服务。"""

    def __init__(self):
        """初始化登录服务。"""
        self.tasks: Dict[str, LoginTask] = {}
        self._lock = asyncio.Lock()

    async def start_login(self, headless: bool = False) -> LoginTask:
        """启动登录流程。

        Args:
            headless: 是否使用无头模式（Linux 服务器环境）

        Returns:
            LoginTask 实例
        """
        async with self._lock:
            for task in self.tasks.values():
                if task.status in (LoginStatus.STARTING, LoginStatus.WAITING, LoginStatus.PROCESSING):
                    return task

            task_id = str(uuid.uuid4())
            task = LoginTask(task_id, headless)
            self.tasks[task_id] = task

            task.task = asyncio.create_task(self._run_login(task))

            return task

    async def _run_login(self, task: LoginTask) -> None:
        """执行登录流程（后台任务）。"""
        try:
            task.update(LoginStatus.STARTING, "正在启动浏览器...", 10)
            await asyncio.sleep(1)

            task.update(LoginStatus.WAITING, "等待用户完成登录...", 20)

            config = await login_via_browser()

            task.update(LoginStatus.PROCESSING, "处理登录信息...", 80)
            await asyncio.sleep(0.5)

            save_config(config)

            task.update(LoginStatus.SUCCESS, "登录成功！", 100)
            task.config = config

        except asyncio.CancelledError:
            task.update(LoginStatus.CANCELLED, "登录已取消", 0)
            raise
        except Exception as e:
            task.update(LoginStatus.FAILED, f"登录失败: {str(e)}", 0)
            task.error = str(e)

    async def get_task(self, task_id: str) -> Optional[LoginTask]:
        """获取任务状态。"""
        return self.tasks.get(task_id)

    async def get_latest_task(self) -> Optional[LoginTask]:
        """获取最新的任务。"""
        if not self.tasks:
            return None

        latest = max(self.tasks.values(), key=lambda t: t.created_at)
        return latest

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务。"""
        task = self.tasks.get(task_id)
        if not task:
            return False

        if task.task and not task.task.done():
            task.task.cancel()
            try:
                await task.task
            except asyncio.CancelledError:
                pass
            return True

        return False

    def cleanup_old_tasks(self, max_age_seconds: int = 3600) -> None:
        """清理旧任务（1小时以上）。"""
        now = datetime.now()
        to_remove = []

        for task_id, task in self.tasks.items():
            age = (now - task.updated_at).total_seconds()
            if age > max_age_seconds and task.status in (
                LoginStatus.SUCCESS,
                LoginStatus.FAILED,
                LoginStatus.CANCELLED,
            ):
                to_remove.append(task_id)

        for task_id in to_remove:
            del self.tasks[task_id]


# 全局服务实例
_login_service: Optional[WebLoginService] = None


def get_login_service() -> WebLoginService:
    """获取全局登录服务实例。"""
    global _login_service
    if _login_service is None:
        _login_service = WebLoginService()
    return _login_service

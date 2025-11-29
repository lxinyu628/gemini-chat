"""远程浏览器服务 - 通过 WebSocket 提供浏览器远程控制功能"""
import asyncio
import base64
import json
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from .config import load_config, get_proxy
from .logger import get_logger

# 模块级 logger
logger = get_logger("remote_browser")


class BrowserSessionStatus(str, Enum):
    """浏览器会话状态"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    LOGIN_SUCCESS = "login_success"
    STOPPED = "stopped"
    ERROR = "error"


class RemoteBrowserSession:
    """远程浏览器会话"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.status = BrowserSessionStatus.IDLE
        self.message = ""
        self.created_at = datetime.now()

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._screenshot_task: Optional[asyncio.Task] = None
        self._subscribers: list[Callable] = []
        self._login_config: Optional[dict] = None

        # 浏览器视口大小
        self.viewport_width = 1280
        self.viewport_height = 800

    async def start(self) -> bool:
        """启动浏览器"""
        # 防止重复启动
        if self.status not in (BrowserSessionStatus.IDLE, BrowserSessionStatus.STOPPED, BrowserSessionStatus.ERROR):
            logger.debug(f"会话已在运行中，状态: {self.status}")
            return True  # 已经在运行，返回成功

        try:
            self.status = BrowserSessionStatus.STARTING
            self.message = "正在启动浏览器..."
            await self._notify_status()

            # 获取代理配置
            config = load_config()
            proxy_url = get_proxy(config)

            # Playwright 不支持 socks5h://，转换为 socks5://
            playwright_proxy = None
            if proxy_url:
                if proxy_url.startswith("socks5h://"):
                    playwright_proxy = {"server": proxy_url.replace("socks5h://", "socks5://", 1)}
                else:
                    playwright_proxy = {"server": proxy_url}
                logger.debug(f"使用代理: {playwright_proxy['server']}")

            self._playwright = await async_playwright().start()

            # 启动 Chromium（headless 模式）
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                ]
            )

            # 创建上下文（带代理）
            context_options = {
                "viewport": {"width": self.viewport_width, "height": self.viewport_height},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            if playwright_proxy:
                context_options["proxy"] = playwright_proxy

            self._context = await self._browser.new_context(**context_options)

            # 创建页面
            self._page = await self._context.new_page()

            # 监听 URL 变化
            self._page.on("framenavigated", self._on_navigation)

            # 导航到登录页
            self.message = "正在访问 Business Gemini..."
            if playwright_proxy:
                self.message += f" (代理: {playwright_proxy['server']})"
            await self._notify_status()

            try:
                await self._page.goto("https://business.gemini.google/", timeout=60000)
            except Exception as nav_error:
                error_msg = str(nav_error)
                if "net::ERR_" in error_msg or "Timeout" in error_msg:
                    if not playwright_proxy:
                        self.message = f"访问失败: {error_msg}\n提示: 未配置代理，可能需要代理才能访问"
                    else:
                        self.message = f"访问失败: {error_msg}\n提示: 请检查代理配置是否正确"
                else:
                    self.message = f"访问失败: {error_msg}"
                await self._notify_status()
                raise

            self.status = BrowserSessionStatus.RUNNING
            self.message = "浏览器已就绪，请在下方完成登录"
            await self._notify_status()

            # 启动截图推送任务
            self._screenshot_task = asyncio.create_task(self._screenshot_loop())

            return True

        except Exception as e:
            self.status = BrowserSessionStatus.ERROR
            if not self.message.startswith("访问失败"):
                self.message = f"启动失败: {str(e)}"
            await self._notify_status()
            await self.stop()
            return False

    async def _screenshot_loop(self) -> None:
        """定期截图并推送"""
        while self.status == BrowserSessionStatus.RUNNING:
            try:
                if self._page:
                    screenshot = await self._page.screenshot(type="jpeg", quality=80)
                    screenshot_b64 = base64.b64encode(screenshot).decode("utf-8")

                    await self._broadcast({
                        "type": "screenshot",
                        "data": screenshot_b64,
                        "url": self._page.url,
                    })

                await asyncio.sleep(0.3)  # 约 3 FPS

            except Exception as e:
                logger.warning(f"截图错误: {e}")
                await asyncio.sleep(1)

    async def _on_navigation(self, frame) -> None:
        """页面导航事件处理"""
        if frame != self._page.main_frame:
            return

        url = self._page.url
        logger.debug(f"导航到: {url}")

        # 检测是否已登录到主页
        if self._is_main_page(url):
            await self._handle_login_success()

    def _is_main_page(self, url: str) -> bool:
        """判断是否已到达主页面（登录成功）"""
        if "business.gemini.google" not in url:
            return False

        login_hosts = [
            "auth.business.gemini.google",
            "accounts.google.com",
            "accountverification.business.gemini.google",
        ]
        intermediate_paths = ["/admin/create", "/admin/setup"]

        for host in login_hosts:
            if host in url:
                return False
        for path in intermediate_paths:
            if path in url:
                return False

        return "/home/" in url

    async def _handle_login_success(self):
        """处理登录成功"""
        try:
            self.message = "检测到登录成功，正在获取凭证..."
            await self._notify_status()

            # 等待页面加载完成
            await self._page.wait_for_load_state("networkidle", timeout=10000)

            url = self._page.url

            # 解析 csesidx 和 group_id
            csesidx = None
            group_id = None

            if "csesidx=" in url:
                csesidx = url.split("csesidx=", 1)[1].split("&", 1)[0]

            if "/cid/" in url:
                after = url.split("/cid/", 1)[1]
                for sep in ("/", "?", "#"):
                    after = after.split(sep, 1)[0]
                group_id = after

            # 获取 cookies
            cookies = await self._context.cookies()
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

            if secure_c_ses and csesidx and group_id:
                self._login_config = {
                    "secure_c_ses": secure_c_ses,
                    "host_c_oses": host_c_oses,
                    "nid": nid,
                    "csesidx": csesidx,
                    "group_id": group_id,
                    "cookies_saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

                self.status = BrowserSessionStatus.LOGIN_SUCCESS
                self.message = "登录成功！凭证已获取"
                await self._notify_status()

                # 广播登录成功
                await self._broadcast({
                    "type": "login_success",
                    "config": self._login_config,
                })
            else:
                self.message = f"登录成功但凭证不完整: csesidx={csesidx}, group_id={group_id}, cookie={'有' if secure_c_ses else '无'}"
                await self._notify_status()

        except Exception as e:
            logger.error(f"处理登录成功时出错: {e}")
            self.message = f"获取凭证失败: {str(e)}"
            await self._notify_status()

    async def click(self, x: int, y: int) -> None:
        """鼠标点击"""
        if self._page and self.status == BrowserSessionStatus.RUNNING:
            await self._page.mouse.click(x, y)

    async def type_text(self, text: str) -> None:
        """输入文本"""
        if self._page and self.status == BrowserSessionStatus.RUNNING:
            await self._page.keyboard.type(text)

    async def press_key(self, key: str) -> None:
        """按键"""
        if self._page and self.status == BrowserSessionStatus.RUNNING:
            await self._page.keyboard.press(key)

    async def scroll(self, delta_x: int, delta_y: int) -> None:
        """滚动"""
        if self._page and self.status == BrowserSessionStatus.RUNNING:
            await self._page.mouse.wheel(delta_x, delta_y)

    async def navigate(self, url: str) -> None:
        """导航到指定 URL"""
        if self._page and self.status == BrowserSessionStatus.RUNNING:
            await self._page.goto(url, timeout=30000)

    def subscribe(self, callback: Callable) -> None:
        """订阅消息"""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        """取消订阅"""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _broadcast(self, message: dict) -> None:
        """广播消息给所有订阅者"""
        for callback in self._subscribers:
            try:
                await callback(message)
            except Exception as e:
                logger.warning(f"广播消息失败: {e}")

    async def _notify_status(self):
        """通知状态变更"""
        await self._broadcast({
            "type": "status",
            "status": self.status.value,
            "message": self.message,
        })

    async def stop(self) -> None:
        """停止浏览器"""
        self.status = BrowserSessionStatus.STOPPED

        if self._screenshot_task:
            self._screenshot_task.cancel()
            try:
                await self._screenshot_task
            except asyncio.CancelledError:
                pass

        if self._page:
            await self._page.close()
            self._page = None

        if self._context:
            await self._context.close()
            self._context = None

        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        await self._notify_status()

    def get_login_config(self) -> Optional[dict]:
        """获取登录配置"""
        return self._login_config


class RemoteBrowserService:
    """远程浏览器服务管理"""

    def __init__(self):
        self._sessions: Dict[str, RemoteBrowserSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(self) -> RemoteBrowserSession:
        """创建新的浏览器会话"""
        async with self._lock:
            # 清理旧会话
            await self._cleanup_old_sessions()

            # 检查是否有正在运行的会话
            for session in self._sessions.values():
                if session.status in (BrowserSessionStatus.STARTING, BrowserSessionStatus.RUNNING):
                    return session

            # 创建新会话
            import uuid
            session_id = str(uuid.uuid4())
            session = RemoteBrowserSession(session_id)
            self._sessions[session_id] = session

            return session

    async def get_session(self, session_id: str) -> Optional[RemoteBrowserSession]:
        """获取会话"""
        return self._sessions.get(session_id)

    async def get_active_session(self) -> Optional[RemoteBrowserSession]:
        """获取当前活跃的会话"""
        for session in self._sessions.values():
            if session.status in (BrowserSessionStatus.STARTING, BrowserSessionStatus.RUNNING, BrowserSessionStatus.LOGIN_SUCCESS):
                return session
        return None

    async def stop_session(self, session_id: str) -> bool:
        """停止会话"""
        session = self._sessions.get(session_id)
        if session:
            await session.stop()
            return True
        return False

    async def _cleanup_old_sessions(self):
        """清理已完成的会话"""
        to_remove = []
        for session_id, session in self._sessions.items():
            if session.status in (BrowserSessionStatus.STOPPED, BrowserSessionStatus.ERROR, BrowserSessionStatus.LOGIN_SUCCESS):
                age = (datetime.now() - session.created_at).total_seconds()
                if age > 300:  # 5 分钟后清理
                    to_remove.append(session_id)

        for session_id in to_remove:
            session = self._sessions.pop(session_id, None)
            if session:
                await session.stop()


# 全局服务实例
_browser_service: Optional[RemoteBrowserService] = None


def get_browser_service() -> RemoteBrowserService:
    """获取全局浏览器服务实例"""
    global _browser_service
    if _browser_service is None:
        _browser_service = RemoteBrowserService()
    return _browser_service

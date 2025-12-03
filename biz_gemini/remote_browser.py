"""远程浏览器服务 - 通过 WebSocket 提供浏览器远程控制功能"""
import asyncio
import base64
import json
import os
import shutil
import tempfile
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from .config import load_config, get_proxy
from .logger import get_logger

# 模块级 logger
logger = get_logger("remote_browser")

# 临时用户数据目录的基础路径
_TEMP_PROFILE_BASE = os.path.join(tempfile.gettempdir(), "gemini_chat_browser_profiles")


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

    def __init__(self, session_id: str, profile_dir: Optional[str] = None, start_url: str = "https://business.gemini.google/"):
        """初始化浏览器会话

        Args:
            session_id: 会话 ID
            profile_dir: 用户数据目录路径。如果为 None，使用临时目录（每次清空）
            start_url: 启动后首先访问的地址
        """
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

        # 用户数据目录策略
        self._custom_profile_dir = profile_dir  # 显式指定的目录
        self._temp_profile_dir: Optional[str] = None  # 实际使用的临时目录路径
        self._start_url = start_url or "https://business.gemini.google/"

    def _prepare_profile_dir(self) -> Optional[str]:
        """准备用户数据目录

        如果显式指定了 profile_dir，则使用它；
        否则使用临时目录，并在启动前清空以避免旧账号/旧 cookie 污染。

        Returns:
            用户数据目录路径，如果不使用持久化则返回 None
        """
        if self._custom_profile_dir:
            # 显式指定的目录，直接使用（不清空）
            logger.info(f"使用自定义用户数据目录: {self._custom_profile_dir}")
            return self._custom_profile_dir

        # 使用临时目录策略
        self._temp_profile_dir = os.path.join(_TEMP_PROFILE_BASE, f"session_{self.session_id}")

        # 清空现有目录（避免旧账号/旧 cookie 污染）
        if os.path.exists(self._temp_profile_dir):
            logger.info(f"清空临时用户数据目录: {self._temp_profile_dir}")
            try:
                shutil.rmtree(self._temp_profile_dir)
            except Exception as e:
                logger.warning(f"清空临时目录失败: {e}")

        # 创建新目录
        os.makedirs(self._temp_profile_dir, exist_ok=True)
        logger.info(f"使用临时用户数据目录: {self._temp_profile_dir}")

        return self._temp_profile_dir

    def get_profile_dir(self) -> Optional[str]:
        """获取当前使用的用户数据目录路径"""
        return self._custom_profile_dir or self._temp_profile_dir

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

            # 准备用户数据目录（清空临时目录以保证干净环境）
            profile_dir = self._prepare_profile_dir()

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

            # 优先尝试以“真浏览器”形态启动 Chrome，减少被判定为不安全的概率
            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,800",
            ]

            launch_kwargs = {
                "headless": False,
                "args": launch_args,
            }

            browser = None
            # 优先使用系统 Chrome（若 playwright 安装了 chrome）
            try:
                browser = await self._playwright.chromium.launch(channel="chrome", **launch_kwargs)
                logger.info("使用 Chrome channel 启动浏览器")
            except Exception as chrome_err:  # noqa: BLE001
                logger.warning(f"Chrome channel 启动失败，回退 chromium: {chrome_err}")
                browser = await self._playwright.chromium.launch(**launch_kwargs)

            self._browser = browser

            # 创建上下文（带代理）
            context_options = {
                "viewport": {"width": self.viewport_width, "height": self.viewport_height},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            }
            if playwright_proxy:
                context_options["proxy"] = playwright_proxy

            self._context = await self._browser.new_context(**context_options)
            # 隐藏自动化标识，降低账号登录时的安全提示概率
            try:
                await self._context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
            except Exception as e:
                logger.debug(f"设置 webdriver 伪装失败: {e}")

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
                await self._page.goto(self._start_url, timeout=60000)
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
            # 如果已经登录成功但凭证不完整，检查新 URL 是否包含 group_id
            if self.status == BrowserSessionStatus.LOGIN_SUCCESS and self._login_config is None:
                # 凭证不完整，检查新 URL 是否有 group_id
                if "/cid/" in url:
                    logger.info(f"检测到包含 group_id 的 URL: {url}")
                    await self._handle_login_success()
            elif self.status == BrowserSessionStatus.RUNNING:
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

            # 如果 URL 中没有 group_id，尝试从页面中获取或等待重定向
            if not group_id:
                logger.info("URL 中没有 group_id，尝试从页面获取...")
                self.message = "正在获取 group_id..."
                await self._notify_status()

                # 方法1: 尝试从页面的 JavaScript 变量或 DOM 中获取 group_id
                try:
                    # 等待页面完全加载
                    await asyncio.sleep(2)

                    # 尝试从 URL 中的重定向获取（有时页面会自动重定向到包含 cid 的 URL）
                    current_url = self._page.url
                    if "/cid/" in current_url:
                        after = current_url.split("/cid/", 1)[1]
                        for sep in ("/", "?", "#"):
                            after = after.split(sep, 1)[0]
                        group_id = after
                        logger.info(f"从重定向 URL 获取到 group_id: {group_id}")
                except Exception as e:
                    logger.warning(f"从页面获取 group_id 失败: {e}")

                # 方法2: 如果还是没有 group_id，尝试点击页面上的链接或等待用户操作
                if not group_id:
                    # 尝试从页面中查找包含 /cid/ 的链接
                    try:
                        links = await self._page.query_selector_all('a[href*="/cid/"]')
                        if links:
                            href = await links[0].get_attribute('href')
                            if href and "/cid/" in href:
                                after = href.split("/cid/", 1)[1]
                                for sep in ("/", "?", "#"):
                                    after = after.split(sep, 1)[0]
                                group_id = after
                                logger.info(f"从页面链接获取到 group_id: {group_id}")
                    except Exception as e:
                        logger.warning(f"从页面链接获取 group_id 失败: {e}")

            # 获取 cookies - 收集 auth.business.gemini.google 和 business.gemini.google 域的全部 cookie
            cookies = await self._context.cookies()
            secure_c_ses = None
            host_c_oses = None
            nid = None

            # 收集相关域的所有 cookie 用于构造 cookie_raw
            gemini_cookies = []
            target_domains = ["auth.business.gemini.google", "business.gemini.google", ".business.gemini.google"]

            for cookie in cookies:
                cookie_domain = cookie.get("domain", "")
                # 检查是否是目标域的 cookie
                is_target_domain = any(
                    cookie_domain == d or cookie_domain.endswith(d)
                    for d in target_domains
                )
                if is_target_domain:
                    gemini_cookies.append(f"{cookie['name']}={cookie['value']}")

                # 同时提取关键 cookie 字段（保持向后兼容）
                if cookie["name"] == "__Secure-C_SES":
                    secure_c_ses = cookie["value"]
                elif cookie["name"] == "__Host-C_OSES":
                    host_c_oses = cookie["value"]
                elif cookie["name"] == "NID":
                    nid = cookie["value"]

            # 构造 cookie_raw（按原样拼出 raw cookie header）
            cookie_raw = "; ".join(gemini_cookies) if gemini_cookies else None

            if secure_c_ses and csesidx and group_id:
                # 获取当前使用的用户数据目录
                profile_dir = self.get_profile_dir()

                self._login_config = {
                    "secure_c_ses": secure_c_ses,
                    "host_c_oses": host_c_oses,
                    "nid": nid,
                    "csesidx": csesidx,
                    "group_id": group_id,
                    "cookie_raw": cookie_raw,  # 新增：完整的 raw cookie header
                    "cookies_saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "cookie_profile_dir": profile_dir,  # 记录 cookie 来源目录
                }

                # 日志记录 cookie 保存信息
                logger.info(f"登录成功，保存 cookies: csesidx={csesidx}, profile_dir={profile_dir}")
                logger.debug(f"Cookie 长度: secure_c_ses={len(secure_c_ses) if secure_c_ses else 0}, "
                           f"host_c_oses={len(host_c_oses) if host_c_oses else 0}, "
                           f"nid={len(nid) if nid else 0}, "
                           f"cookie_raw={len(cookie_raw) if cookie_raw else 0}")

                self.status = BrowserSessionStatus.LOGIN_SUCCESS
                self.message = "登录成功！凭证已获取"
                await self._notify_status()

                # 广播登录成功
                await self._broadcast({
                    "type": "login_success",
                    "config": self._login_config,
                })
            else:
                # 凭证不完整，提供详细提示
                missing = []
                if not csesidx:
                    missing.append("csesidx")
                if not group_id:
                    missing.append("group_id")
                if not secure_c_ses:
                    missing.append("cookie")

                if not group_id and csesidx and secure_c_ses:
                    # 只缺少 group_id，提示用户点击进入对话页面
                    # 保持 RUNNING 状态，继续监听 URL 变化
                    self.status = BrowserSessionStatus.RUNNING
                    self.message = "登录成功，但需要获取 group_id。请在浏览器中点击任意对话或创建新对话"
                    logger.info(f"等待用户操作以获取 group_id，当前 URL: {self._page.url}")

                    # 广播提示消息
                    await self._broadcast({
                        "type": "status",
                        "status": "waiting_group_id",
                        "message": self.message,
                    })
                else:
                    self.message = f"登录成功但凭证不完整，缺少: {', '.join(missing)}"
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

    async def create_session(self, start_url: Optional[str] = None, use_profile_dir: bool = False) -> RemoteBrowserSession:
        """创建新的浏览器会话"""
        async with self._lock:
            # 清理旧会话
            await self._cleanup_old_sessions()

            # 检查是否有正在运行的会话
            for session in self._sessions.values():
                if session.status in (BrowserSessionStatus.STARTING, BrowserSessionStatus.RUNNING):
                    return session

            profile_dir = None
            if use_profile_dir:
                try:
                    config = load_config()
                    profile_candidate = config.get("cookie_profile_dir")
                    if profile_candidate and os.path.exists(profile_candidate):
                        profile_dir = profile_candidate
                        logger.info(f"使用 cookie_profile_dir 作为用户数据目录: {profile_dir}")
                    elif profile_candidate:
                        logger.warning(f"cookie_profile_dir 不存在: {profile_candidate}")
                except Exception as e:
                    logger.warning(f"读取 cookie_profile_dir 失败: {e}")

            # 创建新会话
            import uuid
            session_id = str(uuid.uuid4())
            session = RemoteBrowserSession(session_id, profile_dir=profile_dir, start_url=start_url or "https://business.gemini.google/")
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

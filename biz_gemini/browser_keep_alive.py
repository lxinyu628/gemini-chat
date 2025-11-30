"""浏览器保活服务 - 使用 Playwright 定期访问目标站点保持 Cookie 有效"""
import asyncio
from datetime import datetime
from typing import Callable, Optional, List

from .auth import on_cookie_refreshed
from .config import (
    TIME_FMT,
    get_proxy,
    load_config,
    mark_cookie_expired,
    mark_cookie_valid,
    save_config,
)
from .logger import get_logger

logger = get_logger("browser_keep_alive")

# 目标站点 URL
TARGET_URL = "https://business.gemini.google/"
LOGIN_HOSTS = [
    "auth.business.gemini.google",
    "accounts.google.com",
    "accountverification.business.gemini.google",
]


class BrowserKeepAliveService:
    """浏览器保活服务

    使用 Playwright 定期访问目标站点，保持 Cookie 有效。
    每次访问后提取最新 Cookie 并更新配置。
    """

    def __init__(self, interval_minutes: int = 60, headless: bool = True):
        """初始化浏览器保活服务

        Args:
            interval_minutes: 保活间隔（分钟），默认 60 分钟
            headless: 是否无头模式，默认 True
        """
        self.interval_minutes = interval_minutes
        self.headless = headless
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_refresh: Optional[datetime] = None
        self._refresh_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._callbacks: List[Callable] = []

        # Playwright 相关
        self._playwright = None
        self._browser = None
        self._context = None

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
        """启动浏览器保活服务"""
        if self._running:
            logger.info("浏览器保活服务已在运行")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"浏览器保活服务已启动，间隔: {self.interval_minutes} 分钟, 无头模式: {self.headless}")
        self._notify("started", {"interval_minutes": self.interval_minutes})

    async def stop(self) -> None:
        """停止浏览器保活服务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self._cleanup_browser()
        logger.info("浏览器保活服务已停止")
        self._notify("stopped")

    async def _cleanup_browser(self) -> None:
        """清理浏览器资源"""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def _init_browser(self) -> bool:
        """初始化浏览器（带反检测配置）"""
        try:
            from playwright.async_api import async_playwright

            config = load_config()
            proxy = get_proxy(config)

            # Playwright 不支持 socks5h://，转换为 socks5://
            playwright_proxy = proxy
            if proxy and proxy.startswith("socks5h://"):
                playwright_proxy = proxy.replace("socks5h://", "socks5://", 1)

            self._playwright = await async_playwright().start()

            # 反检测：使用更真实的浏览器启动参数
            launch_args = [
                "--disable-blink-features=AutomationControlled",  # 禁用自动化控制特征
                "--disable-infobars",  # 禁用信息栏
                "--disable-dev-shm-usage",  # 禁用 /dev/shm 使用
                "--no-first-run",  # 跳过首次运行
                "--no-default-browser-check",  # 跳过默认浏览器检查
                "--disable-background-timer-throttling",  # 禁用后台定时器节流
                "--disable-backgrounding-occluded-windows",  # 禁用遮挡窗口后台化
                "--disable-renderer-backgrounding",  # 禁用渲染器后台化
            ]

            # 尝试使用本机 Chrome，如果不存在则使用 Playwright 自带的 Chromium
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless,
                    channel="chrome",  # 优先使用本机 Chrome
                    args=launch_args,
                )
            except Exception as e:
                logger.info(f"本机 Chrome 不可用 ({e})，使用 Playwright Chromium")
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless,
                    args=launch_args,
                )

            # 反检测：使用更真实的浏览器上下文配置
            context_kwargs = {
                # 模拟真实的视口大小
                "viewport": {"width": 1920, "height": 1080},
                # 模拟真实的 User-Agent
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                # 设置语言
                "locale": "zh-CN",
                # 设置时区
                "timezone_id": "Asia/Shanghai",
                # 模拟真实的屏幕参数
                "screen": {"width": 1920, "height": 1080},
                # 设置设备缩放因子
                "device_scale_factor": 1,
                # 启用 JavaScript
                "java_script_enabled": True,
                # 忽略 HTTPS 错误
                "ignore_https_errors": True,
            }

            if playwright_proxy:
                context_kwargs["proxy"] = {"server": playwright_proxy}

            self._context = await self._browser.new_context(**context_kwargs)

            # 反检测：注入脚本覆盖 navigator.webdriver
            await self._context.add_init_script("""
                // 覆盖 navigator.webdriver
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // 覆盖 navigator.plugins（模拟真实插件）
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin' }
                    ]
                });

                // 覆盖 navigator.languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en']
                });

                // 覆盖 chrome 对象
                window.chrome = {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };

                // 覆盖权限查询
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)

            # 设置现有 Cookie
            await self._set_cookies_from_config(config)

            logger.info(f"浏览器已初始化 (代理: {playwright_proxy}, 无头: {self.headless}, 反检测: 已启用)")
            return True

        except Exception as e:
            logger.error(f"初始化浏览器失败: {e}")
            self._last_error = str(e)
            await self._cleanup_browser()
            return False

    async def _simulate_human_behavior(self, page) -> None:
        """模拟人类行为，降低被检测风险"""
        import random

        try:
            # 随机等待 1-3 秒
            await asyncio.sleep(random.uniform(1, 3))

            # 随机移动鼠标
            viewport = page.viewport_size
            if viewport:
                x = random.randint(100, viewport["width"] - 100)
                y = random.randint(100, viewport["height"] - 100)
                await page.mouse.move(x, y)

            # 随机滚动页面
            scroll_y = random.randint(100, 300)
            await page.mouse.wheel(0, scroll_y)

            # 再等待一下
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # 滚动回顶部
            await page.mouse.wheel(0, -scroll_y)

        except Exception as e:
            # 模拟行为失败不影响主流程
            logger.debug(f"模拟人类行为时出错（可忽略）: {e}")

    async def _set_cookies_from_config(self, config: dict) -> None:
        """从配置设置 Cookie 到浏览器上下文"""
        if not self._context:
            return

        cookies = []
        domain = ".business.gemini.google"

        secure_c_ses = config.get("secure_c_ses")
        if secure_c_ses:
            cookies.append({
                "name": "__Secure-C_SES",
                "value": secure_c_ses,
                "domain": domain,
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })

        host_c_oses = config.get("host_c_oses")
        if host_c_oses:
            cookies.append({
                "name": "__Host-C_OSES",
                "value": host_c_oses,
                "domain": domain,
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })

        nid = config.get("nid")
        if nid:
            cookies.append({
                "name": "NID",
                "value": nid,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })

        if cookies:
            await self._context.add_cookies(cookies)
            logger.debug(f"已设置 {len(cookies)} 个 Cookie")

    def _get_random_interval(self) -> float:
        """获取随机化的保活间隔（秒）

        在设定间隔的基础上随机浮动 ±20%，使行为更像真人
        """
        import random

        base_seconds = self.interval_minutes * 60
        # 随机浮动 ±20%
        min_seconds = base_seconds * 0.8
        max_seconds = base_seconds * 1.2
        interval = random.uniform(min_seconds, max_seconds)

        logger.debug(f"下次保活间隔: {interval / 60:.1f} 分钟")
        return interval

    async def _run_loop(self) -> None:
        """保活循环"""
        # 启动后立即执行一次
        await self._do_refresh()

        while self._running:
            try:
                # 等待随机化的间隔
                interval = self._get_random_interval()
                await asyncio.sleep(interval)

                if not self._running:
                    break

                await self._do_refresh()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"保活循环异常: {e}")
                self._error_count += 1
                self._last_error = str(e)
                # 出错后等待 3-7 分钟再重试（也随机化）
                import random
                await asyncio.sleep(random.uniform(180, 420))

    async def _do_refresh(self) -> bool:
        """执行一次保活刷新

        Returns:
            是否成功
        """
        try:
            config = load_config()

            # 检查是否有必要的凭证
            if not config.get("secure_c_ses") or not config.get("csesidx"):
                logger.debug("未登录，跳过浏览器保活")
                return False

            logger.info(f"开始浏览器保活... ({datetime.now().strftime('%H:%M:%S')})")

            # 初始化浏览器（如果需要）
            if not self._browser or not self._context:
                if not await self._init_browser():
                    return False

            # 创建新页面访问目标站点
            page = await self._context.new_page()

            try:
                await page.goto(TARGET_URL, timeout=60000)
                await page.wait_for_load_state("networkidle", timeout=30000)

                # 反检测：模拟人类行为 - 随机等待和鼠标移动
                await self._simulate_human_behavior(page)

                current_url = page.url

                # 检查是否被重定向到登录页
                is_login_page = any(host in current_url for host in LOGIN_HOSTS)

                if is_login_page:
                    logger.warning("访问被重定向到登录页，Cookie 可能已过期")
                    mark_cookie_expired("浏览器保活检测到登录页重定向")
                    self._notify("cookie_expired", {"url": current_url})
                    return False

                # 提取最新 Cookie
                cookies = await self._context.cookies()
                new_config = self._extract_cookies(cookies, current_url)

                if new_config:
                    # 更新配置
                    save_config(new_config)

                    # 清理 JWT/session 缓存
                    on_cookie_refreshed()

                    self._last_refresh = datetime.now()
                    self._refresh_count += 1
                    self._last_error = None

                    logger.info(f"浏览器保活成功 (第 {self._refresh_count} 次)")
                    self._notify("refreshed", {
                        "count": self._refresh_count,
                        "time": self._last_refresh.isoformat(),
                    })
                    return True
                else:
                    logger.warning("未能从浏览器提取有效 Cookie")
                    return False

            finally:
                await page.close()

        except Exception as e:
            self._error_count += 1
            self._last_error = str(e)
            logger.error(f"浏览器保活失败: {e}")
            self._notify("error", {"error": str(e)})

            # 清理浏览器，下次重新初始化
            await self._cleanup_browser()
            return False

    def _extract_cookies(self, cookies: list, current_url: str) -> Optional[dict]:
        """从浏览器 Cookie 提取配置

        Args:
            cookies: Playwright 返回的 Cookie 列表
            current_url: 当前页面 URL

        Returns:
            配置字典，如果提取失败返回 None
        """
        secure_c_ses = None
        host_c_oses = None
        nid = None

        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")

            if name == "__Secure-C_SES":
                secure_c_ses = value
            elif name == "__Host-C_OSES":
                host_c_oses = value
            elif name == "NID":
                nid = value

        if not secure_c_ses:
            return None

        # 尝试从 URL 提取 csesidx
        csesidx = None
        if "csesidx=" in current_url:
            csesidx = current_url.split("csesidx=", 1)[1].split("&", 1)[0]

        result = {
            "secure_c_ses": secure_c_ses,
            "cookies_saved_at": datetime.now().strftime(TIME_FMT),
        }

        if host_c_oses:
            result["host_c_oses"] = host_c_oses
        if nid:
            result["nid"] = nid
        if csesidx:
            result["csesidx"] = csesidx

        return result

    async def refresh_now(self) -> dict:
        """立即执行一次保活刷新"""
        try:
            success = await self._do_refresh()
            return {
                "success": success,
                "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
                "refresh_count": self._refresh_count,
                "error": self._last_error if not success else None,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    def get_status(self) -> dict:
        """获取服务状态"""
        return {
            "running": self._running,
            "interval_minutes": self.interval_minutes,
            "headless": self.headless,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "refresh_count": self._refresh_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "browser_active": self._browser is not None,
        }


# 全局服务实例
_browser_keep_alive_service: Optional[BrowserKeepAliveService] = None


def get_browser_keep_alive_service(
    interval_minutes: int = 60,
    headless: bool = True,
) -> BrowserKeepAliveService:
    """获取全局浏览器保活服务实例"""
    global _browser_keep_alive_service
    if _browser_keep_alive_service is None:
        _browser_keep_alive_service = BrowserKeepAliveService(
            interval_minutes=interval_minutes,
            headless=headless,
        )
    return _browser_keep_alive_service


async def try_refresh_cookie_via_browser(headless: bool = True) -> dict:
    """尝试通过浏览器刷新 Cookie（手动触发）

    这是一个独立的函数，不依赖全局服务实例。
    用于手动触发 Cookie 刷新。

    Args:
        headless: 是否无头模式

    Returns:
        {
            "success": bool,
            "message": str,
            "needs_manual_login": bool,  # 是否需要手动登录
        }
    """
    from playwright.async_api import async_playwright

    config = load_config()
    proxy = get_proxy(config)

    # Playwright 不支持 socks5h://
    playwright_proxy = proxy
    if proxy and proxy.startswith("socks5h://"):
        playwright_proxy = proxy.replace("socks5h://", "socks5://", 1)

    playwright = None
    browser = None

    try:
        playwright = await async_playwright().start()

        # 反检测：使用更真实的浏览器启动参数
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        # 尝试使用本机 Chrome，如果不存在则使用 Playwright Chromium
        try:
            browser = await playwright.chromium.launch(
                headless=headless,
                channel="chrome",
                args=launch_args,
            )
        except Exception:
            browser = await playwright.chromium.launch(
                headless=headless,
                args=launch_args,
            )

        # 反检测：使用更真实的浏览器上下文配置
        context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "screen": {"width": 1920, "height": 1080},
            "device_scale_factor": 1,
            "java_script_enabled": True,
            "ignore_https_errors": True,
        }
        if playwright_proxy:
            context_kwargs["proxy"] = {"server": playwright_proxy}

        context = await browser.new_context(**context_kwargs)

        # 反检测：注入脚本
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' }
                ]
            });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        """)

        # 设置现有 Cookie
        cookies = []
        domain = ".business.gemini.google"

        secure_c_ses = config.get("secure_c_ses")
        if secure_c_ses:
            cookies.append({
                "name": "__Secure-C_SES",
                "value": secure_c_ses,
                "domain": domain,
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })

        host_c_oses = config.get("host_c_oses")
        if host_c_oses:
            cookies.append({
                "name": "__Host-C_OSES",
                "value": host_c_oses,
                "domain": domain,
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })

        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()

        try:
            await page.goto(TARGET_URL, timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=30000)

            current_url = page.url

            # 检查是否在登录页
            is_login_page = any(host in current_url for host in LOGIN_HOSTS)

            if is_login_page:
                logger.warning("Cookie 已失效，需要手动登录")
                return {
                    "success": False,
                    "message": "Cookie 已失效，需要手动登录",
                    "needs_manual_login": True,
                }

            # 提取 Cookie
            browser_cookies = await context.cookies()
            new_secure_c_ses = None
            new_host_c_oses = None
            new_nid = None

            for cookie in browser_cookies:
                if cookie["name"] == "__Secure-C_SES":
                    new_secure_c_ses = cookie["value"]
                elif cookie["name"] == "__Host-C_OSES":
                    new_host_c_oses = cookie["value"]
                elif cookie["name"] == "NID":
                    new_nid = cookie["value"]

            if not new_secure_c_ses:
                return {
                    "success": False,
                    "message": "未能提取有效 Cookie",
                    "needs_manual_login": True,
                }

            # 提取 csesidx
            csesidx = None
            if "csesidx=" in current_url:
                csesidx = current_url.split("csesidx=", 1)[1].split("&", 1)[0]

            # 更新配置
            new_config = {
                "secure_c_ses": new_secure_c_ses,
                "cookies_saved_at": datetime.now().strftime(TIME_FMT),
            }
            if new_host_c_oses:
                new_config["host_c_oses"] = new_host_c_oses
            if new_nid:
                new_config["nid"] = new_nid
            if csesidx:
                new_config["csesidx"] = csesidx

            save_config(new_config)
            on_cookie_refreshed()

            logger.info("Cookie 刷新成功")
            return {
                "success": True,
                "message": "Cookie 刷新成功",
                "needs_manual_login": False,
            }

        finally:
            await page.close()
            await context.close()

    except Exception as e:
        logger.error(f"浏览器刷新 Cookie 失败: {e}")
        return {
            "success": False,
            "message": str(e),
            "needs_manual_login": False,
        }

    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()

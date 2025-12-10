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
# 验证码页面特征（Google 验证码页面可能的 URL 模式）
VERIFICATION_INDICATORS = [
    "accountverification.business.gemini.google",
    "challenge/",
    "/signin/v2/challenge",
    "/v3/signin/challenge",
    "signin/challenge",
    "accounts.google.com/v3/signin",
    "accounts.google.com/signin",
    "interstitialreturn",  # Google 中间页
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

        # __Secure- 前缀的 Cookie 可以设置 domain
        secure_c_ses = config.get("secure_c_ses")
        if secure_c_ses:
            cookies.append({
                "name": "__Secure-C_SES",
                "value": secure_c_ses,
                "domain": "business.gemini.google",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })

        # __Host- 前缀的 Cookie 不能设置 domain，使用 url（不能同时设置 path）
        host_c_oses = config.get("host_c_oses")
        if host_c_oses:
            cookies.append({
                "name": "__Host-C_OSES",
                "value": host_c_oses,
                "url": "https://business.gemini.google/",
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
                    
                    # 检查是否启用了自动登录
                    imap_config = config.get("imap", {})
                    auto_login_enabled = imap_config.get("auto_login", False)
                    imap_enabled = imap_config.get("enabled", False)
                    
                    if auto_login_enabled and imap_enabled:
                        logger.info("[自动登录] 浏览器保活服务检测到过期，尝试自动登录...")
                        result = await _auto_login_flow(page, self._context, config)
                        if result.get("success"):
                            mark_cookie_valid()
                            self._last_refresh = datetime.now()
                            self._refresh_count += 1
                            self._last_error = None
                            logger.info(f"[自动登录] ✓ 登录成功，浏览器保活恢复 (第 {self._refresh_count} 次)")
                            self._notify("refreshed", {
                                "count": self._refresh_count,
                                "time": self._last_refresh.isoformat(),
                                "auto_login": True,
                            })
                            return True
                        else:
                            logger.warning(f"[自动登录] 失败: {result.get('message')}")
                            self._notify("auto_login_failed", {"message": result.get("message")})
                            return False
                    else:
                        logger.info(f"[自动登录] 跳过：auto_login={auto_login_enabled}, imap={imap_enabled}")
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

        # 收集相关域的所有 cookie 用于构造 cookie_raw (去重)
        cookie_map = {}
        target_domains = ["auth.business.gemini.google", "business.gemini.google", ".business.gemini.google"]

        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            
            # 收集 cookie_raw
            cookie_domain = cookie.get("domain", "")
            if any(cookie_domain == d or cookie_domain.endswith(d) for d in target_domains):
                cookie_map[name] = value

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

        # 构造 cookie_raw
        gemini_cookies = [f"{k}={v}" for k, v in cookie_map.items()]
        cookie_raw = "; ".join(gemini_cookies) if gemini_cookies else None

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
        if cookie_raw:
            result["cookie_raw"] = cookie_raw

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
    """尝试通过浏览器刷新 Cookie（支持自动登录）

    这是一个独立的函数，不依赖全局服务实例。
    用于手动触发 Cookie 刷新，如果需要登录会尝试自动完成。

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

    # 检查是否启用了自动登录和 IMAP
    imap_config = config.get("imap", {})
    imap_enabled = imap_config.get("enabled", False)
    # auto_login 配置在 imap 对象中
    auto_login_enabled = imap_config.get("auto_login", False)
    
    # 调试日志
    logger.info(f"[自动登录] 配置检查: imap_enabled={imap_enabled}, auto_login_enabled={auto_login_enabled}")

    # Playwright 不支持 socks5h://
    playwright_proxy = proxy
    if proxy and proxy.startswith("socks5h://"):
        playwright_proxy = proxy.replace("socks5h://", "socks5://", 1)

    playwright = None
    browser = None

    try:
        logger.info("[Cookie刷新] 开始浏览器刷新流程...")
        playwright = await async_playwright().start()
        logger.info("[Cookie刷新] Playwright 已启动")

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
            "viewport": {"width": 1280, "height": 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
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

        # 创建页面
        page = await context.new_page()

        try:
            # 第一步：先访问页面（可能会重定向到登录页）
            logger.info("[自动登录] 正在访问目标页面...")
            await page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")

            # 第二步：设置 Cookie（在正确的域名上下文中）
            cookies = []

            secure_c_ses = config.get("secure_c_ses")
            if secure_c_ses:
                cookies.append({
                    "name": "__Secure-C_SES",
                    "value": secure_c_ses,
                    "domain": "business.gemini.google",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                })

            host_c_oses = config.get("host_c_oses")
            if host_c_oses:
                cookies.append({
                    "name": "__Host-C_OSES",
                    "value": host_c_oses,
                    "domain": "business.gemini.google",
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
                await context.add_cookies(cookies)
                logger.info(f"[自动登录] 已设置 {len(cookies)} 个 Cookie")

            # 第三步：重新访问目标页面（带上 Cookie）
            logger.info("[自动登录] 重新访问目标页面...")
            await page.goto(TARGET_URL, timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=30000)

            current_url = page.url
            logger.info(f"[自动登录] 当前 URL: {current_url}")

            # 检查是否在登录页
            is_login_page = any(host in current_url for host in LOGIN_HOSTS)

            if is_login_page:
                logger.warning(f"[自动登录] 被重定向到登录页: {current_url}")
                
                # 如果启用了自动登录和 IMAP，尝试自动完成登录
                if auto_login_enabled and imap_enabled:
                    logger.info("[自动登录] 尝试自动完成登录流程...")
                    result = await _auto_login_flow(page, context, config)
                    if result["success"]:
                        return result
                    else:
                        logger.warning(f"[自动登录] 自动登录失败: {result.get('message')}")
                        return result
                else:
                    reason = []
                    if not auto_login_enabled:
                        reason.append("auto_login 未启用")
                    if not imap_enabled:
                        reason.append("IMAP 未启用")
                    logger.info(f"[自动登录] 跳过自动登录: {', '.join(reason)}")
                    return {
                        "success": False,
                        "message": f"Cookie 已失效，需要手动登录（{', '.join(reason)}）",
                        "needs_manual_login": True,
                    }

            # Cookie 有效，提取最新值
            return await _extract_and_save_cookies(context, page.url)

        finally:
            await page.close()
            await context.close()

    except Exception as e:
        logger.error(f"[自动登录] 浏览器刷新 Cookie 失败: {e}")
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


async def _auto_login_flow(page, context, config: dict) -> dict:
    """自动登录流程

    流程：
    1. 检测到登录页
    2. 输入邮箱并点击下一步
    3. 等待验证码页面
    4. 从 IMAP 获取验证码
    5. 填充验证码并提交

    Args:
        page: Playwright 页面对象
        context: Playwright 上下文对象
        config: 配置字典

    Returns:
        {"success": bool, "message": str, "needs_manual_login": bool}
    """
    try:
        # 获取保存的用户邮箱
        session_config = config.get("session", {})
        username = session_config.get("username")

        if not username:
            logger.warning("[自动登录] 未找到保存的用户邮箱，无法自动登录")
            return {
                "success": False,
                "message": "未找到保存的用户邮箱，请先手动登录一次",
                "needs_manual_login": True,
            }

        logger.info(f"[自动登录] 使用邮箱: {username}")

        # 等待页面加载
        await asyncio.sleep(2)

        max_wait_seconds = 180  # 最多等待 3 分钟
        start_time = asyncio.get_event_loop().time()
        email_input_handled = False
        verification_handled = False

        # 关键：在登录过程中捕获 csesidx（它只在中间页面的 URL 中出现）
        # 使用列表以便在回调中修改
        captured_csesidx_holder = [None]
        # 记录所有经过的 URL，用于调试
        all_navigated_urls = []

        # 注册导航事件监听器，捕获所有经过的 URL（包括重定向）
        def on_frame_navigated(frame):
            if frame == page.main_frame:
                url = frame.url
                all_navigated_urls.append(url)
                logger.info(f"[自动登录] 页面导航: {url}")
                if "csesidx=" in url and not captured_csesidx_holder[0]:
                    csesidx = url.split("csesidx=", 1)[1].split("&", 1)[0]
                    captured_csesidx_holder[0] = csesidx
                    logger.info(f"[自动登录] ★ 捕获到 csesidx: {csesidx}")

        page.on("framenavigated", on_frame_navigated)

        loop_count = 0
        while asyncio.get_event_loop().time() - start_time < max_wait_seconds:
            loop_count += 1
            current_url = page.url

            # 也从当前 URL 尝试捕获（作为备份）
            if "csesidx=" in current_url and not captured_csesidx_holder[0]:
                captured_csesidx_holder[0] = current_url.split("csesidx=", 1)[1].split("&", 1)[0]
                logger.info(f"[自动登录] 从当前 URL 捕获到 csesidx: {captured_csesidx_holder[0]}")

            # 调试日志：每5次循环或重要状态变化时输出
            is_main = _is_main_page(current_url)
            is_verif = _is_verification_page(current_url)
            is_login = _is_login_page(current_url)

            # 每5次循环输出一次日志，避免刷屏
            if loop_count % 5 == 1:
                logger.info(f"[自动登录] 循环#{loop_count} URL: {current_url[:100]}")
                logger.info(f"[自动登录] 状态: main={is_main}, verif={is_verif}, login={is_login}, email_done={email_input_handled}, code_done={verification_handled}, csesidx={captured_csesidx_holder[0]}")

            # 检查是否已到达主页（登录成功）
            if _is_main_page(current_url):
                logger.info(f"[自动登录] ✓ 登录成功，已到达主页")
                logger.info(f"[自动登录] 捕获的 csesidx: {captured_csesidx_holder[0]}")
                logger.info(f"[自动登录] 所有导航过的 URL ({len(all_navigated_urls)} 个):")
                for i, url in enumerate(all_navigated_urls):
                    logger.info(f"[自动登录]   [{i+1}] {url}")
                # 移除事件监听器
                page.remove_listener("framenavigated", on_frame_navigated)
                # 传递捕获的 csesidx
                return await _extract_and_save_cookies(context, current_url, captured_csesidx=captured_csesidx_holder[0])
            
            # 检查是否在验证码页面
            if _is_verification_page(current_url) and not verification_handled:
                logger.info("[自动登录] 检测到验证码页面，开始获取验证码...")
                
                # 等待一下让页面稳定
                await asyncio.sleep(3)
                
                # 从 IMAP 获取验证码
                from .imap_reader import get_verification_code
                code = await get_verification_code(config=config)
                
                if code:
                    logger.info(f"[自动登录] 从 IMAP 获取到验证码: {code}")
                    
                    # 填充验证码
                    success = await _fill_verification_code(page, code)
                    if success:
                        verification_handled = True
                        logger.info("[自动登录] 验证码已填充，等待页面跳转...")
                    else:
                        logger.warning("[自动登录] 验证码填充失败")
                else:
                    logger.warning("[自动登录] 未能从 IMAP 获取验证码")
                    return {
                        "success": False,
                        "message": "未能从 IMAP 获取验证码",
                        "needs_manual_login": True,
                    }
            
            # 检查是否在登录页（需要输入邮箱）
            elif _is_login_page(current_url) and not email_input_handled:
                logger.info("[自动登录] 检测到登录页，准备输入邮箱...")
                
                # 输入邮箱并点击下一步
                success = await _input_email_and_proceed(page, username)
                if success:
                    email_input_handled = True
                    logger.info("[自动登录] 邮箱已输入，等待页面跳转...")
                else:
                    logger.warning("[自动登录] 输入邮箱失败")
                    return {
                        "success": False,
                        "message": "在登录页输入邮箱失败",
                        "needs_manual_login": True,
                    }
            
            # 如果邮箱已输入但页面既不是验证码页面也不是登录页面，记录警告
            if email_input_handled and not is_verif and not is_login and not is_main:
                if loop_count % 10 == 1:
                    logger.warning(f"[自动登录] 未识别的页面状态，URL: {current_url}")
            
            # 等待页面变化
            await asyncio.sleep(2)
        
        # 超时
        logger.warning("[自动登录] 登录超时")
        # 移除事件监听器
        try:
            page.remove_listener("framenavigated", on_frame_navigated)
        except Exception:
            pass
        return {
            "success": False,
            "message": "自动登录超时（3分钟）",
            "needs_manual_login": True,
        }

    except Exception as e:
        import traceback
        logger.error(f"[自动登录] 自动登录流程异常: {e}")
        logger.error(f"[自动登录] 堆栈追踪:\n{traceback.format_exc()}")
        # 移除事件监听器
        try:
            page.remove_listener("framenavigated", on_frame_navigated)
        except Exception:
            pass
        return {
            "success": False,
            "message": str(e),
            "needs_manual_login": True,
        }


async def _fill_verification_code(page, code: str) -> bool:
    """填充验证码
    
    Args:
        page: Playwright 页面对象
        code: 验证码字符串
        
    Returns:
        是否成功
    """
    try:
        logger.info(f"[自动登录] 准备填充验证码: {code}")
        
        # Google 验证码输入框选择器
        google_pin_selector = 'input[name="pinInput"]'
        
        # 等待输入框加载
        pin_input = None
        for attempt in range(10):
            try:
                pin_input = await page.query_selector(google_pin_selector)
                if pin_input:
                    break
                logger.debug(f"[自动登录] 等待输入框加载... ({attempt + 1}/10)")
                await asyncio.sleep(0.5)
            except Exception:
                await asyncio.sleep(0.5)
        
        if pin_input:
            logger.info("[自动登录] 找到验证码输入框")
            
            # 点击获取焦点
            await pin_input.click()
            await asyncio.sleep(0.3)
            
            # 清空并输入
            for _ in range(6):
                await page.keyboard.press("Backspace")
            await asyncio.sleep(0.1)
            
            await page.keyboard.type(code)
            logger.info(f"[自动登录] 已输入验证码: {code}")
            
            # 等待并尝试自动提交
            await asyncio.sleep(1)
            submit_selectors = [
                'button[type="submit"]',
                'button:has-text("Next")',
                'button:has-text("Verify")',
            ]
            for submit_selector in submit_selectors:
                try:
                    submit_btn = await page.query_selector(submit_selector)
                    if submit_btn:
                        await submit_btn.click()
                        logger.info("[自动登录] 已点击提交按钮")
                        break
                except Exception:
                    continue
            
            return True
        
        # 回退：尝试普通输入框
        logger.info("[自动登录] 未找到 Google 风格输入框，尝试普通输入框")
        input_selectors = ['input[type="text"]', 'input[name="code"]', 'input[name="pin"]']
        
        for selector in input_selectors:
            try:
                input_element = await page.query_selector(selector)
                if input_element:
                    await input_element.click()
                    await asyncio.sleep(0.1)
                    for _ in range(10):
                        await page.keyboard.press("Backspace")
                    await page.keyboard.type(code)
                    logger.info(f"[自动登录] 已通过 {selector} 输入验证码")
                    return True
            except Exception:
                continue
        
        logger.warning("[自动登录] 未找到验证码输入框")
        return False
        
    except Exception as e:
        logger.error(f"[自动登录] 填充验证码失败: {e}")
        return False
def _is_login_page(url: str) -> bool:
    """判断是否在登录页面（需要输入邮箱）"""
    # 登录页特征：auth.business.gemini.google 或 accounts.google.com
    login_indicators = [
        "auth.business.gemini.google/login",
        "auth.business.gemini.google/account-chooser",
        "accounts.google.com/signin",
        "accounts.google.com/v3/signin",
        "accounts.google.com/ServiceLogin",
    ]
    return any(indicator in url for indicator in login_indicators)


async def _input_email_and_proceed(page, email: str) -> bool:
    """在登录页输入邮箱并点击下一步
    
    auth.business.gemini.google/login 页面会自动获取焦点到邮箱输入框，
    所以可以直接使用键盘输入，无需先查找输入框。
    
    Args:
        page: Playwright 页面对象
        email: 要输入的邮箱地址
        
    Returns:
        是否成功
    """
    try:
        logger.info(f"[自动登录] 准备输入邮箱: {email}")
        
        # 等待页面完全加载
        await asyncio.sleep(2)
        
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        
        # 再等待一下让页面稳定（确保输入框获取焦点）
        await asyncio.sleep(1)
        
        # 直接使用键盘输入（页面会自动获取焦点到输入框）
        logger.info(f"[自动登录] 直接键盘输入邮箱: {email}")
        await page.keyboard.type(email, delay=50)  # 模拟人类输入速度
        
        await asyncio.sleep(0.5)
        
        # 按回车键提交
        logger.info("[自动登录] 按回车键提交")
        await page.keyboard.press("Enter")
        
        # 等待页面跳转
        await asyncio.sleep(3)
        
        logger.info("[自动登录] 邮箱已输入并提交")
        return True
        
    except Exception as e:
        logger.error(f"[自动登录] 输入邮箱失败: {e}")
        return False


def _is_verification_page(url: str) -> bool:
    """判断是否在验证码页面"""
    return any(indicator in url for indicator in VERIFICATION_INDICATORS)


def _is_main_page(url: str) -> bool:
    """判断是否已到达主页面（登录成功）
    
    登录成功的标志：
    1. 到达 /home/ 主页
    2. 到达 /auth/setocookie 中间页（带有 csesidx 参数表示认证成功）
    """
    if "business.gemini.google" not in url:
        return False
    
    # 排除登录相关页面
    for host in LOGIN_HOSTS:
        if host in url:
            return False
    
    # 检查是否是主页或 setocookie 中间页（登录成功标志）
    if "/home/" in url:
        return True
    
    # setocookie 页面带有 csesidx 参数表示认证成功
    if "/auth/setocookie" in url and "csesidx" in url:
        return True
    
    return False


async def _extract_and_save_cookies(context, current_url: str, captured_csesidx: str = None) -> dict:
    """提取并保存 Cookie

    Args:
        context: Playwright 上下文对象
        current_url: 当前页面 URL
        captured_csesidx: 在登录过程中捕获的 csesidx（可选，优先使用）

    Returns:
        {"success": bool, "message": str, "needs_manual_login": bool}
    """
    try:
        logger.info(f"[自动登录] _extract_and_save_cookies 被调用")
        logger.info(f"[自动登录]   current_url: {current_url}")
        logger.info(f"[自动登录]   captured_csesidx: {captured_csesidx}")

        # 提取 csesidx：优先使用传入的 captured_csesidx，其次从 URL 提取
        csesidx = captured_csesidx
        if not csesidx and "csesidx=" in current_url:
            csesidx = current_url.split("csesidx=", 1)[1].split("&", 1)[0]
            logger.info(f"[自动登录]   从 URL 提取到 csesidx: {csesidx}")

        # 如果仍然没有 csesidx，尝试通过浏览器的 fetch API 获取 list-sessions
        if not csesidx:
            logger.info("[自动登录] 未捕获到 csesidx，尝试从 list-sessions 获取...")
            try:
                page = await context.new_page()
                try:
                    # 先访问目标域，确保 Cookie 生效
                    await page.goto("https://business.gemini.google/", timeout=30000, wait_until="domcontentloaded")

                    # 使用 JavaScript fetch 获取 list-sessions
                    result = await page.evaluate("""
                        async () => {
                            try {
                                const resp = await fetch('https://auth.business.gemini.google/list-sessions?rt=json', {
                                    credentials: 'include'
                                });
                                const text = await resp.text();
                                return { ok: resp.ok, status: resp.status, text: text };
                            } catch (e) {
                                return { error: e.message };
                            }
                        }
                    """)

                    if result.get("ok"):
                        text = result.get("text", "")
                        # 解析 JSON（可能有前缀）
                        import json
                        if ")]}'\\n" in text:
                            text = text.split(")]}'\\n", 1)[1]
                        elif ")]}'" in text:
                            text = text.split(")]}'", 1)[1]
                        try:
                            data = json.loads(text.strip())
                            sessions = data.get("sessions", [])
                            if sessions:
                                csesidx = str(sessions[0].get("csesidx", ""))
                                logger.info(f"[自动登录] 从 list-sessions 获取到 csesidx: {csesidx}")
                        except json.JSONDecodeError as e:
                            logger.warning(f"[自动登录] 解析 list-sessions 响应失败: {e}, text={text[:200]}")
                    else:
                        logger.warning(f"[自动登录] list-sessions 请求失败: status={result.get('status')}, error={result.get('error')}")
                finally:
                    await page.close()
            except Exception as e:
                logger.warning(f"[自动登录] 从 list-sessions 获取 csesidx 失败: {e}")

        if csesidx:
            logger.info(f"[自动登录] 使用 csesidx: {csesidx}")
        else:
            logger.warning("[自动登录] 未能获取 csesidx，Cookie 可能无法正常使用")

        # 关键步骤：在浏览器中访问 getoxsrf 端点，触发 Cookie 刷新
        # 这样可以确保 Cookie 被 Google 服务器正确刷新，避免后续 httpx 请求时返回 302
        if csesidx:
            try:
                logger.info("[自动登录] 在浏览器中访问 getoxsrf 端点以刷新 Cookie...")
                page = await context.new_page()
                try:
                    getoxsrf_url = f"https://business.gemini.google/auth/getoxsrf?csesidx={csesidx}"
                    await page.goto(getoxsrf_url, timeout=30000, wait_until="domcontentloaded")
                    await asyncio.sleep(1)  # 等待 Cookie 刷新完成
                    logger.info("[自动登录] getoxsrf 访问完成")
                finally:
                    await page.close()
            except Exception as e:
                logger.warning(f"[自动登录] 访问 getoxsrf 失败（可忽略）: {e}")

        # 重新获取 Cookie（可能已被 getoxsrf 更新）
        browser_cookies = await context.cookies()
        new_secure_c_ses = None
        new_host_c_oses = None
        new_nid = None

        # 收集所有相关 Cookie
        cookie_map = {}
        target_domains = ["auth.business.gemini.google", "business.gemini.google", ".business.gemini.google"]

        for cookie in browser_cookies:
            cookie_domain = cookie.get("domain", "")
            is_target_domain = any(
                cookie_domain == d or cookie_domain.endswith(d)
                for d in target_domains
            )
            if is_target_domain:
                cookie_map[cookie['name']] = cookie['value']

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

        # 提取 group_id
        group_id = None
        if "/cid/" in current_url:
            after = current_url.split("/cid/", 1)[1]
            for sep in ("/", "?", "#"):
                after = after.split(sep, 1)[0]
            group_id = after

        # 构造 cookie_raw
        gemini_cookies = [f"{k}={v}" for k, v in cookie_map.items()]
        cookie_raw = "; ".join(gemini_cookies) if gemini_cookies else None

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
        if group_id:
            new_config["group_id"] = group_id
        if cookie_raw:
            new_config["cookie_raw"] = cookie_raw

        save_config(new_config)
        on_cookie_refreshed()
        mark_cookie_valid()

        # 尝试获取 username 并保存（供自动登录使用）
        # 注意：这里不再调用 check_session_status，因为它可能触发 getoxsrf 请求
        # 而我们已经在浏览器中完成了 Cookie 刷新
        logger.info(f"[自动登录] ✓ Cookie 刷新成功，csesidx={csesidx}, group_id={group_id}")
        return {
            "success": True,
            "message": "Cookie 刷新成功",
            "needs_manual_login": False,
        }
        
    except Exception as e:
        logger.error(f"[自动登录] 提取 Cookie 失败: {e}")
        return {
            "success": False,
            "message": str(e),
            "needs_manual_login": False,
        }


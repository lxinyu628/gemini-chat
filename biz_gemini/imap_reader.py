"""IMAP 邮件读取模块 - 用于自动获取验证码"""
import asyncio
import email
import imaplib
import re
import time
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Callable, Optional

from .config import load_config
from .logger import get_logger

logger = get_logger("imap_reader")


class IMAPReader:
    """IMAP 邮件读取器"""

    def __init__(self, config: dict):
        """初始化读取器

        Args:
            config: IMAP 配置字典，包含 host, port, user, password 等
        """
        self.host = config.get("host", "")
        self.port = config.get("port", 993)
        self.user = config.get("user", "")
        self.password = config.get("password", "")
        self.use_ssl = config.get("use_ssl", True)
        self.folder = config.get("folder", "INBOX")
        self.sender_filter = config.get("sender_filter", "noreply-googlecloud@google.com")
        self.code_pattern = config.get(
            "code_pattern",
            r'class="x_verification-code">([A-Z0-9]{6})</span>'
        )
        self.max_age_seconds = config.get("max_age_seconds", 300)
        self.timeout_seconds = config.get("timeout_seconds", 180)
        self.poll_interval = config.get("poll_interval", 5)

        self._connection: Optional[imaplib.IMAP4_SSL] = None

    async def connect(self) -> bool:
        """连接到 IMAP 服务器

        Returns:
            是否连接成功
        """
        if not self.host or not self.user or not self.password:
            logger.error("IMAP 配置不完整: 缺少 host、user 或 password")
            return False

        try:
            logger.info(f"正在连接 IMAP 服务器: {self.host}:{self.port}")

            # IMAP 操作是同步的，使用线程池执行
            loop = asyncio.get_event_loop()
            self._connection = await loop.run_in_executor(
                None,
                self._connect_sync
            )

            if self._connection:
                logger.info("IMAP 连接成功")
                return True
            return False

        except Exception as e:
            logger.error(f"IMAP 连接失败: {e}")
            return False

    def _connect_sync(self) -> Optional[imaplib.IMAP4_SSL]:
        """同步连接 IMAP（在线程池中执行）"""
        try:
            if self.use_ssl:
                conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
            else:
                conn = imaplib.IMAP4(self.host, self.port)
                conn.starttls()

            conn.login(self.user, self.password)
            conn.select(self.folder)
            return conn

        except imaplib.IMAP4.error as e:
            logger.error(f"IMAP 认证失败: {e}")
            return None
        except Exception as e:
            logger.error(f"IMAP 连接异常: {e}")
            return None

    async def close(self):
        """关闭 IMAP 连接"""
        if self._connection:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._close_sync)
            except Exception as e:
                logger.debug(f"关闭 IMAP 连接时出错: {e}")
            finally:
                self._connection = None

    def _close_sync(self):
        """同步关闭连接"""
        if self._connection:
            try:
                self._connection.close()
                self._connection.logout()
            except Exception:
                pass

    async def fetch_verification_code(
        self,
        max_age_seconds: int = None,
    ) -> Optional[str]:
        """获取最新的验证码

        Args:
            max_age_seconds: 最多查找多久前的邮件（秒）

        Returns:
            验证码字符串，如果未找到返回 None
        """
        if not self._connection:
            logger.error("IMAP 未连接")
            return None

        max_age = max_age_seconds or self.max_age_seconds

        try:
            loop = asyncio.get_event_loop()
            code = await loop.run_in_executor(
                None,
                self._fetch_code_sync,
                max_age
            )
            return code

        except Exception as e:
            logger.error(f"获取验证码失败: {e}")
            return None

    def _fetch_code_sync(self, max_age_seconds: int) -> Optional[str]:
        """同步获取验证码（在线程池中执行）"""
        try:
            # 刷新邮箱以获取最新邮件
            self._connection.noop()

            # 计算搜索时间范围
            since_date = (datetime.now() - timedelta(seconds=max_age_seconds)).strftime("%d-%b-%Y")

            # 构建搜索条件
            search_criteria = f'(FROM "{self.sender_filter}" SINCE "{since_date}")'
            logger.debug(f"IMAP 搜索条件: {search_criteria}")

            status, messages = self._connection.search(None, search_criteria)

            if status != "OK" or not messages[0]:
                logger.debug("未找到符合条件的邮件")
                return None

            # 获取邮件 ID 列表（最新的在后面）
            mail_ids = messages[0].split()
            logger.debug(f"找到 {len(mail_ids)} 封符合条件的邮件")

            # 从最新的邮件开始查找
            for mail_id in reversed(mail_ids):
                code = self._extract_code_from_mail(mail_id)
                if code:
                    return code

            return None

        except Exception as e:
            logger.error(f"搜索邮件失败: {e}")
            return None

    def _extract_code_from_mail(self, mail_id: bytes) -> Optional[str]:
        """从邮件中提取验证码"""
        try:
            status, msg_data = self._connection.fetch(mail_id, "(RFC822)")
            if status != "OK":
                return None

            email_body = msg_data[0][1]
            msg = email.message_from_bytes(email_body)

            # 获取邮件正文
            body = self._get_email_body(msg)
            if not body:
                return None

            # 使用正则表达式提取验证码
            match = re.search(self.code_pattern, body, re.IGNORECASE)
            if match:
                code = match.group(1)
                logger.info(f"从邮件中提取到验证码: {code}")
                return code

            # 备用：尝试匹配任何 6 位字母数字组合
            # 在 HTML 中常见的验证码格式
            backup_patterns = [
                r'verification-code[^>]*>([A-Z0-9]{6})<',
                r'code[^>]*>([A-Z0-9]{6})<',
                r'>([A-Z0-9]{6})</span>',
                r'\b([A-Z0-9]{6})\b',  # 最后尝试纯文本
            ]

            for pattern in backup_patterns:
                match = re.search(pattern, body, re.IGNORECASE)
                if match:
                    code = match.group(1).upper()
                    logger.info(f"使用备用模式提取到验证码: {code}")
                    return code

            return None

        except Exception as e:
            logger.debug(f"解析邮件失败: {e}")
            return None

    def _get_email_body(self, msg) -> str:
        """获取邮件正文（支持多种格式）"""
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/html":
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="ignore")
                        break
                    except Exception:
                        continue
                elif content_type == "text/plain" and not body:
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="ignore")
                    except Exception:
                        continue
        else:
            try:
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="ignore")
            except Exception:
                pass

        return body

    async def fetch_verification_code_with_retry(
        self,
        timeout_seconds: int = None,
        poll_interval: float = None,
        max_age_seconds: int = None,
        status_callback: Callable[[str], None] = None,
    ) -> Optional[str]:
        """带重试机制获取验证码

        在 timeout_seconds 时间内持续轮询邮箱，直到获取到验证码或超时

        Args:
            timeout_seconds: 总超时时间（秒），默认 180（3 分钟）
            poll_interval: 轮询间隔（秒），默认 5
            max_age_seconds: 只查找多久前的邮件（秒），默认 300
            status_callback: 状态回调函数，用于更新 UI

        Returns:
            验证码字符串，如果超时返回 None
        """
        timeout = timeout_seconds or self.timeout_seconds
        interval = poll_interval or self.poll_interval
        max_age = max_age_seconds or self.max_age_seconds

        start_time = time.time()
        attempt = 0

        logger.info(f"开始等待验证码邮件，超时时间: {timeout} 秒")

        while time.time() - start_time < timeout:
            attempt += 1
            elapsed = int(time.time() - start_time)
            remaining = timeout - elapsed

            if status_callback:
                try:
                    status_callback(f"正在等待验证码邮件... (剩余 {remaining} 秒)")
                except Exception:
                    pass

            logger.debug(f"第 {attempt} 次尝试获取验证码，剩余 {remaining} 秒")

            code = await self.fetch_verification_code(max_age_seconds=max_age)
            if code:
                if status_callback:
                    try:
                        status_callback(f"已收到验证码 {code}，正在填充...")
                    except Exception:
                        pass
                return code

            # 等待下一次轮询
            await asyncio.sleep(interval)

        logger.warning("等待验证码超时")
        if status_callback:
            try:
                status_callback("等待超时，未收到验证码邮件")
            except Exception:
                pass

        return None


async def get_verification_code(
    config: dict = None,
    status_callback: Callable[[str], None] = None,
) -> Optional[str]:
    """便捷函数：从邮箱获取验证码

    Args:
        config: 配置字典，如果为 None 则自动加载
        status_callback: 状态回调函数

    Returns:
        验证码字符串，如果失败返回 None
    """
    if config is None:
        config = load_config()

    imap_config = config.get("imap", {})

    if not imap_config.get("enabled", False):
        logger.debug("IMAP 未启用")
        return None

    reader = IMAPReader(imap_config)

    try:
        if not await reader.connect():
            return None

        code = await reader.fetch_verification_code_with_retry(
            status_callback=status_callback
        )
        return code

    finally:
        await reader.close()


async def test_imap_connection(config: dict = None) -> dict:
    """测试 IMAP 连接

    Args:
        config: IMAP 配置字典

    Returns:
        {
            "success": bool,
            "message": str,
            "recent_emails": int,  # 最近的邮件数量
        }
    """
    if config is None:
        config = load_config().get("imap", {})

    reader = IMAPReader(config)

    try:
        if not await reader.connect():
            return {
                "success": False,
                "message": "连接失败，请检查服务器地址、端口、用户名和密码",
                "recent_emails": 0,
            }

        # 尝试搜索最近的邮件
        code = await reader.fetch_verification_code(max_age_seconds=86400)  # 24 小时

        return {
            "success": True,
            "message": "IMAP 连接成功",
            "recent_emails": 1 if code else 0,
            "sample_code": code,
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"测试失败: {str(e)}",
            "recent_emails": 0,
        }

    finally:
        await reader.close()

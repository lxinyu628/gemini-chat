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
            logger.debug(f"[IMAP] 连接参数: host={self.host}, port={self.port}, ssl={self.use_ssl}, user={self.user}")
            if self.use_ssl:
                conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
            else:
                conn = imaplib.IMAP4(self.host, self.port)
                conn.starttls()

            logger.debug(f"[IMAP] 正在登录用户: {self.user}")
            conn.login(self.user, self.password)
            logger.debug(f"[IMAP] 登录成功，正在选择文件夹: {self.folder}")
            status, data = conn.select(self.folder)
            logger.info(f"[IMAP] 文件夹 '{self.folder}' 已选中，包含 {data[0].decode()} 封邮件")
            return conn

        except imaplib.IMAP4.error as e:
            logger.error(f"[IMAP] 认证失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[IMAP] 连接异常: {e}")
            import traceback
            logger.debug(f"[IMAP] 异常详情:\n{traceback.format_exc()}")
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
            logger.debug("[IMAP] 正在刷新邮箱 (NOOP)...")
            self._connection.noop()

            # 计算搜索时间范围
            since_date = (datetime.now() - timedelta(seconds=max_age_seconds)).strftime("%d-%b-%Y")

            # 构建搜索条件
            search_criteria = f'(FROM "{self.sender_filter}" SINCE "{since_date}")'
            logger.info(f"[IMAP] 搜索条件: {search_criteria}")
            logger.info(f"[IMAP] 搜索范围: 过去 {max_age_seconds} 秒 (自 {since_date})")

            status, messages = self._connection.search(None, search_criteria)
            logger.debug(f"[IMAP] 搜索返回状态: {status}, 结果: {messages}")

            if status != "OK" or not messages[0]:
                logger.info(f"[IMAP] 未找到来自 '{self.sender_filter}' 的邮件")
                # 尝试列出所有邮件的发件人，帮助调试
                self._list_recent_senders()
                return None

            # 获取邮件 ID 列表（最新的在后面）
            mail_ids = messages[0].split()
            logger.info(f"[IMAP] ✓ 找到 {len(mail_ids)} 封符合条件的邮件，ID: {[mid.decode() for mid in mail_ids]}")

            # 从最新的邮件开始查找
            for i, mail_id in enumerate(reversed(mail_ids)):
                logger.debug(f"[IMAP] 正在处理第 {i+1}/{len(mail_ids)} 封邮件 (ID: {mail_id.decode()})...")
                code = self._extract_code_from_mail(mail_id, max_age_seconds)
                if code:
                    return code
                elif code is None:
                    logger.debug(f"[IMAP] 邮件 {mail_id.decode()} 中未找到验证码")
                # code == False 表示邮件太旧，继续检查下一封

            logger.warning("[IMAP] 已检查所有邮件，均未找到验证码")
            return None

        except Exception as e:
            logger.error(f"[IMAP] 搜索邮件失败: {e}")
            import traceback
            logger.debug(f"[IMAP] 异常详情:\n{traceback.format_exc()}")
            return None

    def _list_recent_senders(self, limit: int = 10):
        """列出最近邮件的发件人（用于调试）"""
        try:
            since_date = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
            status, messages = self._connection.search(None, f'(SINCE "{since_date}")')
            if status == "OK" and messages[0]:
                mail_ids = messages[0].split()[-limit:]  # 只取最近的几封
                logger.info(f"[IMAP] 最近 {len(mail_ids)} 封邮件的发件人:")
                for mail_id in reversed(mail_ids):
                    try:
                        status, data = self._connection.fetch(mail_id, "(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])")
                        if status == "OK":
                            header = data[0][1].decode('utf-8', errors='ignore')
                            # 提取 From 和 Subject
                            from_match = re.search(r'From:\s*(.+)', header, re.IGNORECASE)
                            subj_match = re.search(r'Subject:\s*(.+)', header, re.IGNORECASE)
                            date_match = re.search(r'Date:\s*(.+)', header, re.IGNORECASE)
                            from_addr = from_match.group(1).strip() if from_match else 'N/A'
                            subject = subj_match.group(1).strip()[:50] if subj_match else 'N/A'
                            date_str = date_match.group(1).strip() if date_match else 'N/A'
                            logger.info(f"  - ID {mail_id.decode()}: FROM={from_addr}")
                            logger.info(f"    SUBJ={subject}, DATE={date_str}")
                    except Exception as e:
                        logger.debug(f"  - 邮件 {mail_id.decode()} 读取失败: {e}")
        except Exception as e:
            logger.debug(f"[IMAP] 列出发件人失败: {e}")

    def _extract_code_from_mail(self, mail_id: bytes, max_age_seconds: int = 300):
        """从邮件中提取验证码
        
        Returns:
            str: 验证码
            None: 未找到验证码
            False: 邮件太旧，跳过
        """
        try:
            status, msg_data = self._connection.fetch(mail_id, "(RFC822)")
            if status != "OK":
                logger.debug(f"[IMAP] 获取邮件 {mail_id.decode()} 失败，状态: {status}")
                return None

            email_body = msg_data[0][1]
            msg = email.message_from_bytes(email_body)

            # 打印邮件基本信息
            subject = self._decode_header(msg.get('Subject', 'N/A'))
            from_addr = self._decode_header(msg.get('From', 'N/A'))
            date_str = msg.get('Date', 'N/A')
            logger.info(f"[IMAP] 正在解析邮件:")
            logger.info(f"  Subject: {subject}")
            logger.info(f"  From: {from_addr}")
            logger.info(f"  Date: {date_str}")

            # 检查邮件时间是否在有效范围内
            mail_time = self._parse_email_date(date_str)
            if mail_time:
                # 使用 UTC 时间比较，避免 naive/aware datetime 混用
                from datetime import timezone
                now = datetime.now(timezone.utc)
                # 确保 mail_time 也是 aware datetime
                if mail_time.tzinfo is None:
                    mail_time = mail_time.replace(tzinfo=timezone.utc)
                age_seconds = (now - mail_time).total_seconds()
                logger.info(f"  Age: {int(age_seconds)} 秒前")
                if age_seconds > max_age_seconds:
                    logger.info(f"[IMAP] ⚠ 邮件太旧（{int(age_seconds)}秒 > {max_age_seconds}秒），跳过")
                    return False  # 返回 False 表示太旧
            else:
                logger.warning(f"[IMAP] 无法解析邮件时间: {date_str}")

            # 获取邮件正文
            body = self._get_email_body(msg)
            if not body:
                logger.warning(f"[IMAP] 邮件正文为空")
                return None

            # 打印正文预览（用于调试）
            body_preview = body[:500].replace('\n', ' ').replace('\r', '')
            logger.debug(f"[IMAP] 邮件正文预览 (前 500 字符): {body_preview}")
            logger.debug(f"[IMAP] 邮件正文总长度: {len(body)} 字符")

            # 使用正则表达式提取验证码
            logger.debug(f"[IMAP] 使用主正则匹配: {self.code_pattern}")
            match = re.search(self.code_pattern, body, re.IGNORECASE)
            if match:
                code = match.group(1)
                logger.info(f"[IMAP] ✓ 主正则匹配成功，验证码: {code}")
                return code
            else:
                logger.debug("[IMAP] 主正则未匹配")

            # 增强：参考 auto_login_with_email.py 的行级匹配逻辑
            # 先按行精确匹配提示语，避免误匹配
            lines = body.splitlines()
            for line in lines:
                line_lower = line.lower()
                idx = -1

                # 中文提示语
                if "一次性验证码为" in line:
                    idx = line.index("一次性验证码为")
                elif "一次性验证为" in line:  # 处理被截断的情况
                    idx = line.index("一次性验证为")
                elif "验证码为" in line:
                    idx = line.index("验证码为")
                elif "验证为" in line:  # 处理被截断的情况
                    idx = line.index("验证为")
                elif "您的验证码是" in line:
                    idx = line.index("您的验证码是")
                # 英文提示语
                elif "your one-time verification code is" in line_lower:
                    idx = line_lower.index("your one-time verification code is")
                elif "verification code is" in line_lower:
                    idx = line_lower.index("verification code is")
                elif "one-time verification code is" in line_lower:
                    idx = line_lower.index("one-time verification code is")

                if idx >= 0:
                    # 只在提示语之后的子串中查找
                    sub = line[idx:]
                    candidates = re.findall(r'[A-Z0-9]{6}', sub, re.IGNORECASE)
                    if candidates:
                        code = candidates[0].strip().upper()
                        # 要求：长度恰好 6，且至少包含一个字母（避免纯数字 ID 被误匹配）
                        if len(code) == 6 and any(c.isalpha() for c in code):
                            logger.info(f"[IMAP] ✓ 行级匹配到验证码: {code} (来源行: {line.strip()[:80]}...)")
                            return code

            # 备用：全局模式匹配（参考 auto_login_with_email.py）
            global_patterns = [
                # 中文模式
                (r'一次性验证码为[：:]\s*([A-Z0-9]{6})', '中文-一次性验证码为'),
                (r'一次性验证为[：:]\s*([A-Z0-9]{6})', '中文-一次性验证为'),
                (r'验证码为[：:]\s*([A-Z0-9]{6})', '中文-验证码为'),
                (r'验证为[：:]\s*([A-Z0-9]{6})', '中文-验证为'),
                (r'验证码[：:是]\s*([A-Z0-9]{6})', '中文-验证码'),
                (r'您的验证码是[：:]\s*([A-Z0-9]{6})', '中文-您的验证码是'),
                # 英文模式
                (r'your one-time verification code is[：:]\s*([A-Z0-9]{6})', '英文-one-time code'),
                (r'one-time verification code is[：:]\s*([A-Z0-9]{6})', '英文-one-time'),
                (r'verification code is[：:]\s*([A-Z0-9]{6})', '英文-verification code'),
                (r'code is[：:]\s*([A-Z0-9]{6})', '英文-code is'),
            ]

            logger.debug("[IMAP] 尝试全局模式匹配...")
            for pattern, desc in global_patterns:
                match = re.search(pattern, body, re.IGNORECASE)
                if match:
                    code = match.group(1).strip().upper()
                    if len(code) == 6 and any(c.isalpha() for c in code):
                        logger.info(f"[IMAP] ✓ 全局模式 [{desc}] 匹配成功，验证码: {code}")
                        return code

            # 备用：尝试匹配 HTML 中常见的验证码格式
            backup_patterns = [
                (r'verification-code[^>]*>([A-Z0-9]{6})<', 'verification-code'),
                (r'code[^>]*>([A-Z0-9]{6})<', 'code tag'),
                (r'>([A-Z0-9]{6})</span>', 'span tag'),
                (r'>\s*([A-Z0-9]{6})\s*</td>', 'td tag'),
                (r'>\s*([A-Z0-9]{6})\s*</div>', 'div tag'),
            ]

            logger.debug("[IMAP] 尝试 HTML 标签模式匹配...")
            for pattern, desc in backup_patterns:
                match = re.search(pattern, body, re.IGNORECASE)
                if match:
                    code = match.group(1).upper()
                    # 验证码需要至少包含一个字母
                    if any(c.isalpha() for c in code):
                        logger.info(f"[IMAP] ✓ HTML 模式 [{desc}] 匹配成功，验证码: {code}")
                        return code
                    else:
                        logger.debug(f"[IMAP]   模式 [{desc}] 匹配到 {code}，但不含字母，跳过")

            logger.debug("[IMAP] 所有正则模式均未匹配到验证码")
            return None

        except Exception as e:
            logger.error(f"[IMAP] 解析邮件失败: {e}")
            import traceback
            logger.debug(f"[IMAP] 异常详情:\n{traceback.format_exc()}")
            return None

    def _decode_header(self, header_value: str) -> str:
        """解码邮件头"""
        if not header_value:
            return 'N/A'
        try:
            decoded_parts = decode_header(header_value)
            result = ''
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    result += part.decode(charset or 'utf-8', errors='ignore')
                else:
                    result += part
            return result
        except Exception:
            return str(header_value)

    def _parse_email_date(self, date_str: str) -> Optional[datetime]:
        """解析邮件日期字符串，返回带时区的 datetime"""
        if not date_str or date_str == 'N/A':
            return None
        
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(date_str)
        except Exception:
            pass
        
        # 备用格式解析
        from datetime import timezone
        date_formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S",
            "%d %b %Y %H:%M:%S",
        ]
        
        # 移除时区括号部分 (e.g., "(CST)")
        clean_date = re.sub(r'\s*\([^)]+\)\s*$', '', date_str)
        
        for fmt in date_formats:
            try:
                dt = datetime.strptime(clean_date, fmt)
                # 如果没有时区信息，假设为 UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        
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
                    # 支持同步和异步回调（asyncio 已在模块顶部导入）
                    result = status_callback(f"正在等待验证码邮件... (剩余 {remaining} 秒)")
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

            logger.debug(f"第 {attempt} 次尝试获取验证码，剩余 {remaining} 秒")

            code = await self.fetch_verification_code(max_age_seconds=max_age)
            if code:
                if status_callback:
                    try:
                        result = status_callback(f"已收到验证码 {code}，正在填充...")
                        if asyncio.iscoroutine(result):
                            await result
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

"""自定义异常类。

该模块定义了 Gemini Chat 项目中使用的所有自定义异常，
用于提供更精确的错误处理和更清晰的错误信息。
"""


class GeminiError(Exception):
    """Gemini 客户端基础异常类。

    所有 Gemini 相关异常的基类，可用于捕获所有 Gemini 错误。

    Attributes:
        message: 错误描述信息。
        details: 额外的错误详情（可选）。
    """

    def __init__(self, message: str, details: dict = None):
        """初始化异常。

        Args:
            message: 错误描述信息。
            details: 额外的错误详情字典。
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """返回异常的字符串表示。"""
        if self.details:
            return f"{self.message} (详情: {self.details})"
        return self.message


class AuthenticationError(GeminiError):
    """认证失败异常。

    当 Cookie 无效、JWT 生成失败或其他认证相关错误时抛出。
    """

    pass


class SessionExpiredError(AuthenticationError):
    """会话过期异常。

    当 Google 会话（Cookie）已过期，需要重新登录时抛出。
    通常需要用户手动重新登录来解决。
    """

    pass


class TokenRefreshError(AuthenticationError):
    """令牌刷新失败异常。

    当 JWT 令牌刷新失败时抛出。
    可能是因为 Cookie 无效或网络问题。
    """

    pass


class RateLimitError(GeminiError):
    """速率限制异常。

    当请求频率超过限制（429 错误）时抛出。

    Attributes:
        retry_after: 建议的重试等待时间（秒）。
    """

    def __init__(self, message: str, retry_after: float = None, details: dict = None):
        """初始化速率限制异常。

        Args:
            message: 错误描述信息。
            retry_after: 建议的重试等待时间（秒）。
            details: 额外的错误详情。
        """
        super().__init__(message, details)
        self.retry_after = retry_after

    def __str__(self) -> str:
        """返回异常的字符串表示。"""
        base = super().__str__()
        if self.retry_after:
            return f"{base} (建议 {self.retry_after:.1f} 秒后重试)"
        return base


class ConfigurationError(GeminiError):
    """配置错误异常。

    当配置文件无效、缺少必要配置项或配置格式错误时抛出。
    """

    pass


class SessionError(GeminiError):
    """会话操作异常。

    当会话创建、删除或获取失败时抛出。
    """

    pass


class SessionNotFoundError(SessionError):
    """会话不存在异常。

    当请求的会话 ID 不存在时抛出。
    """

    pass


class APIError(GeminiError):
    """API 调用异常。

    当 Gemini API 返回错误响应时抛出。

    Attributes:
        status_code: HTTP 状态码。
        error_type: API 返回的错误类型。
    """

    def __init__(
        self,
        message: str,
        status_code: int = None,
        error_type: str = None,
        details: dict = None,
    ):
        """初始化 API 异常。

        Args:
            message: 错误描述信息。
            status_code: HTTP 状态码。
            error_type: API 返回的错误类型。
            details: 额外的错误详情。
        """
        super().__init__(message, details)
        self.status_code = status_code
        self.error_type = error_type

    def __str__(self) -> str:
        """返回异常的字符串表示。"""
        parts = [self.message]
        if self.status_code:
            parts.append(f"HTTP {self.status_code}")
        if self.error_type:
            parts.append(f"类型: {self.error_type}")
        return " | ".join(parts)


class PolicyViolationError(APIError):
    """策略违规异常。

    当请求内容违反 Google 或组织的安全策略时抛出。

    Attributes:
        violation_type: 违规类型。
    """

    def __init__(
        self,
        message: str,
        violation_type: str = None,
        status_code: int = None,
        details: dict = None,
    ):
        """初始化策略违规异常。

        Args:
            message: 错误描述信息。
            violation_type: 违规类型。
            status_code: HTTP 状态码。
            details: 额外的错误详情。
        """
        super().__init__(message, status_code, "POLICY_VIOLATION", details)
        self.violation_type = violation_type


class FileOperationError(GeminiError):
    """文件操作异常。

    当文件上传、下载或处理失败时抛出。
    """

    pass


class ImageDownloadError(FileOperationError):
    """图片下载异常。

    当图片下载失败时抛出。
    """

    pass


class BrowserError(GeminiError):
    """浏览器操作异常。

    当 Playwright 浏览器操作失败时抛出。
    用于登录、Cookie 刷新等场景。
    """

    pass


class LoginError(BrowserError):
    """登录失败异常。

    当浏览器登录流程失败时抛出。
    """

    pass


class CookieRefreshError(BrowserError):
    """Cookie 刷新异常。

    当浏览器自动刷新 Cookie 失败时抛出。
    """

    pass


class RedisError(GeminiError):
    """Redis 操作异常。

    当 Redis 连接或操作失败时抛出。
    """

    pass


class IMAPError(GeminiError):
    """IMAP 操作异常。

    当 IMAP 邮件读取失败时抛出。
    用于验证码自动获取场景。
    """

    pass


class VerificationCodeError(IMAPError):
    """验证码获取异常。

    当无法从邮件中获取验证码时抛出。
    """

    pass

"""Business Gemini 客户端模块。

提供 Google Business Gemini API 的完整封装，包括：
- 认证管理（JWT 生成、Cookie 管理）
- API 客户端（会话管理、消息发送、文件上传）
- OpenAI/Anthropic 兼容适配器
- 配置管理
- 日志系统

基本用法::

    from biz_gemini import BizGeminiClient, JWTManager, load_config

    config = load_config()
    jwt_manager = JWTManager(config)
    client = BizGeminiClient(config, jwt_manager)

    response = client.chat("你好")
    print(response)

更多信息请参阅项目文档：https://github.com/ccpopy/gemini-chat
"""

# 版本信息
__version__ = "1.0.0"
__author__ = "ccpopy"

# 认证相关
from .auth import (
    JWTManager,
    check_session_status,
    ensure_jwt_valid,
    create_jwt,
    decode_xsrf_token,
    on_cookie_refreshed,
)

# 客户端相关
from .biz_client import (
    BizGeminiClient,
    ChatResponse,
    ChatImage,
    ImageThumbnail,
    build_headers,
)

# 配置相关
from .config import (
    load_config,
    save_config,
    reload_config,
    get_proxy,
    DEFAULT_CONFIG,
)

# 适配器
from .openai_adapter import OpenAICompatClient
from .anthropic_adapter import AnthropicCompatClient

# 异常类
from .exceptions import (
    GeminiError,
    AuthenticationError,
    SessionExpiredError,
    TokenRefreshError,
    RateLimitError,
    ConfigurationError,
    SessionError,
    SessionNotFoundError,
    APIError,
    PolicyViolationError,
    FileOperationError,
    ImageDownloadError,
    BrowserError,
    LoginError,
    CookieRefreshError,
    RedisError,
    IMAPError,
    VerificationCodeError,
)

# 日志相关
from .logger import get_logger, setup_logger, logger

# API Key 管理
from .api_keys import (
    generate_api_key,
    list_api_keys,
    get_api_key_by_id,
    validate_api_key,
    delete_api_key,
)

# 导出列表
__all__ = [
    # 版本信息
    "__version__",
    "__author__",
    # 认证
    "JWTManager",
    "check_session_status",
    "ensure_jwt_valid",
    "create_jwt",
    "decode_xsrf_token",
    "on_cookie_refreshed",
    # 客户端
    "BizGeminiClient",
    "ChatResponse",
    "ChatImage",
    "ImageThumbnail",
    "build_headers",
    # 配置
    "load_config",
    "save_config",
    "reload_config",
    "get_proxy",
    "DEFAULT_CONFIG",
    # 适配器
    "OpenAICompatClient",
    "AnthropicCompatClient",
    # 异常
    "GeminiError",
    "AuthenticationError",
    "SessionExpiredError",
    "TokenRefreshError",
    "RateLimitError",
    "ConfigurationError",
    "SessionError",
    "SessionNotFoundError",
    "APIError",
    "PolicyViolationError",
    "FileOperationError",
    "ImageDownloadError",
    "BrowserError",
    "LoginError",
    "CookieRefreshError",
    "RedisError",
    "IMAPError",
    "VerificationCodeError",
    # 日志
    "get_logger",
    "setup_logger",
    "logger",
    # API Key
    "generate_api_key",
    "list_api_keys",
    "get_api_key_by_id",
    "validate_api_key",
    "delete_api_key",
]

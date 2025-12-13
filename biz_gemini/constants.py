"""常量定义模块。

集中管理项目中使用的所有常量，包括 API 端点、默认配置、时间常量等。
"""

# =============================================================================
# API 端点
# =============================================================================

# Gemini Business API 基础 URL
GEMINI_API_BASE_URL = "https://biz-discoveryengine.googleapis.com/v1alpha/locations/global"

# Gemini Business API 端点
CREATE_SESSION_URL = f"{GEMINI_API_BASE_URL}/widgetCreateSession"
STREAM_ASSIST_URL = f"{GEMINI_API_BASE_URL}/widgetStreamAssist"
LIST_FILE_METADATA_URL = f"{GEMINI_API_BASE_URL}/widgetListSessionFileMetadata"
DELETE_SESSION_URL = f"{GEMINI_API_BASE_URL}/widgetDeleteSession"
LIST_SESSIONS_URL = f"{GEMINI_API_BASE_URL}/widgetListSessions"
GET_SESSION_URL = f"{GEMINI_API_BASE_URL}/widgetGetSession"
ADD_CONTEXT_FILE_URL = f"{GEMINI_API_BASE_URL}/widgetAddContextFile"

# 认证相关端点
AUTH_BASE_URL = "https://business.gemini.google"
GETOXSRF_URL = f"{AUTH_BASE_URL}/auth/getoxsrf"
AUTH_LIST_SESSIONS_URL = "https://auth.business.gemini.google/list-sessions"

# =============================================================================
# 时间常量
# =============================================================================

# 时间格式字符串
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
TIME_FORMAT_ISO = "%Y-%m-%dT%H:%M:%SZ"

# JWT 相关
JWT_DEFAULT_LIFETIME = 300  # JWT 默认有效期（秒）：5 分钟
JWT_REFRESH_THRESHOLD = 240  # JWT 刷新阈值（秒）：4 分钟
JWT_REFRESH_BUFFER = 60  # JWT 刷新缓冲时间（秒）：提前 1 分钟刷新

# Cookie 相关
COOKIE_MAX_AGE_HOURS = 24  # Cookie 最大有效时间（小时）
COOKIE_WARNING_HOURS = 20  # Cookie 警告时间（小时）

# 保活相关
KEEP_ALIVE_DEFAULT_INTERVAL = 60  # 默认保活间隔（分钟）
KEEP_ALIVE_MIN_INTERVAL = 10  # 最小保活间隔（分钟）
KEEP_ALIVE_MAX_INTERVAL = 120  # 最大保活间隔（分钟）

# 冷却时间
COOLDOWN_LOGIN_FAILURE = 60  # 登录失败冷却时间（秒）
COOLDOWN_RATE_LIMIT = 60  # 速率限制冷却时间（秒）
COOLDOWN_MAX = 3600  # 最大冷却时间（秒）

# =============================================================================
# 网络常量
# =============================================================================

# HTTP 超时
DEFAULT_TIMEOUT = 30  # 默认超时（秒）
STREAM_TIMEOUT = 120  # 流式请求超时（秒）
DOWNLOAD_TIMEOUT = 120  # 下载超时（秒）
LOGIN_TIMEOUT = 300  # 登录超时（秒）：5 分钟

# 重试
MAX_RETRY_ATTEMPTS = 3  # 最大重试次数
RETRY_DELAY = 1  # 重试延迟（秒）
RETRY_BACKOFF_FACTOR = 2  # 重试退避因子

# =============================================================================
# 速率限制
# =============================================================================

# 默认速率限制配置
RATE_LIMIT_DEFAULT_MAX_REQUESTS = 10  # 默认最大请求数
RATE_LIMIT_DEFAULT_WINDOW = 60  # 默认时间窗口（秒）

# =============================================================================
# 文件和目录
# =============================================================================

# 配置文件名
CONFIG_FILE_NAME = "config.json"
CONFIG_EXAMPLE_FILE_NAME = "config.example.json"
OLD_CONFIG_FILE_NAME = "business_gemini_session.json"

# 数据目录
DATA_DIR_NAME = "data"
IMAGES_DIR_NAME = "biz_gemini_images"
LOG_DIR_NAME = "log"

# 数据库文件
API_KEYS_DB_NAME = "api_keys.db"

# =============================================================================
# API Key 相关
# =============================================================================

# API Key 前缀
API_KEY_PREFIX = "sk-"

# API Key 随机部分长度
API_KEY_RANDOM_LENGTH = 48

# =============================================================================
# HTTP 头
# =============================================================================

# 默认 User-Agent
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

# 浏览器指纹头
BROWSER_HEADERS = {
    "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}

# =============================================================================
# Cookie 名称
# =============================================================================

COOKIE_SECURE_C_SES = "__Secure-C_SES"
COOKIE_HOST_C_OSES = "__Host-C_OSES"
COOKIE_NID = "NID"

# =============================================================================
# 模型相关
# =============================================================================

# 默认模型 ID
DEFAULT_MODEL_ID = "auto"

# 支持的模型列表（用于 /v1/models 端点）
SUPPORTED_MODELS = [
    {
        "id": "auto",
        "object": "model",
        "owned_by": "google",
        "created": 1700000000,
    },
    {
        "id": "gemini-2.0-flash-thinking-exp",
        "object": "model",
        "owned_by": "google",
        "created": 1700000000,
    },
    {
        "id": "gemini-2.5-pro",
        "object": "model",
        "owned_by": "google",
        "created": 1700000000,
    },
]

# =============================================================================
# Redis 相关
# =============================================================================

# 默认 Redis 配置
REDIS_DEFAULT_HOST = "127.0.0.1"
REDIS_DEFAULT_PORT = 6379
REDIS_DEFAULT_DB = 0
REDIS_DEFAULT_KEY_PREFIX = "gemini_chat:"

# Redis key 过期时间
REDIS_SESSION_TTL = 3600  # 会话缓存过期时间（秒）：1 小时
REDIS_JWT_TTL = 360  # JWT 缓存过期时间（秒）：6 分钟
REDIS_LOCK_TTL = 120  # 分布式锁过期时间（秒）：2 分钟

# =============================================================================
# 服务器默认配置
# =============================================================================

SERVER_DEFAULT_HOST = "0.0.0.0"
SERVER_DEFAULT_PORT = 8000
SERVER_DEFAULT_WORKERS = 4
SERVER_DEFAULT_LOG_LEVEL = "INFO"

# =============================================================================
# IMAP 默认配置
# =============================================================================

IMAP_DEFAULT_PORT = 993
IMAP_DEFAULT_FOLDER = "INBOX"
IMAP_DEFAULT_TIMEOUT = 180  # 等待验证码超时（秒）
IMAP_DEFAULT_POLL_INTERVAL = 5  # 轮询间隔（秒）
IMAP_DEFAULT_MAX_AGE = 300  # 邮件最大年龄（秒）

# Google 验证码邮件发件人
GOOGLE_VERIFICATION_SENDER = "noreply-googlecloud@google.com"

# 验证码正则表达式
VERIFICATION_CODE_PATTERN = r'class="x_verification-code">([A-Z0-9]{6})</span>'

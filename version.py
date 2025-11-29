"""版本信息管理模块"""

# 版本号
VERSION = "1.0.2"

# 版本信息
VERSION_INFO = {
    "version": VERSION,
    "name": "Gemini Chat",
    "description": "Business Gemini API 代理服务",
    "features": [
        "OpenAI 兼容 API (/v1/chat/completions)",
        "Anthropic 兼容 API (/v1/messages)",
        "Web UI 聊天界面",
        "远程浏览器登录",
        "Session 保活服务",
    ],
}

# GitHub 仓库信息，留空则禁用版本检测
GITHUB_REPO = "ccpopy/gemini-chat"


def get_version() -> str:
    """获取当前版本号"""
    return VERSION


def get_version_info() -> dict:
    """获取完整版本信息"""
    return VERSION_INFO.copy()

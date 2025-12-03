"""版本信息管理模块"""

import subprocess

# 默认版本号（当无法从 git 获取时使用）
DEFAULT_VERSION = "1.0.0"


def _get_version_from_git() -> str:
    """从 git tag 获取最新版本号"""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            # 移除 'v' 前缀（如果有）
            if version.startswith("v"):
                version = version[1:]
            return version
    except Exception:
        pass
    return DEFAULT_VERSION


# 版本号
VERSION = _get_version_from_git()

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

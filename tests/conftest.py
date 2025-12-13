"""Pytest 配置和共享 fixtures。"""
import json
import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """创建临时目录用于测试。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config() -> dict:
    """返回用于测试的示例配置。"""
    return {
        "server": {
            "host": "0.0.0.0",
            "port": 8000,
            "workers": 1,
            "log_level": "INFO",
            "reload": False,
        },
        "proxy": {
            "enabled": False,
            "url": "",
            "timeout": 30,
        },
        "session": {
            "secure_c_ses": "test_secure_c_ses_value",
            "host_c_oses": "test_host_c_oses_value",
            "nid": "test_nid_value",
            "csesidx": "test_csesidx_123",
            "group_id": "test-group-id-uuid",
            "project_id": "test-project-id",
            "cookies_saved_at": "2025-01-01 00:00:00",
            "cookie_raw": "",
            "cookie_profile_dir": "",
        },
        "browser_keep_alive": {
            "enabled": False,
            "interval_minutes": 60,
            "headless": True,
        },
        "remote_browser": {
            "headless": True,
        },
        "account_state": {
            "jwt": "",
            "jwt_time": 0,
            "jwt_expires_at": 0,
            "available": True,
            "cookie_expired": False,
            "cooldown_until": 0,
            "cooldown_reason": "",
            "last_refresh_time": 0,
            "conversation_sessions": {},
        },
        "security": {
            "admin_password": "",
            "require_api_key": False,
        },
        "redis": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 6379,
            "password": "",
            "db": 0,
            "key_prefix": "gemini_chat_test:",
        },
        "imap": {
            "enabled": False,
            "host": "",
            "port": 993,
            "user": "",
            "password": "",
            "use_ssl": True,
            "folder": "INBOX",
            "sender_filter": "noreply-googlecloud@google.com",
            "code_pattern": r'class="x_verification-code">([A-Z0-9]{6})</span>',
            "max_age_seconds": 300,
            "timeout_seconds": 180,
            "poll_interval": 5,
            "auto_login": True,
        },
    }


@pytest.fixture
def config_file(temp_dir: Path, sample_config: dict) -> Path:
    """创建临时配置文件。"""
    config_path = temp_dir / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(sample_config, f, ensure_ascii=False, indent=2)
    return config_path


@pytest.fixture
def mock_jwt_response() -> dict:
    """模拟 getoxsrf API 响应。"""
    return {
        "keyId": "test_key_id_123",
        "xsrfToken": "dGVzdF94c3JmX3Rva2VuX2Jhc2U2NF9lbmNvZGVk",  # base64 encoded test token
    }


@pytest.fixture
def mock_session_response() -> dict:
    """模拟创建会话的 API 响应。"""
    return {
        "session": {
            "name": "collections/default_collection/engines/agentspace-engine/sessions/test_session_123",
            "displayName": "test_session",
        }
    }


@pytest.fixture
def mock_chat_response() -> list:
    """模拟聊天 API 的流式响应。"""
    return [
        {
            "streamAssistResponse": {
                "sessionInfo": {
                    "session": "collections/default_collection/engines/agentspace-engine/sessions/test_session_123"
                },
                "answer": {
                    "state": "SUCCEEDED",
                    "replies": [
                        {
                            "groundedContent": {
                                "content": {
                                    "text": "你好！我是 Gemini，很高兴见到你。",
                                    "thought": False,
                                }
                            }
                        }
                    ],
                },
            }
        }
    ]

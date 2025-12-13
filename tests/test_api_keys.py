"""API 密钥模块测试。"""
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from biz_gemini.api_keys import (
    generate_api_key,
    list_api_keys,
    get_api_key_by_id,
    validate_api_key,
    delete_api_key,
    toggle_api_key,
    init_db,
    DB_FILE,
    DATA_DIR,
)


@pytest.fixture
def temp_db(temp_dir):
    """创建临时数据库。"""
    db_path = temp_dir / "data" / "api_keys.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with patch("biz_gemini.api_keys.DB_FILE", db_path):
        with patch("biz_gemini.api_keys.DATA_DIR", temp_dir / "data"):
            # 初始化数据库
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    name TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP,
                    is_active INTEGER DEFAULT 1
                )
            """)
            conn.commit()
            conn.close()

            yield db_path


class TestGenerateApiKey:
    """generate_api_key 函数测试。"""

    def test_key_format(self, temp_db):
        """测试生成的 key 格式。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = generate_api_key(name="Test Key")

        assert result["key"].startswith("sk-")
        assert len(result["key"]) == 51  # "sk-" + 48 characters

    def test_key_uniqueness(self, temp_db):
        """测试 key 唯一性。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result1 = generate_api_key(name="Key 1")
            result2 = generate_api_key(name="Key 2")

        assert result1["key"] != result2["key"]

    def test_returns_full_info(self, temp_db):
        """测试返回完整信息。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = generate_api_key(name="My API Key")

        assert "id" in result
        assert "key" in result
        assert "name" in result
        assert "created_at" in result
        assert "is_active" in result

        assert result["name"] == "My API Key"
        assert result["is_active"] is True

    def test_empty_name(self, temp_db):
        """测试空名称。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = generate_api_key()

        assert result["name"] == ""


class TestListApiKeys:
    """list_api_keys 函数测试。"""

    def test_empty_list(self, temp_db):
        """测试空列表。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = list_api_keys()

        assert result == []

    def test_list_with_keys(self, temp_db):
        """测试有数据时的列表。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            generate_api_key(name="Key 1")
            generate_api_key(name="Key 2")
            result = list_api_keys()

        assert len(result) == 2

    def test_key_masking(self, temp_db):
        """测试 key 脱敏显示。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            original = generate_api_key(name="Test")
            result = list_api_keys(include_full_key=False)

        # 脱敏格式：sk-xxxx...xxxx
        assert "..." in result[0]["key"]
        assert result[0]["key"].startswith("sk-")

    def test_include_full_key(self, temp_db):
        """测试包含完整 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            original = generate_api_key(name="Test")
            result = list_api_keys(include_full_key=True)

        assert result[0]["key"] == original["key"]
        assert "..." not in result[0]["key"]


class TestGetApiKeyById:
    """get_api_key_by_id 函数测试。"""

    def test_get_existing_key(self, temp_db):
        """测试获取存在的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            created = generate_api_key(name="Test Key")
            result = get_api_key_by_id(created["id"])

        assert result is not None
        assert result["id"] == created["id"]
        assert result["key"] == created["key"]
        assert result["name"] == "Test Key"

    def test_get_nonexistent_key(self, temp_db):
        """测试获取不存在的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = get_api_key_by_id(99999)

        assert result is None


class TestValidateApiKey:
    """validate_api_key 函数测试。"""

    def test_valid_key(self, temp_db):
        """测试有效的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            created = generate_api_key(name="Test")
            result = validate_api_key(created["key"])

        assert result is True

    def test_invalid_key(self, temp_db):
        """测试无效的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = validate_api_key("sk-invalid_key_that_does_not_exist")

        assert result is False

    def test_wrong_prefix(self, temp_db):
        """测试错误前缀的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = validate_api_key("wrong-prefix-key")

        assert result is False

    def test_empty_key(self, temp_db):
        """测试空 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = validate_api_key("")

        assert result is False

    def test_none_key(self, temp_db):
        """测试 None key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = validate_api_key(None)

        assert result is False

    def test_inactive_key(self, temp_db):
        """测试已禁用的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            created = generate_api_key(name="Test")
            toggle_api_key(created["id"], False)
            result = validate_api_key(created["key"])

        assert result is False


class TestDeleteApiKey:
    """delete_api_key 函数测试。"""

    def test_delete_existing(self, temp_db):
        """测试删除存在的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            created = generate_api_key(name="Test")
            result = delete_api_key(created["id"])

        assert result is True

        # 验证已删除
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            check = get_api_key_by_id(created["id"])
        assert check is None

    def test_delete_nonexistent(self, temp_db):
        """测试删除不存在的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = delete_api_key(99999)

        assert result is False


class TestToggleApiKey:
    """toggle_api_key 函数测试。"""

    def test_disable_key(self, temp_db):
        """测试禁用 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            created = generate_api_key(name="Test")
            toggle_api_key(created["id"], False)
            result = get_api_key_by_id(created["id"])

        assert result["is_active"] is False

    def test_enable_key(self, temp_db):
        """测试启用 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            created = generate_api_key(name="Test")
            toggle_api_key(created["id"], False)
            toggle_api_key(created["id"], True)
            result = get_api_key_by_id(created["id"])

        assert result["is_active"] is True

    def test_toggle_nonexistent(self, temp_db):
        """测试切换不存在的 key。"""
        with patch("biz_gemini.api_keys.DB_FILE", temp_db):
            result = toggle_api_key(99999, True)

        assert result is False

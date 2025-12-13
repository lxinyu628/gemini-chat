"""配置模块测试。"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from biz_gemini.config import (
    DEFAULT_CONFIG,
    sanitize_group_id,
    load_config,
    save_config,
    get_proxy,
)


class TestSanitizeGroupId:
    """sanitize_group_id 函数测试。"""

    def test_clean_uuid(self):
        """测试干净的 UUID。"""
        uuid = "51518926-c5e4-4372-b9c1-b4e6f2afa7ed"
        result = sanitize_group_id(uuid)
        assert result == uuid

    def test_with_path(self):
        """测试带路径的 group_id。"""
        group_id = "51518926-c5e4-4372-b9c1-b4e6f2afa7ed/some/path"
        result = sanitize_group_id(group_id)
        assert result == "51518926-c5e4-4372-b9c1-b4e6f2afa7ed"

    def test_with_query_params(self):
        """测试带查询参数的 group_id。"""
        group_id = "51518926-c5e4-4372-b9c1-b4e6f2afa7ed?param=value"
        result = sanitize_group_id(group_id)
        assert result == "51518926-c5e4-4372-b9c1-b4e6f2afa7ed"

    def test_with_hash(self):
        """测试带 hash 的 group_id。"""
        group_id = "51518926-c5e4-4372-b9c1-b4e6f2afa7ed#section"
        result = sanitize_group_id(group_id)
        assert result == "51518926-c5e4-4372-b9c1-b4e6f2afa7ed"

    def test_with_whitespace(self):
        """测试带空白字符的 group_id。"""
        group_id = "  51518926-c5e4-4372-b9c1-b4e6f2afa7ed  "
        result = sanitize_group_id(group_id)
        assert result == "51518926-c5e4-4372-b9c1-b4e6f2afa7ed"

    def test_none_input(self):
        """测试 None 输入。"""
        result = sanitize_group_id(None)
        assert result is None

    def test_empty_string(self):
        """测试空字符串。"""
        result = sanitize_group_id("")
        assert result == ""


class TestDefaultConfig:
    """默认配置测试。"""

    def test_has_all_sections(self):
        """测试默认配置包含所有必要的节。"""
        required_sections = [
            "server",
            "proxy",
            "session",
            "browser_keep_alive",
            "remote_browser",
            "account_state",
            "security",
            "redis",
            "imap",
        ]
        for section in required_sections:
            assert section in DEFAULT_CONFIG, f"缺少配置节: {section}"

    def test_server_defaults(self):
        """测试服务器默认配置。"""
        server = DEFAULT_CONFIG["server"]
        assert server["host"] == "0.0.0.0"
        assert server["port"] == 8000
        assert server["workers"] == 4
        assert server["log_level"] == "INFO"
        assert server["reload"] is False

    def test_proxy_defaults(self):
        """测试代理默认配置。"""
        proxy = DEFAULT_CONFIG["proxy"]
        assert proxy["enabled"] is False
        assert proxy["url"] == ""
        assert proxy["timeout"] == 30

    def test_redis_defaults(self):
        """测试 Redis 默认配置。"""
        redis = DEFAULT_CONFIG["redis"]
        assert redis["enabled"] is False
        assert redis["host"] == "127.0.0.1"
        assert redis["port"] == 6379
        assert redis["db"] == 0


class TestGetProxy:
    """get_proxy 函数测试。"""

    def test_proxy_disabled(self):
        """测试代理禁用时返回 None。"""
        config = {
            "proxy": {
                "enabled": False,
                "url": "http://proxy:8080",
            }
        }
        result = get_proxy(config)
        assert result is None

    def test_proxy_enabled(self):
        """测试代理启用时返回 URL。"""
        config = {
            "proxy": {
                "enabled": True,
                "url": "http://proxy:8080",
            }
        }
        result = get_proxy(config)
        assert result == "http://proxy:8080"

    def test_proxy_enabled_but_empty_url(self):
        """测试代理启用但 URL 为空。"""
        config = {
            "proxy": {
                "enabled": True,
                "url": "",
            }
        }
        result = get_proxy(config)
        assert result is None or result == ""

    def test_legacy_proxy_string(self):
        """测试旧版字符串格式代理配置。"""
        config = {
            "proxy": "http://legacy-proxy:8080"
        }
        result = get_proxy(config)
        assert result == "http://legacy-proxy:8080"


class TestLoadConfig:
    """load_config 函数测试。"""

    def test_load_default_when_no_file(self, temp_dir):
        """测试文件不存在时加载默认配置。"""
        # 使用不存在的配置文件路径
        with patch("biz_gemini.config.NEW_CONFIG_FILE", temp_dir / "nonexistent.json"):
            with patch("biz_gemini.config.OLD_CONFIG_FILE", temp_dir / "old_nonexistent.json"):
                config = load_config()

        # 应该返回包含默认值的配置
        assert "server" in config
        assert "proxy" in config
        assert "session" in config

    def test_load_from_file(self, config_file, sample_config):
        """测试从文件加载配置。"""
        with patch("biz_gemini.config.NEW_CONFIG_FILE", config_file):
            with patch("biz_gemini.config.OLD_CONFIG_FILE", config_file.parent / "old.json"):
                config = load_config()

        assert config["session"]["group_id"] == sample_config["session"]["group_id"]

    def test_merge_with_defaults(self, temp_dir):
        """测试配置与默认值合并。"""
        # 创建只包含部分配置的文件
        partial_config = {
            "server": {
                "port": 9000,
            }
        }
        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump(partial_config, f)

        with patch("biz_gemini.config.NEW_CONFIG_FILE", config_path):
            with patch("biz_gemini.config.OLD_CONFIG_FILE", temp_dir / "old.json"):
                config = load_config()

        # 自定义值应该保留
        assert config["server"]["port"] == 9000
        # 默认值应该补全
        assert config["server"]["host"] == "0.0.0.0"
        assert "proxy" in config


class TestSaveConfig:
    """save_config 函数测试。"""

    def test_save_updates_existing(self, config_file, sample_config):
        """测试更新现有配置。"""
        with patch("biz_gemini.config.NEW_CONFIG_FILE", config_file):
            with patch("biz_gemini.config.OLD_CONFIG_FILE", config_file.parent / "old.json"):
                new_data = {"session": {"group_id": "new-group-id"}}
                result = save_config(new_data)

        assert result["session"]["group_id"] == "new-group-id"

        # 验证文件已更新
        with open(config_file) as f:
            saved = json.load(f)
        assert saved["session"]["group_id"] == "new-group-id"

    def test_save_preserves_other_fields(self, config_file, sample_config):
        """测试保存时保留其他字段。"""
        with patch("biz_gemini.config.NEW_CONFIG_FILE", config_file):
            with patch("biz_gemini.config.OLD_CONFIG_FILE", config_file.parent / "old.json"):
                new_data = {"session": {"group_id": "new-group-id"}}
                result = save_config(new_data)

        # 其他字段应该保留
        assert result["server"]["port"] == sample_config["server"]["port"]

"""认证模块测试。"""
import base64
import json
import time

import pytest

from biz_gemini.auth import (
    url_safe_b64encode,
    kq_encode,
    decode_xsrf_token,
    create_jwt,
    _build_cookie_header,
    _parse_cookie_str,
)


class TestUrlSafeB64Encode:
    """url_safe_b64encode 函数测试。"""

    def test_basic_encoding(self):
        """测试基本编码功能。"""
        result = url_safe_b64encode(b"hello")
        assert result == "aGVsbG8"
        assert "=" not in result  # 无 padding

    def test_empty_bytes(self):
        """测试空字节数组。"""
        result = url_safe_b64encode(b"")
        assert result == ""

    def test_url_safe_characters(self):
        """测试 URL 安全字符（无 + 和 /）。"""
        # 包含会产生 + 和 / 的数据
        data = b"\xfb\xff\xfe"
        result = url_safe_b64encode(data)
        assert "+" not in result
        assert "/" not in result

    def test_no_padding(self):
        """测试各种长度的数据都无 padding。"""
        for i in range(1, 20):
            data = b"x" * i
            result = url_safe_b64encode(data)
            assert "=" not in result


class TestKqEncode:
    """kq_encode 函数测试。"""

    def test_ascii_string(self):
        """测试 ASCII 字符串编码。"""
        result = kq_encode("hello")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unicode_string(self):
        """测试 Unicode 字符串编码。"""
        result = kq_encode("你好")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_mixed_string(self):
        """测试混合字符串编码。"""
        result = kq_encode("hello你好123")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_string(self):
        """测试空字符串。"""
        result = kq_encode("")
        assert result == ""

    def test_double_byte_handling(self):
        """测试双字节字符处理。"""
        # 双字节字符应该被拆分为低字节和高字节
        char = "中"  # ord('中') = 20013 = 0x4E2D
        result = kq_encode(char)
        assert isinstance(result, str)


class TestDecodeXsrfToken:
    """decode_xsrf_token 函数测试。"""

    def test_basic_decoding(self):
        """测试基本解码功能。"""
        # 创建一个已知的 base64 编码值
        original = b"test_key_data"
        encoded = base64.urlsafe_b64encode(original).decode("utf-8").rstrip("=")

        result = decode_xsrf_token(encoded)
        assert result == original

    def test_padding_auto_fix(self):
        """测试自动补齐 padding。"""
        # 不同长度的数据需要不同的 padding
        for data in [b"a", b"ab", b"abc", b"abcd"]:
            encoded = base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")
            result = decode_xsrf_token(encoded)
            assert result == data

    def test_already_padded(self):
        """测试已有 padding 的 token。"""
        original = b"test_data"
        encoded = base64.urlsafe_b64encode(original).decode("utf-8")  # 保留 padding
        result = decode_xsrf_token(encoded)
        assert result == original


class TestCreateJwt:
    """create_jwt 函数测试。"""

    def test_jwt_structure(self):
        """测试 JWT 结构（header.payload.signature）。"""
        key = b"test_secret_key_32_bytes_long!!"
        jwt, expires = create_jwt(key, "key123", "session456")

        parts = jwt.split(".")
        assert len(parts) == 3  # header, payload, signature

    def test_jwt_expires_at(self):
        """测试过期时间计算。"""
        key = b"test_secret_key_32_bytes_long!!"
        lifetime = 300
        before = time.time()
        jwt, expires = create_jwt(key, "key123", "session456", lifetime=lifetime)
        after = time.time()

        # 过期时间应该在 now + lifetime 范围内
        assert before + lifetime <= expires <= after + lifetime

    def test_custom_lifetime(self):
        """测试自定义生命周期。"""
        key = b"test_secret_key_32_bytes_long!!"

        jwt1, exp1 = create_jwt(key, "key1", "sess1", lifetime=100)
        jwt2, exp2 = create_jwt(key, "key2", "sess2", lifetime=600)

        # 不同 lifetime 应该产生不同的过期时间
        assert abs((exp2 - exp1) - 500) < 2  # 允许 2 秒误差

    def test_different_keys_different_signature(self):
        """测试不同密钥产生不同签名。"""
        key1 = b"test_secret_key_1_32_bytes_long"
        key2 = b"test_secret_key_2_32_bytes_long"

        jwt1, _ = create_jwt(key1, "key", "sess")
        jwt2, _ = create_jwt(key2, "key", "sess")

        # 签名部分应该不同
        sig1 = jwt1.split(".")[-1]
        sig2 = jwt2.split(".")[-1]
        assert sig1 != sig2


class TestBuildCookieHeader:
    """_build_cookie_header 函数测试。"""

    def test_prefer_cookie_raw(self):
        """测试优先使用 cookie_raw。"""
        config = {
            "cookie_raw": "full_raw_cookie_string",
            "secure_c_ses": "ses_value",
            "host_c_oses": "oses_value",
        }

        cookie_str, debug_info = _build_cookie_header(config)

        assert cookie_str == "full_raw_cookie_string"
        assert debug_info["cookie_source"] == "cookie_raw"

    def test_fallback_to_fields(self):
        """测试回退到字段拼接。"""
        config = {
            "cookie_raw": "",  # 空字符串
            "secure_c_ses": "ses_value",
            "host_c_oses": "oses_value",
            "nid": "nid_value",
        }

        cookie_str, debug_info = _build_cookie_header(config)

        assert "__Secure-C_SES=ses_value" in cookie_str
        assert "__Host-C_OSES=oses_value" in cookie_str
        assert "NID=nid_value" in cookie_str
        assert debug_info["cookie_source"] == "fields"

    def test_minimal_fields(self):
        """测试只有必要字段。"""
        config = {
            "secure_c_ses": "ses_value",
        }

        cookie_str, debug_info = _build_cookie_header(config)

        assert cookie_str == "__Secure-C_SES=ses_value"

    def test_debug_info_content(self):
        """测试调试信息内容。"""
        config = {
            "cookie_raw": "a" * 200,  # 长字符串
        }

        cookie_str, debug_info = _build_cookie_header(config)

        assert "cookie_header_length" in debug_info
        assert debug_info["cookie_header_length"] == 200
        assert "..." in debug_info["cookie_header_preview"]  # 被截断


class TestParseCookieStr:
    """_parse_cookie_str 函数测试。"""

    def test_single_cookie(self):
        """测试单个 Cookie 解析。"""
        result = _parse_cookie_str("name=value")
        assert result == {"name": "value"}

    def test_multiple_cookies(self):
        """测试多个 Cookie 解析。"""
        result = _parse_cookie_str("name1=value1; name2=value2; name3=value3")
        assert result == {
            "name1": "value1",
            "name2": "value2",
            "name3": "value3",
        }

    def test_empty_string(self):
        """测试空字符串。"""
        result = _parse_cookie_str("")
        assert result == {}

    def test_cookie_with_special_values(self):
        """测试包含特殊字符的值。"""
        result = _parse_cookie_str("name=value%20with%20spaces")
        assert "name" in result

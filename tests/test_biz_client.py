"""客户端数据结构测试。"""
import os
import tempfile
from pathlib import Path

import pytest

from biz_gemini.biz_client import (
    ImageThumbnail,
    ChatImage,
    ChatResponse,
)


class TestImageThumbnail:
    """ImageThumbnail 数据类测试。"""

    def test_default_values(self):
        """测试默认值。"""
        thumb = ImageThumbnail()

        assert thumb.view_id is None
        assert thumb.uri is None
        assert thumb.mime_type == "image/png"
        assert thumb.byte_size is None
        assert thumb.width is None
        assert thumb.height is None

    def test_custom_values(self):
        """测试自定义值。"""
        thumb = ImageThumbnail(
            view_id="view_123",
            uri="https://example.com/thumb.png",
            mime_type="image/jpeg",
            byte_size=1024,
            width=256,
            height=256,
        )

        assert thumb.view_id == "view_123"
        assert thumb.uri == "https://example.com/thumb.png"
        assert thumb.mime_type == "image/jpeg"
        assert thumb.byte_size == 1024
        assert thumb.width == 256
        assert thumb.height == 256


class TestChatImage:
    """ChatImage 数据类测试。"""

    def test_default_values(self):
        """测试默认值。"""
        img = ChatImage()

        assert img.url is None
        assert img.base64_data is None
        assert img.mime_type == "image/png"
        assert img.local_path is None
        assert img.file_id is None
        assert img.thumbnails == {}

    def test_get_thumbnail(self):
        """测试获取缩略图。"""
        thumb = ImageThumbnail(view_id="test", width=256, height=256)
        img = ChatImage(thumbnails={"thumbnail_256x256": thumb})

        result = img.get_thumbnail("thumbnail_256x256")
        assert result == thumb

        result_none = img.get_thumbnail("nonexistent")
        assert result_none is None

    def test_from_file_metadata(self):
        """测试从元数据创建。"""
        metadata = {
            "fileId": "file_123",
            "name": "image.png",
            "mimeType": "image/png",
            "byteSize": "2048",
            "tokenCount": "100",
            "quotaPercentage": 0.5,
            "downloadUri": "https://example.com/download",
            "uploadTime": "2025-01-01T00:00:00Z",
            "fileOriginType": "AI_GENERATED",
            "session": "session_123",
            "views": {
                "thumbnail_256x256": {
                    "viewId": "view_1",
                    "uri": "https://example.com/thumb.png",
                    "mimeType": "image/png",
                    "byteSize": "512",
                    "imageCharacteristics": {
                        "width": 256,
                        "height": 256,
                    },
                }
            },
        }

        img = ChatImage.from_file_metadata(metadata)

        assert img.file_id == "file_123"
        assert img.file_name == "image.png"
        assert img.mime_type == "image/png"
        assert img.byte_size == 2048
        assert img.token_count == 100
        assert img.file_origin_type == "AI_GENERATED"

        thumb = img.get_thumbnail("thumbnail_256x256")
        assert thumb is not None
        assert thumb.width == 256

    def test_save_to_file_with_base64(self, temp_dir):
        """测试从 base64 保存文件。"""
        # 最小的有效 PNG 文件（1x1 透明像素）
        import base64
        png_data = base64.b64encode(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
            b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        ).decode("utf-8")

        img = ChatImage(base64_data=png_data, mime_type="image/png")
        filepath = img.save_to_file(directory=str(temp_dir))

        assert os.path.exists(filepath)
        assert filepath.endswith(".png")
        assert img.local_path == filepath

    def test_save_to_file_no_data(self, temp_dir):
        """测试无数据时保存失败。"""
        img = ChatImage()

        with pytest.raises(ValueError, match="没有图片数据"):
            img.save_to_file(directory=str(temp_dir))

    def test_save_to_file_already_saved(self, temp_dir):
        """测试已保存时直接返回路径。"""
        existing_path = str(temp_dir / "existing.png")
        # 创建文件
        with open(existing_path, "wb") as f:
            f.write(b"fake image data")

        img = ChatImage(local_path=existing_path)
        result = img.save_to_file(directory=str(temp_dir))

        assert result == existing_path


class TestChatResponse:
    """ChatResponse 数据类测试。"""

    def test_default_values(self):
        """测试默认值。"""
        response = ChatResponse()

        assert response.text == ""
        assert response.images == []
        assert response.thoughts == []

    def test_str_text_only(self):
        """测试只有文本时的字符串表示。"""
        response = ChatResponse(text="Hello, World!")

        result = str(response)
        assert "Hello, World!" in result

    def test_str_with_thoughts(self):
        """测试包含思考链时的字符串表示。"""
        response = ChatResponse(
            text="答案是 42",
            thoughts=["首先考虑问题...", "然后计算..."],
        )

        result = str(response)
        assert "[思考]" in result
        assert "首先考虑问题" in result
        assert "然后计算" in result
        assert "答案是 42" in result

    def test_str_with_images(self):
        """测试包含图片时的字符串表示。"""
        img1 = ChatImage(local_path="/path/to/image1.png")
        img2 = ChatImage(url="https://example.com/image2.png")
        img3 = ChatImage(base64_data="aGVsbG8=")

        response = ChatResponse(
            text="这是一些图片",
            images=[img1, img2, img3],
        )

        result = str(response)
        assert "已保存" in result
        assert "URL" in result
        assert "base64" in result

    def test_empty_response(self):
        """测试空响应的字符串表示。"""
        response = ChatResponse()

        result = str(response)
        assert result == ""

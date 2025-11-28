import base64
import time
import uuid
from typing import Dict, Generator, Iterable, List, Optional

from .biz_client import BizGeminiClient, ChatResponse, ChatImage, ImageThumbnail


def _flatten_messages_to_text(messages: List[Dict]) -> str:
    """将 OpenAI 风格 messages 转成一个纯文本 prompt。"""
    lines: List[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            # 处理 content 为 [{"type": "text", "text": "..."}] 的情况
            texts: List[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text", "")))
            content = "\n".join(texts)
        if content:
            lines.append(content)
    return "\n".join(lines)


def _split_chunks(text: str, size: int = 120) -> Iterable[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _build_openai_content(response: ChatResponse, include_image_data: bool = True, embed_images: bool = True) -> List[Dict]:
    """将 ChatResponse 转换为 OpenAI 多模态 content 格式。

    Args:
        response: ChatResponse 对象
        include_image_data: 是否包含图片数据（用于 _image_to_openai_format）
        embed_images: 是否将图片内嵌到 content 中
            - True (默认): 图片以 URL 格式内嵌到 content，兼容 OpenAI 格式，第三方工具可用
            - False: 图片不内嵌，通过独立的 images 字段返回，避免重复

    返回格式:
    - 如果只有文本：返回字符串
    - 如果有多部分内容：返回 [{"type": "text", "text": "..."}, {"type": "image_url", ...}, ...]
    """
    content_parts: List[Dict] = []

    # 添加思考内容（如果有）
    for thought in response.thoughts:
        content_parts.append({
            "type": "text",
            "text": f"[思考] {thought}"
        })

    # 添加文本内容
    if response.text:
        content_parts.append({
            "type": "text",
            "text": response.text
        })

    # 根据 embed_images 参数决定是否将图片内嵌到 content
    if embed_images:
        for img in response.images:
            img_part = _image_to_openai_format(img, include_image_data)
            if img_part:
                content_parts.append(img_part)

    # 如果只有一个文本部分，返回简化格式
    if len(content_parts) == 1 and content_parts[0]["type"] == "text":
        return content_parts[0]["text"]

    return content_parts if content_parts else ""


def _image_to_openai_format(img: ChatImage, include_data: bool = True) -> Optional[Dict]:
    """将 ChatImage 转换为 OpenAI 图片格式。

    优先使用 URL（响应更小），其次使用 base64。
    """
    import os

    # 优先使用本地路径构造 URL（响应更小）
    if img.local_path and os.path.exists(img.local_path):
        filename = os.path.basename(img.local_path)
        return {
            "type": "image_url",
            "image_url": {"url": f"/api/images/{filename}"}
        }

    # 如果有 session 和 file_id，构造下载 URL
    if img.file_id and img.session:
        session_id = img.session.split("/")[-1] if "/" in img.session else img.session
        url = f"/api/sessions/{session_id}/images/{img.file_id}?session_name={img.session}"
        return {
            "type": "image_url",
            "image_url": {"url": url}
        }

    # 如果有直接 URL，使用它
    if img.url:
        return {
            "type": "image_url",
            "image_url": {"url": img.url}
        }

    # 最后才使用 base64（响应较大）
    if include_data and img.base64_data:
        mime = img.mime_type or "image/png"
        data_url = f"data:{mime};base64,{img.base64_data}"
        return {
            "type": "image_url",
            "image_url": {"url": data_url}
        }

    # 如果有本地路径但文件不存在，读取并转 base64
    if include_data and img.local_path:
        try:
            with open(img.local_path, "rb") as f:
                img_bytes = f.read()
            b64_data = base64.b64encode(img_bytes).decode("utf-8")
            mime = img.mime_type or "image/png"
            data_url = f"data:{mime};base64,{b64_data}"
            return {
                "type": "image_url",
                "image_url": {"url": data_url}
            }
        except Exception:
            pass

    # 返回文件元数据信息（不含实际图片数据）
    if img.file_id or img.local_path:
        return {
            "type": "image_file",
            "image_file": {
                "file_id": img.file_id,
                "file_name": img.file_name,
                "local_path": img.local_path,
                "mime_type": img.mime_type,
            }
        }

    return None


def _build_image_metadata(response: ChatResponse) -> Optional[List[Dict]]:
    """构建图片元数据列表（用于扩展字段）。

    返回从 widgetListSessionFileMetadata 接口获取的完整元数据，包括：
    - file_id, file_name, mime_type, local_path
    - url: 可访问的图片 URL
    - byte_size, token_count, quota_percentage
    - download_uri, upload_time, file_origin_type, session
    - thumbnails: 缩略图信息
    """
    if not response.images:
        return None

    import os

    metadata = []
    for img in response.images:
        meta = {
            "file_id": img.file_id,
            "file_name": img.file_name,
            "mime_type": img.mime_type,
            "local_path": img.local_path,
            # 完整元数据字段
            "byte_size": img.byte_size,
            "token_count": img.token_count,
            "quota_percentage": img.quota_percentage,
            "download_uri": img.download_uri,
            "upload_time": img.upload_time,
            "file_origin_type": img.file_origin_type,
            "session": img.session,
        }

        # 构造可访问的 URL
        if img.local_path:
            # 从本地路径提取文件名，构造 API URL
            filename = os.path.basename(img.local_path)
            meta["url"] = f"/api/images/{filename}"
        elif img.url:
            meta["url"] = img.url
        elif img.file_id and img.session:
            # 使用 session image 下载接口
            session_id = img.session.split("/")[-1] if "/" in img.session else img.session
            meta["url"] = f"/api/sessions/{session_id}/images/{img.file_id}?session_name={img.session}"

        # 添加缩略图信息
        if img.thumbnails:
            thumbnails = {}
            for name, thumb in img.thumbnails.items():
                if isinstance(thumb, ImageThumbnail):
                    thumbnails[name] = {
                        "view_id": thumb.view_id,
                        "uri": thumb.uri,
                        "mime_type": thumb.mime_type,
                        "byte_size": thumb.byte_size,
                        "width": thumb.width,
                        "height": thumb.height,
                    }
            if thumbnails:
                meta["thumbnails"] = thumbnails

        metadata.append(meta)

    return metadata


class OpenAICompatClient:
    """一个简单的 OpenAI ChatCompletion 兼容包装。

    调用方式大致类似：
        client = OpenAICompatClient(biz_client)
        resp = client.chat.completions.create(
            model="business-gemini",
            messages=[{"role": "user", "content": "你好"}],
        )
    """

    class _ChatCompletions:
        def __init__(self, biz_client: BizGeminiClient, default_model: str = "business-gemini"):
            self._biz = biz_client
            self._default_model = default_model

        def create(
            self,
            model: Optional[str] = None,
            messages: Optional[List[Dict]] = None,
            stream: bool = False,
            include_image_data: bool = True,
            include_thoughts: bool = False,
            embed_images_in_content: bool = True,
            **kwargs,
        ):
            """创建聊天完成。

            Args:
                model: 模型名称
                messages: 消息列表
                stream: 是否流式输出
                include_image_data: 是否在响应中包含图片 base64 数据（默认 True）
                                   设为 False 时只返回图片元数据
                include_thoughts: 是否返回思考链（默认 False）
                embed_images_in_content: 是否将图片内嵌到 content 中（默认 True）
                    - True: 兼容 OpenAI 格式，第三方工具可显示图片
                    - False: 图片只在 images 字段返回，避免重复（自定义前端使用）
            """
            if messages is None:
                raise ValueError("messages 不能为空")
            model_name = model or self._default_model
            prompt = _flatten_messages_to_text(messages)
            created = int(time.time())
            cmpl_id = f"chatcmpl-{uuid.uuid4().hex}"

            if not stream:
                # 使用 chat_full 获取完整响应（包括图片）
                # 提取模型 ID（如果不是 business-gemini 或 auto，则传递给 API）
                model_id = None if model_name in ("business-gemini", "auto") else model_name
                response = self._biz.chat_full(
                    prompt,
                    auto_save_images=True,
                    model_id=model_id,
                    include_thoughts=include_thoughts,
                )

                # 构建 OpenAI 格式的 content
                content = _build_openai_content(response, include_image_data, embed_images_in_content)

                result = {
                    "id": cmpl_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": content,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                    },
                }

                # 添加图片元数据扩展字段（方便客户端获取文件信息）
                image_metadata = _build_image_metadata(response)
                if image_metadata:
                    result["images"] = image_metadata

                # 添加思考链（作为独立字段，便于前端处理）
                if response.thoughts:
                    result["thoughts"] = response.thoughts

                return result

            # stream=True 返回一个生成器，伪装成 OpenAI 的 SSE 流片段
            def _gen() -> Generator[Dict, None, None]:
                # 流式模式下，先获取完整响应再分块返回
                model_id = None if model_name in ("business-gemini", "auto") else model_name
                response = self._biz.chat_full(
                    prompt,
                    auto_save_images=True,
                    model_id=model_id,
                    include_thoughts=include_thoughts,
                )

                first = True

                # 先返回思考链（如果有）
                if response.thoughts:
                    for thought in response.thoughts:
                        delta = {"thought": thought}
                        if first:
                            delta["role"] = "assistant"
                            first = False
                        yield {
                            "id": cmpl_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_name,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": delta,
                                    "finish_reason": None,
                                }
                            ],
                        }

                # 返回文本部分
                text_content = response.text
                for chunk in _split_chunks(text_content):
                    delta = {"content": chunk}
                    if first:
                        delta["role"] = "assistant"
                        first = False
                    yield {
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": delta,
                                "finish_reason": None,
                            }
                        ],
                    }

                # 如果有图片，在最后一个 chunk 中包含图片信息
                if response.images:
                    image_metadata = _build_image_metadata(response)
                    yield {
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": None,
                            }
                        ],
                        "images": image_metadata,
                    }

                # 结束片段
                yield {
                    "id": cmpl_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                }

            return _gen()

    class _Chat:
        def __init__(self, biz_client: BizGeminiClient, default_model: str = "business-gemini"):
            self.completions = OpenAICompatClient._ChatCompletions(biz_client, default_model)

    def __init__(self, biz_client: BizGeminiClient, default_model: str = "business-gemini"):
        self.chat = OpenAICompatClient._Chat(biz_client, default_model)

"""Anthropic Messages API 兼容适配器

将 Anthropic Messages API 格式的请求转换为 BizGeminiClient 调用，
并将响应转换为 Anthropic 格式，以支持 Claude Code (Claude CLI) 等工具。

Anthropic API 文档: https://docs.anthropic.com/en/api/messages
"""
import time
import uuid
from typing import Dict, Generator, List, Optional, Any, Union

from .biz_client import BizGeminiClient, ChatResponse


def _flatten_anthropic_messages(
    messages: List[Dict],
    system: Optional[str] = None
) -> str:
    """将 Anthropic 风格的 messages 转换为纯文本 prompt。

    Anthropic 格式:
    - messages: [{"role": "user", "content": "..."}]
    - content 可以是字符串或 content blocks 列表
    - system 是独立的顶级参数
    """
    lines: List[str] = []

    # 添加 system prompt（如果有）
    if system:
        lines.append(f"[System]\n{system}\n")

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # content 可以是字符串或 content blocks 列表
        if isinstance(content, str):
            text_content = content
        elif isinstance(content, list):
            # 处理 content blocks: [{"type": "text", "text": "..."}]
            texts: List[str] = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        texts.append(str(block.get("text", "")))
                    elif block_type == "image":
                        # 图片暂时跳过，后续可以扩展支持
                        pass
            text_content = "\n".join(texts)
        else:
            text_content = str(content)

        if text_content:
            lines.append(text_content)

    return "\n".join(lines)


def _build_anthropic_content(response: ChatResponse) -> List[Dict]:
    """将 ChatResponse 转换为 Anthropic content blocks 格式。

    返回格式: [{"type": "text", "text": "..."}]
    """
    content_blocks: List[Dict] = []

    # 添加思考内容（如果有）- 使用 thinking block
    if response.thoughts:
        for thought in response.thoughts:
            content_blocks.append({
                "type": "thinking",
                "thinking": thought
            })

    # 添加文本内容
    if response.text:
        content_blocks.append({
            "type": "text",
            "text": response.text
        })

    # 处理图片（Anthropic 格式使用 image block）
    for img in response.images:
        if img.base64_data:
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.mime_type or "image/png",
                    "data": img.base64_data
                }
            })
        elif img.local_path:
            # 提供本地文件 URL
            import os
            filename = os.path.basename(img.local_path)
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "url",
                    "url": f"/api/images/{filename}"
                }
            })

    # 确保至少有一个 content block
    if not content_blocks:
        content_blocks.append({
            "type": "text",
            "text": ""
        })

    return content_blocks


def _split_text_chunks(text: str, size: int = 100) -> List[str]:
    """将文本分割成小块用于流式传输。"""
    chunks = []
    for i in range(0, len(text), size):
        chunks.append(text[i:i + size])
    return chunks if chunks else [""]


class AnthropicCompatClient:
    """Anthropic Messages API 兼容客户端。

    调用方式类似 Anthropic SDK:
        client = AnthropicCompatClient(biz_client)
        resp = client.messages.create(
            model="gemini-2.5-pro",
            max_tokens=1024,
            messages=[{"role": "user", "content": "你好"}],
        )
    """

    class _Messages:
        def __init__(self, biz_client: BizGeminiClient, default_model: str = "gemini-2.5-pro"):
            self._biz = biz_client
            self._default_model = default_model

        def create(
            self,
            model: Optional[str] = None,
            max_tokens: int = 4096,
            messages: Optional[List[Dict]] = None,
            system: Optional[str] = None,
            stream: bool = False,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            top_k: Optional[int] = None,
            stop_sequences: Optional[List[str]] = None,
            metadata: Optional[Dict] = None,
            **kwargs,
        ) -> Union[Dict, Generator[Dict, None, None]]:
            """创建消息完成。

            Args:
                model: 模型名称
                max_tokens: 最大生成 token 数
                messages: 消息列表
                system: 系统提示
                stream: 是否流式输出
                temperature: 温度参数
                top_p: Top-p 采样
                top_k: Top-k 采样
                stop_sequences: 停止序列
                metadata: 元数据

            Returns:
                非流式: 完整响应字典
                流式: SSE 事件生成器
            """
            if messages is None:
                raise ValueError("messages 不能为空")

            model_name = model or self._default_model
            prompt = _flatten_anthropic_messages(messages, system)
            msg_id = f"msg_{uuid.uuid4().hex[:24]}"

            if not stream:
                return self._create_sync(model_name, prompt, msg_id, max_tokens)
            else:
                return self._create_stream(model_name, prompt, msg_id, max_tokens)

        def _create_sync(
            self,
            model_name: str,
            prompt: str,
            msg_id: str,
            max_tokens: int,
        ) -> Dict:
            """同步创建消息。"""
            # 映射模型名称
            model_id = self._map_model_id(model_name)

            # 调用 Gemini API
            response = self._biz.chat_full(
                prompt,
                auto_save_images=True,
                model_id=model_id,
                include_thoughts=True,  # 获取思考链
            )

            # 构建 Anthropic 格式响应
            content = _build_anthropic_content(response)

            return {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": content,
                "model": model_name,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": self._estimate_tokens(prompt),
                    "output_tokens": self._estimate_tokens(response.text or ""),
                }
            }

        def _create_stream(
            self,
            model_name: str,
            prompt: str,
            msg_id: str,
            max_tokens: int,
        ) -> Generator[Dict, None, None]:
            """流式创建消息，返回 SSE 事件生成器。"""
            model_id = self._map_model_id(model_name)

            # 获取完整响应
            response = self._biz.chat_full(
                prompt,
                auto_save_images=True,
                model_id=model_id,
                include_thoughts=True,
            )

            input_tokens = self._estimate_tokens(prompt)
            output_tokens = 0

            # 1. message_start 事件
            yield {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model_name,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": 1,
                    }
                }
            }

            content_index = 0

            # 2. 处理思考链（如果有）
            for thought in response.thoughts:
                # content_block_start
                yield {
                    "type": "content_block_start",
                    "index": content_index,
                    "content_block": {
                        "type": "thinking",
                        "thinking": ""
                    }
                }

                # 分块发送思考内容
                for chunk in _split_text_chunks(thought):
                    output_tokens += len(chunk) // 4
                    yield {
                        "type": "content_block_delta",
                        "index": content_index,
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": chunk
                        }
                    }

                # content_block_stop
                yield {
                    "type": "content_block_stop",
                    "index": content_index
                }

                content_index += 1

            # 3. 处理文本内容
            if response.text:
                # content_block_start
                yield {
                    "type": "content_block_start",
                    "index": content_index,
                    "content_block": {
                        "type": "text",
                        "text": ""
                    }
                }

                # 分块发送文本
                for chunk in _split_text_chunks(response.text):
                    output_tokens += len(chunk) // 4
                    yield {
                        "type": "content_block_delta",
                        "index": content_index,
                        "delta": {
                            "type": "text_delta",
                            "text": chunk
                        }
                    }

                # content_block_stop
                yield {
                    "type": "content_block_stop",
                    "index": content_index
                }

                content_index += 1

            # 4. 处理图片（如果有）
            for img in response.images:
                if img.local_path or img.base64_data:
                    yield {
                        "type": "content_block_start",
                        "index": content_index,
                        "content_block": {
                            "type": "image",
                            "source": self._build_image_source(img)
                        }
                    }

                    yield {
                        "type": "content_block_stop",
                        "index": content_index
                    }

                    content_index += 1

            # 5. message_delta 事件
            yield {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "end_turn",
                    "stop_sequence": None
                },
                "usage": {
                    "output_tokens": max(output_tokens, 1)
                }
            }

            # 6. message_stop 事件
            yield {
                "type": "message_stop"
            }

        def _map_model_id(self, model_name: str) -> Optional[str]:
            """映射模型名称到 Gemini 模型 ID。"""
            # Claude 模型名映射到 Gemini 模型
            model_mapping = {
                # Claude 模型 -> Gemini 等效
                "claude-haiku-4-5-20251001": "gemini-2.5-flash",
                "claude-sonnet-4-5-20250929": "gemini-2.5-pro",
                "claude-opus-4-5-20251101": "gemini-3-pro-preview",
                # 直接使用 Gemini 模型名
                "gemini-2.5-pro": "gemini-2.5-pro",
                "gemini-2.5-flash": "gemini-2.5-flash",
                "gemini-3-pro-preview": "gemini-3-pro-preview",
                # 默认
                "business-gemini": None,
            }

            # 检查完整名称
            if model_name in model_mapping:
                return model_mapping[model_name]

            # 检查前缀匹配
            for prefix, target in model_mapping.items():
                if model_name.startswith(prefix):
                    return target

            # 如果是 Gemini 模型，直接返回
            if model_name.startswith("gemini"):
                return model_name

            # 默认使用 auto
            return None

        def _build_image_source(self, img) -> Dict:
            """构建图片 source 对象。"""
            if img.base64_data:
                return {
                    "type": "base64",
                    "media_type": img.mime_type or "image/png",
                    "data": img.base64_data
                }
            elif img.local_path:
                import os
                filename = os.path.basename(img.local_path)
                return {
                    "type": "url",
                    "url": f"/api/images/{filename}"
                }
            return {"type": "url", "url": ""}

        def _estimate_tokens(self, text: str) -> int:
            """估算 token 数量（简单估算：每4个字符约1个token）。"""
            if not text:
                return 0
            return max(1, len(text) // 4)

    def __init__(self, biz_client: BizGeminiClient, default_model: str = "gemini-2.5-pro"):
        self.messages = self._Messages(biz_client, default_model)

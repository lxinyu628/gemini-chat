import os
from typing import List, Optional

import requests

from .config import get_proxy, load_config


class GeminiAPIChatBackend:
    """简单的 Gemini 官方 API 聊天封装，接口设计成适合 CLI 使用。

    使用 generateContent 接口，多轮对话会把 history 一起发给模型。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        api_version: str = "v1beta",
    ):
        cfg = load_config()
        self.proxy = get_proxy(cfg)
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("缺少 GEMINI_API_KEY，可以在环境变量中设置。")
        self.model = model
        self.base_url = f"https://generativelanguage.googleapis.com/{api_version}"
        self.history: List[dict] = []

    def reset(self) -> None:
        self.history.clear()

    def send(self, message: str) -> str:
        """发送一条消息并维护多轮对话 history。"""
        self.history.append(
            {
                "role": "user",
                "parts": [{"text": message}],
            }
        )

        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        body = {"contents": self.history}

        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        resp = requests.post(url, headers=headers, json=body, proxies=proxies, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        candidates = data.get("candidates") or []
        if not candidates:
            return ""

        first = candidates[0]
        parts = first.get("content", {}).get("parts") or []
        texts: List[str] = []
        for p in parts:
            t = p.get("text")
            if t:
                texts.append(t)
        answer = "".join(texts)

        self.history.append(
            {
                "role": "model",
                "parts": [{"text": answer}],
            }
        )
        return answer

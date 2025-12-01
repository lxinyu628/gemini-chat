import base64
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import urllib3
import requests

from .auth import JWTManager
from .config import get_proxy
from .logger import get_logger

# 模块级 logger
logger = get_logger("biz_client")

BASE_URL = "https://biz-discoveryengine.googleapis.com/v1alpha/locations/global"
CREATE_SESSION_URL = f"{BASE_URL}/widgetCreateSession"
STREAM_ASSIST_URL = f"{BASE_URL}/widgetStreamAssist"
LIST_FILE_METADATA_URL = f"{BASE_URL}/widgetListSessionFileMetadata"
DELETE_SESSION_URL = f"{BASE_URL}/widgetDeleteSession"
LIST_SESSIONS_URL = f"{BASE_URL}/widgetListSessions"
GET_SESSION_URL = f"{BASE_URL}/widgetGetSession"
ADD_CONTEXT_FILE_URL = f"{BASE_URL}/widgetAddContextFile"

# 代理场景下需要 verify=False，这里屏蔽 TLS 校验的告警噪音。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 图片保存目录（项目根目录下的 biz_gemini_images）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_SAVE_DIR = os.path.join(_PROJECT_ROOT, "biz_gemini_images")


@dataclass
class ImageThumbnail:
    """图片缩略图信息"""
    view_id: Optional[str] = None
    uri: Optional[str] = None
    mime_type: str = "image/png"
    byte_size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class ChatImage:
    """表示生成的图片"""
    url: Optional[str] = None
    base64_data: Optional[str] = None
    mime_type: str = "image/png"
    local_path: Optional[str] = None
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    # 从 widgetListSessionFileMetadata 接口获取的完整元数据
    byte_size: Optional[int] = None
    token_count: Optional[int] = None
    quota_percentage: Optional[float] = None
    download_uri: Optional[str] = None
    upload_time: Optional[str] = None
    file_origin_type: Optional[str] = None
    session: Optional[str] = None
    # 缩略图信息
    thumbnails: dict = field(default_factory=dict)  # {"thumbnail_256x256": ImageThumbnail, ...}

    def get_thumbnail(self, size: str = "thumbnail_256x256") -> Optional[ImageThumbnail]:
        """获取指定尺寸的缩略图"""
        return self.thumbnails.get(size)

    @classmethod
    def from_file_metadata(cls, metadata: dict) -> "ChatImage":
        """从 widgetListSessionFileMetadata 接口返回的元数据创建 ChatImage"""
        # 解析缩略图
        thumbnails = {}
        views = metadata.get("views", {})
        for view_name, view_data in views.items():
            img_chars = view_data.get("imageCharacteristics", {})
            thumbnails[view_name] = ImageThumbnail(
                view_id=view_data.get("viewId"),
                uri=view_data.get("uri"),
                mime_type=view_data.get("mimeType", "image/png"),
                byte_size=int(view_data["byteSize"]) if view_data.get("byteSize") else None,
                width=img_chars.get("width"),
                height=img_chars.get("height"),
            )

        return cls(
            file_id=metadata.get("fileId"),
            file_name=metadata.get("name"),
            mime_type=metadata.get("mimeType", "image/png"),
            byte_size=int(metadata["byteSize"]) if metadata.get("byteSize") else None,
            token_count=int(metadata["tokenCount"]) if metadata.get("tokenCount") else None,
            quota_percentage=metadata.get("quotaPercentage"),
            download_uri=metadata.get("downloadUri"),
            upload_time=metadata.get("uploadTime"),
            file_origin_type=metadata.get("fileOriginType"),
            session=metadata.get("session"),
            thumbnails=thumbnails,
        )

    def save_to_file(self, directory: Optional[str] = None) -> str:
        """保存图片到本地文件，返回文件路径"""
        if self.local_path and os.path.exists(self.local_path):
            return self.local_path

        save_dir = directory or IMAGE_SAVE_DIR
        os.makedirs(save_dir, exist_ok=True)

        ext = ".png"
        if self.mime_type:
            ext_map = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/gif": ".gif",
                "image/webp": ".webp",
            }
            ext = ext_map.get(self.mime_type, ".png")

        # 使用原始文件名或生成新文件名
        if self.file_name:
            filename = self.file_name
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"gemini_{timestamp}_{uuid.uuid4().hex[:8]}{ext}"

        filepath = os.path.join(save_dir, filename)

        if self.base64_data:
            # 从 base64 解码保存
            image_data = base64.b64decode(self.base64_data)
            with open(filepath, "wb") as f:
                f.write(image_data)
        elif self.url:
            # 从 URL 下载保存（不带认证，用于公开 URL）
            resp = requests.get(self.url, timeout=60, verify=False)
            resp.raise_for_status()
            with open(filepath, "wb") as f:
                f.write(resp.content)
        else:
            raise ValueError("没有图片数据可保存")

        self.local_path = filepath
        return filepath

    def save_with_auth(self, url: str, headers: dict, proxies: Optional[dict] = None, directory: Optional[str] = None) -> str:
        """使用认证头下载并保存图片"""
        if self.local_path and os.path.exists(self.local_path):
            return self.local_path

        save_dir = directory or IMAGE_SAVE_DIR
        os.makedirs(save_dir, exist_ok=True)

        ext = ".png"
        if self.mime_type:
            ext_map = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/gif": ".gif",
                "image/webp": ".webp",
            }
            ext = ext_map.get(self.mime_type, ".png")

        if self.file_name:
            filename = self.file_name
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"gemini_{timestamp}_{uuid.uuid4().hex[:8]}{ext}"

        filepath = os.path.join(save_dir, filename)

        resp = requests.get(url, headers=headers, proxies=proxies, timeout=120, verify=False)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)

        self.local_path = filepath
        return filepath


@dataclass
class ChatResponse:
    """聊天响应，包含文本和可能的图片"""
    text: str = ""
    images: List[ChatImage] = field(default_factory=list)
    thoughts: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        """返回纯文本表示"""
        parts = []
        for thought in self.thoughts:
            parts.append(f"[思考] {thought}")
        if self.text:
            parts.append(self.text)
        for i, img in enumerate(self.images, 1):
            if img.local_path:
                parts.append(f"[图片 {i}] 已保存: {img.local_path}")
            elif img.url:
                parts.append(f"[图片 {i}] URL: {img.url}")
            else:
                parts.append(f"[图片 {i}] (base64 数据)")
        return "\n".join(parts)

def build_headers(jwt: str) -> dict:
    """构造与浏览器一致的请求头。"""
    return {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "authorization": f"Bearer {jwt}",
        "content-type": "application/json",
        "origin": "https://business.gemini.google",
        "priority": "u=1, i",
        "referer": "https://business.gemini.google/",
        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Microsoft Edge";v="140"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-form-factors": '"Desktop"',
        "sec-ch-ua-full-version": '"140.0.3485.54"',
        "sec-ch-ua-full-version-list": '"Chromium";v="140.0.7339.81", "Not=A?Brand";v="24.0.0.0", "Microsoft Edge";v="140.0.3485.54"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-ch-ua-wow64": "?0",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
        "x-server-timeout": "1800",
    }


class BizGeminiClient:
    """封装 Business Gemini DiscoveryEngine 聊天调用。"""

    def __init__(self, config: dict, jwt_manager: JWTManager):
        self.config = config
        self.jwt_manager = jwt_manager
        self.group_id = config.get("group_id")
        if not self.group_id:
            raise ValueError("缺少 group_id，请先通过/login登录获取。")
        proxy = get_proxy(config)
        self._proxies = {"http": proxy, "https": proxy} if proxy else None
        self._session_name: Optional[str] = None

    @property
    def session_name(self) -> str:
        if not self._session_name:
            self._session_name = self.create_session()
        return self._session_name

    def create_session(self) -> str:
        """创建新会话，并返回 session name。"""
        session_id = uuid.uuid4().hex[:12]
        body = {
            "configId": self.group_id,
            "additionalParams": {"token": "-"},
            "createSessionRequest": {
                "session": {"name": session_id, "displayName": session_id}
            },
        }

        for attempt in range(2):
            jwt = self.jwt_manager.get_jwt()
            resp = requests.post(
                CREATE_SESSION_URL,
                headers=build_headers(jwt),
                json=body,
                proxies=self._proxies,
                verify=False,
                timeout=30,
            )

            if resp.status_code == 401 and attempt == 0:
                # JWT 过期，刷新后重试一次
                self.jwt_manager.refresh()
                continue

            if resp.status_code != 200:
                raise RuntimeError(
                    f"创建会话失败: {resp.status_code} {resp.text[:200]}"
                )

            data = resp.json()
            self._session_name = data.get("session", {}).get("name")
            if not self._session_name:
                raise RuntimeError(f"创建会话成功但未返回 session.name: {data}")
            return self._session_name

        raise RuntimeError("多次尝试创建会话失败（可能是 cookie 失效，需要重新登录）。")

    def reset_session(self) -> None:
        self._session_name = None

    def delete_session(self, session_name: str) -> bool:
        """删除指定的会话"""
        body = {
            "configId": self.group_id,
            "additionalParams": {"token": "-"},
            "deleteSessionRequest": {
                "name": session_name
            },
        }

        for attempt in range(2):
            jwt = self.jwt_manager.get_jwt()
            resp = requests.post(
                DELETE_SESSION_URL,
                headers=build_headers(jwt),
                json=body,
                proxies=self._proxies,
                verify=False,
                timeout=30,
            )

            if resp.status_code == 401 and attempt == 0:
                # JWT 过期，刷新后重试一次
                self.jwt_manager.refresh()
                continue

            if resp.status_code != 200:
                raise RuntimeError(
                    f"删除会话失败: {resp.status_code} {resp.text[:200]}"
                )

            return True

        raise RuntimeError("多次尝试删除会话失败（可能是 cookie 失效，需要重新登录）。")

    def list_sessions(
        self,
        page_size: int = 110,
        page_token: str = "",
        order_by: str = "update_time desc",
        filter_str: str = 'display_name != "" AND (NOT labels:hidden-from-ui-history)',
    ) -> dict:
        """获取会话列表（对接 Google 官方接口）

        Args:
            page_size: 每页数量，默认 110
            page_token: 分页 token
            order_by: 排序方式，默认按更新时间降序
            filter_str: 过滤条件

        Returns:
            包含会话列表的字典，格式与 Google API 响应一致
        """
        body = {
            "configId": self.group_id,
            "additionalParams": {"token": "-"},
            "listSessionsRequest": {
                "pageSize": page_size,
                "pageToken": page_token,
                "orderBy": order_by,
                "filter": filter_str,
            },
        }

        for attempt in range(2):
            jwt = self.jwt_manager.get_jwt()
            resp = requests.post(
                LIST_SESSIONS_URL,
                headers=build_headers(jwt),
                json=body,
                proxies=self._proxies,
                verify=False,
                timeout=30,
            )

            if resp.status_code == 401 and attempt == 0:
                self.jwt_manager.refresh()
                continue

            if resp.status_code != 200:
                raise RuntimeError(
                    f"获取会话列表失败: {resp.status_code} {resp.text[:200]}"
                )

            return resp.json()

        raise RuntimeError("多次尝试获取会话列表失败（可能是 cookie 失效，需要重新登录）。")

    def get_session(
        self,
        session_name: str,
        include_answer_details: bool = True,
        include_search_details: bool = True,
    ) -> dict:
        """获取会话详情（对接 Google 官方接口）

        Args:
            session_name: 会话名称（可以是完整路径或简短 ID，会自动提取简短 ID）
            include_answer_details: 是否包含回答详情
            include_search_details: 是否包含搜索详情

        Returns:
            包含会话详情的字典，格式与 Google API 响应一致
        """
        # 从完整路径中提取简短的 session ID
        # 完整格式: collections/default_collection/engines/agentspace-engine/sessions/18399586466940005660
        # 简短格式: 18399586466940005660
        if "/" in session_name:
            session_id = session_name.split("/")[-1]
        else:
            session_id = session_name

        body = {
            "configId": self.group_id,
            "additionalParams": {"token": "-"},
            "getSessionRequest": {
                "name": session_id,
                "includeAnswerDetails": include_answer_details,
                "includeMostRecentSearchResponseDetails": include_search_details,
            },
        }

        for attempt in range(2):
            jwt = self.jwt_manager.get_jwt()
            resp = requests.post(
                GET_SESSION_URL,
                headers=build_headers(jwt),
                json=body,
                proxies=self._proxies,
                verify=False,
                timeout=30,
            )

            if resp.status_code == 401 and attempt == 0:
                self.jwt_manager.refresh()
                continue

            if resp.status_code != 200:
                raise RuntimeError(
                    f"获取会话详情失败: {resp.status_code} {resp.text[:200]}"
                )

            data = resp.json()
            session_data = data.get("session", {})
            full_session_name = session_data.get("name", "")
            logger.debug(f"get_session response session.name: {full_session_name}")
            return data

        raise RuntimeError("多次尝试获取会话详情失败（可能是 cookie 失效，需要重新登录）。")

    def add_context_file(
        self,
        file_name: str,
        file_content: bytes,
        mime_type: str = "text/plain",
        session_name: Optional[str] = None,
    ) -> dict:
        """添加上下文文件到会话

        Args:
            file_name: 文件名
            file_content: 文件内容（bytes）
            mime_type: MIME 类型，默认 text/plain
            session_name: 会话名称，如果不提供则使用当前会话

        Returns:
            包含 fileId 和 tokenCount 的字典
        """
        session = session_name or self.session_name

        # Base64 编码文件内容
        file_content_b64 = base64.b64encode(file_content).decode("utf-8")

        logger.debug(f"add_context_file: session={session}, file_name={file_name}, mime_type={mime_type}, content_size={len(file_content)}")

        body = {
            "configId": self.group_id,
            "additionalParams": {"token": "-"},
            "addContextFileRequest": {
                "name": session,
                "fileName": file_name,
                "mimeType": mime_type,
                "fileContents": file_content_b64,
            },
        }

        for attempt in range(2):
            jwt = self.jwt_manager.get_jwt()
            resp = requests.post(
                ADD_CONTEXT_FILE_URL,
                headers=build_headers(jwt),
                json=body,
                proxies=self._proxies,
                verify=False,
                timeout=60,
            )

            logger.debug(f"add_context_file response: status={resp.status_code}")

            if resp.status_code == 401 and attempt == 0:
                self.jwt_manager.refresh()
                continue

            if resp.status_code != 200:
                logger.debug(f"add_context_file error: {resp.text[:500]}")
                raise RuntimeError(
                    f"添加上下文文件失败: {resp.status_code} {resp.text[:200]}"
                )

            data = resp.json()
            logger.debug(f"add_context_file success: {json.dumps(data, ensure_ascii=False)}")
            add_response = data.get("addContextFileResponse", {})
            return {
                "session": add_response.get("session"),
                "file_id": add_response.get("fileId"),
                "token_count": add_response.get("tokenCount"),
            }

        raise RuntimeError("多次尝试添加上下文文件失败（可能是 cookie 失效，需要重新登录）。")

    def add_context_files(
        self,
        files: List[dict],
        session_name: Optional[str] = None,
    ) -> List[dict]:
        """批量添加上下文文件到会话

        Args:
            files: 文件列表，每个元素为 {"name": str, "content": bytes, "mime_type": str}
            session_name: 会话名称

        Returns:
            文件 ID 列表
        """
        results = []
        for f in files:
            result = self.add_context_file(
                file_name=f["name"],
                file_content=f["content"],
                mime_type=f.get("mime_type", "text/plain"),
                session_name=session_name,
            )
            results.append(result)
        return results

    def _do_stream_assist(
        self,
        message: str,
        session_name: Optional[str] = None,
        model_id: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
    ) -> requests.Response:
        """内部方法：调用 widgetStreamAssist，带简单重试。

        Args:
            message: 用户消息
            session_name: 会话名称
            model_id: 模型 ID
            file_ids: 要包含在请求中的文件 ID 列表
        """
        session = session_name or self.session_name

        for attempt in range(3):
            jwt = self.jwt_manager.get_jwt()
            stream_assist_request = {
                "session": session,
                "query": {"parts": [{"text": message}]},
                "filter": "",
                "fileIds": file_ids or [],
                "answerGenerationMode": "NORMAL",
                "toolsSpec": {
                    "webGroundingSpec": {},
                    "toolRegistry": "default_tool_registry",
                    "imageGenerationSpec": {},
                    "videoGenerationSpec": {},
                },
                "languageCode": "zh-CN",
                "userMetadata": {"timeZone": "Asia/Shanghai"},
                "assistSkippingMode": "REQUEST_ASSIST",
            }

            # 如果指定了模型且不是 auto，添加 assistGenerationConfig
            if model_id and model_id != "auto":
                stream_assist_request["assistGenerationConfig"] = {
                    "modelId": model_id
                }

            body = {
                "configId": self.group_id,
                "additionalParams": {"token": "-"},
                "streamAssistRequest": stream_assist_request,
            }

            resp = requests.post(
                STREAM_ASSIST_URL,
                headers=build_headers(jwt),
                json=body,
                proxies=self._proxies,
                verify=False,
                timeout=120,
                stream=True,
            )

            # JWT 失效，刷新后重试
            if resp.status_code == 401 and attempt == 0:
                self.jwt_manager.refresh()
                continue

            # session 失效，重建 session 后重试一次
            if resp.status_code == 404 and attempt == 0:
                self.reset_session()
                session = self.session_name
                continue

            if resp.status_code != 200:
                raise RuntimeError(
                    f"调用 widgetStreamAssist 失败: {resp.status_code} {resp.text[:200]}"
                )

            return resp

        raise RuntimeError("多次调用 widgetStreamAssist 失败。")

    def _get_session_file_metadata(self, session_name: str, file_ids: Optional[List[str]] = None, filter_str: str = "") -> dict:
        """获取 session 中的文件元数据，包括下载链接

        Args:
            session_name: 会话名称
            file_ids: 可选的文件 ID 列表（目前未使用）
            filter_str: 过滤条件，默认获取所有文件。
                       可选值: "file_origin_type = AI_GENERATED" 只获取 AI 生成的文件
                              "file_origin_type = USER_UPLOADED" 只获取用户上传的文件
        """
        jwt = self.jwt_manager.get_jwt()
        body = {
            "configId": self.group_id,
            "additionalParams": {"token": "-"},
            "listSessionFileMetadataRequest": {
                "name": session_name,
            }
        }

        # 只有非空时才添加 filter
        if filter_str:
            body["listSessionFileMetadataRequest"]["filter"] = filter_str

        resp = requests.post(
            LIST_FILE_METADATA_URL,
            headers=build_headers(jwt),
            json=body,
            proxies=self._proxies,
            verify=False,
            timeout=30,
        )

        if resp.status_code == 401:
            self.jwt_manager.refresh()
            jwt = self.jwt_manager.get_jwt()
            resp = requests.post(
                LIST_FILE_METADATA_URL,
                headers=build_headers(jwt),
                json=body,
                proxies=self._proxies,
                verify=False,
                timeout=30,
            )

        if resp.status_code != 200:
            logger.debug(f"_get_session_file_metadata error: {resp.status_code} {resp.text[:200]}")
            return {}

        data = resp.json()
        logger.debug(f"_get_session_file_metadata response: {json.dumps(data, ensure_ascii=False)[:500]}")

        result = {}
        file_metadata_list = data.get("listSessionFileMetadataResponse", {}).get("fileMetadata", [])
        for fm in file_metadata_list:
            fid = fm.get("fileId")
            if fid:
                result[fid] = fm

        return result

    def list_session_files(self, session_name: Optional[str] = None) -> List[dict]:
        """获取会话中的所有文件（包括用户上传和 AI 生成的）

        Args:
            session_name: 会话名称，如果不提供则使用当前会话

        Returns:
            文件元数据列表
        """
        session = session_name or self.session_name
        metadata = self._get_session_file_metadata(session, filter_str="")
        return list(metadata.values())

    def _build_correct_download_url(self, session_name: str, file_id: str) -> str:
        """构造正确的下载 URL

        元数据中的 downloadUri 格式不正确，需要手动构造：
        - 域名: biz-discoveryengine.googleapis.com (不是 discoveryengine.googleapis.com)
        - 版本: v1alpha (不是 v1)
        - 参数名: fileId (驼峰，不是 file_id 下划线)

        session_name 应该使用从 widgetListSessionFileMetadata 响应中获取的完整路径，
        格式如: projects/xxx/locations/global/collections/default_collection/engines/agentspace-engine/sessions/xxx
        """
        url = f"https://biz-discoveryengine.googleapis.com/v1alpha/{session_name}:downloadFile?fileId={file_id}&alt=media"
        logger.debug(f"构造下载 URL: {url}")
        return url

    def _download_file_with_jwt(self, download_uri: str, session_name: Optional[str] = None, file_id: Optional[str] = None) -> bytes:
        """使用 JWT 认证下载文件

        如果提供了 session_name 和 file_id，会构造正确的下载 URL 而不是使用 download_uri
        """
        # 优先使用正确构造的 URL
        if session_name and file_id:
            url = self._build_correct_download_url(session_name, file_id)
        else:
            url = download_uri

        jwt = self.jwt_manager.get_jwt()
        headers = build_headers(jwt)

        # 允许跟随重定向
        resp = requests.get(
            url,
            headers=headers,
            proxies=self._proxies,
            verify=False,
            timeout=120,
            allow_redirects=True,
        )

        if resp.status_code == 401:
            self.jwt_manager.refresh()
            jwt = self.jwt_manager.get_jwt()
            headers = build_headers(jwt)
            resp = requests.get(
                url,
                headers=headers,
                proxies=self._proxies,
                verify=False,
                timeout=120,
                allow_redirects=True,
            )

        # 如果 JWT 认证失败，尝试使用 cookie 认证
        if resp.status_code == 401:
            resp_data = self._download_file_with_cookie(url)
            return resp_data

        resp.raise_for_status()

        # 检查响应内容是否是 base64 编码的
        # 如果 Content-Type 不是图片类型，或者内容以 base64 PNG 头开始
        content = resp.content
        content_type = resp.headers.get("Content-Type", "")

        # 尝试检测是否为 base64 编码的内容
        # PNG base64 以 iVBORw0KGgo 开头，JPEG 以 /9j/ 开头
        try:
            text_content = content.decode("utf-8", errors="ignore").strip()
            if text_content.startswith("iVBORw0KGgo") or text_content.startswith("/9j/"):
                # 是 base64 编码，需要解码
                return base64.b64decode(text_content)
        except Exception:
            pass

        return content

    def _download_file_with_cookie(self, download_uri: str) -> bytes:
        """使用 cookie 认证下载文件"""
        secure_c_ses = self.config.get("secure_c_ses")
        host_c_oses = self.config.get("host_c_oses")

        cookie_str = f"__Secure-C_SES={secure_c_ses}"
        if host_c_oses:
            cookie_str += f"; __Host-C_OSES={host_c_oses}"

        headers = {
            "accept": "*/*",
            "cookie": cookie_str,
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
        }

        resp = requests.get(
            download_uri,
            headers=headers,
            proxies=self._proxies,
            verify=False,
            timeout=120,
        )

        resp.raise_for_status()
        return resp.content

    def chat_full(
        self,
        message: str,
        session_name: Optional[str] = None,
        include_thoughts: bool = False,
        auto_save_images: bool = True,
        debug: bool = False,
        model_id: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
    ) -> ChatResponse:
        """发送一条消息，返回包含文本和图片的完整响应。

        Args:
            message: 用户消息
            session_name: 会话名称
            include_thoughts: 是否包含思考链
            auto_save_images: 是否自动保存图片
            debug: 是否开启调试模式
            model_id: 模型 ID
            file_ids: 要包含在请求中的文件 ID 列表（用于引用已上传的文件）
        """
        resp = self._do_stream_assist(message, session_name, model_id, file_ids)

        full_response = ""
        for line in resp.iter_lines():
            if line:
                full_response += line.decode("utf-8") + "\n"

        result = ChatResponse()

        try:
            data_list = json.loads(full_response)
        except json.JSONDecodeError:
            # 解析失败时，直接返回原始文本
            result.text = full_response
            return result

        # 调试模式：打印完整响应
        if debug:
            logger.debug("完整 API 响应:")
            logger.debug(json.dumps(data_list, indent=2, ensure_ascii=False))

        texts: list[str] = []
        file_ids: list[dict] = []  # 收集需要下载的文件 {fileId, mimeType}
        current_session: Optional[str] = None
        processed_file_ids: set = set()  # 用于去重，避免同一张图片被添加多次

        for data in data_list:
            sar = data.get("streamAssistResponse")
            if not sar:
                continue

            # 获取 session 信息
            session_info = sar.get("sessionInfo", {})
            if session_info.get("session"):
                current_session = session_info["session"]

            # 检查顶层的 generatedImages
            top_gen_images = sar.get("generatedImages") or []
            for gen_img in top_gen_images:
                self._parse_generated_image(gen_img, result, auto_save_images)

            answer = sar.get("answer") or {}
            answer_state = answer.get("state") or sar.get("state")
            skipped_reasons = answer.get("assistSkippedReasons") or sar.get("assistSkippedReasons") or []
            policy_result = answer.get("customerPolicyEnforcementResult") or sar.get("customerPolicyEnforcementResult") or {}

            # 检查 answer 级别的 generatedImages
            answer_gen_images = answer.get("generatedImages") or []
            for gen_img in answer_gen_images:
                self._parse_generated_image(gen_img, result, auto_save_images)

            replies = answer.get("replies") or []

            # 处理被策略阻断的情况：无回复但 state=SKIPPED
            if (answer_state == "SKIPPED" or skipped_reasons) and not replies:
                violation_detail = None
                if "CUSTOMER_POLICY_VIOLATION" in skipped_reasons:
                    for pr in policy_result.get("policyResults") or []:
                        armor = pr.get("modelArmorEnforcementResult") or {}
                        violation_detail = armor.get("modelArmorViolation")
                        if violation_detail:
                            break
                if "CUSTOMER_POLICY_VIOLATION" in skipped_reasons:
                    msg = "由于提示违反了您组织定义的安全政策，因此 Gemini Enterprise 无法回复。"
                    if violation_detail:
                        msg += f"（原因: {violation_detail}）"
                elif skipped_reasons:
                    msg = f"Gemini Enterprise 未能生成回复（原因: {', '.join(skipped_reasons)}）"
                else:
                    msg = f"Gemini Enterprise 未能生成回复（状态: {answer_state or '未知'}）"

                texts.append(msg)
                continue

            for reply in replies:
                # 检查 reply 级别的 generatedImages
                reply_gen_images = reply.get("generatedImages") or []
                for gen_img in reply_gen_images:
                    self._parse_generated_image(gen_img, result, auto_save_images)

                gc = reply.get("groundedContent", {})
                content = gc.get("content", {})
                text = content.get("text", "")
                thought = content.get("thought", False)

                # 检查 file 字段（图片生成的关键）
                file_info = content.get("file")
                if file_info and file_info.get("fileId"):
                    fid = file_info["fileId"]
                    # 只收集未处理过的 file_id
                    if fid not in processed_file_ids:
                        file_ids.append({
                            "fileId": fid,
                            "mimeType": file_info.get("mimeType", "image/png")
                        })
                        processed_file_ids.add(fid)

                # 注意：不再调用 _parse_image_from_content，因为图片通过 fileId 处理
                # 这样避免了重复添加图片

                # 检查 attachments
                attachments = reply.get("attachments") or gc.get("attachments") or content.get("attachments") or []
                for att in attachments:
                    self._parse_attachment(att, result, auto_save_images)

                if not text:
                    continue
                if thought:
                    if include_thoughts:
                        result.thoughts.append(text)
                    continue
                texts.append(text)

        # 处理通过 fileId 引用的图片
        if file_ids and current_session and auto_save_images:
            try:
                file_metadata = self._get_session_file_metadata(current_session)
                if debug and file_metadata:
                    logger.debug("文件元数据:")
                    logger.debug(json.dumps(file_metadata, indent=2, ensure_ascii=False))

                for finfo in file_ids:
                    fid = finfo["fileId"]
                    meta = file_metadata.get(fid)
                    if meta:
                        # 使用完整元数据创建 ChatImage
                        img = ChatImage.from_file_metadata(meta)
                        # 获取正确的 session 路径用于构造下载 URL
                        session_path = meta.get("session") or current_session
                        try:
                            # 使用正确的 session_name 和 file_id 构造下载 URL
                            image_data = self._download_file_with_jwt(
                                download_uri="",  # 不使用元数据中的错误 URL
                                session_name=session_path,
                                file_id=fid
                            )
                            save_dir = IMAGE_SAVE_DIR
                            os.makedirs(save_dir, exist_ok=True)
                            filepath = os.path.join(save_dir, img.file_name or f"gemini_{fid}.png")
                            with open(filepath, "wb") as f:
                                f.write(image_data)
                            img.local_path = filepath
                            if debug:
                                logger.debug(f"图片已保存: {filepath}")
                        except Exception as e:
                            if debug:
                                logger.debug(f"下载图片失败: {e}")
                        result.images.append(img)
                    else:
                        # 没有元数据时，使用基本信息创建
                        img = ChatImage(
                            file_id=fid,
                            mime_type=finfo["mimeType"],
                        )
                        result.images.append(img)
            except Exception as e:
                if debug:
                    logger.debug(f"获取文件元数据失败: {e}")

        result.text = "".join(texts)
        return result

    def _parse_generated_image(self, gen_img: dict, result: ChatResponse, auto_save: bool) -> None:
        """解析 generatedImages 中的图片"""
        if not isinstance(gen_img, dict):
            return

        # 尝试多种可能的字段
        img_data = gen_img.get("image", {})

        b64_data = (
            img_data.get("imageBytes") or
            img_data.get("data") or
            img_data.get("bytesBase64Encoded") or
            gen_img.get("imageBytes") or
            gen_img.get("bytesBase64Encoded")
        )

        url = (
            img_data.get("uri") or
            img_data.get("imageUrl") or
            img_data.get("url") or
            gen_img.get("uri") or
            gen_img.get("imageUrl") or
            gen_img.get("url")
        )

        mime = img_data.get("mimeType") or gen_img.get("mimeType", "image/png")

        if b64_data or url:
            img = ChatImage(base64_data=b64_data, url=url, mime_type=mime)
            if auto_save and (img.base64_data or img.url):
                try:
                    img.save_to_file()
                except Exception:
                    pass
            result.images.append(img)

    def _parse_image_from_content(self, content: dict, result: ChatResponse, auto_save: bool) -> None:
        """从 content 对象中解析图片"""
        if not isinstance(content, dict):
            return

        # inlineData
        inline_data = content.get("inlineData")
        if inline_data:
            img = ChatImage(
                base64_data=inline_data.get("data"),
                mime_type=inline_data.get("mimeType", "image/png"),
            )
            if auto_save and img.base64_data:
                try:
                    img.save_to_file()
                except Exception:
                    pass
            result.images.append(img)

        # imageUrl / uri
        image_url = content.get("imageUrl") or content.get("uri") or content.get("url")
        if image_url:
            img = ChatImage(url=image_url)
            if auto_save:
                try:
                    img.save_to_file()
                except Exception:
                    pass
            result.images.append(img)

        # parts 数组
        parts = content.get("parts") or []
        for part in parts:
            if isinstance(part, dict):
                p_inline = part.get("inlineData")
                if p_inline:
                    img = ChatImage(
                        base64_data=p_inline.get("data"),
                        mime_type=p_inline.get("mimeType", "image/png"),
                    )
                    if auto_save and img.base64_data:
                        try:
                            img.save_to_file()
                        except Exception:
                            pass
                    result.images.append(img)

                p_url = part.get("imageUrl") or part.get("uri") or part.get("fileData", {}).get("fileUri")
                if p_url:
                    img = ChatImage(url=p_url)
                    if auto_save:
                        try:
                            img.save_to_file()
                        except Exception:
                            pass
                    result.images.append(img)

    def _parse_attachment(self, att: dict, result: ChatResponse, auto_save: bool) -> None:
        """解析 attachments 中的图片"""
        if not isinstance(att, dict):
            return

        # 检查是否是图片类型
        mime_type = att.get("mimeType", "")
        if not mime_type.startswith("image/"):
            return

        b64_data = att.get("data") or att.get("bytesBase64Encoded") or att.get("imageBytes")
        url = att.get("uri") or att.get("url") or att.get("imageUrl")

        if b64_data or url:
            img = ChatImage(base64_data=b64_data, url=url, mime_type=mime_type)
            if auto_save and (img.base64_data or img.url):
                try:
                    img.save_to_file()
                except Exception:
                    pass
            result.images.append(img)

    def chat(
        self,
        message: str,
        session_name: Optional[str] = None,
        include_thoughts: bool = False,
        model_id: Optional[str] = None,
    ) -> str:
        """发送一条消息，返回完整回复文本（兼容旧接口）。"""
        response = self.chat_full(
            message,
            session_name=session_name,
            include_thoughts=include_thoughts,
            auto_save_images=True,
            model_id=model_id,
        )
        return str(response)

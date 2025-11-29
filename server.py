"""FastAPI 后端服务，提供 OpenAI 兼容 API 和前端页面"""
import json
import os
import time
import uuid
from typing import List, Optional, Union

from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from biz_gemini.auth import JWTManager, check_session_status
from biz_gemini.biz_client import BizGeminiClient
from biz_gemini.config import cookies_age_seconds, cookies_expired, load_config, reload_config, save_config, get_proxy
from biz_gemini.openai_adapter import OpenAICompatClient
from biz_gemini.anthropic_adapter import AnthropicCompatClient
from biz_gemini.web_login import get_login_service
from biz_gemini.remote_browser import get_browser_service, BrowserSessionStatus
from biz_gemini.keep_alive import get_keep_alive_service
from config_watcher import start_config_watcher, stop_config_watcher

app = FastAPI(title="Gemini Chat API", version="1.0.0")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件目录
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# 图片存储目录
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "biz_gemini_images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# 会话管理：存储多个对话
sessions: dict[str, dict] = {}

# 配置热重载回调
def on_config_changed(new_config: dict):
    """配置变更时的回调函数"""
    print("[*] 配置已更新，部分配置将在下次请求时生效")
    # 可以在这里添加其他配置变更处理逻辑
    # 例如：更新代理配置、刷新客户端等

# 应用生命周期事件
@app.on_event("startup")
async def startup_event():
    """应用启动时执行"""
    print("[*] 启动配置文件监控...")
    start_config_watcher(callback=on_config_changed)
    print("[+] 配置监控已启动")

    # 启动 Session 保活服务
    print("[*] 启动 Session 保活服务...")
    keep_alive = get_keep_alive_service(interval_minutes=10)
    await keep_alive.start()
    print("[+] Session 保活服务已启动（每 10 分钟检查一次）")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    print("[*] 停止 Session 保活服务...")
    keep_alive = get_keep_alive_service()
    await keep_alive.stop()
    print("[+] Session 保活服务已停止")

    print("[*] 停止配置文件监控...")
    stop_config_watcher()
    print("[+] 配置监控已停止")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "business-gemini"
    messages: List[Message]
    stream: bool = False
    session_id: Optional[str] = None
    include_image_data: bool = True
    include_thoughts: bool = False  # 是否返回思考链
    embed_images_in_content: bool = True  # 是否将图片内嵌到 content（OpenAI 兼容模式）


# Anthropic Messages API 请求模型
class AnthropicContentBlock(BaseModel):
    type: str
    text: Optional[str] = None
    # 图片支持
    source: Optional[dict] = None


class AnthropicMessage(BaseModel):
    role: str
    content: Union[str, List[AnthropicContentBlock]]


class AnthropicRequest(BaseModel):
    """Anthropic Messages API 请求格式"""
    model: str = "gemini-2.5-pro"
    max_tokens: int = 4096
    messages: List[AnthropicMessage]
    system: Optional[str] = None
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    stop_sequences: Optional[List[str]] = None
    metadata: Optional[dict] = None


class SessionInfo(BaseModel):
    session_id: str
    title: str
    created_at: float
    message_count: int


def get_or_create_client(session_id: str) -> tuple[OpenAICompatClient, dict]:
    """获取或创建指定会话的客户端"""
    if session_id not in sessions:
        config = load_config()
        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)
        client = OpenAICompatClient(biz_client)
        # 创建 Gemini 会话并获取 session_name
        session_name = biz_client.session_name
        sessions[session_id] = {
            "client": client,
            "biz_client": biz_client,
            "session_name": session_name,
            "messages": [],
            "title": "新对话",
            "created_at": time.time(),
        }
    return sessions[session_id]["client"], sessions[session_id]


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回前端页面"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>请先创建 static/index.html</h1>")


@app.get("/api/status")
async def get_status():
    """获取登录状态（通过 list-sessions 检查真实的 session 状态）"""
    try:
        config = load_config()

        has_credentials = all([
            config.get("secure_c_ses"),
            config.get("csesidx"),
            config.get("group_id"),
        ])

        if not has_credentials:
            return {
                "logged_in": False,
                "message": "未登录，请运行 python app.py login",
            }

        # 获取保活服务的状态（如果可用）
        keep_alive = get_keep_alive_service()
        keep_alive_status = keep_alive.get_status()

        # 如果保活服务已经检查过 session，使用缓存的结果
        if keep_alive_status.get("last_check") and keep_alive_status.get("session_valid") is not None:
            session_valid = keep_alive_status["session_valid"]
            session_username = keep_alive_status.get("session_username")

            if not session_valid:
                return {
                    "logged_in": False,
                    "expired": True,
                    "message": "登录已过期，请重新运行 python app.py login",
                }

            return {
                "logged_in": True,
                "session_valid": True,
                "username": session_username,
                "last_check": keep_alive_status.get("last_check"),
                "message": f"已登录: {session_username}" if session_username else "已登录",
            }

        # 如果保活服务还没有检查过，主动检查一次
        session_status = check_session_status(config)

        if session_status.get("expired", False):
            return {
                "logged_in": False,
                "expired": True,
                "message": "登录已过期，请重新运行 python app.py login",
                "debug": session_status,  # 添加调试信息
            }

        if session_status.get("error"):
            return {
                "logged_in": True,
                "warning": True,
                "message": f"检查状态失败: {session_status['error']}",
                "debug": session_status,  # 添加调试信息
            }

        return {
            "logged_in": True,
            "session_valid": session_status.get("valid", True),
            "username": session_status.get("username"),
            "message": f"已登录: {session_status.get('username')}" if session_status.get("username") else "已登录",
        }

    except Exception as e:
        return {
            "logged_in": False,
            "error": str(e),
        }


@app.get("/api/debug/session-status")
async def debug_session_status():
    """调试端点：查看 list-sessions 原始返回"""
    import httpx
    from biz_gemini.config import get_proxy

    config = load_config()
    secure_c_ses = config.get("secure_c_ses")
    host_c_oses = config.get("host_c_oses")
    nid = config.get("nid")
    csesidx = config.get("csesidx")

    if not secure_c_ses or not csesidx:
        return {"error": "缺少凭证"}

    cookie_str = f"__Secure-C_SES={secure_c_ses}"
    if host_c_oses:
        cookie_str += f"; __Host-C_OSES={host_c_oses}"
    if nid:
        cookie_str += f"; NID={nid}"

    url = f"https://auth.business.gemini.google/list-sessions?csesidx={csesidx}&rt=json"
    proxy = get_proxy(config)

    try:
        client_kwargs = {"verify": False, "timeout": 30.0}
        if proxy:
            client_kwargs["proxy"] = proxy

        with httpx.Client(**client_kwargs) as client:
            resp = client.get(
                url,
                headers={
                    "accept": "*/*",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "origin": "https://business.gemini.google",
                    "referer": "https://business.gemini.google/",
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-site",
                    "cookie": cookie_str,
                },
            )

        text = resp.text
        raw_text = text[:500]  # 保存原始文本前500字符用于调试
        if text.startswith(")]}'"):
            text = text[4:].strip()

        import json
        data = json.loads(text)

        # 检查 session 状态
        sessions = data.get("sessions", [])
        matched_session = None
        csesidx_str = str(csesidx)
        for sess in sessions:
            if str(sess.get("csesidx", "")) == csesidx_str:
                matched_session = sess
                break

        return {
            "http_status": resp.status_code,
            "proxy_used": proxy,
            "csesidx_in_config": csesidx,
            "csesidx_type": type(csesidx).__name__,
            "raw_text_preview": raw_text,
            "raw_response": data,
            "sessions_count": len(sessions),
            "matched_session": matched_session,
            "first_session_csesidx": sessions[0].get("csesidx") if sessions else None,
            "first_session_csesidx_type": type(sessions[0].get("csesidx")).__name__ if sessions else None,
            "check_result": check_session_status(config),
        }
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}


@app.post("/api/config/reload")
async def reload_config_endpoint():
    """手动重载配置"""
    try:
        new_config = reload_config()
        print("[+] 配置手动重载成功")
        return {
            "success": True,
            "message": "配置重载成功",
            "config": {
                "server": new_config.get("server", {}),
                "proxy_enabled": new_config.get("proxy", {}).get("enabled", False) if isinstance(new_config.get("proxy"), dict) else bool(new_config.get("proxy")),
            },
        }
    except Exception as e:
        print(f"[!] 配置重载失败: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@app.post("/api/login/start")
async def start_login(headless: bool = False):
    """启动浏览器登录流程"""
    try:
        login_service = get_login_service()
        task = await login_service.start_login(headless=headless)
        return {
            "success": True,
            "task": task.to_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/login/status")
async def get_login_status(task_id: Optional[str] = None):
    """获取登录状态"""
    login_service = get_login_service()
    
    if task_id:
        task = await login_service.get_task(task_id)
    else:
        # 获取最新任务
        task = await login_service.get_latest_task()
    
    if not task:
        return {
            "success": False,
            "message": "未找到登录任务",
        }
    
    return {
        "success": True,
        "task": task.to_dict(),
    }


@app.post("/api/login/cancel")
async def cancel_login(task_id: str):
    """取消登录流程"""
    login_service = get_login_service()
    success = await login_service.cancel_task(task_id)
    
    if success:
        return {
            "success": True,
            "message": "登录已取消",
        }
    else:
        return {
            "success": False,
            "message": "任务不存在或无法取消",
        }



@app.get("/api/sessions")
async def list_sessions():
    """列出所有会话 - 对接 Google 官方接口"""
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            # 未登录时返回本地缓存的会话
            result = []
            for sid, data in sessions.items():
                result.append({
                    "session_id": sid,
                    "title": data.get("title", "新对话"),
                    "created_at": data.get("created_at", 0),
                    "message_count": len(data.get("messages", [])),
                })
            result.sort(key=lambda x: x["created_at"], reverse=True)
            return result

        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)

        # 调用 Google 官方接口
        api_response = biz_client.list_sessions()
        sessions_response = api_response.get("listSessionsResponse", {})
        google_sessions = sessions_response.get("sessions", [])

        result = []
        for sess in google_sessions:
            # 从 session name 中提取 session_id
            # 格式: collections/default_collection/engines/agentspace-engine/sessions/11281810546812518810
            session_name = sess.get("name", "")
            session_id = session_name.split("/")[-1] if "/" in session_name else session_name

            # 获取 displayName 作为标题
            title = sess.get("displayName", "")
            if not title:
                # 如果没有 displayName，使用第一条消息的文本
                turns = sess.get("turns", [])
                if turns:
                    first_query = turns[0].get("query", {})
                    title = first_query.get("text", "新对话")[:30]

            # 解析时间
            start_time = sess.get("startTime", "")
            created_at = 0
            if start_time:
                try:
                    from datetime import datetime
                    # 处理 ISO 格式时间
                    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    created_at = dt.timestamp()
                except Exception:
                    pass

            # 获取消息数
            turns = sess.get("turns", [])
            message_count = len(turns) * 2  # 每个 turn 包含问和答

            result.append({
                "session_id": session_id,
                "session_name": session_name,  # 完整的 session name，删除时需要
                "title": title,
                "created_at": created_at,
                "message_count": message_count,
                "state": sess.get("state", ""),
                "labels": sess.get("labels", []),
            })

        return result

    except Exception as e:
        # 出错时返回本地缓存
        print(f"获取会话列表失败，返回本地缓存: {e}")
        result = []
        for sid, data in sessions.items():
            result.append({
                "session_id": sid,
                "title": data.get("title", "新对话"),
                "created_at": data.get("created_at", 0),
                "message_count": len(data.get("messages", [])),
            })
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return result


@app.post("/api/sessions")
async def create_session():
    """创建新会话"""
    session_id = str(uuid.uuid4())
    return {"session_id": session_id}


@app.delete("/api/sessions/{session_id:path}")
async def delete_session(session_id: str):
    """删除会话 - 支持 session_id 或完整的 session_name"""
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]

        if not missing:
            jwt_manager = JWTManager(config=config)
            biz_client = BizGeminiClient(config, jwt_manager)

            # 判断是完整的 session_name 还是简短的 session_id
            # 完整格式: collections/default_collection/engines/agentspace-engine/sessions/xxx
            if "/" in session_id:
                session_name = session_id
            else:
                # 需要从会话列表中找到完整的 session_name
                # 或者尝试构造（如果知道格式）
                session_name = session_id

            try:
                biz_client.delete_session(session_name)
            except Exception as e:
                print(f"删除 Google 会话失败: {e}")

        # 同时删除本地缓存
        # 从本地 sessions 中查找匹配的会话
        to_delete = []
        for sid, data in sessions.items():
            if sid == session_id or data.get("session_name") == session_id:
                to_delete.append(sid)
            elif session_id.endswith(sid):  # session_name 以 session_id 结尾
                to_delete.append(sid)

        for sid in to_delete:
            del sessions[sid]

        return {"success": True}
    except Exception as e:
        print(f"删除会话时出错: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, session_name: str = None):
    """获取会话消息历史 - 优先本地存储，其次调用 Google API"""
    # 1. 优先从本地存储获取
    if session_id in sessions:
        local_messages = sessions[session_id].get("messages", [])
        if local_messages:
            return {"messages": local_messages}

    # 2. 本地没有，尝试从 Google API 获取
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            return {"messages": []}

        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)

        # 确定 session_name：可能是完整路径或简短 ID
        # 如果传入了 session_name 参数则直接使用
        # 否则尝试从会话列表中查找
        target_session_name = session_name
        if not target_session_name:
            # 尝试用 session_id 直接作为 name（适用于简短 ID）
            target_session_name = session_id

        # 调用 Google API 获取会话详情
        api_response = biz_client.get_session(target_session_name)
        session_data = api_response.get("session", {})
        turns = session_data.get("turns", [])

        # 获取完整的 session name（用于构造图片下载 URL）
        full_session_name = session_data.get("name", "")

        messages = []
        # 收集所有图片的 fileId，稍后批量获取元数据
        all_file_ids = []

        for turn in turns:
            # 解析用户消息
            query = turn.get("query", {})
            query_text = query.get("text", "")
            if query_text:
                messages.append({
                    "role": "user",
                    "content": query_text,
                    "timestamp": turn.get("createdAt", ""),
                })

            # 解析 AI 回复 - 优先使用 detailedAssistAnswer
            detailed_answer = turn.get("detailedAssistAnswer", {})
            replies = detailed_answer.get("replies", [])

            if replies:
                # 分别收集文本、思考和图片
                reply_texts = []
                thoughts = []
                turn_file_ids = []

                for reply in replies:
                    grounded_content = reply.get("groundedContent", {})
                    content = grounded_content.get("content", {})
                    text = content.get("text", "")
                    is_thought = content.get("thought", False)

                    # 检查是否有图片文件
                    file_info = content.get("file")
                    if file_info and file_info.get("fileId"):
                        file_id = file_info["fileId"]
                        turn_file_ids.append({
                            "fileId": file_id,
                            "mimeType": file_info.get("mimeType", "image/png"),
                            "fileName": file_info.get("name", ""),
                        })
                        all_file_ids.append(file_id)

                    if text:
                        if is_thought:
                            thoughts.append(text)
                        else:
                            reply_texts.append(text)

                if reply_texts or turn_file_ids:
                    msg_data = {
                        "role": "assistant",
                        "content": "\n\n".join(reply_texts) if reply_texts else "",
                        "timestamp": turn.get("createdAt", ""),
                    }

                    # 添加思考链
                    if thoughts:
                        msg_data["thoughts"] = thoughts

                    # 添加图片信息（稍后填充完整元数据）
                    if turn_file_ids:
                        msg_data["images"] = turn_file_ids

                    messages.append(msg_data)

        # 批量获取图片元数据并更新 messages
        if all_file_ids and full_session_name:
            try:
                file_metadata = biz_client._get_session_file_metadata(full_session_name)

                # 从 full_session_name 提取 session_id 用于构造 URL
                # 格式: projects/.../sessions/17970885850102128104
                api_session_id = full_session_name.split("/")[-1] if "/" in full_session_name else session_id

                # 更新每条消息中的图片信息
                for msg in messages:
                    if "images" in msg:
                        enriched_images = []
                        for img_info in msg["images"]:
                            file_id = img_info["fileId"]
                            meta = file_metadata.get(file_id, {})

                            # 构建完整的图片信息
                            local_filename = meta.get("name") or img_info.get("fileName", "") or f"gemini_{file_id}.png"
                            enriched_img = {
                                "file_id": file_id,
                                "file_name": local_filename,
                                "mime_type": meta.get("mimeType") or img_info.get("mimeType", "image/png"),
                                "byte_size": meta.get("byteSize"),
                                "session": meta.get("session") or full_session_name,
                            }

                            # 检查本地是否有缓存
                            local_path = os.path.join(IMAGES_DIR, local_filename)
                            if os.path.exists(local_path):
                                enriched_img["local_path"] = local_path
                                enriched_img["url"] = f"/api/images/{local_filename}"
                            else:
                                # 本地没有缓存，提供按需下载的 URL
                                enriched_img["url"] = f"/api/sessions/{api_session_id}/images/{file_id}?session_name={full_session_name}"

                            enriched_images.append(enriched_img)

                        msg["images"] = enriched_images
            except Exception as e:
                print(f"获取图片元数据失败: {e}")

        return {"messages": messages}

    except Exception as e:
        print(f"从 Google API 获取会话消息失败: {e}")
        return {"messages": []}


@app.put("/api/sessions/{session_id}/title")
async def update_session_title(session_id: str, title: str):
    """更新会话标题"""
    if session_id in sessions:
        sessions[session_id]["title"] = title
    return {"success": True}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    """OpenAI 兼容的聊天接口"""
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail=f"配置缺失: {', '.join(missing)}，请先登录")

        # 检查保活服务的 session 状态（如果可用）
        keep_alive = get_keep_alive_service()
        keep_alive_status = keep_alive.get_status()
        if keep_alive_status.get("last_check") and not keep_alive_status.get("session_valid", True):
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    session_id = request.session_id or str(uuid.uuid4())
    client, session_data = get_or_create_client(session_id)

    # 保存用户消息
    user_message = request.messages[-1] if request.messages else None
    if user_message:
        session_data["messages"].append({
            "role": user_message.role,
            "content": user_message.content,
            "timestamp": time.time(),
        })
        # 更新会话标题（使用第一条消息）
        if len(session_data["messages"]) == 1:
            title = user_message.content[:30]
            if len(user_message.content) > 30:
                title += "..."
            session_data["title"] = title

    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    if request.stream:
        async def generate():
            try:
                response = client.chat.completions.create(
                    model=request.model,
                    messages=messages,
                    stream=True,
                    include_image_data=request.include_image_data,
                    include_thoughts=request.include_thoughts,
                    embed_images_in_content=request.embed_images_in_content,
                )
                full_content = ""
                images_data = None
                thoughts_data = []
                for chunk in response:
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    delta_content = delta.get("content", "")
                    delta_thought = delta.get("thought", "")
                    if delta_content:
                        full_content += delta_content
                    if delta_thought:
                        thoughts_data.append(delta_thought)
                    # 检查是否有图片数据
                    if "images" in chunk:
                        images_data = chunk["images"]
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

                # 保存助手回复
                if full_content or images_data or thoughts_data:
                    msg_data = {
                        "role": "assistant",
                        "content": full_content,
                        "timestamp": time.time(),
                    }
                    if images_data:
                        msg_data["images"] = images_data
                    if thoughts_data:
                        msg_data["thoughts"] = thoughts_data
                    session_data["messages"].append(msg_data)
            except Exception as e:
                error_data = {"error": {"message": str(e)}}
                yield f"data: {json.dumps(error_data)}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    else:
        try:
            response = client.chat.completions.create(
                model=request.model,
                messages=messages,
                stream=False,
                include_image_data=request.include_image_data,
                include_thoughts=request.include_thoughts,
                embed_images_in_content=request.embed_images_in_content,
            )

            # 调试日志：打印响应内容
            print(f"[DEBUG] Chat response keys: {response.keys()}")
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"[DEBUG] Content type: {type(content)}, empty: {not content}")
            if "images" in response:
                print(f"[DEBUG] Images count: {len(response.get('images', []))}")
            if "thoughts" in response:
                print(f"[DEBUG] Thoughts count: {len(response.get('thoughts', []))}")

            # 保存助手回复
            msg_data = {
                "role": "assistant",
                "content": content if isinstance(content, str) else json.dumps(content),
                "timestamp": time.time(),
            }
            if "images" in response:
                msg_data["images"] = response["images"]
            if "thoughts" in response:
                msg_data["thoughts"] = response["thoughts"]
            session_data["messages"].append(msg_data)
            response["session_id"] = session_id
            return response
        except Exception as e:
            import traceback
            print(f"[ERROR] Chat completion failed: {e}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/models")
async def list_models():
    """列出可用模型"""
    return {
        "object": "list",
        "data": [
            {"id": "auto", "object": "model", "owned_by": "google", "name": "自动选择", "description": "自动选择最佳模型"},
            {"id": "gemini-2.5-flash", "object": "model", "owned_by": "google", "name": "Gemini 2.5 Flash", "description": "快速响应，适合日常对话"},
            {"id": "gemini-2.5-pro", "object": "model", "owned_by": "google", "name": "Gemini 2.5 Pro", "description": "更强推理能力，适合复杂任务"},
            {"id": "gemini-3-pro-preview", "object": "model", "owned_by": "google", "name": "Gemini 3 Pro Preview", "description": "最新预览版，体验新功能"},
            {"id": "business-gemini", "object": "model", "owned_by": "google", "name": "Business Gemini", "description": "企业版，稳定可靠"},
        ]
    }


# ==================== Anthropic Messages API ====================

@app.post("/v1/messages")
async def anthropic_messages(request: AnthropicRequest):
    """Anthropic Messages API 兼容接口

    支持 Claude Code (Claude CLI) 等使用 Anthropic API 格式的工具。
    请求和响应格式遵循 Anthropic Messages API 规范。
    """
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail=f"配置缺失: {', '.join(missing)}，请先登录")

        # 检查保活服务的 session 状态
        keep_alive = get_keep_alive_service()
        keep_alive_status = keep_alive.get_status()
        if keep_alive_status.get("last_check") and not keep_alive_status.get("session_valid", True):
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

    # 创建客户端
    jwt_manager = JWTManager(config=config)
    biz_client = BizGeminiClient(config, jwt_manager)
    client = AnthropicCompatClient(biz_client)

    # 转换消息格式
    messages = []
    for msg in request.messages:
        if isinstance(msg.content, str):
            messages.append({"role": msg.role, "content": msg.content})
        else:
            # content blocks 格式
            content_blocks = []
            for block in msg.content:
                block_dict = {"type": block.type}
                if block.text is not None:
                    block_dict["text"] = block.text
                if block.source is not None:
                    block_dict["source"] = block.source
                content_blocks.append(block_dict)
            messages.append({"role": msg.role, "content": content_blocks})

    if request.stream:
        async def generate():
            try:
                response_gen = client.messages.create(
                    model=request.model,
                    max_tokens=request.max_tokens,
                    messages=messages,
                    system=request.system,
                    stream=True,
                    temperature=request.temperature,
                    top_p=request.top_p,
                    top_k=request.top_k,
                    stop_sequences=request.stop_sequences,
                )
                for event in response_gen:
                    event_type = event.get("type", "unknown")
                    yield f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                error_event = {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": str(e)
                    }
                }
                yield f"event: error\ndata: {json.dumps(error_event)}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    else:
        try:
            response = client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                messages=messages,
                system=request.system,
                stream=False,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k,
                stop_sequences=request.stop_sequences,
            )
            return response
        except Exception as e:
            import traceback
            print(f"[ERROR] Anthropic messages failed: {e}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件（预留接口）"""
    # TODO: 实现文件上传到 Gemini
    return {
        "success": True,
        "filename": file.filename,
        "content_type": file.content_type,
        "message": "文件上传功能开发中",
    }


@app.get("/api/images/{filename}")
async def get_image(filename: str):
    """提供本地生成图片的访问"""
    # 安全检查：防止路径遍历
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(IMAGES_DIR, safe_filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="图片不存在")

    # 根据文件扩展名确定 MIME 类型
    ext = os.path.splitext(safe_filename)[1].lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(file_path, media_type=media_type)


@app.get("/api/sessions/{session_id}/images/{file_id}")
async def download_session_image(session_id: str, file_id: str, session_name: str = None):
    """通过 session 和 fileId 下载图片（用于历史对话中的图片）

    优先返回本地缓存，如果没有则从 Google 下载并缓存
    """
    try:
        config = load_config()
        missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not config.get(k)]
        if missing:
            raise HTTPException(status_code=401, detail="未登录")

        jwt_manager = JWTManager(config=config)
        biz_client = BizGeminiClient(config, jwt_manager)

        # 确定 session_name
        target_session_name = session_name or session_id

        # 获取文件元数据以确定文件名和 MIME 类型
        file_metadata = biz_client._get_session_file_metadata(target_session_name)
        meta = file_metadata.get(file_id, {})

        file_name = meta.get("name", f"gemini_{file_id}.png")
        mime_type = meta.get("mimeType", "image/png")
        full_session = meta.get("session") or target_session_name

        # 检查本地是否已有缓存（使用实际文件名）
        local_path = os.path.join(IMAGES_DIR, file_name)
        if os.path.exists(local_path):
            return FileResponse(local_path, media_type=mime_type)

        # 也检查 gemini_{file_id} 格式的缓存
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
            alt_path = os.path.join(IMAGES_DIR, f"gemini_{file_id}{ext}")
            if os.path.exists(alt_path):
                alt_mime = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }.get(ext, "image/png")
                return FileResponse(alt_path, media_type=alt_mime)

        # 本地没有缓存，从 Google 下载
        image_data = biz_client._download_file_with_jwt(
            download_uri="",
            session_name=full_session,
            file_id=file_id
        )

        # 保存到本地缓存
        os.makedirs(IMAGES_DIR, exist_ok=True)
        local_path = os.path.join(IMAGES_DIR, file_name)
        with open(local_path, "wb") as f:
            f.write(image_data)

        return FileResponse(local_path, media_type=mime_type)

    except HTTPException:
        raise
    except Exception as e:
        print(f"下载图片失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 远程浏览器登录 ====================

@app.websocket("/ws/browser")
async def browser_websocket(websocket: WebSocket):
    """远程浏览器 WebSocket 端点"""
    await websocket.accept()

    browser_service = get_browser_service()
    session = await browser_service.create_session()

    # 消息发送回调
    async def send_message(message: dict):
        try:
            await websocket.send_json(message)
        except Exception as e:
            print(f"WebSocket 发送消息失败: {e}")

    session.subscribe(send_message)

    try:
        # 只有当会话是 IDLE 状态时才启动浏览器
        # 如果已经在运行，只需订阅消息即可
        from biz_gemini.remote_browser import BrowserSessionStatus
        if session.status == BrowserSessionStatus.IDLE:
            success = await session.start()
            if not success:
                await websocket.close()
                return
        elif session.status == BrowserSessionStatus.RUNNING:
            # 已经在运行，发送当前状态
            await send_message({
                "type": "status",
                "status": session.status.value,
                "message": session.message,
            })
        elif session.status == BrowserSessionStatus.LOGIN_SUCCESS:
            # 已经登录成功，发送状态和配置
            await send_message({
                "type": "status",
                "status": session.status.value,
                "message": session.message,
            })
            config = session.get_login_config()
            if config:
                await send_message({
                    "type": "login_success",
                    "config": config,
                })

        # 处理客户端消息
        while True:
            try:
                data = await websocket.receive_json()
                action = data.get("action")

                if action == "click":
                    x = data.get("x", 0)
                    y = data.get("y", 0)
                    await session.click(x, y)

                elif action == "type":
                    text = data.get("text", "")
                    await session.type_text(text)

                elif action == "key":
                    key = data.get("key", "")
                    await session.press_key(key)

                elif action == "scroll":
                    delta_x = data.get("deltaX", 0)
                    delta_y = data.get("deltaY", 0)
                    await session.scroll(delta_x, delta_y)

                elif action == "navigate":
                    url = data.get("url", "")
                    await session.navigate(url)

                elif action == "stop":
                    break

                elif action == "save_config":
                    # 保存登录配置
                    config = session.get_login_config()
                    if config:
                        save_config(config)
                        await send_message({
                            "type": "config_saved",
                            "success": True,
                            "message": "配置已保存，正在关闭浏览器..."
                        })
                        # 保存成功后自动停止浏览器
                        await session.stop()
                        break
                    else:
                        await send_message({
                            "type": "config_saved",
                            "success": False,
                            "message": "没有可保存的配置"
                        })

            except WebSocketDisconnect:
                break
            except Exception as e:
                print(f"处理 WebSocket 消息错误: {e}")

    finally:
        session.unsubscribe(send_message)
        # 注意：不自动停止浏览器，让用户可以重新连接
        # 浏览器会在登录成功后或用户手动停止时关闭


@app.post("/api/browser/start")
async def start_browser():
    """启动远程浏览器（REST API 方式）"""
    browser_service = get_browser_service()
    session = await browser_service.get_active_session()

    if session and session.status in (BrowserSessionStatus.STARTING, BrowserSessionStatus.RUNNING):
        return {
            "success": True,
            "session_id": session.session_id,
            "status": session.status.value,
            "message": "浏览器已在运行"
        }

    session = await browser_service.create_session()
    return {
        "success": True,
        "session_id": session.session_id,
        "message": "请通过 WebSocket 连接 /ws/browser 进行操作"
    }


@app.post("/api/browser/stop")
async def stop_browser():
    """停止远程浏览器"""
    browser_service = get_browser_service()
    session = await browser_service.get_active_session()

    if session:
        await session.stop()
        return {"success": True, "message": "浏览器已停止"}

    return {"success": False, "message": "没有运行中的浏览器"}


@app.get("/api/browser/status")
async def browser_status():
    """获取远程浏览器状态"""
    browser_service = get_browser_service()
    session = await browser_service.get_active_session()

    if session:
        return {
            "active": True,
            "session_id": session.session_id,
            "status": session.status.value,
            "message": session.message
        }

    return {
        "active": False,
        "message": "没有活跃的浏览器会话"
    }


@app.post("/api/session/update")
async def update_session_config(config: dict):
    """手动更新登录配置（用于手动粘贴 Cookie）

    只需要提供 secure_c_ses 和 group_id，csesidx 会自动获取
    """
    import httpx

    try:
        # 只需要 secure_c_ses 和 group_id
        required_fields = ["secure_c_ses", "group_id"]
        missing = [f for f in required_fields if not config.get(f)]

        if missing:
            return {
                "success": False,
                "error": f"缺少必要字段: {', '.join(missing)}"
            }

        secure_c_ses = config.get("secure_c_ses")
        host_c_oses = config.get("host_c_oses", "")
        nid = config.get("nid", "")

        # 如果没有提供 csesidx，自动从 list-sessions 获取
        csesidx = config.get("csesidx", "")
        if not csesidx:
            # 构造 cookie 字符串
            cookie_str = f"__Secure-C_SES={secure_c_ses}"
            if host_c_oses:
                cookie_str += f"; __Host-C_OSES={host_c_oses}"
            if nid:
                cookie_str += f"; NID={nid}"

            # 调用 list-sessions API 获取 csesidx
            proxy = get_proxy(load_config())
            client_kwargs = {"verify": False, "timeout": 30.0}
            if proxy:
                client_kwargs["proxy"] = proxy

            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    # 先尝试不带 csesidx 参数
                    resp = await client.get(
                        "https://auth.business.gemini.google/list-sessions?rt=json",
                        headers={
                            "accept": "*/*",
                            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            "origin": "https://business.gemini.google",
                            "referer": "https://business.gemini.google/",
                            "cookie": cookie_str,
                        },
                    )

                    if resp.status_code == 200:
                        text = resp.text
                        if text.startswith(")]}'"):
                            text = text[4:].strip()

                        import json
                        data = json.loads(text)
                        sessions_list = data.get("sessions", [])

                        if sessions_list:
                            # 使用第一个（通常是当前活跃的）session 的 csesidx
                            csesidx = str(sessions_list[0].get("csesidx", ""))
                            # 同时尝试获取 NID（如果响应头中有 Set-Cookie）
                            # 注意：NID 通常不在 list-sessions 响应中

                        if not csesidx:
                            return {
                                "success": False,
                                "error": "无法自动获取 csesidx，请手动输入"
                            }
                    else:
                        return {
                            "success": False,
                            "error": f"验证凭证失败: HTTP {resp.status_code}，请检查 Cookie 是否正确"
                        }

            except Exception as e:
                return {
                    "success": False,
                    "error": f"自动获取 csesidx 失败: {str(e)}，请手动输入"
                }

        # 添加保存时间
        from datetime import datetime
        final_config = {
            "secure_c_ses": secure_c_ses,
            "host_c_oses": host_c_oses,
            "nid": nid,
            "csesidx": csesidx,
            "group_id": config.get("group_id"),
            "cookies_saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        save_config(final_config)

        # 清除现有的会话缓存，强制使用新配置
        sessions.clear()

        return {
            "success": True,
            "message": "配置已更新",
            "csesidx": csesidx  # 返回获取到的 csesidx
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ==================== Session 保活服务 ====================

@app.get("/api/keep-alive/status")
async def keep_alive_status():
    """获取保活服务状态"""
    service = get_keep_alive_service()
    return service.get_status()


@app.post("/api/keep-alive/refresh")
async def keep_alive_refresh():
    """手动触发一次 Session 刷新"""
    service = get_keep_alive_service()
    result = await service.refresh_now()
    return result


@app.post("/api/keep-alive/start")
async def keep_alive_start():
    """启动保活服务"""
    service = get_keep_alive_service()
    if service._running:
        return {"success": True, "message": "服务已在运行"}
    await service.start()
    return {"success": True, "message": "服务已启动"}


@app.post("/api/keep-alive/stop")
async def keep_alive_stop():
    """停止保活服务"""
    service = get_keep_alive_service()
    if not service._running:
        return {"success": True, "message": "服务未运行"}
    await service.stop()
    return {"success": True, "message": "服务已停止"}


# 挂载静态文件
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("Gemini Chat 服务器启动")
    print("访问地址: http://localhost:8000")
    print("API 端点:")
    print("  - POST /v1/chat/completions (OpenAI 兼容)")
    print("  - POST /v1/messages (Anthropic 兼容, Claude Code)")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)

import asyncio
import os
import platform
import subprocess
from typing import Optional, Protocol

from biz_gemini.auth import JWTManager, login_via_browser
from biz_gemini.biz_client import BizGeminiClient, ChatResponse
from biz_gemini.config import load_config, cookies_expired


def open_image(filepath: str) -> bool:
    """使用系统默认程序打开图片"""
    try:
        if platform.system() == "Windows":
            os.startfile(filepath)
        elif platform.system() == "Darwin":  # macOS
            subprocess.run(["open", filepath], check=True)
        else:  # Linux
            subprocess.run(["xdg-open", filepath], check=True)
        return True
    except Exception:
        return False


class ChatBackend(Protocol):
    def send(self, message: str) -> str: ...
    def send_full(self, message: str) -> ChatResponse: ...
    def reset(self) -> None: ...
    def set_include_thoughts(self, value: bool) -> None: ...


class BizGeminiChatBackend:
    """适配 BizGeminiClient 到通用 ChatBackend 接口，用于 CLI。"""

    def __init__(self, config: dict, include_thoughts: bool = False):
        self.config = config
        self.include_thoughts = include_thoughts
        self.debug = False
        self.jwt_manager = JWTManager(config)
        self.client = BizGeminiClient(config, self.jwt_manager)

    def send(self, message: str) -> str:
        return self.client.chat(message, include_thoughts=self.include_thoughts)

    def send_full(self, message: str) -> ChatResponse:
        return self.client.chat_full(message, include_thoughts=self.include_thoughts, debug=self.debug)

    def reset(self) -> None:
        self.client.reset_session()

    def set_include_thoughts(self, value: bool) -> None:
        self.include_thoughts = value

    def set_debug(self, value: bool) -> None:
        self.debug = value


def check_login_status() -> tuple[bool, Optional[dict], str]:
    """检查登录状态，返回 (是否已登录, 配置, 状态消息)"""
    cfg = load_config()

    # 检查必要字段
    missing = [k for k in ("secure_c_ses", "csesidx", "group_id") if not cfg.get(k)]
    if missing:
        return False, None, f"配置缺失字段: {', '.join(missing)}"

    # 检查 cookie 是否过期
    if cookies_expired(cfg, max_age_hours=24):
        return False, cfg, "登录信息已超过 24 小时，建议重新登录"

    return True, cfg, "已登录"


def run_cli() -> None:
    """运行命令行交互界面"""
    print("=" * 60)
    print("Business Gemini 命令行对话")
    print("输入 /help 查看所有命令")
    print("=" * 60)

    # 检查登录状态
    logged_in, cfg, status_msg = check_login_status()
    backend: Optional[BizGeminiChatBackend] = None
    show_thinking = False
    auto_open_images = True  # 默认自动打开图片
    debug_mode = False  # 调试模式

    if logged_in and cfg:
        try:
            backend = BizGeminiChatBackend(cfg, include_thoughts=show_thinking)
            print(f"[*] {status_msg}")
        except Exception as e:
            print(f"[!] 初始化失败: {e}")
            logged_in = False
    else:
        print(f"[!] {status_msg}")
        print("[*] 请输入 /login 进行登录")

    while True:
        try:
            user_input = input("\nYou > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[*] 再见")
            return

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd_parts = user_input[1:].strip().split(maxsplit=1)
            cmd = cmd_parts[0].lower() if cmd_parts else ""
            cmd_arg = cmd_parts[1] if len(cmd_parts) > 1 else ""

            # 退出命令
            if cmd in {"exit", "quit", "q"}:
                print("[*] 已退出")
                return

            # 登录命令
            if cmd == "login":
                print("[*] 正在启动浏览器登录...")
                try:
                    asyncio.run(login_via_browser())
                    # 重新检查登录状态
                    logged_in, cfg, status_msg = check_login_status()
                    if logged_in and cfg:
                        backend = BizGeminiChatBackend(cfg, include_thoughts=show_thinking)
                        print("[+] 登录成功，可以开始对话了")
                    else:
                        print(f"[!] 登录后状态异常: {status_msg}")
                except Exception as e:
                    print(f"[!] 登录失败: {e}")
                continue

            # 重置会话命令
            if cmd in {"new", "reset"}:
                if backend:
                    backend.reset()
                    print("[*] 已重置会话")
                else:
                    print("[!] 尚未登录，请先输入 /login 进行登录")
                continue

            # 显示思考链命令
            if cmd == "showthinking":
                arg = cmd_arg.lower()
                if arg in {"on", "true", "1", "yes"}:
                    show_thinking = True
                    if backend:
                        backend.set_include_thoughts(True)
                    print("[*] 已开启思考链显示")
                elif arg in {"off", "false", "0", "no"}:
                    show_thinking = False
                    if backend:
                        backend.set_include_thoughts(False)
                    print("[*] 已关闭思考链显示")
                else:
                    status = "开启" if show_thinking else "关闭"
                    print(f"[*] 当前思考链显示状态: {status}")
                    print("[*] 用法: /showthinking on 或 /showthinking off")
                continue

            # 自动打开图片命令
            if cmd == "openimage":
                arg = cmd_arg.lower()
                if arg in {"on", "true", "1", "yes"}:
                    auto_open_images = True
                    print("[*] 已开启自动打开图片")
                elif arg in {"off", "false", "0", "no"}:
                    auto_open_images = False
                    print("[*] 已关闭自动打开图片")
                else:
                    status = "开启" if auto_open_images else "关闭"
                    print(f"[*] 当前自动打开图片状态: {status}")
                    print("[*] 用法: /openimage on 或 /openimage off")
                continue

            # 调试模式命令
            if cmd == "debug":
                arg = cmd_arg.lower()
                if arg in {"on", "true", "1", "yes"}:
                    debug_mode = True
                    if backend:
                        backend.set_debug(True)
                    print("[*] 已开启调试模式，将显示完整 API 响应")
                elif arg in {"off", "false", "0", "no"}:
                    debug_mode = False
                    if backend:
                        backend.set_debug(False)
                    print("[*] 已关闭调试模式")
                else:
                    status = "开启" if debug_mode else "关闭"
                    print(f"[*] 当前调试模式状态: {status}")
                    print("[*] 用法: /debug on 或 /debug off")
                continue

            # 帮助命令
            if cmd in {"help", "h", "?"}:
                print("可用命令：")
                print("  /login           启动浏览器进行登录")
                print("  /new             重置会话")
                print("  /showthinking    切换思考链显示 (on/off)")
                print("  /openimage       切换自动打开图片 (on/off)")
                print("  /debug           切换调试模式 (on/off) - 显示完整 API 响应")
                print("  /exit            退出程序")
                print("  /help            显示帮助")
                continue

            print(f"[!] 未知命令: /{cmd}，输入 /help 查看帮助")
            continue

        # 聊天消息
        if not logged_in or not backend:
            print("[!] 尚未登录，请先输入 /login 进行登录")
            continue

        print("Gemini >", end=" ", flush=True)
        try:
            response = backend.send_full(user_input)
        except Exception as e:
            print(f"\n[!] 调用失败: {e}")
            # 检查是否是登录问题
            if "cookie" in str(e).lower() or "401" in str(e) or "登录" in str(e):
                print("[*] 可能需要重新登录，请输入 /login")
                logged_in = False
            continue

        # 输出文本响应
        output_parts = []
        for thought in response.thoughts:
            output_parts.append(f"[思考] {thought}")
        if response.text:
            output_parts.append(response.text)

        if output_parts:
            print("\n".join(output_parts))

        # 处理图片
        if response.images:
            for i, img in enumerate(response.images, 1):
                if img.local_path:
                    print(f"[图片 {i}] 已保存: {img.local_path}")
                    if auto_open_images:
                        if open_image(img.local_path):
                            print(f"[图片 {i}] 已在默认程序中打开")
                        else:
                            print(f"[图片 {i}] 无法自动打开，请手动查看")
                elif img.url:
                    print(f"[图片 {i}] URL: {img.url}")


def main() -> None:
    run_cli()


if __name__ == "__main__":
    main()

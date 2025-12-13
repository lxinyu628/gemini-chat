"""配置文件监控模块，实现配置热重载功能。

使用 watchdog 库监控 config.json 文件变更，自动触发配置重载。
支持 session 变更检测，自动清除 JWT 缓存和重置保活服务状态。
"""
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

from biz_gemini.config import NEW_CONFIG_FILE, reload_config, load_config

# 模块级 logger
logger = logging.getLogger("config_watcher")

# Session 相关的关键字段，变更时需要清除缓存
SESSION_FIELDS = {"secure_c_ses", "host_c_oses", "csesidx", "cookie_raw"}


def _check_session_changed(old_config: dict, new_config: dict) -> bool:
    """检查 session 相关字段是否发生变化"""
    old_session = old_config.get("session", {})
    new_session = new_config.get("session", {})

    for field in SESSION_FIELDS:
        if old_session.get(field) != new_session.get(field):
            return True
    return False


class ConfigFileEventHandler(FileSystemEventHandler):
    """配置文件变更事件处理器"""

    def __init__(self, callback: Callable[[dict], None]):
        super().__init__()
        self.callback = callback
        self.last_reload_time = 0
        self.reload_cooldown = 2  # 防抖：2秒内只重载一次
        # 保存旧配置用于比较
        self._old_config: Optional[dict] = None

    def on_modified(self, event):
        """处理文件变更事件"""
        if isinstance(event, FileModifiedEvent):
            # 只监控 config.json
            if Path(event.src_path).resolve() == NEW_CONFIG_FILE.resolve():
                current_time = time.time()

                # 防抖处理
                if current_time - self.last_reload_time < self.reload_cooldown:
                    return

                self.last_reload_time = current_time
                logger.info(f"检测到配置文件变更: {event.src_path}")

                try:
                    old_config = self._old_config
                    new_config = reload_config()
                    logger.info("配置重载成功")

                    # 检查 session 是否变更，如果变更则清除缓存
                    if old_config and _check_session_changed(old_config, new_config):
                        try:
                            from biz_gemini.auth import on_cookie_refreshed
                            on_cookie_refreshed()
                            logger.info("检测到 session 变更，已清除 JWT 缓存和过期标记")

                            # 同时重置保活服务的内部状态
                            try:
                                from biz_gemini.keep_alive import get_keep_alive_service
                                keep_alive = get_keep_alive_service()
                                keep_alive._session_valid = True
                                keep_alive._cookie_expired = False
                                keep_alive._last_error = None
                                keep_alive._last_check = None
                                logger.info("已重置保活服务状态")
                            except Exception as e:
                                logger.warning(f"重置保活服务状态失败: {e}")
                        except Exception as e:
                            logger.warning(f"清除缓存失败: {e}")

                    self._old_config = new_config

                    if self.callback:
                        self.callback(new_config)
                except Exception as e:
                    logger.error(f"配置重载失败: {e}")


class ConfigWatcher:
    """配置文件监控器"""
    
    def __init__(self, callback: Optional[Callable[[dict], None]] = None):
        """
        初始化配置监控器
        
        Args:
            callback: 配置变更时的回调函数，接收新配置作为参数
        """
        self.callback = callback
        self.observer: Optional[Observer] = None
        self.event_handler: Optional[ConfigFileEventHandler] = None
    
    def start(self) -> None:
        """启动监控。"""
        if self.observer and self.observer.is_alive():
            logger.warning("配置监控器已在运行")
            return

        if not NEW_CONFIG_FILE.exists():
            logger.warning(f"配置文件不存在: {NEW_CONFIG_FILE}")
            return

        self.event_handler = ConfigFileEventHandler(self.callback)

        # 初始化旧配置（用于检测 session 变更）
        try:
            self.event_handler._old_config = load_config()
        except Exception:
            self.event_handler._old_config = None

        self.observer = Observer()
        watch_dir = NEW_CONFIG_FILE.parent
        self.observer.schedule(self.event_handler, str(watch_dir), recursive=False)
        self.observer.start()
        logger.info(f"配置监控已启动，监控文件: {NEW_CONFIG_FILE}")

    def stop(self) -> None:
        """停止监控。"""
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=5)
            logger.info("配置监控已停止")
        else:
            logger.debug("配置监控器未运行")
    
    def is_running(self) -> bool:
        """检查监控器是否运行中"""
        return self.observer is not None and self.observer.is_alive()


# 全局配置监控器实例
_watcher: Optional[ConfigWatcher] = None


def start_config_watcher(callback: Optional[Callable[[dict], None]] = None) -> ConfigWatcher:
    """
    启动全局配置监控器
    
    Args:
        callback: 配置变更时的回调函数
    
    Returns:
        ConfigWatcher 实例
    """
    global _watcher
    
    if _watcher is None:
        _watcher = ConfigWatcher(callback)
    
    _watcher.start()
    return _watcher


def stop_config_watcher() -> None:
    """停止全局配置监控器"""
    global _watcher
    
    if _watcher:
        _watcher.stop()
        _watcher = None


def get_config_watcher() -> Optional[ConfigWatcher]:
    """获取全局配置监控器实例"""
    return _watcher

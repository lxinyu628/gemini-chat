"""配置文件监控模块，实现配置热重载功能"""
import time
from pathlib import Path
from typing import Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

from biz_gemini.config import NEW_CONFIG_FILE, reload_config


class ConfigFileEventHandler(FileSystemEventHandler):
    """配置文件变更事件处理器"""
    
    def __init__(self, callback: Callable[[dict], None]):
        super().__init__()
        self.callback = callback
        self.last_reload_time = 0
        self.reload_cooldown = 2  # 防抖：2秒内只重载一次
    
    def on_modified(self, event):
        """文件修改事件处理"""
        if isinstance(event, FileModifiedEvent):
            # 只监控 config.json
            if Path(event.src_path).resolve() == NEW_CONFIG_FILE.resolve():
                current_time = time.time()
                
                # 防抖处理
                if current_time - self.last_reload_time < self.reload_cooldown:
                    return
                
                self.last_reload_time = current_time
                print(f"[*] 检测到配置文件变更: {event.src_path}")
                
                try:
                    # 重新加载配置
                    new_config = reload_config()
                    print("[+] 配置重载成功")
                    
                    # 调用回调函数
                    if self.callback:
                        self.callback(new_config)
                except Exception as e:
                    print(f"[!] 配置重载失败: {e}")


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
    
    def start(self):
        """启动监控"""
        if self.observer and self.observer.is_alive():
            print("[!] 配置监控器已在运行")
            return
        
        if not NEW_CONFIG_FILE.exists():
            print(f"[!] 配置文件不存在: {NEW_CONFIG_FILE}")
            return
        
        # 创建事件处理器
        self.event_handler = ConfigFileEventHandler(self.callback)
        
        # 创建观察者
        self.observer = Observer()
        
        # 监控配置文件所在目录
        watch_dir = NEW_CONFIG_FILE.parent
        self.observer.schedule(self.event_handler, str(watch_dir), recursive=False)
        
        # 启动观察者
        self.observer.start()
        print(f"[+] 配置监控已启动，监控文件: {NEW_CONFIG_FILE}")
    
    def stop(self):
        """停止监控"""
        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=5)
            print("[+] 配置监控已停止")
        else:
            print("[!] 配置监控器未运行")
    
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

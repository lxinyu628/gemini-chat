"""统一配置管理模块，支持 config.json、环境变量、配置迁移和热重载"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

# 配置文件路径
PROJECT_ROOT = Path(__file__).parent.parent
NEW_CONFIG_FILE = PROJECT_ROOT / "config.json"
OLD_CONFIG_FILE = Path(__file__).parent / "business_gemini_session.json"
TIME_FMT = "%Y-%m-%d %H:%M:%S"

# 默认配置
DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 4,
        "log_level": "INFO",
        "reload": False,
    },
    "proxy": {
        "enabled": False,
        "url": "",
        "timeout": 30,
    },
    "session": {
        "secure_c_ses": "",
        "host_c_oses": "",
        "csesidx": "",
        "group_id": "",
        "cookies_saved_at": "",
    },
}


def sanitize_group_id(group_id: Optional[str]) -> Optional[str]:
    """去掉 group_id 中可能携带的路径或查询参数，只保留裸 UUID。"""
    if not group_id:
        return group_id
    cleaned = group_id.strip()
    for sep in ("/", "?", "#"):
        cleaned = cleaned.split(sep, 1)[0]
    return cleaned


def migrate_old_config() -> bool:
    """迁移旧配置文件到新格式"""
    if not OLD_CONFIG_FILE.exists() or NEW_CONFIG_FILE.exists():
        return False

    try:
        print(f"[*] 检测到旧配置文件，正在迁移...")
        with open(OLD_CONFIG_FILE, "r", encoding="utf-8") as f:
            old_cfg = json.load(f)

        # 创建新配置结构
        new_cfg = DEFAULT_CONFIG.copy()
        
        # 迁移 session 配置
        session_keys = ["secure_c_ses", "host_c_oses", "csesidx", "group_id", "cookies_saved_at", "saved_at"]
        for key in session_keys:
            if key in old_cfg:
                if key == "saved_at":
                    # 旧字段名映射
                    new_cfg["session"]["cookies_saved_at"] = old_cfg[key]
                else:
                    new_cfg["session"][key] = old_cfg[key]

        # 迁移 proxy 配置
        if "proxy" in old_cfg and old_cfg["proxy"]:
            new_cfg["proxy"]["enabled"] = True
            new_cfg["proxy"]["url"] = old_cfg["proxy"]

        # 保存新配置
        with open(NEW_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(new_cfg, f, ensure_ascii=False, indent=2)

        # 备份旧配置
        backup_file = OLD_CONFIG_FILE.with_suffix(".json.backup")
        shutil.copy2(OLD_CONFIG_FILE, backup_file)
        print(f"[+] 配置迁移完成，旧配置已备份到: {backup_file}")
        
        return True
    except Exception as e:
        print(f"[!] 配置迁移失败: {e}")
        return False


def load_config() -> dict:
    """加载配置，优先级：环境变量 > config.json > 默认配置"""
    # 尝试迁移旧配置
    migrate_old_config()

    # 加载配置文件
    if NEW_CONFIG_FILE.exists():
        try:
            with open(NEW_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[!] 配置文件格式错误: {e}")
            cfg = DEFAULT_CONFIG.copy()
    else:
        cfg = DEFAULT_CONFIG.copy()

    # 确保配置结构完整
    for section, defaults in DEFAULT_CONFIG.items():
        if section not in cfg:
            cfg[section] = defaults.copy()
        else:
            for key, value in defaults.items():
                if key not in cfg[section]:
                    cfg[section][key] = value

    # 环境变量覆盖 - Server 配置
    if os.getenv("SERVER_HOST"):
        cfg["server"]["host"] = os.getenv("SERVER_HOST")
    if os.getenv("SERVER_PORT"):
        cfg["server"]["port"] = int(os.getenv("SERVER_PORT"))
    if os.getenv("SERVER_WORKERS"):
        cfg["server"]["workers"] = int(os.getenv("SERVER_WORKERS"))
    if os.getenv("SERVER_LOG_LEVEL"):
        cfg["server"]["log_level"] = os.getenv("SERVER_LOG_LEVEL")

    # 环境变量覆盖 - Proxy 配置
    if os.getenv("PROXY_URL"):
        cfg["proxy"]["enabled"] = True
        cfg["proxy"]["url"] = os.getenv("PROXY_URL")
    if os.getenv("PROXY_TIMEOUT"):
        cfg["proxy"]["timeout"] = int(os.getenv("PROXY_TIMEOUT"))

    # 环境变量覆盖 - Session 配置
    if os.getenv("BIZ_GEMINI_SECURE_C_SES"):
        cfg["session"]["secure_c_ses"] = os.getenv("BIZ_GEMINI_SECURE_C_SES")
    if os.getenv("BIZ_GEMINI_HOST_C_OSES"):
        cfg["session"]["host_c_oses"] = os.getenv("BIZ_GEMINI_HOST_C_OSES")
    if os.getenv("BIZ_GEMINI_CSESIDX"):
        cfg["session"]["csesidx"] = os.getenv("BIZ_GEMINI_CSESIDX")
    if os.getenv("BIZ_GEMINI_GROUP_ID"):
        cfg["session"]["group_id"] = os.getenv("BIZ_GEMINI_GROUP_ID")

    # 为了向后兼容，将 session 配置提升到顶层
    # 这样旧代码可以继续使用 config.get("secure_c_ses") 等
    cfg.update(cfg["session"])
    
    # 为向后兼容添加 proxy_url 字段
    if cfg["proxy"]["enabled"] and cfg["proxy"]["url"]:
        cfg["proxy_url"] = cfg["proxy"]["url"]  # 兼容性字段
    else:
        cfg["proxy_url"] = None  # 代理未启用时设为 None

    # 清理 group_id
    cfg["group_id"] = sanitize_group_id(cfg.get("group_id"))
    if "session" in cfg:
        cfg["session"]["group_id"] = cfg["group_id"]

    return cfg


def save_config(update: dict) -> dict:
    """更新并保存配置，返回合并后的结果"""
    cfg = load_config()
    
    # 如果 update 包含旧格式的顶层字段，映射到新结构
    session_keys = ["secure_c_ses", "host_c_oses", "csesidx", "group_id", "cookies_saved_at", "saved_at"]
    for key in session_keys:
        if key in update:
            if key == "saved_at":
                cfg["session"]["cookies_saved_at"] = update[key]
            else:
                cfg["session"][key] = update[key]
            # 同时更新顶层（向后兼容）
            if key != "saved_at":
                cfg[key] = update[key]
    
    # 处理 proxy
    if "proxy" in update:
        if isinstance(update["proxy"], str):
            # 旧格式：直接是 URL 字符串
            cfg["proxy"]["enabled"] = bool(update["proxy"])
            cfg["proxy"]["url"] = update["proxy"]
            cfg["proxy_url"] = update["proxy"]
        elif isinstance(update["proxy"], dict):
            # 新格式：字典
            cfg["proxy"].update(update["proxy"])
    
    # 清理 group_id
    cfg["session"]["group_id"] = sanitize_group_id(cfg["session"].get("group_id"))
    cfg["group_id"] = cfg["session"]["group_id"]
    
    # 保存到文件（只保存结构化数据）
    save_data = {
        "server": cfg.get("server", DEFAULT_CONFIG["server"]),
        "proxy": {
            "enabled": cfg.get("proxy", {}).get("enabled", False) if isinstance(cfg.get("proxy"), dict) else bool(cfg.get("proxy")),
            "url": cfg.get("proxy", {}).get("url", "") if isinstance(cfg.get("proxy"), dict) else cfg.get("proxy", ""),
            "timeout": cfg.get("proxy", {}).get("timeout", 30) if isinstance(cfg.get("proxy"), dict) else 30,
        },
        "session": cfg["session"],
    }
    
    NEW_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(NEW_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    
    return cfg


def get_proxy(config: dict) -> Optional[str]:
    """返回代理地址（支持 http/socks5/socks5h）。
    
    如果未配置代理，返回 None（直接连接）。
    """
    # 新格式
    if isinstance(config.get("proxy"), dict):
        proxy_cfg = config["proxy"]
        if proxy_cfg.get("enabled") and proxy_cfg.get("url"):
            return proxy_cfg["url"]
    
    # 旧格式兼容
    if isinstance(config.get("proxy"), str) and config["proxy"]:
        return config["proxy"]
    
    # 兼容性字段
    if config.get("proxy_url"):
        return config["proxy_url"]
    
    return None


def cookies_age_seconds(config: dict) -> Optional[float]:
    """返回 cookie 保存时间与当前时间的秒差。"""
    # 尝试从 session 配置获取
    ts_str = None
    if "session" in config:
        ts_str = config["session"].get("cookies_saved_at") or config["session"].get("saved_at")
    
    # 兼容旧格式
    if not ts_str:
        ts_str = config.get("cookies_saved_at") or config.get("saved_at")
    
    if not ts_str:
        return None
    try:
        dt = datetime.strptime(ts_str, TIME_FMT)
    except ValueError:
        return None
    return (datetime.now() - dt).total_seconds()


def cookies_expired(config: dict, max_age_hours: int = 0) -> bool:
    """判断 cookie 是否超过 max_age_hours。

    注意：此函数已废弃，建议使用 auth.check_session_status() 检查真实的 session 状态。
    默认 max_age_hours=0 表示不进行时间检查（始终返回 False）。
    """
    if max_age_hours <= 0:
        return False
    age = cookies_age_seconds(config)
    if age is None:
        return False
    return age > max_age_hours * 3600


# 配置热重载支持
_config_cache = None
_config_mtime = None


def get_cached_config(force_reload: bool = False) -> dict:
    """获取缓存的配置，支持文件变更检测"""
    global _config_cache, _config_mtime
    
    if force_reload:
        _config_cache = None
        _config_mtime = None
    
    current_mtime = None
    if NEW_CONFIG_FILE.exists():
        current_mtime = NEW_CONFIG_FILE.stat().st_mtime
    
    if _config_cache is None or _config_mtime != current_mtime:
        _config_cache = load_config()
        _config_mtime = current_mtime
    
    return _config_cache


def reload_config() -> dict:
    """强制重新加载配置"""
    return get_cached_config(force_reload=True)

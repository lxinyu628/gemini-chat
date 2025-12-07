"""API Key 管理模块

提供 API Key 的生成、存储、验证等功能。
使用 SQLite3 数据库存储 API Keys。
"""
import logging
import os
import secrets
import shutil
import sqlite3
import string
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

# 模块级 logger
logger = logging.getLogger("api_keys")

# 数据库文件路径 - 使用 data 目录
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_FILE = DATA_DIR / "api_keys.db"
OLD_DB_FILE = PROJECT_ROOT / "api_keys.db"  # 旧位置，用于迁移

# 线程锁，确保数据库操作线程安全
_db_lock = threading.Lock()


def _migrate_db_if_needed() -> None:
    """无感迁移：如果旧位置存在数据库，自动迁移到新位置"""
    # 确保 data 目录存在
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # 如果旧文件存在且新文件不存在，执行迁移
    if OLD_DB_FILE.exists() and not DB_FILE.exists():
        try:
            shutil.move(str(OLD_DB_FILE), str(DB_FILE))
            logger.info(f"数据库已自动迁移: {OLD_DB_FILE} -> {DB_FILE}")
        except Exception as e:
            logger.warning(f"数据库迁移失败: {e}，将在新位置创建数据库")
    elif OLD_DB_FILE.exists() and DB_FILE.exists():
        # 两个位置都有文件，记录警告但使用新位置
        logger.warning(f"检测到新旧位置都存在数据库文件，使用新位置: {DB_FILE}")


def _get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """初始化数据库，创建表结构"""
    with _db_lock:
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    name TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP,
                    is_active INTEGER DEFAULT 1
                )
            """)
            conn.commit()
        finally:
            conn.close()


def generate_api_key(name: str = "") -> Dict[str, Any]:
    """生成新的 API Key
    
    Args:
        name: API Key 的名称/备注
        
    Returns:
        包含完整 API Key 信息的字典
    """
    # 生成 sk- 前缀 + 48位随机字符串
    alphabet = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(alphabet) for _ in range(48))
    api_key = f"sk-{random_part}"
    
    with _db_lock:
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            # 使用本地时间
            local_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute(
                "INSERT INTO api_keys (key, name, created_at) VALUES (?, ?, ?)",
                (api_key, name, local_now)
            )
            conn.commit()
            key_id = cursor.lastrowid
            
            return {
                "id": key_id,
                "key": api_key,
                "name": name,
                "created_at": local_now,
                "is_active": True
            }
        finally:
            conn.close()


def list_api_keys(include_full_key: bool = False) -> List[Dict[str, Any]]:
    """获取所有 API Key 列表
    
    Args:
        include_full_key: 是否包含完整的 key，False 则返回脱敏版本
        
    Returns:
        API Key 列表
    """
    with _db_lock:
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, key, name, created_at, last_used_at, is_active
                FROM api_keys
                ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()
            
            result = []
            for row in rows:
                key_data = {
                    "id": row["id"],
                    "name": row["name"] or "",
                    "created_at": row["created_at"],
                    "last_used_at": row["last_used_at"],
                    "is_active": bool(row["is_active"])
                }
                
                full_key = row["key"]
                if include_full_key:
                    key_data["key"] = full_key
                else:
                    # 脱敏显示：sk-xxxx...xxxx（显示前7位和后4位）
                    if len(full_key) > 11:
                        key_data["key"] = f"{full_key[:7]}...{full_key[-4:]}"
                    else:
                        key_data["key"] = full_key
                
                result.append(key_data)
            
            return result
        finally:
            conn.close()


def get_api_key_by_id(key_id: int) -> Optional[Dict[str, Any]]:
    """根据 ID 获取完整的 API Key 信息"""
    with _db_lock:
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, key, name, created_at, last_used_at, is_active FROM api_keys WHERE id = ?",
                (key_id,)
            )
            row = cursor.fetchone()
            
            if row:
                return {
                    "id": row["id"],
                    "key": row["key"],
                    "name": row["name"] or "",
                    "created_at": row["created_at"],
                    "last_used_at": row["last_used_at"],
                    "is_active": bool(row["is_active"])
                }
            return None
        finally:
            conn.close()


def validate_api_key(api_key: str) -> bool:
    """验证 API Key 是否有效
    
    Args:
        api_key: 要验证的 API Key
        
    Returns:
        True 如果 key 有效且激活，否则 False
    """
    if not api_key or not api_key.startswith("sk-"):
        return False
    
    with _db_lock:
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, is_active FROM api_keys WHERE key = ?",
                (api_key,)
            )
            row = cursor.fetchone()
            
            if row and row["is_active"]:
                # 更新最后使用时间（本地时间）
                local_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (local_now, row["id"])
                )
                conn.commit()
                return True
            return False
        finally:
            conn.close()


def delete_api_key(key_id: int) -> bool:
    """删除 API Key
    
    Args:
        key_id: API Key 的 ID
        
    Returns:
        True 如果删除成功，否则 False
    """
    with _db_lock:
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def toggle_api_key(key_id: int, is_active: bool) -> bool:
    """启用/禁用 API Key
    
    Args:
        key_id: API Key 的 ID
        is_active: 是否激活
        
    Returns:
        True 如果更新成功，否则 False
    """
    with _db_lock:
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE api_keys SET is_active = ? WHERE id = ?",
                (1 if is_active else 0, key_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


# 初始化：先迁移旧数据库，再初始化
_migrate_db_if_needed()
init_db()


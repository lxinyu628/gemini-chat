"""Redis管理模块，提供统一的Redis访问接口，支持降级到内存存储"""
import json
import time
from typing import Optional, Any, Dict
from .logger import get_logger

logger = get_logger("redis_manager")

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis库未安装，将使用内存存储作为降级方案")


class RedisManager:
    """Redis管理器，支持自动降级到内存存储"""
    
    def __init__(self, config: dict):
        """初始化Redis管理器
        
        Args:
            config: 包含redis配置的字典
        """
        self.config = config
        self.redis_config = config.get("redis", {})
        self.enabled = self.redis_config.get("enabled", False) and REDIS_AVAILABLE
        self.key_prefix = self.redis_config.get("key_prefix", "gemini_chat:")
        
        # 内存存储降级
        self._memory_store: Dict[str, tuple[Any, Optional[float]]] = {}  # key -> (value, expire_time)
        
        if self.enabled:
            try:
                self.client = redis.Redis(
                    host=self.redis_config.get("host", "127.0.0.1"),
                    port=self.redis_config.get("port", 6379),
                    password=self.redis_config.get("password") or None,
                    db=self.redis_config.get("db", 0),
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )
                # 测试连接（但不在初始化时强制要求成功）
                try:
                    self.client.ping()
                    logger.info("✅ Redis连接成功")
                except Exception as e:
                    logger.warning(f"Redis连接失败，降级到内存存储: {e}")
                    self.enabled = False
            except Exception as e:
                logger.warning(f"Redis初始化失败，降级到内存存储: {e}")
                self.enabled = False
        else:
            self.client = None
            if not REDIS_AVAILABLE:
                logger.info("Redis未启用（库未安装），使用内存存储")
            else:
                logger.info("Redis未启用，使用内存存储")
    
    def _make_key(self, key: str) -> str:
        """生成带前缀的完整key"""
        return f"{self.key_prefix}{key}"
    
    def _cleanup_expired(self):
        """清理过期的内存存储项"""
        current_time = time.time()
        expired_keys = [
            k for k, (_, exp) in self._memory_store.items()
            if exp is not None and exp < current_time
        ]
        for k in expired_keys:
            del self._memory_store[k]
    
    def get(self, key: str) -> Optional[str]:
        """获取值
        
        Args:
            key: 键名
            
        Returns:
            值字符串，如果不存在返回None
        """
        full_key = self._make_key(key)
        
        if self.enabled:
            try:
                return self.client.get(full_key)
            except Exception as e:
                logger.warning(f"Redis get失败，使用内存降级: {e}")
                self.enabled = False
        
        # 内存存储降级
        self._cleanup_expired()
        if full_key in self._memory_store:
            value, expire_time = self._memory_store[full_key]
            if expire_time is None or expire_time > time.time():
                return value
            else:
                del self._memory_store[full_key]
        return None
    
    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """设置值
        
        Args:
            key: 键名
            value: 值
            ex: 过期时间（秒）
            
        Returns:
            是否成功
        """
        full_key = self._make_key(key)
        
        if self.enabled:
            try:
                self.client.set(full_key, value, ex=ex)
                return True
            except Exception as e:
                logger.warning(f"Redis set失败，使用内存降级: {e}")
                self.enabled = False
        
        # 内存存储降级
        expire_time = (time.time() + ex) if ex else None
        self._memory_store[full_key] = (value, expire_time)
        return True
    
    def delete(self, key: str) -> bool:
        """删除值
        
        Args:
            key: 键名
            
        Returns:
            是否成功
        """
        full_key = self._make_key(key)
        
        if self.enabled:
            try:
                self.client.delete(full_key)
                return True
            except Exception as e:
                logger.warning(f"Redis delete失败，使用内存降级: {e}")
                self.enabled = False
        
        # 内存存储降级
        if full_key in self._memory_store:
            del self._memory_store[full_key]
        return True
    
    def get_json(self, key: str) -> Optional[Any]:
        """获取JSON值
        
        Args:
            key: 键名
            
        Returns:
            解析后的JSON对象，如果不存在或解析失败返回None
        """
        value = self.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                logger.warning(f"JSON解析失败: {key}")
                return None
        return None
    
    def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """设置JSON值
        
        Args:
            key: 键名
            value: 要序列化为JSON的对象
            ex: 过期时间（秒）
            
        Returns:
            是否成功
        """
        try:
            json_str = json.dumps(value, ensure_ascii=False)
            return self.set(key, json_str, ex=ex)
        except Exception as e:
            logger.warning(f"JSON序列化失败: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """检查key是否存在
        
        Args:
            key: 键名
            
        Returns:
            是否存在
        """
        full_key = self._make_key(key)
        
        if self.enabled:
            try:
                return bool(self.client.exists(full_key))
            except Exception as e:
                logger.warning(f"Redis exists失败，使用内存降级: {e}")
                self.enabled = False
        
        # 内存存储降级
        self._cleanup_expired()
        if full_key in self._memory_store:
            _, expire_time = self._memory_store[full_key]
            if expire_time is None or expire_time > time.time():
                return True
            else:
                del self._memory_store[full_key]
        return False
    
    def is_redis_enabled(self) -> bool:
        """检查Redis是否启用
        
        Returns:
            是否使用Redis存储
        """
        return self.enabled
    
    def acquire_rate_limit(
        self,
        key: str,
        max_requests: int = 10,
        window_seconds: int = 60
    ) -> tuple[bool, float]:
        """获取速率限制令牌（分布式安全）
        
        使用 Redis INCR + EXPIRE 实现滑动窗口速率限制，
        确保多个 worker 之间的请求协调。
        
        Args:
            key: 速率限制的键名（会自动添加 rate_limit: 前缀）
            max_requests: 时间窗口内允许的最大请求数
            window_seconds: 时间窗口大小（秒）
            
        Returns:
            (allowed, wait_seconds): 是否允许请求，以及需要等待的秒数
        """
        full_key = self._make_key(f"rate_limit:{key}")
        
        if self.enabled:
            try:
                # 使用 Redis pipeline 保证原子性
                pipe = self.client.pipeline()
                pipe.incr(full_key)
                pipe.ttl(full_key)
                results = pipe.execute()
                
                current_count = results[0]
                ttl = results[1]
                
                # 如果是新 key（第一次请求），设置过期时间
                if ttl == -1:
                    self.client.expire(full_key, window_seconds)
                    ttl = window_seconds
                
                if current_count <= max_requests:
                    return (True, 0.0)
                else:
                    # 超出限制，返回需要等待的时间
                    wait_time = float(max(ttl, 1))
                    logger.warning(f"速率限制触发: {key}, 当前请求数={current_count}, 等待={wait_time}s")
                    return (False, wait_time)
                    
            except Exception as e:
                logger.warning(f"Redis rate limit 失败，降级到内存: {e}")
                self.enabled = False
        
        # 内存存储降级（单进程模式）
        self._cleanup_expired()
        rate_key = f"{full_key}:count"
        expire_key = f"{full_key}:expire"
        
        current_time = time.time()
        
        # 检查是否已有计数
        if rate_key in self._memory_store:
            count, _ = self._memory_store[rate_key]
            expire_time_val, _ = self._memory_store.get(expire_key, (current_time + window_seconds, None))
            
            if current_time >= expire_time_val:
                # 窗口已过期，重置
                self._memory_store[rate_key] = (1, None)
                self._memory_store[expire_key] = (current_time + window_seconds, None)
                return (True, 0.0)
            
            new_count = count + 1
            self._memory_store[rate_key] = (new_count, None)
            
            if new_count <= max_requests:
                return (True, 0.0)
            else:
                wait_time = expire_time_val - current_time
                logger.warning(f"速率限制触发(内存): {key}, 当前请求数={new_count}, 等待={wait_time:.1f}s")
                return (False, wait_time)
        else:
            # 首次请求
            self._memory_store[rate_key] = (1, None)
            self._memory_store[expire_key] = (current_time + window_seconds, None)
            return (True, 0.0)
    
    def get_rate_limit_status(self, key: str) -> dict:
        """获取速率限制状态
        
        Args:
            key: 速率限制的键名
            
        Returns:
            包含当前计数和剩余时间的字典
        """
        full_key = self._make_key(f"rate_limit:{key}")
        
        if self.enabled:
            try:
                pipe = self.client.pipeline()
                pipe.get(full_key)
                pipe.ttl(full_key)
                results = pipe.execute()
                
                count = int(results[0]) if results[0] else 0
                ttl = results[1] if results[1] and results[1] > 0 else 0
                
                return {
                    "current_count": count,
                    "ttl_seconds": ttl,
                    "redis_enabled": True
                }
            except Exception as e:
                logger.warning(f"获取速率限制状态失败: {e}")
        
        # 内存降级
        rate_key = f"{full_key}:count"
        expire_key = f"{full_key}:expire"
        
        count = 0
        ttl = 0
        current_time = time.time()
        
        if rate_key in self._memory_store:
            count, _ = self._memory_store[rate_key]
            expire_time_val, _ = self._memory_store.get(expire_key, (current_time, None))
            ttl = max(0, int(expire_time_val - current_time))
        
        return {
            "current_count": count,
            "ttl_seconds": ttl,
            "redis_enabled": False
        }


# 全局Redis管理器实例
_redis_manager: Optional[RedisManager] = None


def get_redis_manager(config: Optional[dict] = None) -> RedisManager:
    """获取全局Redis管理器实例
    
    Args:
        config: 配置字典，首次调用时必须提供
        
    Returns:
        RedisManager实例
    """
    global _redis_manager
    if _redis_manager is None:
        if config is None:
            from .config import load_config
            config = load_config()
        _redis_manager = RedisManager(config)
    return _redis_manager

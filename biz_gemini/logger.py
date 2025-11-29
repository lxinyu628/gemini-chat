"""统一日志配置模块"""
import logging
import sys
from typing import Optional


def setup_logger(
    name: str = "biz_gemini",
    level: int = logging.INFO,
    format_string: Optional[str] = None,
) -> logging.Logger:
    """设置并返回 logger 实例。

    Args:
        name: logger 名称
        level: 日志级别
        format_string: 日志格式字符串

    Returns:
        配置好的 Logger 实例
    """
    if format_string is None:
        format_string = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"

    logger = logging.getLogger(name)

    # 避免重复添加 handler
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(format_string, datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)

    logger.setLevel(level)
    return logger


def get_logger(name: str = "biz_gemini") -> logging.Logger:
    """获取指定名称的 logger。

    Args:
        name: logger 名称

    Returns:
        Logger 实例
    """
    return logging.getLogger(name)


# 默认 logger 实例
_default_logger: Optional[logging.Logger] = None


def get_default_logger() -> logging.Logger:
    """获取默认的 logger 实例。

    Returns:
        默认 Logger 实例
    """
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logger()
    return _default_logger


# 便捷的模块级 logger
logger = get_default_logger()

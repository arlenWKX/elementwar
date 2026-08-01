"""日志模块。

提供：
- setup_logging() 初始化根日志（必须由 main.py 显式调用）
- get_logger(name) 获取模块级 logger
- audit_event() 记录房间事件日志
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from typing import Any

from app.config import LOG_DIR, settings


class TextFormatter(logging.Formatter):
    """彩色文本格式。"""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        color = self.COLORS.get(record.levelname, "")
        room = getattr(record, "room_id", "")
        room_str = f"[{room}] " if room else ""
        msg = record.getMessage()
        exc = ""
        if record.exc_info:
            exc = "\n" + self.formatException(record.exc_info)
        return f"{ts} {color}{record.levelname:<7}{self.RESET} {room_str}{record.name}: {msg}{exc}"


def setup_logging() -> None:
    """配置全局根 logger。

    必须由 main.py 在 lifespan startup 中显式调用。
    重复调用幂等（先清空 handlers）。
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    root.handlers.clear()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(root.level)
    console.setFormatter(TextFormatter())
    root.addHandler(console)

    # 文件（滚动）
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(TextFormatter())
    file_handler.setLevel(root.level)
    root.addHandler(file_handler)

    # 第三方库日志降噪
    logging.getLogger("socketio").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger。"""
    return logging.getLogger(name)


def audit_event(room_id: str, event: str, **fields: Any) -> None:
    """记录一条房间事件日志。"""
    logger = logging.getLogger("audit")
    extra_str = " ".join(f"{k}={v}" for k, v in fields.items()) if fields else ""
    logger.info(f"[{room_id}] {event} {extra_str}")


__all__ = [
    "setup_logging",
    "get_logger",
    "audit_event",
    "TextFormatter",
]

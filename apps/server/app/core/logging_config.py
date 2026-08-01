"""统一日志配置 — 控制台输出 + 格式统一，供 main.py 启动时调用。"""
import logging
import sys


def setup_logging(level: str = "INFO") -> logging.Logger:
    """初始化 firefly 日志器：统一格式、级别，清除重复 handler。"""
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    # firefly 根日志器 —— 业务代码统一用 getLogger("firefly.xxx") 或 getLogger("firefly")
    root = logging.getLogger("firefly")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)

    # 阻止 firefly 日志向上传播到 uvicorn 根日志器（防止重复输出）
    root.propagate = False

    return root


def get_logger(name: str = "") -> logging.Logger:
    """便捷获取子 logger。不传 name 返回到 firefly 根 logger。"""
    full = f"firefly.{name}" if name else "firefly"
    return logging.getLogger(full)

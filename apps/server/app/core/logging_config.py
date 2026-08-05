"""统一日志配置 — 控制台输出 + 格式统一，供 main.py 启动时调用。"""
import logging
import sys


def _make_stream_handler(fmt: logging.Formatter) -> logging.Handler:
    """创建控制台 handler，规避 Windows 控制台 GBK 编码导致的 UnicodeEncodeError。

    - 优先把 stdout 重配置为 UTF-8 + errors="replace"；
    - 若 stdout 不可用（打包后无控制台），回退到 NullHandler，避免启动即崩。
    """
    stream = sys.stdout
    if stream is None:
        handler = logging.NullHandler()
        handler.setFormatter(fmt)
        return handler
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass  # 非 UTF-8 平台或重配置失败时保持原样，仍可正常写入
    handler = logging.StreamHandler(stream)
    handler.setFormatter(fmt)
    return handler


def setup_logging(level: str = "INFO") -> logging.Logger:
    """初始化 firefly 日志器：统一格式、级别，清除重复 handler。"""
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = _make_stream_handler(fmt)

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

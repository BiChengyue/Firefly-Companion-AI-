"""
GPT-SoVITS 本地 API 服务拉起与管理器
- 检查 9880 端口是否在线
- 若未在线且模型文件已全落盘，自动通过子进程拉起 api_v2.py
- 空闲自动关闭：超过 IDLE_SHUTDOWN_SECONDS 无 TTS 使用则停止进程释放内存
"""
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from app.core.voice.model_manager import check_model_status, get_engine_dir

import atexit
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)

_process: Optional[subprocess.Popen] = None
_job_handle = None
_launch_lock = threading.Lock()
_launching = False  # 正在启动中，防止并发拉起

# ── 空闲自动关闭机制 ──────────────────────────────────────────
# GPT-SoVITS 常驻占大量内存（GPU ~2-3GB + CPU RAM ~1-2GB），
# 日常对话并非每句都需要流萤原声，设置 5 分钟无使用自动释放。
IDLE_SHUTDOWN_SECONDS = 300  # 5 minutes
_idle_timer: Optional[threading.Timer] = None


def _assign_process_to_job(process_handle: int):
    """把子进程关联到 Windows Job Object，当主进程关闭时，Windows 操作系统内核会自动强杀子进程。"""
    global _job_handle
    if sys.platform != "win32":
        return
    try:
        if _job_handle is None:
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            JobObjectExtendedLimitInformation = 9

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            _job_handle = ctypes.windll.kernel32.CreateJobObjectW(None, None)
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ctypes.windll.kernel32.SetInformationJobObject(
                _job_handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
        ctypes.windll.kernel32.AssignProcessToJobObject(_job_handle, process_handle)
        logger.info("[GPT-SoVITS Service] 已成功将推理进程绑定至 Windows Job Object (父进程关闭自动强杀子进程)")
    except Exception as e:
        logger.warning(f"[GPT-SoVITS Service] 绑定 Job Object 失败: {e}")


def _load_config_python_path() -> Optional[str]:
    """从 config 中读取 voice.gptSovits.pythonPath 配置项。"""
    try:
        from app.config import get_settings
        path = get_settings().voice.gpt_sovits.python_path
        if path:
            return str(Path(path))
    except Exception:
        pass
    return None


def is_port_in_use(host: str = "127.0.0.1", port: int = 9880) -> bool:
    """检测指定端口是否已有服务在监听"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def ensure_gpt_sovits_started(host: str = "127.0.0.1", port: int = 9880, timeout_seconds: int = 120) -> bool:
    """
    检查并确保 GPT-SoVITS 推理 API 正在运行。
    若端口未监听且模型就绪，自动拉起 api_v2.py 后台子进程。
    使用锁机制防止并发重复拉起。
    """
    global _process, _launching

    # 1. 端口已经在运行 → 直接返回
    if is_port_in_use(host, port):
        logger.debug(f"[GPT-SoVITS Service] 服务已在 {host}:{port} 正常运行")
        mark_gpt_sovits_active()
        return True

    # 2. 获取锁，检查是否已有其他线程正在启动
    if not _launch_lock.acquire(blocking=False):
        # 其他线程正在启动中，等待它完成
        logger.debug("[GPT-SoVITS Service] 已有启动任务进行中，等待完成…")
        _launch_lock.acquire(blocking=True)  # 等上一个启动完成
        _launch_lock.release()
        # 此时要么端口已就绪，要么上次启动失败了
        if is_port_in_use(host, port):
            mark_gpt_sovits_active()
            return True

    try:
        # 3. 双重检查（拿到锁后再次确认端口）
        if is_port_in_use(host, port):
            mark_gpt_sovits_active()
            return True

        # 4. 检查模型文件是否就绪
        status = check_model_status()
        if not status.engine_ready:
            logger.warning(
                f"[GPT-SoVITS Service] 无法自动启动：缺少 {status.missing_files} 个模型文件，请先在设置页完成模型下载。"
            )
            return False

        # 5. 启动 api_v2.py 子进程
        engine_dir = get_engine_dir()
        api_script = engine_dir / "api_v2.py"

        if not api_script.exists():
            logger.error(f"[GPT-SoVITS Service] 找不到推理入口脚本: {api_script}")
            return False

        # 决定使用哪个 Python 解释器
        python_bin = None
        candidates = [
            _load_config_python_path(),
            engine_dir / "env" / "Scripts" / "python.exe",       # Windows
            engine_dir / "env" / "bin" / "python",               # macOS / Linux
            engine_dir / "env" / "bin" / "python3",              # macOS / Linux
        ]
        for c in candidates:
            if c and Path(c).exists():
                python_bin = str(c)
                logger.info(f"[GPT-SoVITS Service] 使用 Python 解释器: {python_bin}")
                break

        if python_bin is None:
            logger.error(
                "[GPT-SoVITS Service] 未找到可用的 Python 解释器。"
                "请在 config/default.local.json 中配置 gpt_sovits.python_path"
            )
            return False

        cmd = [
            python_bin,
            str(api_script),
            "-a", host,
            "-p", str(port),
            "-c", "GPT_SoVITS/configs/tts_infer.yaml",
        ]

        env = dict(os.environ)
        bin_dir = str(engine_dir / "bin")
        env["PATH"] = bin_dir + os.path.pathsep + env.get("PATH", "")

        # 启动前先清理该端口的残留孤儿进程
        if _process is not None:
            try:
                _process.terminate()
                _process.wait(timeout=2)
            except Exception:
                try:
                    _process.kill()
                except Exception:
                    pass
            _process = None
        kill_process_on_port(port)

        logger.info(f"[GPT-SoVITS Service] 正在拉起本地推理服务: {' '.join(cmd)}")

        log_dir = engine_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = open(str(log_dir / "api_v2_stdout.log"), "a", encoding="utf-8")
        stderr_log = open(str(log_dir / "api_v2_stderr.log"), "a", encoding="utf-8")

        _process = subprocess.Popen(
            cmd,
            cwd=str(engine_dir),
            env=env,
            stdout=stdout_log,
            stderr=stderr_log,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        if sys.platform == "win32" and hasattr(_process, "_handle"):
            _assign_process_to_job(int(_process._handle))

        atexit.register(stop_gpt_sovits_service)
        _launching = True

        # 等待端口连通
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if is_port_in_use(host, port):
                logger.info(f"[GPT-SoVITS Service] 本地推理服务启动成功！(端口 {port})")
                mark_gpt_sovits_active()
                _launching = False
                return True
            time.sleep(1.0)

        logger.error(f"[GPT-SoVITS Service] 启动超时 ({timeout_seconds}s)，端口 {port} 未连通。")
        return False

    except Exception as e:
        logger.error(f"[GPT-SoVITS Service] 启动失败: {e}")
        return False
    finally:
        _launching = False
        _launch_lock.release()


def kill_process_on_port(port: int = 9880):
    """查找并强制销毁指定端口上监听的所有残留进程 (例如残留的 api_v2.py)"""
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True, text=True, errors="ignore")
            pids = set()
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and "LISTENING" in parts:
                    pid = parts[-1]
                    if pid != "0":
                        pids.add(pid)
            for pid in pids:
                logger.info(f"[GPT-SoVITS Service] 正在强杀端口 {port} 上的残留进程 PID: {pid}")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            pass
    else:
        # macOS / Linux：lsof 找端口占用进程，fuser 兜底
        try:
            out = subprocess.check_output(
                f"lsof -ti tcp:{port} 2>/dev/null", shell=True, text=True, errors="ignore"
            )
            for pid in out.splitlines():
                pid = pid.strip()
                if pid:
                    logger.info(f"[GPT-SoVITS Service] 正在强杀端口 {port} 上的残留进程 PID: {pid}")
                    subprocess.run(
                        f"kill -9 {pid}", shell=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
            subprocess.run(
                f"fuser -k {port}/tcp 2>/dev/null", shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def mark_gpt_sovits_active():
    """标记 GPT-SoVITS 刚被使用，重置空闲计时器。

    每次成功调用 GPT-SoVITS TTS 后都应调用此函数，
    确保空闲计时器重新开始计时，避免正在使用时被自动关闭。
    """
    global _idle_timer
    cancel_gpt_sovits_idle_timer()
    _idle_timer = threading.Timer(IDLE_SHUTDOWN_SECONDS, _on_idle_timeout)
    _idle_timer.daemon = True
    _idle_timer.start()


def cancel_gpt_sovits_idle_timer():
    """取消空闲计时器（手动停止服务时调用）"""
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _on_idle_timeout():
    """空闲超时回调：自动停止 GPT-SoVITS 子进程释放内存"""
    global _idle_timer
    _idle_timer = None
    logger.info(
        f"[GPT-SoVITS Service] 空闲超过 {IDLE_SHUTDOWN_SECONDS}s，"
        "自动关闭推理进程以释放内存/显存。下次 TTS 请求时将自动重新拉起。"
    )
    stop_gpt_sovits_service()


def stop_gpt_sovits_service():
    """关闭后台拉起的 GPT-SoVITS 子进程"""
    global _process
    cancel_gpt_sovits_idle_timer()  # 先取消空闲计时器
    if _process is not None:
        try:
            _process.terminate()
            _process.wait(timeout=3)
            logger.info("[GPT-SoVITS Service] 已停止本地推理服务进程")
        except Exception:
            try:
                _process.kill()
            except Exception:
                pass
        finally:
            _process = None

    # 兜底强杀 9880 端口残存进程，确保无孤儿进程残留
    kill_process_on_port(9880)

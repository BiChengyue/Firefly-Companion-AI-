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

from app.core import paths as _paths
from app.core.voice.model_manager import check_model_status, get_engine_dir

import atexit
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)

# ── tts_infer.yaml 定制配置（兜底模板）───────────────────────────────────
# custom 段指向流萤专属权重（../firefly/ 相对引擎目录解析 = 数据根/voice/firefly/）。
# 当打包资源根也缺失 tts_infer.yaml 时，用它生成一份可用的配置。
_DEFAULT_TTS_INFER_YAML = """\
custom:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cuda
  is_half: true
  t2s_weights_path: ../firefly/gpt_weights/firefly-e50.ckpt
  version: v2
  vits_weights_path: ../firefly/sovits_weights/firefly_e10_s4420_l32.pth
v1:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cpu
  is_half: false
  t2s_weights_path: GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt
  version: v1
  vits_weights_path: GPT_SoVITS/pretrained_models/s2G488k.pth
v2:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cpu
  is_half: false
  t2s_weights_path: GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt
  version: v2
  vits_weights_path: GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth
v3:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cpu
  is_half: false
  t2s_weights_path: GPT_SoVITS/pretrained_models/s1v3.ckpt
  version: v3
  vits_weights_path: GPT_SoVITS/pretrained_models/s2Gv3.pth
v4:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cpu
  is_half: false
  t2s_weights_path: GPT_SoVITS/pretrained_models/s1v3.ckpt
  version: v4
  vits_weights_path: GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth
"""

# 流萤专属权重（相对引擎目录的 ../firefly/ → 数据根/voice/firefly/）
_FIREFLY_T2S_WEIGHTS = "../firefly/gpt_weights/firefly-e50.ckpt"
_FIREFLY_VITS_WEIGHTS = "../firefly/sovits_weights/firefly_e10_s4420_l32.pth"

_process: Optional[subprocess.Popen] = None
_job_handle = None
_launch_lock = threading.Lock()
_launching = False  # 正在启动中，防止并发拉起

# ── 空闲自动关闭机制 ──────────────────────────────────────────
# GPT-SoVITS 常驻占大量内存（GPU ~2-3GB + CPU RAM ~1-2GB），
# 原设计 5 分钟无使用自动释放；2026-08-07 用户要求语音随时可用
# （每次重新加载引擎需 ~1 分钟，实际不可接受）→ 改为 1 天（实际常驻）。
IDLE_SHUTDOWN_SECONDS = 86400  # 1 day（实际常驻；服务器 3060 专用可承受）
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


def _ensure_tts_infer_config(engine_dir: Path) -> bool:
    """校验并修复 tts_infer.yaml，确保 custom 段指向存在的流萤权重。

    背景：打包时 GPT_SoVITS/configs/.gitignore 的 ``*.yaml`` 曾使
    ``tts_infer.yaml`` 未被 git 跟踪 → PyInstaller 只收集 git 跟踪文件，
    安装版引擎目录缺失该配置；api_v2 回退原版默认配置（指向不存在的
    ``gsv-v2final-pretrained`` 权重）→ FileNotFoundError 崩溃。

    这里做三重兜底：
      1. 文件缺失 → 从资源根（打包内置）复制；资源根也没有 → 写入内置模板；
      2. custom 段的 t2s/vits 权重路径指向的文件不存在 → 改写为流萤权重；
      3. 已存在且路径有效 → 不动。

    返回 True 表示启动前配置就绪（或已修复），False 表示无法写入配置。
    """
    import shutil

    cfg_dir = engine_dir / "GPT_SoVITS" / "configs"
    cfg_file = cfg_dir / "tts_infer.yaml"

    # 1) 文件缺失：优先从资源根（打包内置）复制
    if not cfg_file.exists():
        try:
            cfg_dir.mkdir(parents=True, exist_ok=True)
            bundled = (
                _paths.RESOURCE_ROOT
                / "resources"
                / "voice"
                / "gpt_sovits_engine"
                / "GPT_SoVITS"
                / "configs"
                / "tts_infer.yaml"
            )
            if bundled.is_file():
                shutil.copy2(bundled, cfg_file)
                logger.info(f"[GPT-SoVITS Service] 已从资源根补齐 tts_infer.yaml: {cfg_file}")
            else:
                cfg_file.write_text(_DEFAULT_TTS_INFER_YAML, encoding="utf-8")
                logger.warning(f"[GPT-SoVITS Service] 资源根无 tts_infer.yaml，已写入内置定制配置: {cfg_file}")
        except OSError as e:
            logger.error(f"[GPT-SoVITS Service] 写入 tts_infer.yaml 失败: {e}")
            return False

    # 2) 校验 custom 段权重路径（相对 engine_dir 解析，与 api_v2 的 cwd 一致）
    try:
        import yaml

        with open(cfg_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        custom = data.get("custom") or {}
        t2s = str(custom.get("t2s_weights_path") or "")
        vits = str(custom.get("vits_weights_path") or "")
        t2s_ok = bool(t2s) and (engine_dir / t2s).exists()
        vits_ok = bool(vits) and (engine_dir / vits).exists()
        if t2s_ok and vits_ok:
            return True
        logger.warning(
            f"[GPT-SoVITS Service] tts_infer.yaml custom 段权重路径无效 "
            f"(t2s={t2s!r} exists={t2s_ok}, vits={vits!r} exists={vits_ok})，改写为流萤权重…"
        )
    except Exception as e:
        logger.warning(f"[GPT-SoVITS Service] 解析 tts_infer.yaml 失败，将整体重建: {e}")

    # 3) 行级改写 custom 段路径（保留其余配置项），无 custom 段则追加
    try:
        lines = cfg_file.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        in_custom = False
        changed = False
        for line in lines:
            stripped = line.strip()
            if stripped == "custom:":
                in_custom = True
                out.append(line)
                continue
            if in_custom:
                if stripped and not line.startswith(("  ", "\t")):
                    in_custom = False  # 已离开 custom 段
                elif stripped.startswith("t2s_weights_path:"):
                    out.append("  t2s_weights_path: " + _FIREFLY_T2S_WEIGHTS)
                    changed = True
                    continue
                elif stripped.startswith("vits_weights_path:"):
                    out.append("  vits_weights_path: " + _FIREFLY_VITS_WEIGHTS)
                    changed = True
                    continue
            out.append(line)
        if not changed:
            out.append("custom:")
            out.append("  t2s_weights_path: " + _FIREFLY_T2S_WEIGHTS)
            out.append("  version: v2")
            out.append("  vits_weights_path: " + _FIREFLY_VITS_WEIGHTS)
        cfg_file.write_text("\n".join(out) + "\n", encoding="utf-8")
        logger.info(f"[GPT-SoVITS Service] 已修复 tts_infer.yaml → {cfg_file}")
        return True
    except OSError as e:
        logger.error(f"[GPT-SoVITS Service] 修复 tts_infer.yaml 失败: {e}")
        return False


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

        # 4.5 校验/修复 tts_infer.yaml（防止打包漏带或权重路径无效导致 api_v2 崩溃）
        engine_dir = get_engine_dir()
        if not _ensure_tts_infer_config(engine_dir):
            logger.error("[GPT-SoVITS Service] tts_infer.yaml 修复失败，中止启动。")
            return False

        # 5. 启动 api_v2.py 子进程
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
    """标记 GPT-SoVITS 刚被使用。（空闲自动关闭已禁用，引擎常驻不回收。）"""
    pass


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
    """关闭后台拉起的 GPT-SoVITS 子进程（仅本服务拉起的；外部 NSSM 引擎不碰，2026-08-07）"""
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

        # 仅在自己拉起过进程时才兜底清理端口（避免误杀外部 NSSM 引擎 firefly-gsv）
        kill_process_on_port(9880)

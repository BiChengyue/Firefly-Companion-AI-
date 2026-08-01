// Python sidecar 进程管理 — 对应 Todo 阶段 1 末尾
//
// 开发模式：Tauri setup 时自动从 .venv 拉起 uvicorn，应用退出时 Drop 自动关闭。
// 生产模式（阶段 6/7）：运行 PyInstaller 打包后的 firefly-server.exe。
// 前端也可通过 invoke start_sidecar / stop_sidecar 手动控制。

use std::process::{Child, Command};
use std::sync::Mutex;

pub struct SidecarState {
    pub child: Mutex<Option<Child>>,
}

impl Drop for SidecarState {
    fn drop(&mut self) {
        // 应用退出时自动清理 Python 子进程
        if let Ok(mut guard) = self.child.lock() {
            if let Some(ref mut child) = *guard {
                let _ = child.kill();
                let _ = child.wait(); // 回收僵尸进程
            }
        }
    }
}

/// 向上查找项目根目录（包含 .venv 的目录）。
///
/// Tauri dev 模式下 CWD 是 `src-tauri/` 而非项目根，需要向上遍历。
fn find_project_root() -> Result<std::path::PathBuf, String> {
    let mut dir = std::env::current_dir().map_err(|e| format!("获取当前目录失败: {e}"))?;
    loop {
        let venv_py = if cfg!(target_os = "windows") {
            dir.join(".venv").join("Scripts").join("python.exe")
        } else {
            dir.join(".venv").join("bin").join("python")
        };
        if venv_py.exists() {
            return Ok(dir);
        }
        if !dir.pop() {
            return Err(format!(
                "未找到项目根目录（.venv 不存在于当前目录 {} 的任意上级目录中）\n\
                 请确保已在项目根目录执行 python -m venv .venv",
                std::env::current_dir().unwrap_or_default().display()
            ));
        }
    }
}

/// 开发模式：找到项目 .venv 并启动 Python FastAPI 后端（uvicorn，端口 8765）。
pub fn auto_start_dev_sidecar() -> Result<Child, String> {
    let root = find_project_root()?;

    // 检查 .venv
    let venv_python = if cfg!(target_os = "windows") {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };

    let server_dir = root.join("apps").join("server");
    if !server_dir.exists() {
        return Err(format!("未找到服务器目录: {}", server_dir.display()));
    }

    let child = Command::new(&venv_python)
        .args(["-B", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8765"])
        .current_dir(&server_dir)
        .spawn()
        .map_err(|e| format!("启动 Python 后端失败: {e}"))?;

    println!("[Sidecar] Python 后端已启动 (PID {})", child.id());
    Ok(child)
}

// ── Tauri 命令：前端可手动控制 ──────────────────────────────────────────

#[tauri::command]
pub fn start_sidecar(state: tauri::State<SidecarState>) -> Result<String, String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        return Ok("already running".into());
    }
    let child = auto_start_dev_sidecar()?;
    *guard = Some(child);
    Ok("started".into())
}

#[tauri::command]
pub fn stop_sidecar(state: tauri::State<SidecarState>) -> Result<String, String> {
    kill_all_backend_processes(&state);
    Ok("stopped".into())
}

/// 彻底强制清理所有 Python 后端与 GPT-SoVITS 语音推理进程 (8765 与 9880 端口)
pub fn kill_all_backend_processes(state: &SidecarState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;

        // ① 给 FastAPI 发送 HTTP shutdown 请求，让 Python 先优雅清理
        let _ = Command::new("powershell")
            .args(["-Command", "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/shutdown' -Method Post -TimeoutSec 1 } catch {}"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();

        // ② 兜底强杀 9880 端口 (GPT-SoVITS api_v2.py) 上的残留进程
        let _ = Command::new("cmd")
            .args(["/C", "for /f \"tokens=5\" %a in ('netstat -aon ^| findstr :9880 ^| findstr LISTENING') do taskkill /F /PID %a"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();

        // ③ 兜底强杀 8765 端口 (FastAPI uvicorn) 上的残留进程
        let _ = Command::new("cmd")
            .args(["/C", "for /f \"tokens=5\" %a in ('netstat -aon ^| findstr :8765 ^| findstr LISTENING') do taskkill /F /PID %a"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }

    #[cfg(not(target_os = "windows"))]
    {
        // ① 优雅关闭 FastAPI（macOS / Linux 自带 curl）
        let _ = Command::new("curl")
            .args(["-s", "-X", "POST", "http://127.0.0.1:8765/api/shutdown"])
            .status();

        // ②+③ 兜底强杀残留进程（macOS / Linux 通用）
        let _ = Command::new("sh")
            .args([
                "-c",
                "for p in 9880 8765; do lsof -ti tcp:$p 2>/dev/null | xargs -r kill -9 2>/dev/null; fuser -k ${p}/tcp 2>/dev/null; done; pkill -9 -f 'api_v2.py' 2>/dev/null; pkill -9 -f 'uvicorn main:app' 2>/dev/null",
            ])
            .status();
    }
}

// Python sidecar 进程管理 — 对应 Todo 阶段 1 末尾
//
// 开发模式：Tauri setup 时自动从 .venv 拉起 uvicorn，应用退出时 Drop 自动关闭。
// 生产模式（打包）：运行随安装包分发的 firefly-server.exe（PyInstaller onedir），
// 通过 FIREFLY_ROOT / FIREFLY_RESOURCE_ROOT 环境变量注入数据根与资源根。
// 前端也可通过 invoke start_sidecar / stop_sidecar 手动控制。

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;

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
        .env("PYTHONIOENCODING", "utf-8")
        .spawn()
        .map_err(|e| format!("启动 Python 后端失败: {e}"))?;

    println!("[Sidecar] Python 后端已启动 (PID {})", child.id());
    Ok(child)
}

/// 生产模式：启动随安装包分发的 PyInstaller 后端 exe。
///
/// 布局约定（tauri.conf.json 的 bundle.resources）：
///   resource_dir/firefly-server/firefly-server.exe
///   resource_dir/firefly-server/_internal/{config,resources,...}
///
/// 通过环境变量注入：
///   FIREFLY_ROOT            → 可写数据根（app_data_dir，data/ 等写这里）
///   FIREFLY_RESOURCE_ROOT   → 只读资源根（_internal，config/ 与 resources/ 所在处）
fn start_packaged_sidecar(app: &tauri::AppHandle) -> Result<Child, String> {
    let res_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("获取资源目录失败: {e}"))?;
    let server_dir = res_dir.join("firefly-server");
    let exe = if cfg!(target_os = "windows") {
        server_dir.join("firefly-server.exe")
    } else {
        server_dir.join("firefly-server")
    };
    if !exe.exists() {
        return Err(format!("未找到打包的后端程序: {}", exe.display()));
    }

    // 数据根 → 用户应用数据目录（可写）
    let data_dir: PathBuf = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("获取应用数据目录失败: {e}"))?;
    std::fs::create_dir_all(&data_dir).map_err(|e| format!("创建数据目录失败: {e}"))?;

    // 资源根 → _internal（含 config/ 与 resources/）
    let internal_dir = server_dir.join("_internal");

    let child = Command::new(&exe)
        .env("FIREFLY_ROOT", &data_dir)
        .env("FIREFLY_RESOURCE_ROOT", &internal_dir)
        .env("PORT", "8765")
        .env("PYTHONIOENCODING", "utf-8")
        .spawn()
        .map_err(|e| format!("启动打包后端失败: {e}"))?;

    println!("[Sidecar] 打包后端已启动 (PID {})，数据根: {}", child.id(), data_dir.display());
    Ok(child)
}

/// 统一启动入口：优先生产模式（打包 exe），回退开发模式（.venv + uvicorn）。
pub fn auto_start_sidecar(app: &tauri::AppHandle) -> Result<Child, String> {
    // 启动前先清理残留占用 8765/9880 端口的进程，
    // 否则新实例会因端口绑定失败而立即退出（表现为命令行一闪而过）。
    kill_port_processes();

    match start_packaged_sidecar(app) {
        Ok(child) => Ok(child),
        Err(_) => {
            // 未打包环境（dev）→ 回退开发模式
            auto_start_dev_sidecar()
        }
    }
}

// ── Tauri 命令：前端可手动控制 ──────────────────────────────────────────

#[tauri::command]
pub fn start_sidecar(state: tauri::State<SidecarState>, app: tauri::AppHandle) -> Result<String, String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        return Ok("already running".into());
    }
    let child = auto_start_sidecar(&app)?;
    *guard = Some(child);
    Ok("started".into())
}

#[tauri::command]
pub fn stop_sidecar(state: tauri::State<SidecarState>) -> Result<String, String> {
    kill_all_backend_processes(&state);
    Ok("stopped".into())
}

/// 清理占用 8765 (FastAPI) 与 9880 (GPT-SoVITS) 端口的残留进程。
/// 不依赖 SidecarState，可在启动新后端前直接调用，避免端口占用导致绑定失败退出。
fn kill_port_processes() {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;

        // ① 给 FastAPI 发送 HTTP shutdown 请求，让 Python 先优雅清理
        let _ = Command::new("powershell")
            .args(["-Command", "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/shutdown' -Method Post -TimeoutSec 1 } catch {}"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();

        // ② 解析 netstat -ano 输出，强杀监听 8765 / 9880 的残留进程。
        //    注意：不能用 cmd 的 `for /f "tokens=5" %a ...` 内嵌脚本 —— args 传参时
        //    引号会被转义成 \"，cmd 报「此时不应有 ...」语法错误导致清理失效。
        for port in ["8765", "9880"] {
            if let Ok(output) = Command::new("netstat").args(["-ano"]).output() {
                let text = String::from_utf8_lossy(&output.stdout);
                for line in text.lines() {
                    if line.contains(&format!(":{} ", port)) && line.contains("LISTENING") {
                        if let Some(pid) = line.split_whitespace().last() {
                            if !pid.is_empty() && pid.chars().all(|c| c.is_ascii_digit()) {
                                let _ = Command::new("taskkill")
                                    .args(["/F", "/PID", pid])
                                    .creation_flags(CREATE_NO_WINDOW)
                                    .status();
                            }
                        }
                    }
                }
            }
        }
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

/// 彻底强制清理所有 Python 后端与 GPT-SoVITS 语音推理进程 (8765 与 9880 端口)
pub fn kill_all_backend_processes(state: &SidecarState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    kill_port_processes();
}

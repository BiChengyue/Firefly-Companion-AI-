// Tauri 应用入口 — 整合窗口管理 / sidecar / 托盘。
// 对应 spec 阶段1：透明置顶窗口 + Ctrl 键穿透 + 系统托盘 + Sidecar 进程管理。
mod sidecar;
mod tray;
mod window;

use sidecar::SidecarState;
use std::sync::Mutex;
use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(SidecarState {
            child: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            window::set_cursor_passthrough,
            window::move_window,
            sidecar::start_sidecar,
            sidecar::stop_sidecar,
        ])
        .setup(|app| {
            tray::setup_tray(app)?;

            // 确保桌宠窗口始终置顶（Tauri 2 配置项不总是生效，运行时强制设置）
            if let Some(pet_win) = app.get_webview_window("pet") {
                let _ = pet_win.set_always_on_top(true);
            }

            // 自动启动 Python 后端（优先打包 exe，回退 dev 模式），存到 SidecarState
            // 应用退出时 SidecarState::Drop 自动 kill 子进程
            match sidecar::auto_start_sidecar(app.handle()) {
                Ok(child) => {
                    let state = app.state::<SidecarState>();
                    *state.child.lock().unwrap() = Some(child);
                }
                Err(e) => {
                    eprintln!("[Sidecar] 自动启动失败: {e}");
                    // 不阻止应用启动 — 用户可以手动 start_sidecar 或单独跑服务端
                }
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

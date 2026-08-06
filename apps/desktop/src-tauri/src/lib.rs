// Tauri 应用入口 — 窗口管理 + 托盘。
// 桌宠 = 总线客户端（CONTRACTS §0.2）：不再内嵌 Python 后端，
// sidecar 已移除（UPSTREAM_PATCHES #1/2/3 评估落地，见 T-06 变更说明）。
mod tray;
mod window;

use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            window::set_cursor_passthrough,
            window::move_window,
        ])
        .setup(|app| {
            tray::setup_tray(app)?;

            // 确保桌宠窗口始终置顶（Tauri 2 配置项不总是生效，运行时强制设置）
            if let Some(pet_win) = app.get_webview_window("pet") {
                let _ = pet_win.set_always_on_top(true);
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

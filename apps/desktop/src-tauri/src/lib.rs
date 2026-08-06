// Tauri 应用入口 — 窗口管理 + 托盘。
// 桌宠 = 总线客户端（CONTRACTS §0.2）：不再内嵌 Python 后端，
// sidecar 已移除（UPSTREAM_PATCHES #1/2/3 评估落地，见 T-06 变更说明）。
mod tray;
mod window;

use tauri::Manager;

/// 读桌宠总线 token 配置文件（T-20 切单轨配套，2026-08-06）
/// 路径：%APPDATA%\firefly-desktop\bus-token.txt（格式同服务器 bus-token.txt：
/// `BUS_WS_TOKEN=<hex>` 或裸 token）。桌宠前端经 invoke 预载到 localStorage
/// （firefly_bus_ws_token），随后 resolveBusWsUrl() 自动带上 ?token=。
#[tauri::command]
fn read_bus_token() -> Option<String> {
    let appdata = std::env::var("APPDATA").ok()?;
    let path = std::path::Path::new(&appdata)
        .join("firefly-desktop")
        .join("bus-token.txt");
    let content = std::fs::read_to_string(path).ok()?;
    for line in content.lines() {
        let line = line.trim();
        if let Some(v) = line.strip_prefix("BUS_WS_TOKEN=") {
            let v = v.trim();
            if !v.is_empty() {
                return Some(v.to_string());
            }
        }
    }
    let t = content.trim();
    if t.is_empty() {
        None
    } else {
        Some(t.to_string())
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            window::set_cursor_passthrough,
            window::move_window,
            read_bus_token,
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

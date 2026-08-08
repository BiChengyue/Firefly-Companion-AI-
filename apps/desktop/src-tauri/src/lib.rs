// Tauri 应用入口 — 窗口管理 + 托盘。
// 桌宠 = 总线客户端（CONTRACTS §0.2）：不再内嵌 Python 后端，
// sidecar 已移除（UPSTREAM_PATCHES #1/2/3 评估落地，见 T-06 变更说明）。
mod tray;
mod window;

use tauri::Manager;

/// 读桌宠总线 token 配置文件（T-20 切单轨配套，2026-08-06；T-27 B 补兜底路径）
/// 路径优先级：
///   1) %APPDATA%\firefly-desktop\bus-token.txt（桌宠机本地，用户手动放置/分发）
///   2) %ProgramData%\firefly-bot\bus-token.txt（服务器/同机部署时 install.ps1
///      -PersistTokens 落盘处；ACL 为 SYSTEM/Administrators，普通用户进程读不到时自然跳过）
/// 文件格式：`BUS_WS_TOKEN=<hex>` 或裸 token。桌宠前端经 invoke 预载到 localStorage
/// （firefly_bus_ws_token），随后 resolveBusWsUrl() 自动带上 ?token=。
#[tauri::command]
fn read_bus_token() -> Option<String> {
    let mut candidates: Vec<std::path::PathBuf> = Vec::new();
    if let Ok(appdata) = std::env::var("APPDATA") {
        candidates.push(
            std::path::Path::new(&appdata).join("firefly-desktop").join("bus-token.txt"),
        );
    }
    if let Ok(program_data) = std::env::var("ProgramData") {
        candidates.push(
            std::path::Path::new(&program_data).join("firefly-bot").join("bus-token.txt"),
        );
    }
    for path in candidates {
        if let Some(tok) = read_token_file(&path) {
            return Some(tok);
        }
    }
    None
}

fn read_token_file(path: &std::path::Path) -> Option<String> {
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
            window::start_ctrl_override,
            read_bus_token,
        ])
        .setup(|app| {
            tray::setup_tray(app)?;

            // 确保桌宠窗口始终置顶（Tauri 2 配置项不总是生效，运行时强制设置）
            if let Some(pet_win) = app.get_webview_window("pet") {
                let _ = pet_win.set_always_on_top(true);
            }

            // T35：启动 Ctrl 全局钩子（按住拖动、松开穿透锁定）
            window::start_ctrl_override(app.handle().clone());

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

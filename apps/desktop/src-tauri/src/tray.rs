// 系统托盘 — 对应 spec PLANNING 6.8
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

pub fn setup_tray(app: &tauri::App) -> tauri::Result<()> {
    let show_i = MenuItem::with_id(app, "show", "显示流萤", true, None::<&str>)?;
    let hide_i = MenuItem::with_id(app, "hide", "隐藏流萤", true, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show_i, &hide_i, &quit_i])?;

    let _tray = TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("流萤桌面伴侣")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => {
                if let Some(w) = app.get_webview_window("pet") {
                    let _ = w.show();
                    let _ = w.set_focus();
                    let _ = w.set_always_on_top(true);
                }
            }
            "hide" => {
                if let Some(w) = app.get_webview_window("pet") {
                    let _ = w.hide();
                }
            }
            "quit" => {
                let state = app.state::<crate::sidecar::SidecarState>();
                crate::sidecar::kill_all_backend_processes(&state);
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;

    Ok(())
}

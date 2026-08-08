// 窗口管理：透明置顶 + Ctrl 键穿透控制 — 对应 spec 2 Control-Key Override
use tauri::{Emitter, Manager, Window};

/// 设置鼠标穿透状态。
/// 平时穿透（passthrough=true，不阻挡后面窗口点击），
/// 长按 Ctrl 时变为可交互（passthrough=false，可点击/拖拽）。
/// 对应 spec 第 2 章「按键唤醒移动方案」。
#[tauri::command]
pub fn set_cursor_passthrough(window: Window, passthrough: bool) {
    let _ = window.set_ignore_cursor_events(passthrough);
    let _ = window.emit("passthrough-changed", passthrough);
}

/// 移动窗口到指定位置（拖拽时调用）。
#[tauri::command]
pub fn move_window(window: Window, x: i32, y: i32) {
    use tauri::PhysicalPosition;
    let _ = window.set_position(PhysicalPosition { x, y });
}

// ── T35：按住 Ctrl 拖动桌宠 ────────────────────────────
// 点击穿透（set_ignore_cursor_events=true）时窗口收不到鼠标/键盘事件，
// 必须用 Windows 全局键状态轮询（GetAsyncKeyState）感知 Ctrl。
// Ctrl 按住 → 取消穿透（可拖）+ 发射 ctrl-override-changed=true；
// Ctrl 松开 → 恢复穿透（锁定）+ 发射 ctrl-override-changed=false。
#[tauri::command]
pub fn start_ctrl_override(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        let mut prev_down = false;
        loop {
            std::thread::sleep(std::time::Duration::from_millis(60));
            let down = unsafe { GetAsyncKeyState(0x11) as u16 & 0x8000 != 0 }; // VK_CONTROL
            if down != prev_down {
                prev_down = down;
                if let Some(win) = app.get_webview_window("pet") {
                    let _ = win.set_ignore_cursor_events(!down);
                }
                let _ = app.emit("ctrl-override-changed", down);
            }
        }
    });
}

#[link(name = "user32")]
extern "system" {
    fn GetAsyncKeyState(vKey: i32) -> i16;
}

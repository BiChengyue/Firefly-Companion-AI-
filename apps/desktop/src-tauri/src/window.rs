// 窗口管理：透明置顶 + Ctrl 键穿透控制 — 对应 spec 2 Control-Key Override
use tauri::{Emitter, Window};

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

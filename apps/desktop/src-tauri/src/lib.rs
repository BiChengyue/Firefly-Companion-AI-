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
fn read_bus_token() -> Option<String> {    let mut candidates: Vec<std::path::PathBuf> = Vec::new();
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

#[tauri::command]
fn read_sensor_state() -> Option<String> {
    // A3：桌宠本地电脑状态卡——直接读本机 sensor 采集器落盘的状态文件（不走 hub，桌面端自给）
    std::fs::read_to_string(r"C:\ProgramData\firefly-bot\computer_sensor_state.json").ok()
}

/// UI 偏好持久化文件（key=value 每行，避免引 JSON 依赖）——深色模式等不依赖 WebView2 localStorage（重启丢）
fn ui_prefs_path() -> std::path::PathBuf {
    let mut p = std::env::var("APPDATA")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::path::PathBuf::from("."));
    p.push("firefly-desktop");
    p.push("ui-prefs.txt");
    p
}

#[tauri::command]
fn read_ui_pref(key: String) -> String {
    let p = ui_prefs_path();
    if let Ok(s) = std::fs::read_to_string(&p) {
        for line in s.lines() {
            if let Some((k, v)) = line.split_once('=') {
                if k.trim() == key {
                    return v.trim().to_string();
                }
            }
        }
    }
    String::new()
}

#[tauri::command]
fn write_ui_pref(key: String, value: String) {    let p = ui_prefs_path();
    let mut lines: Vec<String> = Vec::new();
    if let Ok(s) = std::fs::read_to_string(&p) {
        lines = s.lines().map(|l| l.to_string()).collect();
    }
    let kv = format!("{key}={value}");
    let mut found = false;
    for l in lines.iter_mut() {
        if let Some((k, _)) = l.split_once('=') {
            if k.trim() == key {
                *l = kv.clone();
                found = true;
            }
        }
    }
    if !found {
        lines.push(kv);
    }
    if let Some(dir) = p.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(&p, lines.join("\n"));
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

/// 2026-08-08：手机快捷面板——通过无线 adb（Tailnet）对手机执行白名单动作。
/// 每次执行前先 connect（adb 幂等，无线连接 daemon 重启后会丢，connect 可恢复）。
const PHONE_ADB: &str = r"C:\Android\platform-tools\adb.exe";
const PHONE_DEV: &str = "100.108.223.31:5555";

fn run_cmd(args: &[&str]) -> Result<String, String> {
    let out = std::process::Command::new(args[0])
        .args(&args[1..])
        .output()
        .map_err(|e| format!("启动失败: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

#[tauri::command]
fn phone_command(action: String) -> Result<String, String> {
    // 先确保 adb 无线连接
    let _ = run_cmd(&[PHONE_ADB, "connect", PHONE_DEV]);
    let shell = |args: &[&str]| -> Result<String, String> {
        let mut full = vec![PHONE_ADB, "-s", PHONE_DEV, "shell"];
        full.extend_from_slice(args);
        run_cmd(&full)
    };
    match action.as_str() {
        // 声音三态循环：响铃 → 静音 → 震动 → 响铃（2026-08-08 合并为一键）
        "sound_toggle" => {
            let ring = shell(&["settings", "get", "system", "volume_ring"])?;
            let vib = shell(&["settings", "get", "system", "vibrate_when_ringing"]).unwrap_or_default();
            let ring_vol = ring.trim().parse::<i32>().unwrap_or(0);
            let vib_on = vib.trim() == "1";
            if ring_vol > 0 {
                // 当前响铃 → 静音
                shell(&["settings", "put", "system", "volume_ring", "0"])?;
                shell(&["settings", "put", "system", "volume_music", "0"])?;
                Ok("silent".into())
            } else if vib_on {
                // 当前震动 → 响铃
                shell(&["settings", "put", "system", "vibrate_when_ringing", "0"])?;
                shell(&["settings", "put", "system", "volume_ring", "15"])?;
                shell(&["settings", "put", "system", "volume_music", "15"])?;
                Ok("ring".into())
            } else {
                // 当前静音 → 震动
                shell(&["settings", "put", "system", "vibrate_when_ringing", "1"])?;
                Ok("vibrate".into())
            }
        }
        // 找手机：音量拉满 + 华为音乐单次播放组件播铃声（VIEW 会被文件管理器/航旅纵横抢，必须指定组件）
        "find_phone" => {
            shell(&["settings", "put", "system", "volume_music", "15"])?;
            shell(&["settings", "put", "system", "volume_ring", "15"])?;
            // 确保铃声在手机（不存在则从电脑 push，再不行报错提示）
            if shell(&["ls", "/sdcard/Download/findphone.mp3"]).is_err() {
                let local_ring = r"C:\ProgramData\firefly-bot\findphone.mp3";
                if !std::path::Path::new(local_ring).exists() {
                    return Err("铃声文件缺失：请把 findphone.mp3 放到 C:\\ProgramData\\firefly-bot\\".into());
                }
                run_cmd(&[PHONE_ADB, "-s", PHONE_DEV, "push", local_ring, "/sdcard/Download/findphone.mp3"])?;
            }
            shell(&[
                "am", "start", "-a", "android.intent.action.VIEW",
                "-d", "file:///sdcard/Download/findphone.mp3", "-t", "audio/mp3",
                "-n", "com.huawei.music.local/com.huawei.music.ui.player.oneshot.MediaPlaybackActivityStarter",
            ])
        }
        // 激活 Shizuku
        "shizuku" => shell(&[
            "sh", "/storage/emulated/0/Android/data/moe.shizuku.privileged.api/start.sh",
        ]),
        // 勿扰开关（读当前 zen_mode 反写）
        "dnd_toggle" => {
            let cur = shell(&["settings", "get", "secure", "zen_mode"])?;
            let next = if cur.trim() == "1" { "0" } else { "1" };
            shell(&["settings", "put", "secure", "zen_mode", next])?;
            Ok(if next == "1" { "dnd_on" } else { "dnd_off" }.into())
        }
        // 截图：screencap 存到 C:\ProgramData\firefly-bot\screenshots\
        "screenshot" => {
            let dir = r"C:\ProgramData\firefly-bot\screenshots";
            let _ = std::fs::create_dir_all(dir);
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let out_path = format!(r"{dir}\phone_{ts}.png");
            let child = std::process::Command::new(PHONE_ADB)
                .args(["-s", PHONE_DEV, "exec-out", "screencap", "-p"])
                .stdout(std::fs::File::create(&out_path).map_err(|e| e.to_string())?)
                .stderr(std::process::Stdio::null())
                .status()
                .map_err(|e| format!("截图失败: {e}"))?;
            if !child.success() {
                return Err("截图失败".into());
            }
            Ok(format!("saved:{out_path}"))
        }
        // 录屏：screenrecord 15 秒 → pull 到电脑（2026-08-08）
        "screenrecord" => {
            let dir = r"C:\ProgramData\firefly-bot\records";
            let _ = std::fs::create_dir_all(dir);
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            shell(&["screenrecord", "--time-limit", "15", "--bit-rate", "4000000", "/sdcard/record.mp4"])?;
            let out = format!(r"{dir}\phone_{ts}.mp4");
            run_cmd(&[PHONE_ADB, "-s", PHONE_DEV, "pull", "/sdcard/record.mp4", &out])?;
            Ok(format!("saved:{out}"))
        }
        // 访问文件：在手机上打开文件管理器浏览（不复制文件到电脑）——2026-08-08 修正
        "pull_files" => {
            shell(&[
                "am", "start", "-a", "android.intent.action.VIEW",
                "-d", "file:///sdcard/Download",
            ])
        }
        "scrcpy" => {
            let scrcpy = r"E:\AI\scrcpy\scrcpy-win64-v3.2\scrcpy.exe";
            if !std::path::Path::new(scrcpy).exists() {
                return Err("scrcpy 未安装（E:\\AI\\scrcpy 下找不到）".into());
            }
            let _ = std::process::Command::new(scrcpy)
                .args(["-s", PHONE_DEV])
                .spawn()
                .map_err(|e| format!("scrcpy 启动失败: {e}"))?;
            Ok("scrcpy started".into())
        }
        // 手电筒：华为无系统 torch 命令，需手机端 App/Shizuku 实现
        "torch" => Err("华为无系统手电筒命令，需手机端 App 实现".into()),
        _ => Err(format!("unknown action: {action}")),
    }
}

/// 2026-08-08：手机文件浏览器（桌宠内嵌）——adb 驱动：列表 / 下载 / 上传
#[derive(serde::Serialize)]
struct PhoneFsEntry {
    name: String,
    dir: bool,
    size: u64,
}

fn phone_adb_shell(args: &[&str]) -> Result<String, String> {
    let _ = run_cmd(&[PHONE_ADB, "connect", PHONE_DEV]);
    let mut full = vec![PHONE_ADB, "-s", PHONE_DEV, "shell"];
    full.extend_from_slice(args);
    run_cmd(&full)
}

#[tauri::command]
fn phone_fs_list(path: String) -> Result<Vec<PhoneFsEntry>, String> {
    // /sdcard 是符号链接，加尾部斜杠展开目录内容
    let p = if path.ends_with('/') {
        path.clone()
    } else {
        format!("{path}/")
    };
    let out = phone_adb_shell(&["ls", "-la", &p])?;
    let mut entries = Vec::new();
    for line in out.lines().skip(1) {
        // Android toybox ls：perms links owner group size date time name
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() < 8 {
            continue;
        }
        let perms = fields[0];
        let size = fields[4].parse::<u64>().unwrap_or(0);
        let name = fields[7..].join(" ");
        if name.is_empty() || name == "." || name == ".." {
            continue;
        }
        entries.push(PhoneFsEntry {
            name,
            dir: perms.starts_with('d') || perms.starts_with('l'),
            size,
        });
    }
    Ok(entries)
}

#[tauri::command]
fn phone_fs_pull(remote: String, dest_dir: String) -> Result<String, String> {
    let _ = run_cmd(&[PHONE_ADB, "connect", PHONE_DEV]);
    let fname = remote.rsplit('/').next().unwrap_or("file").to_string();
    let dest = format!(r"{dest_dir}\{fname}");
    run_cmd(&[PHONE_ADB, "-s", PHONE_DEV, "pull", &remote, &dest])?;
    Ok(format!("saved:{dest}"))
}

#[tauri::command]
fn phone_fs_push(base64_data: String, remote: String) -> Result<String, String> {
    // base64 → 临时文件 → adb push
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(base64_data.trim())
        .map_err(|e| format!("base64 解码失败: {e}"))?;
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let tmp = std::env::temp_dir().join(format!("fb_push_{ts}"));
    std::fs::write(&tmp, &bytes).map_err(|e| e.to_string())?;
    let _ = run_cmd(&[PHONE_ADB, "connect", PHONE_DEV]);
    let res = run_cmd(&[PHONE_ADB, "-s", PHONE_DEV, "push", tmp.to_str().unwrap_or(""), &remote]);
    let _ = std::fs::remove_file(&tmp);
    res?;
    Ok("pushed".into())
}

/// 2026-08-08：拖放上传——Tauri onDragDropEvent 拿到本地路径，直接 push（无需 base64）
#[tauri::command]
fn phone_fs_push_path(local: String, remote: String) -> Result<String, String> {
    if !std::path::Path::new(&local).exists() {
        return Err(format!("本地文件不存在: {local}"));
    }
    // 确保目标目录存在（remote 形如 /sdcard/Download/xxx.png）
    if let Some(idx) = remote.rfind('/') {
        let dir = &remote[..idx];
        let _ = phone_adb_shell(&["mkdir", "-p", dir]);
    }
    run_cmd(&[PHONE_ADB, "-s", PHONE_DEV, "push", &local, &remote])?;
    Ok(format!("pushed:{remote}"))
}

// ── T35：桌宠位置持久化 ────────────────────────────────
// 拖动桌宠后自动记住位置，下次启动恢复（tauri.conf 的 x/y 只作首次兜底）。
fn pet_pos_path() -> std::path::PathBuf {
    let mut p = std::env::var("APPDATA")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::path::PathBuf::from("."));
    p.push("firefly-desktop");
    p.push("pet-pos.txt");
    p
}

fn load_pet_pos() -> Option<(i32, i32)> {
    let content = std::fs::read_to_string(pet_pos_path()).ok()?;
    let mut parts = content.split_whitespace();
    let x: i32 = parts.next()?.parse().ok()?;
    let y: i32 = parts.next()?.parse().ok()?;
    Some((x, y))
}

fn save_pet_pos(x: i32, y: i32) {
    let path = pet_pos_path();
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(&path, format!("{x} {y}"));
}

/// 崩溃日志：panic 写入 %APPDATA%/firefly-desktop/panic.log（定位启动崩溃）
fn setup_panic_log() {
    std::panic::set_hook(Box::new(|info| {
        use std::io::Write;
        let mut p = std::env::var("APPDATA")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|_| std::path::PathBuf::from("."));
        p.push("firefly-desktop");
        p.push("panic.log");
        if let Some(dir) = p.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&p) {
            let _ = writeln!(f, "[{}] {:?}", chrono_like_now(), info);
        }
        eprintln!("panic: {:?}", info);
    }));
}

/// 简易时间戳（避免引入 chrono 依赖）
fn chrono_like_now() -> String {
    let d = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    format!("t+{}s", d.as_secs())
}

/// Windows 命名互斥体单实例锁：已有一个实例则退出，防多开/残留进程窗口类冲突崩溃
fn acquire_single_instance() -> bool {
    unsafe {
        let name: Vec<u16> = "Local\\firefly-desktop-single-instance\0".encode_utf16().collect();
        let h = CreateMutexW(std::ptr::null_mut(), false, name.as_ptr());
        if h.is_null() {
            return true; // 互斥体创建失败不阻塞启动
        }
        if GetLastError() == ERROR_ALREADY_EXISTS as u32 {
            CloseHandle(h);
            return false;
        }
        // 持有句柄直到进程退出（有意泄漏，互斥体随进程结束自动释放）
        std::mem::forget(h);
        true
    }
}

#[link(name = "kernel32")]
extern "system" {
    fn CreateMutexW(lpMutexAttributes: *mut std::ffi::c_void, bInitialOwner: bool, lpName: *const u16) -> *mut std::ffi::c_void;
    fn GetLastError() -> u32;
    fn CloseHandle(hObject: *mut std::ffi::c_void) -> bool;
}
const ERROR_ALREADY_EXISTS: i32 = 183;

pub fn run() {
    setup_panic_log();
    if !acquire_single_instance() {
        eprintln!("[firefly] another instance already running, exit");
        return;
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            window::set_cursor_passthrough,
            window::move_window,
            window::start_ctrl_override,
            read_bus_token,
            read_sensor_state,
            read_ui_pref,
            write_ui_pref,
            phone_command,
            phone_fs_list,
            phone_fs_pull,
            phone_fs_push,
            phone_fs_push_path,
        ])
        .setup(|app| {
            tray::setup_tray(app)?;

            // 确保桌宠窗口始终置顶（Tauri 2 配置项不总是生效，运行时强制设置）
            if let Some(pet_win) = app.get_webview_window("pet") {
                let _ = pet_win.set_always_on_top(true);

                // T35：恢复上次保存的桌宠位置（拖动持久化）
                if let Some((x, y)) = load_pet_pos() {
                    let _ = pet_win.set_position(tauri::PhysicalPosition { x, y });
                }
                // 移动时自动保存位置（下一次启动恢复）
                let _ = pet_win.on_window_event(|event| {
                    if let tauri::WindowEvent::Moved(pos) = event {
                        save_pet_pos(pos.x, pos.y);
                    }
                });
            }

            // 2026-08-08：main 主窗口关闭 = 隐藏而非退出（伴侣常驻，托盘可恢复）
            // 修复：关闭主窗口后无入口重开，只能重启应用
            if let Some(main_win) = app.get_webview_window("main") {
                let h = main_win.clone(); // 闭包捕获独立 handle（避免与 &self 借用冲突）
                let _ = main_win.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = h.hide();
                    }
                });
            }

            // T35：启动 Ctrl 全局钩子（按住拖动、松开穿透锁定）
            window::start_ctrl_override(app.handle().clone());

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

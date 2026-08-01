/** 文件夹选择器 — 三层降级：Tauri 原生 > showDirectoryPicker > 手输回退 */

// 检测是否在 Tauri 环境
let _isTauri: boolean | null = null
async function isTauri(): Promise<boolean> {
  if (_isTauri !== null) return _isTauri
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window')
    _isTauri = !!getCurrentWindow()
  } catch {
    _isTauri = false
  }
  return _isTauri
}

// 检测浏览器是否支持目录选择 API
function hasShowDirectoryPicker(): boolean {
  return 'showDirectoryPicker' in window && typeof (window as any).showDirectoryPicker === 'function'
}

/**
 * 弹出原生文件夹选择对话框，返回选中路径字符串。
 * 用户取消时返回 null。
 *
 * 降级顺序：
 *   1. Tauri 环境 → 原生 Windows 对话框
 *   2. Chrome/Edge → showDirectoryPicker API
 *   3. 兜底 → 手动输入路径
 */
export async function pickFolder(): Promise<string | null> {
  // [1] Tauri 原生对话框
  if (await isTauri()) {
    try {
      const { open } = await import('@tauri-apps/plugin-dialog')
      const selected = await open({ directory: true, multiple: false, title: '选择工作空间目录' })
      return typeof selected === 'string' ? selected : null
    } catch {
      // 失败时继续降级
    }
  }

  // [2] 浏览器 File System Access API
  if (hasShowDirectoryPicker()) {
    try {
      const handle = await (window as any).showDirectoryPicker({ mode: 'readwrite' })
      // handle 没有直接返回路径，用 handle.name 和已有信息构造
      // 大多数实现中 name 就是文件夹名
      return handle.name || null
    } catch {
      // 用户取消或其他错误
      return null
    }
  }

  // [3] 兜底：返回 null 让调用方弹出手输框
  return null
}

/**
 * 纯兜底——总是返回 null（让调用方显示手输路径的文本框）
 */
export async function fallbackManualInput(): Promise<string | null> {
  return null
}

import { onMounted } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { useCompanionStore } from '@/stores/companion'

/**
 * 穿透与交互控制：
 * 默认桌宠处于点击穿透状态（passthrough = true，不挡鼠标）。
 * T35：Rust 全局 Ctrl 钩子（start_ctrl_override）——按住 Ctrl 取消穿透（可拖），
 * 松开恢复穿透（锁定）。穿透窗口收不到键盘事件，所以用 GetAsyncKeyState 轮询，
 * 变化经 ctrl-override-changed 事件回传，这里同步 store 状态。
 */
export function useCtrlOverride() {
  const companion = useCompanionStore()

  async function setPassthrough(passthrough: boolean) {
    try {
      await invoke('set_cursor_passthrough', { passthrough })
      companion.setPassthrough(passthrough)
    } catch {
      /* 非 Tauri 环境静默捕获 */
    }
  }

  onMounted(() => {
    // 默认开启点击穿透，桌宠不挡鼠标
    setPassthrough(true)

    // 监听 Rust Ctrl 钩子：按住 Ctrl → 可拖；松开 → 锁定 + 穿透
    listen<boolean>('ctrl-override-changed', (e) => {
      const down = e.payload
      companion.setPassthrough(!down)
      companion.setPetLocked(!down)
    })
  })

  return {}
}

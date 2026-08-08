import { onMounted } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { useCompanionStore } from '@/stores/companion'

/**
 * 穿透与交互控制：
 * 默认桌宠处于可交互状态（passthrough = false），锁住位置时支持全方位点击互动与光标跟随；
 * 当解锁移动时，用于拖拽窗口改动桌面位置。
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
    // T35：默认开启点击穿透，桌宠不挡鼠标；需要拖动/互动时用 TopBar 的「穿透/交互」按钮切回
    setPassthrough(true)
  })

  return {}
}

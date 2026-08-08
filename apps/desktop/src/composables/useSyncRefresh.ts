/**
 * 同步刷新（T35 A3 v2）— 服务器/电脑/健康三卡共享同一条 30s 刷新线。
 * 第一个订阅者启动定时器，最后一个取消订阅时停止；所有订阅同 tick 触发。
 */
import { onUnmounted } from 'vue'

type Listener = () => void

const listeners = new Set<Listener>()
let timer: ReturnType<typeof setInterval> | null = null

export function useSyncRefresh(fn: Listener, interval = 30000): Listener {
  listeners.add(fn)
  if (!timer) {
    timer = setInterval(() => {
      listeners.forEach((l) => {
        try {
          l()
        } catch {
          /* 单个订阅失败不影响其它 */
        }
      })
    }, interval)
  }
  onUnmounted(() => {
    listeners.delete(fn)
    if (listeners.size === 0 && timer) {
      clearInterval(timer)
      timer = null
    }
  })
  return fn
}

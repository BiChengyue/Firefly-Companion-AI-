/**
 * 轻量全局 Toast — 单例响应式队列，任意模块可调用 showToast。
 * 仅用于 UI 提示，不触碰记忆/会话等业务数据。
 */
import { reactive, readonly } from 'vue'

export type ToastType = 'info' | 'success' | 'error' | 'warning'

interface ToastItem {
  id: number
  message: string
  type: ToastType
}

const toasts = reactive<ToastItem[]>([])
let seq = 0

export function showToast(message: string, type: ToastType = 'info', duration = 3000) {
  const id = ++seq
  toasts.push({ id, message, type })
  window.setTimeout(() => {
    const i = toasts.findIndex((t) => t.id === id)
    if (i !== -1) toasts.splice(i, 1)
  }, duration)
}

export function useToast() {
  return { toasts: readonly(toasts), showToast }
}

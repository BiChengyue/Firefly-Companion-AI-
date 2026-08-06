/**
 * composables/useToast.ts 冒烟测试 — 覆盖 showToast 添加 / duration 后自动移除 /
 * 多条互不干扰 / readonly 只读语义。
 * useToast 返回的 toasts 是 readonly 数组（非 ref），模板中直接 v-for 使用。
 * 模块级单例（reactive toasts）通过 vi.resetModules() 每个用例重新加载隔离。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

async function loadToastModule() {
  vi.resetModules()
  return await import('./useToast')
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('window', { setTimeout, clearTimeout })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useToast', () => {
  it('showToast 添加一条 toast，带 message/type', async () => {
    const { showToast, useToast } = await loadToastModule()
    const { toasts } = useToast()

    expect(toasts.length).toBe(0)
    showToast('你好', 'success')
    expect(toasts.length).toBe(1)
    expect(toasts[0].message).toBe('你好')
    expect(toasts[0].type).toBe('success')
  })

  it('toast 在 duration 后自动移除', async () => {
    const { showToast, useToast } = await loadToastModule()
    const { toasts } = useToast()

    showToast('短暂提示', 'info', 3000)
    expect(toasts.length).toBe(1)

    vi.advanceTimersByTime(2999)
    expect(toasts.length).toBe(1)
    vi.advanceTimersByTime(1)
    expect(toasts.length).toBe(0)
  })

  it('多条 toast 互不干扰，各自按 duration 移除', async () => {
    const { showToast, useToast } = await loadToastModule()
    const { toasts } = useToast()

    showToast('a', 'info', 1000)
    showToast('b', 'warning', 3000)
    expect(toasts.map((t) => t.message)).toEqual(['a', 'b'])

    vi.advanceTimersByTime(1000)
    expect(toasts.map((t) => t.message)).toEqual(['b'])

    vi.advanceTimersByTime(2000)
    expect(toasts.length).toBe(0)
  })

  it('toasts 为 readonly，直接修改不生效', async () => {
    const { showToast, useToast } = await loadToastModule()
    const { toasts } = useToast()

    showToast('x', 'info')
    const snapshot = toasts.length
    ;(toasts as unknown[]).push({} as never)
    expect(toasts.length).toBe(snapshot)
  })
})

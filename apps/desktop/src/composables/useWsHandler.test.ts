/**
 * T-15 生成状态兜底测试 — useWsHandler + useCompanionStore 的真实接线：
 * - 发送（ack）后 90s 无回复 → 强制复位全部生成状态 + Toast「回复超时，可重试」
 * - 收到 error → 复位 streaming/isThinking/agentRunning + Toast
 * - WS 断开时若生成中 → 复位 + Toast「连接已断开」
 * - 正常回复（done）→ 计时器清除，90s 后不弹超时 Toast
 *
 * 依赖：真实 pinia store（mock 掉 wsClient 以便捕获 onStatus/onMessage handler）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useCompanionStore } from '@/stores/companion'
import { useToast } from '@/composables/useToast'
import { wsClient } from '@/services/ws'
import { useWsHandler } from './useWsHandler'

vi.mock('@/services/ws', () => ({
  wsClient: {
    connect: vi.fn(),
    onStatus: vi.fn(),
    onMessage: vi.fn(),
    send: vi.fn(() => true),
  },
}))

type StatusHandler = (status: string) => void

function setup() {
  const companion = useCompanionStore()
  const handler = useWsHandler({ onVoice: vi.fn(), onTransitionLine: vi.fn() })
  handler.connect()
  const onStatus = vi.mocked(wsClient.onStatus).mock.calls[0][0] as StatusHandler
  return { companion, handler, onStatus }
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  // store 内部用 window.setTimeout 调度超时、顶层读 localStorage
  vi.stubGlobal('window', { setTimeout, clearTimeout, setInterval, clearInterval })
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  })
  vi.mocked(wsClient.onStatus).mockClear()
  vi.mocked(wsClient.onMessage).mockClear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('T-15 生成状态兜底', () => {
  it('发送后 90s 未收到回复 → 强制复位全部生成状态 + Toast「回复超时，可重试」', () => {
    const { companion, handler } = setup()
    handler.handleWsMessage({ type: 'ack', messageId: 'm1' })
    expect(companion.isThinking).toBe(true)

    vi.advanceTimersByTime(89999)
    expect(companion.isThinking).toBe(true) // 未满 90s 不复位

    vi.advanceTimersByTime(1)
    expect(companion.isThinking).toBe(false)
    expect(companion.streaming).toBe(false)
    expect(companion.agentRunning).toBe(false)
    expect(useToast().toasts.some((t) => t.message === '回复超时，可重试')).toBe(true)
  })

  it('收到 error 消息 → 复位 streaming/isThinking/agentRunning + Toast', () => {
    const { companion, handler } = setup()
    handler.handleWsMessage({ type: 'ack', messageId: 'm1' })
    expect(companion.isThinking).toBe(true)

    handler.handleWsMessage({ type: 'error', message: 'content required' })
    expect(companion.isThinking).toBe(false)
    expect(companion.streaming).toBe(false)
    expect(companion.agentRunning).toBe(false)
    expect(companion.lastError).toBe('content required')
    expect(
      useToast().toasts.some((t) => t.message === 'content required' && t.type === 'error'),
    ).toBe(true)
  })

  it('error 时若正在流式 → 保留已生成文本为占位消息', () => {
    const { companion, handler } = setup()
    handler.handleWsMessage({ type: 'ack', messageId: 'm1' })
    handler.handleWsMessage({ type: 'token', delta: '部分' })
    expect(companion.streaming).toBe(true)

    handler.handleWsMessage({ type: 'error', message: '生成失败' })
    expect(companion.streaming).toBe(false)
    expect(companion.messages.some((m) => m.content === '部分')).toBe(true)
  })

  it('WS 断开时若生成中 → 复位 + Toast「连接已断开」（重连后不卡按钮）', () => {
    const { companion, onStatus } = setup()
    companion.startThinking()
    expect(companion.isThinking).toBe(true)

    onStatus('closed')
    expect(companion.isThinking).toBe(false)
    expect(companion.streaming).toBe(false)
    expect(companion.agentRunning).toBe(false)
    expect(useToast().toasts.some((t) => t.message === '连接已断开')).toBe(true)
  })

  it('收到 done 正常回复 → 计时器清除，90s 后不弹超时 Toast', () => {
    const { companion, handler } = setup()
    handler.handleWsMessage({ type: 'ack', messageId: 'm1' })
    handler.handleWsMessage({
      type: 'done',
      message: { id: 'd1', role: 'assistant', content: '好的', createdAt: Date.now() },
    })
    expect(companion.streaming).toBe(false)

    const beforeTotal = useToast().toasts.length
    const beforeTimeout = useToast().toasts.filter((t) => t.message === '回复超时，可重试').length
    vi.advanceTimersByTime(90000)
    expect(useToast().toasts.length).toBe(beforeTotal) // 无新增 Toast（计时器已被 done 清除）
    expect(useToast().toasts.filter((t) => t.message === '回复超时，可重试').length).toBe(beforeTimeout)
  })

  it('超时后重发消息 → 计时器重置，可再次等待完整 90s', () => {
    const { companion, handler } = setup()
    handler.handleWsMessage({ type: 'ack', messageId: 'm1' })
    vi.advanceTimersByTime(45000) // 中途（未超时）

    handler.handleWsMessage({ type: 'ack', messageId: 'm2' }) // 重发 → 重置计时
    vi.advanceTimersByTime(45000)
    expect(companion.isThinking).toBe(true) // 距第二次发送仅 45s，未超时

    vi.advanceTimersByTime(45000)
    expect(companion.isThinking).toBe(false) // 距第二次发送 90s → 超时复位
  })

  it('mode_switched 缺字段时兜底默认值（T-18 🟠3）', () => {
    const { companion } = setup()
    // bus 回包可能只带 current，其余字段缺失 → 取默认，不落 undefined
    companion.applyModeConfig({ current: 'work' })
    expect(companion.mode).toBe('work')
    expect(companion.theme).toEqual({ name: 'firefly' })
    expect(companion.hudVisible).toBe(true)
    expect(companion.thinkVisible).toBe(true)
    expect(companion.proactiveCare).toBe(true)

    // 全字段时照常透传
    companion.applyModeConfig({
      current: 'daily',
      theme: { name: 'daily-theme' },
      hudVisible: false,
      thinkVisible: false,
      proactiveCare: false,
    })
    expect(companion.mode).toBe('daily')
    expect(companion.theme).toEqual({ name: 'daily-theme' })
    expect(companion.hudVisible).toBe(false)
    expect(companion.proactiveCare).toBe(false)
  })
})

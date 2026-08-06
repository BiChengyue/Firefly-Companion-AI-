/**
 * services/ws.ts 冒烟测试 — mock 全局 WebSocket，覆盖 URL 解析 / 连接状态流转 /
 * 指数退避重连 / 封顶 / 手动断开不重连 / 收发消息 / 非法 JSON 容错。
 * 运行环境为 node，浏览器全局（WebSocket / window）在 beforeEach 中 stub。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WsServerMessage } from '@shared/index'
import { DEFAULT_BUS_WS_URL, resolveBusWsUrl, WsClient } from './ws'

class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: MockWebSocket[] = []

  url: string
  readyState = MockWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  sent: string[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }

  // ── 测试辅助：模拟浏览器事件 ──
  emitOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }

  emitMessage(data: string) {
    this.onmessage?.({ data })
  }

  emitError() {
    this.onerror?.()
  }

  emitClose() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  // ws.ts 内部使用 window.setTimeout 调度重连、window.setInterval 驱动心跳
  vi.stubGlobal('window', { setTimeout, clearTimeout, setInterval, clearInterval })
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  })
  vi.stubGlobal('WebSocket', MockWebSocket)
  MockWebSocket.instances = []
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('WsClient — URL 与连接状态', () => {
  it('connect() 用给定 URL 创建 WebSocket，状态 connecting → open', () => {
    const client = new WsClient('ws://127.0.0.1:9000/ws/bus')
    const statuses: string[] = []
    client.onStatus((s) => statuses.push(s))

    client.connect()
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(MockWebSocket.instances[0].url).toBe('ws://127.0.0.1:9000/ws/bus')
    expect(client.status).toBe('connecting')
    expect(client.connected).toBe(false)

    MockWebSocket.instances[0].emitOpen()
    expect(client.status).toBe('open')
    expect(client.connected).toBe(true)

    // onStatus 注册时立即推送一次当前状态
    expect(statuses).toEqual(['closed', 'connecting', 'open'])
  })

  it('默认 URL 为总线地址 ws://100.111.201.71:8767/ws/desktop（Tailnet 服务器）', () => {
    const client = new WsClient()
    client.connect()
    expect(MockWebSocket.instances[0].url).toBe(DEFAULT_BUS_WS_URL)
  })

  it('已连接时重复 connect() 不重复建连', () => {
    const client = new WsClient()
    client.connect()
    MockWebSocket.instances[0].emitOpen()
    client.connect()
    expect(MockWebSocket.instances).toHaveLength(1)
  })
})

describe('WsClient — 重连逻辑', () => {
  it('非手动关闭后 1s 自动重连（BASE_DELAY）', () => {
    const client = new WsClient()
    client.connect()
    const ws = MockWebSocket.instances[0]
    ws.emitOpen()

    ws.emitClose()
    expect(client.status).toBe('closed')

    vi.advanceTimersByTime(999)
    expect(MockWebSocket.instances).toHaveLength(1) // 未到 1s 不重连
    vi.advanceTimersByTime(1)
    expect(MockWebSocket.instances).toHaveLength(2) // 已重连
  })

  it('重试延迟指数增长并封顶 30s（MAX_BACKOFF）', () => {
    const client = new WsClient()
    client.connect()

    // 每次断开后触发下次重连所需的等待：断开 #1 → 1000ms，#2 → 2000ms，…
    // #5 → 16000ms，#6 → 30000ms（封顶，不再 ×2）。
    // 注意：不 emitOpen —— onopen 会重置 _retryCount，连续断开才能让退避序列累积。
    const expectedDelays = [1000, 2000, 4000, 8000, 16000, 30000]

    for (let i = 0; i < expectedDelays.length; i++) {
      const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1]
      ws.emitClose()
      const before = MockWebSocket.instances.length
      vi.advanceTimersByTime(expectedDelays[i] - 1)
      expect(MockWebSocket.instances).toHaveLength(before) // 少 1ms 不触发重连
      vi.advanceTimersByTime(1)
      expect(MockWebSocket.instances).toHaveLength(before + 1)
    }
  })

  it('disconnect() 手动关闭后不自动重连', () => {
    const client = new WsClient()
    client.connect()
    const ws = MockWebSocket.instances[0]
    ws.emitOpen()

    client.disconnect()
    expect(client.status).toBe('closed')
    vi.advanceTimersByTime(60000)
    expect(MockWebSocket.instances).toHaveLength(1)
  })

  it('连接出错（onerror → close）同样走重连', () => {
    const client = new WsClient()
    client.connect()
    const ws = MockWebSocket.instances[0]
    ws.emitError()
    // onerror 内部同步调用 ws.close() → onclose：error 状态一闪而过，最终为 closed
    expect(client.status).toBe('closed')
    vi.advanceTimersByTime(1000)
    expect(MockWebSocket.instances).toHaveLength(2)
  })
})

describe('WsClient — 消息收发', () => {
  it('send() 在 open 时发送 JSON 并返回 true，未连接返回 false', () => {
    const client = new WsClient()
    client.connect()
    const ws = MockWebSocket.instances[0]

    expect(client.send({ type: 'chat', content: 'hi' })).toBe(false)

    ws.emitOpen()
    expect(client.send({ type: 'chat', content: 'hi' })).toBe(true)
    expect(ws.sent).toEqual([JSON.stringify({ type: 'chat', content: 'hi' })])
  })

  it('onMessage 解析并分发服务端消息，非法 JSON 不抛出', () => {
    const client = new WsClient()
    const received: WsServerMessage[] = []
    const unsubscribe = client.onMessage((msg) => received.push(msg))

    client.connect()
    const ws = MockWebSocket.instances[0]
    ws.emitMessage(JSON.stringify({ type: 'concern', content: '你好' }))
    expect(received).toHaveLength(1)
    expect(received[0]).toEqual({ type: 'concern', content: '你好' })

    expect(() => ws.emitMessage('not-json')).not.toThrow()
    expect(received).toHaveLength(1) // 非法消息被丢弃

    unsubscribe()
    ws.emitMessage(JSON.stringify({ type: 'concern', content: 'again' }))
    expect(received).toHaveLength(1) // 退订后不再分发
  })
})

describe('WsClient — 10s 心跳（PROTOCOL.md）', () => {
  it('连接 open 后每 10s 发送一次 heartbeat', () => {
    const client = new WsClient()
    client.connect()
    const ws = MockWebSocket.instances[0]
    ws.emitOpen()

    vi.advanceTimersByTime(9999)
    expect(ws.sent).toEqual([]) // 未满 10s 不发
    vi.advanceTimersByTime(1)
    expect(ws.sent).toEqual([JSON.stringify({ type: 'heartbeat' })])

    vi.advanceTimersByTime(10000)
    expect(ws.sent).toHaveLength(2) // 第二个周期
    expect(ws.sent[1]).toEqual(JSON.stringify({ type: 'heartbeat' }))
  })

  it('断开后心跳停止，不再发送', () => {
    const client = new WsClient()
    client.connect()
    const ws = MockWebSocket.instances[0]
    ws.emitOpen()
    vi.advanceTimersByTime(10000)
    expect(ws.sent).toHaveLength(1)

    client.disconnect() // 手动关闭 → 心跳停止
    vi.advanceTimersByTime(60000)
    expect(ws.sent).toHaveLength(1)
  })

  it('断线重连成功后心跳恢复', () => {
    const client = new WsClient()
    client.connect()
    const ws1 = MockWebSocket.instances[0]
    ws1.emitOpen()
    ws1.emitClose() // 非手动断开 → 1s 后重连

    vi.advanceTimersByTime(1000)
    const ws2 = MockWebSocket.instances[1]
    expect(ws2).toBeDefined()
    ws2.emitOpen()
    vi.advanceTimersByTime(10000)
    expect(ws2.sent).toEqual([JSON.stringify({ type: 'heartbeat' })])
  })
})

describe('resolveBusWsUrl — 总线地址解析（localStorage 覆盖）', () => {
  it('localStorage 无配置时回退默认总线地址', () => {
    expect(resolveBusWsUrl()).toBe(DEFAULT_BUS_WS_URL)
  })

  it('localStorage 有 firefly_server_url 时优先使用（Tailnet 部署覆盖）', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => 'ws://100.111.201.71:8767/ws/desktop',
      setItem: () => {},
      removeItem: () => {},
    })
    expect(resolveBusWsUrl()).toBe('ws://100.111.201.71:8767/ws/desktop')
  })
})

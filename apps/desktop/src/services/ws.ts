import type { WsClientMessage, WsServerMessage } from '@shared/index'

type MessageHandler = (msg: WsServerMessage) => void
type StatusHandler = (status: WsStatus) => void

export type WsStatus = 'connecting' | 'open' | 'closed' | 'error'

/** 总线地址默认值（PROTOCOL.md v1：bus WS /ws/desktop，BUS_WS_PORT 默认 8767）。
 *  Tailnet 部署时经 localStorage `firefly_server_url` 覆盖（settings.ts）。 */
export const DEFAULT_BUS_WS_URL = 'ws://100.111.201.71:8767/ws/desktop'

/** 解析总线地址：优先 localStorage `firefly_server_url`（设置 UI 写入），缺省回退默认值 */
export function resolveBusWsUrl(): string {
  try {
    if (typeof localStorage !== 'undefined') {
      const saved = localStorage.getItem('firefly_server_url')
      if (saved) return saved
    }
  } catch {
    // localStorage 不可用（SSR/隐私模式）→ 用默认值
  }
  return DEFAULT_BUS_WS_URL
}

/**
 * WebSocket 客户端 — 连接消息总线 bus（PROTOCOL.md：/ws/desktop）
 * - 指数退避重连（1s → 2s → 4s → 8s → 16s → 30s 上限）
 * - 连接 open 期间每 10s 发一次 heartbeat（驱动 bus 侧可达性，10s×3 置信由 bus 判定）
 */
export class WsClient {
  private ws: WebSocket | null = null
  private url: string
  private handlers: Set<MessageHandler> = new Set()
  private statusHandlers: Set<StatusHandler> = new Set()
  private reconnectTimer: number | null = null
  private heartbeatTimer: number | null = null
  private _status: WsStatus = 'closed'
  private _manualClose = false
  private _retryCount = 0
  private readonly MAX_BACKOFF = 30000
  private readonly BASE_DELAY = 1000
  private readonly HEARTBEAT_INTERVAL = 10000

  constructor(url: string = DEFAULT_BUS_WS_URL) {
    this.url = url
  }

  get status(): WsStatus {
    return this._status
  }

  get connected(): boolean {
    return this._status === 'open'
  }

  private setStatus(status: WsStatus) {
    this._status = status
    this.statusHandlers.forEach((h) => h(status))
  }

  /** 计算指数退避延迟：每次 ×2，上限 MAX_BACKOFF */
  private backoffDelay(): number {
    return Math.min(this.BASE_DELAY * Math.pow(2, this._retryCount), this.MAX_BACKOFF)
  }

  /** 连接 open 期间启动 10s 心跳（断开/关闭时停止） */
  private startHeartbeat() {
    this.stopHeartbeat()
    this.heartbeatTimer = window.setInterval(() => {
      this.send({ type: 'heartbeat' })
    }, this.HEARTBEAT_INTERVAL)
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return
    this._manualClose = false
    this.setStatus('connecting')
    this.ws = new WebSocket(this.url)
    this.ws.onopen = () => {
      console.log('[WS] 已连接', this.url)
      this._retryCount = 0
      this.startHeartbeat()
      this.setStatus('open')
    }
    this.ws.onmessage = (event) => {
      try {
        const msg: WsServerMessage = JSON.parse(event.data)
        this.handlers.forEach((h) => h(msg))
      } catch (e) {
        console.error('[WS] 解析消息失败:', e)
      }
    }
    this.ws.onclose = () => {
      this.stopHeartbeat()
      this.setStatus('closed')
      if (!this._manualClose) {
        const delay = this.backoffDelay()
        console.log(`[WS] 连接关闭，${delay / 1000}s 后重连 (第 ${this._retryCount + 1} 次)`)
        this.reconnectTimer = window.setTimeout(() => {
          this._retryCount++
          this.connect()
        }, delay)
      }
    }
    this.ws.onerror = () => {
      console.error('[WS] 连接错误')
      this.setStatus('error')
      this.ws?.close()
    }
  }

  disconnect() {
    this._manualClose = true
    this._retryCount = 0
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.stopHeartbeat()
    this.ws?.close()
    this.ws = null
    this.setStatus('closed')
  }

  /**
   * 发送消息。返回 true 表示已发送，false 表示连接未就绪。
   */
  send(msg: WsClientMessage): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
      return true
    }
    console.warn('[WS] 未连接，消息未发送:', msg.type)
    return false
  }

  onMessage(handler: MessageHandler) {
    this.handlers.add(handler)
    return () => {
      this.handlers.delete(handler)
    }
  }

  onStatus(handler: StatusHandler) {
    this.statusHandlers.add(handler)
    // 立即推送一次当前状态
    handler(this._status)
    return () => {
      this.statusHandlers.delete(handler)
    }
  }
}

export const wsClient = new WsClient(resolveBusWsUrl())

import type { WsClientMessage, WsServerMessage } from '@shared/index'

type MessageHandler = (msg: WsServerMessage) => void
type StatusHandler = (status: WsStatus) => void

export type WsStatus = 'connecting' | 'open' | 'closed' | 'error'

/** 总线地址默认值（PROTOCOL.md v1：bus WS /ws/desktop，BUS_WS_PORT 默认 8767）。
 *  Tailnet 部署时经 localStorage `firefly_server_url` 覆盖（settings.ts）。 */
export const DEFAULT_BUS_WS_URL = 'ws://100.111.201.71:8767/ws/desktop'

/** 解析总线地址：优先 localStorage `firefly_server_url`（设置 UI 写入），缺省回退默认值；
 *  有 `firefly_bus_ws_token` 时追加 `?token=`（T-18 🟠4，服务端 BUS_WS_TOKEN 恒定时间比较）。 */
export function resolveBusWsUrl(): string {
  let base = DEFAULT_BUS_WS_URL
  try {
    if (typeof localStorage !== 'undefined') {
      const saved = localStorage.getItem('firefly_server_url')
      if (saved) base = saved
    }
  } catch {
    // localStorage 不可用（SSR/隐私模式）→ 用默认值
  }

  let token = ''
  try {
    if (typeof localStorage !== 'undefined') {
      token = localStorage.getItem('firefly_bus_ws_token') ?? ''
    }
  } catch {
    // 同上
  }
  if (token) {
    const sep = base.includes('?') ? '&' : '?'
    base = `${base}${sep}token=${encodeURIComponent(token)}`
  }
  return base
}

// T-20 切单轨配套：启动时经 Tauri invoke 从配置文件（%APPDATA%\firefly-desktop\bus-token.txt）
// 预载 bus token 到 localStorage，使 resolveBusWsUrl() 自动带上 ?token=。
// 纯浏览器/测试环境无 __TAURI__ → 静默跳过（仍可用 localStorage 手动配置）。
// 修复（2026-08-06）：先取 invoke 引用再判空 + 整体 try/catch——
// 桌宠未开 withGlobalTauri 时 window.__TAURI__ 为 undefined，链式 .then 会抛 TypeError
// 中断模块加载，导致整个前端崩溃（main 白屏 + pet 透明无 Live2D）。
if (typeof window !== 'undefined') {
  try {
    const tauri = (
      window as {
        __TAURI__?: { core?: { invoke?: (cmd: string) => Promise<unknown> } }
      }
    ).__TAURI__
    const invoke = tauri?.core?.invoke
    if (invoke) {
      invoke('read_bus_token')
        .then((t: unknown) => {
          if (typeof t === 'string' && t) {
            try {
              localStorage.setItem('firefly_bus_ws_token', t)
              console.log('[WS] 已从配置文件载入 bus token')
            } catch {
              // localStorage 不可用 → 忽略
            }
          }
        })
        .catch(() => {
          // 配置文件不存在/command 未注册 → 忽略
        })
    }
  } catch {
    // 非 Tauri 环境/异常 → 忽略（绝不中断模块加载）
  }
}

/**
 * WebSocket 客户端 — 连接消息总线 bus（PROTOCOL.md：/ws/desktop）
 * - 指数退避重连（1s → 2s → 4s → 8s → 16s → 30s 上限）
 * - 连接 open 期间每 10s 发一次 heartbeat（驱动 bus 侧可达性，10s×3 置信由 bus 判定）
 */
export class WsClient {
  private ws: WebSocket | null = null
  private url: string
  private dynamicUrl: boolean
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

  constructor(url?: string) {
    // 显式传 URL（测试）→ 固定使用；未传（生产）→ 动态解析（每次重连重新 resolve，
    // 使 localStorage / 配置文件 token 变更即时生效，T-20 切单轨配套）
    this.dynamicUrl = url === undefined
    this.url = url ?? resolveBusWsUrl()
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
    if (this.dynamicUrl) this.url = resolveBusWsUrl()
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

export const wsClient = new WsClient()  // 不传 URL：dynamicUrl=true，重连时重新 resolve（token 预载/设置变更生效）

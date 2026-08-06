/**
 * services/api.ts 冒烟测试 — 覆盖 base URL 解析（dev / Tauri 打包两种形态）、
 * localStorage 覆盖语义、photoUrl 拼接。只测纯逻辑，不触发真实 fetch。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getApiBase, photoUrl } from './api'

function stubWindowLocation(protocol: string, hostname: string) {
  vi.stubGlobal('window', { location: { protocol, hostname } })
}

function stubLocalStorage(initial: Record<string, string> = {}) {
  const store = new Map<string, string>(Object.entries(initial))
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, v)
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
  })
}

beforeEach(() => {
  stubWindowLocation('http:', '127.0.0.1')
  stubLocalStorage()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('getApiBase — base URL 解析', () => {
  it('dev（非 Tauri）返回空串 → fetch 走相对路径', () => {
    expect(getApiBase()).toBe('')
  })

  it('Tauri 打包（tauri: 协议）优先读 localStorage firefly_http_base', () => {
    stubWindowLocation('tauri:', 'localhost')
    stubLocalStorage({ firefly_http_base: 'http://100.111.201.71:8765' })
    expect(getApiBase()).toBe('http://100.111.201.71:8765')
  })

  it('Tauri 打包（tauri.localhost 主机名）无配置时回退默认地址', () => {
    stubWindowLocation('http:', 'tauri.localhost')
    expect(getApiBase()).toBe('http://100.111.201.71:8765')
  })

  it('Tauri 打包但 localStorage 无 firefly_http_base 时回退默认地址', () => {
    stubWindowLocation('tauri:', 'localhost')
    expect(getApiBase()).toBe('http://100.111.201.71:8765')
  })
})

describe('photoUrl — 静态资源 URL 拼接', () => {
  it('dev 下返回相对路径，并去掉开头的 /', () => {
    expect(photoUrl('/playground.png')).toBe('/photo/playground.png')
  })

  it('Tauri 打包下拼接后端地址', () => {
    stubWindowLocation('tauri:', 'localhost')
    expect(photoUrl('work.png')).toBe('http://100.111.201.71:8765/photo/work.png')
  })
})

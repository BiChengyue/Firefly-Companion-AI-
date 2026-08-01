#!/usr/bin/env node
/**
 * 一键启动开发环境
 * 启动 Tauri 桌面应用（Python FastAPI 后端由 Tauri Sidecar 自动管理）。
 * 如需单独调试后端，使用 pnpm dev:server。
 */

import { spawn, execSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '..')
const SERVER_DIR = resolve(ROOT, 'apps/server')

// 注入 Rust 工具链路径，确保 Tauri 编译能找到 cargo。
// Windows：工具链装在 D 盘，需显式注入；
// macOS / Linux：rustup 默认装在 $HOME/.cargo，若不在 PATH 则补上。
if (process.platform === 'win32') {
  process.env.RUSTUP_HOME = 'D:\\rust\\.rustup'
  process.env.CARGO_HOME = 'D:\\rust\\.cargo'
  const cargoBin = 'D:\\rust\\.cargo\\bin'
  const pathParts = (process.env.PATH || '').split(';')
  if (!pathParts.includes(cargoBin)) {
    process.env.PATH = `${cargoBin};${process.env.PATH}`
  }
} else {
  const home = process.env.HOME || ''
  const cargoBin = resolve(home, '.cargo/bin')
  if (home && existsSync(cargoBin)) {
    const pathParts = (process.env.PATH || '').split(':')
    if (!pathParts.includes(cargoBin)) {
      process.env.PATH = `${cargoBin}:${process.env.PATH}`
    }
  }
}

const COLOR = {
  cyan: '\x1b[36m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  gray: '\x1b[90m',
  reset: '\x1b[0m',
}

function log(tag, msg) {
  const time = new Date().toLocaleTimeString()
  console.log(`${COLOR.gray}[${time}]${COLOR.reset} ${COLOR.cyan}[${tag}]${COLOR.reset} ${msg}`)
}

// 检查 Python 环境
function checkPython() {
  if (!existsSync(resolve(SERVER_DIR, 'main.py'))) {
    log('WARN', `${COLOR.yellow}Python 服务入口未找到，仅启动前端${COLOR.reset}`)
    return false
  }
  return true
}

// ═══════════════════════════════════════════════════════════════════════
// Python 后端现已由 Tauri Sidecar 自动管理（src-tauri/src/sidecar.rs）。
// 如需单独启动后端调试，使用 pnpm dev:server。
// 以下函数保留供参考，主流程不再调用。
// ═══════════════════════════════════════════════════════════════════════
// eslint-disable-next-line no-unused-vars
function startServerStandalone() {
  if (!checkPython()) return null

  const isWin = process.platform === 'win32'
  const venvPy = isWin
    ? resolve(ROOT, '.venv/Scripts/python.exe')
    : resolve(ROOT, '.venv/bin/python')
  const cmd = existsSync(venvPy) ? venvPy : (isWin ? 'python' : 'python3')

  const proc = spawn(cmd, ['-m', 'uvicorn', 'main:app', '--reload', '--port', '8765'], {
    cwd: SERVER_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    shell: isWin,
  })

  proc.stdout.on('data', (d) => log('SERVER', d.toString().trim()))
  proc.stderr.on('data', (d) => log('SERVER', d.toString().trim()))

  log('SERVER', `${COLOR.green}Python FastAPI 启动中... (port 8765)${COLOR.reset}`)
  return proc
}

// 启动 Tauri 前端
function startDesktop() {
  // Windows：detached 的控制台子进程会弹出新 cmd 窗口，故仅用 windowsHide 抑制，
  //          tree-kill 交给 taskkill /T（按父子孙树递归）；
  // Unix：detached 让子进程自成进程组，便于 -pid 一次性杀整组，且无窗口问题。
  const spawnOpts = {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    shell: true,
  }
  if (process.platform === 'win32') {
    spawnOpts.windowsHide = true
  } else {
    spawnOpts.detached = true
  }

  const proc = spawn('pnpm', ['--filter', 'desktop', 'tauri', 'dev'], spawnOpts)

  proc.stdout.on('data', (d) => log('DESKTOP', d.toString().trim()))
  proc.stderr.on('data', (d) => log('DESKTOP', d.toString().trim()))

  log('DESKTOP', `${COLOR.green}Tauri 桌面应用启动中...${COLOR.reset}`)
  return proc
}

// 跨平台杀掉整棵进程树（含所有孙进程：tauri → vite / esbuild 等 Node 进程、Python sidecar）。
// 仅 kill 直接子进程在 Windows(shell:true)下无效——壳被杀了，Vite/esbuild 仍会孤儿化残留。
function killTree(pid) {
  if (!pid) return
  try {
    if (process.platform === 'win32') {
      // /T 递归杀所有子孙，/F 强制终止
      spawn('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore', shell: true })
    } else {
      // detached 子进程自成进程组，用 -pid 一次性杀整组
      try { process.kill(-pid, 'SIGTERM') } catch { /* 已退出 */ }
      try { process.kill(pid, 'SIGTERM') } catch { /* 已退出 */ }
    }
  } catch { /* 忽略 */ }
}

// 兜底：杀掉占用端口的进程（Python 后端 sidecar），防止被孤儿化（跨平台）
function killPort(port) {
  try {
    if (process.platform === 'win32') {
      const out = execSync(`netstat -ano | findstr :${port}`, {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
      })
      const pids = new Set()
      for (const line of out.split('\n')) {
        const m = line.trim().match(/(\d+)\s*$/)
        if (m) pids.add(m[1])
      }
      for (const p of pids) {
        spawn('taskkill', ['/pid', p, '/F'], { stdio: 'ignore', shell: true })
      }
    } else {
      // macOS / Linux：lsof 找端口占用进程，fuser 兜底
      const pids = new Set()
      try {
        const out = execSync(`lsof -ti tcp:${port}`, {
          encoding: 'utf8',
          stdio: ['ignore', 'pipe', 'ignore'],
        })
        for (const line of out.split('\n')) {
          const p = line.trim()
          if (p) pids.add(p)
        }
      } catch { /* 端口无占用 */ }
      try {
        execSync(`fuser -k ${port}/tcp`, { stdio: 'ignore' })
      } catch { /* 无 fuser 或端口空闲 */ }
      for (const p of pids) {
        try { process.kill(Number(p), 'SIGKILL') } catch { /* 已退出 */ }
      }
    }
  } catch { /* 忽略 */ }
}

// 主流程
log('DEV', `${COLOR.green}启动 Firefly Companion 开发环境${COLOR.reset}`)

const desktop = startDesktop()

// 退出清理：杀整棵进程树 + 端口兜底，确保 Vite/esbuild/Python 全部退出
function cleanup() {
  log('DEV', `${COLOR.yellow}正在关闭服务...${COLOR.reset}`)
  killTree(desktop?.pid)
  killPort(8765)
  setTimeout(() => process.exit(0), 800)
}

process.on('SIGINT', cleanup)
process.on('SIGTERM', cleanup)

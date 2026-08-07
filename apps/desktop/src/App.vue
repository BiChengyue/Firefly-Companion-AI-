<script setup lang="ts">
import { onMounted, onUnmounted, watch, ref, computed, defineAsyncComponent } from 'vue'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { listen, emit } from '@tauri-apps/api/event'
import { useCompanionStore } from '@/stores/companion'
import { useSettingsStore } from '@/stores/settings'
import { wsClient } from '@/services/ws'
import { getMode, getCoreModelStatus, photoUrl } from '@/services/api'
import { useCtrlOverride } from '@/composables/useCtrlOverride'
import { useWsHandler } from '@/composables/useWsHandler'
import ErrorBoundary from '@/components/Common/ErrorBoundary.vue'
import ModelSetupOverlay from '@/components/Setup/ModelSetupOverlay.vue'
import ChatPanel from '@/components/Chat/ChatPanel.vue'
import SamHudPanel from '@/components/SamHUD/SamHudPanel.vue'
import TopBar from '@/components/Layout/TopBar.vue'
import LeftSidebar from '@/components/Sidebar/LeftSidebar.vue'
import RightPanel from '@/components/RightPanel/RightPanel.vue'
import ApprovalDialog from '@/components/SamHUD/ApprovalDialog.vue'
import ToastHost from '@/components/Common/ToastHost.vue'
import '@/assets/themes.css'

import ModeTransitionCanvas from '@/components/Common/ModeTransitionCanvas.vue'
import { useThemeTransition } from '@/composables/useThemeTransition'
import { useReminderScheduler } from '@/composables/useReminderScheduler'

/* 阶段5：Live2DPet 异步加载 — 避免 pixi-live2d-display 初始化崩溃波及主窗口 */
const Live2DPet = defineAsyncComponent({
  loader: () => import('@/components/Live2D/Live2DPet.vue'),
  onError(err) {
    console.warn('[App] Live2DPet 加载失败，桌宠窗口将显示占位:', err)
  },
})

const companion = useCompanionStore()
const settings = useSettingsStore()

const canvasRef = ref<InstanceType<typeof ModeTransitionCanvas> | null>(null)

const { executeTransition } = useThemeTransition()
const { activeToast, snoozeReminder, dismissReminder } = useReminderScheduler()

const currentWindow = getCurrentWindow()
const windowLabel = ref('')

const isMainWindow = computed(() => windowLabel.value === 'main')
const isPetWindow = computed(() => windowLabel.value === 'pet')

// ── 核心模型引导下载（首次启动缺模型时显示全屏引导页）──
const showModelSetup = ref(false)
const modelSetupSkipped = ref(false)

async function checkCoreModelsAndMaybeShowSetup() {
  // 「跳过」仅对本次会话有效（内存态），每次启动都会重新检查，
  // 避免 localStorage 标记在重装后仍残留导致永远不再弹出下载引导。
  if (modelSetupSkipped.value) return

  // 后端 sidecar 启动需要数秒（exe 解压 + 数据库初始化），首次请求可能连不上，
  // 轮询等待后端就绪（最多约 20 秒）后再判断核心模型是否需要下载。
  let status: Awaited<ReturnType<typeof getCoreModelStatus>> | null = null
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      status = await getCoreModelStatus()
      break
    } catch {
      // 后端尚未就绪，等 1 秒后重试
      await new Promise((r) => setTimeout(r, 1000))
    }
  }

  if (!status) {
    // 长时间无法连接后端：本次不打扰用户，下次启动再试
    return
  }
  if (status.ready) {
    modelSetupSkipped.value = true
    return
  }
  showModelSetup.value = true
}

function handleSetupSkip() {
  // 仅本次会话跳过，不写 localStorage；下次启动仍会再次检查下载
  modelSetupSkipped.value = true
  showModelSetup.value = false
}

function handleSetupClose() {
  modelSetupSkipped.value = true
  showModelSetup.value = false
}

const bgImage = computed(() =>
  companion.isWork ? `url(${photoUrl('work.png')})` : `url(${photoUrl('playground.png')})`,
)

// ── TTS 当前播放控制 ──
let currentAudio: HTMLAudioElement | null = null

// ── 模式切换过场台词（阶段10）──
const transitionLine = ref('')
const transitionToMode = ref<'daily' | 'work'>('daily')
const showTransitionLine = ref(false)
let transitionTimer: number | null = null

function showTransition(line: string, toMode: 'daily' | 'work') {
  if (transitionTimer) clearTimeout(transitionTimer)
  transitionLine.value = line
  transitionToMode.value = toMode
  showTransitionLine.value = true
  transitionTimer = window.setTimeout(() => {
    showTransitionLine.value = false
  }, 4000)
}

function playVoice(url: string, text: string) {
  // 语音队列（2026-08-07）：分条消息 N 条 voice_audio 几乎同时到达，
  // 原实现「新音频打断旧音频」→ 只听到最后一条——改为排队串行播放，逐条播完。
  voiceQueue.push({ url, text })
  drainVoice()
}

const voiceQueue: { url: string; text: string }[] = []
let voicePlaying = false

function drainVoice() {
  if (voicePlaying || voiceQueue.length === 0) return
  const { url, text } = voiceQueue.shift()!
  voicePlaying = true
  const audio = new Audio(url)
  currentAudio = audio
  const finish = () => {
    voicePlaying = false
    currentAudio = null
    audio.onended = null
    audio.onerror = null
    drainVoice()
  }
  audio.onended = finish
  audio.onerror = finish
  audio.play().catch(finish)
  try { emit('play-voice', { text }) } catch {}
  window.dispatchEvent(new CustomEvent('play-voice', { detail: { text } }))
}

if (currentWindow.label === 'pet') {
  useCtrlOverride()
}

  // ── WS 消息处理（抽取到 useWsHandler composable）──
  const { connect: connectWs } = useWsHandler({
    onVoice: playVoice,
    onTransitionLine: showTransition,
  })

  // T-27 C：App.vue 直接注册的 onStatus 需随组件卸载释放（HMR 重挂载不叠加）
  let unsubscribeWsStatus: (() => void) | null = null

  onMounted(async () => {
    windowLabel.value = currentWindow.label

    // 监听全局模式切换请求（带有点击坐标）
    window.addEventListener('trigger-mode-switch', (e: Event) => {
      const { event, mode } = (e as CustomEvent).detail || {}
      executeTransition(
        event,
        mode,
        () => {
          companion.mode = mode
          if (mode === 'daily') {
            companion.hudVisible = false
            companion.thinkVisible = false
            companion.stopBgm()
          } else {
            companion.hudVisible = true
            companion.thinkVisible = true
            if (companion.bgmEnabled) companion.playBgm()
          }
          wsClient.send({ type: 'mode_switch', mode })
        },
        canvasRef,
      )
    })

    // 只有主聊天窗口 (main) 需要连接 WS 与加载数据，避免桌宠窗口 (pet) 造成双重连接与重复发信
    if (currentWindow.label === 'main') {
      // 1. 立即连接 WS（不阻塞，让连接状态条尽快显示「已连接」）
      connectWs()

      // 0. 检测核心模型，缺失且未跳过 → 显示全屏引导下载页
      checkCoreModelsAndMaybeShowSetup()

      // 补充 WS 状态处理：会话层逻辑（模式同步、dailyUnlock 同步、断连保护）
      // T-27 C：保存退订函数，onUnmounted 释放——HMR 重建主窗口时不再叠加重复 handler
      unsubscribeWsStatus = wsClient.onStatus((status) => {
        // WS 连接建立 → 同步前端模式与 dailyUnlocked 状态到后端
        if (status === 'open') {
          wsClient.send({ type: 'mode_switch', mode: companion.mode })
          if (settings.dailyUnlocked) {
            wsClient.send({ type: 'daily_unlock', unlocked: true })
          }
        }
        if ((status === 'error' || status === 'closed') && companion.streaming) {
          companion.finishStreaming({
            id: `sys-${Date.now()}`,
            role: 'assistant',
            content: companion.currentStreamText || '[连接已断开]',
            createdAt: Date.now(),
          })
        }
      })

      // 2. 并行加载：会话/工作空间初始化 + 模式配置 + 头像列表
      //    WS 已连接，用户可立即发消息；数据在后台并行填充
      await Promise.all([
        companion.initialize(),
        getMode().then(m => companion.applyModeConfig(m)).catch(() => {}),
        companion.loadAvatars(),
      ])
    } else if (currentWindow.label === 'pet') {
      listen('pet-lock-changed', (event: { payload: { locked: boolean } }) => {
        companion.setPetLocked(event.payload.locked)
      })
    }
  })

// ── 语音开关同步到后端 ──
watch(() => companion.voiceEnabled, (enabled) => {
  wsClient.send({ type: 'voice_toggle', enabled })
  if (!enabled) {
    // 关闭语音：清空队列 + 停止当前播放
    voiceQueue.length = 0
    voicePlaying = false
    if (currentAudio) {
      currentAudio.pause()
      currentAudio.onended = null
      currentAudio.onerror = null
      currentAudio = null
    }
  }
})

// T-27 C：组件卸载（含 HMR 重建）时释放本组件注册的 WS 状态 handler
onUnmounted(() => {
  if (unsubscribeWsStatus) {
    unsubscribeWsStatus()
    unsubscribeWsStatus = null
  }
})
</script>

<template>
  <!-- 全局 Toast（Teleport 到 body，任意窗口均可触发） -->
  <ToastHost />

  <!-- ── 窗口 A：主聊天客户端（三栏式） ────────────────────── -->
  <div
    v-if="isMainWindow"
    class="chat-client-root"
    :class="companion.mode"
  >
    <ErrorBoundary>
      <LeftSidebar />
    </ErrorBoundary>

    <ErrorBoundary>
      <div class="center-panel">
        <div class="bg-layer" :style="{ backgroundImage: bgImage }" />
        <TopBar />
        <div class="chat-container">
          <ChatPanel />
        </div>
        <SamHudPanel />
      </div>
    </ErrorBoundary>

    <ErrorBoundary>
      <RightPanel />
    </ErrorBoundary>
    <ApprovalDialog />

    <!-- 🚀 首次启动核心模型引导下载页 -->
    <ModelSetupOverlay
      v-if="showModelSetup && isMainWindow"
      @close="handleSetupClose"
      @skip="handleSetupSkip"
    />

    <!-- 💥 高能 60FPS 变身特效 Canvas -->
    <ModeTransitionCanvas ref="canvasRef" />

    <!-- 🎭 模式切换过场台词（阶段10：firefly-skill 融合） -->
    <Transition name="line-fade">
      <div
        v-if="showTransitionLine"
        class="transition-line-banner"
        :class="transitionToMode === 'work' ? 'to-work' : 'to-daily'"
      >
        <div class="transition-line-icon">
          {{ transitionToMode === 'work' ? '⚙' : '✨' }}
        </div>
        <span class="transition-line-text">{{ transitionLine }}</span>
        <div class="transition-line-glow" />
      </div>
    </Transition>

    <!-- 🔔 到期全息提醒 Toast Banner -->
    <Transition name="toast-slide">
      <div v-if="activeToast" class="reminder-toast-banner">
        <div class="toast-content">
          <span class="toast-bell">🔔</span>
          <div class="toast-text-group">
            <span class="toast-title">提醒到期！</span>
            <span class="toast-body">{{ activeToast.text }}</span>
          </div>
        </div>
        <div class="toast-actions">
          <button class="toast-btn snooze" title="延长5分钟" @click="snoozeReminder(activeToast.id, 5)">+5分钟</button>
          <button class="toast-btn dismiss" title="知道了" @click="dismissReminder(activeToast.id)">知道了</button>
        </div>
      </div>
    </Transition>
  </div>

  <!-- ── 窗口 B：Live2D 桌宠窗口 ────────────────────────────── -->
  <Live2DPet v-else-if="isPetWindow" />
</template>

<style>
html,
body,
#app {
  margin: 0;
  padding: 0;
  height: 100%;
  width: 100%;
  background: transparent;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
</style>

<style scoped>
/* ── 主窗口：三栏 Grid ────────────────────────────────── */
.chat-client-root {
  height: 100%;
  width: 100%;
  display: grid;
  grid-template-columns: 240px 1fr 280px;
  grid-template-rows: 100%;
  box-sizing: border-box;
  background: var(--bg-root);
  transition: background var(--transition);
  overflow: hidden;
}

/* ── 中央面板 ─────────────────────────────────────── */
.center-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  border-left: 1px solid var(--border-main);
  border-right: 1px solid var(--border-main);
}

/* 背景图层：仅在中央聊天区，contain 模式显示全图 */
.bg-layer {
  position: absolute;
  inset: 0;
  background-size: contain;
  background-position: center;
  background-repeat: no-repeat;
  opacity: 0.22;
  pointer-events: none;
  z-index: 0;
  transition: opacity var(--transition);
}

/* 中央面板的子元素在背景之上 */
.center-panel > :not(.bg-layer) {
  position: relative;
  z-index: 1;
}

/* ── 中央面板内容（顶栏 + 聊天 + HUD） ────────────────────── */
.chat-container {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ── 全局提醒 Toast Banner ── */
.reminder-toast-banner {
  position: absolute;
  top: 48px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 99999;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-width: 320px;
  max-width: 460px;
  padding: 10px 16px;
  background: var(--bg-surface);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg), 0 0 16px var(--accent-glow);
}

.toast-content {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.toast-bell {
  font-size: 20px;
  animation: ring-bell 1s infinite alternate ease-in-out;
}

.toast-text-group {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.toast-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--accent-strong);
  text-transform: uppercase;
}

.toast-body {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.toast-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.toast-btn {
  border: none;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.toast-btn.snooze {
  background: rgba(255, 170, 0, 0.2);
  color: #d97706;
}

.toast-btn.snooze:hover {
  background: #d97706;
  color: #fff;
}

.toast-btn.dismiss {
  background: var(--accent);
  color: #fff;
}

.toast-btn.dismiss:hover {
  background: var(--accent-strong);
}

.toast-slide-enter-active,
.toast-slide-leave-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.toast-slide-enter-from,
.toast-slide-leave-to {
  opacity: 0;
  transform: translate(-50%, -20px);
}

/* ── 模式切换过场台词（阶段10）── */
.transition-line-banner {
  position: absolute;
  bottom: 100px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 99998;
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 500px;
  padding: 14px 22px;
  border-radius: 14px;
  backdrop-filter: blur(12px);
  text-align: left;
  overflow: hidden;
}

/* ▸▸ 切至工作模式：萨姆暗黑装甲风 */
.transition-line-banner.to-work {
  background: linear-gradient(135deg, rgba(20, 10, 5, 0.92), rgba(30, 15, 10, 0.85));
  border: 1px solid rgba(204, 51, 0, 0.3);
  box-shadow:
    0 0 20px rgba(204, 51, 0, 0.15),
    0 8px 32px rgba(0, 0, 0, 0.5),
    inset 0 0 30px rgba(204, 51, 0, 0.04);
}
.transition-line-banner.to-work .transition-line-text {
  color: rgba(240, 200, 180, 0.95);
}
.transition-line-banner.to-work .transition-line-icon {
  color: #cc3300;
  filter: drop-shadow(0 0 6px rgba(204, 51, 0, 0.6));
}
.transition-line-banner.to-work .transition-line-glow {
  position: absolute;
  top: -50%;
  left: -20%;
  width: 140%;
  height: 200%;
  background: radial-gradient(ellipse at center, rgba(204, 51, 0, 0.06), transparent 70%);
  pointer-events: none;
  animation: glow-pulse-work 2s ease-in-out infinite;
}

/* ▸▸ 切至日常模式：流萤萤火温馨风 */
.transition-line-banner.to-daily {
  background: linear-gradient(135deg, rgba(10, 30, 20, 0.88), rgba(15, 25, 18, 0.82));
  border: 1px solid rgba(100, 220, 140, 0.25);
  box-shadow:
    0 0 24px rgba(100, 220, 140, 0.12),
    0 8px 32px rgba(0, 0, 0, 0.4),
    inset 0 0 30px rgba(100, 220, 140, 0.03);
}
.transition-line-banner.to-daily .transition-line-text {
  color: rgba(200, 240, 210, 0.95);
}
.transition-line-banner.to-daily .transition-line-icon {
  color: #88ddaa;
  filter: drop-shadow(0 0 6px rgba(130, 220, 150, 0.5));
}
.transition-line-banner.to-daily .transition-line-glow {
  position: absolute;
  top: -50%;
  left: -20%;
  width: 140%;
  height: 200%;
  background: radial-gradient(ellipse at center, rgba(130, 220, 160, 0.06), transparent 70%);
  pointer-events: none;
  animation: glow-pulse-daily 2.5s ease-in-out infinite;
}

.transition-line-icon {
  font-size: 20px;
  flex-shrink: 0;
}

.transition-line-text {
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.4px;
  line-height: 1.7;
  position: relative;
  z-index: 1;
}

@keyframes glow-pulse-work {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}
@keyframes glow-pulse-daily {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.85; }
}

.line-fade-enter-active {
  transition: all 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}
.line-fade-leave-active {
  transition: all 0.8s ease-in;
}
.line-fade-enter-from {
  opacity: 0;
  transform: translate(-50%, 20px);
  filter: blur(4px);
}
.line-fade-leave-to {
  opacity: 0;
  transform: translate(-50%, -12px);
  filter: blur(2px);
}
</style>

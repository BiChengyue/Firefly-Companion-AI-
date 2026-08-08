<script setup lang="ts">
/** 顶栏 — 时间 / 标题 / 模式状态 / 通知 / 信号 + 模式切块。 */
import { ref, onMounted, onUnmounted } from 'vue'
import { useCompanionStore } from '@/stores/companion'
import { emit } from '@tauri-apps/api/event'
import { invoke } from '@tauri-apps/api/core'
import ModeSwitch from '@/components/Common/ModeSwitch.vue'

const companion = useCompanionStore()

// ── 实时时钟 ────────────────────────────────────────────
const time = ref('')
let timer: ReturnType<typeof setInterval> | null = null

function tick() {
  const now = new Date()
  time.value = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

onMounted(() => { tick(); timer = setInterval(tick, 15_000) })
onUnmounted(() => { if (timer) clearInterval(timer) })

// ── 桌宠锁定 ───────────────────────────────────────────
function togglePetLock() {
  const next = !companion.petLocked
  companion.setPetLocked(next)
  emit('pet-lock-changed', { locked: next })
}

// ── 桌宠点击穿透（T35）─────────────────────────────────
async function togglePassthrough() {
  const next = !companion.passthrough
  try {
    await invoke('set_cursor_passthrough', { passthrough: next })
    companion.setPassthrough(next)
  } catch {}
}
</script>

<template>
  <div class="top-bar">
    <!-- 左侧：时间 -->
    <div class="bar-left">
      <span class="time">{{ time }}</span>
    </div>

    <!-- 中间：标题 + 模式状态 -->
    <div class="bar-center">
      <span class="title">Firefly AI Agent</span>
      <span class="mode-tag" :class="companion.mode">
        {{ companion.isWork ? '● SAM SYSTEM ONLINE' : '✦ Daily Mode' }}
      </span>
    </div>

    <!-- 右侧：模式开关 + 快捷操作 -->
    <div class="bar-right">
      <ModeSwitch />
      <button
        class="voice-toggle-btn"
        :class="{ muted: !companion.voiceEnabled }"
        :title="companion.voiceEnabled ? '语音已开启：点击关闭' : '语音已关闭：点击开启'"
        @click="companion.toggleVoice()"
      >
        <span class="voice-icon">{{ companion.voiceEnabled ? '🔊' : '🔇' }}</span>
        <span class="voice-label">{{ companion.voiceEnabled ? '语音' : '静音' }}</span>
      </button>
      <button
        class="pet-lock-btn"
        :class="{ unlocked: !companion.petLocked }"
        :title="companion.petLocked ? '点击解锁：可按住拖拽调整桌宠位置' : '点击锁定：固定桌宠位置，点击即可互动'"
        @click="togglePetLock"
      >
        <span class="lock-icon">{{ companion.petLocked ? '🔓' : '🔒' }}</span>
        <span class="lock-label">{{ companion.petLocked ? '移动桌宠' : '锁定桌宠' }}</span>
      </button>
      <button
        class="pet-pt-btn"
        :class="{ passthrough: companion.passthrough }"
        :title="companion.passthrough ? '点击穿透已开：桌宠不挡鼠标；点击关闭可拖动/互动' : '点击穿透已关：桌宠可交互；点击开启穿透'"
        @click="togglePassthrough"
      >
        <span class="pt-icon">{{ companion.passthrough ? '👆' : '🖱️' }}</span>
        <span class="pt-label">{{ companion.passthrough ? '穿透' : '交互' }}</span>
      </button>
      <span class="signal">◉◉◉</span>
    </div>
  </div>
</template>

<style scoped>
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  padding: 0 var(--gap-md);
  background: var(--bg-glass);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.bar-left  { display: flex; align-items: center; min-width: 80px; }
.bar-center { display: flex; align-items: center; gap: var(--gap-sm); }
.bar-right { display: flex; align-items: center; gap: var(--gap-sm); }

.time {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: 0.5px;
}

.title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.mode-tag {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 10px;
  letter-spacing: 0.8px;
}

.mode-tag.daily {
  color: var(--accent);
  background: var(--accent-light);
}

.mode-tag.work {
  color: var(--accent);
  background: var(--accent-light);
  font-family: 'Courier New', monospace;
  text-shadow: 0 0 4px var(--accent-glow);
}

.pet-lock-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-surface);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  padding: 4px 10px;
  border-radius: 14px;
  transition: all var(--transition-fast);
  color: var(--text-secondary);
}

.pet-lock-btn:hover {
  background: var(--bg-surface-hover);
  border-color: var(--border-accent);
  color: var(--text-primary);
}

.pet-lock-btn.unlocked {
  background: var(--accent-light);
  border-color: var(--accent);
  color: var(--accent-strong);
}

/* T35：点击穿透切换按钮（复用锁按钮样式，穿透态高亮） */
.pet-pt-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-surface);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  padding: 4px 10px;
  border-radius: 14px;
  transition: all var(--transition-fast);
  color: var(--text-secondary);
}

.pet-pt-btn:hover {
  background: var(--bg-surface-hover);
  border-color: var(--border-accent);
  color: var(--text-primary);
}

.pet-pt-btn.passthrough {
  background: var(--accent-light);
  border-color: var(--accent);
  color: var(--accent-strong);
}

.voice-toggle-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-surface);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  padding: 4px 10px;
  border-radius: 14px;
  transition: all var(--transition-fast);
  color: var(--text-secondary);
}

.voice-toggle-btn:hover {
  background: var(--bg-surface-hover);
  border-color: var(--border-accent);
  color: var(--text-primary);
}

.voice-toggle-btn.muted {
  background: rgba(239, 68, 68, 0.08);
  border-color: rgba(239, 68, 68, 0.25);
  color: #ef4444;
}

.voice-icon {
  font-size: 13px;
  line-height: 1;
}

.voice-label {
  font-size: 12px;
  line-height: 1;
}

.lock-icon {
  font-size: 13px;
  line-height: 1;
}

.lock-label {
  font-size: 12px;
  line-height: 1;
}

.signal {
  font-size: 12px;
  color: var(--accent);
  letter-spacing: 2px;
}
</style>

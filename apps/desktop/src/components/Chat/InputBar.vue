<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { wsClient } from '@/services/ws'
import { useCompanionStore } from '@/stores/companion'
import type { ChatMessage } from '@shared/index'

import { useReminderScheduler } from '@/composables/useReminderScheduler'

const text = ref('')
const inputRef = ref<HTMLTextAreaElement | null>(null)
const emit = defineEmits<{ help: []; 'toggle-skill-picker': [] }>()
const companion = useCompanionStore()

const THEME = {
  daily: { border: 'rgba(46, 204, 113, 0.45)', bg: 'rgba(46, 204, 113, 0.18)' },
  work:  { border: 'rgba(204, 51, 0, 0.45)',   bg: 'rgba(204, 51, 0, 0.18)' },
}
const skillBtnStyle = computed(() => {
  const t = THEME[companion.mode as 'daily' | 'work'] ?? THEME.work
  return {
    borderColor: t.border,
    background: t.bg,
  }
})

/** textarea 自动扩展高度，上限 200px 后出现滚动条 */
function autoResize() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

// 监听左侧栏"新建任务" → 聚焦输入栏并预填
watch(
  () => companion.newTaskTrigger,
  async () => {
    text.value = '帮我 '
    await nextTick()
    inputRef.value?.focus()
  },
)

const canSend = computed(() => text.value.trim() !== '' && companion.wsConnected && !companion.streaming && !companion.isThinking && !companion.agentRunning)

const isRunning = computed(() => companion.streaming || companion.isThinking || companion.agentRunning)

function cancelGeneration() {
  companion.cancelGeneration()
  wsClient.send({ type: 'cancel' })
}

function send(e?: Event) {
  if (e && e instanceof KeyboardEvent && (e.isComposing || e.keyCode === 229)) return
  if (!canSend.value) return

  const content = text.value.trim()
  if (!content) return
  // 检查连接状态，未连接时给出本地提示
  if (!companion.wsConnected) {
    companion.setError('未连接到服务端，请确认后端服务已启动（端口 8765）')
    return
  }

  // 本地智能检测提醒口令，实现零延时 instant 刷弹提醒卡片
  if (/(提醒|闹钟|叫醒|定时)/.test(content)) {
    try {
      const { addReminder } = useReminderScheduler()
      addReminder(content)
    } catch { /* ignore */ }
  }

  // 立即追加用户消息到本地，让气泡瞬间出现（不等后端）
  const userMsg: ChatMessage = {
    id: `user-${Date.now()}`,
    role: 'user',
    content,
    createdAt: Date.now(),
  }
  companion.addMessage(userMsg)
  companion.clearError()

  const sent = wsClient.send({
    type: 'chat',
    content,
    sessionId: companion.activeSessionId ?? undefined,
    workspacePath: companion.activeWorkspace?.path ?? undefined,
  })
  if (!sent) {
    companion.setError('消息发送失败：WebSocket 未就绪')
  }
  text.value = ''
  nextTick(autoResize)
}

/** 外部（SkillPicker）选中 Skill 后追加触发短语到输入框 */
function appendSkillTrigger(skillName: string) {
  const trigger = `使用 ${skillName} skill `
  text.value = trigger + text.value
  nextTick(() => {
    inputRef.value?.focus()
    autoResize()
  })
}

defineExpose({ appendSkillTrigger })

/** Enter 发送，Shift+Enter 换行 */
function onEnter(e: KeyboardEvent) {
  if (e.shiftKey) return // Shift+Enter 正常换行
  e.preventDefault()
  send()
}
</script>

<template>
  <div class="input-bar">
    <button
      class="skill-btn"
      :style="skillBtnStyle"
      title="Skill 面板"
      :disabled="!companion.wsConnected"
      @click="emit('toggle-skill-picker')"
    >🪄</button>
    <textarea
      ref="inputRef"
      v-model="text"
      class="input-field"
      rows="1"
      :placeholder="companion.wsConnected ? '和流萤说点什么…' : '等待连接服务…'"
      :disabled="!companion.wsConnected"
      @keydown.enter="onEnter"
      @input="autoResize"
    />
    <button v-if="isRunning" class="stop-btn" title="终止生成" @click="cancelGeneration">⏹</button>
    <button v-else class="send-btn" :disabled="!canSend" @click="send">发送</button>
  </div>
</template>

<style scoped>
.input-bar {
  display: flex;
  gap: 8px;
  padding: 8px;
  background: var(--bg-glass);
  border-top: 1px solid var(--border-subtle);
  align-items: center;
  position: relative;
}
.skill-btn {
  width: 36px;
  height: 36px;
  border: 1px solid rgba(204, 51, 0, 0.25);
  border-radius: var(--radius-sm);
  background: rgba(204, 51, 0, 0.06);
  cursor: pointer;
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition-fast);
}
.skill-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.input-field {
  flex: 1;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  resize: none;
  overflow-y: auto;
  min-height: 32px;
  max-height: 200px;
  transition: border-color var(--transition-fast);
}
.input-field:focus {
  border-color: var(--border-accent);
}
.input-field:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.input-field::placeholder {
  color: var(--text-placeholder);
}
.send-btn {
  border: none;
  border-radius: var(--radius-sm);
  padding: 8px 16px;
  background: var(--accent);
  color: var(--text-on-accent);
  cursor: pointer;
  font-size: 14px;
  transition: opacity var(--transition-fast);
}
.send-btn:hover:not(:disabled) {
  opacity: 0.85;
}
.send-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.stop-btn {
  border: 2px solid #cc3300;
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  background: rgba(204, 51, 0, 0.15);
  color: #cc3300;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  min-width: 40px;
  aspect-ratio: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background var(--transition-fast), box-shadow var(--transition-fast);
}
.stop-btn:hover {
  background: rgba(204, 51, 0, 0.25);
  box-shadow: 0 0 10px rgba(204, 51, 0, 0.4);
}
</style>

<style>
/* 🪄 Skill 按钮主题 — 非 scoped，与 App.vue 的 .daily/.work 类联动 */
.daily .skill-btn {
  border-color: rgba(46, 204, 113, 0.3);
  background: rgba(46, 204, 113, 0.08);
}
.daily .skill-btn:hover:not(:disabled) {
  border-color: rgba(46, 204, 113, 0.55);
  background: rgba(46, 204, 113, 0.15);
  box-shadow: 0 0 8px rgba(46, 204, 113, 0.25);
}
.work .skill-btn {
  border-color: rgba(204, 51, 0, 0.28);
  background: rgba(204, 51, 0, 0.07);
}
.work .skill-btn:hover:not(:disabled) {
  border-color: rgba(204, 51, 0, 0.5);
  background: rgba(204, 51, 0, 0.13);
  box-shadow: 0 0 8px rgba(204, 51, 0, 0.22);
}
</style>

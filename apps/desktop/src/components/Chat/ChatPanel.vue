<script setup lang="ts">
import { ref, nextTick, watch, onMounted, onUnmounted, computed } from 'vue'
import type { ChatMessage } from '@shared/index'
import { useCompanionStore } from '@/stores/companion'
import MessageBubble from './MessageBubble.vue'
import InputBar from './InputBar.vue'
import SkillPicker from './SkillPicker.vue'

const companion = useCompanionStore()
const scrollRef = ref<HTMLElement | null>(null)
const inputBarRef = ref<InstanceType<typeof InputBar> | null>(null)
const showSkillPicker = ref(false)

/** 将流式文本包装为 ChatMessage 对象，复用 MessageBubble 的完整渲染（头像+泡泡+Markdown）。 */
const streamingMsg = computed<ChatMessage | null>(() => {
  if (!companion.streaming) return null
  return {
    id: '_streaming_',
    role: 'assistant',
    content: companion.currentStreamText || '',
    createdAt: Date.now(),
  }
})

// 自动滚动到底部
watch(
  () => [companion.messages.length, companion.currentStreamText],
  async () => {
    await nextTick()
    if (scrollRef.value) {
      scrollRef.value.scrollTop = scrollRef.value.scrollHeight
    }
  },
)

function toggleSkillPicker() {
  showSkillPicker.value = !showSkillPicker.value
}
function onSkillSelected(skillName: string) {
  showSkillPicker.value = false
  inputBarRef.value?.appendSkillTrigger(skillName)
}
function closeSkillPicker() {
  showSkillPicker.value = false
}

/** 点击面板外部关闭 */
function onDocumentClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (target.closest('.skill-picker-wrapper')) return
  showSkillPicker.value = false
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onUnmounted(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div class="chat-panel">
    <!-- 连接状态指示条 -->
    <div class="conn-status" :class="companion.wsConnected ? 'online' : 'offline'">
      <span class="dot" />
      {{ companion.wsConnected ? '已连接流萤' : '正在连接服务…' }}
    </div>

    <div ref="scrollRef" class="messages">
      <MessageBubble v-for="msg in companion.messages" :key="msg.id" :msg="msg" />
      <!-- 流式输出 → 复用 MessageBubble 的完整渲染（头像+泡泡+Markdown+主题） -->
      <MessageBubble v-if="companion.streaming && companion.currentStreamText" :msg="streamingMsg!" />
      <!-- 思考/流式占位（ACK 已收到但首个 Token 尚未到达，或已开始但还没有 token） -->
      <div v-else-if="companion.isThinking || companion.streaming" class="typing-indicator">
        <span class="typing-dot" /> <span class="typing-dot" /> <span class="typing-dot" />
      </div>
    </div>

    <!-- 错误提示条 -->
    <div v-if="companion.lastError" class="error-bar">
      <span class="err-icon">⚠</span>
      <span class="err-text">{{ companion.lastError }}</span>
      <button class="err-close" @click="companion.clearError()">×</button>
    </div>

    <div class="skill-picker-wrapper">
      <Transition name="picker-fade">
        <SkillPicker
          v-if="showSkillPicker"
          @select="onSkillSelected"
          @close="closeSkillPicker"
        />
      </Transition>
      <InputBar ref="inputBarRef" @toggle-skill-picker="toggleSkillPicker" />
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  position: relative;
  background: transparent;
}
.conn-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg-surface-hover);
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}
.conn-status .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.conn-status.online .dot {
  background: var(--accent);
  box-shadow: 0 0 6px var(--accent-glow);
}
.conn-status.offline .dot {
  background: #e0a040;
  animation: blink-warn 1s infinite;
}
@keyframes blink-warn {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}
.messages {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: 8px;
}
/* ── 流式打字指示器 ─────────────────────────────── */
.typing-indicator {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 8px 0;
  margin-left: 44px; /* 对齐助手头像右侧 */
}
.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: typing-bounce 1.2s infinite;
}
.typing-dot:nth-child(2) { animation-delay: 0.2s; }
.typing-dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-5px); opacity: 1; }
}
.error-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: rgba(180, 60, 60, 0.25);
  border-top: 1px solid rgba(220, 100, 100, 0.3);
  color: #f0a0a0;
  font-size: 12px;
}
.err-icon {
  flex-shrink: 0;
}
.err-text {
  flex: 1;
  word-break: break-all;
}
.err-close {
  flex-shrink: 0;
  background: none;
  border: none;
  color: #f0a0a0;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 0 4px;
}
.err-close:hover {
  color: #fff;
}

/* ── SkillPicker 容器 ─────────────────────────── */
.skill-picker-wrapper {
  position: relative;
  flex-shrink: 0;
}
.picker-fade-enter-active,
.picker-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.picker-fade-enter-from,
.picker-fade-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
</style>

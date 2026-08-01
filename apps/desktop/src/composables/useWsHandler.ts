/**
 * WebSocket 消息处理器 — 从 App.vue 抽取，处理所有 20 种 WS 消息类型。
 * 减少 App.vue 体积（612 → ~500 行），提升可维护性。
 */
import { useCompanionStore } from '@/stores/companion'
import { wsClient } from '@/services/ws'
import { useReminderScheduler } from '@/composables/useReminderScheduler'
import type { WsServerMessage } from '@shared/index'

interface WsHandlerCallbacks {
  /** 收到语音 URL 时回调 */
  onVoice: (url: string, text: string) => void
  /** 模式切换过场台词 */
  onTransitionLine: (line: string, toMode: 'daily' | 'work') => void
}

export function useWsHandler(callbacks: WsHandlerCallbacks) {
  const companion = useCompanionStore()

  function handleWsMessage(msg: WsServerMessage) {
    switch (msg.type) {
      // ── 服务端 ACK：已收到消息，立即展示思考指示 ──
      case 'ack_received':
        companion.startThinking()
        companion.clearError()
        break

      // ── 流式内容 ──
      case 'token':
        if (!companion.streaming) companion.startStreaming()
        companion.clearError()
        companion.appendToken(msg.delta)
        break

      case 'thinking':
        if (!companion.streaming) companion.startStreaming()
        companion.appendThinking(msg.delta)
        break

      case 'planning_thought':
        companion.appendPlanning(msg.delta)
        break

      case 'done':
        companion.finishStreaming(msg.message)
        break

      case 'error':
        companion.isThinking = false
        companion.setError(msg.message)
        if (companion.streaming) {
          companion.finishStreaming({
            id: `err-${Date.now()}`,
            role: 'assistant',
            content: companion.currentStreamText || '',
            createdAt: Date.now(),
          })
        }
        break

      // ── 多媒体 ──
      case 'voice_audio':
        if (companion.voiceEnabled && (msg.audioUrl || msg.audioBase64)) {
          const url = (msg.audioUrl as string) || `data:audio/wav;base64,${msg.audioBase64}`
          callbacks.onVoice(url, (msg.text as string) || '')
        }
        break

      case 'meme':
        companion.addMemeMessage(msg.id, msg.data, msg.createdAt)
        break

      case 'emotion':
        companion.setCurrentEmotion(msg.label)
        try {
          import('@tauri-apps/api/event').then(mod => mod.emit('emotion-changed', { label: msg.label }))
        } catch {}
        break

      // ── 关怀与提醒 ──
      case 'concern':
        companion.addConcernMessage(msg.content)
        break

      case 'proactive_speech':
        companion.addProactiveSpeech(
          msg.content,
          (msg as any).motion,
          (msg as any).expression,
        )
        break

      case 'reminder_created':
        if (msg.reminder) {
          const { addReminder } = useReminderScheduler()
          const delayMs = msg.reminder.dueTimestamp
            ? msg.reminder.dueTimestamp - Date.now()
            : undefined
          // fromApi=true：标记为后端权威确认，addReminder 会据此去重（升级本地即时卡）
          addReminder(msg.reminder.text, delayMs && delayMs > 0 ? delayMs : undefined, true)
        }
        break

      // ── Agent 执行 ──
      case 'step_update':
        companion.updateAgentStep(msg.step)
        break

      case 'agent_task':
        companion.setAgentTask(msg.task)
        break

      case 'compact_step':
        companion.markStepsCompacted(msg.step_ids)
        break

      case 'tool_call':
        break

      // ── 模式与配置 ──
      case 'mode_switched':
        companion.applyModeConfig({
          current: msg.mode,
          theme: msg.theme,
          hudVisible: msg.hudVisible,
          thinkVisible: msg.thinkVisible,
          proactiveCare: msg.proactiveCare,
        })
        break

      case 'token_usage':
        companion.setMessageTokenUsage(msg.messageId, msg.usage)
        break

      case 'daily_unlocked':
        // 后端状态已同步，前端无需额外处理
        break

      case 'transition_line':
        callbacks.onTransitionLine(msg.line, msg.to_mode)
        break

      // ── 记忆变更 ──
      case 'memory_updated':
        console.log('[App] 🧠 后台记忆提取完成，派发 memory-updated 事件:', msg)
        window.dispatchEvent(new CustomEvent('memory-updated', { detail: msg }))
        break
    }
  }

  /** 连接 WS 并绑定消息/状态处理器 */
  function connect() {
    wsClient.connect()

    wsClient.onStatus((status) => {
      companion.setWsConnected(status === 'open')
    })

    wsClient.onMessage(handleWsMessage)
  }

  return { handleWsMessage, connect }
}

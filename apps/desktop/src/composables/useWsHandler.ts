/**
 * WebSocket 消息处理器 — 从 App.vue 抽取，处理所有 WS 消息类型。
 * 总线协议（PROTOCOL.md v1）：bus 回 `ack`（入 inbox 确认）、`device_command`（说做分离动作）。
 * 减少 App.vue 体积（612 → ~500 行），提升可维护性。
 */
import { onUnmounted } from 'vue'
import { useCompanionStore } from '@/stores/companion'
import { wsClient } from '@/services/ws'
import { useReminderScheduler } from '@/composables/useReminderScheduler'
import { showToast } from '@/composables/useToast'
import type { WsDeviceCommand, WsServerMessage } from '@shared/index'

interface WsHandlerCallbacks {
  /** 收到语音 URL 时回调 */
  onVoice: (url: string, text: string) => void
  /** 模式切换过场台词 */
  onTransitionLine: (line: string, toMode: 'daily' | 'work') => void
}

/** 说做分离动作（§13.4）：本期只做「通知」类，其余显示提示不执行（执行待 C-3/后续） */
function handleDeviceCommand(command: WsDeviceCommand) {
  const { kind, payload } = command
  const summary = String(
    (payload && (payload.message ?? payload.text ?? payload.title)) || '',
  ).slice(0, 60)
  switch (kind) {
    case 'notify':
      showToast(summary || `新通知（${command.id}）`, 'info')
      break
    default:
      showToast(`收到指令 ${kind}（本期不执行）${summary ? `：${summary}` : ''}`, 'info')
      break
  }
}

export function useWsHandler(callbacks: WsHandlerCallbacks) {
  const companion = useCompanionStore()
  const cleanupFns: Array<() => void> = []

  // HMR/重挂载时清理已注册的 WS 处理器（2026-08-07 修复：onMessage/onStatus 的
  // 清理函数此前未保存，App.vue 热更新后 handler 叠加 → 同一条消息被处理多次 → 消息重复显示）
  onUnmounted(() => {
    cleanupFns.forEach((fn) => fn())
  })

  function handleWsMessage(msg: WsServerMessage) {
    // 2026-08-08：消息去处标注——带 target 且不含 desktop 的消息不在电脑端处理（bus 已按路由过滤，前端双保险）
    if ('target' in msg && typeof msg.target === 'string' && msg.target !== 'desktop') {
      return
    }
    switch (msg.type) {
      // ── 服务端 ACK：已收到消息，立即展示思考指示 + 启动生成超时计时（T-15）──
      case 'ack_received':
        companion.startThinking()
        companion.startGenerationTimer()
        companion.clearError()
        break

      // 总线协议：bus 确认用户消息已入 inbox（PROTOCOL.md）
      case 'ack':
        companion.startThinking()
        companion.startGenerationTimer()
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
        companion.handleGenerationError(msg.message)
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
        // T-20 切单轨：bus 以完整消息回包（不发流式 done）→ 收到即本轮结束，
        // 先复位 isThinking/streaming，避免按钮卡「停止」
        companion.settleRound()
        companion.addProactiveSpeech(
          msg.content,
          (msg as any).motion,
          (msg as any).expression,
        )
        break

      // 说做分离动作（PROTOCOL.md v1 / CONTRACTS §13.4）：本期只做「通知」类，其余提示不执行
      case 'device_command':
        handleDeviceCommand(msg.command)
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

    cleanupFns.push(
      wsClient.onStatus((status) => {
        companion.setWsConnected(status === 'open')
        // T-15：WS 断开/出错时若仍处于生成中 → 强制复位 + Toast，避免按钮卡死（重连后不卡）
        if (status === 'closed' || status === 'error') {
          if (companion.streaming || companion.isThinking || companion.agentRunning) {
            companion.forceResetGeneration('连接已断开')
          }
        }
      }),
    )

    cleanupFns.push(wsClient.onMessage(handleWsMessage))
  }

  return { handleWsMessage, connect }
}

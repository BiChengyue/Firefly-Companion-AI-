import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 设置状态管理 — 对应 spec 3.9.5。
 * 配置项全部 localStorage 持久化。
 * 敏感字段（API Key）使用 Base64 混淆存储，避免明文暴露。
 */
export const useSettingsStore = defineStore('settings', () => {
  // ── Base64 混淆工具（防本地明文暴露，非加密）──
  const SENSITIVE_KEYS = new Set(['firefly_api_key', 'firefly_minimax_api_key'])

  function obfuscate(text: string): string {
    if (!text) return ''
    try { return btoa(unescape(encodeURIComponent(text))) } catch { return text }
  }

  function deobfuscate(encoded: string): string {
    if (!encoded) return ''
    try { return decodeURIComponent(escape(atob(encoded))) } catch { return encoded }
  }

  function loadSensitive(key: string, fallback: string): string {
    const raw = localStorage.getItem(key)
    if (!raw) return fallback
    // 新格式带前缀标记，兼容旧明文数据
    if (raw.startsWith('b64:')) return deobfuscate(raw.slice(4))
    // 旧明文数据：读取后立即升级为混淆格式
    const upgraded = obfuscate(raw)
    localStorage.setItem(key, `b64:${upgraded}`)
    return raw
  }

  function saveSensitive(key: string, value: string) {
    if (!value) { localStorage.removeItem(key); return }
    localStorage.setItem(key, `b64:${obfuscate(value)}`)
  }

  // ── LLM 设置 ───────────────────────────────────────
  const llmProvider = ref(loadStr('firefly_llm_provider', 'zhipu'))
  const llmModel = ref(loadStr('firefly_llm_model', 'glm-4-plus'))
  const apiKey = ref(loadSensitive('firefly_api_key', ''))
  const llmBaseUrl = ref(loadStr('firefly_llm_base_url', ''))
  const llmMaxTokens = ref(loadNum('firefly_llm_max_tokens', 4096))
  const llmTemperature = ref(loadNum('firefly_llm_temperature', 0.8))
  const llmEnableThinking = ref(loadBool('firefly_llm_enable_thinking', true))
  const serverUrl = ref(loadStr('firefly_server_url', 'ws://127.0.0.1:8765'))
  const httpBaseUrl = ref(loadStr('firefly_http_base', 'http://127.0.0.1:8765'))

  // ── 网络设置 ───────────────────────────────────────
  const wsPort = ref(loadStr('firefly_ws_port', '8765'))
  const reconnectDelay = ref(loadNum('firefly_reconnect_delay', 5000))

  // ── 语音设置 ───────────────────────────────────────
  const voiceProvider = ref(loadStr('firefly_voice_provider', 'edge-tts'))
  const voiceId = ref(loadStr('firefly_voice_id', 'zh-CN-XiaoyiNeural'))
  const gptSovitsUrl = ref(loadStr('firefly_gpt_sovits_url', 'http://127.0.0.1:9880'))
  const gptSovitsPythonPath = ref(loadStr('firefly_gpt_sovits_python_path', ''))
  const minimaxApiKey = ref(loadSensitive('firefly_minimax_api_key', ''))
  const minimaxVoiceId = ref(loadStr('firefly_minimax_voice_id', ''))
  const autoPlayVoice = ref(loadBool('firefly_auto_play_voice', true))

  // ── 阶段9：日常模式解除限制 ──────────────────────
  const dailyUnlocked = ref(loadBool('firefly_daily_unlocked', false))

  function toggleDailyUnlock() {
    dailyUnlocked.value = !dailyUnlocked.value
    saveBool('firefly_daily_unlocked', dailyUnlocked.value)
  }

  // ── 阶段25：主动聊天设置 ──────────────────────────
  const proactiveChatEnabled = ref(loadBool('firefly_proactive_chat_enabled', true))
  const proactiveChatIdleMinutes = ref(loadNum('firefly_proactive_idle_minutes', 45))
  const proactiveChatQuietStart = ref(loadNum('firefly_proactive_quiet_start', 23))
  const proactiveChatQuietEnd = ref(loadNum('firefly_proactive_quiet_end', 8))
  const proactiveChatDailyLimit = ref(loadNum('firefly_proactive_daily_limit', 5))

  function saveProactiveChatSettings(settings: {
    enabled?: boolean
    idleMinutes?: number
    quietHoursStart?: number
    quietHoursEnd?: number
    dailyLimit?: number
  }) {
    if (settings.enabled !== undefined) {
      proactiveChatEnabled.value = settings.enabled
      saveBool('firefly_proactive_chat_enabled', settings.enabled)
    }
    if (settings.idleMinutes !== undefined) {
      proactiveChatIdleMinutes.value = settings.idleMinutes
      saveNum('firefly_proactive_idle_minutes', settings.idleMinutes)
    }
    if (settings.quietHoursStart !== undefined) {
      proactiveChatQuietStart.value = settings.quietHoursStart
      saveNum('firefly_proactive_quiet_start', settings.quietHoursStart)
    }
    if (settings.quietHoursEnd !== undefined) {
      proactiveChatQuietEnd.value = settings.quietHoursEnd
      saveNum('firefly_proactive_quiet_end', settings.quietHoursEnd)
    }
    if (settings.dailyLimit !== undefined) {
      proactiveChatDailyLimit.value = settings.dailyLimit
      saveNum('firefly_proactive_daily_limit', settings.dailyLimit)
    }
  }

  // ── 持久化工具 ─────────────────────────────────────
  function loadStr(key: string, fallback: string): string {
    return localStorage.getItem(key) ?? fallback
  }
  function loadNum(key: string, fallback: number): number {
    const v = localStorage.getItem(key)
    return v ? Number(v) : fallback
  }
  function loadBool(key: string, fallback: boolean): boolean {
    return localStorage.getItem(key) === 'true' || (!localStorage.getItem(key) && fallback)
  }
  function saveStr(key: string, value: string) {
    localStorage.setItem(key, value)
  }
  function saveNum(key: string, value: number) {
    localStorage.setItem(key, String(value))
  }
  function saveBool(key: string, value: boolean) {
    localStorage.setItem(key, String(value))
  }

  // ── Actions ────────────────────────────────────────
  function saveAllSettings(settings: {
    llmProvider?: string
    llmModel?: string
    apiKey?: string
    llmBaseUrl?: string
    llmMaxTokens?: number
    llmTemperature?: number
    llmEnableThinking?: boolean
    serverUrl?: string
    httpBaseUrl?: string
    wsPort?: string
    reconnectDelay?: number
    voiceProvider?: string
    voiceId?: string
    gptSovitsUrl?: string
    gptSovitsPythonPath?: string
    minimaxApiKey?: string
    minimaxVoiceId?: string
    autoPlayVoice?: boolean
  }) {
    if (settings.llmProvider !== undefined) {
      llmProvider.value = settings.llmProvider
      saveStr('firefly_llm_provider', settings.llmProvider)
    }
    if (settings.llmModel !== undefined) {
      llmModel.value = settings.llmModel
      saveStr('firefly_llm_model', settings.llmModel)
    }
    if (settings.apiKey !== undefined) {
      apiKey.value = settings.apiKey
      saveSensitive('firefly_api_key', settings.apiKey)
    }
    if (settings.llmBaseUrl !== undefined) {
      llmBaseUrl.value = settings.llmBaseUrl
      saveStr('firefly_llm_base_url', settings.llmBaseUrl)
    }
    if (settings.llmMaxTokens !== undefined) {
      llmMaxTokens.value = settings.llmMaxTokens
      saveNum('firefly_llm_max_tokens', settings.llmMaxTokens)
    }
    if (settings.llmTemperature !== undefined) {
      llmTemperature.value = settings.llmTemperature
      saveNum('firefly_llm_temperature', settings.llmTemperature)
    }
    if (settings.llmEnableThinking !== undefined) {
      llmEnableThinking.value = settings.llmEnableThinking
      saveBool('firefly_llm_enable_thinking', settings.llmEnableThinking)
    }
    if (settings.serverUrl !== undefined) {
      serverUrl.value = settings.serverUrl
      saveStr('firefly_server_url', settings.serverUrl)
    }
    if (settings.httpBaseUrl !== undefined) {
      httpBaseUrl.value = settings.httpBaseUrl
      saveStr('firefly_http_base', settings.httpBaseUrl)
    }
    if (settings.wsPort !== undefined) {
      wsPort.value = settings.wsPort
      saveStr('firefly_ws_port', settings.wsPort)
    }
    if (settings.reconnectDelay !== undefined) {
      reconnectDelay.value = settings.reconnectDelay
      saveNum('firefly_reconnect_delay', settings.reconnectDelay)
    }
    if (settings.voiceProvider !== undefined) {
      voiceProvider.value = settings.voiceProvider
      saveStr('firefly_voice_provider', settings.voiceProvider)
    }
    if (settings.voiceId !== undefined) {
      voiceId.value = settings.voiceId
      saveStr('firefly_voice_id', settings.voiceId)
    }
    if (settings.gptSovitsUrl !== undefined) {
      gptSovitsUrl.value = settings.gptSovitsUrl
      saveStr('firefly_gpt_sovits_url', settings.gptSovitsUrl)
    }
    if (settings.gptSovitsPythonPath !== undefined) {
      gptSovitsPythonPath.value = settings.gptSovitsPythonPath
      saveStr('firefly_gpt_sovits_python_path', settings.gptSovitsPythonPath)
    }
    if (settings.minimaxApiKey !== undefined) {
      minimaxApiKey.value = settings.minimaxApiKey
      saveSensitive('firefly_minimax_api_key', settings.minimaxApiKey)
    }
    if (settings.minimaxVoiceId !== undefined) {
      minimaxVoiceId.value = settings.minimaxVoiceId
      saveStr('firefly_minimax_voice_id', settings.minimaxVoiceId)
    }
    if (settings.autoPlayVoice !== undefined) {
      autoPlayVoice.value = settings.autoPlayVoice
      saveBool('firefly_auto_play_voice', settings.autoPlayVoice)
    }
  }

  function settingsChanged() {
    // 配置有变更时返回 true（供调用方判断是否需要重连等操作）
    return true
  }

  return {
    llmProvider,
    llmModel,
    apiKey,
    llmBaseUrl,
    llmMaxTokens,
    llmTemperature,
    llmEnableThinking,
    serverUrl,
    httpBaseUrl,
    wsPort,
    reconnectDelay,
    voiceProvider,
    voiceId,
    gptSovitsUrl,
    gptSovitsPythonPath,
    minimaxApiKey,
    minimaxVoiceId,
    autoPlayVoice,
    saveAllSettings,
    settingsChanged,
    dailyUnlocked,
    toggleDailyUnlock,
    // 阶段25：主动聊天
    proactiveChatEnabled,
    proactiveChatIdleMinutes,
    proactiveChatQuietStart,
    proactiveChatQuietEnd,
    proactiveChatDailyLimit,
    saveProactiveChatSettings,
  }
})

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { AppMode, ModeConfig, ChatMessage, Session, AgentTask, TaskStep, EmotionLabel } from '@shared/index'
import { useThinkingStore } from '@/stores/thinking'
import * as api from '@/services/api'
import { showToast } from '@/composables/useToast'

/**
 * 伴侣状态管理 — 模式切换状态机 + 阶段3 后端持久化。
 */
export const useCompanionStore = defineStore('companion', () => {
  // === 状态 ===
  const mode = ref<AppMode>('daily')
  const theme = ref<Record<string, unknown>>({})
  const hudVisible = ref(false)
  const thinkVisible = ref(false)
  const proactiveCare = ref(true)
  const messages = ref<ChatMessage[]>([])
  const streaming = ref(false)
  const isThinking = ref(false) // ACK 已收到但首个 Token 尚未到达的过渡状态
  const currentStreamText = ref('')
  // 思考状态委托给 useThinkingStore（负责 currentThinking / currentPlanning / thinkingHistory）
  const thinkingStore = useThinkingStore()
  const passthrough = ref(true)
  const interactionLocked = ref(false)
  const petLocked = ref(false) // T35：默认可拖动（点击即拖拽移动桌宠位置）；TopBar 锁按钮可切回锁定互动
  // 连接与错误状态
  const wsConnected = ref(false)
  const lastError = ref('')

  // 左侧栏交互
  const newTaskTrigger = ref(0)
  // 工作空间（后端持久化，path=文件目录）
  const workspaces = ref<{ id: string; name: string; path: string; isDefault?: boolean; pathExists?: boolean }[]>([])
  const activeWorkspaceId = ref<string | null>(localStorage.getItem('firefly_active_ws') || null)

  // T34：深色模式开关（localStorage `firefly_dark_mode` 持久化；work 模式强制萨姆暗红不受其影响）
  // 命名 themeMode 而非 theme——theme 已被「模式配置对象」占用（applyModeConfig）
  const themeMode = ref<'light' | 'dark'>(
    localStorage.getItem('firefly_dark_mode') === 'dark' ? 'dark' : 'light',
  )

  function toggleThemeMode() {
    themeMode.value = themeMode.value === 'dark' ? 'light' : 'dark'
    try {
      localStorage.setItem('firefly_dark_mode', themeMode.value)
    } catch {
      // localStorage 不可用（隐私模式）→ 仅本次会话生效
    }
  }

  // 阶段3：会话管理（数据源：后端 SQLite，localStorage 仅作 activeSessionId 缓存）
  const sessions = ref<Session[]>([])
  const activeSessionId = ref<string | null>(null)
  let _initializing = false  // 防止 refreshSessionsFromBackend 竞态覆盖恢复的会话
  // 阶段4：Agent 任务状态
  const agentTask = ref<AgentTask | null>(null)
  const agentRunning = ref(false)
  const approvalPendingId = ref<string | null>(null)
  const sessionsLoaded = ref(false)

  // === 计算属性 ===
  const isDaily = computed(() => mode.value === 'daily')
  const isWork = computed(() => mode.value === 'work')
  const activeSession = computed(() =>
    sessions.value.find((s) => s.id === activeSessionId.value) ?? null,
  )

  // ── 阶段3：从后端加载会话历史 ──────────────────────────
  /** T-28：后端历史按 (role, content, createdAt) 去重——修复重复入库导致的重启后
   *  显示多条相同发送/相同回答（bus 双实例时代的旧脏数据 + 防御未来重复）。 */
  function dedupeHistory<T extends { role?: unknown; content?: unknown; createdAt?: unknown }>(history: T[]): T[] {
    const seen = new Set<string>()
    return history.filter((h) => {
      const key = `${String(h.role)}|${String(h.content)}|${String(h.createdAt)}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }

  async function loadSessionHistory(sessionId: string): Promise<ChatMessage[]> {
    try {
      const history = await api.getSessionHistory(sessionId, 50)
      return dedupeHistory(history).map((h, i) => ({
        // 优先用后端真实行 id（供"删除单条消息"使用），后端未返回时退回临时索引 id
        id: typeof h.id === 'number' && Number.isFinite(h.id) ? String(h.id) : `h-${sessionId}-${i}`,
        role: h.role as 'user' | 'assistant' | 'system',
        content: h.content,
        emotion: h.emotion as ChatMessage['emotion'],
        mode: mode.value,
        createdAt: h.createdAt,
      }))
    } catch (e) {
      console.warn(`[store] 加载会话 ${sessionId} 历史失败:`, e)
      return []
    }
  }

  // ── localStorage 兜底 ──────────────────────────────────
  function loadSessionsFromLS(): Session[] {
    try {
      const raw = localStorage.getItem('firefly_sessions')
      return raw ? JSON.parse(raw) : []
    } catch {
      return []
    }
  }

  function loadActiveSessionIdFromLS(): string | null {
    return localStorage.getItem('firefly_active_session') || null
  }

  function saveActiveSessionIdToLS() {
    if (activeSessionId.value) {
      localStorage.setItem('firefly_active_session', activeSessionId.value)
    } else {
      localStorage.removeItem('firefly_active_session')
    }
  }

  // ── localStorage 持久化 ──────────────────────────────
  function saveSessionsToLS() {
    try {
      // 只存轻量字段，不存 messages
      const light = sessions.value.map(s => ({
        id: s.id, title: s.title, createdAt: s.createdAt, updatedAt: s.updatedAt,
      }))
      localStorage.setItem('firefly_sessions', JSON.stringify(light))
    } catch { /* ignore */ }
  }

  // ── 初始化（异步，由 App.vue 在 onMounted 中调用）───────
  async function initialize() {
    _initializing = true
    // 1. 立即从 localStorage 加载会话列表（瞬间显示，不阻塞）
    sessions.value = loadSessionsFromLS()
    sessionsLoaded.value = true

    // 2. 立即从 localStorage 加载工作空间缓存
    try {
      const raw = localStorage.getItem('firefly_workspaces')
      if (raw) workspaces.value = JSON.parse(raw)
    } catch { /* ignore */ }

    // 3. 确定活动会话并加载历史（带重试）
    const restoredId = loadActiveSessionIdFromLS()
    const restored = restoredId ? sessions.value.find((s) => s.id === restoredId) : null

    if (restored) {
      activeSessionId.value = restoredId
      messages.value = await loadSessionHistoryWithRetry(restoredId!)
    } else if (sessions.value.length > 0) {
      const latest = sessions.value.reduce((a, b) => (a.updatedAt > b.updatedAt ? a : b))
      activeSessionId.value = latest.id
      saveActiveSessionIdToLS()
      messages.value = await loadSessionHistoryWithRetry(latest.id)
    }

    // 4. 后台刷新会话列表与工作空间（不阻塞初始化流程）
    _initializing = false
    refreshSessionsFromBackend()
    loadWorkspacesFromBackend()
  }

  /** 后台从后端刷新会话列表（带重试），成功后更新 localStorage 缓存 */
  async function refreshSessionsFromBackend() {
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const list = await api.getSessions()
        sessions.value = list.map((si) => ({
          id: si.id,
          title: si.title,
          messages: [],
          createdAt: si.createdAt,
          updatedAt: si.updatedAt,
        }))
        saveSessionsToLS()
        console.log(`[store] ✅ 从后端刷新 ${sessions.value.length} 个会话`)
        // 后端刷新成功后，如果没有活动会话且未在初始化中，设置最新的
        if (sessions.value.length > 0 && !activeSessionId.value && !_initializing) {
          const latest = sessions.value.reduce((a, b) => (a.updatedAt > b.updatedAt ? a : b))
          activeSessionId.value = latest.id
          saveActiveSessionIdToLS()
          messages.value = await loadSessionHistoryWithRetry(latest.id)
        }
        // 如果当前活动会话历史为空，重新加载
        if (activeSessionId.value && messages.value.length === 0) {
          messages.value = await loadSessionHistoryWithRetry(activeSessionId.value)
        }
        return
      } catch (e) {
        console.warn(`[store] 后台刷新会话失败 (尝试 ${attempt + 1}/5):`, e)
        if (attempt < 4) {
          await new Promise(resolve => setTimeout(resolve, 1500))
        }
      }
    }
    console.warn('[store] 后台刷新会话列表失败，保留 localStorage 数据')
    // 如果刷新后仍无会话，创建默认会话
    if (sessions.value.length === 0 && !activeSessionId.value) {
      await createSessionInBackend('默认会话')
    }
  }

  /** 加载会话历史（带重试，后端可能还在启动中） */
  async function loadSessionHistoryWithRetry(sessionId: string): Promise<ChatMessage[]> {
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const history = await api.getSessionHistory(sessionId, 50)
        // T-28 审查 🟠：与 loadSessionHistory 共用同一去重（初始化/恢复会话路径也要防重复显示）
        return dedupeHistory(history).map((h, i) => ({
          // 用后端真实行 id（供"删除单条消息"删除数据库记录）；后端未返回时退回临时索引 id
          id: typeof h.id === 'number' && Number.isFinite(h.id) ? String(h.id) : `h-${sessionId}-${i}`,
          role: h.role as 'user' | 'assistant' | 'system',
          content: h.content,
          emotion: h.emotion as ChatMessage['emotion'],
          mode: mode.value,
          createdAt: h.createdAt,
        }))
      } catch (e) {
        console.warn(`[store] 加载会话历史失败 (尝试 ${attempt + 1}/5):`, e)
        if (attempt < 4) {
          await new Promise(resolve => setTimeout(resolve, 1500))
        }
      }
    }
    return []
  }

  // ── 后端创建会话 ───────────────────────────────────────
  async function createSessionInBackend(title?: string) {
    const id = `s${Date.now()}`
    try {
      await api.createSession(id, title || '新会话', mode.value)
    } catch (e) {
      console.warn('[store] 后端创建会话失败:', e)
    }
    const s: Session = {
      id,
      title: title || '新会话',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    sessions.value.unshift(s)
    activeSessionId.value = id
    messages.value = []
    saveActiveSessionIdToLS()
  }

  // === Actions ===
  /** 应用模式配置；bus/companion 回包可能缺字段（T-18 🟠3）→ 对 undefined 取默认值兜底 */
  function applyModeConfig(config: Partial<ModeConfig>) {
    mode.value = config.current ?? 'daily'
    theme.value = config.theme ?? { name: 'firefly' }
    hudVisible.value = config.hudVisible ?? true
    thinkVisible.value = config.thinkVisible ?? true
    proactiveCare.value = config.proactiveCare ?? true
  }

  function addMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function addMemeMessage(id: string, data: string, createdAt: number) {
    messages.value.push({
      id,
      role: 'assistant',
      content: '',
      meme: data,
      createdAt,
    })
  }

  /** 处理主动关怀消息 */
  function addConcernMessage(content: string) {
    messages.value.push({
      id: `concern-${Date.now()}`,
      role: 'assistant',
      content: `[关怀] ${content}`,
      createdAt: Date.now(),
    })
  }

  /** 处理主动聊天推送（引擎 B + 复查跟进） */
  function addProactiveSpeech(content: string, motion?: string, expression?: string) {
    messages.value.push({
      id: `proactive-${Date.now()}`,
      role: 'assistant',
      content,
      createdAt: Date.now(),
    })
    // Live2D 动作联动 + 气泡提示
    try {
      import('@tauri-apps/api/event').then(mod =>
        mod.emit('proactive-speech-trigger', { motion, expression, content })
      )
    } catch {}
  }

  /**
   * 本轮生成完成复位（T-20 切单轨配套，2026-08-06）：
   * bus 以完整消息（proactive_speech）回包、不发流式 done → 收到即视为本轮结束，
   * 复位 isThinking/streaming 与 90s 计时器，避免按钮卡「停止」。
   */
  function settleRound() {
    clearGenerationTimer()
    isThinking.value = false
    streaming.value = false
    agentRunning.value = false
    currentStreamText.value = ''
    thinkingStore.clearThinking()
  }

  // ── 生成超时/异常兜底（T-15）：发送后 90s 未复位 → 强制复位，避免按钮卡死 ──
  const GENERATION_TIMEOUT_MS = 90000
  let generationTimer: number | null = null

  /** 启动生成超时计时器（幂等：先清再设）——发送成功 / bus ack 时调用 */
  function startGenerationTimer() {
    clearGenerationTimer()
    generationTimer = window.setTimeout(() => {
      generationTimer = null
      forceResetGeneration('回复超时，可重试')
    }, GENERATION_TIMEOUT_MS)
  }

  /** 清除生成超时计时器——收到回复/error/取消/断开时调用 */
  function clearGenerationTimer() {
    if (generationTimer !== null) {
      clearTimeout(generationTimer)
      generationTimer = null
    }
  }

  /** 强制复位全部生成状态（超时/断连兜底），不追加消息 */
  function forceResetGeneration(message?: string) {
    clearGenerationTimer()
    isThinking.value = false
    streaming.value = false
    agentRunning.value = false
    currentStreamText.value = ''
    thinkingStore.clearThinking()
    thinkingStore.currentPlanning = ''
    if (message) showToast(message, 'warning')
  }

  /** 生成错误兜底（T-15）：清计时器 + 复位生成状态 + 展示错误；流式中保留已生成文本 */
  function handleGenerationError(message: string) {
    clearGenerationTimer()
    isThinking.value = false
    agentRunning.value = false
    setError(message)
    showToast(message, 'error')
    if (streaming.value) {
      finishStreaming({
        id: `err-${Date.now()}`,
        role: 'assistant',
        content: currentStreamText.value || '',
        createdAt: Date.now(),
      })
    }
  }

  function startThinking() {
    isThinking.value = true
    streaming.value = false
    currentStreamText.value = ''
    thinkingStore.clearThinking()
  }

  function startStreaming() {
    isThinking.value = false
    streaming.value = true
    currentStreamText.value = ''
    thinkingStore.clearThinking()
  }

  function appendToken(delta: string) {
    currentStreamText.value += delta
  }

  function appendThinking(delta: string) {
    thinkingStore.currentThinking += delta
  }

  function appendPlanning(delta: string) {
    thinkingStore.currentPlanning += delta
  }

  function finishStreaming(msg: ChatMessage) {
    clearGenerationTimer()
    isThinking.value = false
    streaming.value = false
    currentStreamText.value = ''
    // 思考历史归档（流结束前保存）
    if (thinkingStore.currentThinking.trim()) {
      thinkingStore.appendThinking({
        id: `think-${Date.now()}`,
        content: thinkingStore.currentThinking,
        timestamp: Date.now(),
        type: 'thinking',
      })
    }
    thinkingStore.clearThinking()
    addMessage(msg)
  }

  /** 用户点击终止生成 — 立即重置 UI 状态，等待后端 done 统一处理消息 */
  function cancelGeneration() {
    clearGenerationTimer()
    isThinking.value = false
    streaming.value = false
    agentRunning.value = false
    thinkingStore.clearThinking()
    thinkingStore.currentPlanning = ''
  }

  function setPassthrough(value: boolean) {
    passthrough.value = value
  }

  function setInteractionLocked(value: boolean) {
    interactionLocked.value = value
  }

  function setPetLocked(value: boolean) {
    petLocked.value = value
  }

  function setWsConnected(value: boolean) {
    wsConnected.value = value
  }

  function setError(message: string) {
    lastError.value = message
  }

  function clearError() {
    lastError.value = ''
  }

  function triggerNewTask() {
    newTaskTrigger.value++
  }

  // ── 阶段3：会话管理（后端优先）────────────────────────

  async function createSession(title?: string) {
    await createSessionInBackend(title)
  }

  async function switchSession(id: string) {
    if (activeSessionId.value === id) return

    // 保存旧会话的 currentStreamText（如果有的话，它还没成为持久化消息）
    activeSessionId.value = id
    saveActiveSessionIdToLS()

    // 从后端加载目标会话历史
    messages.value = await loadSessionHistory(id)
  }

async function deleteSession(id: string) {
  // 调用后端删除；失败则保留本地项并提示，避免"UI 消失但数据库仍在"的假删
  try {
    await api.deleteSession(id)
  } catch (e) {
    const errMsg = (e as { message?: string })?.message
    console.warn('[store] 后端删除会话失败:', e)
    showToast(`会话删除失败，已保留在本地${errMsg ? `：${errMsg}` : ''}`, 'error')
    return
  }

  // 后端删除成功后，才同步更新本地列表
  const idx = sessions.value.findIndex((s) => s.id === id)
  if (idx === -1) return
  sessions.value.splice(idx, 1)
  if (activeSessionId.value === id) {
    activeSessionId.value = sessions.value[0]?.id ?? null
    saveActiveSessionIdToLS()
    if (activeSessionId.value) {
      messages.value = await loadSessionHistory(activeSessionId.value)
    } else {
      messages.value = []
    }
  }
  showToast('会话已删除', 'success')
}

async function deleteMessage(msgId: string) {
  // 从消息列表移除（后端持久化记录随后同步删除）
  const idx = messages.value.findIndex((m) => m.id === msgId)
  if (idx === -1) return
  const msg = messages.value[idx]
  const msgContent = typeof msg.content === 'string' ? msg.content : ''
  messages.value.splice(idx, 1)

  const numericId = Number(msgId)
  const isRealId = /^\d+$/.test(msgId)

  // 先删后端（历史消息用真实 id；本次会话刚发送的临时 id 消息用"内容+角色"匹配删除，
  // 避免"UI 删了但数据库还在、重开后消息又回来"）
  let backendOk = true
  if (activeSessionId.value) {
    try {
      if (isRealId) {
        await api.deleteMessage(activeSessionId.value, numericId)
      } else {
        await api.deleteMessageByContent(activeSessionId.value, msg.role, msgContent)
      }
    } catch (e) {
      const errMsg = (e as { message?: string })?.message
      console.warn('[store] 后端删除单条消息失败:', e)
      backendOk = false
      // 后端删除失败则回滚本地，避免"UI 消失但数据库仍在"
      if (msg) messages.value.splice(idx, 0, msg)
      showToast(`消息删除失败${errMsg ? `：${errMsg}` : ''}`, 'error')
    }
  }
  void backendOk
}

async function renameSession(id: string, title: string) {
  const session = sessions.value.find((s) => s.id === id)
  if (!session) return
  const oldTitle = session.title
  session.title = title
  try {
    await api.renameSession(id, title)
  } catch (e) {
    session.title = oldTitle
    const errMsg = (e as { message?: string })?.message
    showToast(`重命名失败${errMsg ? `：${errMsg}` : ''}`, 'error')
  }
}

  function clearCurrentSession() {
    messages.value = []
    resetSessionTokens()
    thinkingStore.clearHistory()
  }

  // ── 阶段4：Agent 任务操作 ────────────────────────────
  function setAgentTask(task: AgentTask) {
    agentTask.value = task
    if (task.status === 'planning' || task.status === 'running') {
      agentRunning.value = true
    } else {
      agentRunning.value = false
      // 规划思考历史归档
      if (thinkingStore.currentPlanning.trim()) {
        thinkingStore.appendThinking({
          id: `plan-${Date.now()}`,
          content: thinkingStore.currentPlanning,
          timestamp: Date.now(),
          type: 'planning',
        })
      }
      thinkingStore.currentPlanning = ''  // Agent 结束，清空规划思考
      // 完成/失败的任务加入历史
      if (task.status === 'done' || task.status === 'failed') {
        addTaskHistory(task)
      }
    }
  }

  function updateAgentStep(step: TaskStep) {
    if (!agentTask.value) return
    const idx = agentTask.value.steps.findIndex((s) => s.id === step.id)
    if (idx >= 0) {
      agentTask.value.steps[idx] = { ...step }
    }
  }

  function setApprovalPending(stepId: string | null) {
    approvalPendingId.value = stepId
  }

  function clearAgentTask() {
    agentTask.value = null
    agentRunning.value = false
    approvalPendingId.value = null
    thinkingStore.currentPlanning = ''
    compactedStepIds.value.clear()
  }

  // ── 阶段23：上下文压缩 ─────────────────────────────
  const compactedStepIds = ref<Set<string>>(new Set())

  function markStepsCompacted(ids: string[]) {
    for (const id of ids) {
      compactedStepIds.value.add(id)
    }
  }

  function isStepCompacted(stepId: string): boolean {
    return compactedStepIds.value.has(stepId)
  }

  // ── 工作空间管理（后端 + localStorage缓存）──────

  const activeWorkspace = computed(() =>
    workspaces.value.find(w => w.id === activeWorkspaceId.value) ?? null
  )

  async function loadWorkspacesFromBackend() {
    // 先从 localStorage 加载缓存，让侧边栏瞬间显示
    try {
      const raw = localStorage.getItem('firefly_workspaces')
      if (raw) workspaces.value = JSON.parse(raw)
    } catch { /* ignore */ }

    // 再从后端刷新（带重试）
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const list = await api.getWorkspaces()
        workspaces.value = list
        // 缓存到 localStorage 供下次启动快速显示
        localStorage.setItem('firefly_workspaces', JSON.stringify(list))
        console.log(`[store] ✅ 从后端加载 ${list.length} 个工作空间`)
        return
      } catch (e) {
        if (attempt < 4) {
          await new Promise(resolve => setTimeout(resolve, 1500))
        }
      }
    }
    console.warn('[store] 工作空间后端加载失败，保留缓存数据')
  }

  async function addWorkspace(name: string, path: string) {
    try {
      const cwd = await api.createWorkspace(name, path)
      workspaces.value.unshift(cwd)
    } catch (e) { console.warn('[store] 创建工作空间失败:', e) }
  }

  async function removeWorkspace(id: string) {
    // 内置默认工作空间禁止删除
    if (id === '__builtin__') return
    try {
      await api.deleteWorkspace(id)
    } catch (e) { console.warn('[store] 删除工作空间失败:', e) }
    workspaces.value = workspaces.value.filter(w => w.id !== id)
    if (activeWorkspaceId.value === id) {
      activeWorkspaceId.value = null
      localStorage.removeItem('firefly_active_ws')
    }
  }

  function setActiveWorkspace(id: string | null) {
    activeWorkspaceId.value = id
    if (id) localStorage.setItem('firefly_active_ws', id)
    else localStorage.removeItem('firefly_active_ws')
  }

  // 旧 activeWorkspace 兼容
  const activeWorkspaceName = ref(localStorage.getItem('firefly_active_workspace') || 'agent')

  // ── 阶段5：Live2D 联动 ─────────────────────────────
  const currentEmotion = ref<EmotionLabel | null>(null)
  const live2dConnected = ref(false)

  // ── 阶段7：语音开关 ─────────────────────────────
  const voiceEnabled = ref(true)

  function toggleVoice() {
    voiceEnabled.value = !voiceEnabled.value
  }

  // ── 阶段9：BGM 控制 ──────────────────────────────
  // 是否启用工作模式自动播放 BGM（默认启用，持久化到 localStorage）
  const bgmEnabled = ref(localStorage.getItem('firefly_bgm_enabled') !== 'false')
  const bgmPlaying = ref(false)
  let bgmAudio: HTMLAudioElement | null = null
  let bgmTimer: ReturnType<typeof setTimeout> | null = null

  function playBgm() {
    stopBgm()
    try {
      bgmAudio = new Audio('/music/HOYO-MiX - 永不复焉 Nevermore.ogg')
      bgmAudio.volume = 0.5
      bgmAudio.play().then(() => {
        bgmPlaying.value = true
        bgmTimer = setTimeout(() => {
          stopBgm()
        }, 20000)
      }).catch(() => {
        bgmPlaying.value = false
      })
    } catch {
      bgmPlaying.value = false
    }
  }

  function stopBgm() {
    if (bgmTimer) { clearTimeout(bgmTimer); bgmTimer = null }
    if (bgmAudio) {
      bgmAudio.pause()
      bgmAudio.onended = null
      bgmAudio = null
    }
    bgmPlaying.value = false
  }

  // 切换"工作模式是否自动播放 BGM"并持久化；关闭时立即停止当前播放
  function toggleBgmEnabled() {
    bgmEnabled.value = !bgmEnabled.value
    localStorage.setItem('firefly_bgm_enabled', bgmEnabled.value ? 'true' : 'false')
    if (!bgmEnabled.value) stopBgm()
  }

  // ── 阶段9：头像轮换 ─────────────────────────────
  const avatarIndexDaily = ref(parseInt(localStorage.getItem('firefly_avatar_daily') || '0', 10))
  const avatarIndexWork = ref(parseInt(localStorage.getItem('firefly_avatar_work') || '0', 10))
  // 动态头像列表（从后端 API 加载，fallback 到硬编码）
  const dailyAvatars = ref<string[]>([])
  const workAvatars = ref<string[]>([])
  const avatarsLoaded = ref(false)

  // 与 apps/desktop/public/photo（打包后 FIREFLY_ROOT/public/photo）实际文件保持一致，
  // 仅作为后端未就绪时的离线兜底，不含已不存在的 daily6/daily9 等旧版文件
  const DEFAULT_DAILY_FALLBACK = [
    'daily1.jpeg', 'daily2.jpg', 'daily3.png', 'daily4.jpeg',
    'daily5.jpg', 'daily7.jpeg', 'daily8.jpeg', 'daily10.png',
    'daily11.png', 'daily12.png', 'daily13.png', 'daily17.jpeg',
    'daily18.png', 'daily19.png', 'daily20.png', 'daily21.jpeg',
    'daily22.jpeg'
  ].map(api.photoUrl)
  const DEFAULT_WORK_FALLBACK = [
    'work1.png', 'work2.jpg', 'work3.png', 'work4.png',
    'work5.png', 'work6.jpg', 'work7.png'
  ].map(api.photoUrl)

  /** 保存当前选中状态（同时写盘索引数字与特定文件名） */
  function saveCurrentAvatarState() {
    if (isWork.value) {
      localStorage.setItem('firefly_avatar_work', String(avatarIndexWork.value))
      const cur = workAvatars.value[avatarIndexWork.value % (workAvatars.value.length || 1)]
      if (cur) localStorage.setItem('firefly_avatar_work_file', cur.split('/').pop() || '')
    } else {
      localStorage.setItem('firefly_avatar_daily', String(avatarIndexDaily.value))
      const cur = dailyAvatars.value[avatarIndexDaily.value % (dailyAvatars.value.length || 1)]
      if (cur) localStorage.setItem('firefly_avatar_daily_file', cur.split('/').pop() || '')
    }
  }

  /** 依据已保存的文件名智能唤醒重定位，确保重启程序后精准恢复上次选择 */
  function restoreSavedAvatarState() {
    const savedDailyFile = localStorage.getItem('firefly_avatar_daily_file')
    if (savedDailyFile && dailyAvatars.value.length > 0) {
      const idx = dailyAvatars.value.findIndex(u => u.endsWith(savedDailyFile))
      if (idx !== -1) avatarIndexDaily.value = idx
    }
    const savedWorkFile = localStorage.getItem('firefly_avatar_work_file')
    if (savedWorkFile && workAvatars.value.length > 0) {
      const idx = workAvatars.value.findIndex(u => u.endsWith(savedWorkFile))
      if (idx !== -1) avatarIndexWork.value = idx
    }
  }

  /** 从后端加载头像列表（具备离线缓存与启动异步重试）
   *  - 非重试模式：先等后端 /health 就绪再请求，防止 /photo 挂载前 404 死锁
   *  - 后端超时则走离线缓存/fallback，异步重试最终恢复 */
  async function loadAvatars(isRetry = false) {
    // ── 等待后端 sidecar 就绪（/photo 静态路由挂载完成）──
    if (!isRetry) {
      await api.waitForBackend(15000, 600)
    }
    try {
      const [dailyRes, workRes] = await Promise.all([
        api.getAvatars('daily'),
        api.getAvatars('work'),
      ])
      if (dailyRes.avatars && dailyRes.avatars.length > 0) {
        dailyAvatars.value = dailyRes.avatars.map(a => api.photoUrl(a.filename))
        localStorage.setItem('firefly_daily_avatars_cache_v2', JSON.stringify(dailyAvatars.value))
      }
      if (workRes.avatars && workRes.avatars.length > 0) {
        workAvatars.value = workRes.avatars.map(a => api.photoUrl(a.filename))
        localStorage.setItem('firefly_work_avatars_cache_v2', JSON.stringify(workAvatars.value))
      }
      avatarsLoaded.value = true
    } catch {
      // 1. 优先读取上一次成功的离线真实全量缓存
      const cachedDaily = localStorage.getItem('firefly_daily_avatars_cache_v2')
      const cachedWork = localStorage.getItem('firefly_work_avatars_cache_v2')
      if (cachedDaily && cachedWork) {
        try {
          dailyAvatars.value = JSON.parse(cachedDaily)
          workAvatars.value = JSON.parse(cachedWork)
        } catch {
          dailyAvatars.value = DEFAULT_DAILY_FALLBACK
          workAvatars.value = DEFAULT_WORK_FALLBACK
        }
      } else {
        dailyAvatars.value = DEFAULT_DAILY_FALLBACK
        workAvatars.value = DEFAULT_WORK_FALLBACK
      }
      avatarsLoaded.value = true

      // 2. 启动竞态防线：若因后端未就绪进入 catch，1.5 秒后自动静默重试一次（等待后端就绪）
      if (!isRetry) {
        setTimeout(() => {
          loadAvatars(true)
        }, 1500)
      }
    } finally {
      // 加载完成后精准恢复上一次挑选的特定头像
      restoreSavedAvatarState()
    }
  }

  /** 获取当前分类的头像列表 */
  function getAvatarList(): string[] {
    const list = isWork.value ? workAvatars.value : dailyAvatars.value
    if (list.length > 0) return list
    return isWork.value ? DEFAULT_WORK_FALLBACK : DEFAULT_DAILY_FALLBACK
  }

  function getCurrentAvatar(): string {
    const list = getAvatarList()
    const index = isWork.value ? avatarIndexWork.value : avatarIndexDaily.value
    return list[Math.abs(index) % list.length] || api.photoUrl('avatar.png')
  }

  /** 直接选择特定图像 */
  function selectAvatar(category: 'daily' | 'work', filename: string) {
    const list = category === 'work' ? workAvatars.value : dailyAvatars.value
    const idx = list.findIndex(u => u.endsWith(filename))
    if (idx !== -1) {
      if (category === 'work') {
        avatarIndexWork.value = idx
      } else {
        avatarIndexDaily.value = idx
      }
      saveCurrentAvatarState()
    }
  }

  function rotateAvatar() {
    // getAvatarList() 已保证返回非空列表（含默认 fallback），count 恒 > 0
    const list = getAvatarList()
    const count = list.length
    if (isWork.value) {
      avatarIndexWork.value = (avatarIndexWork.value + 1) % count
    } else {
      avatarIndexDaily.value = (avatarIndexDaily.value + 1) % count
    }
    saveCurrentAvatarState()
  }

  function rotateAvatarLeft() {
    const list = getAvatarList()
    const count = list.length
    if (isWork.value) {
      avatarIndexWork.value = (avatarIndexWork.value - 1 + count) % count
    } else {
      avatarIndexDaily.value = (avatarIndexDaily.value - 1 + count) % count
    }
    saveCurrentAvatarState()
  }

  /** 当某张头像加载失败 (404) 时，响应式从列表中物理剔除，彻底杜绝 Vue 重新渲染后的破图回退 */
  function markAvatarFailed(badUrl: string) {
    if (!badUrl) return
    dailyAvatars.value = dailyAvatars.value.filter(u => u !== badUrl)
    workAvatars.value = workAvatars.value.filter(u => u !== badUrl)
    // 剔除后同步修正索引：若当前索引越界则回绕到有效范围，避免"当前头像"与列表错位
    if (dailyAvatars.value.length > 0 && avatarIndexDaily.value >= dailyAvatars.value.length) {
      avatarIndexDaily.value = avatarIndexDaily.value % dailyAvatars.value.length
    }
    if (workAvatars.value.length > 0 && avatarIndexWork.value >= workAvatars.value.length) {
      avatarIndexWork.value = avatarIndexWork.value % workAvatars.value.length
    }
  }

  /** 刷新头像列表（上传/删除后调用） */
  async function refreshAvatars() {
    avatarsLoaded.value = false
    await loadAvatars()
  }

  // ── 阶段9：Token 用量更新 ────────────────────────
  const sessionTotalPrompt = ref(0)
  const sessionTotalCompletion = ref(0)
  const sessionTotalTokens = ref(0)

  function setMessageTokenUsage(messageId: string, usage: import('@shared/index').TokenUsage) {
    const msg = messages.value.find(m => m.id === messageId)
    if (msg) {
      msg.tokenUsage = usage
    }
    // 累计会话统计（仅助手消息计入）
    sessionTotalPrompt.value += usage.promptTokens || 0
    sessionTotalCompletion.value += usage.completionTokens || 0
    sessionTotalTokens.value += usage.totalTokens || 0
  }

  function resetSessionTokens() {
    sessionTotalPrompt.value = 0
    sessionTotalCompletion.value = 0
    sessionTotalTokens.value = 0
  }

  function setCurrentEmotion(emotion: EmotionLabel | null) {
    currentEmotion.value = emotion
  }

  function setLive2DConnected(connected: boolean) {
    live2dConnected.value = connected
  }

  // ── Agent 任务历史 ────────────────────────────────
  const taskHistory = ref<AgentTask[]>([])

  function addTaskHistory(task: AgentTask) {
    taskHistory.value.unshift(task)
    if (taskHistory.value.length > 50) taskHistory.value = taskHistory.value.slice(0, 50)
  }

  return {
    mode,
    theme,
    hudVisible,
    thinkVisible,
    proactiveCare,
    messages,
    streaming,
    isThinking,
    currentStreamText,
    currentThinking: thinkingStore.currentThinking,
    currentPlanning: thinkingStore.currentPlanning,
    passthrough,
    interactionLocked,
    petLocked,
    wsConnected,
    lastError,
    isDaily,
    isWork,
    // T34：深色模式开关
    themeMode,
    toggleThemeMode,
    sessions,
    activeSessionId,
    activeSession,
    sessionsLoaded,
    initialize,
    applyModeConfig,
    addMessage,
    addMemeMessage,
    addConcernMessage,
    addProactiveSpeech,
    startStreaming,
    settleRound,
    appendToken,
    appendThinking,
    appendPlanning,
    clearThinking: thinkingStore.clearThinking,
    clearPlanning: () => { thinkingStore.currentPlanning = '' },
    thinkingHistory: thinkingStore.thinkingHistory,
    clearThinkingHistory: thinkingStore.clearHistory,
    finishStreaming,
    cancelGeneration,
    // T-15：生成超时/异常兜底
    startGenerationTimer,
    clearGenerationTimer,
    forceResetGeneration,
    handleGenerationError,
    setPassthrough,
    setInteractionLocked,
    setPetLocked,
    setWsConnected,
    setError,
    clearError,
    startThinking,
    newTaskTrigger,
    triggerNewTask,
    createSession,
    // 新工作空间系统
    workspaces,
    activeWorkspaceId,
    activeWorkspace,
    loadWorkspacesFromBackend,
    addWorkspace,
    removeWorkspace,
    setActiveWorkspace,
    activeWorkspaceName,
    switchSession,
    deleteSession,
    deleteMessage,
    renameSession,
    clearCurrentSession,
    // 阶段4：Agent 任务
    agentTask,
    agentRunning,
    approvalPendingId,
    setAgentTask,
    updateAgentStep,
    setApprovalPending,
    clearAgentTask,
    // 阶段23：上下文压缩
    compactedStepIds,
    markStepsCompacted,
    isStepCompacted,
    // 任务历史
    taskHistory,
    addTaskHistory,
    // 阶段5：Live2D 联动
    currentEmotion,
    live2dConnected,
    setCurrentEmotion,
    setLive2DConnected,
    // 阶段7：语音开关
    voiceEnabled,
    toggleVoice,
    // 阶段9：BGM + 头像 + Token
    bgmPlaying,
    bgmEnabled,
    playBgm,
    stopBgm,
    toggleBgmEnabled,
    avatarIndexDaily,
    avatarIndexWork,
    dailyAvatars,
    workAvatars,
    avatarsLoaded,
    loadAvatars,
    refreshAvatars,
    markAvatarFailed,
    getAvatarList,
    getCurrentAvatar,
    selectAvatar,
    rotateAvatar,
    rotateAvatarLeft,
    setMessageTokenUsage,
    sessionTotalPrompt,
    sessionTotalCompletion,
    sessionTotalTokens,
    resetSessionTokens,
  }
})

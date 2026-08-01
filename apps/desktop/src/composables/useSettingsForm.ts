/** useSettingsForm — 设置对话框中所有表单状态 / 保存 / 供应商 / 工具 / 语音 / 头像管理。

避免在 SettingsModal.vue 及其子组件之间重复定义状态：
- 所有表单 ref 集中于此
- handleSave / loadProviders / handleRefreshMcp / handleDeleteMcp 等行动函数
- 子 Tab 组件通过 provide/inject 或 props 获取所需字段
*/
import { ref, computed } from 'vue'
import { useCompanionStore } from '@/stores/companion'
import { useSettingsStore } from '@/stores/settings'
import { wsClient } from '@/services/ws'
import {
  diagnosePing, diagnoseLLM, getTools, updateConfig,
  getVoiceList, getApiBase, getGptSovitsStatus, startGptSovits, stopGptSovits,
  getMcpServers, deleteMcpServer, refreshMcpServer,
  getMcpRawConfig, saveMcpRawConfig,
  reloadSkills, importSkillFile, importSkillFolder, listSkills, deleteSkill,
  getAudioCacheStats, cleanupAudioCache,
  getAvatars, uploadAvatar, deleteAvatar, getProviders,
} from '@/services/api'
import type {
  ToolInfo, GptSovitsStatus, AudioCacheStats, AvatarInfo, ProviderInfo,
  SkillMeta,
} from '@/services/api'
import type { VoiceOption, McpServerStatus } from '@shared/index'

// ── 内部类型 ──
interface ModelFileInfo {
  name: string; local_path: string; size_mb: number
  exists: boolean; bundled: boolean; file_size_mb: number | null
}
interface ModelStatus {
  engine_ready: boolean; total_files: number; present_files: number
  missing_files: number; download_size_mb: number; engine_dir: string
  firefly_dir: string; files: ModelFileInfo[]
}
interface DownloadProgress {
  overall_percent: number; overall_downloaded_mb: number
  overall_total_mb: number; current_file: string; file_percent: number
}

export function useSettingsForm() {
  const companion = useCompanionStore()
  const settings = useSettingsStore()

  // ── 表单 refs ──
  const formLlmProvider = ref(settings.llmProvider)
  const formLlmModel = ref(settings.llmModel)
  const formApiKey = ref(settings.apiKey)
  const formLlmBaseUrl = ref(settings.llmBaseUrl)
  const formLlmMaxTokens = ref(settings.llmMaxTokens)
  const formLlmTemperature = ref(settings.llmTemperature)
  const formEnableThinking = ref(settings.llmEnableThinking)
  const formServerUrl = ref(settings.serverUrl)
  const formHttpBaseUrl = ref(settings.httpBaseUrl)
  const formWsPort = ref(settings.wsPort)
  const formReconnectDelay = ref(settings.reconnectDelay)

  // 语音设置
  const formVoiceProvider = ref(settings.voiceProvider)
  const formVoiceId = ref(settings.voiceId)
  const formGptSovitsUrl = ref(settings.gptSovitsUrl)
  const formGptSovitsPythonPath = ref(settings.gptSovitsPythonPath)
  const formMinimaxApiKey = ref(settings.minimaxApiKey)
  const formMinimaxVoiceId = ref(settings.minimaxVoiceId)
  const formAutoPlayVoice = ref(settings.autoPlayVoice)

  // 主动聊天设置
  const formProactiveChatEnabled = ref(settings.proactiveChatEnabled)
  const formProactiveChatIdleMinutes = ref(settings.proactiveChatIdleMinutes)
  const formProactiveChatQuietStart = ref(settings.proactiveChatQuietStart)
  const formProactiveChatQuietEnd = ref(settings.proactiveChatQuietEnd)
  const formProactiveChatDailyLimit = ref(settings.proactiveChatDailyLimit)

  const voiceList = ref<VoiceOption[]>([])
  const testingVoice = ref(false)
  const sampleAudio = ref<HTMLAudioElement | null>(null)

  // ── Toast / 消息 ──
  const toast = ref('')
  function showToast(msg: string) { toast.value = msg }

  // ── 供应商 / 模型 ──
  const providerList = ref<ProviderInfo[]>([])
  const providersLoaded = ref(false)
  const providersError = ref(false)
  const currentModels = ref<Array<{ id: string; name: string; maxTokens: number }>>([])
  const isCustomProvider = computed(() => formLlmProvider.value === '__custom__')

  async function loadProviders() {
    providersError.value = false
    try {
      const res = await getProviders()
      providerList.value = res.providers
      syncProviderDefaults()
    } catch {
      providersError.value = true
    }
    providersLoaded.value = true
  }

  function syncProviderDefaults() {
    const pid = formLlmProvider.value
    if (!pid || pid === '__custom__') { currentModels.value = []; return }
    const p = providerList.value.find(x => x.id === pid)
    if (p) {
      formLlmBaseUrl.value = p.baseUrl
      currentModels.value = p.models
      if (!formLlmTemperature.value) formLlmTemperature.value = p.temperature
      if (currentModels.value.length && !currentModels.value.find(m => m.id === formLlmModel.value)) {
        formLlmModel.value = currentModels.value[0].id
      }
      syncModelMaxTokens()
    }
  }

  function onProviderChange() {
    if (formLlmProvider.value === '__custom__') { currentModels.value = []; return }
    syncProviderDefaults()
  }

  function onModelChange() {
    syncModelMaxTokens()
  }

  function syncModelMaxTokens() {
    const m = currentModels.value.find(x => x.id === formLlmModel.value)
    if (m) formLlmMaxTokens.value = m.maxTokens
  }

  // ── 工具列表 ──
  const dynamicTools = ref<ToolInfo[]>([])
  const toolsLoaded = ref(false)

  async function loadTools() {
    try { const res = await getTools(); dynamicTools.value = res.tools } catch { /* */ }
    toolsLoaded.value = true
  }

  // ── 保存 ──
  async function handleSave() {
    const config = {
      llmProvider: formLlmProvider.value, llmModel: formLlmModel.value,
      apiKey: formApiKey.value, llmBaseUrl: formLlmBaseUrl.value,
      llmMaxTokens: formLlmMaxTokens.value, llmTemperature: formLlmTemperature.value,
      llmEnableThinking: formEnableThinking.value,
      serverUrl: formServerUrl.value, httpBaseUrl: formHttpBaseUrl.value,
      wsPort: formWsPort.value, reconnectDelay: formReconnectDelay.value,
      voiceProvider: formVoiceProvider.value, voiceId: formVoiceId.value,
      gptSovitsUrl: formGptSovitsUrl.value,
      gptSovitsPythonPath: formGptSovitsPythonPath.value,
      minimaxApiKey: formMinimaxApiKey.value,
      minimaxVoiceId: formVoiceId.value === 'custom' ? formMinimaxVoiceId.value : formVoiceId.value,
      autoPlayVoice: formAutoPlayVoice.value,
    }
    settings.saveAllSettings(config)
    settings.saveProactiveChatSettings({
      enabled: formProactiveChatEnabled.value,
      idleMinutes: formProactiveChatIdleMinutes.value,
      quietHoursStart: formProactiveChatQuietStart.value,
      quietHoursEnd: formProactiveChatQuietEnd.value,
      dailyLimit: formProactiveChatDailyLimit.value,
    })
    try {
      await updateConfig({
        llm: {
          provider: 'openai_compat', model: config.llmModel, apiKey: config.apiKey,
          baseUrl: config.llmBaseUrl, maxTokens: config.llmMaxTokens,
          temperature: config.llmTemperature, enableThinking: config.llmEnableThinking,
        },
        voice: {
          provider: config.voiceProvider, voice_id: config.voiceId,
          gpt_sovits_url: config.gptSovitsUrl,
          gptSovits: { pythonPath: config.gptSovitsPythonPath },
          minimax: { apiKey: config.minimaxApiKey, voiceId: config.minimaxVoiceId },
        },
        server: { port: Number(config.wsPort) || 8765 },
        proactiveChat: {
          enabled: formProactiveChatEnabled.value,
          idleMinutes: formProactiveChatIdleMinutes.value,
          quietHoursStart: formProactiveChatQuietStart.value,
          quietHoursEnd: formProactiveChatQuietEnd.value,
          dailyLimit: formProactiveChatDailyLimit.value,
        },
      })
    } catch { /* 后端不可用 */ }
  }

  function handleCancel() { /* emit close handled by parent */ }

  // ── 诊断 ──
  const diagnosing = ref(false)
  const diagResult = ref<{ success: boolean; message: string } | null>(null)

  async function runPingTest() {
    diagnosing.value = true; diagResult.value = null
    try {
      const r = await diagnosePing()
      diagResult.value = { success: r.success, message: `${r.message}（${r.latency_ms}ms）` }
    } catch (e: any) {
      diagResult.value = { success: false, message: `Ping 失败: ${e.message}` }
    } finally { diagnosing.value = false }
  }

  async function runLLMTest() {
    if (!formApiKey.value.trim()) {
      diagResult.value = { success: false, message: '请先填写 API Key' }; return
    }
    diagnosing.value = true; diagResult.value = null
    try {
      const r = await diagnoseLLM({
        api_key: formApiKey.value,
        base_url: formLlmBaseUrl.value || 'http://127.0.0.1:8765',
        model: formLlmModel.value,
      })
      diagResult.value = { success: r.success, message: `${r.message}（${r.latency_ms}ms）` }
    } catch (e: any) {
      diagResult.value = { success: false, message: `检测失败: ${e.message}` }
    } finally { diagnosing.value = false }
  }

  // ── 语音 ──
  async function loadVoices() {
    try {
      const res = await getVoiceList(formVoiceProvider.value)
      voiceList.value = res.voices
    } catch { /* */ }
  }

  // GPT-SoVITS
  const gsStatus = ref<GptSovitsStatus | null>(null)
  const gsStatusLoading = ref(false)
  const gsStarting = ref(false)
  const gsStopping = ref(false)
  const gsActionMsg = ref('')

  async function checkGsStatus() {
    gsStatusLoading.value = true
    try { gsStatus.value = await getGptSovitsStatus() } catch { gsStatus.value = null }
    finally { gsStatusLoading.value = false }
  }
  async function handleStartGs() {
    gsStarting.value = true; gsActionMsg.value = ''
    try {
      const r = await startGptSovits(); gsActionMsg.value = r.message
      gsStatus.value = { running: true, port: 9880 }
    } catch (e: any) { gsActionMsg.value = `❌ 启动失败: ${e.message}` }
    finally { gsStarting.value = false }
  }
  async function handleStopGs() {
    gsStopping.value = true; gsActionMsg.value = ''
    try {
      const r = await stopGptSovits(); gsActionMsg.value = r.message
      gsStatus.value = { running: false, port: 9880 }
    } catch (e: any) { gsActionMsg.value = `❌ 停止失败: ${e.message}` }
    finally { gsStopping.value = false }
  }

  // 缓存
  const audioCacheStats = ref<AudioCacheStats | null>(null)
  const cacheCleaning = ref(false)
  const cacheMsg = ref('')
  async function loadCacheStats() {
    try { audioCacheStats.value = await getAudioCacheStats() } catch { audioCacheStats.value = null }
  }
  async function handleCleanupCache() {
    cacheCleaning.value = true; cacheMsg.value = ''
    try {
      const r = await cleanupAudioCache(undefined, undefined, true)
      cacheMsg.value = `✅ ${r.message}`; audioCacheStats.value = r.after
    } catch (e: any) { cacheMsg.value = `❌ 清理失败: ${e.message}` }
    finally { cacheCleaning.value = false }
  }

  // 环境
  const envReady = ref(false); const envChecking = ref(false)
  const engineEnvReady = ref(false); const configuredPathExists = ref(false)
  const configuredPath = ref(''); const envEngineDir = ref('')
  async function checkEnvStatus() {
    envChecking.value = true
    try {
      const base = getApiBase(); const res = await fetch(`${base}/api/voice/env/status`)
      if (res.ok) {
        const data = await res.json()
        envReady.value = data.env_ready; engineEnvReady.value = data.engine_env_ready
        configuredPathExists.value = data.configured_path_exists
        configuredPath.value = data.configured_path; envEngineDir.value = data.engine_dir
      }
    } catch { /* */ } finally { envChecking.value = false }
  }
  async function openEngineDir() {
    try {
      const base = getApiBase()
      await fetch(`${base}/api/voice/env/open-dir`, { method: 'POST' })
    } catch { /* */ }
  }

  // 模型下载
  const modelStatus = ref<ModelStatus | null>(null)
  const modelStatusLoading = ref(false)
  const modelDownloading = ref(false)
  const downloadLog = ref<string[]>([])
  const downloadProgress = ref<DownloadProgress>({
    overall_percent: 0, overall_downloaded_mb: 0, overall_total_mb: 0,
    current_file: '', file_percent: 0,
  })
  async function checkModelStatus() {
    modelStatusLoading.value = true
    try {
      const base = getApiBase(); const res = await fetch(`${base}/api/voice/model/status`)
      if (res.ok) modelStatus.value = await res.json()
    } catch { /* */ } finally { modelStatusLoading.value = false }
  }

  async function startModelDownload() {
    if (modelDownloading.value) return
    modelDownloading.value = true
    downloadLog.value = []
    downloadProgress.value = { overall_percent: 0, overall_downloaded_mb: 0, overall_total_mb: 0, current_file: '', file_percent: 0 }
    try {
      const base = getApiBase()
      const res = await fetch(`${base}/api/voice/model/download`, { method: 'POST' })
      if (!res.ok || !res.body) throw new Error('下载请求失败')
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const evt = JSON.parse(line.slice(6))
            if (evt.event === 'progress') {
              downloadProgress.value = {
                overall_percent: evt.overall_percent, overall_downloaded_mb: evt.overall_downloaded_mb,
                overall_total_mb: evt.overall_total_mb, current_file: evt.file, file_percent: evt.file_percent,
              }
            } else if (evt.event === 'file_start') {
              downloadLog.value.push(`📥 开始下载: ${evt.file} (${evt.index}/${evt.total})`)
            } else if (evt.event === 'file_done') {
              downloadLog.value.push(`✅ 完成: ${evt.file}`)
            } else if (evt.event === 'url_fallback') {
              downloadLog.value.push(`⚠️ 切换备用源: ${evt.file}`)
            } else if (evt.event === 'complete') {
              downloadLog.value.push(`🎉 ${evt.message}`)
              downloadProgress.value.overall_percent = 100
              await checkModelStatus()
            } else if (evt.event === 'already_complete') {
              downloadLog.value.push(`✅ ${evt.message}`)
              await checkModelStatus()
            } else if (evt.event === 'fatal') {
              downloadLog.value.push(`❌ ${evt.message}`)
            }
          } catch { /* ignore */ }
        }
      }
    } catch (e: any) {
      downloadLog.value.push(`❌ 请求失败: ${e.message}`)
    } finally {
      modelDownloading.value = false
    }
  }

  // ── MCP ──
  const mcpServers = ref<McpServerStatus[]>([])
  const mcpLoading = ref(false)
  const mcpActionMsg = ref('')
  const mcpJsonVisible = ref(false)
  const mcpJsonContent = ref('')
  const mcpJsonSaving = ref(false)
  const expandedServer = ref<string | null>(null)

  async function loadMcpServers() {
    mcpLoading.value = true
    try { const res = await getMcpServers(); mcpServers.value = res.servers }
    catch { mcpServers.value = [] }
    finally { mcpLoading.value = false }
  }
  async function loadMcpRawConfig() {
    try {
      const r = await getMcpRawConfig(); mcpJsonContent.value = r.content
    } catch { mcpJsonContent.value = '{\n  "mcpServers": {},\n  "disabledMcpServers": []\n}' }
  }
  async function toggleMcpJsonEditor() {
    if (!mcpJsonVisible.value) { await loadMcpRawConfig(); mcpActionMsg.value = '' }
    mcpJsonVisible.value = !mcpJsonVisible.value
  }
  async function handleSaveMcpJson() {
    try { JSON.parse(mcpJsonContent.value) } catch { mcpActionMsg.value = '❌ JSON 格式无效'; return }
    mcpJsonSaving.value = true; mcpActionMsg.value = ''
    try {
      const r = await saveMcpRawConfig(mcpJsonContent.value)
      mcpActionMsg.value = r.message; mcpJsonVisible.value = false
      await loadMcpServers(); await loadTools()
    } catch (e: any) { mcpActionMsg.value = `❌ 保存失败: ${e.message}` }
    finally { mcpJsonSaving.value = false }
  }
  async function handleDeleteMcp(name: string) {
    mcpActionMsg.value = ''
    try {
      const r = await deleteMcpServer(name); mcpActionMsg.value = r.message
      if (expandedServer.value === name) expandedServer.value = null
      await loadMcpServers(); await loadTools()
    } catch (e: any) { mcpActionMsg.value = `❌ ${e.message}` }
  }
  async function handleRefreshMcp(name: string) {
    mcpActionMsg.value = ''
    try {
      const r = await refreshMcpServer(name); mcpActionMsg.value = r.message
      await loadMcpServers(); await loadTools()
    } catch (e: any) { mcpActionMsg.value = `❌ 刷新失败: ${e.message}` }
  }
  function toggleServerExpand(name: string) {
    expandedServer.value = expandedServer.value === name ? null : name
  }

  // ── Skill ──
  const skillList = ref<SkillMeta[]>([])
  const skillImporting = ref(false)
  const skillReloading = ref(false)
  const skillMsg = ref('')

  async function loadSkillList() {
    try { const res = await listSkills(); skillList.value = res.skills } catch { skillList.value = [] }
  }
  async function handleImportSkill() {
    const input = document.createElement('input')
    input.type = 'file'; input.accept = '.md'
    input.onchange = async () => {
      const file = input.files?.[0]; if (!file) return
      skillImporting.value = true; skillMsg.value = ''
      try {
        const content = await file.text(); const r = await importSkillFile(content)
        skillMsg.value = `✅ ${r.message}`; await loadSkillList()
      } catch (e: any) { skillMsg.value = `❌ 导入失败: ${e.message}` }
      finally { skillImporting.value = false }
    }
    input.click()
  }
  async function handleImportSkillFolder() {
    const input = document.createElement('input')
    input.type = 'file'; input.setAttribute('webkitdirectory', ''); input.setAttribute('directory', '')
    input.onchange = async () => {
      const files = Array.from(input.files || []); if (!files.length) return
      skillImporting.value = true; skillMsg.value = ''
      try {
        const entries = await Promise.all(
          files.map(async (f) => ({ path: (f as any).webkitRelativePath || f.name, content: await f.text() }))
        )
        const r = await importSkillFolder(entries)
        skillMsg.value = `✅ ${r.message}`; await loadSkillList()
      } catch (e: any) { skillMsg.value = `❌ 文件夹导入失败: ${e.message}` }
      finally { skillImporting.value = false }
    }
    input.click()
  }
  async function handleReloadSkills() {
    skillReloading.value = true; skillMsg.value = ''
    try {
      const r = await reloadSkills(); skillMsg.value = `✅ ${r.message}`
      await loadSkillList(); await loadTools()
    } catch (e: any) { skillMsg.value = `❌ 重载失败: ${e.message}` }
    finally { skillReloading.value = false }
  }
  async function handleDeleteSkill(name: string) {
    skillMsg.value = ''
    try {
      const r = await deleteSkill(name); skillMsg.value = `✅ ${r.message}`
      await loadSkillList(); await loadTools()
    } catch (e: any) { skillMsg.value = `❌ 删除失败: ${e.message}` }
  }

  // ── 头像 ──
  const dailyAvatarList = ref<AvatarInfo[]>([])
  const workAvatarList = ref<AvatarInfo[]>([])
  const avatarLoading = ref(false)
  const avatarUploading = ref(false)
  const avatarMsg = ref('')

  async function loadAvatarLists() {
    avatarLoading.value = true
    try {
      const [dailyRes, workRes] = await Promise.all([getAvatars('daily'), getAvatars('work')])
      dailyAvatarList.value = dailyRes.avatars; workAvatarList.value = workRes.avatars
    } catch { avatarMsg.value = '❌ 加载头像列表失败' }
    finally { avatarLoading.value = false }
  }
  async function handleAvatarUpload(category: 'daily' | 'work') {
    const input = document.createElement('input')
    input.type = 'file'; input.accept = '.png,.jpg,.jpeg,.webp,.gif'
    input.onchange = async () => {
      const file = input.files?.[0]; if (!file) return
      avatarUploading.value = true; avatarMsg.value = ''
      try {
        await uploadAvatar(file, category); await loadAvatarLists(); await companion.refreshAvatars()
        avatarMsg.value = '✅ 头像上传成功'
      } catch (e: any) { avatarMsg.value = `❌ 上传失败: ${e.message}` }
      finally { avatarUploading.value = false }
    }
    input.click()
  }
  async function handleAvatarDelete(category: string, filename: string) {
    avatarMsg.value = ''
    try {
      await deleteAvatar(category, filename); await loadAvatarLists(); await companion.refreshAvatars()
      avatarMsg.value = '✅ 已删除'
    } catch (e: any) { avatarMsg.value = `❌ 删除失败: ${e.message}` }
  }

  async function playVoiceSample() {
    if (testingVoice.value) return
    testingVoice.value = true
    try {
      const res = await fetch(`${getApiBase()}/api/voice/sample`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: '太好了，能再次见到你！我是流萤。',
          provider: formVoiceProvider.value, voice_id: formVoiceId.value,
          gpt_sovits_url: formGptSovitsUrl.value,
          api_key: formVoiceProvider.value === 'minimax' ? formMinimaxApiKey.value : undefined,
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        alert(`试听失败: ${err.detail}`); testingVoice.value = false; return
      }
      const blob = await res.blob(); const blobUrl = URL.createObjectURL(blob)
      if (sampleAudio.value) { sampleAudio.value.pause(); URL.revokeObjectURL(sampleAudio.value.src) }
      const audio = new Audio(blobUrl)
      sampleAudio.value = audio
      audio.onended = () => { testingVoice.value = false; URL.revokeObjectURL(blobUrl) }
      audio.onerror = () => { testingVoice.value = false; URL.revokeObjectURL(blobUrl); alert('试听播放失败') }
      await audio.play()
    } catch (e: any) { testingVoice.value = false; alert(`试听请求失败: ${e.message}`) }
  }

  function toggleDailyUnlockHandler() {
    settings.toggleDailyUnlock()
    wsClient.send({ type: 'daily_unlock', unlocked: settings.dailyUnlocked })
  }

  function triggerProactive() {
    const ok = wsClient.send({
      type: 'trigger_proactive',
      sessionId: companion.activeSessionId ?? undefined,
    })
    if (!ok) {
      console.warn('[Settings] trigger_proactive 发送失败：WebSocket 未连接')
      alert('WebSocket 未连接，请确认后端服务和流萤窗口正在运行。')
    } else {
      console.log('[Settings] trigger_proactive 已发送, session=', companion.activeSessionId)
    }
  }

  function riskLabel(level: string): string {
    switch (level) {
      case 'low': return '只读'
      case 'medium': return '中危'
      case 'high': return '高危'
      default: return level
    }
  }

  // 工具分组
  const builtinTools = computed(() => dynamicTools.value.filter(t => !t.source || t.source === 'builtin'))
  const mcpTools = computed(() => dynamicTools.value.filter(t => t.source === 'mcp'))

  return {
    // form refs
    formLlmProvider, formLlmModel, formApiKey, formLlmBaseUrl, formLlmMaxTokens,
    formLlmTemperature, formEnableThinking, formServerUrl, formHttpBaseUrl,
    formWsPort, formReconnectDelay,
    // voice
    formVoiceProvider, formVoiceId, formGptSovitsUrl, formGptSovitsPythonPath,
    formMinimaxApiKey, formMinimaxVoiceId, formAutoPlayVoice,
    voiceList, testingVoice, playVoiceSample,
    // proactive chat
    formProactiveChatEnabled, formProactiveChatIdleMinutes,
    formProactiveChatQuietStart, formProactiveChatQuietEnd, formProactiveChatDailyLimit,
    triggerProactive,

    // provider
    providerList, providersLoaded, providersError, currentModels, isCustomProvider,
    loadProviders, onProviderChange, onModelChange, loadTools,
    // save
    handleSave, handleCancel, showToast, toast,
    // diagnostics
    diagnosing, diagResult, runPingTest, runLLMTest,
    // voice env
    gsStatus, gsStatusLoading, gsStarting, gsStopping, gsActionMsg,
    checkGsStatus, handleStartGs, handleStopGs,
    audioCacheStats, cacheCleaning, cacheMsg, loadCacheStats, handleCleanupCache,
    envReady, envChecking, engineEnvReady, configuredPathExists,
    configuredPath, envEngineDir, checkEnvStatus, openEngineDir,
    modelStatus, modelStatusLoading, modelDownloading, downloadLog, downloadProgress,
    loadVoices, checkModelStatus, startModelDownload,
    // MCP
    mcpServers, mcpLoading, mcpActionMsg, mcpJsonVisible, mcpJsonContent,
    mcpJsonSaving, expandedServer,
    loadMcpServers, toggleMcpJsonEditor, handleSaveMcpJson,
    loadMcpRawConfig,
    handleDeleteMcp, handleRefreshMcp, toggleServerExpand,
    // skills
    skillList, skillImporting, skillReloading, skillMsg,
    loadSkillList, handleImportSkill, handleImportSkillFolder,
    handleReloadSkills, handleDeleteSkill,
    // tools
    dynamicTools, toolsLoaded, builtinTools, mcpTools,
    // avatars
    dailyAvatarList, workAvatarList, avatarLoading, avatarUploading, avatarMsg,
    loadAvatarLists, handleAvatarUpload, handleAvatarDelete,
    // misc
    toggleDailyUnlockHandler, riskLabel,
  }
}

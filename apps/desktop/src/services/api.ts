/** 后端 HTTP API 封装 */
import type { AppMode, ModeConfig, HealthResponse, SessionInfo, MemoryItem, MemoryType } from '@shared/index'

const DEFAULT_HTTP_BASE = 'http://127.0.0.1:8765'

function getBaseHttp(): string {
  try {
    if (typeof window === 'undefined') return DEFAULT_HTTP_BASE
    const { protocol, hostname } = window.location

    // ⚠️ 打包后的 Tauri webview 源并不是 tauri:// —— 在 Windows(WebView2) 上是
    // http://tauri.localhost（启用 useHttpsScheme 时为 https://tauri.localhost），
    // 只有 macOS/Linux 才是 tauri://localhost。因此绝不能只用 protocol === 'http:'
    // 判断 dev，否则打包版会被误判为 dev 而走相对路径，把请求打到
    // http://tauri.localhost/api/... （Tauri 静态资源处理器）→ 全部 404。
    const isTauriBundle = protocol === 'tauri:' || hostname === 'tauri.localhost'

    // Vite dev server (http://127.0.0.1:5173)：返回空字符串 → fetch 走相对路径
    // → Vite proxy 转发 → 无 CORS。同时避免被 localStorage 中的旧配置覆盖
    // (settings.ts 会把 firefly_http_base 写入 localStorage)。
    if (!isTauriBundle) return ''

    // Tauri 生产模式：从 localStorage 读取用户配置或使用默认值
    const raw = localStorage.getItem('firefly_http_base')
    if (raw) return raw
    return DEFAULT_HTTP_BASE
  } catch {
    return DEFAULT_HTTP_BASE
  }
}

/** 对外暴露的 HTTP base URL 获取函数（供需要直接 fetch 的模块使用） */
export function getApiBase(): string {
  return getBaseHttp()
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${getBaseHttp()}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body}`)
  }
  return res.json()
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health')
}

export function getMode(): Promise<ModeConfig> {
  return request<ModeConfig>('/api/mode')
}

export function switchMode(mode: AppMode): Promise<ModeConfig> {
  return request<ModeConfig>(`/api/mode?mode=${mode}`, { method: 'POST' })
}

export interface ProviderInfo {
  id: string
  name: string
  baseUrl: string
  models: Array<{ id: string; name: string; maxTokens: number }>
  temperature: number
  enableThinking: boolean
}

export function getProviders(): Promise<{ providers: ProviderInfo[] }> {
  return request<{ providers: ProviderInfo[] }>('/api/providers')
}

// ── 诊断 API ──
export interface DiagnoseResult {
  success: boolean
  latency_ms: number
  message: string
}

export function diagnosePing(): Promise<DiagnoseResult> {
  return request<DiagnoseResult>('/api/diagnose/ping')
}

export function diagnoseLLM(body: {
  api_key?: string
  base_url?: string
  model?: string
}): Promise<DiagnoseResult> {
  return request<DiagnoseResult>('/api/diagnose/llm', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ── 阶段3：会话 API ──
export function getSessions(): Promise<SessionInfo[]> {
  return request<SessionInfo[]>('/api/sessions')
}

export function createSession(sessionId: string, title?: string, mode?: AppMode, workspaceId?: string): Promise<SessionInfo> {
  return request<SessionInfo>('/api/sessions', {
    method: 'POST',
    body: JSON.stringify({ id: sessionId, title: title || '新会话', mode: mode || 'daily', workspaceId }),
  })
}

export function deleteSession(sessionId: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/sessions/${sessionId}`, { method: 'DELETE' })
}

export function renameSession(sessionId: string, title: string): Promise<{ ok: boolean; title: string }> {
  return request<{ ok: boolean; title: string }>(`/api/sessions/${sessionId}/rename`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export function getSessionHistory(sessionId: string, limit = 50): Promise<Array<{ role: string; content: string; emotion?: string; createdAt: number }>> {
  return request(`/api/sessions/${sessionId}/history?limit=${limit}`)
}

// ── 阶段3：记忆 API ──
export function getMemories(namespace?: string, mode?: AppMode): Promise<MemoryItem[]> {
  const params = new URLSearchParams()
  if (namespace) params.set('namespace', namespace)
  if (mode) params.set('mode', mode)
  const qs = params.toString()
  return request<MemoryItem[]>(`/api/memories${qs ? '?' + qs : ''}`)
}

export function searchMemories(query: string, mode?: AppMode): Promise<MemoryItem[]> {
  const params = new URLSearchParams()
  params.set('q', query)
  if (mode) params.set('mode', mode)
  return request<MemoryItem[]>(`/api/memories/search?${params.toString()}`)
}

export function upsertMemory(body: {
  id?: string
  type?: MemoryType
  content: string
  namespace?: string
  confidence?: number
}): Promise<MemoryItem> {
  return request<MemoryItem>('/api/memories', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateMemory(
  id: string,
  body: { content?: string; confidence?: number; namespace?: string },
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/memories/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteMemory(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/memories/${id}`, { method: 'DELETE' })
}

// ── 阶段3：关怀 API ──
export function checkConcern(trigger = 'first_chat', mode = 'daily'): Promise<{ shouldFire: boolean }> {
  return request<{ shouldFire: boolean }>('/api/concern/check', {
    method: 'POST',
    body: JSON.stringify({ trigger, mode }),
  })
}

export function recordConcern(trigger: string, content: string, mode = 'daily'): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/api/concern/record', {
    method: 'POST',
    body: JSON.stringify({ trigger, content, mode }),
  })
}

// ── 阶段25：主动聊天设置 API ──
export interface ProactiveChatSettings {
  enabled: boolean
  idleMinutes: number
  quietHoursStart: number
  quietHoursEnd: number
  dailyLimit: number
}

export function updateProactiveChatSettings(
  settings: ProactiveChatSettings,
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/api/config', {
    method: 'POST',
    body: JSON.stringify({
      proactiveChat: {
        enabled: settings.enabled,
        idleMinutes: settings.idleMinutes,
        quietHoursStart: settings.quietHoursStart,
        quietHoursEnd: settings.quietHoursEnd,
        dailyLimit: settings.dailyLimit,
      },
    }),
  })
}

export function getConcernQueue(
  mode = 'daily',
  limit = 10,
): Promise<{ items: Array<{
  id: string; type: string; detail: string; severity: string;
  status: string; createdAt: number; expiresAt: number;
  lastCheckedAt: number | null; checkCount: number; mode: string;
}>; count: number }> {
  return request(`/api/concern/queue?mode=${encodeURIComponent(mode)}&limit=${limit}`)
}

export function resolveConcern(
  concernId: string,
  status = 'resolved',
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/api/concern/resolve', {
    method: 'POST',
    body: JSON.stringify({ concernId, status }),
  })
}

// ── 阶段4.5：工作空间 API ──
export interface WorkspaceInfo {
  id: string
  name: string
  path: string
  isDefault?: boolean     // 内置默认工作空间（不可删除）
  pathExists?: boolean    // 文件夹是否实际存在
  createdAt: number
  updatedAt: number
}

export function getWorkspaces(): Promise<WorkspaceInfo[]> {
  return request<WorkspaceInfo[]>('/api/workspaces')
}

export function createWorkspace(name: string, path: string, id?: string): Promise<WorkspaceInfo> {
  return request<WorkspaceInfo>('/api/workspaces', {
    method: 'POST',
    body: JSON.stringify({ id, name, path }),
  })
}

export function deleteWorkspace(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/workspaces/${id}`, { method: 'DELETE' })
}

export function moveSessionToWorkspace(sessionId: string, workspaceId: string | null): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/sessions/${sessionId}/move`, {
    method: 'POST',
    body: JSON.stringify({ workspaceId }),
  })
}

// ── 阶段4.5：系统状态 & 工具 & 配置 API ──
export interface SystemStatus {
  cpuPercent: number
  memoryPercent: number
  memoryUsedGb: number
  memoryTotalGb: number
}

export function getSystemStatus(): Promise<SystemStatus> {
  return request<SystemStatus>('/api/system/status')
}

export interface ToolInfo {
  name: string
  description: string
  riskLevel: string
  source?: 'builtin' | 'skill' | 'mcp'
}

export function getTools(): Promise<{ tools: ToolInfo[]; count: number }> {
  return request<{ tools: ToolInfo[]; count: number }>('/api/tools')
}

export function updateConfig(body: Record<string, unknown>): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/api/config', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ── 阶段7：语音 API ──
import type { VoiceOption } from '@shared/index'

export function getVoiceList(provider?: string): Promise<{ voices: VoiceOption[] }> {
  const params = provider ? `?provider=${encodeURIComponent(provider)}` : ''
  return request<{ voices: VoiceOption[] }>(`/api/voice/voices${params}`)
}

export function getVoiceAudioUrl(
  text: string,
  provider = 'edge-tts',
  voiceId = 'zh-CN-XiaoyiNeural',
  gptSovitsUrl = 'http://127.0.0.1:9880',
): string {
  const base = getBaseHttp()
  const params = new URLSearchParams({
    text,
    provider,
    voice_id: voiceId,
    gpt_sovits_url: gptSovitsUrl,
  })
  return `${base}/api/voice/tts?${params.toString()}`
}

// ── GPT-SoVITS 服务进程控制 ──
export interface GptSovitsStatus {
  running: boolean
  port: number
}

export function getGptSovitsStatus(): Promise<GptSovitsStatus> {
  return request<GptSovitsStatus>('/api/voice/gpt-sovits/status')
}

export function startGptSovits(): Promise<{ started: boolean; message: string }> {
  return request<{ started: boolean; message: string }>('/api/voice/gpt-sovits/start', { method: 'POST' })
}

export function stopGptSovits(): Promise<{ stopped: boolean; message: string }> {
  return request<{ stopped: boolean; message: string }>('/api/voice/gpt-sovits/stop', { method: 'POST' })
}

// ── 音频缓存管理 ──
export interface AudioCacheStats {
  file_count: number
  total_size_mb: number
  oldest_ts: number | null
  newest_ts: number | null
}

export function getAudioCacheStats(): Promise<AudioCacheStats> {
  return request<AudioCacheStats>('/api/voice/audio-cache')
}

export function cleanupAudioCache(ttlDays?: number, maxSizeMb?: number, force?: boolean): Promise<{
  ok: boolean
  before: AudioCacheStats
  after: AudioCacheStats
  deleted_count: number
  freed_mb: number
  message: string
}> {
  const params = new URLSearchParams()
  if (ttlDays !== undefined) params.set('ttl_days', String(ttlDays))
  if (maxSizeMb !== undefined) params.set('max_size_mb', String(maxSizeMb))
  if (force) params.set('force', 'true')
  const qs = params.toString()
  return request(`/api/voice/audio-cache${qs ? '?' + qs : ''}`, { method: 'DELETE' })
}

// ── 阶段4.7：MCP 服务 (标准 mcp.json) & Skill (SKILL.md) 管理 ──
import type { McpServerStatus } from '@shared/index'

export interface SkillMeta {
  name: string
  path: string
  description: string
  license: string
  metadata: Record<string, string>
}

export interface SkillBody extends SkillMeta {
  body: string
  has_scripts: boolean
  has_references: boolean
}

// MCP 服务器管理
export function getMcpServers(): Promise<{ servers: McpServerStatus[] }> {
  return request<{ servers: McpServerStatus[] }>('/api/mcp/servers')
}

export function addMcpServer(cfg: {
  name: string
  type: 'stdio' | 'sse' | 'http'
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  headers?: Record<string, string>
  description?: string
  defer_loading?: boolean
}): Promise<{ server: McpServerStatus; message: string }> {
  return request<{ server: McpServerStatus; message: string }>('/api/mcp/servers', {
    method: 'POST',
    body: JSON.stringify(cfg),
  })
}

export function deleteMcpServer(name: string): Promise<{ ok: boolean; message: string }> {
  return request<{ ok: boolean; message: string }>(`/api/mcp/servers/${name}`, { method: 'DELETE' })
}

export function refreshMcpServer(name: string): Promise<{ server: McpServerStatus; message: string }> {
  return request<{ server: McpServerStatus; message: string }>(`/api/mcp/servers/${name}/refresh`, { method: 'POST' })
}

/** 获取 mcp.json 原始内容（供 JSON 编辑器直接编辑） */
export function getMcpRawConfig(): Promise<{ content: string }> {
  return request<{ content: string }>('/api/mcp/raw-config')
}

/** 保存 mcp.json 并重载所有 MCP 服务器 */
export function saveMcpRawConfig(content: string): Promise<{ ok: boolean; message: string }> {
  return request<{ ok: boolean; message: string }>('/api/mcp/raw-config', {
    method: 'PUT',
    body: JSON.stringify({ content }),
  })
}

// Skill (SKILL.md) 管理
export function listSkills(): Promise<{ skills: SkillMeta[]; count: number }> {
  return request<{ skills: SkillMeta[]; count: number }>('/api/skills')
}

export function reloadSkills(): Promise<{ skills: SkillMeta[]; count: number; message: string }> {
  return request<{ skills: SkillMeta[]; count: number; message: string }>('/api/skills/reload', { method: 'POST' })
}

export function importSkillFile(content: string): Promise<{ ok: boolean; skill: SkillMeta; message: string }> {
  return request<{ ok: boolean; skill: SkillMeta; message: string }>('/api/skills/import', {
    method: 'POST',
    body: JSON.stringify({ content, filename: 'SKILL.md' }),
  })
}

export function importSkillFolder(files: { path: string; content: string }[]): Promise<{ ok: boolean; skill: SkillMeta; message: string }> {
  return request<{ ok: boolean; skill: SkillMeta; message: string }>('/api/skills/import-folder', {
    method: 'POST',
    body: JSON.stringify({ files }),
  })
}

export function deleteSkill(name: string): Promise<{ ok: boolean; message: string }> {
  return request<{ ok: boolean; message: string }>(`/api/skills/${name}`, { method: 'DELETE' })
}

// ── 头像管理 API ──
export interface AvatarInfo {
  filename: string
  stem: string
  size: number
}

export interface AvatarListResult {
  category: string
  avatars: AvatarInfo[]
  count: number
}

export function getAvatars(category: 'daily' | 'work' = 'daily'): Promise<AvatarListResult> {
  return request<AvatarListResult>(`/api/avatars?category=${category}`)
}

export async function uploadAvatar(
  file: File,
  category: 'daily' | 'work' = 'daily',
): Promise<{ ok: boolean; filename: string; stem: string; index: number; size: number; category: string }> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('category', category)
  const res = await fetch(`${getBaseHttp()}/api/avatars/upload`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body}`)
  }
  return res.json()
}

export function deleteAvatar(category: string, filename: string): Promise<{ ok: boolean; deleted: string }> {
  return request<{ ok: boolean; deleted: string }>(`/api/avatars/${category}/${filename}`, { method: 'DELETE' })
}


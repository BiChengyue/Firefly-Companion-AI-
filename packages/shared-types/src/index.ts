// 前后端共享协议类型（Contract-First）
// 后端以 Pydantic 模型为事实来源，这里维护一份便于前端使用的镜像。

/** 消息角色 */
export type Role = 'user' | 'assistant' | 'system'

/** 情感标签，映射到 Live2D 表情 */
export type EmotionLabel =
  | 'neutral'
  | 'happy'
  | 'sad'
  | 'angry'
  | 'shy'
  | 'thinking'
  | 'surprised'

/** 运行模式 — 对应 spec 3.10 */
export type AppMode = 'daily' | 'work'

/** 萨姆子态 — 对应 spec 3.1.2 */
export type SamSubTone = 'execution' | 'warning' | 'completion'

/** Token 消耗明细 — CodeBuddy 风格细分 */
export interface TokenUsage {
  promptTokens: number           // 输入 token 总数
  completionTokens: number       // 输出 token 总数
  totalTokens: number            // 总计
  cachedTokens?: number          // 缓存命中（输入中）
  cacheWriteTokens?: number      // 缓存写入（输入中）
  reasoningTokens?: number       // 思考过程（输出中）
  replyTokens?: number           // 回复内容（输出中）
  elapsedMs?: number             // 本次生成耗时（毫秒）
}

/** 一条聊天消息 */
export interface ChatMessage {
  id: string
  role: Role
  content: string
  emotion?: EmotionLabel
  mode?: AppMode
  meme?: string  // 表情包文件路径（对应 spec 3.1）
  tokenUsage?: TokenUsage  // 该条消息的 Token 消耗
  createdAt: number
}

/** 会话 */
export interface Session {
  id: string
  title: string
  messages: ChatMessage[]
  workspaceId?: string | null
  createdAt: number
  updatedAt: number
}

/** 记忆条目类型 */
export type MemoryType = 'user_profile' | 'preference' | 'event' | 'promise' | 'emotion'

/** 记忆条目（REST API 返回） */
export interface MemoryItem {
  id: string
  type: MemoryType
  content: string
  namespace: string
  confidence: number
  topic?: string
  entity?: string
  createdAt: number
  updatedAt: number
}

/** 会话小结（REST API 返回，不含 messages 列表） */
export interface SessionInfo {
  id: string
  title: string
  mode: AppMode
  workspaceId?: string | null
  createdAt: number
  updatedAt: number
}

/** 模式配置 — 对应 spec 3.10 */
export interface ModeConfig {
  current: AppMode
  theme: Record<string, unknown>
  hudVisible: boolean
  thinkVisible: boolean
  proactiveCare: boolean
}

/** WebSocket：客户端 -> 服务端 */
export type WsClientMessage =
  | { type: 'chat'; content: string; sessionId?: string; workspacePath?: string; resumeTaskId?: string }
  | { type: 'reset'; sessionId?: string }
  | { type: 'mode_switch'; mode: AppMode }
  | { type: 'voice_input'; audioChunk: number[] }
  | { type: 'voice_toggle'; enabled: boolean }
  | { type: 'approval_response'; stepId: string; approved: boolean }  // 阶段4：人在回路审批回复
  | { type: 'cancel' }  // 终止当前生成/Agent 任务
  | { type: 'daily_unlock'; unlocked: boolean }  // 日常模式解除限制
  | { type: 'trigger_proactive'; sessionId?: string }  // 手动触发主动聊天

/** WebSocket：服务端 -> 客户端 */
export type WsServerMessage =
  | { type: 'token'; delta: string }
  | { type: 'thinking'; delta: string } // <think> 标签内容 — 对应 spec 3.2
  | { type: 'emotion'; label: EmotionLabel }
  | { type: 'done'; message: ChatMessage }
  | { type: 'meme'; id: string; data: string; createdAt: number } // 独立表情包消息（base64 data URL）
  | { type: 'tool_call'; name: string; args: unknown; stepId?: string; requiresApproval?: boolean; description?: string }
  | { type: 'step_update'; step: TaskStep }  // 阶段4：步骤状态变更
  | { type: 'agent_task'; task: AgentTask }  // 阶段4：Agent 任务开始/完成
  | {
      type: 'mode_switched'
      mode: AppMode
      theme: Record<string, unknown>
      hudVisible: boolean
      thinkVisible: boolean
      proactiveCare: boolean
    }
  | { type: 'error'; message: string }
  | { type: 'concern'; content: string } // 主动关怀推送（阶段3）
  | { type: 'reminder_created'; reminder: { id: string; text: string; dueTimestamp: number; fromApi?: boolean } }
  | { type: 'memory_updated'; count: number } // 后台记忆抽取完成通知（阶段8）
  | { type: 'voice_audio'; audioUrl?: string; audioBase64?: string; text?: string } // 语音推播消息（阶段7）
  | { type: 'token_usage'; usage: TokenUsage; messageId: string } // 单条消息 Token 消耗明细（阶段9）
  | { type: 'daily_unlocked'; unlocked: boolean } // 日常模式解除限制状态变更（阶段9）
  | { type: 'transition_line'; line: string; to_mode: AppMode } // 模式切换过场台词（阶段10）
  | { type: 'planning_thought'; delta: string } // Agent 规划阶段流式思考（阶段20）
  | { type: 'compact_step'; step_ids: string[] } // 上下文压缩通知，前端折叠被压缩的步骤（阶段23）
  | { type: 'proactive_speech'; content: string; source?: string; motion?: string; expression?: string } // 主动聊天推送（阶段25：双引擎）
  | { type: 'ack_received' } // 服务端确认收到消息（0ms 乐观响应，前端立即解除 Loading）

/** 语音音色选项 */
export interface VoiceOption {
  id: string
  name: string
  gender: string
  description: string
  recommended?: boolean
}

/** 语音配置 */
export interface VoiceConfig {
  provider: 'edge-tts' | 'gpt-sovits'
  voiceId: string
  gptSovitsUrl: string
  autoPlay: boolean
}

/** 健康检查响应 */
export interface HealthResponse {
  name: string
  version: string
  llmReady: boolean
}

/** Agent 任务状态 — 对应 spec PLANNING 6.6 */
export type TaskStatus = 'planning' | 'running' | 'paused' | 'done' | 'failed'
export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface TaskStep {
  id: string
  thought: string
  action: string
  action_input: Record<string, unknown>
  observation: string
  status: StepStatus
  requires_approval: boolean
}

export interface AgentTask {
  id: string
  user_input: string
  status: TaskStatus
  steps: TaskStep[]
  created_at: string
  result: string | null
}

/** 工具来源标识 */
export type ToolSource = 'builtin' | 'skill' | 'mcp'

/** MCP 服务器配置 */
export interface McpServerConfig {
  name: string
  type: 'stdio' | 'sse' | 'http'
  command: string
  args: string[]
  env: Record<string, string>
  url: string
  headers: Record<string, string>
  description: string
  defer_loading: boolean
}

/** MCP 服务端工具信息 */
export interface McpToolInfo {
  name: string
  description: string
}

/** MCP 服务器状态（来自 API） */
export interface McpServerStatus {
  name: string
  type: string
  command: string
  args: string[]
  env: Record<string, string>
  url: string
  headers: Record<string, string>
  description: string
  defer_loading: boolean
  online: boolean
  toolCount: number
  tools: McpToolInfo[]
}

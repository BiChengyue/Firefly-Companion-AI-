<script setup lang="ts">
/**
 * SAM HUD 面板 — 工作模式专属红色科技风控制台（阶段4升级版）
 *
 * 双模式：
 *   - thinking: <think> 推理流滚动（原有）
 *   - agent: Agent 任务步骤清单（阶段4新增）
 *
 * 行为：
 *   - thinking 或 agent 启动时自动展开
 *   - 结束后 5s 自动收起
 *   - 点击状态条可手动切换展开/收起
 */
import { ref, watch, computed, nextTick } from 'vue'
import { useCompanionStore } from '@/stores/companion'

const companion = useCompanionStore()

// ── 展开/收起 ──────────────────────────────────────────────────
const expanded = ref(false)
const manualToggle = ref(false)
let collapseTimer: ReturnType<typeof setTimeout> | null = null

// ── 历史记录折叠控制 ──────────────────────────────────────────
const historyExpandedIds = ref<Set<string>>(new Set())

function toggleHistoryItem(id: string) {
  if (historyExpandedIds.value.has(id)) {
    historyExpandedIds.value.delete(id)
  } else {
    historyExpandedIds.value.add(id)
  }
}

// ── 阶段23：被压缩步骤的展开/折叠 ──────────────────────────────
const expandedCompactedIds = ref<Set<string>>(new Set())

function toggleExpandCompacted(stepId: string) {
  if (expandedCompactedIds.value.has(stepId)) {
    expandedCompactedIds.value.delete(stepId)
  } else {
    expandedCompactedIds.value.add(stepId)
  }
}

function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hrs = Math.floor(min / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

function getPreview(content: string, max = 50): string {
  const firstLine = content.split('\n')[0] || ''
  if (firstLine.length <= max) return firstLine
  return firstLine.slice(0, max) + '…'
}

function clearHistory() {
  companion.clearThinkingHistory()
  historyExpandedIds.value.clear()
}

function scheduleCollapse(delay = 5000) {
  if (collapseTimer) clearTimeout(collapseTimer)
  collapseTimer = setTimeout(() => {
    expanded.value = false
    manualToggle.value = false
  }, delay)
}

// thinking 流到达 → 展开
watch(
  () => companion.currentThinking,
  (val) => {
    if (val && companion.streaming) {
      expanded.value = true
      manualToggle.value = true
      if (collapseTimer) { clearTimeout(collapseTimer); collapseTimer = null }
    }
  },
)

// planning 流到达 → 展开
watch(
  () => companion.currentPlanning,
  (val) => {
    if (val && companion.agentRunning) {
      expanded.value = true
      manualToggle.value = true
      if (collapseTimer) { clearTimeout(collapseTimer); collapseTimer = null }
    }
  },
)

// Agent 任务变化 → 展开
watch(
  () => companion.agentRunning,
  (val) => {
    if (val) {
      expanded.value = true
      manualToggle.value = true
      if (collapseTimer) { clearTimeout(collapseTimer); collapseTimer = null }
    }
  },
)

// 流式结束 + Agent结束 → 延迟收起
watch(
  () => companion.streaming,
  (val) => {
    if (!val && !companion.agentRunning && expanded.value && manualToggle.value) {
      scheduleCollapse()
    }
  },
)

watch(
  () => companion.agentRunning,
  (val) => {
    if (!val && !companion.streaming && expanded.value && manualToggle.value) {
      scheduleCollapse()
    }
  },
)

// ── 控制台文本行（planning 模式）─────────────────────────────
const planningLines = computed<string[]>(() => {
  if (!companion.currentPlanning) return []
  return companion.currentPlanning.split('\n')
})

// ── 控制台文本行（thinking 模式）─────────────────────────────
const thinkingLines = computed<string[]>(() => {
  if (!companion.currentThinking) return []
  return companion.currentThinking.split('\n')
})

const showCursor = computed(() =>
  companion.streaming && (viewMode.value === 'thinking' || viewMode.value === 'planning')
)

// ── 状态行 ────────────────────────────────────────────────────
const viewMode = computed<'idle' | 'thinking' | 'planning' | 'agent'>(() => {
  // planning: agent 正在运行但还在规划阶段（无步骤）
  if (companion.agentRunning && companion.agentTask?.status === 'planning' && (!companion.agentTask.steps || companion.agentTask.steps.length === 0)) return 'planning'
  if (companion.agentRunning || companion.agentTask) return 'agent'
  if (companion.currentThinking) return 'thinking'
  return 'idle'
})

const samStateLabel = computed(() => {
  if (companion.agentRunning) {
    const status = companion.agentTask?.status
    if (status === 'planning') return 'PLANNING'
    if (status === 'running') return 'EXECUTING'
    return 'PROCESSING'
  }
  if (companion.currentThinking) return companion.streaming ? 'ANALYZING' : 'COMPLETE'
  return 'IDLE'
})

const barLabel = computed(() => {
  if (viewMode.value === 'planning') return '[SAM-CORE // 规划中]'
  if (viewMode.value === 'agent') return '[SAM-CORE // Agent 执行]'
  return '[SAM-CORE // 思维核心]'
})

// ── Agent 步骤状态图标 ────────────────────────────────────────
function stepIcon(status: string): string {
  switch (status) {
    case 'done': return '✓'
    case 'failed': return '✗'
    case 'running': return '▶'
    case 'skipped': return '−'
    default: return '○'
  }
}

function stepClass(status: string): string {
  return `step-${status}`
}

// ── 手动展开 ────────────────────────────────────────────────────
function togglePanel() {
  if (expanded.value) {
    expanded.value = false
    manualToggle.value = false
    if (collapseTimer) { clearTimeout(collapseTimer); collapseTimer = null }
  } else {
    expanded.value = true
    manualToggle.value = true
  }
}

// ── 自动滚动 ────────────────────────────────────────────────────
const consoleRef = ref<HTMLElement | null>(null)
watch([thinkingLines, planningLines, () => companion.agentTask?.steps], async () => {
  await nextTick()
  if (consoleRef.value) {
    consoleRef.value.scrollTop = consoleRef.value.scrollHeight
  }
})
</script>

<template>
  <div
    v-if="companion.hudVisible || companion.agentRunning"
    class="sam-hud"
    :class="{ expanded }"
  >
    <!-- 顶部高能战术激光光束 -->
    <div class="hud-scan-beam" />

    <!-- 状态条（始终可见，可点击切换） -->
    <div class="hud-bar" @click="togglePanel">
      <span class="bar-title">{{ barLabel }}</span>
      <span class="bar-spacer" />
      <span class="bar-pulse" :class="samStateLabel.toLowerCase()">●</span>
      <span class="bar-state">{{ samStateLabel }}</span>
      <span class="bar-chevron" :class="{ open: expanded }">▼</span>
    </div>

    <!-- 控制台体（可折叠） -->
    <div class="hud-console" ref="consoleRef">
      <template v-if="expanded">
        <!-- ── Planning 思考视图 ───────────────────────── -->
        <template v-if="viewMode === 'planning'">
          <div class="task-header">
            > 分析任务: {{ companion.agentTask?.user_input }}
          </div>
          <div
            v-for="(line, i) in planningLines"
            :key="'p'+i"
            class="console-line"
          >
            <span class="line-prefix">&gt;</span>
            <span class="line-text planning">{{ line || ' ' }}</span>
          </div>
          <div v-if="companion.agentRunning" class="console-cursor">█</div>
        </template>

        <!-- ── Agent 步骤清单视图 ────────────────────────────── -->
        <template v-else-if="viewMode === 'agent' && companion.agentTask">
          <div class="task-header">
            > 任务: {{ companion.agentTask.user_input }}
          </div>
          <div
            v-for="step in companion.agentTask.steps"
            :key="step.id"
            class="step-row"
            :class="[
              stepClass(step.status),
              { 'step-compacted': companion.isStepCompacted(step.id) }
            ]"
          >
            <span class="step-icon">{{ stepIcon(step.status) }}</span>
            <div class="step-body">
              <div class="step-thought">{{ step.thought || step.action }}</div>
              <!-- 阶段23：被压缩的步骤显示折叠条 -->
              <div
                v-if="companion.isStepCompacted(step.id) && step.observation"
                class="step-compact-bar"
                @click="toggleExpandCompacted(step.id)"
              >
                <span class="compact-icon">{{ expandedCompactedIds.has(step.id) ? '▼' : '▶' }}</span>
                <span class="compact-label">[已压缩] {{ expandedCompactedIds.has(step.id) ? '已展开' : '点击查看原文' }}</span>
              </div>
              <!-- 展开后显示完整原文 -->
              <div
                v-if="expandedCompactedIds.has(step.id) && step.observation"
                class="step-obs step-obs-compacted"
              >
                {{ step.observation }}
              </div>
              <!-- 未压缩的步骤正常显示 -->
              <div
                v-else-if="!companion.isStepCompacted(step.id) && step.observation"
                class="step-obs"
              >
                {{ step.observation.length > 120 ? step.observation.slice(0, 120) + '…' : step.observation }}
              </div>
            </div>
          </div>
          <div v-if="companion.agentRunning" class="console-cursor">█</div>
        </template>

        <!-- ── Thinking 实时流（原有）─────────────────────── -->
        <template v-else-if="viewMode === 'thinking'">
          <div
            v-for="(line, i) in thinkingLines"
            :key="i"
            class="console-line"
          >
            <span class="line-prefix">&gt;</span>
            <span class="line-text">{{ line || ' ' }}</span>
          </div>
          <div v-if="showCursor" class="console-cursor">█</div>
        </template>

        <!-- ── Idle：思考历史列表 ────────────────────────── -->
        <template v-else>
          <!-- 有历史记录 -->
          <template v-if="companion.thinkingHistory.length > 0">
            <div class="history-header">
              <span class="history-title">&gt; 思考历史 ({{ companion.thinkingHistory.length }})</span>
            </div>
            <div
              v-for="record in companion.thinkingHistory"
              :key="record.id"
              class="history-item"
            >
              <div class="history-row" @click="toggleHistoryItem(record.id)">
                <span class="history-chevron" :class="{ open: historyExpandedIds.has(record.id) }">▶</span>
                <span class="history-time">{{ formatRelativeTime(record.timestamp) }}</span>
                <span class="history-type" :class="record.type">{{ record.type === 'planning' ? '[PLAN]' : '[THINK]' }}</span>
                <span class="history-preview">{{ getPreview(record.content) }}</span>
              </div>
              <div v-if="historyExpandedIds.has(record.id)" class="history-content">
                <div
                  v-for="(line, i) in record.content.split('\n')"
                  :key="i"
                  class="console-line"
                >
                  <span class="line-prefix">&gt;</span>
                  <span class="line-text">{{ line || ' ' }}</span>
                </div>
              </div>
            </div>
            <div class="history-footer">
              <button class="history-clear-btn" @click="clearHistory">清除历史</button>
            </div>
          </template>
          <!-- 无历史记录 -->
          <div v-else class="console-hint" style="text-align:left;padding-left:4px;">
            &gt; 暂无思考历史记录
          </div>
        </template>
      </template>

      <!-- 收起时提示 -->
      <div v-else class="console-hint">
        {{ viewMode === 'planning' ? 'PLANNING CORE ACTIVE // CLICK TO EXPAND' : viewMode === 'agent' ? 'AGENT CORE STANDBY // CLICK TO EXPAND' : 'THINKING CORE STANDBY // CLICK TO EXPAND' }}
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ═══════════════════════════════════════════════════════════════
   萨姆装甲红配色：主 #cc3300 / 亮 #ff5544 / 暗 #881100
   ═══════════════════════════════════════════════════════════════ */

.sam-hud {
  position: relative;
  flex-shrink: 0;
  font-family: 'Courier New', Courier, monospace;
  font-size: 12px;
  border-top: 1px solid rgba(204, 51, 0, 0.4);
  transition: max-height 0.4s var(--ease-tactile);
  max-height: 38px;
  overflow: hidden;
  box-shadow: 0 -4px 20px rgba(204, 51, 0, 0.15);
}

.hud-scan-beam {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, #ff5544, #ffaa00, #ff5544, transparent);
  box-shadow: 0 0 10px #ff5544, 0 0 20px #ff0000;
  animation: scan-beam-move 3s ease-in-out infinite;
}

@keyframes scan-beam-move {
  0% { transform: translateX(-100%); }
  50% { transform: translateX(100%); }
  100% { transform: translateX(-100%); }
}

.sam-hud.expanded {
  max-height: 320px;
}

/* ── 状态条 ──────────────────────────────────────────────── */
.hud-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  background: rgba(10, 2, 0, 0.92);
  border-bottom: 1px solid rgba(204, 51, 0, 0.2);
  min-height: 26px;
  cursor: pointer;
  user-select: none;
}

.hud-bar:hover { background: rgba(15, 3, 0, 0.95); }

.bar-title {
  color: #ff5544;
  font-weight: bold;
  font-size: 11px;
  letter-spacing: 1px;
  text-shadow: 0 0 6px rgba(204, 51, 0, 0.5);
}

.bar-spacer { flex: 1; }

.bar-pulse {
  font-size: 8px;
  animation: pulse-dot 1.5s infinite;
}

.bar-pulse.idle { color: rgba(204, 51, 0, 0.4); animation: none; }
.bar-pulse.analyzing { color: #ff5544; text-shadow: 0 0 8px rgba(255, 85, 68, 0.7); }
.bar-pulse.complete { color: #cc6644; animation: none; }
.bar-pulse.planning { color: #ffaa44; text-shadow: 0 0 8px rgba(255, 170, 68, 0.7); }
.bar-pulse.executing { color: #ff5544; text-shadow: 0 0 8px rgba(255, 85, 68, 0.7); }
.bar-pulse.processing { color: #ff5544; animation: pulse-dot 0.8s infinite; }

@keyframes pulse-dot {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}

.bar-state {
  color: rgba(255, 85, 68, 0.7);
  font-size: 10px;
  letter-spacing: 2px;
}

.bar-chevron {
  color: rgba(204, 51, 0, 0.5);
  font-size: 9px;
  transition: transform 0.3s;
}

.bar-chevron.open { transform: rotate(180deg); }

/* ── 控制台体 ────────────────────────────────────────────── */
.hud-console {
  background: rgba(6, 0, 0, 0.93);
  padding: 6px 14px 10px;
  max-height: 280px;
  overflow-y: auto;
  scroll-behavior: smooth;
  border-top: 1px solid rgba(204, 51, 0, 0.12);
}

.hud-console::-webkit-scrollbar { width: 4px; }
.hud-console::-webkit-scrollbar-track { background: transparent; }
.hud-console::-webkit-scrollbar-thumb { background: rgba(204, 51, 0, 0.3); border-radius: 2px; }

/* ── ITEM 步骤行 ─────────────────────────────────── */
.task-header {
  color: #ffaa44;
  font-size: 11px;
  padding: 2px 0 6px;
  border-bottom: 1px solid rgba(204, 51, 0, 0.15);
  margin-bottom: 6px;
  word-break: break-all;
}

.step-row {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid rgba(204, 51, 0, 0.06);
  animation: line-in 0.25s var(--ease-tactile);
}

.step-row.step-done { opacity: 0.8; }
.step-row.step-failed { background: rgba(204, 51, 0, 0.08); }
.step-row.step-running { background: rgba(255, 85, 68, 0.06); }
.step-row.step-skipped { opacity: 0.4; }

.step-icon {
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  font-size: 11px;
}

.step-done .step-icon { color: #44cc44; }
.step-failed .step-icon { color: #ff4444; }
.step-running .step-icon { color: #ffaa44; }
.step-skipped .step-icon { color: rgba(204, 51, 0, 0.3); }
.step-pending .step-icon { color: rgba(204, 51, 0, 0.3); }

.step-body { flex: 1; min-width: 0; }
.step-thought { font-size: 11px; color: #ffccaa; }
.step-obs {
  font-size: 10px;
  color: rgba(255, 170, 130, 0.6);
  margin-top: 2px;
  word-break: break-all;
}

/* ── 阶段23：被压缩步骤 ────────────────────────────── */
.step-compact-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  padding: 3px 8px;
  background: rgba(204, 51, 0, 0.08);
  border: 1px dashed rgba(204, 51, 0, 0.2);
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.2s;
  user-select: none;
}
.step-compact-bar:hover {
  background: rgba(204, 51, 0, 0.15);
}
.compact-icon {
  font-size: 9px;
  color: rgba(255, 170, 100, 0.6);
  flex-shrink: 0;
}
.compact-label {
  font-size: 10px;
  color: rgba(255, 170, 100, 0.5);
}
.step-obs-compacted {
  padding: 4px 8px;
  background: rgba(204, 51, 0, 0.05);
  border-left: 2px solid rgba(204, 51, 0, 0.15);
  border-radius: 0 4px 4px 0;
  max-height: 120px;
  overflow-y: auto;
  white-space: pre-wrap;
  font-family: 'Consolas', monospace;
}
.step-compacted .step-obs:not(.step-obs-compacted) {
  display: none;
}

/* ── 控制台行（thinking 模式）────────────────────────── */
.console-line {
  display: flex;
  gap: 8px;
  padding: 1px 0;
  animation: line-in 0.2s var(--ease-tactile);
}

@keyframes line-in {
  from { opacity: 0; transform: translateX(-10px) scale(0.98); }
  to   { opacity: 1; transform: translateX(0) scale(1); }
}

.line-prefix {
  color: rgba(204, 51, 0, 0.5);
  flex-shrink: 0;
}

.line-text {
  color: #ff5544;
  white-space: pre-wrap;
  word-break: break-all;
}

.line-text.planning {
  color: #ffc166;
}

/* ── 闪烁光标 ────────────────────────────────────────────── */
.console-cursor {
  color: #ff5544;
  animation: blink-cursor 1s step-end infinite;
  padding-top: 1px;
}

@keyframes blink-cursor {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* ── 收起时提示 ──────────────────────────────────────────── */
.console-hint {
  color: rgba(204, 51, 0, 0.25);
  font-size: 10px;
  letter-spacing: 1px;
  padding: 4px 0;
  text-align: center;
}

/* ── 思考历史 ────────────────────────────────────────── */
.history-header {
  padding: 4px 0 8px;
  border-bottom: 1px solid rgba(204, 51, 0, 0.2);
  margin-bottom: 6px;
}
.history-title {
  color: #ffaa44;
  font-size: 11px;
  font-weight: bold;
}
.history-item {
  border-bottom: 1px solid rgba(204, 51, 0, 0.08);
}
.history-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 0;
  cursor: pointer;
  user-select: none;
}
.history-row:hover {
  background: rgba(204, 51, 0, 0.06);
}
.history-chevron {
  color: rgba(204, 51, 0, 0.5);
  font-size: 8px;
  flex-shrink: 0;
  transition: transform 0.2s;
}
.history-chevron.open {
  transform: rotate(90deg);
}
.history-time {
  color: rgba(204, 51, 0, 0.45);
  font-size: 10px;
  flex-shrink: 0;
  min-width: 50px;
}
.history-type {
  font-size: 10px;
  flex-shrink: 0;
  padding: 0 4px;
  border-radius: 2px;
}
.history-type.thinking {
  color: #ff5544;
  background: rgba(255, 85, 68, 0.1);
}
.history-type.planning {
  color: #ffaa44;
  background: rgba(255, 170, 68, 0.1);
}
.history-preview {
  color: rgba(255, 170, 130, 0.6);
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.history-content {
  padding: 2px 0 6px 18px;
}
.history-footer {
  display: flex;
  justify-content: flex-end;
  padding: 8px 0 4px;
}
.history-clear-btn {
  background: none;
  border: 1px solid rgba(204, 51, 0, 0.3);
  color: rgba(204, 51, 0, 0.6);
  font-family: inherit;
  font-size: 10px;
  padding: 2px 8px;
  cursor: pointer;
  letter-spacing: 1px;
  transition: all 0.2s;
}
.history-clear-btn:hover {
  border-color: rgba(255, 85, 68, 0.6);
  color: #ff5544;
  background: rgba(204, 51, 0, 0.06);
}
</style>

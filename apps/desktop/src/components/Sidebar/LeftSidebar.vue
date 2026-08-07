<script setup lang="ts">
/**
 * 左侧栏 — 搜索 / 工具菜单 / 历史会话 / 任务列表 / 工作空间 / 用户。
 * 功能：搜索过滤、历史会话管理、工作空间切换、新建任务→聚焦输入栏、设置弹窗唤起。
 */
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useCompanionStore } from '@/stores/companion'
import SettingsModal from '@/components/Settings/SettingsModal.vue'
import { pickFolder } from '@/services/folderPicker'
import { useTaskSync } from '@/composables/useTaskSync'

const companion = useCompanionStore()
const { sortedTasks, toggleTask, deleteTask, addTask, onExternalChange } = useTaskSync()

// ── 设置弹窗 ────────────────────────────────────────────
const showSettings = ref(false)

// ── 任务历史面板 ──────────────────────────────────────
const showTaskHistory = ref(false)

// ── 监听跨组件任务同步 ──────────────────────────────────
let unsubTasks: (() => void) | null = null
onMounted(() => { unsubTasks = onExternalChange() })
onUnmounted(() => { if (unsubTasks) unsubTasks() })

// ── 搜索 ──────────────────────────────────────────────────
const searchQuery = ref('')

function clearSearch() {
  searchQuery.value = ''
}

// ── 系统工具（阶段4：激活为 Agent 快捷入口）──────────────────
const tools = [
  { id: 'agent-console', label: 'Agent 控制台', disabled: false, action: () => toggleHud() },
  { id: 'tools-center', label: '工具中心', disabled: false, action: () => { showSettings.value = true } },
  { id: 'task-history', label: '任务历史', disabled: false, action: () => { showTaskHistory.value = !showTaskHistory.value } },
]

function toggleHud() {
  // 展开/收起 SAM HUD
  companion.hudVisible = !companion.hudVisible
}

// ── 历史会话（spec 3.9.1）─────────────────────────────────
const searchLower = computed(() => searchQuery.value.toLowerCase())

const filteredSessions = computed(() => {
  if (!searchLower.value) return companion.sessions
  return companion.sessions.filter(s => s.title.toLowerCase().includes(searchLower.value))
})

function handleNewSession() {
  companion.createSession('新会话')
}

function handleSwitchSession(id: string) {
  companion.switchSession(id)
}

function handleDeleteSession(id: string) {
  companion.deleteSession(id)
}

// ── 会话重命名 ──────────────────────────────────────────
const renamingId = ref<string | null>(null)
const renameTitle = ref('')
const renameInput = ref<HTMLInputElement | null>(null)

function startRename(s: { id: string; title: string }, event: Event) {
  event.stopPropagation()
  renamingId.value = s.id
  renameTitle.value = s.title
  nextTick(() => { renameInput.value?.focus(); renameInput.value?.select() })
}

function commitRename() {
  if (renamingId.value && renameTitle.value.trim()) {
    companion.renameSession(renamingId.value, renameTitle.value.trim())
  }
  renamingId.value = null
  renameTitle.value = ''
}

function cancelRename() {
  renamingId.value = null
  renameTitle.value = ''
}

// ── 任务列表 ──────────────────────────────────────────────
// 搜索过滤
const filteredTasks = computed(() => {
  if (!searchLower.value) return sortedTasks.value
  return sortedTasks.value.filter((t) => t.text.toLowerCase().includes(searchLower.value))
})

const showAddTask = ref(false)
const newTaskText = ref('')
const addTaskInput = ref<HTMLInputElement | null>(null)

function startAddTask(e?: Event) {
  e?.stopPropagation()
  e?.preventDefault()
  showAddTask.value = !showAddTask.value
  if (showAddTask.value) {
    newTaskText.value = ''
    nextTick(() => {
      addTaskInput.value?.focus()
    })
  }
}

function cancelAddTask() {
  showAddTask.value = false
  newTaskText.value = ''
}

function commitNewTask() {
  const text = newTaskText.value.trim()
  if (text) {
    addTask(text)
  }
  newTaskText.value = ''
  showAddTask.value = false
}

// ── 工作空间（阶段4.5：目录选择器 + 后端驱动）──────────
const showWsAdd = ref(false)
const newWsName = ref('')
const newWsPath = ref('')

const filteredWorkspaces = computed(() => {
  if (!searchLower.value) return companion.workspaces
  return companion.workspaces.filter(w =>
    w.name.toLowerCase().includes(searchLower.value) ||
    w.path.toLowerCase().includes(searchLower.value)
  )
})

function selectWorkspace(id: string | null) {
  companion.setActiveWorkspace(id)
}

async function handlePickFolder() {
  const p = await pickFolder()
  if (p) {
    newWsPath.value = p
    // 名称自动取路径最后一段
    const segs = p.replace(/\\/g, '/').split('/').filter(Boolean)
    if (!newWsName.value.trim() && segs.length > 0) {
      newWsName.value = segs[segs.length - 1]
    }
  }
}

async function handleWsAdd() {
  const name = newWsName.value.trim()
  const path = newWsPath.value.trim()
  if (!name || !path) { showWsAdd.value = false; return }
  await companion.addWorkspace(name, path)
  newWsName.value = ''
  newWsPath.value = ''
  showWsAdd.value = false
}

async function handleDeleteWs(id: string) {
  await companion.removeWorkspace(id)
}

function truncatePath(p: string): string {
  return p.length > 35 ? '…' + p.slice(-34) : p
}

function taskHistoryStatusLabel(status: string): string {
  switch (status) {
    case 'done': return '✓ 完成'
    case 'failed': return '✗ 失败'
    case 'running': return '▶ 执行中'
    case 'planning': return '○ 规划中'
    default: return status
  }
}

// ── 新建任务 → 聚焦输入栏 ─────────────────────────────────
function handleNewTask(e?: Event) {
  e?.stopPropagation()
  e?.preventDefault()
  startAddTask(e)
}

// ── 用户信息（切换设置弹窗）────────────────────────────────
function handleOpenSettings() {
  showSettings.value = true
}

const userDisplayName = computed(() =>
  companion.isWork ? '流萤 // SAM' : '流萤',
)
const userAvatarChar = computed(() =>
  companion.isWork ? 'S' : '萤',
)
</script>

<template>
  <aside class="sidebar">
    <!-- T-29 修复：滚动区（搜索+列表）与 footer 解耦——footer 固定底部，不被长列表挤出视口 -->
    <div class="sidebar-scroll">
    <!-- 搜索 -->
    <div class="search-box">
      <span class="search-icon">🔍</span>
      <input
        v-model="searchQuery"
        type="text"
        placeholder="搜索会话或任务..."
        class="search-input"
      />
      <button v-if="searchQuery" class="search-clear" @click="clearSearch">×</button>
    </div>

    <!-- 新建任务 -->
    <button class="new-task-btn" @click="handleNewTask">
      <span>＋</span> 新建任务
    </button>

    <!-- 工具菜单 -->
    <nav class="tools-nav">
      <button
        v-for="t in tools"
        :key="t.id"
        class="tool-item"
        :class="{ disabled: t.disabled }"
        :title="t.disabled ? '开发中' : t.label"
        @click="!t.disabled && t.action()"
      >
        <span class="tool-icon">▸</span>
        {{ t.label }}
      </button>
    </nav>

    <hr class="sep" />

    <!-- 历史会话列表 -->
    <div class="section-title">
      历史会话
      <button class="add-task-btn" title="新建会话" @click="handleNewSession">＋</button>
    </div>
    <ul v-if="filteredSessions.length" class="session-list">
      <li
        v-for="s in filteredSessions"
        :key="s.id"
        class="session-item"
        :class="{ active: companion.activeSessionId === s.id, renaming: renamingId === s.id }"
        @click="renamingId !== s.id && handleSwitchSession(s.id)"
      >
        <span class="session-dot" />
        <div class="session-info">
          <input
            v-if="renamingId === s.id"
            ref="renameInput"
            v-model="renameTitle"
            class="session-rename-input"
            @keydown.enter="commitRename()"
            @keydown.escape="cancelRename()"
            @blur="commitRename()"
            @click.stop
          />
          <template v-else>
            <span class="session-title" @dblclick="startRename(s, $event)" title="双击重命名">{{ s.title }}</span>
            <span class="session-time">{{ new Date(s.updatedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}</span>
          </template>
        </div>
        <button class="session-del" title="删除会话" @click.stop="handleDeleteSession(s.id)">×</button>
      </li>
    </ul>
    <div v-else class="empty-hint">暂无历史会话</div>

    <hr class="sep" />

    <!-- 任务列表 -->
    <div class="section-title clickable" @click="startAddTask($event)">
      <span>任务</span>
      <button class="add-task-btn" title="添加任务" @click.stop.prevent="startAddTask($event)">＋</button>
    </div>

    <!-- 新增任务输入 -->
    <div v-if="showAddTask" class="add-task-row">
      <input
        ref="addTaskInput"
        v-model="newTaskText"
        type="text"
        placeholder="输入任务名..."
        class="add-task-input"
        @keydown.enter="commitNewTask"
        @keydown.escape="cancelAddTask"
      />
      <button class="task-confirm-btn" title="确认添加" @click="commitNewTask">✓</button>
      <button class="task-cancel-btn" title="取消" @click="cancelAddTask">✕</button>
    </div>

    <ul v-if="filteredTasks.length" class="task-list">
      <li
        v-for="t in filteredTasks"
        :key="t.id"
        class="task-item"
        :class="{ done: t.done }"
        @click="toggleTask(t.id)"
      >
        <span class="task-check">{{ t.done ? '☑' : '☐' }}</span>
        <span class="task-text">{{ t.text }}</span>
        <button class="task-del" title="删除任务" @click.stop="deleteTask(t.id, $event)">×</button>
      </li>
    </ul>
    <div v-else class="empty-hint">无匹配结果</div>

    <hr class="sep" />

    <!-- 任务历史面板 -->
    <div v-if="showTaskHistory" class="history-panel">
      <div class="history-header">
        Agent 任务历史
        <button class="add-task-btn" @click="showTaskHistory = false">×</button>
      </div>
      <div v-if="companion.taskHistory.length" class="history-list">
        <div v-for="h in companion.taskHistory" :key="h.id" class="history-item">
          <span class="h-status">{{ taskHistoryStatusLabel(h.status) }}</span>
          <span class="h-input">{{ h.user_input.slice(0, 40) }}</span>
          <span class="h-time">{{ new Date(h.created_at).toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' }) }}</span>
        </div>
      </div>
      <div v-else class="empty-hint">暂无任务记录</div>
    </div>

    <!-- 工作空间（阶段4.5：目录路径 + 会话关联） -->
    <div class="section-title">
      📁 工作空间
      <button class="add-task-btn" title="添加工作空间" @click="showWsAdd = !showWsAdd">＋</button>
    </div>

    <div v-if="showWsAdd" class="ws-add-form">
      <input v-model="newWsName" class="add-task-input" placeholder="空间名称（留空自动取文件夹名）"
        @keydown.escape="showWsAdd = false" />
      <button class="ws-pick-btn" @click="handlePickFolder">📁 选择目录</button>
      <div v-if="newWsPath" class="ws-path-preview">{{ newWsPath }}</div>
      <div v-else class="ws-path-hint">尚未选择目录</div>
      <button class="ws-add-ok" @click="handleWsAdd">✓ 创建</button>
    </div>

    <ul v-if="filteredWorkspaces.length" class="workspace-list">
      <li
        v-for="ws in filteredWorkspaces"
        :key="ws.id"
        class="ws-item"
        :class="{ active: companion.activeWorkspaceId === ws.id, 'ws-missing': !ws.pathExists }"
        @click="selectWorkspace(companion.activeWorkspaceId === ws.id ? null : ws.id)"
        :title="!ws.pathExists ? '工作空间路径不存在，请在文件资源管理器中检查' : ''"
      >
        <span class="ws-dot" :class="{ 'ws-dot-missing': !ws.pathExists }" />
        <div class="ws-info">
          <span class="ws-name">
            {{ ws.name }}
            <span v-if="ws.isDefault" class="ws-badge">🔒</span>
            <span v-if="!ws.pathExists" class="ws-warn">⚠ 不可用</span>
          </span>
          <span class="ws-path">{{ truncatePath(ws.path) }}</span>
        </div>
        <button
          v-if="!ws.isDefault"
          class="ws-act-btn danger"
          title="删除"
          @click.stop="handleDeleteWs(ws.id)">×</button>
      </li>
    </ul>
    <div v-if="companion.workspaces.length === 0 && !showWsAdd" class="empty-hint">
      暂无工作空间 — 点击＋添加目录
    </div>
    </div>
    <!-- /sidebar-scroll -->

    <!-- 底部用户区（固定底部，不随列表滚动） -->
    <div class="sidebar-footer">
      <div class="user-profile" @click="handleOpenSettings" title="点击打开系统设置">
        <span class="user-avatar">{{ userAvatarChar }}</span>
        <span class="user-name">{{ userDisplayName }}</span>
      </div>
    </div>

  </aside>

  <!-- 设置弹窗（Teleport 到 body 避免 sidebar overflow 裁剪） -->
  <Teleport to="body">
    <SettingsModal v-if="showSettings" @close="showSettings = false" />
  </Teleport>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  box-sizing: border-box;  /* T-29 修复：content-box 高度 100%+padding 溢出被父容器裁剪 */
  background: var(--bg-surface);
  border-right: 1px solid var(--border-main);
  padding: 16px 14px;
  gap: 10px;
  /* overflow-y 移给 .sidebar-scroll（footer 固定底部，不与列表共滚） */
}

/* T-29 修复：滚动区（搜索+列表）独占剩余高度；footer 在滚动区外固定底部 */
.sidebar-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* ── 搜索 ──────────────────────────────────────── */
.search-box {
  display: flex;
  align-items: center;
  padding: 7px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  border: 1px solid var(--border-subtle);
}

.search-icon { font-size: 13px; color: var(--text-muted); margin-right: 6px; }

.search-input {
  border: none;
  background: none;
  flex: 1;
  font-size: 13px;
  color: var(--text-primary);
  outline: none;
}

.search-input::placeholder { color: var(--text-placeholder); }

.search-clear {
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 16px;
  padding: 0 2px;
  line-height: 1;
}

.search-clear:hover { color: var(--text-primary); }

/* ── 新建按钮 ──────────────────────────────────── */
.new-task-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--border-accent);
  border-radius: var(--radius-sm);
  background: var(--accent-light);
  color: var(--accent-strong);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--transition-fast);
}

.new-task-btn:hover {
  background: var(--accent);
  color: var(--text-on-accent);
}

/* ── 工具导航 ──────────────────────────────────── */
.tools-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tool-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: none;
  background: none;
  color: var(--text-secondary);
  font-size: 13px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--transition-fast);
  text-align: left;
}

.tool-item:hover { background: var(--bg-surface-hover); color: var(--text-primary); }

.tool-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.tool-item.disabled:hover { background: none; }

.tool-icon { font-size: 10px; color: var(--text-muted); }

/* ── 分割线 ────────────────────────────────────── */
.sep {
  border: none;
  border-top: 1px solid var(--border-subtle);
  margin: 4px 0;
}

/* ── 区块标题 ──────────────────────────────────── */
.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-muted);
  padding: 2px 2px;
  user-select: none;
}

.section-title.clickable {
  cursor: pointer;
}

.section-title.clickable:hover {
  color: var(--text-primary);
}

.add-task-btn {
  border: 1px solid var(--border-subtle);
  background: var(--bg-input);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 14px;
  font-weight: 700;
  padding: 2px 6px;
  line-height: 1;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  min-height: 22px;
  transition: all var(--transition-fast);
}

.add-task-btn:hover {
  color: var(--accent-strong);
  background: var(--accent-light);
  border-color: var(--border-accent);
  transform: scale(1.06);
}

.add-task-btn:active {
  transform: scale(0.94);
}

/* ── 新增任务输入 ──────────────────────────────── */
.add-task-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 2px;
  margin: 4px 0;
}

.add-task-input {
  flex: 1;
  min-width: 0;
  box-sizing: border-box;
  padding: 5px 8px;
  border: 1px solid var(--border-accent);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
}

.add-task-input:focus {
  border-color: var(--accent);
}

.task-confirm-btn,
.task-cancel-btn {
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: bold;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 4px;
  transition: color var(--transition-fast), background var(--transition-fast);
}

.task-confirm-btn:hover {
  color: #5ebd82;
  background: rgba(94, 189, 130, 0.15);
}

.task-cancel-btn:hover {
  color: #cc3300;
  background: rgba(204, 51, 0, 0.15);
}

/* ── 空提示 ────────────────────────────────────── */
.empty-hint {
  font-size: 11px;
  color: var(--text-muted);
  padding: 4px 8px;
  font-style: italic;
}

/* ── 历史会话列表 ──────────────────────────────── */
.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 140px;
  overflow-y: auto;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.session-item:hover { background: var(--bg-surface-hover); }
.session-item.active { background: var(--accent-light); }

.session-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-muted);
  flex-shrink: 0;
}

.session-item.active .session-dot { background: var(--accent); }

.session-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.session-title {
  font-size: 12px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-rename-input {
  font-size: 12px;
  padding: 2px 6px;
  border: 1px solid var(--accent);
  border-radius: 4px;
  background: var(--bg-input, #fff);
  color: var(--text-primary);
  outline: none;
  width: 100%;
}

.session-time {
  font-size: 10px;
  color: var(--text-muted);
}

.session-del {
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 16px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  border-radius: 4px;
  visibility: hidden;
}

.session-item:hover .session-del { visibility: visible; }
.session-del:hover { color: #cc3300; background: rgba(204, 51, 0, 0.1); }

/* ── 任务列表 ──────────────────────────────────── */
.task-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 160px;
  overflow-y: auto;
  padding-right: 2px;
}

.task-list::-webkit-scrollbar {
  width: 4px;
}

.task-list::-webkit-scrollbar-thumb {
  background: var(--border-subtle);
  border-radius: 4px;
}

.task-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-muted);
}

.task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  color: var(--text-primary);
  transition: background var(--transition-fast);
}

.task-item:hover { background: var(--bg-surface-hover); }

.task-item.done .task-text { text-decoration: line-through; color: var(--text-muted); opacity: 0.7; }

.task-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.task-check { font-size: 13px; flex-shrink: 0; }

.task-del {
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  border-radius: 4px;
  opacity: 0;
  margin-left: auto;
  transition: opacity var(--transition-fast), color var(--transition-fast);
}

.task-item:hover .task-del { opacity: 1; }
.task-del:hover { color: #cc3300; background: rgba(204, 51, 0, 0.1); }

/* ── 工作空间 ──────────────────────────────────── */
.workspace-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ws-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.ws-item:hover { background: var(--bg-surface-hover); }
.ws-item.active { background: var(--accent-light); color: var(--accent-strong); font-weight: 600; }

.ws-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-muted);
  flex-shrink: 0;
}

.ws-item.active .ws-dot { background: var(--accent); }
.ws-dot-missing { background: #cc3300 !important; }

.ws-info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ws-name { font-size: 12px; color: var(--text-primary); }
.ws-path { font-size: 10px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.ws-badge { font-size: 10px; margin-left: 2px; }
.ws-warn { font-size: 10px; color: #cc3300; font-weight: 600; }

.ws-missing { opacity: 0.6; background: rgba(204, 51, 0, 0.06); }
.ws-missing .ws-name { color: #cc3300; }
.ws-act-btn {
  border: none; background: none; color: var(--text-muted); font-size: 14px;
  cursor: pointer; padding: 0 3px; border-radius: 3px; visibility: hidden;
}
.ws-item:hover .ws-act-btn { visibility: visible; }
.ws-act-btn:hover { color: var(--text-primary); }
.ws-act-btn.danger:hover { color: #cc3300; }

.ws-add-form { display: flex; flex-direction: column; gap: 6px; margin-bottom: 6px; padding: 8px; background: var(--bg-surface-hover); border-radius: 8px; border: 1px solid var(--border-subtle); }
.ws-pick-btn {
  border: 1px solid var(--border-accent); background: var(--accent-light); color: var(--accent-strong);
  padding: 5px 0; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600;
  text-align: center; transition: background var(--transition-fast);
}
.ws-pick-btn:hover { background: var(--accent); color: var(--text-on-accent); }
.ws-path-preview { font-size: 10px; color: var(--accent); word-break: break-all; }
.ws-path-hint { font-size: 10px; color: var(--text-muted); font-style: italic; }
.ws-add-ok {
  align-self: flex-end; border: none; background: var(--accent); color: var(--text-on-accent);
  padding: 3px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;
}

/* ── 任务历史面板 ──────────────────────────────── */
.history-panel {
  background: var(--bg-surface-hover);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  max-height: 160px;
  overflow-y: auto;
}
.history-header {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 11px; font-weight: 700; color: var(--text-muted); margin-bottom: 6px;
}
.history-list { display: flex; flex-direction: column; gap: 2px; }
.history-item {
  display: flex; align-items: center; gap: 6px; font-size: 11px; padding: 2px 0;
}
.h-status { flex-shrink: 0; font-size: 10px; color: var(--accent); }
.h-input { flex: 1; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.h-time { flex-shrink: 0; color: var(--text-muted); font-size: 10px; }

/* ── 底部用户 ──────────────────────────────────── */
.sidebar-footer {
  flex-shrink: 0;          /* 固定底部，不压缩 */
  padding-top: 10px;
  padding-bottom: 10px;    /* 底部留白，避免贴窗口底边被裁 */
  border-top: 1px solid var(--border-subtle);
  background: var(--bg-surface);
}

.user-profile {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px;
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast);
}

.user-profile:hover { background: var(--bg-surface-hover); }

.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--accent);
  color: var(--text-on-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
}

.user-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
</style>

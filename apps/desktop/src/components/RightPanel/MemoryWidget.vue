<script setup lang="ts">
/**
 * 记忆管理卡片 — 对应 spec 阶段3/8.7。
 * 支持带显式【🔍 搜索】按钮的向量语义检索、归属空间下拉选择与折叠手风琴。
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useCompanionStore } from '@/stores/companion'
import type { MemoryItem, MemoryType } from '@shared/index'
import * as api from '@/services/api'

const companion = useCompanionStore()

const memories = ref<(MemoryItem & { isUniversal?: boolean })[]>([])
const loading = ref(false)
const searching = ref(false)
const searchQuery = ref('')
const isSearchActive = ref(false)
const editingId = ref<string | null>(null)
const editContent = ref('')
const showAddForm = ref(false)

// 表单字段：记忆类型与空间分组
const newType = ref<MemoryType>('preference')
const newNamespace = ref<string>('shared_profile')
const newContent = ref('')

// 折叠手风琴状态
const collapsedGroups = ref<Record<string, boolean>>({})
const errorMessage = ref('')

// ── 防止竞态条件：版本计数器 ───────────────────────────────
// loadMemories() 与 doSearch() 各自维护递增版本号，
// 只有当前调用是最新版本时才应用结果，防止旧请求覆盖新数据。
let loadVersion = 0
let searchVersion = 0
let lastLoadTime = 0
const MIN_LOAD_INTERVAL = 3000 // 两次加载最小间隔 3 秒，防频繁刷新

// ── 数据量大时的分页展示（每组默认显示 3 条，可展开全部或收起）──
let _ = 0  // 保留位置；旧 GROUP_PAGE_SIZE 已替换为 PAGE_SIZE_STEP

function handleMemoryUpdated() {
  lastLoadTime = 0 // 重置冷却时间，保证实时加载
  loadMemories()
}

function toggleGroup(key: string) {
  collapsedGroups.value[key] = !collapsedGroups.value[key]
}

const typeLabels: Record<string, string> = {
  user_profile: '个人信息',
  preference: '偏好',
  event: '事件',
  promise: '承诺',
  emotion: '情感',
}

const namespaceLabels: Record<string, string> = {
  shared_profile: '🌐 全局共享',
  work_tasks: '💼 工作专属',
  daily_life: '☕ 日常专属',
}

// ── 触发后端向量语义检索 API ─────────────────────────────
async function doSearch() {
  const q = searchQuery.value.trim()
  if (!q) {
    clearSearch()
    return
  }
  const version = ++searchVersion
  searching.value = true
  isSearchActive.value = true
  collapsedGroups.value = {} // 搜索时强制展开所有匹配的分组
  groupExpanded.value = {}   // 重置分页展开状态
  console.log(`[MemoryWidget] 🔍 开始搜索: "${q}", mode=${companion.mode}, version=${version}`)
  try {
    const results = await api.searchMemories(q, companion.mode)
    errorMessage.value = ''
    console.log(`[MemoryWidget] ✅ 搜索返回 ${results.length} 条结果, version=${version}`)
    if (version === searchVersion) {
      memories.value = results
      console.log(`[MemoryWidget] 📝 memories.value 已更新, 当前共 ${memories.value.length} 条`)
    } else {
      console.log(`[MemoryWidget] ⏭️ 搜索 v${version} 结果被丢弃 (当前版本 v${searchVersion})`)
    }
  } catch (e) {
    console.warn('[MemoryWidget] ❌ 搜索失败:', e)
  } finally {
    if (version === searchVersion) {
      searching.value = false
    }
  }
}

function clearSearch() {
  searchQuery.value = ''
  isSearchActive.value = false
  collapsedGroups.value = {}
  groupVisibleCount.value = {}
  errorMessage.value = ''
  loadMemories()
}

// ── 渐进加载：每组默认显示 PAGE_SIZE_STEP 条，可展开全部或收起 ──
const PAGE_SIZE_STEP = 3
const groupVisibleCount = ref<Record<string, number>>({})

function loadMoreGroup(key: string) {
  // 展开全部：找到该分组的总条数，一次性全部显示
  const group = groupedMemories.value[key]
  if (group) {
    groupVisibleCount.value[key] = group.items.length
  }
}

function collapseGroup(key: string) {
  groupVisibleCount.value[key] = PAGE_SIZE_STEP
}

// 分组 (按命名空间划分: 🌐 全局共享偏好 / 💼 工作模式专有 / ☕ 日常生活记录)
const groupedMemories = computed(() => {
  const groups: Record<string, { label: string; items: (MemoryItem & { isUniversal?: boolean })[] }> = {
    shared_profile: { label: '🌐 全局共享偏好', items: [] },
    work_tasks: { label: '💼 工作模式专有', items: [] },
    daily_life: { label: '☕ 日常生活记录', items: [] },
  }

  for (const m of memories.value) {
    const ns = m.namespace || (m.isUniversal ? 'shared_profile' : 'daily_life')
    if (!groups[ns]) {
      groups[ns] = { label: `📁 ${ns}`, items: [] }
    }
    groups[ns].items.push(m)
  }

  // 移除空分组
  const result: Record<string, { label: string; items: (MemoryItem & { isUniversal?: boolean })[] }> = {}
  for (const [k, v] of Object.entries(groups)) {
    if (v.items.length > 0) {
      result[k] = v
    }
  }
  return result
})

// ── 加载全量记忆列表（双重竞态防护 + 冷却） ────────────
async function loadMemories() {
  // 第 1 道防线：搜索模式下拒绝加载
  if (isSearchActive.value) return

  // 冷却检查：避免 WebSocket/轮询/手动刷新同时触发导致频繁请求
  const now = Date.now()
  if (now - lastLoadTime < MIN_LOAD_INTERVAL) return
  lastLoadTime = now

  const version = ++loadVersion
  loading.value = true
  try {
    const result = await api.getMemories('', companion.mode)
    // ⚠️ 第 2 道防线：API 调用期间用户可能开始了搜索，
    // 此时必须丢弃结果，防止覆盖 doSearch() 已设置的搜索结果
    if (isSearchActive.value) return
    // 仅当此调用仍是最新版本时才应用结果
    if (version === loadVersion) {
      memories.value = result
      errorMessage.value = ''
    }
  } catch (e: unknown) {
    console.warn('[MemoryWidget] ❌ 加载记忆失败:', e)
    errorMessage.value = (e instanceof Error ? e.message : String(e))
    // 不在错误时清空已有数据，保留用户当前看到的内容
  } finally {
    if (version === loadVersion) {
      loading.value = false
    }
  }
}

// ── 删除记忆（等 API 确认后再移除 UI，不再乐观更新） ────
async function deleteMemory(id: string) {
  try {
    await api.deleteMemory(id)
    // 仅在 API 确认成功后才从列表中移除
    memories.value = memories.value.filter((m) => m.id !== id)
  } catch (e) {
    console.warn('[MemoryWidget] 删除记忆失败:', e)
  }
}

function startEdit(m: MemoryItem) {
  editingId.value = m.id
  editContent.value = m.content
}

async function commitEdit() {
  const m = memories.value.find((m) => m.id === editingId.value)
  if (m && editContent.value.trim()) {
    try {
      await api.updateMemory(m.id, { content: editContent.value.trim() })
      m.content = editContent.value.trim()
    } catch (e) {
      console.warn('[MemoryWidget] 更新记忆失败:', e)
    }
  }
  editingId.value = null
  editContent.value = ''
}

function cancelEdit() {
  editingId.value = null
  editContent.value = ''
}

// ── 添加记忆 (含空间选择) ──────────────────────────────────────
const memoryTypes: { value: MemoryType; label: string }[] = [
  { value: 'preference', label: '偏好' },
  { value: 'user_profile', label: '个人信息' },
  { value: 'event', label: '事件' },
  { value: 'promise', label: '承诺' },
  { value: 'emotion', label: '情感' },
]

const namespaceOptions = [
  { value: 'shared_profile', label: '🌐 全局共享 (推荐)' },
  { value: 'work_tasks', label: '💼 工作专属' },
  { value: 'daily_life', label: '☕ 日常专属' },
]

function openAddForm() {
  showAddForm.value = true
  newType.value = 'preference'
  newNamespace.value = 'shared_profile'
  newContent.value = ''
}

async function commitAdd() {
  const content = newContent.value.trim()
  if (!content) return
  try {
    const created = await api.upsertMemory({
      type: newType.value,
      content,
      namespace: newNamespace.value,
      confidence: 1.0,
    })
    memories.value.unshift(created)
  } catch (e) {
    console.warn('[MemoryWidget] 添加记忆失败:', e)
  }
  showAddForm.value = false
  newContent.value = ''
}

const show = computed(() => true)

onMounted(async () => {
  window.addEventListener('memory-updated', handleMemoryUpdated)
  // 首次加载：后端可能还未就绪，最多重试 3 次（每次间隔 2 秒）
  for (let attempt = 0; attempt < 3; attempt++) {
    lastLoadTime = 0  // 重置冷却，允许重试
    await loadMemories()
    if (!errorMessage.value) break
    if (attempt < 2) {
      console.log(`[MemoryWidget] 首次加载失败，2s 后重试 (${attempt + 1}/3)`)
      await new Promise(resolve => setTimeout(resolve, 2000))
    }
  }
})

onUnmounted(() => {
  window.removeEventListener('memory-updated', handleMemoryUpdated)
})

// 切换模式时重新加载对应 namespace 的记忆
watch(() => companion.mode, () => { loadMemories() })
</script>

<template>
  <div v-if="show" class="memory-widget">
    <!-- 头部标题栏 -->
    <div class="widget-title">
      <span>🧠 记忆管理</span>
      <div class="widget-actions">
        <button class="refresh-btn" @click="openAddForm" title="添加记忆">＋</button>
        <button class="refresh-btn" @click="loadMemories" :disabled="loading" title="刷新">↻</button>
      </div>
    </div>

    <!-- 搜索栏（含显式【🔍 搜索】点击按钮与清空按钮） -->
    <div class="search-bar">
      <input
        v-model="searchQuery"
        class="search-input"
        placeholder="搜索记忆正文或操作系统/硬件..."
        @keydown.enter="doSearch"
      />
      <button class="search-btn" @click="doSearch" :disabled="searching" title="点击搜索">
        {{ searching ? '...' : '🔍 搜索' }}
      </button>
      <span v-if="searchQuery" class="clear-search" @click="clearSearch" title="清空搜索">×</span>
    </div>

    <div v-if="isSearchActive" class="search-status-bar">
      <span>检索结果 ({{ memories.length }})</span>
      <button class="reset-search-link" @click="clearSearch">返回全量列表</button>
    </div>

    <!-- 添加记忆表单 -->
    <div v-if="showAddForm" class="add-form">
      <div class="select-row">
        <select v-model="newType" class="type-select" title="记忆类型">
          <option v-for="mt in memoryTypes" :key="mt.value" :value="mt.value">{{ mt.label }}</option>
        </select>
        <select v-model="newNamespace" class="type-select ns-select" title="归属空间分组">
          <option v-for="ns in namespaceOptions" :key="ns.value" :value="ns.value">{{ ns.label }}</option>
        </select>
      </div>
      <input
        v-model="newContent"
        class="add-input"
        placeholder="输入记忆内容 (如: 偏好使用 Mac 电脑做开发)…"
        @keydown.enter="commitAdd"
        @keydown.escape="showAddForm = false"
      />
      <div class="add-actions">
        <button class="mem-act-btn confirm" @click="commitAdd" title="确认添加">✓ 确认添加</button>
        <button class="mem-act-btn cancel" @click="showAddForm = false" title="取消">取消</button>
      </div>
    </div>

    <div v-if="errorMessage && !loading && !searching" class="mem-error" @click="errorMessage = ''">
      ⚠️ {{ errorMessage }} (点击清除)
    </div>

    <div v-if="loading || searching" class="mem-empty">加载检索中…</div>

    <!-- 分组手风琴渲染（记忆列表由外层 RightPanel 的 overflow-y 统一接管） -->
    <div v-else-if="Object.keys(groupedMemories).length" class="mem-scroll-container">
      <div v-for="(group, key) in groupedMemories" :key="key" class="mem-group">
        <div class="group-header" @click="toggleGroup(key)">
          <span class="group-label">{{ group.label }} ({{ group.items.length }})</span>
          <span class="toggle-icon">{{ collapsedGroups[key] ? '►' : '▼' }}</span>
        </div>

        <div v-if="!collapsedGroups[key]" class="mem-list">
          <div v-for="m in group.items.slice(0, groupVisibleCount[key] || PAGE_SIZE_STEP)" :key="m.id" class="mem-item">
            <div class="mem-header">
              <div class="tag-row">
                <span class="mem-type">{{ typeLabels[m.type] || m.type }}</span>
                <span
                  class="ns-badge"
                  :class="m.namespace === 'shared_profile' || m.isUniversal ? 'universal' : m.namespace"
                >
                  {{ namespaceLabels[m.namespace] || (m.isUniversal ? '🌐 全局共享' : '☕ 日常') }}
                </span>
              </div>
              <span class="mem-conf">{{ (m.confidence * 100).toFixed(0) }}%</span>
            </div>

            <div v-if="editingId !== m.id" class="mem-content">{{ m.content }}</div>
            <input
              v-else
              v-model="editContent"
              class="mem-edit-input"
              @keydown.enter="commitEdit"
              @keydown.escape="cancelEdit"
              @blur="commitEdit"
            />

            <div class="mem-actions">
              <button v-if="editingId === m.id" class="mem-act-btn confirm" @click.stop="commitEdit">✓</button>
              <button v-if="editingId === m.id" class="mem-act-btn cancel" @click.stop="cancelEdit">×</button>
              <button v-if="editingId !== m.id" class="mem-act-btn" @click.stop="startEdit(m)" title="修改">✎</button>
              <button v-if="editingId !== m.id" class="mem-act-btn danger" @click.stop="deleteMemory(m.id)" title="删除">🗑</button>
            </div>
          </div>
          <!-- 渐进加载：默认显示 3 条，可点击展开全部或收起 -->
          <div
            v-if="(groupVisibleCount[key] || PAGE_SIZE_STEP) < group.items.length"
            class="expand-more"
            @click="loadMoreGroup(key)"
          >
            展开全部（共 {{ group.items.length }} 条）▾
          </div>
          <div
            v-else-if="group.items.length > PAGE_SIZE_STEP"
            class="expand-more collapse-btn"
            @click="collapseGroup(key)"
          >
            ▲ 收起（仅显示前 {{ PAGE_SIZE_STEP }} 条）
          </div>
        </div>
      </div>
    </div>
    <div v-else class="mem-empty">暂无匹配的记忆条目</div>
  </div>
</template>

<style scoped>
.memory-widget {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 12px;
  max-width: 100%;
}

.widget-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-secondary);
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.widget-actions {
  display: flex;
  gap: 2px;
}

.refresh-btn {
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 0 4px;
  border-radius: 4px;
}

.refresh-btn:hover { color: var(--accent); background: var(--accent-light); }

/* ── 带显式搜索按钮的搜索栏 ───────────────────── */
.search-bar {
  display: flex;
  gap: 4px;
  position: relative;
  margin-bottom: 8px;
}

.search-input {
  flex: 1;
  box-sizing: border-box;
  padding: 5px 20px 5px 8px;
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 11px;
  outline: none;
}

.search-input:focus { border-color: var(--accent); }

.search-btn {
  padding: 4px 8px;
  border: none;
  background: var(--accent);
  color: white;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}

.search-btn:hover {
  opacity: 0.9;
}

.clear-search {
  position: absolute;
  right: 56px;
  top: 4px;
  font-size: 12px;
  color: var(--text-muted);
  cursor: pointer;
}

.search-status-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 10px;
  color: var(--text-muted);
  margin-bottom: 6px;
  padding: 0 2px;
}

.reset-search-link {
  background: none;
  border: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 10px;
  text-decoration: underline;
}

/* ── 添加表单 ──────────────────────────────────── */
.add-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  background: var(--bg-surface-hover);
  border: 1px solid var(--border-accent);
  border-radius: 8px;
}

.select-row {
  display: flex;
  gap: 6px;
}

.type-select {
  flex: 1;
  padding: 4px 6px;
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 11px;
  outline: none;
}

.ns-select {
  flex: 1.2;
}

.add-input {
  width: 100%;
  box-sizing: border-box;
  padding: 4px 6px;
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 11px;
  outline: none;
}

.add-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}

/* ── 分组手风琴（不再内嵌 max-height 与滚动条，由父 RightPanel overflow-y 统一接管） ── */
.mem-scroll-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mem-group {
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  overflow: hidden;
}

.group-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  background: var(--bg-surface-hover);
  cursor: pointer;
  user-select: none;
}

.group-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-secondary);
}

.toggle-icon {
  font-size: 9px;
  color: var(--text-muted);
}

.mem-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px;
}

.mem-item {
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
}

.mem-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.tag-row {
  display: flex;
  gap: 4px;
  align-items: center;
}

.mem-type {
  font-size: 9px;
  font-weight: 700;
  color: var(--accent-strong);
  background: var(--accent-light);
  padding: 1px 4px;
  border-radius: 3px;
}

.ns-badge {
  font-size: 9px;
  font-weight: 600;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(128, 128, 128, 0.15);
  color: var(--text-muted);
}

.ns-badge.universal {
  background: rgba(0, 168, 255, 0.15);
  color: #0088cc;
}

.mem-conf {
  font-size: 9px;
  color: var(--text-muted);
}

.mem-content {
  font-size: 11px;
  color: var(--text-primary);
  line-height: 1.4;
  word-break: break-word;
}

.mem-edit-input {
  width: 100%;
  box-sizing: border-box;
  padding: 4px;
  border: 1px solid var(--accent);
  border-radius: 4px;
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 11px;
}

.mem-actions {
  display: flex;
  gap: 4px;
  margin-top: 4px;
  justify-content: flex-end;
}

.mem-act-btn {
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 3px;
}

.mem-act-btn.confirm {
  background: var(--accent);
  color: white;
}

.mem-act-btn.cancel {
  background: var(--bg-surface-hover);
}

.mem-act-btn:hover { color: var(--text-primary); background: var(--bg-surface-hover); }
.mem-act-btn.danger:hover { color: #cc3300; }

.mem-error {
  font-size: 10px;
  color: #cc3300;
  background: rgba(204, 51, 0, 0.08);
  border: 1px solid rgba(204, 51, 0, 0.25);
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 6px;
  cursor: pointer;
  word-break: break-all;
}

.expand-more {
  font-size: 10px;
  color: var(--text-muted);
  text-align: center;
  padding: 6px;
  cursor: pointer;
  border-top: 1px dashed var(--border-subtle);
  transition: color 0.15s;
}
.expand-more:hover {
  color: var(--text-primary);
}

.mem-empty {
  font-size: 11px;
  color: var(--text-muted);
  font-style: italic;
  text-align: center;
  padding: 12px;
}
</style>

<script setup lang="ts">
/** 提醒组件 — 全功能升级版：支持智能自然语言解析、预设快捷时间胶囊、动态倒计时与 Snooze 延时。 */
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useReminderScheduler } from '@/composables/useReminderScheduler'

const {
  items,
  pendingReminders,
  firedReminders,
  addReminder,
  removeReminder,
  dismissReminder,
  snoozeReminder,
} = useReminderScheduler()

const showAdd = ref(false)
const newText = ref('')
const selectedPresetMins = ref<number | null>(null)
const addInput = ref<HTMLInputElement | null>(null)
const nowTick = ref(Date.now())
let tickTimer: number | null = null
let fastTimer: number | null = null

/** 检查是否存在 < 5 分钟的提醒 */
function hasUrgentReminder(): boolean {
  const now = Date.now()
  return pendingReminders.value.some(r => {
    const diffMs = r.dueTimestamp - now
    return diffMs > 0 && diffMs < 5 * 60 * 1000
  })
}

onMounted(() => {
  // 实时刷新：每秒一次，保证所有倒计时（含 >5 分钟的提醒）都实时、精确到秒。
  // 原先的 10 秒慢模式会导致 15 分钟这类长提醒看起来"卡住再跳"且偏差最多 10 秒；
  // 单组件每秒刷新开销可忽略，故统一用 1 秒。
  tickTimer = window.setInterval(() => {
    nowTick.value = Date.now()
    // 有紧急提醒时切换到快速模式（视觉上无差别，保留以防后续扩展）
    if (hasUrgentReminder() && !fastTimer) {
      startFastTicker()
    }
  }, 1000)

  // 初次检查是否需要快速模式
  if (hasUrgentReminder()) {
    startFastTicker()
  }
})

function startFastTicker() {
  if (fastTimer) return
  // 快速模式：每秒更新，仅持续到紧急提醒全部到期
  fastTimer = window.setInterval(() => {
    nowTick.value = Date.now()
    if (!hasUrgentReminder()) {
      stopFastTicker()
    }
  }, 1000)
}

function stopFastTicker() {
  if (fastTimer) {
    clearInterval(fastTimer)
    fastTimer = null
  }
}

onUnmounted(() => {
  if (tickTimer) clearInterval(tickTimer)
  stopFastTicker()
})

function toggleAdd() {
  showAdd.value = !showAdd.value
  if (showAdd.value) {
    selectedPresetMins.value = null
    nextTick(() => {
      addInput.value?.focus()
    })
  }
}

function selectPreset(mins: number) {
  selectedPresetMins.value = mins
  if (!newText.value.trim()) {
    newText.value = `${mins}分钟后 `
  }
  addInput.value?.focus()
}

function submitAdd() {
  const text = newText.value.trim()
  if (!text) return

  if (selectedPresetMins.value) {
    addReminder(text.replace(/^\d+分钟后\s*/, ''), selectedPresetMins.value * 60 * 1000)
  } else {
    addReminder(text)
  }

  newText.value = ''
  selectedPresetMins.value = null
  showAdd.value = false
}

function formatCountdown(dueTimestamp: number): { text: string; isOverdue: boolean } {
  const diffMs = dueTimestamp - nowTick.value
  if (diffMs <= 0) {
    return { text: '到期!', isOverdue: true }
  }

  const totalSecs = Math.floor(diffMs / 1000)
  const hours = Math.floor(totalSecs / 3600)
  const mins = Math.floor((totalSecs % 3600) / 60)
  const secs = totalSecs % 60

  const pad = (n: number) => n.toString().padStart(2, '0')

  if (hours > 0) {
    return { text: `${hours}h ${pad(mins)}m`, isOverdue: false }
  }
  return { text: `${pad(mins)}:${pad(secs)}`, isOverdue: false }
}

const icons = ['🔔', '📩', '🔋', '📅', '💡', '⏰', '⚡', '✅']
function getIcon(idx: number) {
  return icons[idx % icons.length]
}
</script>

<template>
  <div class="card">
    <div class="card-header">
      <div class="title-group">
        <span>🔔 提醒</span>
        <span v-if="firedReminders.length" class="badge-fired">
          {{ firedReminders.length }} 到期
        </span>
      </div>
      <button class="add-btn" :class="{ active: showAdd }" @click="toggleAdd" title="添加提醒">＋</button>
    </div>

    <!-- 添加提醒区域 -->
    <div v-if="showAdd" class="add-section">
      <div class="quick-pills">
        <button
          class="pill-btn"
          :class="{ active: selectedPresetMins === 5 }"
          @click="selectPreset(5)"
        >+5分钟</button>
        <button
          class="pill-btn"
          :class="{ active: selectedPresetMins === 30 }"
          @click="selectPreset(30)"
        >+30分钟</button>
        <button
          class="pill-btn"
          :class="{ active: selectedPresetMins === 60 }"
          @click="selectPreset(60)"
        >+1小时</button>
      </div>

      <div class="add-row">
        <input
          ref="addInput"
          v-model="newText"
          class="add-input"
          placeholder="例如: 10分钟后拿快递..."
          @keydown.enter="submitAdd"
          @keydown.escape="showAdd = false"
        />
        <button class="ok-btn" title="确认添加" @click="submitAdd">✓</button>
      </div>
    </div>

    <!-- 提醒列表 -->
    <div v-if="items.length" class="list-container">
      <!-- 1. 已到期的提醒 (Fired Alerts) -->
      <div v-if="firedReminders.length" class="fired-group">
        <div
          v-for="r in firedReminders"
          :key="r.id"
          class="item fired-item"
        >
          <span class="pulse-bell">🔔</span>
          <div class="item-content">
            <span class="text fired-text">{{ r.text }}</span>
            <span class="overdue-tag">时间到!</span>
          </div>
          <div class="act-btns">
            <button class="act-btn snooze" title="稍后5分钟提醒" @click="snoozeReminder(r.id, 5)">+5分</button>
            <button class="act-btn dismiss" title="知道了" @click="dismissReminder(r.id)">✓</button>
            <button class="act-btn del" title="删除" @click="removeReminder(r.id, r.fromApi)">×</button>
          </div>
        </div>
      </div>

      <!-- 2. 待触发与已归档提醒 -->
      <ul class="list">
        <li
          v-for="(r, idx) in pendingReminders"
          :key="r.id"
          class="item pending-item"
        >
          <span class="icon">{{ getIcon(idx) }}</span>
          <span class="text">{{ r.text }}</span>
          <span class="countdown-badge">
            ⏱️ {{ formatCountdown(r.dueTimestamp).text }}
          </span>
          <button class="del" title="删除" @click="removeReminder(r.id, r.fromApi)">×</button>
        </li>
      </ul>
    </div>

    <div v-else class="empty">✓ 暂无提醒 (可点击＋快捷设置)</div>
  </div>
</template>

<style scoped>
.card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.title-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.badge-fired {
  background: #ff4400;
  color: #fff;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: bold;
  animation: pulse-badge 1.5s infinite;
}

@keyframes pulse-badge {
  0% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.1); opacity: 0.8; }
  100% { transform: scale(1); opacity: 1; }
}

.add-btn {
  border: 1px solid var(--border-subtle);
  background: var(--bg-input);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 14px;
  font-weight: bold;
  padding: 2px 6px;
  line-height: 1;
  border-radius: 4px;
  min-width: 24px;
  min-height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition-fast);
}
.add-btn:hover,
.add-btn.active {
  color: var(--accent-strong);
  background: var(--accent-light);
  border-color: var(--border-accent);
}

/* 添加区域 */
.add-section {
  margin-bottom: 10px;
  background: var(--bg-surface-hover);
  padding: 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-subtle);
}

.quick-pills {
  display: flex;
  gap: 4px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.pill-btn {
  border: 1px solid var(--border-subtle);
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 10px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.pill-btn:hover,
.pill-btn.active {
  border-color: var(--accent);
  color: var(--accent-strong);
  background: var(--accent-light);
}

.add-row {
  display: flex;
  gap: 4px;
}

.add-input {
  flex: 1;
  min-width: 0;
  padding: 4px 8px;
  border: 1px solid var(--border-accent);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
}

.ok-btn {
  border: none;
  background: var(--accent);
  color: var(--text-on-accent);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  font-weight: bold;
}

/* 列表容器 */
.list-container {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 200px;
  overflow-y: auto;
  padding-right: 2px;
}

.fired-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 4px;
}

.fired-item {
  background: rgba(255, 68, 0, 0.12);
  border: 1px solid rgba(255, 68, 0, 0.4);
  padding: 6px 8px;
  border-radius: var(--radius-sm);
}

.pulse-bell {
  font-size: 15px;
  animation: ring-bell 1s infinite alternate ease-in-out;
}

@keyframes ring-bell {
  0% { transform: rotate(-15deg); }
  100% { transform: rotate(15deg); }
}

.item-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.fired-text {
  font-weight: 600;
  color: #ff4400;
}

.overdue-tag {
  font-size: 10px;
  color: #ff6633;
}

.act-btns {
  display: flex;
  align-items: center;
  gap: 4px;
}

.act-btn {
  border: none;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
  font-weight: bold;
  transition: all var(--transition-fast);
}

.act-btn.snooze {
  background: rgba(255, 170, 0, 0.2);
  color: #d97706;
}

.act-btn.snooze:hover {
  background: #d97706;
  color: #fff;
}

.act-btn.dismiss {
  background: var(--accent);
  color: #fff;
}

.act-btn.del {
  background: none;
  color: var(--text-muted);
  font-size: 14px;
}

.act-btn.del:hover {
  color: #cc3300;
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pending-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-primary);
  padding: 4px 6px;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast);
}

.pending-item:hover {
  background: var(--bg-surface-hover);
}

.icon {
  font-size: 14px;
  flex-shrink: 0;
}

.text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.countdown-badge {
  font-size: 11px;
  color: var(--accent-strong);
  background: var(--accent-light);
  padding: 1px 6px;
  border-radius: 10px;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.del {
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 14px;
  padding: 0 4px;
  border-radius: 4px;
  opacity: 0;
  transition: opacity var(--transition-fast), color var(--transition-fast);
}

.pending-item:hover .del {
  opacity: 1;
}

.del:hover {
  color: #cc3300;
  background: rgba(204, 51, 0, 0.12);
}

.empty {
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
  padding: 8px 0;
}
</style>

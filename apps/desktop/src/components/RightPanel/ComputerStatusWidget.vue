<script setup lang="ts">
/** 电脑本地状态卡（A3）— 直接读本机 sensor 采集器落盘的 computer_sensor_state.json（不走 hub）。
 *  60s 自动刷新 + 手动刷新按钮；读不到（sensor 未运行/未装）显示「本地监测未运行」。
 *
 *  传感器健康不在此展示（统一放服务器状态卡）；手机端状态待接入（预留：future `phone-state`
 *  数据源，接口结构与这里一致，卡片内留空位即可）。 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { invoke } from '@tauri-apps/api/core'

interface SensorState {
  category?: string
  game?: string | null
  video?: string | null
  sitting_minutes?: number
  last_at?: number
  sitting_last_active_at?: number
  error?: string | null
  fail_streak?: number
}

const state = ref<SensorState | null>(null)
const error = ref('')
const lastTs = ref(0)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
let refreshVersion = 0

const CAT_LABELS: Record<string, string> = {
  coding: '写代码',
  browsing: '浏览网页',
  communication: '通讯聊天',
  game: '游戏',
  video: '看视频',
  document: '文档处理',
  meeting: '会议',
  idle: '空闲',
  unknown: '使用中',
  tool: '工具',
}

const label = computed(() => CAT_LABELS[state.value?.category ?? ''] ?? state.value?.category ?? '—')
const stale = computed(() => {
  if (!state.value?.last_at) return false
  return Date.now() / 1000 - state.value.last_at > 5 * 60
})
const sitting = computed(() => {
  const s = state.value?.sitting_minutes
  return typeof s === 'number' && s >= 1 ? Math.round(s) : 0
})
// 空闲时长：上次活动时间距今（近似；sitting_last_active_at 是坐姿活跃时刻）
const idleMin = computed(() => {
  const t = state.value?.sitting_last_active_at
  if (!t) return 0
  return Math.max(0, Math.round((Date.now() / 1000 - t) / 60))
})
const lastAtAgo = computed(() => {
  const t = state.value?.last_at
  if (!t) return ''
  const min = Math.max(0, Math.round((Date.now() / 1000 - t) / 60))
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  return `${Math.round(min / 60)} 小时前`
})

async function refresh() {
  const myVersion = ++refreshVersion
  loading.value = true
  try {
    const raw = await invoke<string>('read_sensor_state')
    if (myVersion !== refreshVersion) return
    state.value = raw ? JSON.parse(raw) : null
    error.value = ''
    lastTs.value = Date.now()
  } catch {
    if (myVersion === refreshVersion) error.value = '本地监测未运行'
  } finally {
    if (myVersion === refreshVersion) loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(refresh, 60000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="computer-card">
    <div class="head">
      <span class="title">🖥 电脑状态</span>
      <button class="refresh-btn" :disabled="loading" title="刷新" @click="refresh">⟳</button>
    </div>

    <div v-if="error" class="unavailable">{{ error }}</div>

    <template v-else-if="state">
      <div class="main-row">
        <span class="cat">{{ label }}</span>
        <span v-if="state.game" class="game">《{{ state.game }}》</span>
        <span v-if="stale" class="stale-mark">离开/休眠</span>
      </div>
      <div v-if="state.video" class="video">🎬 {{ state.video }}</div>

      <ul class="details">
        <li v-if="sitting >= 1">
          <span class="k">久坐</span>
          <span class="v" :class="{ warn: sitting >= 30 }">{{ sitting }} 分钟</span>
        </li>
        <li>
          <span class="k">空闲</span>
          <span class="v">{{ idleMin }} 分钟</span>
        </li>
        <li>
          <span class="k">数据</span>
          <span class="v">{{ lastAtAgo }}</span>
        </li>
        <!-- 手机端状态：预留接口位（future phone-state） -->
      </ul>

      <div class="foot">更新于 {{ lastTs ? new Date(lastTs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }}</div>
    </template>

    <div v-else class="unavailable">暂未获取到本地状态</div>
  </div>
</template>

<style scoped>
.computer-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  font-size: 12px;
  color: var(--text-secondary);
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary);
}
.refresh-btn {
  background: none;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  padding: 2px 6px;
}
.refresh-btn:hover {
  color: var(--accent-strong);
  border-color: var(--border-accent);
}
.refresh-btn:disabled { opacity: 0.5; cursor: default; }
.unavailable {
  font-size: 11px;
  color: var(--text-muted);
  padding: 6px 0;
}
.main-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.cat {
  font-size: 14px;
  font-weight: 700;
  color: var(--accent-strong);
}
.game {
  font-size: 12px;
  color: var(--text-primary);
}
.stale-mark {
  font-size: 10px;
  color: var(--warn, #c07a1f);
  border: 1px solid var(--warn, #c07a1f);
  border-radius: 8px;
  padding: 0 6px;
}
.video {
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 4px;
}
.details {
  list-style: none;
  margin: 0;
  padding: 0;
}
.details li {
  display: flex;
  justify-content: space-between;
  padding: 2px 0;
  border-bottom: 1px dashed var(--border-subtle);
}
.details li:last-child {
  border-bottom: none;
}
.k {
  color: var(--text-tertiary);
}
.v.warn {
  color: var(--warn, #c07a1f);
  font-weight: 600;
}
.v.ok {
  color: var(--ok, #2e9e5b);
}
.v.bad {
  color: #c0392b;
}
.foot {
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-tertiary);
}
</style>

<script setup lang="ts">
/** 电脑本地状态卡（A3）— 直接读本机 sensor 采集器落盘的 computer_sensor_state.json（不走 hub）。
 *  60s 自动刷新；读不到（sensor 未运行/未装）显示「本地监测未运行」。 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { invoke } from '@tauri-apps/api/core'

interface SensorState {
  category?: string
  game?: string | null
  video?: string | null
  sitting_minutes?: number
  last_at?: number
  error?: string | null
}

const state = ref<SensorState | null>(null)
const error = ref('')
const lastTs = ref(0)
let timer: ReturnType<typeof setInterval> | null = null

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

async function refresh() {
  try {
    const raw = await invoke<string>('read_sensor_state')
    state.value = raw ? JSON.parse(raw) : null
    error.value = ''
    lastTs.value = Date.now()
  } catch {
    error.value = '本地监测未运行'
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
  <div class="card compact computer-card">
    <div class="card-head">
      <span class="card-title">🖥 电脑状态</span>
      <span class="card-sub">{{ stale ? '离开/休眠' : (state ? '本地' : '') }}</span>
    </div>

    <div v-if="error" class="empty">{{ error }}</div>
    <div v-else-if="!state" class="empty">暂未获取到本地状态</div>
    <div v-else class="body">
      <div class="row main">
        <span class="cat">{{ label }}</span>
        <span v-if="state.game" class="game">《{{ state.game }}》</span>
        <span v-if="state.video" class="video">🎬 {{ state.video }}</span>
      </div>
      <div class="row sub">
        <span v-if="sitting >= 30" class="sit">已坐 {{ sitting }} 分钟</span>
        <span v-else-if="sitting > 0" class="sit">坐 {{ sitting }} 分钟</span>
        <span v-if="lastTs" class="ts">更新于 {{ new Date(lastTs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.computer-card .card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 6px;
}
.card-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary);
}
.card-sub {
  font-size: 10px;
  color: var(--text-tertiary);
}
.empty {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 4px 0;
}
.body .row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.row.main {
  margin-bottom: 4px;
}
.cat {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-strong);
}
.game {
  font-size: 12px;
  color: var(--text-primary);
}
.video {
  font-size: 11px;
  color: var(--text-secondary);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row.sub {
  font-size: 11px;
  color: var(--text-secondary);
}
.sit {
  color: var(--warn, #c07a1f);
}
.ts {
  color: var(--text-tertiary);
}
</style>

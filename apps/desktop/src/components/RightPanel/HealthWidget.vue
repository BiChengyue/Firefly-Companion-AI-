<script setup lang="ts">
/** 健康数据小组件（T-31-A2）— 从 bus /api/v1/fitness(+history) 拉取健康数据。
 *  30s 自动刷新 + 手动刷新；异步加载不阻塞；整体失败显示「健康数据暂不可用」。 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getFitness, getFitnessHistory, type FitnessDaily, type FitnessHistory } from '@/services/api'

const fitness = ref<FitnessDaily | null>(null)
const history = ref<FitnessHistory | null>(null)
const error = ref('')
const lastTs = ref(0)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
let refreshVersion = 0

onMounted(async () => {
  await refresh()
  timer = setInterval(refresh, 30000) // 30s 自动刷新
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function refresh() {
  const myVersion = ++refreshVersion
  loading.value = true
  try {
    // 最新 + 近 7 天趋势并行拉取（任一失败整体降级，不阻塞主界面）
    const [f, h] = await Promise.all([getFitness(), getFitnessHistory(7)])
    if (myVersion === refreshVersion) {
      fitness.value = f
      history.value = h
      error.value = ''
      lastTs.value = Date.now()
    }
  } catch {
    // 仅当此调用仍是最新版本时显示失败（防旧请求覆盖新数据）
    if (myVersion === refreshVersion) error.value = '健康数据暂不可用'
  } finally {
    if (myVersion === refreshVersion) loading.value = false
  }
}

/** 睡眠时长 → "7h0m"；缺失 → null（显示「—」）。 */
const sleepText = computed<string | null>(() => {
  const s = fitness.value?.sleep
  if (!s || s.secs == null) return null
  const h = Math.floor(s.secs / 3600)
  const m = Math.round((s.secs % 3600) / 60)
  return `${h}h${m ? `${m}m` : ''}`
})

const sleepScore = computed<number | null>(() => fitness.value?.sleep?.score ?? null)
const restingHr = computed<number | null>(() => fitness.value?.resting_hr ?? null)

/** 近 7 天步数趋势（升序、取最近 7 天；步数缺失的天显示空条）。 */
const trendDays = computed(() => {
  const days = history.value?.history ?? []
  const sorted = [...days].sort((a, b) => (a.date < b.date ? -1 : 1)).slice(-7)
  const max = Math.max(1, ...sorted.map((d) => d.steps ?? 0))
  return sorted.map((d) => ({
    date: d.date.slice(5), // MM-DD
    steps: d.steps ?? null,
    pct: d.steps != null ? Math.max(4, Math.round(((d.steps ?? 0) / max) * 100)) : 0,
  }))
})

const stepsText = computed<string>(() => {
  const s = fitness.value?.steps
  return s != null ? Number(s).toLocaleString() : '—'
})
</script>

<template>
  <div class="health-card">
    <div class="head">
      <span class="title">健康数据</span>
      <button class="refresh-btn" :disabled="loading" title="刷新" @click="refresh">⟳</button>
    </div>

    <div v-if="error" class="unavailable">{{ error }}</div>

    <template v-else-if="fitness">
      <div class="date-row">
        数据日期 {{ fitness.date || '—' }}
      </div>

      <div class="metrics">
        <div class="metric steps">
          <span class="m-label">步数</span>
          <span class="m-big">{{ stepsText }}</span>
        </div>
        <div class="metric">
          <span class="m-label">睡眠</span>
          <span class="m-val">{{ sleepText ?? '—' }}<template v-if="sleepScore != null"> · 评分 {{ sleepScore }}</template></span>
        </div>
        <div class="metric">
          <span class="m-label">静息心率</span>
          <span class="m-val">{{ restingHr != null ? `${restingHr} bpm` : '—' }}</span>
        </div>
      </div>

      <div v-if="trendDays.length" class="trend">
        <div class="trend-title">近 {{ trendDays.length }} 天步数</div>
        <div class="bars">
          <div v-for="d in trendDays" :key="d.date" class="bar-col" :title="`${d.date}：${d.steps ?? '—'}`">
            <div class="bar-track">
              <div class="bar-fill" :style="{ height: d.pct + '%' }" :class="{ none: d.steps == null }" />
            </div>
            <span class="bar-date">{{ d.date }}</span>
          </div>
        </div>
      </div>

      <div v-if="fitness.summary" class="summary">{{ fitness.summary }}</div>

      <div class="foot">
        更新于 {{ lastTs ? new Date(lastTs).toLocaleTimeString() : '—' }}
      </div>
    </template>

    <div v-else class="unavailable">加载中…</div>
  </div>
</template>

<style scoped>
.health-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
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
.refresh-btn:disabled { opacity: 0.5; cursor: default; }
.unavailable {
  font-size: 11px;
  color: var(--text-muted);
  padding: 6px 0;
}
.date-row {
  font-size: 10px;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-bottom: 10px;
}
.metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.m-label {
  font-size: 9px;
  color: var(--text-muted);
}
.m-big {
  font-size: 20px;
  font-weight: 800;
  color: var(--text-primary);
  font-family: 'Courier New', monospace;
}
.m-val {
  font-size: 11px;
  color: var(--text-secondary);
}
.trend-title {
  font-size: 9px;
  color: var(--text-muted);
  margin-bottom: 4px;
}
.bars {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 56px;
  margin-bottom: 4px;
}
.bar-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-width: 0;
}
.bar-track {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  background: var(--bg-surface);
  border-radius: 2px;
  overflow: hidden;
}
.bar-fill {
  width: 100%;
  background: var(--accent);
  border-radius: 2px;
  transition: height 0.4s;
}
.bar-fill.none { background: transparent; }
.bar-date {
  font-size: 8px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
.summary {
  font-size: 10px;
  color: var(--text-secondary);
  margin-top: 6px;
}
.foot {
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
  margin-top: 6px;
}
</style>

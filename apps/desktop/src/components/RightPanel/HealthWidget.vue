<script setup lang="ts">
/** 健康数据小组件（T-31-A2 + T32 布局）— 从 bus /api/v1/fitness(+history) 拉取健康数据。
 *  T32：2 列 grid（🚶步数｜💓心率 / 😴睡眠｜📈近7天步数 SVG 折线图）；移除 summary 简报；
 *  心率/睡眠缺失显示「—」。30s 自动刷新 + 手动刷新；异步加载不阻塞。 */
import { ref, computed, onMounted } from 'vue'
import { useSyncRefresh } from '@/composables/useSyncRefresh'
import { getFitness, getFitnessHistory, type FitnessDaily, type FitnessHistory } from '@/services/api'

const fitness = ref<FitnessDaily | null>(null)
const history = ref<FitnessHistory | null>(null)
const error = ref('')
const lastTs = ref(0)
const loading = ref(false)
let refreshVersion = 0

useSyncRefresh(refresh, 30000) // 30s 同步刷新线（setup 顶层订阅）

onMounted(async () => {
  await refresh()
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

/** 睡眠时长 → "7h23m"；缺失 → null（显示「—」）。 */
const sleepText = computed<string | null>(() => {
  const s = fitness.value?.sleep
  if (!s || s.secs == null) return null
  const h = Math.floor(s.secs / 3600)
  const m = Math.round((s.secs % 3600) / 60)
  return `${h}h${m ? `${m}m` : ''}`
})

const sleepScore = computed<number | null>(() => fitness.value?.sleep?.score ?? null)

/** 睡眠评分评级色（T33：只留数字+配色——≥85 绿 / 70-84 黄 / <70 红） */
const scoreColor = computed<string>(() => {
  const s = fitness.value?.sleep?.score
  if (s == null) return 'var(--text-secondary)'
  if (s >= 85) return '#4caf50'
  if (s >= 70) return '#f5a623'
  return '#ef4444'
})
const restingHr = computed<number | null>(() => fitness.value?.resting_hr ?? null)
const hrText = computed<string>(() => (restingHr.value != null ? `${restingHr.value} bpm` : '—'))

const stepsText = computed<string>(() => {
  const s = fitness.value?.steps
  return s != null ? Number(s).toLocaleString() : '—'
})

/** 2026-08-08：步数健康分级色（≥8000 绿 / ≥5000 黄 / <5000 红）——与评分同机制 */
const stepsColor = computed<string>(() => {
  const v = fitness.value?.steps
  if (v == null) return 'var(--text-primary)'
  if (Number(v) >= 8000) return '#22c55e'
  if (Number(v) >= 5000) return '#eab308'
  return '#ef4444'
})

/** 2026-08-08：静息心率分级色（55-85 绿 / 50-95 黄 / 其余红） */
const hrColor = computed<string>(() => {
  const v = restingHr.value
  if (v == null) return 'var(--text-primary)'
  if (v >= 55 && v <= 85) return '#22c55e'
  if (v >= 50 && v <= 95) return '#eab308'
  return '#ef4444'
})

/** 2026-08-08：睡眠时长分级色（7-9h 绿 / ≥6h 黄 / <6h 红，成人推荐） */
const sleepColor = computed<string>(() => {
  const s = fitness.value?.sleep?.secs
  if (s == null) return 'var(--text-primary)'
  const h = s / 3600
  if (h >= 7 && h <= 9) return '#22c55e'
  if (h >= 6) return '#eab308'
  return '#ef4444'
})

// ── T32：近 7 天步数 SVG 折线图（手绘 polyline，不引图表库；缺失天跳过）──
const SVG_W = 120
const SVG_H = 42

const trendPts = computed(() => {
  const days = (history.value?.history ?? []) // 数据在 history（days 是请求参数数字——AI-4 误改回 .days 会 TypeError）
    .filter((d) => d.steps != null) // 步数缺失的天跳过
    .sort((a, b) => (a.date < b.date ? -1 : 1))
    .slice(-7)
  if (!days.length) return []
  const nums = days.map((d) => Number(d.steps))
  const max = Math.max(...nums)
  const min = Math.min(...nums)
  const range = max - min || 1
  return days.map((d, i) => {
    const x = days.length === 1 ? SVG_W / 2 : (i / (days.length - 1)) * SVG_W
    const y = SVG_H - 4 - ((Number(d.steps) - min) / range) * (SVG_H - 8)
    return { x, y, date: d.date.slice(5), steps: Number(d.steps) }
  })
})

const polyPoints = computed(() =>
  trendPts.value.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '),
)

// 2026-08-08：趋势面积路径（折线下渐变填充）
const areaPath = computed(() => {
  const pts = trendPts.value
  if (!pts.length) return ''
  const first = pts[0]
  const last = pts[pts.length - 1]
  return `${polyPoints.value} L ${last.x.toFixed(1)},${SVG_H} L ${first.x.toFixed(1)},${SVG_H} Z`
})

// ── T33：hover 查看具体数据（鼠标移到折线附近 → 显示日期+步数）──
const hoverIdx = ref(-1)
const hoverPoint = computed(() =>
  hoverIdx.value >= 0 && hoverIdx.value < trendPts.value.length ? trendPts.value[hoverIdx.value] : null,
)

/** SVG 鼠标移动 → 换算 viewBox 坐标 → 找最近数据点（preserveAspectRatio=none 需按实际尺寸缩放） */
function onSvgMove(e: MouseEvent) {
  const svg = e.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const px = ((e.clientX - rect.left) / rect.width) * SVG_W
  let best = -1
  let bestDist = Infinity
  trendPts.value.forEach((p, i) => {
    const d = Math.abs(p.x - px)
    if (d < bestDist) {
      bestDist = d
      best = i
    }
  })
  hoverIdx.value = best
}
</script>

<template>
  <div class="health-card">
    <div class="head">
      <span class="title">💚 健康数据</span>
      <button class="refresh-btn" :disabled="loading" title="刷新" @click="refresh">⟳</button>
    </div>

    <div v-if="error" class="unavailable">{{ error }}</div>

    <template v-else-if="fitness">
      <div class="date-row">
        数据日期 {{ fitness.date || '—' }}
      </div>

      <!-- T32：2 列 grid：🚶步数｜💓心率 / 😴睡眠｜📈趋势（SVG 折线图） -->
      <div class="grid2">
        <div class="cell">
          <span class="c-label">🚶 步数</span>
          <span class="c-big" :style="{ color: stepsColor }">{{ stepsText }}</span>
        </div>
        <div class="cell">
          <span class="c-label">💓 心率</span>
          <span class="c-big" :style="{ color: hrColor }">{{ hrText }}</span>
        </div>
        <div class="cell">
          <span class="c-label">😴 睡眠</span>
          <!-- 2026-08-08：取消评分分行——合并为 时长|评分 单行（分隔符弱色，不跟两边同色） -->
          <span class="c-big"><span :style="{ color: sleepColor }">{{ sleepText ?? '—' }}</span><span class="sleep-sep">|</span><span :style="sleepScore != null ? { color: scoreColor } : {}">{{ sleepScore ?? '--' }}</span></span>
        </div>
        <div class="cell trend-cell">
          <span class="c-label">📈 近 7 天步数</span>
          <div class="chart-wrap">
            <svg
              v-if="trendPts.length"
              class="sparkline"
              :viewBox="`0 0 ${SVG_W} ${SVG_H}`"
              preserveAspectRatio="none"
              @mousemove="onSvgMove"
              @mouseleave="hoverIdx = -1"
            >
              <defs>
                <!-- 2026-08-08：趋势彩色化——折线渐变 + 面积渐变 -->
                <linearGradient id="trendLine" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stop-color="var(--accent)" />
                  <stop offset="100%" stop-color="#22d3ee" />
                </linearGradient>
                <linearGradient id="trendArea" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.28" />
                  <stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />
                </linearGradient>
              </defs>
              <path :d="areaPath" fill="url(#trendArea)" />
              <polyline
                :points="polyPoints"
                fill="none"
                stroke="url(#trendLine)"
                stroke-width="1.6"
                stroke-linejoin="round"
                stroke-linecap="round"
              />
              <circle
                v-for="(p, i) in trendPts"
                :key="p.date"
                :cx="p.x"
                :cy="p.y"
                :r="i === trendPts.length - 1 ? 3 : 1.8"
                :fill="i === trendPts.length - 1 ? '#22d3ee' : 'var(--accent)'"
                :stroke="i === trendPts.length - 1 ? '#fff' : 'none'"
                stroke-width="1"
              />
              <circle
                v-if="hoverPoint"
                :cx="hoverPoint.x"
                :cy="hoverPoint.y"
                r="3"
                fill="var(--accent)"
                stroke="#fff"
                stroke-width="1"
              />
            </svg>
            <div v-else class="trend-empty">暂无趋势</div>
            <div v-if="hoverPoint" class="chart-tip">
              {{ hoverPoint.date }} · {{ hoverPoint.steps.toLocaleString() }} 步
            </div>
          </div>
        </div>
      </div>

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
/* T32：2 列 grid 布局 */
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 10px;
}
.cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  align-items: center; /* T33：数据居中 */
  text-align: center;
}
.c-label {
  font-size: 10px;      /* 2026-08-08：步数/心率/睡眠/趋势标题稍拉大（原 9px） */
  color: var(--text-muted);
  white-space: nowrap;
}
.c-big {
  font-size: 20px;
  font-weight: 800;
  color: var(--text-primary);
  font-family: 'Courier New', monospace;
  white-space: nowrap;
}
/* 2026-08-08：睡眠 时长|评分 的分隔符——弱色，不与两侧同色 */
.sleep-sep {
  color: var(--text-tertiary);
  font-weight: 400;
  margin: 0 1px;
}
.c-val {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}
.c-sub {
  /* T33：睡眠评分第二行——字号调大匹配其他数据 */
  font-size: 18px;
  font-weight: 700;
  color: var(--text-secondary);
  white-space: nowrap;
}
.trend-cell {
  /* 第四格：📈 折线图（2×2 grid 右下角） */
}
.sparkline {
  width: 100%;
  height: 42px;
  background: var(--bg-surface);
  border-radius: 4px;
  padding: 2px;
  box-sizing: border-box;
}
.chart-wrap {
  /* T33：hover tooltip 定位容器 */
  position: relative;
  width: 100%;
}
.chart-tip {
  position: absolute;
  top: -2px;
  left: 50%;
  transform: translate(-50%, -100%);
  background: var(--bg-solid);
  border: 1px solid var(--border-main);
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 10px;
  color: var(--text-primary);
  white-space: nowrap;
  pointer-events: none;
  z-index: 5;
}
.trend-empty {
  font-size: 10px;
  color: var(--text-muted);
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-surface);
  border-radius: 4px;
}
.foot {
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
  margin-top: 6px;
}
</style>

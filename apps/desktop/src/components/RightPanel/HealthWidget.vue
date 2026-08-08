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

// ── T32/T38：近 7 天趋势 SVG 折线图（可切换纵轴指标；缺失值前向填充+默认值绘图）──
const SVG_W = 120
const SVG_H = 42
const PAD_X = 6

type MetricKey = 'steps' | 'sleepSecs' | 'sleepScore' | 'restingHr' | 'weight'

interface MetricDef {
  label: string
  unit: string
  def: number // 全无数据时的绘图默认值
  pick: (d: FitnessDaily) => number | null
  grade: (v: number, all: number[]) => string
  range: (vals: number[]) => [number, number]
}

const METRICS: Record<MetricKey, MetricDef> = {
  steps: {
    label: '近 7 天步数', unit: '步', def: 6000,
    pick: (d) => (d.steps != null ? Number(d.steps) : null),
    grade: (v) => (v >= 8000 ? '#22c55e' : v >= 5000 ? '#eab308' : '#ef4444'),
    range: (vals) => [0, Math.max(...vals, 1)],
  },
  sleepSecs: {
    label: '近 7 天睡眠时长', unit: '小时', def: 7 * 3600,
    pick: (d) => d.sleep?.secs ?? null,
    grade: (v) => { const h = v / 3600; return h >= 7 && h <= 9 ? '#22c55e' : h >= 6 ? '#eab308' : '#ef4444' },
    range: () => [0, 12 * 3600],
  },
  sleepScore: {
    label: '近 7 天睡眠得分', unit: '分', def: 80,
    pick: (d) => d.sleep?.score ?? null,
    grade: (v) => (v >= 85 ? '#22c55e' : v >= 70 ? '#eab308' : '#ef4444'),
    range: () => [0, 100],
  },
  restingHr: {
    label: '近 7 天静息心率', unit: 'bpm', def: 70,
    pick: (d) => d.resting_hr ?? null,
    grade: (v) => (v >= 55 && v <= 85 ? '#22c55e' : v >= 50 && v <= 95 ? '#eab308' : '#ef4444'),
    range: () => [40, 120],
  },
  weight: {
    label: '近 7 天体重', unit: 'kg', def: 90, // 180 斤
    pick: (d) => d.weight ?? null,
    grade: (v, all) => {
      const sorted = all.slice().sort((a, b) => a - b)
      const mid = sorted.length ? sorted[Math.floor(sorted.length / 2)] : v
      const d = Math.abs(v - mid) / (mid || 1)
      return d <= 0.05 ? '#22c55e' : d <= 0.1 ? '#eab308' : '#ef4444'
    },
    range: (vals) => {
      const mn = Math.min(...vals); const mx = Math.max(...vals)
      const pad = (mx - mn) * 0.15 || 2
      return [Math.max(0, mn - pad), mx + pad]
    },
  },
}

const selectedMetric = ref<MetricKey>('steps')
const metric = computed(() => METRICS[selectedMetric.value])

/** 近 7 天数据（按日期排序，缺失天保留——绘图用前向填充/默认值） */
const trendDays = computed(() =>
  (history.value?.history ?? []).sort((a, b) => (a.date < b.date ? -1 : 1)).slice(-7),
)

/** 绘图数值：前向填充（无值用前一天值）；开头缺/全缺用默认值；raw 保留原始（数据栏显示不可用） */
const trendVals = computed(() => {
  const days = trendDays.value
  const m = metric.value
  const raw = days.map((d) => m.pick(d))
  let prev: number | null = null
  const plot = raw.map((v) => {
    if (v != null) { prev = v; return v }
    return prev != null ? prev : m.def
  })
  return { raw, plot }
})

/** 按尺寸构建点（y 用所选指标范围；点色按指标分级） */
function buildPts(days: FitnessDaily[], vals: number[], W: number, H: number, PAD: number) {
  const [lo, hi] = metric.value.range(vals)
  const range = hi - lo || 1
  const all = vals
  return days.map((d, i) => {
    const x = days.length === 1 ? W / 2 : PAD + (i / (days.length - 1)) * (W - PAD * 2)
    const y = H - 4 - ((vals[i] - lo) / range) * (H - 8)
    return { x, y, date: d.date.slice(5), rawDay: d, val: vals[i], color: metric.value.grade(vals[i], all) }
  })
}
const trendPts = computed(() => buildPts(trendDays.value, trendVals.value.plot, SVG_W, SVG_H, PAD_X))

/** 折线分段（相邻点每段一个线性渐变，从起点色过渡到终点色） */
function segsFrom(pts: Array<{ x: number; y: number; color: string }>, prefix: string) {
  const segs: Array<{ id: string; x1: number; y1: number; x2: number; y2: number; c1: string; c2: string }> = []
  for (let i = 0; i < pts.length - 1; i++) {
    segs.push({
      id: `${prefix}-${i}`,
      x1: pts[i].x, y1: pts[i].y, x2: pts[i + 1].x, y2: pts[i + 1].y,
      c1: pts[i].color, c2: pts[i + 1].color,
    })
  }
  return segs
}
const segColors = computed(() => segsFrom(trendPts.value, 'tg'))

// ── 2026-08-08：趋势大图（点击小图展开，卡片下部显示；右上角纵轴选择器）──
const showTrendLarge = ref(false)
const SVG_LG_W = 300
const SVG_LG_H = 160
const PAD_LG = 26 // 大图左侧轴区留白（纵轴刻度不遮挡点）
const trendPtsLg = computed(() => buildPts(trendDays.value, trendVals.value.plot, SVG_LG_W, SVG_LG_H, PAD_LG))
const segColorsLg = computed(() => segsFrom(trendPtsLg.value, 'tglg'))
const areaPath = computed(() => {
  const pts = trendPts.value
  if (!pts.length) return ''
  const first = pts[0]; const last = pts[pts.length - 1]
  return `${pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')} L ${last.x.toFixed(1)},${SVG_H} L ${first.x.toFixed(1)},${SVG_H} Z`
})
const areaPathLg = computed(() => {
  const pts = trendPtsLg.value
  if (!pts.length) return ''
  return `${pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')} L ${pts[pts.length - 1].x.toFixed(1)},${SVG_LG_H} L ${pts[0].x.toFixed(1)},${SVG_LG_H} Z`
})

/** 大图纵轴刻度（3 档，按指标范围与单位）——标签放轴区（x≈20），不被边框遮挡 */
const yTicksLg = computed(() => {
  const vals = trendVals.value.plot
  const [lo, hi] = metric.value.range(vals)
  const ticks: Array<{ y: number; label: string }> = []
  for (let i = 0; i <= 3; i++) {
    const v = lo + ((hi - lo) * i) / 3
    const y = SVG_LG_H - 16 - ((v - lo) / (hi - lo || 1)) * (SVG_LG_H - 32)
    let label = String(Math.round(v))
    if (metric.value === METRICS.sleepSecs) label = `${(v / 3600).toFixed(1)}h`
    else if (metric.value === METRICS.weight) label = v.toFixed(1)
    ticks.push({ y, label })
  }
  return ticks
})

// ── hover：显示当天全部数据（原始值，缺失标 —，不用旧值/默认值）──
const hoverIdx = ref(-1)
const hoverIdxLg = ref(-1)
/** 2026-08-08：大图悬浮窗跟随鼠标（像素位置，相对 SVG 容器） */
const hoverPosLg = ref<{ x: number; y: number } | null>(null)
const hoverPoint = computed(() => (hoverIdx.value >= 0 && hoverIdx.value < trendPts.value.length ? trendPts.value[hoverIdx.value] : null))
const hoverPointLg = computed(() => (hoverIdxLg.value >= 0 && hoverIdxLg.value < trendPtsLg.value.length ? trendPtsLg.value[hoverIdxLg.value] : null))

function fmtHm(secs: number): string {
  const h = Math.floor(secs / 3600)
  const m = Math.round((secs % 3600) / 60)
  return `${h}h${m ? `${m}m` : ''}`
}

/** 当天全部健康数据（原始值；没有的标 —）——hover 数据栏用，绝不使用旧值/默认值 */
const hoverDetail = computed(() => {
  const p = hoverPoint.value ?? hoverPointLg.value
  if (!p || !p.rawDay) return null
  const d = p.rawDay
  return {
    date: d.date,
    rows: [
      ['步数', d.steps != null ? Number(d.steps).toLocaleString() : '—'],
      ['睡眠时长', d.sleep?.secs != null ? fmtHm(d.sleep.secs) : '—'],
      ['睡眠得分', d.sleep?.score != null ? String(d.sleep.score) : '—'],
      ['静息心率', d.resting_hr != null ? `${d.resting_hr} bpm` : '—'],
      ['体重', d.weight != null ? `${d.weight} kg` : '—'],
      ['血氧', d.spo2 != null ? `${d.spo2}%` : '—'],
      ['摄氧量', d.vo2max != null ? String(d.vo2max) : '—'],
    ] as Array<[string, string]>,
  }
})

/** SVG 鼠标移动 → 换算 viewBox 坐标 → 找最近数据点（小图） */
function onSvgMove(e: MouseEvent) {
  const svg = e.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const px = ((e.clientX - rect.left) / rect.width) * SVG_W
  let best = -1; let bestDist = Infinity
  trendPts.value.forEach((p, i) => {
    const d = Math.abs(p.x - px)
    if (d < bestDist) { bestDist = d; best = i }
  })
  hoverIdx.value = best
  hoverIdxLg.value = -1
}

/** 大图鼠标移动 */
function onSvgMoveLg(e: MouseEvent) {
  const svg = e.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const px = ((e.clientX - rect.left) / rect.width) * SVG_LG_W
  let best = -1; let bestDist = Infinity
  trendPtsLg.value.forEach((p, i) => {
    const d = Math.abs(p.x - px)
    if (d < bestDist) { bestDist = d; best = i }
  })
  hoverIdxLg.value = best
  hoverIdx.value = -1
  // 悬浮窗跟随鼠标（像素）；左右留边防溢出
  const x = Math.min(Math.max(e.clientX - rect.left, 70), rect.width - 70)
  hoverPosLg.value = { x, y: e.clientY - rect.top }
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
          <div class="trend-head">
            <span class="c-label">📈 {{ metric.label }}</span>
            <!-- 2026-08-08：纵轴下拉已移到大图区（只在显示大图时出现）；小图标题跟随联动 -->
          </div>
          <div class="chart-wrap" :class="{ large: showTrendLarge }" @click="showTrendLarge = !showTrendLarge" title="点击查看大图">
            <svg
              v-if="trendPts.length"
              class="sparkline"
              :viewBox="`0 0 ${SVG_W} ${SVG_H}`"
              preserveAspectRatio="none"
              @mousemove="onSvgMove"
              @mouseleave="hoverIdx = -1"
            >
              <defs>
                <!-- 2026-08-08：趋势彩色化——每段折线独立渐变（起点色→终点色过渡），面积渐变 -->
                <linearGradient v-for="sg in segColors" :key="sg.id" :id="sg.id" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" :stop-color="sg.c1" />
                  <stop offset="100%" :stop-color="sg.c2" />
                </linearGradient>
                <linearGradient id="trendArea" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.28" />
                  <stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />
                </linearGradient>
              </defs>
              <path :d="areaPath" fill="url(#trendArea)" />
              <line
                v-for="sg in segColors"
                :key="sg.id"
                :x1="sg.x1"
                :y1="sg.y1"
                :x2="sg.x2"
                :y2="sg.y2"
                :stroke="`url(#${sg.id})`"
                stroke-width="1.6"
                stroke-linecap="round"
              />
              <circle
                v-for="(p, i) in trendPts"
                :key="p.date"
                :cx="p.x"
                :cy="p.y"
                :r="i === trendPts.length - 1 ? 3 : 1.8"
                :fill="p.color"
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
            <div v-else class="trend-empty">暂无数据</div>
            <!-- 2026-08-08：大图展开时移除小图悬浮显示（避免遮挡/与点重叠） -->
            <div v-if="!showTrendLarge && hoverDetail" class="chart-tip">
              <div class="tip-date">{{ hoverDetail.date }}</div>
              <div v-for="(r, ri) in hoverDetail.rows" :key="ri" class="tip-row">
                <span class="tip-k">{{ r[0] }}</span>
                <span class="tip-v">{{ r[1] }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 2026-08-08：趋势大图（点击小图展开，卡片下部显示；再点收起） -->
      <div v-if="showTrendLarge && trendPtsLg.length" class="trend-large">
        <div class="trend-large-head">
          <span class="c-label">📈 {{ metric.label }}</span>
          <!-- 2026-08-08：纵轴下拉放回大图区右上（只在显示大图时出现） -->
          <select v-model="selectedMetric" class="metric-select" title="选择纵轴指标">
            <option value="steps">步数</option>
            <option value="sleepSecs">睡眠时长</option>
            <option value="sleepScore">睡眠得分</option>
            <option value="restingHr">静息心率</option>
            <option value="weight">体重</option>
          </select>
        </div>
        <svg
          :viewBox="`0 0 ${SVG_LG_W} ${SVG_LG_H}`"
          class="trend-large-svg"
          @mousemove="onSvgMoveLg"
          @mouseleave="hoverIdxLg = -1"
        >
          <defs>
            <linearGradient v-for="sg in segColorsLg" :key="sg.id" :id="sg.id" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" :stop-color="sg.c1" />
              <stop offset="100%" :stop-color="sg.c2" />
            </linearGradient>
            <linearGradient id="trendAreaLg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.25" />
              <stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />
            </linearGradient>
          </defs>
          <!-- 2026-08-08：明显横纵轴（主线 + 刻度虚线 + 轴区标签防遮挡） -->
          <line :x1="PAD_LG" y1="8" :x2="PAD_LG" :y2="SVG_LG_H - 10" stroke="var(--border-main)" stroke-width="1" />
          <line :x1="PAD_LG" :y1="SVG_LG_H - 10" :x2="SVG_LG_W - 6" :y2="SVG_LG_H - 10" stroke="var(--border-main)" stroke-width="1" />
          <g v-for="(t, ti) in yTicksLg" :key="'y' + ti">
            <line
              :x1="PAD_LG" :y1="t.y" :x2="SVG_LG_W - 8" :y2="t.y"
              stroke="var(--border-subtle)" stroke-width="0.5" stroke-dasharray="2 3"
            />
            <text :x="PAD_LG - 5" :y="t.y + 3" text-anchor="end" font-size="8" fill="var(--text-primary)">
              {{ t.label }}
            </text>
          </g>
          <path :d="areaPathLg" fill="url(#trendAreaLg)" />
          <line
            v-for="sg in segColorsLg"
            :key="sg.id"
            :x1="sg.x1" :y1="sg.y1" :x2="sg.x2" :y2="sg.y2"
            :stroke="`url(#${sg.id})`" stroke-width="2" stroke-linecap="round"
          />
          <!-- 日期标注（深色可读：半透明深描边 + paint-order） -->
          <text
            v-for="p in trendPtsLg"
            :key="'d' + p.date"
            :x="p.x" :y="SVG_LG_H - 2"
            text-anchor="middle" font-size="9" fill="var(--text-secondary)"
            stroke="rgba(0,0,0,0.55)" stroke-width="2" paint-order="stroke"
          >{{ p.date }}</text>
          <!-- 点 -->
          <circle
            v-for="(p, i) in trendPtsLg"
            :key="'c' + p.date"
            :cx="p.x" :cy="p.y"
            :r="i === trendPtsLg.length - 1 ? 4 : 2.5"
            :fill="p.color"
            :stroke="i === trendPtsLg.length - 1 ? '#fff' : 'none'"
            stroke-width="1"
          />
          <!-- hover 高亮点 -->
          <circle
            v-if="hoverPointLg"
            :cx="hoverPointLg.x" :cy="hoverPointLg.y"
            r="4.5" fill="var(--accent)" stroke="#fff" stroke-width="1.2"
          />
        </svg>
        <!-- 2026-08-08：大图悬浮窗跟随鼠标位置（不固定显示） -->
        <div
          v-if="hoverPointLg && hoverDetail"
          class="chart-tip-lg"
          :style="{ left: (hoverPosLg?.x ?? 0) + 'px', top: (hoverPosLg?.y ?? 0) + 'px' }"
        >
          <div class="tip-date">{{ hoverDetail.date }}</div>
          <div v-for="(r, ri) in hoverDetail.rows" :key="ri" class="tip-row">
            <span class="tip-k">{{ r[0] }}</span>
            <span class="tip-v">{{ r[1] }}</span>
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
  cursor: pointer;      /* 2026-08-08：点击展开/收起大图 */
}
/* 2026-08-08：趋势大图（卡片下部展开） */
.trend-large {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-subtle);
  position: relative;   /* 2026-08-08：大图悬浮窗跟随鼠标定位基准 */
}
.trend-large-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.trend-large-svg {
  width: 100%;
  height: 160px;
  display: block;
}
/* 2026-08-08：大图悬浮窗——跟随鼠标位置（由 onSvgMoveLg 写入 left/top） */
.chart-tip-lg {
  position: absolute;
  transform: translate(-50%, calc(-100% - 8px));
  background: var(--bg-elevated, rgba(20, 20, 24, 0.95));
  border: 1px solid var(--border-main);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 10px;
  line-height: 1.5;
  z-index: 6;
  min-width: 120px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
  pointer-events: none;
}
.chart-tip {
  position: absolute;
  top: -2px;
  right: 0;
  background: var(--bg-elevated, rgba(20, 20, 24, 0.95));
  border: 1px solid var(--border-main);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 10px;
  line-height: 1.5;
  z-index: 5;
  min-width: 120px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
  pointer-events: none;   /* 2026-08-08：不拦截鼠标——修复悬浮窗与数据点重叠导致 hover 无高亮 */
}
.tip-date { font-weight: 700; margin-bottom: 2px; color: var(--text-primary); }
.tip-row { display: flex; justify-content: space-between; gap: 10px; }
.tip-k { color: var(--text-muted); }
.tip-v { color: var(--text-primary); font-family: 'Courier New', monospace; }
.trend-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.metric-select {
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 4px;
  border: 1px solid var(--border-main);
  background: var(--bg-surface);
  color: var(--text-secondary);
  cursor: pointer;
  max-width: 84px;
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

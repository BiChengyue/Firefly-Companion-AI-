<script setup lang="ts">
/** 电脑状态卡（A3 v2）— 本地读 sensor state 文件（不走 hub），30s 同步刷新。
 *  布局：①前台进程(焦点绿/非焦点蓝/检测器绿红) ②占用条 ③当日圆环+图例 ④本日活动条 ⑤foot */
import { ref, computed, onMounted } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { useSyncRefresh } from '@/composables/useSyncRefresh'

interface Seg { start: number; end: number; type: string; label?: string; detail?: Record<string, number> }
interface SensorState {
  category?: string
  game?: string | null
  video?: string | null
  sitting_minutes?: number
  last_at?: number
  screens?: Array<{ monitor: number; name: string; primary: boolean; category: string; streaming?: boolean }>
  focus_monitor?: number
  system_usage?: { cpu?: number; mem?: number; disk?: Record<string, number>; gpu?: number }
  detector_ok?: boolean
  error?: string | null
  today_activities?: Seg[]
  today_categories?: Record<string, number>
}

const state = ref<SensorState | null>(null)
const error = ref('')
const lastTs = ref(0)
const loading = ref(false)
const hoverSeg = ref<Seg | null>(null)
let refreshVersion = 0

const CAT_LABELS: Record<string, string> = {
  coding: '写代码', browsing: '浏览网页', communication: '通讯聊天', game: '游戏',
  video: '看视频', document: '文档处理', meeting: '会议', design: '设计',
  writing: '写作', tool: '工具', unknown: '其他', rest: '休息',
  multi: '多任务', offline: '离线', star_rail: '星铁',
}
const CAT_COLORS: Record<string, string> = {
  coding: '#3b82f6', browsing: '#22c55e', communication: '#a855f7', game: '#f97316',
  video: '#ef4444', document: '#06b6d4', design: '#ec4899', writing: '#84cc16',
  meeting: '#8b5cf6', tool: '#94a3b8', unknown: '#64748b', rest: '#22c55e',
  multi: '#eab308', offline: '#9ca3af', star_rail: '#f97316',
}

const stale = computed(() => {
  if (!state.value?.last_at) return false
  return Date.now() / 1000 - state.value.last_at > 5 * 60
})
const sitting = computed(() => {
  const s = state.value?.sitting_minutes
  return typeof s === 'number' && s >= 1 ? Math.round(s) : 0
})
// 占用条数据（对齐服务器 bars）
const resourceBars = computed(() => {
  const u = state.value?.system_usage
  if (!u) return []
  return [
    { label: 'CPU', pct: u.cpu ?? 0 },
    { label: '内存', pct: u.mem ?? 0 },
    { label: '磁盘 C', pct: u.disk?.C ?? 0 },
    ...(typeof u.gpu === 'number' ? [{ label: '显卡', pct: u.gpu }] : []),
  ]
})
// 前台进程列表（screens + 焦点标记 + 检测器行；多屏按主屏幕/副屏幕/副屏幕 N 命名）
const procRows = computed(() => {
  const rows: Array<{ name: string; cat: string; focus: boolean }> = []
  let subIdx = 0
  for (const s of state.value?.screens ?? []) {
    rows.push({
      name: s.primary ? '主屏幕' : (subIdx === 0 ? '副屏幕' : `副屏幕 ${subIdx + 1}`),
      cat: s.category,
      focus: s.monitor === state.value?.focus_monitor,
    })
    if (!s.primary) subIdx++
  }
  if (!rows.length && state.value?.category) {
    rows.push({ name: '主屏幕', cat: state.value.category ?? 'unknown', focus: true })
  }
  // 主屏幕排最上面（primary 优先），其余按原顺序
  rows.sort((a, b) => (a.name === '主屏幕' ? -1 : 0) - (b.name === '主屏幕' ? -1 : 0))
  return rows
})
// 当日圆环数据
const ringItems = computed(() => {
  const c = state.value?.today_categories ?? {}
  const items = Object.entries(c).filter(([, v]) => v >= 60)
  const total = items.reduce((a, [, v]) => a + v, 0)
  return { items: items.map(([k, v]) => ({ k, v, pct: total ? v / total : 0 })), total }
})
// 活动条（0:00 → 24:00 全长，未来时段黑色）
const timeline = computed(() => {
  const acts = state.value?.today_activities ?? []
  const t0 = new Date(); t0.setHours(0, 0, 0, 0)
  const start = t0.getTime() / 1000
  const end = start + 86400
  const now = Date.now() / 1000
  return { acts, start, end, total: 86400, now }
})

function fmtMin(sec: number) {
  if (sec < 60) return `${Math.round(sec)} 秒`
  if (sec < 3600) return `${Math.round(sec / 60)} 分钟`
  return `${Math.floor(sec / 3600)} 小时 ${Math.round((sec % 3600) / 60)} 分`
}
function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
const hoverText = computed(() => {
  const s = hoverSeg.value
  if (!s) return ''
  const dur = fmtMin(s.end - s.start)
  const base = `${CAT_LABELS[s.type] ?? s.type} ${fmtTime(s.start)}–${fmtTime(s.end)}（${dur}）`
  if (s.type === 'multi' && s.detail) {
    const total = Object.values(s.detail).reduce((a, b) => a + b, 0) || 1
    const parts = Object.entries(s.detail)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `${CAT_LABELS[k] ?? k} ${Math.round((v / total) * 100)}%`)
    return `${base}\n${parts.join(' · ')}`
  }
  return base
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

onMounted(() => {
  refresh()
  useSyncRefresh(refresh, 30000)
})
</script>

<template>
  <div class="computer-card">
    <div class="head">
      <span class="title">🖥️ 电脑状态</span>
      <button class="refresh-btn" :disabled="loading" title="刷新" @click="refresh">⟳</button>
    </div>

    <div v-if="error" class="unavailable">{{ error }}</div>

    <template v-else-if="state">
      <!-- ① 前台进程 + 检测器 -->
      <ul class="procs">
        <li v-for="(r, i) in procRows" :key="i">
          <span class="dot" :class="r.focus ? 'focus' : 'blur'" />
          <span class="pname">{{ r.name }}</span>
          <span class="pcat">{{ CAT_LABELS[r.cat] ?? r.cat }}</span>
        </li>
        <li>
          <span class="dot" :class="stale || state.detector_ok === false ? 'down' : 'up'" />
          <span class="pname">检测器</span>
          <span class="pcat">{{ stale ? '失效' : (state.detector_ok === false ? '异常' : '正常') }}</span>
        </li>
      </ul>

      <!-- ② 占用条 -->
      <div class="bars">
        <div v-for="b in resourceBars" :key="b.label" class="bar-row">
          <span class="bar-label">{{ b.label }}</span>
          <div class="bar"><div class="bar-fill" :style="{ width: Math.min(100, b.pct) + '%' }" /></div>
          <span class="bar-val">{{ b.pct }}%</span>
        </div>
      </div>

      <!-- ③ 圆环 + 图例 -->
      <div class="ring-block">
        <div class="ring" :style="{ background: ringItems.total ? `conic-gradient(${ringItems.items.map((it, idx) => `${CAT_COLORS[it.k] ?? '#888'} ${idx === 0 ? 0 : ringItems.items.slice(0, idx).reduce((a, x) => a + x.pct, 0) * 100}% ${ringItems.items.slice(0, idx + 1).reduce((a, x) => a + x.pct, 0) * 100}%`).join(',')})` : '#333' }">
          <div class="ring-hole">{{ ringItems.total ? Math.round(ringItems.total / 60) : 0 }}<small>分</small></div>
        </div>
        <ul class="legend">
          <li v-for="it in ringItems.items" :key="it.k">
            <span class="swatch" :style="{ background: CAT_COLORS[it.k] ?? '#888' }" />
            <span class="lk">{{ CAT_LABELS[it.k] ?? it.k }}</span>
            <span class="lv">{{ Math.round(it.pct * 100) }}%</span>
          </li>
          <li v-if="!ringItems.items.length" class="empty">今日暂无分类统计</li>
        </ul>
      </div>

      <!-- ④ 本日活动条 -->
      <div class="timeline-wrap">
        <div class="timeline" @mouseleave="hoverSeg = null">
          <div
            v-for="(s, i) in timeline.acts"
            :key="i"
            class="tseg"
            :class="s.type"
            :style="{ width: Math.max(0.3, ((s.end - s.start) / timeline.total) * 100) + '%', background: CAT_COLORS[s.type] ?? '#888' }"
            @mouseenter="hoverSeg = s"
          />
          <!-- 未来时段（now → 24:00）黑色 -->
          <div v-if="timeline.now < timeline.end" class="tseg future" :style="{ width: ((timeline.end - timeline.now) / timeline.total) * 100 + '%' }" />
        </div>
        <div class="tlabel"><span>0:00</span><span>24:00</span></div>
        <div v-if="hoverSeg" class="tooltip">{{ hoverText }}</div>
      </div>

      <div class="foot">
        更新于 {{ lastTs ? new Date(lastTs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }} · 坐 {{ sitting }} 分钟
      </div>
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
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.title { font-size: 12px; font-weight: 700; color: var(--text-primary); }
.refresh-btn {
  background: none; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm);
  color: var(--text-muted); cursor: pointer; font-size: 12px; line-height: 1; padding: 2px 6px;
}
.refresh-btn:hover { color: var(--accent-strong); border-color: var(--border-accent); }
.refresh-btn:disabled { opacity: 0.5; cursor: default; }
.unavailable { font-size: 11px; color: var(--text-muted); padding: 6px 0; }

.procs { list-style: none; margin: 0 0 8px; padding: 0; }
.procs li { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot.focus { background: #22c55e; }
.dot.blur { background: #3b82f6; }
.dot.up { background: #22c55e; }
.dot.down { background: #ef4444; }
.pname { color: var(--text-primary); }
.pcat { margin-left: auto; color: var(--text-tertiary); font-size: 11px; }

.bars { margin: 0 0 8px; }
.bar-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
.bar-label { width: 40px; font-size: 11px; color: var(--text-tertiary); }
.bar { flex: 1; height: 6px; background: var(--border-subtle); border-radius: 3px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--accent); border-radius: 3px; }
.bar-val { width: 34px; text-align: right; font-size: 11px; font-family: 'Courier New', monospace; }

.ring-block { display: flex; align-items: center; gap: 12px; margin: 4px 0 8px; }
.ring {
  width: 72px; height: 72px; border-radius: 50%; position: relative; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
.ring-hole {
  width: 48px; height: 48px; border-radius: 50%; background: var(--bg-elevated);
  display: flex; align-items: baseline; justify-content: center; font-size: 15px; font-weight: 700; color: var(--text-primary);
}
.ring-hole small { font-size: 9px; color: var(--text-tertiary); margin-left: 2px; font-weight: 400; }
.legend { list-style: none; margin: 0; padding: 0; flex: 1; }
.legend li { display: flex; align-items: center; gap: 6px; padding: 1px 0; font-size: 11px; }
.swatch { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
.lk { color: var(--text-secondary); }
.lv { margin-left: auto; color: var(--text-tertiary); font-family: 'Courier New', monospace; }
.legend .empty { color: var(--text-tertiary); }

.timeline-wrap { margin: 6px 0 4px; position: relative; }
.timeline { display: flex; height: 12px; border-radius: 3px; overflow: hidden; background: #333; }
.tseg { height: 100%; }
.tseg.empty { opacity: 0.3; }
.tseg.future { background: #000 !important; }
.tlabel { display: flex; justify-content: space-between; font-size: 9px; color: var(--text-tertiary); margin-top: 2px; font-family: 'Courier New', monospace; }
.tooltip {
  position: absolute; top: 16px; left: 0; right: 0; z-index: 5;
  background: rgba(0, 0, 0, 0.85); color: #eee; font-size: 10px; padding: 4px 8px;
  border-radius: 6px; white-space: pre-line; line-height: 1.4;
}

.foot { margin-top: 8px; font-size: 9px; color: var(--text-muted); font-family: 'Courier New', monospace; }
</style>

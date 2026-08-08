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
  screens?: Array<{ monitor: number; name: string; primary: boolean; category: string; proc?: string | null; streaming?: boolean }>
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
const hoverSeg = ref<Seg | null>(null)      // 副条 hover 命中的段
const barEl = ref<HTMLElement | null>(null)
let refreshVersion = 0

// 预览窗三态：auto（最近1h，常显）/ follow（跟随主条鼠标）/ lock（点击锁定）
const winMode = ref<'auto' | 'follow' | 'lock'>('auto')
const winCenter = ref<number | null>(null)  // follow/lock 的窗口中心（当天秒）
let lastInteract = Date.now()
const AUTO_IDLE_MS = 5 * 60 * 1000           // 5 分钟未与主条交互 → 回 auto

const CAT_LABELS: Record<string, string> = {
  coding: '写代码', browsing: '浏览网页', communication: '通讯聊天', game: '游戏',
  video: '看视频', document: '文档处理', meeting: '会议', design: '设计',
  writing: '写作', tool: '工具', unknown: '其他', rest: '休息', away: '休息',
  multi: '多任务', offline: '离线', star_rail: '星铁', firefly: '流萤',
}
const CAT_COLORS: Record<string, string> = {
  coding: '#3b82f6', browsing: '#eab308', communication: '#a855f7', game: '#f97316',
  video: '#ef4444', document: '#0d9488', design: '#ec4899', writing: '#84cc16',
  meeting: '#8b5cf6', tool: '#94a3b8', unknown: '#64748b', rest: '#22c55e', away: '#22c55e',
  multi: '#eab308', offline: '#9ca3af', star_rail: '#f97316', firefly: '#06b6d4',
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
  // 2026-08-08（手机卡）：只显示「当前应用」一行（删除副屏幕行）——名称 + 类型
  // 数据暂用电脑 sensor 主屏（接口改造后换 phone.focus_app）
  const rows: Array<{ name: string; cat: string; focus: boolean }> = []
  const s = state.value?.screens?.find((x) => x.primary)
  if (s) {
    rows.push({ name: s.proc ?? '当前应用', cat: s.category, focus: true })
  } else if (state.value?.category) {
    rows.push({ name: '当前应用', cat: state.value.category ?? 'unknown', focus: true })
  }
  return rows
})
// 当日圆环数据
// 圆环中心：>1 小时显示「X.X 时」，否则「X 分」
const ringTotal = computed(() => {
  const total = ringItems.value.total
  if (total >= 3600) return { v: (total / 3600).toFixed(1), unit: '时' }
  return { v: Math.round(total / 60), unit: '分' }
})
const ringItems = computed(() => {
  const c = state.value?.today_categories ?? {}
  const items = Object.entries(c).filter(([, v]) => v >= 60)
  const total = items.reduce((a, [, v]) => a + v, 0)
  return { items: items.map(([k, v]) => ({ k, v, pct: total ? v / total : 0 })), total }
})
// 活动条（0:00 → 24:00 全长，未来时段黑色）
const timeline = computed(() => {
  // 过滤零宽段 + 离线段（offline 当无记录显示，不占数据段）
  const acts = (state.value?.today_activities ?? []).filter((s) => s.end > s.start && s.type !== 'offline')
  const t0 = new Date(); t0.setHours(0, 0, 0, 0)
  const start = t0.getTime() / 1000
  const end = start + 86400
  const now = Date.now() / 1000
  // 全真实比例（不保底）——保证 hover-mask/主条/放大窗严格线性对应
  const widths = acts.map((s) => ((s.end - s.start) / 86400) * 100)
  return { acts, start, end, total: 86400, now, widths }
})
// A：主条交互——mousemove 跟随（follow）/ click 锁定 / mouseleave 未锁回 auto
function onBarMove(e: MouseEvent) {
  if (winMode.value === 'lock') return // 锁定态不跟随
  lastInteract = Date.now()
  const el = barEl.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
  // 2026-08-08：hover 到未来区（黑色）时钳到当前时间——窗口不越过 now
  const nowSec = Date.now() / 1000 - timeline.value.start
  const sec = Math.min(ratio * 86400, Math.max(0, nowSec))
  winMode.value = 'follow'
  winCenter.value = sec
  hoverSeg.value = findSegAt(sec)
}
function onBarClick(e: MouseEvent) {
  lastInteract = Date.now()
  if (winMode.value === 'lock') {
    winMode.value = 'auto' // 再点解锁回 auto
    winCenter.value = null
  } else {
    const el = barEl.value
    if (el) {
      const rect = el.getBoundingClientRect()
      const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
      const nowSec = Date.now() / 1000 - timeline.value.start
      winMode.value = 'lock'
      winCenter.value = Math.min(ratio * 86400, Math.max(0, nowSec))
      hoverSeg.value = findSegAt(winCenter.value)
    }
  }
}
function onBarLeave() {
  if (winMode.value !== 'lock') {
    winMode.value = 'auto'
    winCenter.value = null
  }
  hoverSeg.value = null
}
function findSegAt(sec: number): Seg | null {
  const t = timeline.value.start + sec
  return timeline.value.acts.find((s) => t >= s.start && t < s.end) ?? null
}

// 预览窗（三态）：auto = [now-1h, now]；follow/lock = center±30min
const winWindow = computed(() => {
  const t = timeline.value
  const nowSec = t.now - t.start
  let winStart: number, winEnd: number
  if (winMode.value === 'auto' || winCenter.value === null) {
    // AUTO：窗口正好覆盖已记录数据（最后采样 → 前 1 小时），末尾不留空隙
    const lastAt = Math.min(nowSec, Math.max(0, (state.value?.last_at ?? t.now) - t.start))
    winEnd = lastAt
    winStart = Math.max(0, winEnd - 3600)
  } else {
    winStart = Math.max(0, winCenter.value - 1800)
    // 2026-08-08：窗口右边界不超过当前时间（接近/超过 now 的部分不显示未来）
    winEnd = Math.min(nowSec, winCenter.value + 1800)
    if (winEnd <= winStart) winEnd = winStart + 1 // 防零宽
  }
  const segs = t.acts
    .filter((s) => (s.start - t.start) < winEnd && (s.end - t.start) > winStart)
    .map((s) => {
      const w = ((Math.min(s.end, t.start + winEnd) - Math.max(s.start, t.start + winStart)) / (winEnd - winStart)) * 100
      return { ...s, w }
    })
    .sort((a, b) => a.start - b.start)
  // 窗口内小空隙（<90s，采样断档间隙）并入前段填色，副条连续无灰缝
  const raw: typeof segs = []
  for (const s of segs) {
    const prev = raw[raw.length - 1]
    if (prev && s.start - prev.end > 0 && s.start - prev.end < 90) {
      prev.end = s.start // 前段只延伸到空隙起点，保留 s
      prev.w = ((Math.min(prev.end, t.start + winEnd) - Math.max(prev.start, t.start + winStart)) / (winEnd - winStart)) * 100
    }
    raw.push(s)
  }
  // 2026-08-08：绝对定位（与主条同机制）——flex width% 与主条 left% 解析基准不一致导致错位；累加 left
  // 2026-08-08b：防御性强制按时间升序重排
  // 2026-08-08d【真凶】：left 必须是「绝对位置」(s.start-winStart)/窗口宽——不是累加！
  //   累加会把窗口开头的大段空隙（无数据灰区）挤到末尾 → 副条「先蓝后灰」与主条「先灰后蓝」翻转
  const sorted = [...raw].sort((a, b) => a.start - b.start)
  const filled = sorted.map((s) => {
    const left = Math.max(0, ((s.start - t.start - winStart) / (winEnd - winStart)) * 100)
    return { ...s, left }
  })
  // 2026-08-08c：副条补画未来黑色段（winEnd 之后到 now 之间的部分主条为黑色——副条同样画黑，不再露灰背景）
  let futureLeft = 0
  let futureW = 0
  if (winEnd > nowSec) {
    futureLeft = ((Math.max(winStart, nowSec) - winStart) / (winEnd - winStart)) * 100
    futureW = ((winEnd - Math.max(winStart, nowSec)) / (winEnd - winStart)) * 100
  }
  return { winStart, winEnd, segs: filled, mode: winMode.value, futureLeft, futureW }
})

// 5 分钟闲置 → 回 auto（30s tick 检查）
function checkIdleAuto() {
  if (Date.now() - lastInteract > AUTO_IDLE_MS && winMode.value !== 'auto') {
    winMode.value = 'auto'
    winCenter.value = null
    hoverSeg.value = null
  }
}

// 预览窗未 hover 时的汇总文本（窗口内各类型时长 top3）
const winSummary = computed(() => {
  const segs = winWindow.value.segs
  if (!segs.length) return '（该时段无记录）'
  const dur: Record<string, number> = {}
  for (const s of segs) dur[s.type] = (dur[s.type] ?? 0) + (s.end - s.start)
  const parts = Object.entries(dur)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([k, v]) => `${CAT_LABELS[k] ?? k} ${Math.round(v / 60)} 分`)
  return parts.join(' · ')
})

function fmtMin(sec: number) {
  if (sec < 60) return `${Math.round(sec)} 秒`
  if (sec < 3600) return `${Math.round(sec / 60)} 分钟`
  return `${Math.floor(sec / 3600)} 小时 ${Math.round((sec % 3600) / 60)} 分`
}
function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
function fmtSeg(s: Seg) {
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
}

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

useSyncRefresh(refresh, 30000) // 30s 同步刷新线（setup 顶层订阅，onUnmounted 生效）

onMounted(() => {
  refresh()
  setInterval(checkIdleAuto, 30000) // 5 分钟闲置检查（与刷新同节奏）
})
</script>

<template>
  <div class="computer-card">
    <div class="head">
      <div class="head-left">
        <span class="dot" :class="stale ? 'down' : 'up'" title="手机在线状态（30s 心跳）" />
        <span class="title">📱 手机状态</span>
      </div>
      <button class="refresh-btn" :disabled="loading" title="刷新" @click="refresh">⟳</button>
    </div>

    <div v-if="error" class="unavailable">{{ error }}</div>

    <template v-else-if="state">
      <!-- ① 前台进程（检测器在线状态已移到标题栏红绿点） -->
      <ul class="procs">
        <li v-for="(r, i) in procRows" :key="i">
          <span class="dot" :class="r.focus ? 'focus' : 'blur'" />
          <span class="pname">{{ r.name }}</span>
          <span class="pcat">{{ CAT_LABELS[r.cat] ?? r.cat }}</span>
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
          <div class="ring-hole">{{ ringTotal.v }}<small>{{ ringTotal.unit }}</small></div>
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

      <!-- ④ 本日活动条 + 常显预览窗（三态：最近1h/跟随/锁定） -->
      <div class="timeline-wrap">
        <div ref="barEl" class="timeline" @mousemove="onBarMove" @click="onBarClick" @mouseleave="onBarLeave">
          <!-- 当前窗口在主条上的边界高亮 -->
          <div
            class="hover-mask"
            :style="{
              left: (winWindow.winStart / timeline.total) * 100 + '%',
              width: ((winWindow.winEnd - winWindow.winStart) / timeline.total) * 100 + '%',
            }"
          />
          <div
            v-for="(s, i) in timeline.acts"
            :key="i"
            class="tseg"
            :class="s.type"
            :style="{
              left: ((s.start - timeline.start) / timeline.total) * 100 + '%',
              width: timeline.widths[i] + '%',
              background: CAT_COLORS[s.type] ?? '#888',
            }"
          />
          <!-- 未来时段（now → 24:00）黑色 -->
          <div
            v-if="timeline.now < timeline.end"
            class="tseg future"
            :style="{
              left: ((timeline.now - timeline.start) / timeline.total) * 100 + '%',
              width: Math.max(0.3, ((timeline.end - timeline.now) / timeline.total) * 100) + '%',
            }"
          />
        </div>
        <div class="tlabel"><span>0:00</span><span>24:00</span></div>

        <!-- 预览窗（常显） -->
        <div class="zoom">
          <div class="zoom-head">
            <span>{{ fmtTime(timeline.start + winWindow.winStart) }}–{{ fmtTime(timeline.start + winWindow.winEnd) }}</span>
            <span class="zoom-mode" :class="winWindow.mode">{{ winWindow.mode === 'auto' ? '最近 1 小时' : (winWindow.mode === 'lock' ? '已锁定' : '跟随') }}</span>
            <button v-if="winWindow.mode !== 'auto'" class="zoom-back" title="回到最近 1 小时" @click="winMode = 'auto'; winCenter = null; hoverSeg = null">⟲</button>
          </div>
          <div class="zoom-bar" @mouseleave="hoverSeg = null">
            <div
              v-for="(s, i) in winWindow.segs"
              :key="i"
              class="zseg"
              :style="{ left: s.left + '%', width: s.w + '%', background: CAT_COLORS[s.type] ?? '#888' }"
              @mouseenter="hoverSeg = s"
            />
            <!-- 2026-08-08c：副条未来黑色段（与主条 future 一致） -->
            <div v-if="winWindow.futureW > 0" class="zseg future" :style="{ left: winWindow.futureLeft + '%', width: winWindow.futureW + '%' }" />
          </div>
          <div class="zoom-detail">{{ hoverSeg ? fmtSeg(hoverSeg) : winSummary }}</div>
        </div>
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
.head-left { display: flex; align-items: center; gap: 6px; }
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
  display: flex; align-items: center; justify-content: center; font-size: 15px; font-weight: 700; color: var(--text-primary);
}
.ring-hole small { font-size: 9px; color: var(--text-tertiary); margin-left: 2px; font-weight: 400; }
.legend { list-style: none; margin: 0; padding: 0; flex: 1; }
.legend li { display: flex; align-items: center; gap: 6px; padding: 1px 0; font-size: 11px; }
.swatch { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
.lk { color: var(--text-secondary); }
.lv { margin-left: auto; color: var(--text-tertiary); font-family: 'Courier New', monospace; }
.legend .empty { color: var(--text-tertiary); }

.timeline-wrap { margin: 6px 0 4px; position: relative; }
.timeline { display: flex; height: 12px; border-radius: 3px; overflow: hidden; background: #333; position: relative; }
.hover-mask {
  position: absolute; top: 0; bottom: 0; z-index: 2;
  border: 1.5px solid #fff; background: rgba(255, 255, 255, 0.15); border-radius: 3px;
  pointer-events: none; box-sizing: border-box;
}
.tseg { position: absolute; top: 0; height: 100%; }
.tseg.empty { opacity: 0.3; }
.tseg.future { background: #000 !important; }
.tlabel { display: flex; justify-content: space-between; font-size: 9px; color: var(--text-tertiary); margin-top: 2px; font-family: 'Courier New', monospace; }
.tooltip {
  position: absolute; top: 16px; left: 0; right: 0; z-index: 5;
  background: rgba(0, 0, 0, 0.85); color: #eee; font-size: 10px; padding: 4px 8px;
  border-radius: 6px; white-space: pre-line; line-height: 1.4;
}
/* A：hover 放大浮层（±30 分钟窗） */
.zoom {
  margin-top: 6px; background: var(--bg-surface, rgba(0, 0, 0, 0.4)); border: 1px solid var(--border-subtle);
  border-radius: 6px; padding: 6px 8px;
}
.zoom-head { font-size: 10px; color: var(--text-tertiary); margin-bottom: 4px; font-family: 'Courier New', monospace; display: flex; align-items: center; gap: 6px; }
.zoom-mode { font-size: 9px; padding: 0 5px; border-radius: 6px; border: 1px solid var(--border-subtle); font-family: inherit; }
.zoom-mode.auto { color: var(--text-muted); }
.zoom-mode.follow { color: var(--accent-strong); border-color: var(--accent); }
.zoom-mode.lock { color: #c07a1f; border-color: #c07a1f; }
.zoom-back { margin-left: auto; border: 1px solid var(--border-subtle); background: none; color: var(--text-muted); border-radius: 4px; font-size: 10px; cursor: pointer; padding: 0 4px; }
.zoom-back:hover { color: var(--accent-strong); border-color: var(--accent); }
.zoom-bar { position: relative; display: block; height: 14px; border-radius: 3px; overflow: hidden; background: #3a3a3a; }
.zseg { position: absolute; top: 0; height: 100%; }
.zseg.future { background: #000 !important; }
.zoom-detail { margin-top: 4px; font-size: 10px; color: var(--text-secondary); white-space: pre-line; line-height: 1.4; }

.foot { margin-top: 8px; font-size: 9px; color: var(--text-muted); font-family: 'Courier New', monospace; }
</style>

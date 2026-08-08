<script setup lang="ts">
/**
 * 手机状态卡 — 基于电脑卡布局逐步换手机接口。
 * 布局（保持电脑卡顺序）：①标题栏 ②当前应用 ③资源占用条(电量/存储/内存)
 * ④圆环+图例 ⑤活动条+三态预览窗 ⑥foot(屏幕分钟)
 * 手机特有：勿扰开关/网络/位置(轨迹)插在③下方。
 *
 * 数据源：phone mock（接口未接）；接入 hub GET /api/v1/phone-state 后填 refresh()。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'

/** 未来 hub /api/v1/phone-state 契约 */
interface Seg { start: number; end: number; type: string; label?: string }
interface TrackPt { t: number; lat: number; lng: number }
interface PhoneState {
  last_at?: number
  battery?: number
  charging?: { active: boolean; method?: 'wired' | 'wireless' }
  storage_pct?: number
  ram_pct?: number
  dnd?: boolean
  network?: { kind: 'wifi' | 'mobile' | 'offline'; ssid?: string }
  loc_bucket?: 'home' | 'work' | 'out'
  track?: TrackPt[]
  focus_app?: { name: string; cat: string } | null
  screen_today_min?: number
  today_activities?: Seg[]
  today_categories?: Record<string, number>
}

function mockActivities(): Seg[] {
  const t0 = new Date(); t0.setHours(0, 0, 0, 0)
  const base = t0.getTime() / 1000
  const now = Date.now() / 1000
  const plan: Array<[number, number, string, string?]> = [
    [8 * 3600, 8 * 3600 + 1500, 'social', '微信'],
    [8 * 3600 + 1500, 9 * 3600 + 600, 'video', '哔哩哔哩'],
    [10 * 3600, 11 * 3600, 'game', '云·星穹铁道'],
    [11 * 3600, 12 * 3600, 'social', 'QQ'],
    [12 * 3600, 13 * 3600, 'rest', undefined],
    [13 * 3600, 14 * 3600 + 900, 'reading', '知乎'],
    [15 * 3600, 16 * 3600, 'tool', '甲壳虫ADB'],
    [16 * 3600, 17 * 3600, 'social', '微信'],
  ]
  return plan
    .filter(([s]) => base + s < now)
    .map(([s, e, t, l]) => ({ start: base + s, end: Math.min(base + e, now), type: t, ...(l ? { label: l } : {}) }))
}
const mockTrack: TrackPt[] = [
  { t: Date.now() / 1000 - 8 * 3600, lat: 31.2304, lng: 121.4737 },
  { t: Date.now() / 1000 - 7 * 3600, lat: 31.231, lng: 121.474 },
  { t: Date.now() / 1000 - 6 * 3600, lat: 31.232, lng: 121.4745 },
  { t: Date.now() / 1000 - 5 * 3600, lat: 31.233, lng: 121.475 },
]

const phone = ref<PhoneState | null>({
  last_at: Date.now() / 1000,
  battery: 87,
  charging: { active: true, method: 'wireless' },
  storage_pct: 64,
  ram_pct: 58,
  dnd: true,
  network: { kind: 'wifi', ssid: '我家WiFi' },
  loc_bucket: 'home',
  track: mockTrack,
  focus_app: { name: '微信', cat: 'social' },
  screen_today_min: 156,
  today_activities: mockActivities(),
  today_categories: { social: 7800, video: 4200, game: 3600, reading: 5400, rest: 3600, tool: 3600 },
})
const error = ref('')
const lastTs = ref(Date.now())
const loading = ref(false)

/** 勿扰开关（本地 UI；未来调 bus 下发手机端） */
const dndOverride = ref<boolean | null>(null)
function toggleDnd() {
  if (!phone.value) return
  const cur = dndOverride.value ?? phone.value.dnd ?? false
  dndOverride.value = !cur
}
const showTrack = ref(false)
function toggleTrack() {
  showTrack.value = !showTrack.value
}

/** 30s 心跳 → 5 分钟无心跳离线 */
const stale = computed(() => {
  if (!phone.value?.last_at) return true
  return Date.now() / 1000 - phone.value.last_at > 5 * 60
})

/** 手机 App 分类（圆环/图例/活动条共用） */
const CAT_LABELS: Record<string, string> = {
  social: '社交', video: '影音', game: '游戏', shopping: '购物',
  reading: '阅读', travel: '出行', finance: '金融', meeting: '会议',
  life: '生活', browsing: '浏览网页', document: '文档办公',
  tool: '工具', system: '系统', unknown: '其他', rest: '休息', away: '休息',
  offline: '离线',
}
const CAT_COLORS: Record<string, string> = {
  social: '#a855f7', video: '#ef4444', game: '#f97316', shopping: '#ec4899',
  reading: '#06b6d4', travel: '#14b8a6', finance: '#eab308', meeting: '#8b5cf6',
  life: '#84cc16', browsing: '#eab308', document: '#0d9488',
  tool: '#94a3b8', system: '#64748b', unknown: '#64748b', rest: '#22c55e', away: '#22c55e',
  offline: '#9ca3af',
}
const LOC_LABELS: Record<string, string> = { home: '家', work: '公司', out: '外出' }
const NET_LABELS: Record<string, string> = { wifi: 'Wi-Fi', mobile: '蜂窝', offline: '离线' }

/** ② 当前应用行 */
const focusRow = computed(() => phone.value?.focus_app ?? null)

/** 手机特有：勿扰 / 网络 / 位置 */
const basics = computed(() => {
  const p = phone.value
  return [
    {
      label: '勿扰',
      value: (dndOverride.value ?? p?.dnd) != null ? ((dndOverride.value ?? p?.dnd) ? '开启' : '关闭') : '—',
      toggle: true,
    },
    {
      label: '网络',
      value: p?.network
        ? `${NET_LABELS[p.network.kind] ?? p.network.kind}${p.network.kind === 'wifi' && p.network.ssid ? `·${p.network.ssid}` : ''}`
        : '—',
    },
    {
      label: '位置',
      value: p?.loc_bucket ? (LOC_LABELS[p.loc_bucket] ?? p.loc_bucket) : '—',
      track: true,
    },
  ]
})

/** ③ 资源占用条：电量 / 存储 / 内存 */
const resourceBars = computed(() => {
  const p = phone.value
  if (!p) return []
  return [
    { label: '电量', pct: p.battery ?? 0, sub: p.charging?.active ? (p.charging.method === 'wireless' ? '⚡无线' : '⚡有线') : '' },
    { label: '存储', pct: p.storage_pct ?? 0 },
    { label: '内存', pct: p.ram_pct ?? 0 },
  ]
})

/** ④ 当日圆环 + 图例 */
const ringItems = computed(() => {
  const cats = phone.value?.today_categories
  if (!cats) return { items: [] as Array<{ k: string; pct: number }>, total: 0 }
  const total = Object.values(cats).reduce((a, b) => a + b, 0)
  if (!total) return { items: [], total: 0 }
  const items = Object.entries(cats)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ k, pct: v / total }))
    .sort((a, b) => b.pct - a.pct)
  return { items, total }
})
const ringTotal = computed(() => {
  const total = ringItems.value.total
  if (!total) return { v: '—', unit: '' }
  if (total < 3600) return { v: Math.round(total / 60), unit: '分' }
  return { v: (total / 3600).toFixed(1), unit: '时' }
})

// ── ⑤ 活动条 + 三态预览窗（与电脑卡一致）──
const DAY_SEC = 86400
const timeline = computed(() => {
  const acts = (phone.value?.today_activities ?? []).filter((s) => s.end > s.start && s.type !== 'offline')
  const t0 = new Date(); t0.setHours(0, 0, 0, 0)
  const start = t0.getTime() / 1000
  const end = start + DAY_SEC
  const now = Date.now() / 1000
  const widths = acts.map((s) => ((s.end - s.start) / DAY_SEC) * 100)
  return { acts, start, end, total: DAY_SEC, now, widths }
})

const hoverSeg = ref<Seg | null>(null)
const barEl = ref<HTMLElement | null>(null)
const winMode = ref<'auto' | 'follow' | 'lock'>('auto')
const winCenter = ref<number | null>(null)
let lastInteract = Date.now()
const AUTO_IDLE_MS = 5 * 60 * 1000

function onBarMove(e: MouseEvent) {
  if (winMode.value === 'lock') return
  lastInteract = Date.now()
  const el = barEl.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
  const nowSec = Date.now() / 1000 - timeline.value.start
  const sec = Math.min(ratio * DAY_SEC, Math.max(0, nowSec))
  winMode.value = 'follow'
  winCenter.value = sec
  hoverSeg.value = findSegAt(sec)
}
function onBarClick(e: MouseEvent) {
  lastInteract = Date.now()
  if (winMode.value === 'lock') {
    winMode.value = 'auto'
    winCenter.value = null
  } else {
    const el = barEl.value
    if (el) {
      const rect = el.getBoundingClientRect()
      const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
      const nowSec = Date.now() / 1000 - timeline.value.start
      winMode.value = 'lock'
      winCenter.value = Math.min(ratio * DAY_SEC, Math.max(0, nowSec))
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

const winWindow = computed(() => {
  const t = timeline.value
  const nowSec = t.now - t.start
  let winStart: number, winEnd: number
  if (winMode.value === 'auto' || winCenter.value === null) {
    const lastAt = Math.min(nowSec, Math.max(0, (phone.value?.last_at ?? t.now) - t.start))
    winEnd = lastAt
    winStart = Math.max(0, winEnd - 3600)
  } else {
    winStart = Math.max(0, winCenter.value - 1800)
    winEnd = Math.min(nowSec, winCenter.value + 1800)
    if (winEnd <= winStart) winEnd = winStart + 1
  }
  const segs = t.acts
    .filter((s) => (s.start - t.start) < winEnd && (s.end - t.start) > winStart)
    .map((s) => {
      const w = ((Math.min(s.end, t.start + winEnd) - Math.max(s.start, t.start + winStart)) / (winEnd - winStart)) * 100
      return { ...s, w }
    })
    .sort((a, b) => a.start - b.start)
  const raw: typeof segs = []
  for (const s of segs) {
    const prev = raw[raw.length - 1]
    if (prev && s.start - prev.end > 0 && s.start - prev.end < 90) {
      prev.end = s.start
      prev.w = ((Math.min(prev.end, t.start + winEnd) - Math.max(prev.start, t.start + winStart)) / (winEnd - winStart)) * 100
    }
    raw.push(s)
  }
  const sorted = [...raw].sort((a, b) => a.start - b.start)
  const filled = sorted.map((s) => {
    const left = Math.max(0, ((s.start - t.start - winStart) / (winEnd - winStart)) * 100)
    return { ...s, left }
  })
  let futureLeft = 0
  let futureW = 0
  if (winEnd > nowSec) {
    futureLeft = ((Math.max(winStart, nowSec) - winStart) / (winEnd - winStart)) * 100
    futureW = ((winEnd - Math.max(winStart, nowSec)) / (winEnd - winStart)) * 100
  }
  return { winStart, winEnd, segs: filled, mode: winMode.value, futureLeft, futureW }
})

function checkIdleAuto() {
  if (Date.now() - lastInteract > AUTO_IDLE_MS && winMode.value !== 'auto') {
    winMode.value = 'auto'
    winCenter.value = null
    hoverSeg.value = null
  }
}

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
  return `${CAT_LABELS[s.type] ?? s.type} ${fmtTime(s.start)}–${fmtTime(s.end)}（${dur}）${s.label ? ` · ${s.label}` : ''}`
}

let idleTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  idleTimer = setInterval(checkIdleAuto, 30000)
})
onUnmounted(() => {
  if (idleTimer) clearInterval(idleTimer)
})

function refresh() {
  // 2026-08-08：接口未接，留空（MOCK）；接入后 fetch hub /api/v1/phone-state 写 phone/lastTs
}
</script>

<template>
  <div class="phone-card">
    <div class="head">
      <div class="head-left">
        <span class="dot" :class="phone && !stale ? 'up' : 'down'" title="手机在线状态（30s 心跳）" />
        <span class="title">📱 手机状态</span>
      </div>
      <button class="refresh-btn" :disabled="loading || !phone" title="刷新" @click="refresh">⟳</button>
    </div>

    <div v-if="error" class="unavailable">{{ error }}</div>

    <template v-else-if="phone">
      <!-- ② 当前应用 -->
      <ul class="procs">
        <li v-if="focusRow">
          <span class="dot focus" />
          <span class="pname">{{ focusRow.name }}</span>
          <span class="pcat">{{ CAT_LABELS[focusRow.cat] ?? focusRow.cat }}</span>
        </li>
        <li v-else class="empty-row">当前应用 —</li>
      </ul>

      <!-- ③ 资源占用条 -->
      <div class="bars">
        <div v-for="b in resourceBars" :key="b.label" class="bar-row">
          <span class="bar-label">{{ b.label }}</span>
          <div class="bar"><div class="bar-fill" :style="{ width: Math.min(100, b.pct) + '%' }" /></div>
          <span class="bar-val">{{ b.pct }}%{{ b.sub ? ' ' + b.sub : '' }}</span>
        </div>
      </div>

      <!-- 手机特有：勿扰 / 网络 / 位置 -->
      <div class="grid">
        <div
          v-for="b in basics"
          :key="b.label"
          class="cell"
          :class="{ clickable: b.toggle || b.track }"
          @click="b.toggle ? toggleDnd() : b.track ? toggleTrack() : null"
        >
          <span class="c-label">{{ b.label }}</span>
          <span class="c-right">
            <span class="c-val">{{ b.value }}</span>
            <span v-if="b.toggle" class="switch" :class="(dndOverride ?? phone.dnd) ? 'on' : 'off'">⌁</span>
            <span v-else-if="b.track" class="track-btn">{{ showTrack ? '▲' : '📍' }}</span>
          </span>
        </div>
      </div>

      <div v-if="showTrack" class="track-panel">
        <div v-if="phone.track?.length" class="track-hint">轨迹 {{ phone.track.length }} 点（点击打开地图）</div>
        <div v-else class="track-hint">暂无轨迹数据</div>
      </div>

      <!-- ④ 圆环 + 图例 -->
      <div class="ring-block">
        <div class="ring" :style="ringItems.total ? { background: `conic-gradient(${ringItems.items.map((it, idx) => `${CAT_COLORS[it.k] ?? '#888'} ${idx === 0 ? 0 : ringItems.items.slice(0, idx).reduce((a, x) => a + x.pct, 0) * 100}% ${ringItems.items.slice(0, idx + 1).reduce((a, x) => a + x.pct, 0) * 100}%`).join(',')})` } : { background: '#333' }">
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

      <!-- ⑤ 活动条 + 三态预览窗 -->
      <div class="timeline-wrap">
        <div ref="barEl" class="timeline" @mousemove="onBarMove" @click="onBarClick" @mouseleave="onBarLeave">
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
            :style="{
              left: ((s.start - timeline.start) / timeline.total) * 100 + '%',
              width: timeline.widths[i] + '%',
              background: CAT_COLORS[s.type] ?? '#888',
            }"
          />
          <div v-if="timeline.now < timeline.end" class="tseg future" :style="{ left: ((timeline.now - timeline.start) / timeline.total) * 100 + '%', width: Math.max(0.3, ((timeline.end - timeline.now) / timeline.total) * 100) + '%' }" />
        </div>
        <div class="tlabel"><span>0:00</span><span>24:00</span></div>

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
            <div v-if="winWindow.futureW > 0" class="zseg future" :style="{ left: winWindow.futureLeft + '%', width: winWindow.futureW + '%' }" />
          </div>
          <div class="zoom-detail">{{ hoverSeg ? fmtSeg(hoverSeg) : winSummary }}</div>
        </div>
      </div>
    </template>

    <div v-else class="unavailable">手机端待接入</div>

    <div class="foot">更新于 {{ lastTs ? new Date(lastTs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }} · 屏幕 {{ phone?.screen_today_min != null ? phone.screen_today_min + ' 分钟' : '—' }}</div>
  </div>
</template>

<style scoped>
.phone-card {
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
.head-left {
  display: flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot.up { background: #22c55e; }
.dot.down { background: #ef4444; }
.dot.focus { background: #22c55e; }
.title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary);
}
.refresh-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 13px;
  padding: 2px 4px;
}
.refresh-btn:disabled { opacity: 0.4; cursor: default; }
.unavailable {
  font-size: 11px;
  color: var(--text-muted);
  padding: 6px 0;
}
.procs {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
}
.procs li {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
}
.procs .pname {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.procs .pcat {
  font-size: 10px;
  color: var(--text-muted);
}
.empty-row {
  font-size: 11px;
  color: var(--text-muted);
}
.bars {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.bar-label { width: 30px; font-size: 10px; color: var(--text-muted); }
.bar {
  flex: 1;
  height: 5px;
  border-radius: 3px;
  background: var(--bg-surface, rgba(0, 0, 0, 0.3));
  overflow: hidden;
}
.bar-fill { height: 100%; border-radius: 3px; background: var(--accent, #06b6d4); }
.bar-val {
  width: 74px;
  text-align: right;
  font-size: 10px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px 10px;
  margin-bottom: 8px;
}
.cell {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg-surface, rgba(0, 0, 0, 0.2));
  border-radius: 6px;
  padding: 4px 8px;
}
.cell.clickable { cursor: pointer; }
.cell.clickable:hover { background: var(--bg-surface, rgba(255, 255, 255, 0.06)); }
.c-label {
  font-size: 10px;
  color: var(--text-muted);
}
.c-right {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}
.c-val {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-primary);
  font-family: 'Courier New', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.switch {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  border: 1px solid var(--border-main);
  color: var(--text-muted);
  flex-shrink: 0;
}
.switch.on { background: #22c55e; border-color: #22c55e; color: #fff; }
.switch.off { background: transparent; }
.track-btn { font-size: 10px; }
.track-panel {
  margin-bottom: 8px;
  padding: 6px 8px;
  background: var(--bg-surface, rgba(0, 0, 0, 0.2));
  border-radius: 6px;
}
.track-hint {
  font-size: 10px;
  color: var(--text-muted);
}
.ring-block {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 8px 0;
}
.ring {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  flex-shrink: 0;
}
.ring-hole {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--bg-elevated);
  margin: 6px auto;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: 'Courier New', monospace;
}
.ring-hole small {
  font-size: 8px;
  color: var(--text-tertiary, var(--text-muted));
  margin-left: 1px;
  font-weight: 400;
}
.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.legend li {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
}
.legend .empty { color: var(--text-muted); }
.swatch {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex-shrink: 0;
}
.lk { flex: 1; color: var(--text-secondary); }
.lv { color: var(--text-muted); font-family: 'Courier New', monospace; }
.timeline {
  position: relative;
  height: 12px;
  border-radius: 3px;
  background: #333;
  overflow: hidden;
  cursor: pointer;
}
.tseg {
  position: absolute;
  top: 0;
  height: 100%;
}
.tseg.future { background: #000 !important; }
.hover-mask {
  position: absolute;
  top: 0;
  height: 100%;
  border: 1px solid #fff;
  background: rgba(255, 255, 255, 0.12);
  z-index: 2;
  pointer-events: none;
  border-radius: 3px;
}
.tlabel {
  display: flex;
  justify-content: space-between;
  font-size: 9px;
  color: var(--text-muted);
  margin-top: 2px;
}
.zoom {
  margin-top: 6px;
  background: var(--bg-surface, rgba(0, 0, 0, 0.25));
  border-radius: 6px;
  padding: 6px 8px;
}
.zoom-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 9px;
  color: var(--text-muted);
}
.zoom-mode {
  font-size: 9px;
  color: var(--text-tertiary, var(--text-muted));
}
.zoom-mode.follow { color: var(--accent, #06b6d4); }
.zoom-mode.lock { color: #eab308; }
.zoom-back {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 10px;
  padding: 0 2px;
}
.zoom-bar {
  position: relative;
  display: block;
  height: 6px;
  border-radius: 3px;
  background: #3a3a3a;
  margin-top: 4px;
  overflow: hidden;
}
.zseg {
  position: absolute;
  top: 0;
  height: 100%;
}
.zseg.future { background: #000 !important; }
.zoom-detail {
  margin-top: 4px;
  font-size: 10px;
  color: var(--text-secondary);
  white-space: pre-line;
  line-height: 1.4;
}
.foot {
  margin-top: 8px;
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
</style>

<script setup lang="ts">
/**
 * 手机状态卡（v3 完整版）— 仿电脑状态卡：30s 采样上报（位置低频）。
 * 接口留空：数据契约已定，接入 hub `GET /api/v1/phone-state` 后填 refresh() 即可。
 *
 * 布局（完全参考电脑状态卡）：
 *   ① 标题栏（红绿点 = 30s 心跳新鲜度）
 *   ② 前台 App（焦点 dot + 名称 + 分类）
 *   ③ 基础格区：电量(含充电方式) / 勿扰(可开关) / 网络(含 WiFi 名) / 位置(点开地图+当天轨迹)
 *   ④ 当日圆环 + 图例（今日 App 分类分布）——同电脑卡
 *   ⑤ 本日活动条（主条 + 三态预览窗 AUTO/FOLLOW/LOCK）——同电脑卡
 *   ⑥ foot（更新时间）
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'

/** 未来 hub /api/v1/phone-state 返回结构（接口暂留空，先定契约） */
interface Seg { start: number; end: number; type: string; label?: string }
interface TrackPt { t: number; lat: number; lng: number }
interface PhoneState {
  last_at?: number                            // 最近一次 30s 心跳
  battery?: number                            // 电量 %
  charging?: { active: boolean; method?: 'wired' | 'wireless' }  // 充电中 + 方式（有线/无线）
  dnd?: boolean                               // 勿扰（电脑可开关）
  network?: { kind: 'wifi' | 'mobile' | 'offline'; ssid?: string }  // 网络 + WiFi 名称
  loc_bucket?: 'home' | 'work' | 'out'        // 位置桶（低频上报）
  track?: TrackPt[]                           // 当天轨迹（低频上报；点开地图显示）
  focus_app?: { name: string; cat: string } | null   // 前台 App
  today_activities?: Seg[]                    // 今日活动段（仿电脑 sensor）
  today_categories?: Record<string, number>   // 今日分类时长秒（圆环）
}

/** 2026-08-08：MOCK 数据——接口未接，先用模拟数据把 UI 完整渲染出来；
 *  手机端接入后：删掉初始 mock，在 refresh() 里 fetch hub /api/v1/phone-state 写 phone/lastTs */
function mockActivities(): Seg[] {
  const t0 = new Date(); t0.setHours(0, 0, 0, 0)
  const base = t0.getTime() / 1000
  const now = Date.now() / 1000
  const plan: Array<[number, number, string, string?]> = [
    [8 * 3600, 8 * 3600 + 1500, 'social', '微信'],
    [8 * 3600 + 1500, 9 * 3600 + 600, 'video', '哔哩哔哩'],
    [10 * 3600, 11 * 3600, 'game', '原神'],
    [11 * 3600, 12 * 3600, 'social', 'QQ'],
    [12 * 3600, 13 * 3600, 'rest', undefined],
    [13 * 3600, 14 * 3600 + 900, 'reading', '番茄小说'],
    [15 * 3600, 16 * 3600, 'tool', '文件管理'],
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
  dnd: true,
  network: { kind: 'wifi', ssid: '我家WiFi' },
  loc_bucket: 'home',
  track: mockTrack,
  focus_app: { name: '微信', cat: 'social' },
  today_activities: mockActivities(),
  today_categories: { social: 7800, video: 4200, game: 3600, reading: 5400, rest: 3600, tool: 3600 },
})
const lastTs = ref(Date.now())
const error = ref('')
const loading = ref(false)

/** 勿扰开关（本地 UI；未来调 bus 下发指令到手机） */
const dndOverride = ref<boolean | null>(null)
function toggleDnd() {
  if (!phone.value) return
  const cur = dndOverride.value ?? phone.value.dnd ?? false
  dndOverride.value = !cur
  // TODO(手机端接入后)：POST bus 下发 dnd 开关指令到手机端
}

/** 位置格点击 → 展开/收起当天轨迹面板 */
const showTrack = ref(false)
function toggleTrack() {
  showTrack.value = !showTrack.value
}

/** 30s 上报 → 5 分钟无心跳视为离线（与电脑卡一致） */
const stale = computed(() => {
  if (!phone.value?.last_at) return true
  return Date.now() / 1000 - phone.value.last_at > 5 * 60
})

/** 手机 App 分类（今日圆环/图例/活动条） */
const CAT_LABELS: Record<string, string> = {
  social: '社交', video: '影音', game: '游戏', shopping: '购物',
  tool: '工具', reading: '阅读', unknown: '其他', rest: '休息', away: '休息',
  offline: '离线',
}
const CAT_COLORS: Record<string, string> = {
  social: '#a855f7', video: '#ef4444', game: '#f97316', shopping: '#ec4899',
  tool: '#94a3b8', reading: '#06b6d4', unknown: '#64748b', rest: '#22c55e', away: '#22c55e',
  offline: '#9ca3af',
}

const LOC_LABELS: Record<string, string> = { home: '家', work: '公司', out: '外出' }
const NET_LABELS: Record<string, string> = { wifi: 'Wi-Fi', mobile: '蜂窝', offline: '离线' }

/** 基础格区四项 */
const basics = computed(() => {
  const p = phone.value
  return [
    {
      label: '电量',
      value: p?.battery != null
        ? `${p.battery}%${p.charging?.active ? (p.charging.method === 'wireless' ? ' ⚡无线' : ' ⚡有线') : ''}`
        : '—',
      clickable: false,
    },
    {
      label: '勿扰',
      value: (dndOverride.value ?? p?.dnd) != null ? ((dndOverride.value ?? p?.dnd) ? '开启' : '关闭') : '—',
      clickable: true,
      toggle: true,
    },
    {
      label: '网络',
      value: p?.network
        ? `${NET_LABELS[p.network.kind] ?? p.network.kind}${p.network.kind === 'wifi' && p.network.ssid ? `·${p.network.ssid}` : ''}`
        : '—',
      clickable: false,
    },
    {
      label: '位置',
      value: p?.loc_bucket ? (LOC_LABELS[p.loc_bucket] ?? p.loc_bucket) : '—',
      clickable: true,
      track: true,
    },
  ]
})

/** 前台 App 行 */
const focusRow = computed(() => phone.value?.focus_app ?? null)

/** 当日圆环（同电脑卡） */
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

// ── 活动条（完全参考电脑卡：主条 + 三态预览窗）──
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
  const sec = ratio * DAY_SEC
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
      winMode.value = 'lock'
      winCenter.value = ratio * DAY_SEC
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
    winEnd = Math.min(DAY_SEC, winCenter.value + 1800)
  }
  const segs = t.acts
    .filter((s) => (s.start - t.start) < winEnd && (s.end - t.start) > winStart)
    .map((s) => {
      const w = ((Math.min(s.end, t.start + winEnd) - Math.max(s.start, t.start + winStart)) / (winEnd - winStart)) * 100
      return { ...s, w }
    })
    .sort((a, b) => a.start - b.start)
  const filled: typeof segs = []
  for (const s of segs) {
    const prev = filled[filled.length - 1]
    if (prev && s.start - prev.end > 0 && s.start - prev.end < 90) {
      prev.end = s.start
      prev.w = ((Math.min(prev.end, t.start + winEnd) - Math.max(prev.start, t.start + winStart)) / (winEnd - winStart)) * 100
    }
    filled.push(s)
  }
  return { winStart, winEnd, segs: filled, mode: winMode.value }
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
  // 2026-08-08：接口未接，留空；接入后 fetch hub /api/v1/phone-state 并写 phone/lastTs
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
      <!-- ② 前台 App -->
      <ul class="procs">
        <li v-if="focusRow">
          <span class="dot focus" />
          <span class="pname">{{ focusRow.name }}</span>
          <span class="pcat">{{ CAT_LABELS[focusRow.cat] ?? focusRow.cat }}</span>
        </li>
        <li v-else class="empty-row">前台 App —</li>
      </ul>

      <!-- ③ 基础格区 -->
      <div class="grid">
        <div
          v-for="b in basics"
          :key="b.label"
          class="cell"
          :class="{ clickable: b.clickable }"
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

      <!-- 位置：当天轨迹面板（接口留空；未来 track 数组画折线 + 打开地图） -->
      <div v-if="showTrack" class="track-panel">
        <div v-if="phone.track?.length" class="track-hint">轨迹 {{ phone.track.length }} 点（点击打开地图）</div>
        <div v-else class="track-hint">暂无轨迹数据（手机端接入后显示当天轨迹）</div>
        <!-- future: <svg> 按 track 经纬度画折线迷你地图；点击调 open(地图链接) </svg> -->
      </div>

      <!-- ④ 圆环 + 图例（同电脑卡） -->
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

      <!-- ⑤ 活动条 + 三态预览窗（同电脑卡） -->
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
              :style="{ width: s.w + '%', background: CAT_COLORS[s.type] ?? '#888' }"
              @mouseenter="hoverSeg = s"
            />
          </div>
          <div class="zoom-detail">{{ hoverSeg ? fmtSeg(hoverSeg) : winSummary }}</div>
        </div>
      </div>
    </template>

    <div v-else class="unavailable">
      手机端待接入
      <div class="hint">接入后显示：前台 App / 电量(有线·无线) / 勿扰(可开关) / 网络(WiFi名) / 位置(轨迹地图) / 今日分类 / 活动时间线</div>
    </div>

    <div class="foot">更新于 {{ lastTs ? new Date(lastTs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }}</div>
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
.hint {
  margin-top: 4px;
  font-size: 10px;
  color: var(--text-tertiary, var(--text-muted));
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
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
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
}
.c-val {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  font-family: 'Courier New', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.switch {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  border: 1px solid var(--border-main);
  color: var(--text-muted);
}
.switch.on { background: #22c55e; border-color: #22c55e; color: #fff; }
.switch.off { background: transparent; }
.track-btn { font-size: 11px; }
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
  display: flex;
  gap: 0;
  height: 6px;
  border-radius: 3px;
  background: #3a3a3a;
  margin-top: 4px;
  overflow: hidden;
}
.zseg {
  height: 100%;
  flex: 0 0 auto;
}
.zoom-detail {
  margin-top: 4px;
  font-size: 10px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.foot {
  margin-top: 8px;
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
</style>

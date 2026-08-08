<script setup lang="ts">
/** 手机状态卡（2026-08-08：基于电脑卡布局，数据源全部改为手机 phone mock）。
 *  布局：①当前应用 ②占用条(CPU/内存/磁盘/电量) ③当日圆环+图例 ④本日活动条+三态预览窗 ⑤foot(屏幕分钟)。
 *  接口未接（MOCK 渲染）；接入 hub GET /api/v1/phone-state 后填 refresh()。 */
import { ref, computed, onMounted, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { open as dialogOpen } from '@tauri-apps/plugin-dialog'
import { getPhoneState, getPhoneTrack, getPhoneNotifies, type PhoneNotifyItem } from '@/services/api'

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

/** 2026-08-08：手机端数据契约（hub GET /api/v1/phone-state）——手机卡全部数据源 */
interface PhoneState {
  last_at?: number                            // 30s 心跳
  battery?: number                            // 电量 %
  charging?: { active: boolean; method?: 'wired' | 'wireless' }
  cpu_pct?: number                            // CPU 占用 %
  ram_pct?: number                            // 内存占用 %
  storage_pct?: number                        // 存储占用 %
  dnd?: boolean                               // 勿扰
  network?: { kind: 'wifi' | 'mobile' | 'offline'; ssid?: string }
  loc_bucket?: 'home' | 'work' | 'out'        // 位置桶（低频）
  track?: Array<{ t: number; lat: number; lng: number; accuracy?: number }>  // 当天轨迹（低频）
  focus_app?: { name: string; cat: string } | null        // 当前应用
  screen_today_min?: number                   // 今日屏幕使用分钟
  volume_music?: number                       // 媒体音量（2026-08-08：App 上报，重做音量系统）
  volume_ring?: number                        // 铃声音量
  today_activities?: Seg[]
  today_categories?: Record<string, number>
}

const state = ref<SensorState | null>(null)

/** 2026-08-08：手机活动条数据源（MOCK）——接口未接，先用模拟手机活动段渲染；接入后 fill refresh() */
// 2026-08-08：手机数据全部来自 hub（无 MOCK）——refresh 拉取后填充；拉取前显示空态
const phone = ref<PhoneState | null>(null)
const error = ref('')
const lastTs = ref(0)
const loading = ref(false)
const hoverSeg = ref<Seg | null>(null)      // 副条 hover 命中的段
const barEl = ref<HTMLElement | null>(null)

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
  // 手机分类（2026-08-08：配色方案，尽量贴合电脑卡语义）
  social: '社交', shopping: '购物', reading: '阅读', travel: '出行',
  finance: '金融', life: '生活', system: '系统',
}
const CAT_COLORS: Record<string, string> = {
  coding: '#3b82f6', browsing: '#f59e0b', communication: '#a855f7', game: '#f97316',
  video: '#ef4444', document: '#0d9488', design: '#ec4899', writing: '#84cc16',
  meeting: '#8b5cf6', tool: '#94a3b8', unknown: '#64748b', rest: '#22c55e', away: '#22c55e',
  multi: '#eab308', offline: '#9ca3af', star_rail: '#f97316', firefly: '#06b6d4',
  // 手机分类（2026-08-08）
  social: '#a855f7', shopping: '#ec4899', reading: '#06b6d4', travel: '#3b82f6',
  finance: '#eab308', life: '#84cc16', system: '#64748b',
}

const stale = computed(() => {
  if (!phone.value?.last_at) return false
  return Date.now() / 1000 - phone.value.last_at > 5 * 60
})
const sitting = computed(() => {
  const s = phone.value?.screen_today_min
  return typeof s === 'number' && s >= 1 ? Math.round(s) : 0
})
// 占用条（手机卡）：CPU / 内存 / 磁盘 / 电量（2026-08-08 数据源全改 phone）
const resourceBars = computed(() => {
  const p = phone.value
  const bars: Array<{ label: string; pct: number; dash?: boolean }> = []
  if (p) {
    // 2026-08-08：CPU 需 Shizuku 提权采集——未授权时 App 不上报，显示「—」而非 0
    if (p.cpu_pct != null) bars.push({ label: 'CPU', pct: p.cpu_pct })
    else bars.push({ label: 'CPU', pct: 0, dash: true })
    bars.push({ label: '内存', pct: p.ram_pct ?? 0 })
    bars.push({ label: '磁盘', pct: p.storage_pct ?? 0 })
    // 电量三态 emoji：🔌有线充电 / ⚡无线充电 / 🔋未充电
    const emoji = !p.charging?.active ? '🔋' : (p.charging.method === 'wired' ? '🔌' : '⚡')
    bars.push({ label: `电量${emoji}`, pct: p.battery ?? 0 })
  }
  return bars
})
// ── 2026-08-08：音量条（可滑动 + emoji 一键静音/30%）——走 set_volume 广播 → App AudioManager ──
const volumeSlider = ref(0)
const volumeBusy = ref(false)
const volumeLabel = computed(() => `音量${volumeSlider.value === 0 ? '🔇' : '🔊'}`)
// App 上报校正（30s 刷新时同步滑块，避免本地值漂移）
watch(
  () => phone.value?.volume_music,
  (v) => {
    if (v != null && !volumeBusy.value) volumeSlider.value = Math.max(0, Math.min(15, v))
  },
)
let volTimer: ReturnType<typeof setTimeout> | null = null
async function sendVolume(v: number) {
  volumeBusy.value = true
  try {
    await invoke('phone_command', { action: 'set_volume', stream: 'music', value: v })
  } catch (e) {
    actionMsg.value = `✗ 音量设置失败: ${e}`
  } finally {
    volumeBusy.value = false
  }
}
// 滑块拖动：本地即时 + 停止 300ms 防抖后发送
function onVolumeInput() {
  if (volTimer) clearTimeout(volTimer)
  volTimer = setTimeout(() => sendVolume(volumeSlider.value), 300)
}
// 点击 emoji：一键静音 ↔ 30%（15×30%≈5）
async function toggleVolumeMute() {
  const target = volumeSlider.value > 0 ? 0 : 5
  volumeSlider.value = target
  if (volTimer) clearTimeout(volTimer)
  await sendVolume(target)
}
// 当前应用行（2026-08-08：数据源改 phone.focus_app）
const procRows = computed(() => {
  const rows: Array<{ name: string; cat: string; focus: boolean }> = []
  const f = phone.value?.focus_app
  if (f) {
    rows.push({ name: f.name, cat: f.cat, focus: true })
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
  const c = phone.value?.today_categories ?? {}
  const items = Object.entries(c).filter(([, v]) => v >= 60)
  const total = items.reduce((a, [, v]) => a + v, 0)
  return { items: items.map(([k, v]) => ({ k, v, pct: total ? v / total : 0 })), total }
})
// 活动条（0:00 → 24:00 全长，未来时段黑色）
const timeline = computed(() => {
  // 2026-08-08：活动条数据源改手机（phone.today_activities 优先）；配色已换手机分类色
  // 过滤零宽段 + 离线段（offline 当无记录显示，不占数据段）
  const acts = (phone.value?.today_activities ?? state.value?.today_activities ?? []).filter((s) => s.end > s.start && s.type !== 'offline')
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
    const lastAt = Math.min(nowSec, Math.max(0, ((phone.value?.last_at ?? state.value?.last_at) ?? t.now) - t.start))
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

function refresh() {
  // 2026-08-08：接 hub（经 bus /api/v1/phone-state + phone-track）真实数据；失败显示错误
  loading.value = true
  Promise.all([
    getPhoneState(),
    getPhoneTrack(24, 200).catch(() => null),
    getPhoneNotifies(10).catch(() => null),
  ])
    .then(([st, tr, nt]) => {
      if (nt?.notifies) phoneNotifies.value = nt.notifies
      const r = st.raw ?? {}
      phone.value = {
        last_at: st.at ?? Date.now() / 1000,
        battery: st.battery,
        volume_music: (st.raw as any)?.volume_music,
        volume_ring: (st.raw as any)?.volume_ring,
        charging: { active: !!r.charging, method: 'wireless' },
        cpu_pct: r.cpu_pct ?? 0,
        ram_pct: r.ram_pct,
        storage_pct: r.storage_pct,
        dnd: !!r.dnd,
        network: {
          kind: (r.network?.kind === 'cellular' ? 'mobile' : r.network?.kind ?? 'offline') as 'wifi' | 'mobile' | 'offline',
          ssid: r.network?.ssid,
        },
        loc_bucket: undefined,
        track: (tr?.track ?? []).map((p) => ({ t: p.at, lat: p.lat, lng: p.lng, accuracy: p.accuracy })),
        // 2026-08-08：hub 已透出分类后的 focus_app（{name,pkg,cat}）与真实使用段/聚合
        focus_app: st.focus_app?.name ? { name: st.focus_app.name, cat: st.focus_app.cat ?? 'unknown' } : null,
        screen_today_min: r.screen_today_min,
        today_activities: (st.today_activities ?? []).filter((s) => s.start != null && s.end != null && s.type) as Seg[],
        today_categories: st.today_categories ?? undefined,
      }
      lastTs.value = Date.now()
      error.value = ''
    })
    .catch((e) => {
      error.value = `手机数据暂不可用: ${e}`
    })
    .finally(() => {
      loading.value = false
    })
}

// ── 2026-08-08：快捷面板——按钮走 Rust phone_command（无线 adb 直连手机）──
interface PhoneAction {
  key: string
  icon: string
  label: string
  hint: string
  local?: boolean
  disabled?: boolean
}
const PHONE_ACTIONS: PhoneAction[] = [
  // ── 手机控制（App 驱动：AudioManager/NotificationManager/CameraManager）──
  { key: 'sound_toggle', icon: '🔔', label: '声音', hint: '响铃/静音/震动循环' },
  { key: 'dnd_toggle', icon: '🌙', label: '勿扰', hint: '勿扰开关' },
  { key: 'torch', icon: '🔦', label: '手电', hint: 'App 手电筒（CameraManager）' },
  // ── 屏幕捕获（adb）──
  { key: 'screenshot', icon: '📸', label: '截图', hint: '截图存电脑' },
  { key: 'screenrecord', icon: '🎥', label: '录屏', hint: '录 15 秒存电脑' },
  { key: 'scrcpy', icon: '🖥️', label: '投屏', hint: 'scrcpy 无线投屏' },
  // ── 数据查看（前端直连 hub）──
  { key: 'pull_files', icon: '📁', label: '文件', hint: '电脑端浏览手机文件（拖拽传输）', local: true },
  { key: 'notify', icon: '💬', label: '通知', hint: '手机通知转发（hub）', local: true },
  { key: 'track', icon: '🗺️', label: '轨迹', hint: '查看今日轨迹', local: true },
  // ── 系统工具 ──
  { key: 'shizuku', icon: '🔐', label: 'Shizuku', hint: '激活 Shizuku' },
]
const soundState = ref<'ring' | 'silent' | 'vibrate'>('ring')
const SOUND_META: Record<string, { icon: string; label: string }> = {
  ring: { icon: '🔔', label: '响铃' },
  silent: { icon: '🔕', label: '静音' },
  vibrate: { icon: '📳', label: '震动' },
}
const busyKey = ref('')
const actionMsg = ref('')
const showTrack = ref(false)

async function runPhoneAction(a: { key: string; label: string; local?: boolean; disabled?: boolean }) {
  if (a.disabled) return
  if (a.local) {
    if (a.key === 'track') {
      showTrack.value = !showTrack.value
    } else if (a.key === 'pull_files') {
      showFiles.value = !showFiles.value
      if (showFiles.value) fsList(fsPath.value)
    } else if (a.key === 'notify') {
      showNotifies.value = !showNotifies.value
    }
    return
  }
  busyKey.value = a.key
  actionMsg.value = ''
  try {
    const res = await invoke<string>('phone_command', { action: a.key })
    if (a.key === 'sound_toggle' && (res === 'ring' || res === 'silent' || res === 'vibrate')) {
      soundState.value = res
      actionMsg.value = `声音 ✓ 已切到${SOUND_META[res].label}`
    } else {
      actionMsg.value = `${a.label} ✓ ${res}`
    }
  } catch (e) {
    actionMsg.value = `${a.label} ✗ ${e}`
  } finally {
    busyKey.value = ''
  }
}

/** 轨迹迷你图（SVG 按经纬度归一化连线；数据 mock，接口接入后 phone.track 替换） */

/** 2026-08-08：手机通知区（hub 最近转发） */
const showNotifies = ref(false)
const phoneNotifies = ref<PhoneNotifyItem[]>([])
const notifyIcon: Record<string, string> = { call: '📞', chat: '💬', sms: '✉️', alarm: '⏰', notify: '🔔' }

/** 2026-08-08：文件浏览器（桌宠内嵌，adb 驱动）——列表 / 下载 / 拖放上传 */
const showFiles = ref(false)
const fsPath = ref('/sdcard')
const fsEntries = ref<Array<{ name: string; dir: boolean; size: number }>>([])
const fsLoading = ref(false)
const fsMsg = ref('')

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB'
  return n + ' B'
}
function fmtNotifyTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
async function fsList(path: string) {
  fsLoading.value = true
  fsMsg.value = ''
  try {
    fsEntries.value = await invoke<Array<{ name: string; dir: boolean; size: number }>>('phone_fs_list', { path })
    fsPath.value = path
  } catch (e) {
    fsMsg.value = `✗ ${e}`
  } finally {
    fsLoading.value = false
  }
}
function fsOpen(name: string, dir: boolean) {
  if (!dir) return
  fsList(fsPath.value.endsWith('/') ? fsPath.value + name : fsPath.value + '/' + name)
}
function fsUp() {
  const p = fsPath.value
  const idx = p.lastIndexOf('/')
  if (idx > 0) fsList(p.slice(0, idx))
}
async function fsDownload(name: string, dir: boolean) {
  if (dir) return
  const dirSel = await dialogOpen({ directory: true })
  if (!dirSel || Array.isArray(dirSel)) return
  try {
    const res = await invoke<string>('phone_fs_pull', { remote: fsPath.value + '/' + name, destDir: String(dirSel) })
    fsMsg.value = `⤓ ${res}`
  } catch (e) {
    fsMsg.value = `✗ ${e}`
  }
}
/** 2026-08-08：Tauri 拖放（OS 文件 → 路径）——Tauri 2 接管了 HTML5 drop，必须用 onDragDropEvent */
async function fsPushPaths(paths: string[]) {
  fsMsg.value = ''
  for (const p of paths) {
    const name = p.split(/[\\/]/).pop() || 'file'
    try {
      const res = await invoke<string>('phone_fs_push_path', { local: p, remote: fsPath.value + '/' + name })
      fsMsg.value = `⤒ ${name} 已上传`
      console.log('push ok', res)
    } catch (err) {
      fsMsg.value = `✗ ${name} ${err}`
    }
  }
  fsList(fsPath.value)
}

// ── 2026-08-08d：Leaflet 轨迹地图（成熟库：平滑拖动/缩放/惯性/瓦片缓存）——高德瓦片 GCJ-02 ──
// 2026-08-08e：手机 GPS（Android 原生 Location）返回 WGS-84，高德瓦片是 GCJ-02（火星坐标）——
// 直接画会偏移几百米，需先做 WGS-84 → GCJ-02 转换。
function wgs84ToGcj02(lat: number, lng: number): [number, number] {
  const a = 6378245.0
  const ee = 0.00669342162296594323
  if (lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271) return [lat, lng] // 境外不转
  const tl = (x: number, y: number) => {
    let r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x))
    r += ((20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0) / 3.0
    r += ((20.0 * Math.sin(y * Math.PI) + 40.0 * Math.sin((y / 3.0) * Math.PI)) * 2.0) / 3.0
    r += ((160.0 * Math.sin((y / 12.0) * Math.PI) + 320.0 * Math.sin((y * Math.PI) / 30.0)) * 2.0) / 3.0
    return r
  }
  const tg = (x: number, y: number) => {
    let r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x))
    r += ((20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0) / 3.0
    r += ((20.0 * Math.sin(x * Math.PI) + 40.0 * Math.sin((x / 3.0) * Math.PI)) * 2.0) / 3.0
    r += ((150.0 * Math.sin((x / 12.0) * Math.PI) + 300.0 * Math.sin((x / 30.0) * Math.PI)) * 2.0) / 3.0
    return r
  }
  let dLat = tl(lng - 105.0, lat - 35.0)
  let dLng = tg(lng - 105.0, lat - 35.0)
  const radLat = (lat / 180.0) * Math.PI
  let magic = Math.sin(radLat)
  magic = 1 - ee * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  dLat = (dLat * 180.0) / (((a * (1 - ee)) / (magic * sqrtMagic)) * Math.PI)
  dLng = (dLng * 180.0) / ((a / sqrtMagic) * Math.cos(radLat) * Math.PI)
  return [lat + dLat, lng + dLng]
}

let trackMap: L.Map | null = null
const trackMapEl = ref<HTMLElement | null>(null)

function initLeafletMap() {
  const el = trackMapEl.value
  const pts = phone.value?.track ?? []
  if (!el || pts.length < 1) return
  // 2026-08-08：过滤漂移点——定位精度 >100m 的（纯基站/网络定位）丢弃；保留至少 2 点保证连线
  const clean = pts.filter((p) => p.accuracy == null || p.accuracy <= 100)
  const use = clean.length >= 2 ? clean : pts
  if (trackMap) {
    trackMap.remove()
    trackMap = null
  }
  // WGS-84 → GCJ-02：地图（高德）与轨迹点统一到火星坐标
  const toGcj = (lat: number, lng: number): [number, number] => wgs84ToGcj02(lat, lng)
  const center: [number, number] = toGcj(use[use.length - 1].lat, use[use.length - 1].lng)
  trackMap = L.map(el, { zoomControl: false, attributionControl: false }).setView(center, 14)
  L.tileLayer(
    'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    { subdomains: ['1', '2', '3', '4'], maxZoom: 19 },
  ).addTo(trackMap)
  const latlngs: Array<[number, number]> = use.map((p) => toGcj(p.lat, p.lng))
  L.polyline(latlngs, { color: '#06b6d4', weight: 3 }).addTo(trackMap)
  L.circleMarker(latlngs[0], { color: '#22c55e', radius: 5 }).addTo(trackMap)
  L.circleMarker(latlngs[latlngs.length - 1], { color: '#ef4444', radius: 5 }).addTo(trackMap)
  // 2026-08-08：轨迹点 hover 显示精度（米）
  const map = trackMap
  use.forEach((p, i) => {
    if (p.accuracy == null) return
    L.circleMarker(latlngs[i], {
      radius: 2, color: 'rgba(6,182,212,0.35)', fillOpacity: 0.4, interactive: false,
    }).bindTooltip(`精度 ${Math.round(p.accuracy)}m`).addTo(map)
  })
  L.control.zoom({ position: 'bottomright' }).addTo(trackMap)
  requestAnimationFrame(() => trackMap?.invalidateSize()) // 容器渲染后修正尺寸
}

function destroyLeafletMap() {
  if (trackMap) {
    trackMap.remove()
    trackMap = null
  }
}


onMounted(() => {
  refresh()
  setInterval(() => {
    refresh()
    checkIdleAuto()
  }, 30000) // 30s：手机状态刷新 + 5 分钟闲置检查
  // 2026-08-08：Tauri 拖放（OS 文件拖入 → 路径列表；Tauri 2 接管 HTML5 drop）
  getCurrentWebview().onDragDropEvent((ev) => {
    if (ev.payload.type === 'drop' && ev.payload.paths.length) {
      fsPushPaths(ev.payload.paths)
    }
  })
})
watch(showTrack, (v) => {
  if (v) window.setTimeout(initLeafletMap, 120) // 等容器挂载后初始化
  else destroyLeafletMap()
})
// 2026-08-08：文件列表 10s 自动刷新（面板打开时启动，关闭时清理；慢请求防重叠）
let fsTimer: ReturnType<typeof setInterval> | null = null
watch(showFiles, (v) => {
  if (v && !fsTimer) {
    fsTimer = setInterval(() => {
      if (!fsLoading.value) fsList(fsPath.value)
    }, 10000)
  } else if (!v && fsTimer) {
    clearInterval(fsTimer)
    fsTimer = null
  }
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

    <template v-else-if="phone">
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
          <span class="bar-val">{{ b.dash ? '—' : b.pct + '%' }}</span>
        </div>
        <!-- 2026-08-08：音量条——可滑动（300ms 防抖）+ 点击 emoji 一键静音/30% -->
        <div v-if="phone?.volume_music != null" class="vol-row" :title="'音量 0-15，点击图标一键静音/30%'">
          <span class="vol-emoji" @click="toggleVolumeMute">{{ volumeLabel }}</span>
          <input
            class="vol-slider"
            type="range" min="0" max="15" step="1"
            v-model.number="volumeSlider" @input="onVolumeInput"
          />
          <span class="vol-num">{{ volumeSlider }}</span>
        </div>
      </div>

      <!-- ②b 快捷面板（2026-08-08：Rust phone_command 无线 adb 直连手机） -->
      <div class="quick-panel">
        <div
          v-for="a in PHONE_ACTIONS"
          :key="a.key"
          class="qbtn"
          :class="{ disabled: a.disabled, busy: busyKey === a.key }"
          :title="a.hint"
          @click="runPhoneAction(a)"
        >
          <span class="qicon">{{ busyKey === a.key ? '⏳' : (a.key === 'sound_toggle' ? SOUND_META[soundState].icon : a.icon) }}</span>
          <span class="qlabel">{{ a.key === 'sound_toggle' ? SOUND_META[soundState].label : a.label }}</span>
        </div>
      </div>
      <div v-if="actionMsg" class="qmsg">{{ actionMsg }}</div>

      <!-- ②b2 文件浏览器（桌宠内嵌，adb 驱动：列表/下载/拖放上传） -->
      <div v-if="showFiles" class="fs-panel">
        <div class="fs-head">
          <button class="fs-btn" title="上级" @click="fsUp">⬆</button>
          <span class="fs-path">{{ fsPath }}</span>
          <button class="fs-btn" title="回到 /sdcard" @click="fsList('/sdcard')">🏠</button>
        </div>
        <div class="fs-list">
          <template v-if="!fsLoading">
            <div
              v-for="e in fsEntries"
              :key="e.name"
              class="fs-row"
              :class="{ dir: e.dir }"
              @dblclick="fsOpen(e.name, e.dir)"
            >
              <span class="fs-icon">{{ e.dir ? '📁' : '📄' }}</span>
              <span class="fs-name" :title="e.name">{{ e.name }}</span>
              <span class="fs-size">{{ e.dir ? '—' : fmtSize(e.size) }}</span>
              <button v-if="!e.dir" class="fs-dl" title="下载到电脑" @click.stop="fsDownload(e.name, e.dir)">⤓</button>
            </div>
            <div v-if="!fsEntries.length" class="fs-empty">（空目录）</div>
          </template>
          <div v-else class="fs-empty">加载中…</div>
        </div>
        <div class="fs-hint">双击文件夹进入 · ⤓ 下载到电脑 · 拖文件到此处上传到手机</div>
        <div v-if="fsMsg" class="fs-msg">{{ fsMsg }}</div>
      </div>

      <!-- ②b3 手机通知区（hub 转发的最近通知） -->
      <div v-if="showNotifies" class="fs-panel">
        <div class="fs-head">
          <span class="fs-path">🔔 最近手机通知（{{ phoneNotifies.length }}）</span>
          <button class="fs-btn" title="刷新" @click="refresh">⟳</button>
        </div>
        <div class="fs-list">
          <div v-for="n in phoneNotifies" :key="n.id" class="notify-row">
            <span class="fs-icon">{{ notifyIcon[n.data?.kind ?? 'notify'] ?? '🔔' }}</span>
            <div class="notify-body">
              <div class="notify-title">{{ n.data?.title }}</div>
              <div v-if="n.data?.text" class="notify-text">{{ n.data.text }}</div>
              <div class="notify-time">{{ fmtNotifyTime(n.created_at) }}</div>
            </div>
          </div>
          <div v-if="!phoneNotifies.length" class="fs-empty">暂无转发通知（App 通知桥未开或未收到）</div>
        </div>
        <div class="fs-hint">手机通知 → hub 转发（App 需开通知桥 + 通知使用权）</div>
      </div>

      <!-- ②c 轨迹地图（点开「轨迹」直接内嵌地图显示当前位置 + 下方轨迹走向） -->
      <div v-if="showTrack" class="track-panel">
        <div class="track-head">🗺️ 今日轨迹 <span class="track-tip">滚轮缩放 · 拖动平移</span></div>
        <div v-if="phone?.track?.length" ref="trackMapEl" class="track-map" />
        <div v-else class="track-hint">暂无轨迹数据</div>
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
        更新于 {{ lastTs ? new Date(lastTs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }} · 屏幕 {{ sitting }} 分钟
      </div>
    </template>

    <div v-else class="unavailable">手机端待接入（MOCK 数据接口未接）</div>
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
/* 2026-08-08：音量条——可滑动 + emoji 点击一键静音/30% */
.vol-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
.vol-emoji { font-size: 10px; color: var(--text-secondary); cursor: pointer; user-select: none; flex: 0 0 auto; }
.vol-emoji:hover { color: var(--text-primary); }
.vol-slider { flex: 1; min-width: 0; height: 14px; accent-color: #22c55e; }
.vol-num { width: 20px; text-align: right; font-size: 10px; color: var(--text-muted); font-family: 'Courier New', monospace; }
.bar-label { width: 40px; font-size: 11px; color: var(--text-tertiary); }
.bar { flex: 1; height: 6px; background: var(--border-subtle); border-radius: 3px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--accent); border-radius: 3px; }
.bar-val { width: 34px; text-align: right; font-size: 11px; font-family: 'Courier New', monospace; }

/* 2026-08-08：快捷面板 */
.quick-panel {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
  margin-bottom: 6px;
}
.qbtn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  background: var(--bg-surface, rgba(0, 0, 0, 0.25));
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 6px 2px;
  cursor: pointer;
  user-select: none;
}
.qbtn:hover { background: var(--bg-surface, rgba(255, 255, 255, 0.07)); }
.qbtn.disabled { opacity: 0.4; cursor: not-allowed; }
.qbtn.busy { opacity: 0.6; }
.qicon { font-size: 14px; line-height: 1; }
.qlabel { font-size: 9px; color: var(--text-secondary); }
.qmsg {
  margin-bottom: 6px;
  font-size: 10px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 文件浏览器 */
.fs-panel {
  margin-bottom: 8px;
  padding: 6px 8px;
  background: var(--bg-surface, rgba(0, 0, 0, 0.2));
  border-radius: 6px;
}
.fs-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.fs-btn {
  background: var(--bg-surface, rgba(255, 255, 255, 0.08));
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  color: var(--text-secondary);
  font-size: 11px;
  cursor: pointer;
  padding: 1px 5px;
}
.fs-btn:hover { color: var(--text-primary); }
.fs-path {
  flex: 1;
  font-size: 10px;
  color: var(--text-secondary);
  font-family: 'Courier New', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fs-list {
  max-height: 160px;
  overflow-y: auto;
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.15);
}
.fs-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 6px;
  font-size: 11px;
}
.fs-row:hover { background: rgba(255, 255, 255, 0.06); }
.fs-row.dir { cursor: pointer; }
.fs-icon { font-size: 11px; }
.fs-name {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-primary);
}
.fs-size {
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
.fs-dl {
  background: none;
  border: none;
  color: var(--accent, #06b6d4);
  cursor: pointer;
  font-size: 12px;
  padding: 0 2px;
}
.fs-dl:hover { color: #fff; }
.fs-empty {
  padding: 8px;
  font-size: 10px;
  color: var(--text-muted);
  text-align: center;
}
/* 通知区 */
.notify-row {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 6px;
  border-bottom: 1px dashed var(--border-subtle);
}
.notify-row:last-child { border-bottom: none; }
.notify-body { flex: 1; min-width: 0; }
.notify-title {
  font-size: 11px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.notify-text {
  font-size: 9px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.notify-time {
  font-size: 8px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
.fs-hint {
  margin-top: 4px;
  font-size: 9px;
  color: var(--text-muted);
}
.fs-msg {
  margin-top: 2px;
  font-size: 10px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 轨迹面板 */
.track-panel {
  margin-bottom: 8px;
  padding: 6px 8px;
  background: var(--bg-surface, rgba(0, 0, 0, 0.2));
  border-radius: 6px;
}
.track-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 10px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.track-tip {
  font-size: 9px;
  color: var(--text-tertiary, var(--text-muted));
}
.track-map-link {
  color: var(--accent, #06b6d4);
  text-decoration: none;
  font-size: 10px;
}
.track-map {
  width: 100%;
  height: 140px;
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.15);
  margin-bottom: 4px;
}
.track-hint {
  font-size: 10px;
  color: var(--text-muted);
}

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

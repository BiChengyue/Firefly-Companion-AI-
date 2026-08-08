<script setup lang="ts">
/**
 * 手机状态卡（v2 完整设计版）— 仿电脑状态卡：30s 采样上报（位置信息例外，低频）。
 *
 * 数据契约（接口留空）：hub `GET /api/v1/phone-state`（与 sensor_context._fmt_phone 同构）。
 * 手机端 Android 客户端落地后，填 refresh() 里的接口赋值即可，卡片结构无需改动。
 *
 * 布局（仿电脑状态卡）：
 *   ① 标题栏（红绿点 = 30s 心跳新鲜度）
 *   ② 前台 App（焦点 dot + 名称 + 分类）
 *   ③ 基础格区：电量(含充电) / 勿扰 / 网络 / 位置（位置低频上报，其余 30s）
 *   ④ 当日圆环 + 图例（今日 App 分类分布）
 *   ⑤ 本日活动条（主条 + 常显预览窗「最近 1 小时」）
 *   ⑥ foot（更新时间）
 */
import { ref, computed } from 'vue'

/** 未来 hub /api/v1/phone-state 返回结构（接口暂留空，先定契约） */
interface Seg { start: number; end: number; type: string; label?: string }
interface PhoneState {
  last_at?: number                            // 最近一次 30s 心跳
  battery?: number                            // 电量 %
  charging?: boolean                          // 充电中
  dnd?: boolean                               // 勿扰
  network?: 'wifi' | 'mobile' | 'offline'     // 网络
  loc_bucket?: 'home' | 'work' | 'out'        // 位置桶（低频上报）
  focus_app?: { name: string; cat: string } | null   // 前台 App
  today_activities?: Seg[]                    // 今日活动段（仿电脑 sensor）
  today_categories?: Record<string, number>   // 今日分类时长秒（圆环）
}

const phone = ref<PhoneState | null>(null)   // 2026-08-08：接口未接，恒 null → 显示占位
const error = ref('')
const lastTs = ref(0)

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
    { label: '电量', value: p?.battery != null ? `${p.battery}%${p.charging ? ' ⚡' : ''}` : '—' },
    { label: '勿扰', value: p?.dnd != null ? (p.dnd ? '开启' : '关闭') : '—' },
    { label: '网络', value: p?.network ? (NET_LABELS[p.network] ?? p.network) : '—' },
    { label: '位置', value: p?.loc_bucket ? (LOC_LABELS[p.loc_bucket] ?? p.loc_bucket) : '—' },
  ]
})

/** 前台 App 行 */
const focusRow = computed(() => phone.value?.focus_app ?? null)

/** 当日圆环（仿电脑卡：今日分类聚合） */
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

/** 活动条（仿电脑卡） */
const DAY_SEC = 86400
const timeline = computed(() => {
  const acts = (phone.value?.today_activities ?? []).filter((s) => s.end > s.start && s.type !== 'offline')
  const start = (() => {
    const d = new Date()
    d.setHours(0, 0, 0, 0)
    return d.getTime() / 1000
  })()
  const end = start + DAY_SEC
  const now = Date.now() / 1000
  const widths: number[] = []
  for (const s of acts) {
    const w = ((Math.min(s.end, now) - s.start) / DAY_SEC) * 100
    widths.push(Math.max(0, w))
  }
  return { start, end, now, acts, widths }
})

/** 预览窗：最近 1 小时（简化版，不做三态） */
const winWindow = computed(() => {
  const t = timeline.value
  const end = Math.min(t.now, t.end)
  const start = Math.max(t.start, end - 3600)
  const segs = (phone.value?.today_activities ?? [])
    .filter((s) => s.end > start && s.start < end && s.type !== 'offline')
    .sort((a, b) => a.start - b.start)
    .map((s) => {
      const ss = Math.max(s.start, start)
      const ee = Math.min(s.end, end)
      return {
        type: s.type,
        w: ((ee - ss) / (end - start)) * 100,
        label: s.label ?? '',
      }
    })
  return { winStart: start - t.start, winEnd: end - t.start, segs }
})

const winSummary = computed(() => {
  const segs = winWindow.value.segs
  if (!segs.length) return '该时段无记录'
  return segs.map((s) => `${CAT_LABELS[s.type] ?? s.type} ${s.w.toFixed(0)}%`).join(' · ')
})

function fmtTime(sec: number): string {
  const d = new Date(sec * 1000)
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

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
      <button class="refresh-btn" :disabled="!phone" title="刷新" @click="refresh">⟳</button>
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
        <div v-for="b in basics" :key="b.label" class="cell">
          <span class="c-label">{{ b.label }}</span>
          <span class="c-val">{{ b.value }}</span>
        </div>
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

      <!-- ⑤ 活动条 + 预览窗 -->
      <div class="timeline-wrap">
        <div class="timeline">
          <div
            v-for="(s, i) in timeline.acts"
            :key="i"
            class="tseg"
            :style="{
              left: ((s.start - timeline.start) / DAY_SEC) * 100 + '%',
              width: timeline.widths[i] + '%',
              background: CAT_COLORS[s.type] ?? '#888',
            }"
          />
          <div v-if="timeline.now < timeline.end" class="tseg future" :style="{ left: ((timeline.now - timeline.start) / DAY_SEC) * 100 + '%', width: Math.max(0.3, ((timeline.end - timeline.now) / DAY_SEC) * 100) + '%' }" />
        </div>
        <div class="tlabel"><span>0:00</span><span>24:00</span></div>

        <div class="zoom">
          <div class="zoom-head">
            <span>{{ fmtTime(timeline.start + winWindow.winStart) }}–{{ fmtTime(timeline.start + winWindow.winEnd) }}</span>
            <span class="zoom-mode">最近 1 小时</span>
          </div>
          <div class="zoom-bar">
            <div
              v-for="(s, i) in winWindow.segs"
              :key="i"
              class="zseg"
              :style="{ width: s.w + '%', background: CAT_COLORS[s.type] ?? '#888' }"
            />
          </div>
          <div class="zoom-detail">{{ winSummary }}</div>
        </div>
      </div>
    </template>

    <div v-else class="unavailable">
      手机端待接入
      <div class="hint">接入后显示：前台 App / 电量 / 勿扰 / 网络 / 位置 / 今日分类 / 活动时间线</div>
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
  align-items: baseline;
  background: var(--bg-surface, rgba(0, 0, 0, 0.2));
  border-radius: 6px;
  padding: 4px 8px;
}
.c-label {
  font-size: 10px;
  color: var(--text-muted);
}
.c-val {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  font-family: 'Courier New', monospace;
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
}
.tseg {
  position: absolute;
  top: 0;
  height: 100%;
}
.tseg.future { background: #000 !important; }
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

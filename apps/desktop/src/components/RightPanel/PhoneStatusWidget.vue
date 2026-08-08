<script setup lang="ts">
/**
 * 手机状态卡（v1 设计版）— 仿电脑状态卡布局；数据接口暂留空。
 *
 * 未来数据源：hub `GET /api/v1/phone-state`（与 sensor_context._fmt_phone 同构），
 * 手机端 Android 客户端上报。接入后把 `phone` ref 的赋值处换成接口即可，卡片结构无需改动。
 *
 * 布局（仿电脑状态卡五区）：
 *   ① 标题栏（红绿点=接入/心跳新鲜度）
 *   ② 基础格区：电量(含充电) / 勿扰 / 位置桶 / 网络
 *   ③ 前台 App（焦点 dot + 名称 + 分类）
 *   ④ 屏幕使用：今日累计 + 分类时长条
 *   ⑤ foot（更新时间）
 */
import { ref, computed } from 'vue'

/** 未来 hub /api/v1/phone-state 返回结构（接口暂留空，先定契约） */
interface PhoneState {
  last_at?: number                       // 最近一次心跳（在线判定：>5min 视为离线）
  battery?: number                       // 电量 %
  charging?: boolean                     // 是否充电中
  dnd?: boolean                          // 勿扰模式
  loc_bucket?: 'home' | 'work' | 'out'   // 位置桶（家/公司/外出）
  network?: 'wifi' | 'mobile' | 'offline'
  focus_app?: { name: string; cat: string } | null   // 前台 App（未来）
  screen_today_min?: number              // 今日屏幕使用分钟
  screen_cats?: Record<string, number>   // 今日各分类使用分钟
}

const phone = ref<PhoneState | null>(null)   // 2026-08-08：接口未接，恒 null → 显示占位
const error = ref('')
const lastTs = ref(0)

/** 心跳新鲜度：5 分钟内在线 → 绿，否则红 */
const stale = computed(() => {
  if (!phone.value?.last_at) return true
  return Date.now() / 1000 - phone.value.last_at > 5 * 60
})

/** 手机端 App 分类（未来屏幕使用条用）——暂用与电脑一致的色板 */
const PHONE_CAT_LABELS: Record<string, string> = {
  social: '社交', video: '影音', game: '游戏', shopping: '购物',
  tool: '工具', reading: '阅读', unknown: '其他', rest: '休息',
}

const LOC_LABELS: Record<string, string> = { home: '家', work: '公司', out: '外出' }
const NET_LABELS: Record<string, string> = { wifi: 'Wi-Fi', mobile: '蜂窝', offline: '离线' }

/** 基础格区四项（电量/勿扰/位置/网络）——未接入显示 — */
const basics = computed(() => {
  const p = phone.value
  return [
    { label: '电量', value: p?.battery != null ? `${p.battery}%${p.charging ? ' ⚡' : ''}` : '—' },
    { label: '勿扰', value: p?.dnd != null ? (p.dnd ? '开启' : '关闭') : '—' },
    { label: '位置', value: p?.loc_bucket ? (LOC_LABELS[p.loc_bucket] ?? p.loc_bucket) : '—' },
    { label: '网络', value: p?.network ? (NET_LABELS[p.network] ?? p.network) : '—' },
  ]
})

/** 前台 App 行（未来） */
const focusRow = computed(() => phone.value?.focus_app ?? null)

/** 屏幕使用分类条（未来）——按分钟比例 */
const screenBars = computed(() => {
  const p = phone.value
  const cats = p?.screen_cats
  if (!cats) return []
  const total = p.screen_today_min ?? Object.values(cats).reduce((a, b) => a + b, 0)
  if (!total) return []
  return Object.entries(cats)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ label: PHONE_CAT_LABELS[k] ?? k, pct: Math.round((v / total) * 100) }))
    .sort((a, b) => b.pct - a.pct)
    .slice(0, 5)
})

function refresh() {
  // 2026-08-08：接口未接，留空；接入后改为 fetch hub /api/v1/phone-state 并写 phone/lastTs
}
</script>

<template>
  <div class="phone-card">
    <div class="head">
      <div class="head-left">
        <span class="dot" :class="phone && !stale ? 'up' : 'down'" title="手机接入/在线状态" />
        <span class="title">📱 手机状态</span>
      </div>
      <button class="refresh-btn" :disabled="!phone" title="刷新" @click="refresh">⟳</button>
    </div>

    <div v-if="error" class="unavailable">{{ error }}</div>

    <template v-else-if="phone">
      <!-- ② 基础格区：电量 / 勿扰 / 位置 / 网络 -->
      <div class="grid">
        <div v-for="b in basics" :key="b.label" class="cell">
          <span class="c-label">{{ b.label }}</span>
          <span class="c-val">{{ b.value }}</span>
        </div>
      </div>

      <!-- ③ 前台 App -->
      <ul class="procs">
        <li v-if="focusRow">
          <span class="dot focus" />
          <span class="pname">{{ focusRow.name }}</span>
          <span class="pcat">{{ PHONE_CAT_LABELS[focusRow.cat] ?? focusRow.cat }}</span>
        </li>
        <li v-else class="empty-row">前台 App —</li>
      </ul>

      <!-- ④ 屏幕使用 -->
      <div class="screen-block">
        <div class="screen-head">
          <span>今日屏幕使用</span>
          <span class="screen-total">{{ phone.screen_today_min != null ? phone.screen_today_min + ' 分钟' : '—' }}</span>
        </div>
        <div class="bars">
          <div v-for="b in screenBars" :key="b.label" class="bar-row">
            <span class="bar-label">{{ b.label }}</span>
            <div class="bar"><div class="bar-fill" :style="{ width: Math.min(100, b.pct) + '%' }" /></div>
            <span class="bar-val">{{ b.pct }}%</span>
          </div>
          <div v-if="!screenBars.length" class="empty-line">暂无分类统计</div>
        </div>
      </div>
    </template>

    <div v-else class="unavailable">
      手机端待接入
      <div class="hint">接入后显示：电量 / 勿扰 / 位置 / 网络 / 前台 App / 屏幕使用</div>
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
.screen-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 4px;
  font-size: 10px;
  color: var(--text-muted);
}
.screen-total {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-primary);
}
.bars { display: flex; flex-direction: column; gap: 3px; }
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
.bar-val { width: 28px; text-align: right; font-size: 10px; color: var(--text-muted); font-family: 'Courier New', monospace; }
.empty-line {
  font-size: 10px;
  color: var(--text-muted);
  padding: 2px 0;
}
.foot {
  margin-top: 8px;
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
</style>

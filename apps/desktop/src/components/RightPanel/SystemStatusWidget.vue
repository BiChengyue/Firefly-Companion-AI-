<script setup lang="ts">
/** 系统状态小组件 — 从 /api/system/status 获取真实 CPU/内存，带竞态保护。 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getSystemStatus } from '@/services/api'

interface SysStatus { cpuPercent: number; memoryPercent: number; memoryUsedGb: number; memoryTotalGb: number }

const status = ref<SysStatus>({ cpuPercent: 0, memoryPercent: 0, memoryUsedGb: 0, memoryTotalGb: 0 })
let timer: ReturnType<typeof setInterval> | null = null
let refreshVersion = 0

onMounted(async () => {
  await refresh()
  timer = setInterval(refresh, 10000) // 每10秒刷新
})

onUnmounted(() => { if (timer) clearInterval(timer) })

async function refresh() {
  const myVersion = ++refreshVersion
  try {
    const data = await getSystemStatus()
    // 仅当此调用是最新版本时应用结果，防止旧请求覆盖新数据
    if (myVersion === refreshVersion) {
      status.value = data
    }
  } catch { /* 保持上次数据，不清空 */ }
}

const CIRCUMFERENCE = 2 * Math.PI * 32 // ≈ 201.06

// 综合负载率（取 CPU 和 内存的最大值作为环形图）
const loadPercent = computed(() => Math.max(status.value.cpuPercent, status.value.memoryPercent))
const dashoffset = computed(() => CIRCUMFERENCE * (1 - loadPercent.value / 100))
const statusLabel = computed(() => {
  if (loadPercent.value > 80) return 'WARNING'
  if (loadPercent.value > 50) return 'ACTIVE'
  return 'NOMINAL'
})
</script>

<template>
  <div class="card">
    <div class="gauge">
      <svg viewBox="0 0 80 80" class="gauge-svg">
        <circle cx="40" cy="40" r="32" fill="none" stroke="var(--border-accent)" stroke-width="4" />
        <circle
          cx="40" cy="40" r="32" fill="none" stroke="var(--accent)"
          stroke-width="4" stroke-linecap="round"
          :stroke-dasharray="CIRCUMFERENCE"
          :stroke-dashoffset="dashoffset"
          transform="rotate(-90 40 40)"
        />
      </svg>
      <div class="gauge-text">
        <span class="gauge-value">{{ Math.round(loadPercent) }}%</span>
      </div>
    </div>
    <div class="metrics">
      <div class="metric">CPU {{ status.cpuPercent }}%</div>
      <div class="metric">MEM {{ status.memoryUsedGb }}/{{ status.memoryTotalGb }}G</div>
    </div>
    <div class="status-label" :class="statusLabel.toLowerCase()">
      {{ statusLabel }}
    </div>
  </div>
</template>

<style scoped>
.card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 14px;
  text-align: center;
}
.gauge { position: relative; width: 80px; height: 80px; margin: 0 auto 6px; }
.gauge-svg { width: 100%; height: 100%; }
.gauge-text { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.gauge-value { font-size: 16px; font-weight: 700; font-family: 'Courier New', monospace; color: var(--accent); }
.metrics { display: flex; justify-content: center; gap: 12px; margin-bottom: 4px; }
.metric { font-size: 10px; font-family: 'Courier New', monospace; color: var(--text-muted); }
.status-label {
  font-size: 10px; font-family: 'Courier New', monospace; letter-spacing: 1.5px; color: var(--accent);
}
.status-label.warning { color: #ff5544; text-shadow: 0 0 6px rgba(204,51,0,0.5); }
.status-label.active { color: #e0a040; }
.status-label.nominal { color: #5ebd82; }
</style>

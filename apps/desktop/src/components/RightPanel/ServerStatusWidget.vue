<script setup lang="ts">
/** 服务器状态小组件（T-29-A3）— 从 bus /api/v1/monitor 拉取服务器状态快照。
 *  30s 自动刷新 + 手动刷新按钮；异步加载不阻塞主界面，失败显示「监控暂不可用」。 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getServerMonitor, type ServerMonitor } from '@/services/api'

const monitor = ref<ServerMonitor | null>(null)
const error = ref('')
const lastTs = ref(0)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
let refreshVersion = 0

onMounted(async () => {
  await refresh()
  timer = setInterval(refresh, 30000) // 30s 自动刷新
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function refresh() {
  const myVersion = ++refreshVersion
  loading.value = true
  try {
    const data = await getServerMonitor()
    if (myVersion === refreshVersion) {
      monitor.value = data
      error.value = ''
      lastTs.value = Date.now()
    }
  } catch {
    // 仅当此调用仍是最新版本时显示失败（防旧请求覆盖新数据）
    if (myVersion === refreshVersion) error.value = '监控暂不可用'
  } finally {
    if (myVersion === refreshVersion) loading.value = false
  }
}

const runningCount = computed(() =>
  monitor.value?.services.filter((s) => s.status === 'running').length ?? 0,
)
const resourceBars = computed(() => {
  const r = monitor.value?.resource
  if (!r) return []
  return [
    { label: 'CPU', pct: r.cpu ?? 0 },
    { label: '内存', pct: r.mem ?? 0 },
    { label: '磁盘 C', pct: r.disk?.C ?? 0 },
  ]
})
</script>

<template>
  <div class="server-card">
    <div class="head">
      <span class="title">服务器状态</span>
      <button class="refresh-btn" :disabled="loading" title="刷新" @click="refresh">⟳</button>
    </div>

    <div v-if="error" class="unavailable">{{ error }}</div>

    <template v-else-if="monitor">
      <ul class="services">
        <li v-for="s in monitor.services" :key="s.name" class="service">
          <span class="dot" :class="s.status === 'running' ? 'up' : 'down'" />
          <span class="name">{{ s.name }}</span>
          <span class="ports">{{ Object.keys(s.ports).filter((p) => s.ports[p]).join('/') }}</span>
        </li>
      </ul>

      <div class="bars">
        <div v-for="b in resourceBars" :key="b.label" class="bar-row">
          <span class="bar-label">{{ b.label }}</span>
          <div class="bar">
            <div class="bar-fill" :style="{ width: Math.min(100, b.pct) + '%' }" />
          </div>
          <span class="bar-val">{{ Math.round(b.pct) }}%</span>
        </div>
        <div v-if="monitor.resource.temp != null" class="temp">温度 {{ monitor.resource.temp }}℃</div>
      </div>

      <div class="network">
        <span class="net-item" :class="monitor.network.tailscale ? 'ok' : 'bad'">
          Tailscale {{ monitor.network.tailscale ? '✓' : '✗' }}
        </span>
        <span class="net-item" :class="monitor.network.deepseek_api ? 'ok' : 'bad'">
          DeepSeek API {{ monitor.network.deepseek_api ? '✓' : '✗' }}
        </span>
        <span class="net-item" :class="monitor.network.qq_gateway ? 'ok' : 'bad'">
          QQ 网关 {{ monitor.network.qq_gateway ? '✓' : '✗' }}
        </span>
      </div>

      <div v-if="monitor.alerts && monitor.alerts.length" class="alerts">
        <div v-for="(a, i) in monitor.alerts" :key="i" class="alert">
          {{ typeof a === 'string' ? a : JSON.stringify(a) }}
        </div>
      </div>

      <div class="foot">
        更新于 {{ lastTs ? new Date(lastTs).toLocaleTimeString() : '—' }} · 服务
        {{ runningCount }}/{{ monitor.services.length }}
      </div>
    </template>

    <div v-else class="unavailable">加载中…</div>
  </div>
</template>

<style scoped>
.server-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
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
.services {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px 10px;
}
.service {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  color: var(--text-secondary);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot.up { background: #5ebd82; box-shadow: 0 0 4px rgba(94, 189, 130, 0.6); }
.dot.down { background: #ff5544; box-shadow: 0 0 4px rgba(255, 85, 68, 0.6); }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ports { margin-left: auto; color: var(--text-muted); font-family: 'Courier New', monospace; }
.bars { margin-bottom: 8px; display: flex; flex-direction: column; gap: 3px; }
.bar-row { display: flex; align-items: center; gap: 6px; font-size: 10px; color: var(--text-muted); }
.bar-label { width: 38px; flex-shrink: 0; }
.bar { flex: 1; height: 6px; background: var(--bg-surface); border-radius: 3px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.4s; }
.bar-val { width: 38px; text-align: right; font-family: 'Courier New', monospace; }
.temp { font-size: 10px; color: var(--text-muted); }
.network {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  margin-bottom: 6px;
}
.net-item { font-size: 10px; }
.net-item.ok { color: #5ebd82; }
.net-item.bad { color: #ff5544; }
.alerts { margin-bottom: 6px; display: flex; flex-direction: column; gap: 2px; }
.alert {
  font-size: 10px;
  color: #e0a040;
  background: rgba(224, 160, 64, 0.08);
  border: 1px solid rgba(224, 160, 64, 0.25);
  border-radius: var(--radius-sm);
  padding: 2px 6px;
}
.foot {
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
</style>

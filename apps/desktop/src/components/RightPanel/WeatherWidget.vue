<script setup lang="ts">
/** 天气小组件 — 通过后端 /api/weather 代理获取天气 (open-meteo 免费源)，支持城市输入，5 分钟本地缓存。
 *  T32：紧凑一行样式（图标+温度+描述+城市）+ 显示更新时间（HH:MM）+ 每小时刷新（3600s）。 */
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { getApiBase } from '@/services/api'

const temp = ref('--')
const icon = ref('🌤')
const desc = ref('加载中…')
const city = ref('')
const lastUpdated = ref('') // T32：更新时间 HH:MM
const inputting = ref(false)
const loading = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)

const CACHE_KEY = 'firefly_weather_cache'
const SAVED_CITY_KEY = 'firefly_weather_last_city'
const CACHE_TTL = 5 * 60 * 1000
// T32：每小时刷新（原 30s 未实现，现统一每小时）
const REFRESH_MS = 3600 * 1000
let fetchVersion = 0
let savedCityName = ''
let timer: ReturnType<typeof setInterval> | null = null

interface WeatherCache {
  temp: string; icon: string; desc: string; city: string; ts: number
}

function loadCache(): WeatherCache | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const c: WeatherCache = JSON.parse(raw)
    if (Date.now() - c.ts > CACHE_TTL) { localStorage.removeItem(CACHE_KEY); return null }
    return c
  } catch { return null }
}

function saveCache(cityName: string) {
  localStorage.setItem(CACHE_KEY, JSON.stringify({
    temp: temp.value, icon: icon.value, desc: desc.value,
    city: cityName, ts: Date.now()
  }))
}

function weatherIconCN(type: string): string {
  if (!type) return '🌤'
  if (type.includes('雷')) return '⛈'
  if (type.includes('雨')) return '🌧'
  if (type.includes('雪')) return '🌨'
  if (type.includes('雾') || type.includes('霾')) return '🌫'
  if (type.includes('阴')) return '☁️'
  if (type.includes('多云')) return '⛅'
  if (type.includes('晴')) return '☀️'
  return '🌤'
}

function nowHHMM(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function fetchWeather(cityName: string, silent = false) {
  const myVersion = ++fetchVersion
  if (!silent) loading.value = true

  // T-20 切单轨配套：用 api.ts 的 getApiBase()（dev vite proxy / 生产 Tailnet 统一）
  const apiUrl = `${getApiBase()}/api/weather?city=${encodeURIComponent(cityName)}`
  try {
    const res = await fetch(apiUrl, {
      signal: AbortSignal.timeout(12000),
    })
    if (!res.ok) {
      const errData = await res.json().catch(() => ({})) as { detail?: string }
      throw new Error(errData.detail || `HTTP ${res.status}`)
    }
    const data = await res.json() as {
      temp: string; type: string; high: string; low: string
    }
    if (myVersion !== fetchVersion) return

    temp.value = `${data.temp}°C`
    desc.value = data.type || '--'
    icon.value = weatherIconCN(data.type)
    lastUpdated.value = nowHHMM() // T32：记录更新时间
    saveCache(cityName)
  } catch (err: unknown) {
    if (myVersion !== fetchVersion) return
    const msg = (err as Error)?.message || ''
    console.error(`[Weather] 获取失败 (${cityName}):`, msg, err)
    if (!silent) {
      temp.value = '--'
      desc.value = msg.includes('未找到城市') ? '未找到城市' : `天气获取失败 [${msg.slice(0, 20)}]`
      icon.value = '❓'
    }
  } finally {
    if (myVersion === fetchVersion) loading.value = false
  }
}

// ── 城市编辑 ──

function startInput() {
  inputting.value = true
  nextTick(() => {
    inputRef.value?.focus()
    inputRef.value?.select()
  })
}

function confirmCity() {
  const trimmed = city.value.trim()
  if (!trimmed) {
    cancelInput()
    return
  }
  savedCityName = trimmed
  localStorage.setItem(SAVED_CITY_KEY, trimmed)
  fetchWeather(trimmed)
  inputting.value = false
}

function cancelInput() {
  city.value = savedCityName
  inputting.value = false
}

function onInputKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    e.preventDefault()
    confirmCity()
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    cancelInput()
  }
}

// ── 初始化 ──

onMounted(async () => {
  savedCityName = localStorage.getItem(SAVED_CITY_KEY) || ''
  city.value = savedCityName

  const cached = loadCache()
  if (cached) {
    temp.value = cached.temp
    icon.value = cached.icon
    desc.value = cached.desc
    city.value = cached.city
    savedCityName = cached.city
    lastUpdated.value = cached.ts ? new Date(cached.ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''
    fetchWeather(cached.city, true) // 后台静默更新
  } else if (savedCityName) {
    await fetchWeather(savedCityName)
  } else {
    loading.value = false
  }

  // T32：每小时刷新一次
  timer = setInterval(() => {
    if (savedCityName) fetchWeather(savedCityName, true)
  }, REFRESH_MS)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="card compact" :class="{ dim: loading }">
    <div class="w-layout">
      <!-- 左列：城市（上）+ 更新时间（下） -->
      <div class="w-left">
        <div class="w-city-row">
          <template v-if="!inputting">
            <span class="w-city" @click="startInput" title="点击切换城市">
              {{ city || '输入城市' }}
            </span>
            <button class="w-edit" title="切换城市" @click="startInput">✎</button>
          </template>
          <template v-else>
            <input
              ref="inputRef"
              v-model="city"
              class="w-input"
              placeholder="城市"
              @keydown="onInputKeydown"
            />
            <button class="w-ok" title="确认" @click="confirmCity">✓</button>
            <button class="w-cancel" title="取消" @click="cancelInput">✕</button>
          </template>
        </div>
        <div class="w-foot">
          <span v-if="lastUpdated">更新于 {{ lastUpdated }}</span>
          <span v-else>更新于 —</span>
          <span v-if="loading" class="w-spin">⟳</span>
        </div>
      </div>
      <!-- 右列：具体天气信息（更大字） -->
      <div class="w-right">
        <span class="w-icon">{{ icon }}</span>
        <span class="w-temp">{{ temp }}</span>
        <span class="w-desc">{{ desc }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* T32：紧凑卡片（一行），缩小占位 */
.card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 8px 10px;
}
.card.dim { opacity: 0.6; }

/* T33：左右布局——左列城市+时间（小字），右列天气信息（大字号） */
.w-layout {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.w-left {
  display: flex;
  flex-direction: column;
  gap: 3px;
  align-items: flex-start;
  min-width: 0;
  flex-shrink: 0;
}
.w-city-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.w-right {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-left: auto;
}
.w-icon { font-size: 22px; flex-shrink: 0; } /* T33：右列天气信息放大 */
.w-temp {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  flex-shrink: 0;
}
.w-desc {
  font-size: 13px; /* T33：右列天气信息放大 */
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.w-city {
  font-size: 11px;
  color: var(--text-muted);
  cursor: pointer;
  white-space: nowrap;
  padding: 1px 3px;
  border-bottom: 1px dashed transparent;
}
.w-city:hover {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}
.w-edit,
.w-ok,
.w-cancel {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  padding: 1px 3px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.w-edit:hover { color: var(--color-primary); }
.w-ok { color: var(--color-success, #4caf50); }
.w-cancel:hover { color: var(--color-danger, #ef4444); }
.w-input {
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--color-primary);
  outline: none;
  font-size: 12px;
  color: var(--text-primary);
  width: 70px;
  padding: 1px 0;
  font-family: inherit;
}
.w-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
  margin-top: 4px;
}
.w-spin { animation: wspin 1s linear infinite; }
@keyframes wspin { to { transform: rotate(360deg); } }
</style>

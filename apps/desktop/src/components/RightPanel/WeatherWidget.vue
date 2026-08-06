<script setup lang="ts">
/** 天气小组件 — 通过后端 /api/weather 代理获取天气 (open-meteo 免费源)，支持城市输入，5 分钟本地缓存。 */
import { ref, onMounted, nextTick } from 'vue'
import { getApiBase } from '@/services/api'

const temp = ref('--')
const icon = ref('🌤')
const desc = ref('加载中…')
const city = ref('')
const inputting = ref(false)
const loading = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)

const CACHE_KEY = 'firefly_weather_cache'
const SAVED_CITY_KEY = 'firefly_weather_last_city'
const CACHE_TTL = 5 * 60 * 1000
let fetchVersion = 0
let savedCityName = ''

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

async function fetchWeather(cityName: string, silent = false) {
  const myVersion = ++fetchVersion
  if (!silent) loading.value = true

  // T-20 切单轨配套（2026-08-06）：原硬编码 127.0.0.1:8765（后端同机假设）在
  // 桌宠=总线客户端改造后失效（后端在 Tailnet 服务器）。改用 api.ts 的 getApiBase()：
  // dev(vite proxy) / 生产(Tailnet 默认 + localStorage firefly_http_base 覆盖) 统一。
  const apiUrl = `${getApiBase()}/api/weather?city=${encodeURIComponent(cityName)}`
  try {
    console.log('[Weather] 请求:', apiUrl)
    const res = await fetch(apiUrl, {
      signal: AbortSignal.timeout(12000),
    })
    console.log('[Weather] 响应状态:', res.status)
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
  // 立即持久化城市名（不论 fetch 成败）
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
    fetchWeather(cached.city, true) // 后台静默更新
  } else if (savedCityName) {
    await fetchWeather(savedCityName)
  } else {
    // 首次使用：未保存城市，等待用户手动输入（open-meteo 无 IP 定位功能）
    loading.value = false
  }
})
</script>

<template>
  <div class="card">
    <!-- 城市行 -->
    <div class="city-row" :class="{ active: inputting }">
      <!-- 显示模式：城市名 + 编辑按钮 -->
      <template v-if="!inputting">
        <span class="city-label" @click="startInput" title="点击切换城市">
          {{ city || '点击输入城市' }}
        </span>
        <button class="edit-btn" title="切换城市" @click="startInput">✎</button>
      </template>

      <!-- 编辑模式：输入框 + 确认/取消按钮 -->
      <template v-else>
        <input
          ref="inputRef"
          v-model="city"
          class="city-input"
          placeholder="输入城市名"
          @keydown="onInputKeydown"
        />
        <button class="action-btn confirm" title="确认" @click="confirmCity">✓</button>
        <button class="action-btn cancel" title="取消" @click="cancelInput">✕</button>
      </template>
    </div>

    <!-- 天气信息 -->
    <div class="weather-row" :class="{ dim: loading }">
      <span class="temp">{{ temp }}</span>
      <span class="icon">{{ icon }}</span>
    </div>
    <div class="label">{{ desc }}</div>
  </div>
</template>

<style scoped>
.card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  text-align: center;
}

/* 城市行 */
.city-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-bottom: 6px;
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  transition: background 0.15s;
  min-height: 26px;
}
.city-row.active {
  background: var(--bg-card);
}
.city-label {
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 4px;
  border-bottom: 1px dashed transparent;
  transition: color 0.15s, border-color 0.15s;
}
.city-label:hover {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}
.city-input {
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--color-primary);
  outline: none;
  font-size: 13px;
  color: var(--text-primary);
  text-align: center;
  width: 80px;
  padding: 2px 0;
  font-family: inherit;
}
.city-input::placeholder {
  color: var(--text-muted);
  opacity: 0.7;
}

/* 按钮 */
.edit-btn,
.action-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 13px;
  padding: 2px 4px;
  line-height: 1;
  border-radius: 3px;
  transition: color 0.15s, background 0.15s;
  flex-shrink: 0;
}
.edit-btn {
  color: var(--text-muted);
}
.edit-btn:hover {
  color: var(--color-primary);
  background: var(--bg-card);
}
.action-btn.confirm {
  color: var(--color-success, #4caf50);
}
.action-btn.confirm:hover {
  background: var(--bg-card);
}
.action-btn.cancel {
  color: var(--text-muted);
}
.action-btn.cancel:hover {
  color: var(--color-danger, #ef4444);
  background: var(--bg-card);
}

/* 天气展示 */
.weather-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: opacity 0.3s;
}
.weather-row.dim {
  opacity: 0.5;
}
.temp {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
}
.icon {
  font-size: 28px;
}
.label {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-muted);
  min-height: 18px;
}
</style>

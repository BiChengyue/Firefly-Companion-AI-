<template>
  <Teleport to="body">
    <div class="model-setup-overlay">
      <!-- 星空 / 萤火虫背景 -->
      <div class="stars-layer" aria-hidden="true">
        <span v-for="i in 60" :key="i" class="star" :style="starStyle(i)"></span>
      </div>
      <div class="firefly-layer" aria-hidden="true">
        <span v-for="i in 14" :key="i" class="firefly" :style="fireflyStyle(i)"></span>
      </div>

      <div class="setup-card">
        <!-- 头部 -->
        <header class="setup-header">
          <div class="brand-badge">流萤 · Firefly</div>
          <h1 class="title">欢迎回来，开拓者</h1>
          <p class="subtitle">
            {{
              statusReady
                ? '所有核心模型已就绪，即将进入主界面'
                : '首次使用需要下载语义记忆与语音模型，请保持网络畅通'
            }}
          </p>
        </header>

        <!-- 整体进度 -->
        <div class="overall-progress">
          <div class="progress-ring" :style="{ '--pct': overallPercent }">
            <div class="ring-inner">
              <span class="ring-pct">{{ Math.round(overallPercent) }}%</span>
              <span class="ring-label">{{ stateText }}</span>
            </div>
          </div>
          <div class="overall-meta" v-if="!statusReady && !fatal">
            <span>已下载 {{ overallDownloadedMb }} MB</span>
            <span>共 {{ overallTotalMb }} MB</span>
          </div>
        </div>

        <!-- 文件列表 -->
        <ul class="file-list" v-if="!statusReady">
          <li
            v-for="f in fileStates"
            :key="f.key"
            class="file-item"
            :class="fileItemClass(f)"
          >
            <span class="file-icon" :class="`icon-${f.state}`">
              {{ f.state === 'done' ? '✓' : f.state === 'downloading' ? '↓' : f.state === 'error' ? '!' : '○' }}
            </span>
            <div class="file-info">
              <div class="file-name-row">
                <span class="file-name">{{ f.name }}</span>
                <span class="file-size">{{ f.sizeMb }} MB</span>
              </div>
              <div class="file-bar">
                <div class="file-bar-fill" :style="{ width: f.percent + '%' }"></div>
              </div>
              <div class="file-desc">
                <span class="file-desc-text">{{ f.desc }}</span>
                <span class="file-status">{{ fileStatusText(f) }}</span>
              </div>
            </div>
          </li>
        </ul>

        <!-- 错误提示 -->
        <div class="error-box" v-if="fatal">
          <p class="error-text">{{ fatal }}</p>
          <p class="error-hint">请检查网络后重试；也可以先跳过，下次启动时会自动再次检查。</p>
        </div>

        <!-- 操作区 -->
        <footer class="setup-footer">
          <button v-if="!statusReady && !downloading && !fatal" class="btn btn-ghost" @click="startDownload">
            立即下载（{{ totalMissingMb }} MB）
          </button>
          <button v-if="downloading" class="btn btn-ghost" disabled>
            下载中…
          </button>
          <button v-if="fatal" class="btn btn-primary" @click="startDownload">重试下载</button>
          <button v-if="!statusReady && !downloading" class="btn btn-link" @click="handleSkip">
            跳过，先进去看看
          </button>
          <button v-if="statusReady" class="btn btn-primary" @click="handleFinish">进入应用</button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { downloadCoreModels, getCoreModelStatus, type CoreModelFileStatus, type CoreModelDownloadEvent } from '@/services/api'

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'skip'): void
}>()

// ── 状态 ──
const loading = ref(true)          // 正在检查模型状态
const statusReady = ref(false)     // 模型已就绪
const downloading = ref(false)     // 正在下载
const fatal = ref('')              // 致命错误信息
const overallPercent = ref(0)
const overallDownloadedMb = ref(0)
const overallTotalMb = ref(0)

interface FileUiState {
  key: string
  name: string
  desc: string
  sizeMb: number
  state: 'pending' | 'downloading' | 'done' | 'error'
  percent: number
}

const fileStates = ref<FileUiState[]>([])
let abortDownload: (() => void) | null = null

// ── 计算属性 ──
const totalMissingMb = computed(() =>
  Math.round(fileStates.value.reduce((sum, f) => sum + (f.state === 'done' ? 0 : f.sizeMb), 0)),
)

const stateText = computed(() => {
  if (statusReady.value) return '已就绪'
  if (fatal.value) return '下载失败'
  if (downloading.value) return '下载中'
  if (loading.value) return '检查中'
  return '等待开始'
})

function fileItemClass(f: FileUiState) {
  return {
    'is-done': f.state === 'done',
    'is-downloading': f.state === 'downloading',
    'is-error': f.state === 'error',
  }
}

function fileStatusText(f: FileUiState) {
  if (f.state === 'done') return '已完成'
  if (f.state === 'downloading') return `${f.percent}%`
  if (f.state === 'error') return '失败'
  return '等待中'
}

// ── 背景装饰 ──
function starStyle(_i: number) {
  const size = 1 + Math.random() * 2
  return {
    left: `${Math.random() * 100}%`,
    top: `${Math.random() * 100}%`,
    width: `${size}px`,
    height: `${size}px`,
    animationDelay: `${Math.random() * 3}s`,
    animationDuration: `${2 + Math.random() * 3}s`,
  }
}

function fireflyStyle(_i: number) {
  return {
    left: `${Math.random() * 100}%`,
    top: `${Math.random() * 100}%`,
    animationDelay: `${Math.random() * 6}s`,
    animationDuration: `${8 + Math.random() * 8}s`,
    transform: `scale(${0.6 + Math.random() * 0.8})`,
  }
}

// ── 逻辑 ──
function initFileStates(files: CoreModelFileStatus[]) {
  fileStates.value = files.map(f => ({
    key: f.key,
    name: f.name,
    desc: f.desc,
    sizeMb: f.size_mb,
    state: f.exists ? ('done' as const) : ('pending' as const),
    percent: f.exists ? 100 : 0,
  }))
}

async function checkStatus() {
  loading.value = true
  try {
    const status = await getCoreModelStatus()
    initFileStates(status.files)
    statusReady.value = status.ready
    overallPercent.value = status.ready ? 100 : 0
    if (status.ready) {
      overallDownloadedMb.value = status.files.reduce((s, f) => s + (f.exists ? f.size_mb : 0), 0)
      overallTotalMb.value = status.files.reduce((s, f) => s + f.size_mb, 0)
    }
  } catch (e: any) {
    fatal.value = `无法连接后端服务：${e.message}`
  } finally {
    loading.value = false
  }
}

function handleEvent(evt: CoreModelDownloadEvent) {
  if (evt.event === 'progress') {
    overallPercent.value = evt.overall_percent ?? overallPercent.value
    overallDownloadedMb.value = evt.overall_downloaded_mb ?? overallDownloadedMb.value
    overallTotalMb.value = evt.overall_total_mb ?? overallTotalMb.value
    if (evt.file) {
      const f = fileStates.value.find(x => x.name === evt.file)
      if (f) {
        f.state = 'downloading'
        f.percent = evt.file_percent ?? f.percent
      }
    }
  } else if (evt.event === 'file_start') {
    if (evt.file) {
      const f = fileStates.value.find(x => x.name === evt.file)
      if (f) { f.state = 'downloading'; f.percent = 0 }
    }
  } else if (evt.event === 'file_done') {
    if (evt.file) {
      const f = fileStates.value.find(x => x.name === evt.file)
      if (f) { f.state = 'done'; f.percent = 100 }
    }
  } else if (evt.event === 'file_error') {
    if (evt.file) {
      const f = fileStates.value.find(x => x.name === evt.file)
      if (f) f.state = 'error'
    }
  } else if (evt.event === 'complete' || evt.event === 'already_complete') {
    statusReady.value = true
    overallPercent.value = 100
    fileStates.value.forEach(f => { f.state = 'done'; f.percent = 100 })
  } else if (evt.event === 'fatal') {
    fatal.value = evt.message || '下载失败'
  }
}

async function startDownload() {
  if (downloading.value) return
  downloading.value = true
  fatal.value = ''
  fileStates.value.forEach(f => { if (f.state !== 'done') f.state = 'pending' })

  const { promise, abort } = downloadCoreModels(handleEvent)
  abortDownload = abort
  try {
    await promise
  } catch (e: any) {
    if (e.name !== 'AbortError') fatal.value = `下载中断：${e.message}`
  } finally {
    downloading.value = false
    abortDownload = null
  }
}

function handleSkip() {
  abortDownload?.()
  emit('skip')
}

function handleFinish() {
  emit('close')
}

onMounted(() => {
  checkStatus()
})

onBeforeUnmount(() => {
  abortDownload?.()
})
</script>

<style scoped>
.model-setup-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    radial-gradient(1200px 800px at 70% -10%, rgba(94, 189, 130, 0.22), transparent 60%),
    radial-gradient(900px 700px at 10% 110%, rgba(60, 130, 200, 0.18), transparent 60%),
    linear-gradient(160deg, #0b1420 0%, #0e1a2b 45%, #0a1626 100%);
  overflow: hidden;
}

/* 星空 */
.stars-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.star {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.7);
  box-shadow: 0 0 6px rgba(255, 255, 255, 0.5);
  animation: twinkle 3s ease-in-out infinite;
}
@keyframes twinkle {
  0%, 100% { opacity: 0.2; }
  50% { opacity: 1; }
}

/* 萤火虫 */
.firefly-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.firefly {
  position: absolute;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(200, 255, 200, 0.95), rgba(120, 220, 160, 0.35) 60%, transparent);
  box-shadow: 0 0 12px 4px rgba(120, 220, 160, 0.45);
  animation: float 10s ease-in-out infinite;
}
@keyframes float {
  0%, 100% { transform: translate(0, 0); opacity: 0.15; }
  25% { transform: translate(40px, -50px); opacity: 0.85; }
  50% { transform: translate(-25px, -90px); opacity: 0.4; }
  75% { transform: translate(55px, -40px); opacity: 0.9; }
}

/* 卡片 */
.setup-card {
  position: relative;
  z-index: 1;
  width: min(560px, 100%);
  max-height: 92vh;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 32px;
  border-radius: 24px;
  border: 1px solid rgba(120, 220, 160, 0.22);
  background: rgba(16, 28, 44, 0.82);
  backdrop-filter: blur(18px);
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.5);
  overflow-y: auto;
}

.setup-header {
  text-align: center;
}
.brand-badge {
  display: inline-block;
  padding: 4px 14px;
  border-radius: 999px;
  font-size: 12px;
  letter-spacing: 2px;
  color: #c8f5d8;
  background: rgba(94, 189, 130, 0.15);
  border: 1px solid rgba(120, 220, 160, 0.3);
}
.title {
  margin: 12px 0 6px;
  font-size: 26px;
  font-weight: 700;
  color: #eefaf2;
}
.subtitle {
  margin: 0;
  font-size: 14px;
  color: rgba(220, 240, 230, 0.65);
}

/* 整体进度环 */
.overall-progress {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.progress-ring {
  --pct: 0;
  width: 130px;
  height: 130px;
  border-radius: 50%;
  background: conic-gradient(
    #5ebd82 calc(var(--pct) * 1%),
    rgba(255, 255, 255, 0.08) 0
  );
  display: grid;
  place-items: center;
  box-shadow: 0 0 30px rgba(94, 189, 130, 0.25);
}
.ring-inner {
  width: 104px;
  height: 104px;
  border-radius: 50%;
  background: #0e1a2b;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.ring-pct {
  font-size: 24px;
  font-weight: 700;
  color: #eefaf2;
}
.ring-label {
  font-size: 12px;
  color: rgba(220, 240, 230, 0.6);
}
.overall-meta {
  display: flex;
  gap: 18px;
  font-size: 13px;
  color: rgba(220, 240, 230, 0.7);
}

/* 文件列表 */
.file-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.file-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  transition: border-color 0.3s var(--ease-tactile), background 0.3s var(--ease-tactile);
}
.file-item.is-downloading {
  border-color: rgba(94, 189, 130, 0.55);
  background: rgba(94, 189, 130, 0.08);
}
.file-item.is-done {
  border-color: rgba(94, 189, 130, 0.25);
}
.file-item.is-error {
  border-color: rgba(255, 110, 90, 0.55);
}
.file-icon {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 50%;
  font-size: 16px;
  font-weight: 700;
  color: rgba(220, 240, 230, 0.7);
  background: rgba(255, 255, 255, 0.07);
}
.file-icon.icon-downloading {
  color: #5ebd82;
  animation: bounce 1.2s ease-in-out infinite;
}
.file-icon.icon-done {
  color: #5ebd82;
  background: rgba(94, 189, 130, 0.2);
}
.file-icon.icon-error {
  color: #ff6e5a;
  background: rgba(255, 110, 90, 0.18);
}
@keyframes bounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(3px); }
}
.file-info {
  flex: 1;
  min-width: 0;
}
.file-name-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}
.file-name {
  font-size: 14px;
  font-weight: 600;
  color: #eaf6ee;
  word-break: break-all;
}
.file-size {
  font-size: 12px;
  color: rgba(220, 240, 230, 0.5);
  flex-shrink: 0;
}
.file-bar {
  margin-top: 7px;
  height: 5px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.file-bar-fill {
  height: 100%;
  border-radius: 99px;
  background: linear-gradient(90deg, #3da061, #5ebd82, #8fe3ae);
  box-shadow: 0 0 8px rgba(94, 189, 130, 0.5);
  transition: width 0.3s var(--ease-tactile);
}
.file-desc {
  margin-top: 5px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: rgba(220, 240, 230, 0.45);
}
.file-status {
  flex-shrink: 0;
  color: rgba(220, 240, 230, 0.65);
}

/* 错误 */
.error-box {
  padding: 12px 14px;
  border-radius: 12px;
  background: rgba(255, 110, 90, 0.1);
  border: 1px solid rgba(255, 110, 90, 0.3);
}
.error-text {
  margin: 0 0 4px;
  font-size: 13px;
  color: #ffb3a6;
}
.error-hint {
  margin: 0;
  font-size: 12px;
  color: rgba(255, 200, 190, 0.7);
}

/* 操作区 */
.setup-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: center;
  gap: 14px;
  padding-top: 4px;
}
.btn {
  border: none;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  border-radius: 12px;
  padding: 11px 22px;
  transition: transform 0.2s var(--ease-tactile), box-shadow 0.2s var(--ease-tactile), opacity 0.2s;
}
.btn:disabled { opacity: 0.55; cursor: default; }
.btn-primary {
  color: #06251a;
  background: linear-gradient(135deg, #5ebd82, #8fe3ae);
  box-shadow: 0 6px 24px rgba(94, 189, 130, 0.4);
}
.btn-primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 8px 30px rgba(94, 189, 130, 0.55);
}
.btn-ghost {
  color: #d9f5e4;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.14);
}
.btn-ghost:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.12);
}
.btn-link {
  color: rgba(220, 240, 230, 0.65);
  background: transparent;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.btn-link:hover {
  color: rgba(220, 240, 230, 0.9);
}
</style>

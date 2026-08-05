<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import type { ChatMessage } from '@shared/index'
import { useCompanionStore } from '@/stores/companion'
import { photoUrl } from '@/services/api'
import { marked } from 'marked'

// 配置 marked：去掉可能产生 XSS 的选项
marked.setOptions({ breaks: true, gfm: true })

const props = defineProps<{ msg: ChatMessage }>()
const companion = useCompanionStore()

/** Markdown → HTML，空内容返回 '' */
const renderedContent = computed(() => {
  const raw = props.msg.content?.trim()
  if (!raw) return ''
  try {
    // 智能预处理：确保单行文本（如开场白）后紧跟的 # 标题或 - 列表自动空行分割，满足 GFM 标准规范
    const formattedRaw = raw
      .replace(/([^\n])\n(#+\s+)/g, '$1\n\n$2')
      .replace(/([^\n])\n(-\s+\[)/g, '$1\n\n$2')
    return (marked.parse(formattedRaw) as string)
      .replace(/<a /g, '<a target="_blank" rel="noopener noreferrer" ')
  } catch {
    return raw
  }
})

/** 是否为独立表情包气泡（content 空，只有 meme） */
const isMemeOnly = computed(
  () => !!props.msg.meme && (!props.msg.content || props.msg.content.trim() === ''),
)

/** 根据当前应用模式给气泡加主题 class */
const bubbleClass = computed(() => [
  props.msg.role,
  companion.mode,
  isMemeOnly.value ? 'meme-only' : '',
])

// 表情包大图预览
const memePreview = ref(false)

// Token 明细面板展开状态
const showTokenDetail = ref(false)

function toggleTokenDetail() {
  showTokenDetail.value = !showTokenDetail.value
}

// 时间格式化
const timeStr = computed(() =>
  new Date(props.msg.createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
)

// 简单 token 显示：prompt + completion = total
function formatTokens(usage: ChatMessage['tokenUsage']): string {
  if (!usage) return ''
  return `${usage.promptTokens} + ${usage.completionTokens} = ${usage.totalTokens}`
}

// 缓存命中率
const cacheHitRate = computed(() => {
  const u = props.msg.tokenUsage
  if (!u) return 0
  const prompt = u.promptTokens || 0
  if (prompt === 0) return 0
  return Math.round(((u.cachedTokens || 0) / prompt) * 1000) / 10
})

// 耗时（秒）
const elapsedSec = computed(() => {
  const ms = props.msg.tokenUsage?.elapsedMs
  if (ms === undefined || ms === null) return null
  return (ms / 1000).toFixed(1)
})

// 数字千分位格式化
function fmt(n: number | undefined): string {
  if (n === undefined || n === null) return '0'
  return n.toLocaleString('en-US')
}

// 是否有可靠的思考/回复拆分数据
const hasReasoning = computed(() => (props.msg.tokenUsage?.reasoningTokens || 0) > 0)

// 是否有可靠的缓存数据
const hasCache = computed(() => (props.msg.tokenUsage?.cachedTokens || 0) > 0)

// 头像 - 助手（复用 store 中统一逻辑）
// 依赖 avatarRetry：失败计数变化时重新求值并拼上 ?retry=N 破除浏览器 404 缓存
const assistantAvatarSrc = computed(() => {
  const base = companion.getCurrentAvatar()
  if (!base) return base
  const sep = base.includes('?') ? '&' : '?'
  return `${base}${sep}_rt=${avatarRetry.value}`
})

/** 头像失败重试计数器：每失败一次计数+1，迫使 Vue 响应式重新求值并带上
 *  不同 query string 以破除浏览器/WebView2 对 404 的强缓存。 */
const avatarRetry = ref(0)

function handleAvatarError(_e: Event) {
  const currentSrc = assistantAvatarSrc.value
  if (currentSrc) {
    companion.markAvatarFailed(currentSrc)
  }
  // 不直接修改 target.src —— 那会覆盖 Vue 的 :src 绑定，导致响应式断开。
  // 改为 bump 计数器，让 assistantAvatarSrc 重新求值（带 ?retry= N 破缓存）。
  avatarRetry.value++
}

// ── 右键菜单：删除单条消息 ──────────────────────────────
const menuVisible = ref(false)
const menuX = ref(0)
const menuY = ref(0)
const deleting = ref(false)

function openMenu(e: MouseEvent) {
  // 屏蔽左键/长按触发的 contextmenu（仅响应真实右键）
  if (e.button !== 2 && !(e.ctrlKey && e.button === 0)) return
  menuX.value = e.clientX
  menuY.value = e.clientY
  menuVisible.value = true
}

function closeMenu() {
  menuVisible.value = false
}

async function onDeleteMessage() {
  if (deleting.value) return
  deleting.value = true
  try {
    await companion.deleteMessage(props.msg.id)
    closeMenu()
  } finally {
    deleting.value = false
  }
}

function onGlobalClick() {
  closeMenu()
}

onMounted(() => document.addEventListener('click', onGlobalClick))
onUnmounted(() => document.removeEventListener('click', onGlobalClick))
</script>

<template>
  <div class="bubble-wrapper" :class="msg.role" @contextmenu.prevent="openMenu">
    <!-- 助手头像（左侧） -->
    <div v-if="msg.role === 'assistant'" class="avatar-col">
      <img :src="assistantAvatarSrc" class="avatar-img" alt="流萤" @error="handleAvatarError" />
    </div>

    <!-- 独立表情包气泡：只显示图片（不需要聊天框，剥离 assistant.daily 胶囊样式） -->
    <div v-if="isMemeOnly" class="bubble meme-bubble meme-only">
      <img
        :src="msg.meme"
        class="meme-img-standalone"
        alt="表情"
        @click="memePreview = true"
      />
    </div>

    <!-- 普通气泡：文字 + 可选表情 -->
    <div v-else class="bubble-col">
      <div class="bubble" :class="bubbleClass">
        <div class="content markdown-body" v-html="renderedContent"></div>
        <!-- 日常模式装饰元素（阶段11 v4：拆分素材定位） -->
        <template v-if="msg.role === 'assistant' && companion.mode === 'daily'">
          <img class="bubble-deco butterfly" src="/chat-butterfly.png" alt="" aria-hidden="true" />
          <img class="bubble-deco potion" src="/chat-potion.png" alt="" aria-hidden="true" />
          <img class="bubble-deco cat" src="/chat-cat.png" alt="" aria-hidden="true" />
        </template>
        <!-- 工作模式装饰元素（萨姆：齿轮 / 能源 / 萨姆） -->
        <template v-else-if="msg.role === 'assistant' && companion.mode === 'work'">
          <img class="bubble-deco gear" src="/chat-gear.png" alt="" aria-hidden="true" />
          <img class="bubble-deco energy" src="/chat-energy.png" alt="" aria-hidden="true" />
          <img class="bubble-deco sam" src="/chat-sam.png" alt="" aria-hidden="true" />
        </template>
        <!-- 用户气泡装饰元素（日常/工作通用：浣熊在左，棒球/手套在右，适配用户居右） -->
        <template v-else-if="msg.role === 'user'">
          <img class="bubble-deco raccoon" src="/chat-raccoon.png" alt="" aria-hidden="true" />
          <img class="bubble-deco baseball" src="/chat-baseball.png" alt="" aria-hidden="true" />
          <img class="bubble-deco glove" src="/chat-glove.png" alt="" aria-hidden="true" />
        </template>
      </div>
      <!-- 时间戳 + Token 明细 -->
      <div v-if="msg.tokenUsage" class="msg-meta" :class="msg.role">
        <span class="ts">{{ timeStr }}</span>
        <button
          class="token-info"
          :class="{ active: showTokenDetail }"
          @click="toggleTokenDetail"
          title="点击查看 Token 消耗明细"
        >
          🎫 {{ formatTokens(msg.tokenUsage) }}
        </button>
      </div>
      <div v-else class="msg-meta" :class="msg.role">
        <span class="ts">{{ timeStr }}</span>
      </div>
    </div>

    <!-- 用户头像（右侧） -->
    <div v-if="msg.role === 'user'" class="avatar-col">
      <img :src="photoUrl('user.png')" class="avatar-img user-avatar-style" alt="用户" />
    </div>

    <!-- CodeBuddy 风格 Token 明细面板 -->
    <Teleport to="body">
      <div
        v-if="showTokenDetail && msg.tokenUsage"
        class="token-detail-overlay"
        @click.self="showTokenDetail = false"
      >
        <div class="token-detail-panel" @click.stop>
          <!-- 标题栏 -->
          <div class="tdp-header">
            <span class="tdp-title">Token 消耗明细</span>
            <button class="tdp-close" @click="showTokenDetail = false">×</button>
          </div>

          <!-- 总计行 -->
          <div class="tdp-row tdp-total">
            <span class="tdp-row-label">总计</span>
            <span class="tdp-row-value tdp-total-num">{{ fmt(msg.tokenUsage.totalTokens) }}</span>
          </div>

          <!-- 输入分组 -->
          <div class="tdp-group">
            <div class="tdp-group-header">
              <span class="tdp-dot dot-input" />
              <span class="tdp-group-label">输入</span>
              <span class="tdp-group-total">{{ fmt(msg.tokenUsage.promptTokens) }}</span>
            </div>
            <!-- 仅当有缓存数据时才显示缓存拆分 -->
            <template v-if="hasCache">
              <div class="tdp-sub-row">
                <span class="tdp-sub-label">缓存命中</span>
                <span class="tdp-sub-value">{{ fmt(msg.tokenUsage.cachedTokens) }}</span>
              </div>
              <div class="tdp-sub-row">
                <span class="tdp-sub-label">缓存未命中</span>
                <span class="tdp-sub-value">
                  {{ fmt((msg.tokenUsage.promptTokens || 0) - (msg.tokenUsage.cachedTokens || 0)) }}
                </span>
              </div>
            </template>
          </div>

          <!-- 输出分组 -->
          <div class="tdp-group">
            <div class="tdp-group-header">
              <span class="tdp-dot dot-output" />
              <span class="tdp-group-label">输出</span>
              <span class="tdp-group-total">{{ fmt(msg.tokenUsage.completionTokens) }}</span>
            </div>
            <!-- 有思考数据时显示拆分 -->
            <template v-if="hasReasoning">
              <div class="tdp-sub-row">
                <span class="tdp-dot dot-reasoning" />
                <span class="tdp-sub-label">思考过程</span>
                <span class="tdp-sub-value">{{ fmt(msg.tokenUsage.reasoningTokens) }}</span>
              </div>
              <div class="tdp-sub-row">
                <span class="tdp-dot dot-reply" />
                <span class="tdp-sub-label">回复内容</span>
                <span class="tdp-sub-value">{{ fmt(msg.tokenUsage.replyTokens) }}</span>
              </div>
            </template>
          </div>

          <!-- 缓存命中率（仅在有缓存数据时显示） -->
          <div v-if="hasCache" class="tdp-hitrate">
            <div class="tdp-hitrate-label">
              <span class="tdp-hitrate-icon">⚡</span>
              <span>缓存命中率</span>
              <span class="tdp-hitrate-pct" :class="{ zero: cacheHitRate === 0 }">
                {{ cacheHitRate }}%
              </span>
            </div>
            <div class="tdp-progress-bar">
              <div
                class="tdp-progress-fill"
                :style="{ width: cacheHitRate + '%' }"
              />
            </div>
          </div>

          <!-- 底部状态栏 -->
          <div class="tdp-footer">
            <span v-if="elapsedSec" class="tdp-footer-item">⏱ {{ elapsedSec }}s</span>
            <span class="tdp-footer-item">本次 {{ fmt(msg.tokenUsage.totalTokens) }} tokens</span>
          </div>
          <!-- 会话累计 -->
          <div class="tdp-footer tdp-footer-cumulative">
            <span class="tdp-footer-item">📊 会话累计</span>
            <span class="tdp-footer-item">
              入 {{ fmt(companion.sessionTotalPrompt) }} · 出 {{ fmt(companion.sessionTotalCompletion) }} · 共 {{ fmt(companion.sessionTotalTokens) }}
            </span>
          </div>
        </div>
      </div>
    </Teleport>
  </div>

  <!-- 表情包大图预览 -->
  <Teleport to="body">
    <div v-if="memePreview" class="meme-overlay" @click="memePreview = false">
      <img :src="msg.meme!" class="meme-full" alt="表情预览" />
    </div>
  </Teleport>

  <!-- 右键菜单：删除单条消息 -->
  <Teleport to="body">
    <div
      v-if="menuVisible"
      class="msg-context-menu"
      :style="{ left: menuX + 'px', top: menuY + 'px' }"
      @click.stop
    >
      <button class="cm-item cm-danger" :disabled="deleting" @click="onDeleteMessage">
        <span class="cm-icon">🗑</span>
        <span>{{ deleting ? '删除中…' : '删除该消息' }}</span>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.bubble-wrapper {
  display: flex;
  margin: 8px 0;
  animation: bubble-pop-in 0.25s var(--ease-spring);
  gap: 8px;
}

.bubble-wrapper.user {
  flex-direction: row;
  justify-content: flex-end;
}

.bubble-wrapper.assistant {
  flex-direction: row;
  justify-content: flex-start;
}

@keyframes bubble-pop-in {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* ── 头像列 ─────────────────────────────────── */
.avatar-col {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  margin-top: 2px;
}

.avatar-img {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  object-fit: cover;
  display: block;
  border: 1px solid var(--border-subtle);
}

.avatar-img.user-avatar-style {
  border: 1px solid var(--border-accent);
}

/* ── 气泡列 ─────────────────────────────────── */
.bubble-col {
  display: flex;
  flex-direction: column;
  max-width: 72%;
}

.bubble {
  position: relative;
  padding: 9px 14px;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.65;
  word-break: break-word;
  transition: transform 0.2s var(--ease-spring), box-shadow 0.2s, border-color 0.2s;
}

.bubble:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

/* 文字层：确保浮于 ::before 光晕之上，长文本不会被光晕遮盖 */
.bubble .content {
  position: relative;
  z-index: 1;
}

/* ── Markdown 渲染样式 ────────────────────────────── */
.markdown-body {
  word-break: break-word;
  line-height: 1.7;
}
.markdown-body h1, .markdown-body h2, .markdown-body h3 {
  margin: 12px 0 6px;
  font-weight: bold;
  line-height: 1.4;
}
.markdown-body h3 { font-size: 1.1em; }
.markdown-body h2 { font-size: 1.2em; }
.markdown-body > *:first-child { margin-top: 0; }
.markdown-body > *:last-child { margin-bottom: 0; }
.markdown-body p { margin: 4px 0; }
.markdown-body strong { font-weight: bold; }
.markdown-body em { font-style: italic; }
.markdown-body code {
  background: rgba(127, 127, 127, 0.12);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.9em;
  font-family: "Cascadia Code", "Fira Code", Consolas, monospace;
}
.markdown-body pre {
  background: rgba(0, 0, 0, 0.06);
  padding: 10px 14px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 0.85em;
  line-height: 1.5;
  margin: 8px 0;
}
.markdown-body pre code {
  background: none;
  padding: 0;
  border-radius: 0;
}
.markdown-body ul, .markdown-body ol {
  padding-left: 20px;
  margin: 6px 0;
}
.markdown-body li { margin: 2px 0; }
.markdown-body blockquote {
  border-left: 3px solid rgba(127, 127, 127, 0.3);
  padding-left: 12px;
  margin: 8px 0;
  color: inherit;
  opacity: 0.85;
}
.markdown-body hr {
  border: none;
  border-top: 1px solid rgba(127, 127, 127, 0.2);
  margin: 12px 0;
}
.markdown-body a {
  color: var(--bubble-link, #3b82f6);
  text-decoration: underline;
}

/* ── 用户气泡（基础：work 模式微信绿） ─────────── */
.bubble.user {
  background: var(--bubble-user-bg);
  color: var(--bubble-user-text);
  border-bottom-right-radius: 4px;
}

/* ── 用户气泡 · 日常模式 — 药丸形（与日常助手气泡对称）── */
.bubble.user.daily {
  background: #eef4fb;
  border: 1.5px solid #8fb8d8;
  border-radius: 28px;
  padding: 14px 24px 14px 100px;
  min-height: 56px;
  box-shadow: 0 2px 8px rgba(120, 160, 200, 0.1);
  position: relative;
  color: #1f3a4d;
}

.bubble.user.daily:hover {
  border-color: #6fa0c8;
  box-shadow: 0 4px 12px rgba(120, 160, 200, 0.15);
}

/* 中间蓝色椭圆光晕 */
.bubble.user.daily::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 96%;
  height: 70%;
  background: radial-gradient(
    ellipse 100% 100% at center,
    rgba(180, 210, 235, 0.35) 0%,
    rgba(205, 225, 240, 0.25) 50%,
    rgba(220, 235, 245, 0.1) 80%,
    rgba(255, 255, 255, 0) 98%
  );
  border-radius: 28px;
  pointer-events: none;
  z-index: 0;
}

/* 浣熊（代替小猫）：左下外侧，朝向中心 */
.bubble-deco.raccoon {
  left: -18px;
  bottom: -28px;
  width: 116px;
  height: auto;
}

/* 棒球（代替蝴蝶）：右上外侧 */
.bubble-deco.baseball {
  right: -18px;
  top: -8px;
  width: 44px;
  height: auto;
}

/* 手套（代替药水）：右下外侧 */
.bubble-deco.glove {
  right: -24px;
  bottom: -8px;
  width: 56px;
  height: auto;
}

/* ── 用户气泡 · 工作模式 — 暖铜药丸（与萨姆气泡对称）── */
.bubble.user.work {
  background: #fffaf4;
  border: 1.5px solid #d49060;
  border-radius: 28px;
  padding: 14px 24px 14px 100px;
  min-height: 56px;
  box-shadow: 0 2px 8px rgba(200, 150, 100, 0.1);
  position: relative;
  color: #3a2520;
}

.bubble.user.work:hover {
  border-color: #c07840;
  box-shadow: 0 4px 12px rgba(200, 150, 100, 0.15);
}

/* 中间暖橙椭圆光晕 */
.bubble.user.work::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 96%;
  height: 70%;
  background: radial-gradient(
    ellipse 100% 100% at center,
    rgba(230, 185, 140, 0.35) 0%,
    rgba(240, 205, 165, 0.25) 50%,
    rgba(245, 220, 190, 0.1) 80%,
    rgba(255, 255, 255, 0) 98%
  );
  border-radius: 28px;
  pointer-events: none;
  z-index: 0;
}

/* ── 助手气泡 · 日常模式 — CSS 复刻药丸形气泡（阶段11 v6）── */
.bubble.assistant.daily {
  background: #ffffff;
  border: 1.5px solid #b8c895;
  border-radius: 28px;
  padding: 14px 100px 14px 24px;
  min-height: 56px;
  box-shadow: 0 2px 8px rgba(180, 180, 130, 0.08);
  position: relative;
  color: #2d3a32;
}

.bubble.assistant.daily:hover {
  border-color: #9bb870;
  box-shadow: 0 4px 12px rgba(180, 180, 130, 0.12);
}

/* 中间绿色椭圆光晕（用 ::before 模拟药丸中间的渐变） */
.bubble.assistant.daily::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 96%;
  height: 70%;
  background: radial-gradient(
    ellipse 100% 100% at center,
    rgba(190, 225, 200, 0.35) 0%,
    rgba(210, 235, 215, 0.25) 50%,
    rgba(225, 240, 225, 0.1) 80%,
    rgba(255, 255, 255, 0) 98%
  );
  border-radius: 28px;
  pointer-events: none;
  z-index: 0;
}

/* 分割装饰元素通用 */
.bubble-deco {
  position: absolute;
  pointer-events: none;
  user-select: none;
  z-index: 2;
}

/* 蝴蝶：药水正上方（左侧装饰柱） */
.bubble-deco.butterfly {
  left: -18px;
  top: -8px;
  width: 44px;
  height: auto;
}

/* 药水（蝴蝶结+珠子+叶子）：左下外侧 */
.bubble-deco.potion {
  left: -24px;
  bottom: -8px;
  width: 56px;
  height: auto;
}

/* 小猫 + Meow 气泡：半个身子在气泡内 + 半个溢出气泡外 */
.bubble-deco.cat {
  right: -18px;
  bottom: -28px;
  width: 116px;
  height: auto;
}

/* ── 助手气泡 · 工作模式（萨姆 · 暖铜药丸风） ── */
.bubble.assistant.work {
  background: #ffffff;
  border: 1.5px solid #d49060;
  border-radius: 28px;
  padding: 14px 100px 14px 24px;
  min-height: 56px;
  box-shadow: 0 2px 8px rgba(200, 150, 100, 0.08);
  position: relative;
  color: #3a2520;
}

.bubble.assistant.work:hover {
  border-color: #c07840;
  box-shadow: 0 4px 12px rgba(200, 150, 100, 0.12);
}

/* 中间暖橙椭圆光晕（用 ::before 模拟药丸中间的渐变） */
.bubble.assistant.work::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 96%;
  height: 70%;
  background: radial-gradient(
    ellipse 100% 100% at center,
    rgba(230, 185, 140, 0.35) 0%,
    rgba(240, 205, 165, 0.25) 50%,
    rgba(245, 220, 190, 0.1) 80%,
    rgba(255, 255, 255, 0) 98%
  );
  border-radius: 28px;
  pointer-events: none;
  z-index: 0;
}

/* 齿轮（代替蝴蝶）：左上外侧 */
.bubble-deco.gear {
  left: -18px;
  top: -8px;
  width: 44px;
  height: auto;
}

/* 能源（代替药水）：左下外侧 */
.bubble-deco.energy {
  left: -20px;
  bottom: -6px;
  width: 40px;
  height: auto;
}

/* 萨姆（代替小猫）：右下溢出 */
.bubble-deco.sam {
  right: -18px;
  bottom: -28px;
  width: 116px;
  height: auto;
}

/* ── 独立表情包气泡（无文字，仅图片） ────────── */
.bubble.meme-only {
  max-width: 200px;
  padding: 6px;
  background: transparent !important;
  border: none !important;
  border-radius: 12px;
  overflow: hidden;
}

.meme-img-standalone {
  display: block;
  max-width: 180px;
  max-height: 180px;
  border-radius: 10px;
  cursor: pointer;
  object-fit: contain;
  transition: transform var(--transition-fast);
}

.meme-img-standalone:hover {
  transform: scale(1.05);
}

/* ── 消息元信息（时间戳 + Token） ─────────────── */
.msg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 3px;
  font-size: 10px;
}

.msg-meta.user {
  justify-content: flex-end;
  padding-right: 4px;
}

.msg-meta.assistant {
  justify-content: flex-start;
  padding-left: 4px;
}

.ts {
  color: var(--text-muted);
  white-space: nowrap;
}

.token-info {
  color: var(--text-muted);
  white-space: nowrap;
  font-family: 'Courier New', monospace;
  font-size: 9px;
  border: 1px solid transparent;
  background: transparent;
  padding: 1px 6px;
  border-radius: 4px;
  cursor: pointer;
  transition: color var(--transition-fast), background var(--transition-fast), border-color var(--transition-fast);
}

.token-info:hover {
  color: var(--accent-strong);
  background: var(--accent-light);
  border-color: var(--border-accent);
}

.token-info.active {
  color: var(--accent-strong);
  background: var(--accent-light);
  border-color: var(--accent);
}

/* ── 大图预览 ────────────────────────────────── */
.meme-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.meme-full {
  max-width: 80vw;
  max-height: 80vh;
  border-radius: 16px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
}
</style>

<!-- 非 scoped：明细面板用 Teleport 移到 body，全局生效 -->
<style>
.token-detail-overlay {
  position: fixed;
  inset: 0;
  z-index: 300;
  background: rgba(0, 0, 0, 0.25);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: tdp-fade 0.18s ease;
}

@keyframes tdp-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}

.token-detail-panel {
  width: 320px;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--border-subtle, rgba(0, 0, 0, 0.1));
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
  padding: 14px 16px 10px;
  font-size: 13px;
  color: rgba(0, 0, 0, 0.85);
  animation: tdp-pop 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes tdp-pop {
  from { transform: scale(0.96) translateY(8px); opacity: 0; }
  to { transform: scale(1) translateY(0); opacity: 1; }
}

/* 工作模式：暖铜浅色风（与萨姆药丸气泡一致） */
.chat-client-root.work .token-detail-panel {
  background: rgba(255, 252, 248, 0.98);
  border-color: rgba(212, 144, 96, 0.45);
  color: rgba(58, 37, 32, 0.92);
  box-shadow: 0 12px 40px rgba(200, 150, 100, 0.25);
}

.tdp-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 700;
  font-size: 14px;
  margin-bottom: 10px;
}

.tdp-title {
  color: inherit;
}

.tdp-close {
  border: none;
  background: none;
  color: var(--text-muted, #888);
  font-size: 20px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  border-radius: 4px;
}

.tdp-close:hover {
  background: rgba(0, 0, 0, 0.06);
}

/* 总计行 */
.tdp-total {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  margin-bottom: 8px;
}

.chat-client-root.work .tdp-total {
  border-bottom-color: rgba(212, 144, 96, 0.25);
}

.tdp-row-label {
  font-size: 13px;
  color: var(--text-muted, #666);
}

.tdp-total-num {
  font-size: 18px;
  font-weight: 700;
  font-family: 'Courier New', monospace;
  color: var(--accent-strong, #2e7d52);
}

.chat-client-root.work .tdp-total-num {
  color: #c07840;
}

/* 分组 */
.tdp-group {
  margin: 8px 0;
  padding: 4px 0;
}

.tdp-group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 4px;
}

.tdp-group-label {
  flex: 1;
}

.tdp-group-total {
  font-family: 'Courier New', monospace;
  font-weight: 700;
}

.tdp-sub-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0 2px 18px;
  font-size: 12px;
  color: var(--text-muted, #666);
}

.tdp-sub-label {
  flex: 1;
}

.tdp-sub-value {
  font-family: 'Courier New', monospace;
  font-weight: 600;
}

/* 圆点 */
.tdp-dot {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  flex-shrink: 0;
}

.dot-input       { background: #4488ff; }
.dot-output      { background: #a855f7; }
.dot-cache-hit   { background: #22c55e; }
.dot-cache-miss  { background: #ef4444; }
.dot-cache-write { background: #eab308; }
.dot-reasoning   { background: #ec4899; }
.dot-reply       { background: #06b6d4; }

/* 缓存命中率 */
.tdp-hitrate {
  margin: 10px 0 6px;
  padding: 8px 10px;
  background: rgba(0, 0, 0, 0.03);
  border-radius: 8px;
}

.chat-client-root.work .tdp-hitrate {
  background: rgba(212, 144, 96, 0.1);
}

.tdp-hitrate-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  margin-bottom: 6px;
}

.tdp-hitrate-icon {
  font-size: 12px;
}

.tdp-hitrate-pct {
  margin-left: auto;
  font-family: 'Courier New', monospace;
  font-weight: 700;
  color: #22c55e;
}

.tdp-hitrate-pct.zero {
  color: var(--text-muted, #888);
}

.tdp-progress-bar {
  height: 5px;
  background: rgba(0, 0, 0, 0.08);
  border-radius: 3px;
  overflow: hidden;
}

.chat-client-root.work .tdp-progress-bar {
  background: rgba(212, 144, 96, 0.18);
}

.tdp-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #22c55e, #4ade80);
  border-radius: 3px;
  transition: width 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.chat-client-root.work .tdp-progress-fill {
  background: linear-gradient(90deg, #d49060, #e8b07a);
}

/* 底部 */
.tdp-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: var(--text-muted, #888);
  padding-top: 8px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
}

.chat-client-root.work .tdp-footer {
  border-top-color: rgba(212, 144, 96, 0.25);
}

.tdp-footer-item {
  font-family: 'Courier New', monospace;
}

.tdp-footer-cumulative {
  border-top: 1px dashed rgba(0, 0, 0, 0.1);
  padding-top: 6px;
  margin-top: 4px;
  font-size: 10px;
  opacity: 0.8;
}
</style>

<!-- 右键菜单样式（Teleport 到 body，需非 scoped） -->
<style>
.msg-context-menu {
  position: fixed;
  z-index: 9999;
  min-width: 150px;
  padding: 6px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  animation: cm-pop 0.12s ease-out;
  transform-origin: top left;
}

@keyframes cm-pop {
  from {
    opacity: 0;
    transform: scale(0.96);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.cm-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  border: none;
  background: transparent;
  border-radius: 7px;
  font-size: 13px;
  color: #e5484d;
  cursor: pointer;
  transition: background 0.15s;
}

.cm-item:hover {
  background: rgba(229, 72, 77, 0.08);
}

.cm-item:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.cm-icon {
  font-size: 14px;
}

/* 深色主题适配（ChatPanel 根节点有 .dark 时） */
.dark .msg-context-menu {
  background: rgba(32, 32, 36, 0.96);
  border-color: rgba(255, 255, 255, 0.1);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
}

.dark .cm-item {
  color: #ff6b6b;
}

.dark .cm-item:hover {
  background: rgba(255, 107, 107, 0.12);
}
</style>

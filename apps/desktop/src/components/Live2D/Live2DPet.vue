<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useCompanionStore } from '@/stores/companion'

const companion = useCompanionStore()
const container = ref<HTMLDivElement>()
const canvasWrapper = ref<HTMLDivElement>()

// ═══════════════════════════════════════════════════════════
// 阶段 7 动作优先级状态机 (IDLE → SPEAKING ←→ REACTING)
// ═══════════════════════════════════════════════════════════
type ActionState = 'IDLE' | 'SPEAKING' | 'REACTING'
let actionState: ActionState = 'IDLE'
let prevActionState: ActionState = 'IDLE'            // REACTING 结束后恢复
let pendingEmotion: string | null = null              // 语音期间去重保留最后一个情绪
let reactionEndResolve: (() => void) | null = null    // REACTING 完成回调
let reactionTimeout: number | null = null              // REACTING 超时兜底

// 热区点击悬浮气泡提示
const toastMessage = ref('')
let toastTimer: number | null = null

function showToast(msg: string) {
  toastMessage.value = msg
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => {
    toastMessage.value = ''
  }, 2500)
}

let app: any = null
let model: any = null
let resizeObserver: ResizeObserver | null = null
let globalFocusTimer: number | null = null

// 阶段 A 核心系统：待机动作轮询与情绪淡出定时器
let idleTimer: number | null = null
let emotionRevertTimer: number | null = null
let starSeaDrinkTimer: number | null = null   // 点燃星海后 5s 自动喝饮料

// 阶段 7 语音播放计时器（用于 SPEAKING 状态自动回归）
let speakingTimer: number | null = null

/** 语音开始：进 SPEAKING 状态，按文本长度估算持续时间后自动回归 */
function onVoiceStart(textLen: number) {
  if (actionState === 'REACTING') {
    // 互动动作中不打断，只标记有语音待处理
    return
  }
  // 中断旧语音计时器
  if (speakingTimer) clearTimeout(speakingTimer)
  actionState = 'SPEAKING'
  // 估算：中文 ~4 字/秒，最少 2 秒
  const estimatedSec = Math.max(2, Math.ceil(textLen / 4))
  speakingTimer = window.setTimeout(() => {
    speakingTimer = null
    onSpeechEnded()
  }, estimatedSec * 1000)
}

/** 语音结束：消费情绪队列，回归 IDLE */
function onSpeechEnded() {
  if (actionState === 'SPEAKING') {
    const emo = pendingEmotion
    pendingEmotion = null
    if (emo) {
      setTimeout(() => {
        if (actionState === 'IDLE') applyEmotionWithAutoRevert(emo)
      }, 1000)
    }
    actionState = 'IDLE'
    resetIdleTimer()
  }
}

// 状态控制：墨镜与猫耳的切换开关
let sunglassesOn = false
let catEarsOn = false
let isToggleReaction = false  // 当前反应是否为 toggle（墨镜/猫耳），跳过强制回正

// 区分模式：解锁移动状态用于拖拽窗口，锁住固定状态用于触发点击互动
function onCanvasPointerDown(e: MouseEvent) {
  if (e.button !== 0) return

  // 解锁移动模式（petLocked === false）：专门用于抓取并拖拽窗口调整桌面位置
  if (!companion.petLocked) {
    try {
      import('@tauri-apps/api/window').then(({ getCurrentWindow }) => {
        getCurrentWindow().startDragging()
      })
    } catch {}
  }
}

// 情绪与 Live2D 表情/动作映射
// 对话中的情绪变化只用 expression（瞬时，不冲突定时器）；
// Motion 动画留给 PROACTIVE_MOTION_MAP 处理（主动聊天事件，有独立生命周期）
const EMOTION_MAP: Record<string, { expression: number }> = {
  happy:     { expression: 10 },   // 嘻嘻
  sad:       { expression: 7  },   // 哭泣
  angry:     { expression: 5  },   // 生气
  shy:       { expression: 6  },   // 疑问（低头接近害羞）
  thinking:  { expression: 6  },   // 疑问（歪头思考）
  surprised: { expression: 9  },   // 呆愣
  neutral:   { expression: 0  },   // 回正
  work:      { expression: 1  },   // 墨镜
}

// 主动聊天动作映射 — 将后端字符串名转为数字索引
// 引擎B空闲触发 / 复查跟进 使用（低频事件，可用完整动画）
const PROACTIVE_MOTION_MAP: Record<string, { expression?: number; motion?: [string, number] }> = {
  greet:    { expression: 10, motion: ['表情组', 10] },   // 嘻嘻 — 温柔打招呼
  gentle:   { expression: 6,  motion: ['表情组', 6]  },   // 疑问 — 关切凝视
  care:     { expression: 6,  motion: ['表情组', 6]  },   // 疑问 — 忧心关切
  smile:    { expression: 10 },                            // 嘻嘻 — 纯微笑
  caring:   { expression: 6  },                            // 疑问 — 关切表情
}

function applyEmotion(emotionLabel: string) {
  if (!model) return
  const mapping = EMOTION_MAP[emotionLabel] || EMOTION_MAP.neutral
  if (mapping.expression !== undefined && typeof model.expression === 'function') {
    model.expression(mapping.expression)
  }
}

/** 应用主动聊天表情 — 使用 PROACTIVE_MOTION_MAP 解析后端传来的字符串名 */
function applyProactiveExpression(motionName: string, expressionName?: string) {
  if (!model) return

  // 优先用 motionName（如 "greet", "gentle"），否则用 expressionName（如 "smile", "caring"）
  const key = motionName || expressionName || ''
  const mapping = PROACTIVE_MOTION_MAP[key] || PROACTIVE_MOTION_MAP['greet']

  // 保存 pre-action 状态以支持动画结束后回归
  prevActionState = actionState
  actionState = 'REACTING'

  if (mapping.motion && typeof model.motion === 'function') {
    model.motion(mapping.motion[0], mapping.motion[1])
  } else if (mapping.expression !== undefined && typeof model.expression === 'function') {
    model.expression(mapping.expression)
  }

  // motion 动画结束后自动回归（加超时兜底）
  if (reactionTimeout) clearTimeout(reactionTimeout)
  const estimatedDuration = mapping.motion ? 3500 : 2500
  reactionTimeout = window.setTimeout(() => {
    onReactionComplete()
  }, estimatedDuration)
}

// 阶段 A：应用对话情绪并设置 6 秒超时自动回归常态 (防止悲伤/惊讶表情永久卡死)
function applyEmotionWithAutoRevert(emotionLabel: string) {
  if (!model) return

  applyEmotion(emotionLabel)

  if (emotionRevertTimer) clearTimeout(emotionRevertTimer)

  // 只要不是 neutral 或特定长状态，6 秒后优雅淡出回归默认 expression00
  if (emotionLabel !== 'neutral' && emotionLabel !== 'work') {
    emotionRevertTimer = window.setTimeout(() => {
      if (!model) return
      console.log('[pet] Emotion auto-reverted to neutral')
      if (typeof model.expression === 'function') {
        model.expression(0) // 回正
      }
      resetIdleTimer()
    }, 6000)
  }
}

// 阶段 A：Idle 待机轮询重置（仅 IDLE 状态触发动作）
function resetIdleTimer(customDelay?: number) {
  if (idleTimer) clearTimeout(idleTimer)

  // 默认 16 - 24 秒随机，可传 customDelay 覆盖（如点燃星海后快速回正）
  const delay = customDelay ?? (Math.floor(Math.random() * 8000) + 16000)
  idleTimer = window.setTimeout(() => {
    if (actionState === 'IDLE') {
      triggerIdleMotion()
    }
    resetIdleTimer()
  }, delay)
}

// 阶段 A：触发 3 组待机动作之一
function triggerIdleMotion() {
  if (!model || typeof model.motion !== 'function') return
  const idleIndex = Math.floor(Math.random() * 3)
  console.log('[pet] Idle motion triggered:', idleIndex)
  model.motion('Tick2', idleIndex)
}

/** 用户点击 HitArea 后的回调 */
function onReactionComplete() {
  if (reactionTimeout) {
    clearTimeout(reactionTimeout)
    reactionTimeout = null
  }
  if (reactionEndResolve) {
    reactionEndResolve()
    reactionEndResolve = null
  }
  actionState = prevActionState

  // 回到 IDLE：播放排队的情绪
  if (actionState === 'IDLE') {
    const emo = pendingEmotion
    pendingEmotion = null
    if (emo) applyEmotionWithAutoRevert(emo)
    resetIdleTimer()
  }

  // 确保表情回正 — 兜底 NextMtn 链不触发或表情回正定时器已取消的情况
  // 但跳过 toggle 反应（墨镜/猫耳），它们是持久状态不应被重置
  if (!isToggleReaction && model && typeof model.expression === 'function') {
    model.expression(0)
  }
  isToggleReaction = false
}

// 细粒度 HitAreas 热区与表情/动作精确映射
function handleHitAreas(hitAreaNames: string[]) {
  console.log('[pet] HitArea triggered:', hitAreaNames)

  // 保存当前状态
  prevActionState = actionState
  actionState = 'REACTING'
  isToggleReaction = false

  // 取消正在等待的表情回正定时器 — 防 motion 播放中被 model.expression(0) 覆写
  if (emotionRevertTimer) {
    clearTimeout(emotionRevertTimer)
    emotionRevertTimer = null
  }

  // 执行点击动作
  let hasMotion = false

  if (hitAreaNames.includes('刘海')) {
    sunglassesOn = !sunglassesOn
    if (typeof model.expression === 'function') {
      model.expression(sunglassesOn ? 1 : 0)
    }
    isToggleReaction = true  // 持久状态，不要让 onReactionComplete 重置
    showToast(sunglassesOn ? '🕶️ 开启酷酷墨镜！' : '✨ 摘下墨镜啦~')
  } else if (hitAreaNames.includes('右侧后发')) {
    catEarsOn = !catEarsOn
    if (typeof model.expression === 'function') {
      model.expression(catEarsOn ? 2 : 0)
    }
    isToggleReaction = true  // 持久状态，不要让 onReactionComplete 重置
    showToast(catEarsOn ? '🐱 开启萌萌猫耳！' : '✨ 恢复常态~')
  } else if (hitAreaNames.includes('蛋糕')) {
    if (typeof model.motion === 'function') {
      model.motion('表情组', 1)
      hasMotion = true
    }
    showToast('🍰 橡木蛋糕真好吃呀~')
  } else if (hitAreaNames.includes('左侧后发')) {
    if (typeof model.motion === 'function') {
      model.motion('表情组', 2)
      hasMotion = true
    }
    showToast('🔥 变身！点燃星海！')
    // 点燃星海 motion 结束后快速启动 idle 回正（NextMtn 链不可靠的兜底）
    setTimeout(() => {
      if (actionState === 'IDLE') resetIdleTimer(2000)
    }, 5200)
    // 点燃星海 5s 后自动喝饮料（连续动作彩蛋）
    if (starSeaDrinkTimer) clearTimeout(starSeaDrinkTimer)
    starSeaDrinkTimer = window.setTimeout(() => {
      starSeaDrinkTimer = null
      triggerDrink()
    }, 5000)
  } else if (hitAreaNames.includes('饮料')) {
    triggerDrink()
  } else {
    triggerRandomGeneralReaction()
    hasMotion = true
  }

  // 清除旧超时，启动新超时兜底
  if (reactionTimeout) clearTimeout(reactionTimeout)

  if (!hasMotion) {
    // 纯表情切换：300ms 恢复
    reactionTimeout = window.setTimeout(onReactionComplete, 300)
  } else if (hitAreaNames.includes('左侧后发')) {
    // 变身动画 ~5s
    reactionTimeout = window.setTimeout(onReactionComplete, 5000)
  } else {
    // 其他 motion ~3s
    reactionTimeout = window.setTimeout(onReactionComplete, 3000)
  }
}

// 喝饮料动作（抽离复用：点击饮料热区 / 点燃星海后自动触发）
function triggerDrink() {
  if (!model) return
  // 进入 REACTING 状态（连续动作时打断当前待机轮询）
  prevActionState = actionState
  actionState = 'REACTING'
  isToggleReaction = false

  // 取消正在等待的表情回正定时器 — 防 motion 播放中被 model.expression(0) 覆写
  if (emotionRevertTimer) {
    clearTimeout(emotionRevertTimer)
    emotionRevertTimer = null
  }

  if (typeof model.expression === 'function') {
    model.expression(0)
  }
  if (typeof model.motion === 'function') {
    model.motion('Tick2', 0)
  }
  showToast('🍹 喝一口爽口的饮品~')

  // 饮料 motion ~3s 后自动回归
  if (reactionTimeout) clearTimeout(reactionTimeout)
  reactionTimeout = window.setTimeout(onReactionComplete, 3000)
}

// 触摸主体随机通用表情与动作
function triggerRandomGeneralReaction() {
  const reactions = [
    { msg: '嘻嘻~ 你好呀！', type: 'expression', val: 10 },
    { msg: '嗯？有什么吩咐吗？', type: 'expression', val: 6 },
    { msg: '呆... 呆住...', type: 'expression', val: 9 },
    { msg: '流萤一直都在陪伴着你哦~', type: 'motion', group: 'Tick2', index: 0 },
    { msg: '要和我一起看星空吗？', type: 'motion', group: 'Tick2', index: 1 },
  ]
  const item = reactions[Math.floor(Math.random() * reactions.length)]
  if (item.type === 'expression' && typeof model.expression === 'function') {
    model.expression(item.val)
  } else if (item.type === 'motion' && typeof model.motion === 'function') {
    model.motion(item.group, item.index)
  }
  showToast(item.msg)
}

// 模型自适应尺寸重算与居中
function fitModel() {
  if (!app || !model || !container.value) return

  const cw = container.value.clientWidth || 300
  const ch = container.value.clientHeight || 400

  app.renderer.resize(cw, ch)

  const currentScaleX = model.scale?.x || 1
  const currentScaleY = model.scale?.y || 1
  const rawW = model.width / currentScaleX
  const rawH = model.height / currentScaleY

  if (rawW > 0 && rawH > 0) {
    const scaleX = (cw * 0.96) / rawW
    const scaleY = (ch * 0.96) / rawH
    const fitScale = Math.min(scaleX, scaleY)

    model.scale.set(fitScale)

    if (model.anchor && typeof model.anchor.set === 'function') {
      model.anchor.set(0.5, 0.5)
      model.x = cw / 2
      model.y = ch / 2
    } else {
      model.x = (cw - model.width) / 2
      model.y = (ch - model.height) / 2
    }
  }
}

// 窗口内指针移动跟随
function handlePointerMove(e: MouseEvent) {
  if (model && typeof model.focus === 'function') {
    model.focus(e.clientX, e.clientY)
  }
}

// 全局屏幕光标追踪（跨窗口全屏互动）
async function setupGlobalCursorTracking() {
  try {
    const { getCurrentWindow, cursorPosition } = await import('@tauri-apps/api/window')
    const appWin = getCurrentWindow()

    globalFocusTimer = window.setInterval(async () => {
      if (!model || typeof model.focus !== 'function') return
      try {
        const cursor = await cursorPosition()
        const winPos = await appWin.outerPosition()

        const localX = cursor.x - winPos.x
        const localY = cursor.y - winPos.y

        model.focus(localX, localY)
      } catch { /* 静默捕获 */ }
    }, 50)
  } catch {
    /* 非 Tauri 环境退回到窗口内监听 */
  }
}

// 强制清除宿主容器背景色与阴影
function forcePurgeContainerBackgrounds() {
  try {
    document.documentElement.style.cssText += ';background:transparent !important;background-color:transparent !important;box-shadow:none !important;border:none !important;'
    document.body.style.cssText += ';background:transparent !important;background-color:transparent !important;box-shadow:none !important;border:none !important;'
    const appEl = document.getElementById('app')
    if (appEl) {
      appEl.style.cssText += ';background:transparent !important;background-color:transparent !important;box-shadow:none !important;border:none !important;'
    }
  } catch {}
}

// ── 情绪监听（含状态感知去重） ──
watch(() => companion.currentEmotion, (newEmotion) => {
  if (!newEmotion) return

  if (actionState === 'REACTING') {
    // 点击交互中不打断，只保留最后一个情绪
    pendingEmotion = newEmotion
    console.log('[pet] Emotion queued (state=' + actionState + '):', newEmotion)
    return
  }
  applyEmotionWithAutoRevert(newEmotion)
})

onMounted(async () => {
  forcePurgeContainerBackgrounds()

  // 禁用 Tauri 原生窗口阴影
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window')
    const appWin = getCurrentWindow()
    if (typeof appWin.setShadow === 'function') {
      await appWin.setShadow(false)
    }
  } catch {}

  try {
    // 彻底销毁与清理旧的 Pixi 实例与 Canvas DOM，防止 HMR 重载产生重复模型
    if (model) {
      try { model.destroy() } catch {}
      model = null
    }
    if (app) {
      try { app.destroy(true, { children: true }) } catch {}
      app = null
    }
    if (canvasWrapper.value) {
      canvasWrapper.value.innerHTML = ''
    }

    const { Application } = await import('pixi.js')
    const { Live2DModel } = await import('pixi-live2d-display')

    const cw = container.value!.clientWidth || 300
    const ch = container.value!.clientHeight || 400
    app = new Application({
      width: cw,
      height: ch,
      backgroundAlpha: 0,
      antialias: true,
      preserveDrawingBuffer: true,
    })
    
    const canvas = app.view as HTMLCanvasElement
    canvas.style.cssText = 'background:transparent !important;background-color:transparent !important;box-shadow:none !important;border:none !important;outline:none !important;display:block;'

    if (canvasWrapper.value) {
      canvasWrapper.value.appendChild(canvas)
    } else if (container.value) {
      container.value.appendChild(canvas)
    }

    canvas.addEventListener('mousedown', onCanvasPointerDown)
    window.addEventListener('pointermove', handlePointerMove)

    const core = (window as any).Live2DCubismCore
    if (core && typeof core.then === 'function') await core

    if (!core.Memory) core.Memory = {}
    if (!core.Memory.initializeAmountOfMemory) {
      core.Memory.initializeAmountOfMemory = () => {}
    }

    // 模型来源（T-20 切单轨配套，2026-08-06）：桌宠（电脑端）加载服务器 companion 的
    // Live2D 模型。原硬编码 127.0.0.1:8765 只对「桌宠与后端同机」成立，改造后后端在
    // Tailnet 服务器 → 改指向服务器；可用 localStorage `firefly_model_base` 覆盖。
    let modelBase = 'http://100.111.201.71:8765'
    try {
      if (typeof localStorage !== 'undefined') {
        const saved = localStorage.getItem('firefly_model_base')
        if (saved) modelBase = saved
      }
    } catch {
      // localStorage 不可用 → 用默认
    }
    const url = `${modelBase}/static/live2d/firefly/firefly.model3.json`
    model = await Live2DModel.from(url, { ticker: app.ticker })

    model.autoBreath = true
    model.autoBlink = true
    model.interactive = true

    // HitAreas 碰撞与点击事件驱动
    if (typeof model.on === 'function') {
      model.on('hit', (hitAreaNames: string[]) => {
        if (companion.petLocked) {
          handleHitAreas(hitAreaNames)
        }
      })

      // 主体点击事件兜底
      model.on('pointertap', (e: { data: { global: { x: number; y: number } } }) => {
        if (!companion.petLocked) return
        const hitAreas = model.hitTest ? model.hitTest(e.data.global.x, e.data.global.y) : []
        if (!hitAreas || hitAreas.length === 0) {
          handleHitAreas(['主体'])
        }
      })

      // 悬停更改手型指针光标
      model.on('pointerover', () => {
        if (container.value) container.value.style.cursor = 'pointer'
      })
      model.on('pointerout', () => {
        if (container.value) container.value.style.cursor = 'default'
      })
    }

    app.stage.addChild(model)

    fitModel()

    if (window.ResizeObserver && container.value) {
      resizeObserver = new ResizeObserver(() => fitModel())
      resizeObserver.observe(container.value)
    }

    setupGlobalCursorTracking()

    // 监听主聊天窗口广播过来的对话情绪变化与语音播放事件
    try {
      const { listen } = await import('@tauri-apps/api/event')
      listen('emotion-changed', (event: { payload: { emotion: string; label: string; intensity: number } }) => {
        const emo = event.payload?.label
        console.log('[pet] Received broadcast emotion from chat window:', emo)
        if (emo) {
          // 跨窗口情绪：同样走状态感知去重
          if (actionState === 'REACTING') {
            pendingEmotion = emo
          } else {
            applyEmotionWithAutoRevert(emo)
          }
        }
      })

      listen('play-voice', (event: { payload: { text: string } }) => {
        const text = event.payload?.text || ''
        if (text) {
          onVoiceStart(text.length)
        }
      })

      listen('firefly-reminder-fired', (event: { payload: { id: string; text: string; dueTimestamp: number } }) => {
        const payload = event.payload
        if (payload && payload.text) {
          showToast(`🔔 主人，到了您预约的 [${payload.text}] 时间啦！`)
          applyEmotionWithAutoRevert('happy')
        }
      })

      listen('proactive-speech-trigger', (event: { payload: { motion?: string; expression?: string; content?: string } }) => {
        const { motion, expression, content } = event.payload || {}
        console.log('[pet] Proactive speech trigger:', motion, expression, content)
        applyProactiveExpression(motion || '', expression || '')
        if (content) {
          showToast(content)
        }
      })
    } catch {}

    // 本地 DOM 事件兜底
    window.addEventListener('firefly-reminder-fired', ((e: CustomEvent) => {
      const item = e.detail
      if (item && item.text) {
        showToast(`🔔 主人，到了您预约的 [${item.text}] 时间啦！`)
        applyEmotionWithAutoRevert('happy')
      }
    }) as EventListener)

    window.addEventListener('play-voice', ((e: CustomEvent) => {
      const text = e.detail?.text || ''
      if (text) onVoiceStart(text.length)
    }) as EventListener)

    // 阶段 A：启动 Idle 待机动作轮询系统
    resetIdleTimer()

    if (companion.currentEmotion) {
      applyEmotionWithAutoRevert(companion.currentEmotion)
    }
  } catch (e: unknown) {
    console.error('[pet] CRASH:', e)
  }
})

onUnmounted(() => {
  if (speakingTimer) clearTimeout(speakingTimer)
  window.removeEventListener('pointermove', handlePointerMove)
  if (toastTimer) clearTimeout(toastTimer)
  if (idleTimer) clearTimeout(idleTimer)
  if (emotionRevertTimer) clearTimeout(emotionRevertTimer)
  if (starSeaDrinkTimer) clearTimeout(starSeaDrinkTimer)
  if (globalFocusTimer !== null) {
    clearInterval(globalFocusTimer)
    globalFocusTimer = null
  }
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (model) try { model.destroy() } catch {}
  if (app) try { app.destroy(true, { children: true }) } catch {}
})
</script>

<template>
  <div ref="container" class="root">
    <div ref="canvasWrapper" class="canvas-wrapper" />
    <!-- 热区交互浮动提示气泡 -->
    <transition name="toast-fade">
      <div v-if="toastMessage" class="hit-toast">
        {{ toastMessage }}
      </div>
    </transition>
  </div>
</template>

<style scoped>
:global(html),
:global(body),
:global(#app) {
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
  border: none !important;
  outline: none !important;
  overflow: hidden !important;
}

.root {
  width: 100%;
  height: 100%;
  position: relative;
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
  border: none !important;
  outline: none !important;
  overflow: hidden;
}

.canvas-wrapper {
  width: 100%;
  height: 100%;
  position: absolute;
  inset: 0;
  pointer-events: auto;
}

.root :deep(canvas) {
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
  border: none !important;
  outline: none !important;
}

/* 浮动提示气泡样式 */
.hit-toast {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(30, 43, 35, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: #ffffff;
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.18);
  pointer-events: none;
  z-index: 100;
  max-width: 260px;
  white-space: normal;
  text-align: center;
  line-height: 1.5;
  letter-spacing: 0.5px;
}

.toast-fade-enter-active,
.toast-fade-leave-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.toast-fade-enter-from,
.toast-fade-leave-to {
  opacity: 0;
  transform: translate(-50%, -10px) scale(0.95);
}
</style>

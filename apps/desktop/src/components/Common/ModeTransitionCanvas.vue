<script setup lang="ts">
import { ref, onUnmounted } from 'vue'

interface StarParticle {
  x: number
  y: number
  vx: number
  vy: number
  size: number
  rotation: number
  vRot: number
  alpha: number
  color: string
  decay: number
}

interface HexParticle {
  x: number
  y: number
  radius: number
  maxRadius: number
  rotation: number
  alpha: number
  color: string
}

interface StarParticle {
  x: number
  y: number
  vx: number
  vy: number
  size: number
  rotation: number
  vRot: number
  alpha: number
  color: string
  decay: number
}

interface HexParticle {
  x: number
  y: number
  radius: number
  maxRadius: number
  rotation: number
  alpha: number
  color: string
}

interface RadialPulseHalo {
  x: number
  y: number
  radius: number
  maxRadius: number
  alpha: number
  colorCore: string
  colorGlow: string
}

const canvasRef = ref<HTMLCanvasElement | null>(null)
let animId: number | null = null

const stars: StarParticle[] = []
const hexes: HexParticle[] = []
let halo: RadialPulseHalo | null = null

function trigger(x: number, y: number, targetMode: 'daily' | 'work') {
  const canvas = canvasRef.value
  if (!canvas) return

  const w = (canvas.width = window.innerWidth)
  const h = (canvas.height = window.innerHeight)

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  stars.length = 0
  hexes.length = 0

  const isWork = targetMode === 'work'

  // 1. 配色方案（极具呼吸感与角色调性）
  const starColors = isWork
    ? ['#ffffff', '#ff3300', '#ff8800', '#ff0055', '#ffccaa']
    : ['#ffffff', '#5ebd82', '#98f5be', '#c2ffd9', '#e0fff0']

  // 2. 生成 50+ 颗高亮四角星芒粒子 (向四周自然飘散)
  for (let i = 0; i < 50; i++) {
    const angle = Math.random() * Math.PI * 2
    const speed = Math.random() * (isWork ? 7 : 5) + 1.5
    stars.push({
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      size: Math.random() * 8 + 4,
      rotation: Math.random() * Math.PI,
      vRot: (Math.random() - 0.5) * 0.05,
      alpha: 1,
      color: starColors[Math.floor(Math.random() * starColors.length)],
      decay: Math.random() * 0.007 + 0.005,
    })
  }

  // 3. 生成战术六边形全息扩散
  for (let i = 0; i < 8; i++) {
    hexes.push({
      x: x + (Math.random() - 0.5) * 140,
      y: y + (Math.random() - 0.5) * 140,
      radius: Math.random() * 15 + 10,
      maxRadius: Math.random() * 200 + 150,
      rotation: Math.random() * Math.PI,
      alpha: 0.9,
      color: isWork ? '#ff3300' : '#5ebd82',
    })
  }

  // 4. 径向战术能量光晕 (从点击坐标优雅漫延全屏)
  const maxDim = Math.hypot(Math.max(x, w - x), Math.max(y, h - y)) * 1.2
  halo = {
    x,
    y,
    radius: 10,
    maxRadius: maxDim,
    alpha: 0.85,
    colorCore: isWork ? 'rgba(255, 200, 180, 0.9)' : 'rgba(230, 255, 240, 0.9)',
    colorGlow: isWork ? 'rgba(255, 50, 0, 0.65)' : 'rgba(94, 189, 130, 0.65)',
  }

  if (animId) cancelAnimationFrame(animId)
  loop(ctx, w, h)
}

/** 绘制四角星芒 */
function drawStar(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  size: number,
  rotation: number,
  color: string,
  alpha: number,
) {
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate(rotation)
  ctx.globalAlpha = alpha
  ctx.fillStyle = color
  ctx.shadowColor = color
  ctx.shadowBlur = 14

  ctx.beginPath()
  for (let i = 0; i < 4; i++) {
    ctx.rotate(Math.PI / 2)
    ctx.lineTo(0, -size)
    ctx.quadraticCurveTo(0, 0, size * 0.22, 0)
  }
  ctx.fill()
  ctx.restore()
}

/** 绘制六边形 */
function drawHex(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  radius: number,
  rotation: number,
  color: string,
  alpha: number,
) {
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate(rotation)
  ctx.globalAlpha = alpha
  ctx.strokeStyle = color
  ctx.lineWidth = 2.5
  ctx.shadowColor = color
  ctx.shadowBlur = 16

  ctx.beginPath()
  for (let i = 0; i < 6; i++) {
    const a = (i * Math.PI) / 3
    const rx = radius * Math.cos(a)
    const ry = radius * Math.sin(a)
    if (i === 0) ctx.moveTo(rx, ry)
    else ctx.lineTo(rx, ry)
  }
  ctx.closePath()
  ctx.stroke()
  ctx.restore()
}

function loop(ctx: CanvasRenderingContext2D, w: number, h: number) {
  ctx.clearRect(0, 0, w, h)
  ctx.globalCompositeOperation = 'lighter' // 高能发光混合

  let isAlive = false

  // 1. 渲染径向柔和雾化能量光晕 (Radial Energy Halo)
  if (halo && halo.radius < halo.maxRadius) {
    halo.radius += (halo.maxRadius - halo.radius) * 0.1
    halo.alpha *= 0.95
    if (halo.alpha > 0.01) {
      isAlive = true
      ctx.save()
      const grad = ctx.createRadialGradient(
        halo.x,
        halo.y,
        0,
        halo.x,
        halo.y,
        halo.radius,
      )
      grad.addColorStop(0, halo.colorCore)
      grad.addColorStop(0.3, halo.colorGlow)
      grad.addColorStop(0.8, halo.colorGlow.replace(/[\d\.]+\)$/, '0.15)'))
      grad.addColorStop(1, 'rgba(0,0,0,0)')

      ctx.globalAlpha = halo.alpha
      ctx.fillStyle = grad
      ctx.beginPath()
      ctx.arc(halo.x, halo.y, halo.radius, 0, Math.PI * 2)
      ctx.fill()

      // 外围脉冲发光环
      ctx.strokeStyle = halo.colorGlow
      ctx.lineWidth = 3
      ctx.shadowColor = halo.colorGlow
      ctx.shadowBlur = 20
      ctx.stroke()
      ctx.restore()
    }
  }

  // 2. 渲染全息六边形
  for (let i = hexes.length - 1; i >= 0; i--) {
    const hx = hexes[i]
    hx.radius += (hx.maxRadius - hx.radius) * 0.04
    hx.rotation += 0.01
    hx.alpha *= 0.97

    if (hx.alpha > 0.01) {
      isAlive = true
      drawHex(ctx, hx.x, hx.y, hx.radius, hx.rotation, hx.color, hx.alpha)
    }
  }

  // 3. 渲染四角星芒粒子
  for (let i = stars.length - 1; i >= 0; i--) {
    const p = stars[i]
    p.x += p.vx
    p.y += p.vy
    p.vx *= 0.97
    p.vy *= 0.97
    p.rotation += p.vRot
    p.alpha -= p.decay

    if (p.alpha > 0.01) {
      isAlive = true
      drawStar(ctx, p.x, p.y, p.size, p.rotation, p.color, p.alpha)
    }
  }

  if (isAlive) {
    animId = requestAnimationFrame(() => loop(ctx, w, h))
  } else {
    ctx.clearRect(0, 0, w, h)
    animId = null
  }
}

onUnmounted(() => {
  if (animId) cancelAnimationFrame(animId)
})

defineExpose({ trigger })
</script>

<template>
  <canvas ref="canvasRef" class="mode-transition-canvas" />
</template>

<style scoped>
.mode-transition-canvas {
  position: fixed;
  inset: 0;
  width: 100vw;
  height: 100vh;
  pointer-events: none;
  z-index: 99999;
}
</style>

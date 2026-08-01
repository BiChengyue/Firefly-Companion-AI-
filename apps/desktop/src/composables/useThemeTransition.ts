import { ref, type Ref } from 'vue'

export function useThemeTransition() {
  const isTransitioning = ref(false)

  function executeTransition(
    event: MouseEvent | undefined,
    targetMode: 'daily' | 'work',
    onSwitch: () => void,
    canvasRef?: Ref<{ trigger: (x: number, y: number, mode: 'daily' | 'work') => void } | null>,
  ) {
    if (isTransitioning.value) return

    // 1. 获取点击坐标
    const x = event ? event.clientX : window.innerWidth / 2
    const y = event ? event.clientY : window.innerHeight / 2

    isTransitioning.value = true

    // 1. 立即同步触发主题置换 (0ms 零延迟，保证界面切换与 Canvas 特效 100% 同步)
    onSwitch()

    // 2. 同时触发 Canvas 径向战术能量光晕与星芒粒子
    if (canvasRef?.value) {
      canvasRef.value.trigger(x, y, targetMode)
    }

    setTimeout(() => {
      isTransitioning.value = false
    }, 500)
  }

  return {
    isTransitioning,
    executeTransition,
  }
}

<script setup lang="ts">
import { useCompanionStore } from '@/stores/companion'
import type { AppMode } from '@shared/index'

const companion = useCompanionStore()

function switchMode(event: MouseEvent, mode: AppMode) {
  if (mode === companion.mode) return

  window.dispatchEvent(
    new CustomEvent('trigger-mode-switch', {
      detail: { event, mode },
    }),
  )
}
</script>

<template>
  <div class="mode-switch" :class="companion.mode">
    <button class="mode-btn" :class="{ active: companion.isDaily }" @click="switchMode($event, 'daily')">
      日常
    </button>
    <button class="mode-btn" :class="{ active: companion.isWork }" @click="switchMode($event, 'work')">
      工作
    </button>
    <div class="slider" :class="{ work: companion.isWork }" />
  </div>
</template>

<style scoped>
.mode-switch {
  position: relative;
  display: flex;
  background: rgba(0, 0, 0, 0.2);
  border-radius: var(--radius-sm);
  padding: 2px;
  overflow: hidden;
  box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.2);
}
.mode-btn {
  z-index: 1;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.6);
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: color 0.2s, transform 0.15s var(--ease-spring);
}
.mode-btn:active {
  transform: scale(0.94);
}
.mode-btn.active {
  color: #fff;
  text-shadow: 0 0 8px rgba(255, 255, 255, 0.4);
}
.slider {
  position: absolute;
  top: 2px;
  left: 2px;
  width: calc(50% - 2px);
  height: calc(100% - 4px);
  background: rgba(255, 255, 255, 0.18);
  border-radius: calc(var(--radius-sm) - 2px);
  transition: transform 0.35s var(--ease-spring), background 0.3s;
}
.slider.work {
  transform: translateX(100%);
  background: rgba(204, 51, 0, 0.4);
  box-shadow: 0 0 12px rgba(255, 68, 0, 0.5);
}
</style>

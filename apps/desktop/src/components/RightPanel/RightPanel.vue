<script setup lang="ts">
/** 右侧面板 — 角色卡片 + 精简小组件（T32：天气/服务器/健康 3 卡片；
 *  任务/提醒/记忆已挪左栏，见 LeftSidebar.vue）。 */
import { useCompanionStore } from '@/stores/companion'
import CharacterCard from './CharacterCard.vue'
import WeatherWidget from './WeatherWidget.vue'
import SystemStatusWidget from './SystemStatusWidget.vue'
import ServerStatusWidget from './ServerStatusWidget.vue'
import ComputerStatusWidget from './ComputerStatusWidget.vue'
import PhoneStatusWidget from './PhoneStatusWidget.vue'
import HealthWidget from './HealthWidget.vue'

const companion = useCompanionStore()
</script>

<template>
  <aside class="right-panel">
    <CharacterCard />
    <WeatherWidget v-if="companion.isDaily" />
    <SystemStatusWidget v-else />
    <ServerStatusWidget />
    <ComputerStatusWidget />
    <PhoneStatusWidget />
    <HealthWidget />
  </aside>
</template>

<style scoped>
.right-panel {
  display: block;         /* 2026-08-08：block 布局滚动最可靠（flex 下滚动高度计算偶发异常 → 划不到底） */
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  background: var(--bg-surface);
  border-left: 1px solid var(--border-main);
  padding: 16px 14px;
  overflow-y: auto;
}
/* 2026-08-08：block 下恢复卡片间距（flex gap 丢失） */
.right-panel > * + * {
  margin-top: 14px;
}
</style>

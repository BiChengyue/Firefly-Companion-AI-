<script setup lang="ts">
/** 右侧面板 — 角色卡片 + 小组件聚合。 */
import { useCompanionStore } from '@/stores/companion'
import CharacterCard from './CharacterCard.vue'
import TaskWidget from './TaskWidget.vue'
import ReminderWidget from './ReminderWidget.vue'
import WeatherWidget from './WeatherWidget.vue'
import SystemStatusWidget from './SystemStatusWidget.vue'
import ServerStatusWidget from './ServerStatusWidget.vue'
import MemoryWidget from './MemoryWidget.vue'

const companion = useCompanionStore()
</script>

<template>
  <aside class="right-panel">
    <CharacterCard />
    <TaskWidget />
    <ReminderWidget />
    <WeatherWidget v-if="companion.isDaily" />
    <SystemStatusWidget v-else />
    <ServerStatusWidget />
    <MemoryWidget />
  </aside>
</template>

<style scoped>
.right-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-surface);
  border-left: 1px solid var(--border-main);
  padding: 16px 14px;
  gap: 14px;
  overflow-y: auto;
}
</style>

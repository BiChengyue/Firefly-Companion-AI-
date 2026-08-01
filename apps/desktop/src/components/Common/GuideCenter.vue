<script setup lang="ts">
import { ref } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { useCompanionStore } from '@/stores/companion'

const settings = useSettingsStore()
const companion = useCompanionStore()
const visible = ref(false)

function show() {
  visible.value = true
  companion.setInteractionLocked(true) // 显示引导面板，锁定窗口为可交互
}

function close() {
  visible.value = false
  companion.setInteractionLocked(false) // 关闭引导面板，解除锁定恢复鼠标穿透
  settings.markGuideShown()
}

defineExpose({ show })
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="guide-overlay" :class="companion.mode" @click="close">
      <div class="guide-panel" @click.stop>
        <h2>流萤引导中心</h2>
        <section>
          <h3>交互教学</h3>
          <ul>
            <li>按住 <kbd>Ctrl</kbd> 键并用左键拖动，可以帮流萤搬家哦</li>
            <li>按住 <kbd>Ctrl</kbd> 单击流萤，可以触发日常互动</li>
            <li>松开 <kbd>Ctrl</kbd>，流萤会恢复鼠标穿透，不挡后面的窗口</li>
            <li>点击上方「日常/工作」滑块切换流萤与萨姆模式</li>
          </ul>
        </section>
        <section>
          <h3>口头指令库</h3>
          <ul>
            <li>"流萤，记住我的生日是10月15日" — 让流萤记住信息</li>
            <li>"流萤，我最近胃不太好" — 触发主动关怀</li>
            <li>"帮我整理桌面截图" — 进入萨姆工作模式执行任务</li>
            <li>输入 <code>/help</code> 随时召唤此面板</li>
          </ul>
        </section>
        <button class="close-btn" @click="close">明白了</button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.guide-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.guide-panel {
  background: var(--bg-elevated, rgba(30, 30, 40, 0.95));
  backdrop-filter: blur(20px);
  border-radius: var(--radius-md, 16px);
  padding: 24px 32px;
  max-width: 480px;
  color: var(--text-primary, #e0e0e0);
  border: 1px solid var(--border-subtle, transparent);
}

.guide-panel h2 {
  margin: 0 0 16px;
  color: var(--accent-strong, #e6f4ea);
}

.guide-panel h3 {
  margin: 16px 0 8px;
  color: var(--text-secondary, #fff7e6);
  font-size: 15px;
}

.guide-panel ul {
  padding-left: 20px;
  margin: 0;
}

.guide-panel li {
  margin: 6px 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary, #e0e0e0);
}

kbd {
  background: var(--bg-input, rgba(255, 255, 255, 0.1));
  border: 1px solid var(--border-subtle, rgba(255, 255, 255, 0.08));
  border-radius: var(--radius-sm, 4px);
  padding: 1px 6px;
  font-family: monospace;
  color: var(--text-primary, #e0e0e0);
}

code {
  background: var(--accent-light, rgba(0, 255, 136, 0.15));
  border-radius: var(--radius-sm, 4px);
  padding: 1px 6px;
  font-family: monospace;
  color: var(--accent-strong, #00ff88);
}

.close-btn {
  margin-top: 20px;
  width: 100%;
  padding: 10px;
  border: none;
  border-radius: var(--radius-sm, 10px);
  background: var(--accent-light, #e6f4ea);
  color: var(--accent-strong, #1a3a2a);
  font-size: 15px;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.close-btn:hover {
  background: var(--accent, #5ebd82);
  color: var(--text-on-accent, #fff);
}

/* ── 工作模式覆写（因 Teleport 到 body，不继承 .chat-client-root.work）── */
.guide-overlay.work .guide-panel {
  background: rgba(14, 4, 1, 0.96);
  border-color: rgba(204, 51, 0, 0.3);
  color: #ffaa88;
}

.guide-overlay.work .guide-panel h2 {
  color: #ff4400;
}

.guide-overlay.work .guide-panel h3 {
  color: #cc6644;
}

.guide-overlay.work .guide-panel li {
  color: #ffaa88;
}

.guide-overlay.work kbd {
  background: rgba(204, 51, 0, 0.2);
  border-color: rgba(204, 51, 0, 0.3);
  color: #ffaa88;
}

.guide-overlay.work code {
  background: rgba(204, 51, 0, 0.2);
  color: #ff4400;
}

.guide-overlay.work .close-btn {
  background: rgba(204, 51, 0, 0.2);
  color: #ffaa88;
}

.guide-overlay.work .close-btn:hover {
  background: #cc3300;
  color: #fff;
}
</style>

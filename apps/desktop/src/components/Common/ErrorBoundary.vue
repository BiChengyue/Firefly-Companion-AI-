<script setup lang="ts">
import { ref, onErrorCaptured } from 'vue'

const error = ref<string | null>(null)
const errorStack = ref<string | null>(null)

onErrorCaptured((err: unknown) => {
  error.value = err instanceof Error ? err.message : String(err)
  errorStack.value = err instanceof Error ? err.stack || null : null
  console.error('[ErrorBoundary] 捕获组件错误:', err)
  return false // 阻止向上冒泡，避免白屏
})

function retry() {
  error.value = null
  errorStack.value = null
  // 强制重新渲染：Vue 会在下次 tick 重新渲染 slot 内容
}
</script>

<template>
  <div v-if="error" class="error-boundary">
    <div class="error-card">
      <div class="error-icon">⚠</div>
      <h3>出错了</h3>
      <p class="error-msg">{{ error }}</p>
      <details v-if="errorStack" class="error-stack">
        <summary>技术细节</summary>
        <pre>{{ errorStack }}</pre>
      </details>
      <button class="retry-btn" @click="retry">🔄 重新加载</button>
    </div>
  </div>
  <slot v-else />
</template>

<style scoped>
.error-boundary {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 24px;
  background: var(--bg-root, #0a0a0a);
}

.error-card {
  text-align: center;
  max-width: 420px;
  padding: 32px 28px;
  background: var(--bg-surface, rgba(20, 20, 30, 0.95));
  border: 1px solid var(--border-main, #cc330033);
  border-radius: var(--radius-md, 14px);
  backdrop-filter: blur(12px);
}

.error-icon {
  font-size: 42px;
  margin-bottom: 12px;
  filter: drop-shadow(0 0 8px rgba(204, 51, 0, 0.4));
}

.error-card h3 {
  margin: 0 0 8px;
  font-size: 18px;
  color: var(--accent-strong, #ff4400);
}

.error-msg {
  margin: 0 0 16px;
  font-size: 14px;
  color: var(--text-secondary, #cc6644);
  line-height: 1.5;
}

.error-stack {
  margin-bottom: 16px;
  text-align: left;
}

.error-stack summary {
  font-size: 12px;
  color: var(--text-muted, #888);
  cursor: pointer;
  margin-bottom: 6px;
}

.error-stack pre {
  max-height: 120px;
  overflow-y: auto;
  font-size: 11px;
  color: var(--text-muted, #888);
  background: var(--bg-input, rgba(0, 0, 0, 0.2));
  padding: 10px;
  border-radius: var(--radius-sm, 6px);
  white-space: pre-wrap;
  word-break: break-all;
}

.retry-btn {
  padding: 10px 28px;
  border: 1px solid var(--accent, #cc3300);
  border-radius: var(--radius-sm, 8px);
  background: var(--accent-light, rgba(204, 51, 0, 0.2));
  color: var(--accent-strong, #ff4400);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.retry-btn:hover {
  background: var(--accent, #cc3300);
  color: var(--text-on-accent, #fff);
}
</style>

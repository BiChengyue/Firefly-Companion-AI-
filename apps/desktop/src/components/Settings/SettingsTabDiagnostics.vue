<script setup lang="ts">
import { inject } from 'vue'
import { useSettingsForm } from '@/composables/useSettingsForm'

const form = inject<ReturnType<typeof useSettingsForm>>('settingsForm')!
</script>

<template>
  <div class="form-section">
    <p class="diag-desc">测试服务端与 LLM 的连通性，帮助快速定位网络或密钥问题。</p>
    <div class="diag-actions">
      <button class="diag-btn" :disabled="form.diagnosing.value" @click="form.runPingTest()">{{ form.diagnosing.value ? '检测中…' : '🔗 Ping 服务端' }}</button>
      <button class="diag-btn" :disabled="form.diagnosing.value" @click="form.runLLMTest()">{{ form.diagnosing.value ? '检测中…' : '🧠 LLM 连通性测试' }}</button>
    </div>
    <div v-if="form.diagResult.value" class="diag-result" :class="form.diagResult.value.success ? 'ok' : 'fail'">
      <span class="diag-icon">{{ form.diagResult.value.success ? '✅' : '❌' }}</span>
      {{ form.diagResult.value.message }}
    </div>
  </div>
</template>

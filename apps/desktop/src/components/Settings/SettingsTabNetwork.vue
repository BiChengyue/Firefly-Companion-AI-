<script setup lang="ts">
import { inject } from 'vue'
import { useSettingsForm } from '@/composables/useSettingsForm'

const form = inject<ReturnType<typeof useSettingsForm>>('settingsForm')!
</script>

<template>
  <div class="form-section">
    <label class="form-label">WebSocket 地址</label>
    <input v-model="form.formServerUrl.value" type="text" class="form-input" placeholder="ws://127.0.0.1:8765" />

    <label class="form-label">HTTP 后端地址</label>
    <input v-model="form.formHttpBaseUrl.value" type="text" class="form-input" placeholder="http://127.0.0.1:8765" />
  </div>
</template>

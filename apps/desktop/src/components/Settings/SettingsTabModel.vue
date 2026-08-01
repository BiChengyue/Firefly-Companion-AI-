<script setup lang="ts">
import { inject } from 'vue'
import { useSettingsForm } from '@/composables/useSettingsForm'

const form = inject<ReturnType<typeof useSettingsForm>>('settingsForm')!
</script>

<template>
  <div class="form-section">
    <label class="form-label">LLM 供应商</label>
    <select v-model="form.formLlmProvider.value" class="form-input" @change="form.onProviderChange()">
      <option v-if="!form.providersLoaded.value" value="">加载中…</option>
      <option v-for="p in form.providerList.value" :key="p.id" :value="p.id">{{ p.name }}</option>
      <option value="__custom__">🔧 自定义 (Custom)</option>
    </select>
    <p v-if="form.providersError.value" class="diag-desc err">
      ⚠️ 无法加载预设供应商 — 请确认 Python 后端已启动
      （<code>cd apps/server && uvicorn main:app --port 8765</code>）
      后重新打开此标签页。
    </p>

    <template v-if="form.isCustomProvider.value">
      <div class="custom-banner">
        ⚠ 自定义模型需兼容 <strong>OpenAI Chat Completions API</strong> 接口格式<br />
        即支持 <code>POST /v1/chat/completions</code> 端点，与 DeepSeek / 智谱 / Ollama 等一致。
      </div>
      <label class="form-label">Base URL</label>
      <input v-model="form.formLlmBaseUrl.value" type="text" class="form-input" placeholder="https://api.example.com/v1" />
      <p class="diag-desc">填入兼容 OpenAI 接口的 API 地址。各厂商、代理或本地服务均可。</p>
      <label class="form-label">模型名称</label>
      <input v-model="form.formLlmModel.value" type="text" class="form-input" placeholder="输入模型 ID，如 deepseek-v4-pro" />
      <p class="diag-desc">模型名由供应商定义，请填写 API 文档中的 <code>model</code> 参数值。</p>
    </template>

    <template v-else>
      <label class="form-label">Base URL</label>
      <input v-model="form.formLlmBaseUrl.value" type="text" class="form-input" :placeholder="form.providerList.value.find(p => p.id === form.formLlmProvider.value)?.baseUrl || ''" />
      <label class="form-label">模型</label>
      <input v-model="form.formLlmModel.value" type="text" :list="'modelDatalist_' + form.formLlmProvider.value" class="form-input" placeholder="输入或选择模型 ID" @change="form.onModelChange()" autocomplete="off" />
      <datalist :id="'modelDatalist_' + form.formLlmProvider.value">
        <option v-for="m in form.currentModels.value" :key="m.id" :value="m.id">{{ m.name }} — {{ m.id }}</option>
      </datalist>
      <p class="diag-desc">下拉可选预设模型，也可手动输入任意兼容 OpenAI API 的模型 ID。（如供应商新增了模型，或使用代理/中转服务）</p>
    </template>

    <label class="form-label">API Key</label>
    <input v-model="form.formApiKey.value" type="password" class="form-input" placeholder="输入你的 API Key" autocomplete="off" />

    <label class="form-label">Max Tokens（输出上限）</label>
    <input v-model.number="form.formLlmMaxTokens.value" type="number" class="form-input" min="256" step="256" />
    <p class="diag-desc">单次生成的最多 token 数。选模型时自动填入推荐值，可手动覆盖。</p>

    <label class="form-label">Temperature（温度）</label>
    <input v-model.number="form.formLlmTemperature.value" type="number" class="form-input" min="0" max="2" step="0.1" />
    <p class="diag-desc">控制输出随机性：0=保守精确，2=创意发散。常用范围 0.6–1.0。</p>

    <div class="toggle-row">
      <div class="toggle-info">
        <span class="toggle-label">🧠 启用思考模式 (Reasoning)</span>
        <span class="toggle-desc">开启后向支持推理的模型发送 thinking 参数；若模型不支持请关闭，否则可能报错。</span>
      </div>
      <button class="toggle-switch" :class="{ active: form.formEnableThinking.value }" @click="form.formEnableThinking.value = !form.formEnableThinking.value">
        <span class="toggle-knob" />
      </button>
    </div>

    <div class="divider" />

    <!-- 诊断（原独立 Tab，现合并到模型设置底部） -->
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

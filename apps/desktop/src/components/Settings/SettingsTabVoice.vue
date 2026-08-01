<script setup lang="ts">
import { inject } from 'vue'
import { useSettingsForm } from '@/composables/useSettingsForm'

const form = inject<ReturnType<typeof useSettingsForm>>('settingsForm')!
</script>

<template>
  <div class="form-section">
    <label class="form-label">TTS 语音合成驱动</label>
    <select v-model="form.formVoiceProvider.value" class="form-input" @change="form.loadVoices()">
      <option value="edge-tts">Edge-TTS (默认轻量，微软神经网络大模型)</option>
      <option value="gpt-sovits">流萤声音模型GPT-SoVITS (专属驱动)</option>
      <option value="minimax">MiniMax (云端大模型，需 API Key)</option>
    </select>

    <template v-if="form.formVoiceProvider.value === 'edge-tts'">
      <label class="form-label">选择音色</label>
      <select v-model="form.formVoiceId.value" class="form-input">
        <option v-for="v in form.voiceList.value" :key="v.id" :value="v.id">{{ v.name }} - {{ v.description }} {{ v.recommended ? '⭐ [推荐]' : '' }}</option>
      </select>
    </template>

    <template v-else-if="form.formVoiceProvider.value === 'minimax'">
      <label class="form-label">MiniMax API Key</label>
      <input v-model="form.formMinimaxApiKey.value" type="password" class="form-input" placeholder="输入 MiniMax API Key (platform.minimax.io 获取)" autocomplete="off" />
      <p class="diag-desc">前往 <a href="https://platform.minimax.io" target="_blank">platform.minimax.io</a> 注册并获取接口密钥。密钥保存在本地浏览器。</p>
      <label class="form-label">音色选择（含声音克隆）</label>
      <select v-model="form.formVoiceId.value" class="form-input">
        <option v-for="v in form.voiceList.value" :key="v.id" :value="v.id">{{ v.name }} {{ v.recommended ? '⭐ [推荐]' : '' }}</option>
      </select>
      <template v-if="form.formVoiceId.value === 'custom'">
        <label class="form-label">自定义声音克隆 Voice ID</label>
        <input v-model="form.formMinimaxVoiceId.value" type="text" class="form-input" placeholder="输入克隆后获得的 voice_id" />
        <p class="diag-desc">通过 MiniMax 声音克隆 API 上传训练音频后获得。填入后会覆盖上方预设音色。</p>
      </template>
      <p class="diag-desc" style="margin-top:8px; color: var(--text-subtle)">MiniMax 按字符数计费，音质较好。若 API 故障将自动降级到 Edge-TTS。</p>
    </template>

    <template v-else>
      <label class="form-label">GPT-SoVITS API 端口地址</label>
      <input v-model="form.formGptSovitsUrl.value" type="text" class="form-input" placeholder="http://127.0.0.1:9880" />
      <p class="diag-desc">流萤 GPT-SoVITS 引擎运行后，填入其监听地址（默认 <code>9880</code> 端口）。</p>

      <label class="form-label">Python 解释器路径（整合包 env/python.exe）</label>
      <input v-model="form.formGptSovitsPythonPath.value" type="text" class="form-input" placeholder="例如: D:\项目\GPT-SOVITS-V4(整合包)\1\env\python.exe" />
      <p class="diag-desc">GPT-SoVITS 引擎需专用的 Python 环境（含 PyTorch + CUDA）。请指向整合包内的 <code>env/python.exe</code>。留空则自动尝试 <code>engine/env/python.exe</code>。</p>

      <div class="model-card" :class="{ 'model-card-good': form.envReady.value }">
        <div class="model-card-header">
          <span class="model-card-title">{{ form.envReady.value ? '✅' : '📦' }} Python 推理环境</span>
          <span class="model-card-badge" :class="form.envReady.value ? 'badge-ready' : 'badge-warn'">{{ form.envReady.value ? '已就绪' : '未安装' }}</span>
        </div>
        <p class="diag-desc" style="margin-bottom: 8px">
          <template v-if="form.envReady.value">
            <span v-if="form.engineEnvReady.value">✅ 一键安装环境就绪：<code>engine/env/Scripts/python.exe</code></span>
            <span v-else-if="form.configuredPathExists.value">✅ 使用自定义路径：<code>{{ form.configuredPath.value }}</code></span>
          </template>
          <template v-else>
            ⚠️ 未检测到推理环境。可通过以下任一方式配置：
            <br>① 点击下方按钮打开目录，双击 <code>install_env.bat</code> 一键安装
            <br>② 在上方填写整合包的 <code>env/python.exe</code> 路径
          </template>
        </p>
        <div class="model-card-actions">
          <button class="btn" style="font-size:13px; padding:6px 12px; border:1px solid var(--border-subtle); border-radius:5px; background:var(--bg-btn,#f8f8f8); cursor:pointer" @click="form.openEngineDir()">📂 打开引擎目录</button>
          <button v-if="!form.envReady.value" class="btn" style="font-size:13px; padding:6px 12px; border:1px solid var(--border-subtle); border-radius:5px; background:var(--bg-btn,#f8f8f8); cursor:pointer" @click="form.checkEnvStatus()">🔄 刷新检测</button>
        </div>
      </div>

      <div class="model-card">
        <div class="model-card-header">
          <span class="model-card-title">🎙️ 流萤语音模型文件</span>
          <button class="model-refresh-btn" :disabled="form.modelStatusLoading.value" @click="form.checkModelStatus()">{{ form.modelStatusLoading.value ? '检测中…' : '🔄 重新检测' }}</button>
        </div>
        <div v-if="form.modelStatus.value" class="model-status-overview">
          <div :class="['model-status-badge', form.modelStatus.value.engine_ready ? 'ready' : 'missing']">{{ form.modelStatus.value.engine_ready ? '✅ 引擎就绪' : `⚠️ 缺少 ${form.modelStatus.value.missing_files} 个文件` }}</div>
          <span class="model-status-detail">{{ form.modelStatus.value.present_files }} / {{ form.modelStatus.value.total_files }} 个文件已就绪<template v-if="!form.modelStatus.value.engine_ready && form.modelStatus.value.download_size_mb > 0"> · 还需下载约 {{ Math.round(form.modelStatus.value.download_size_mb / 1024 * 10) / 10 }} GB</template></span>
        </div>
        <div v-else-if="form.modelStatusLoading.value" class="model-status-loading">正在检测文件状态…</div>
        <div v-if="form.modelStatus.value" class="model-file-list">
          <div v-for="f in form.modelStatus.value.files" :key="f.local_path" class="model-file-row">
            <span :class="['model-file-dot', f.exists ? 'exist' : 'miss']"></span>
            <span class="model-file-name">{{ f.name }}</span>
            <span v-if="f.bundled" class="model-file-tag bundled">仓库内置</span>
            <span class="model-file-size">{{ f.exists ? `${Math.round((f.file_size_mb ?? f.size_mb) * 10) / 10} MB` : `~${f.size_mb} MB` }}</span>
          </div>
        </div>
        <div v-if="form.modelDownloading.value" class="model-download-progress">
          <div class="progress-label"><span>{{ form.downloadProgress.value.current_file }}</span><span>{{ form.downloadProgress.value.overall_percent }}%</span></div>
          <div class="progress-bar-wrap"><div class="progress-bar-fill" :style="{ width: form.downloadProgress.value.overall_percent + '%' }"></div></div>
          <div class="progress-sub">{{ form.downloadProgress.value.overall_downloaded_mb }} / {{ form.downloadProgress.value.overall_total_mb }} MB</div>
        </div>
        <div v-if="form.downloadLog.value.length" class="model-download-log">
          <p v-for="(line, i) in form.downloadLog.value" :key="i" class="log-line">{{ line }}</p>
        </div>
        <div class="model-card-actions">
          <button class="model-download-btn" :disabled="form.modelDownloading.value || (form.modelStatus.value?.engine_ready ?? false)" @click="form.startModelDownload()">
            <template v-if="form.modelDownloading.value">⏳ 下载中，请勿关闭…</template>
            <template v-else-if="form.modelStatus.value?.engine_ready">✅ 模型已完整，无需下载</template>
            <template v-else>⬇️ 下载缺失的基础模型（约 {{ Math.round((form.modelStatus.value?.download_size_mb ?? 1821) / 1024 * 10) / 10 }} GB）</template>
          </button>
        </div>
        <p class="diag-desc" style="margin-top: 4px;">国内用户推荐使用 HF-Mirror 加速源，下载时请保持应用运行。已下载文件支持断点续传。</p>
      </div>
    </template>

    <label class="form-label">自动播放对话语音</label>
    <div style="display: flex; align-items: center; gap: 8px;">
      <input id="autoVoice" v-model="form.formAutoPlayVoice.value" type="checkbox" style="cursor: pointer;" />
      <label for="autoVoice" style="font-size: 13px; cursor: pointer; color: var(--text-primary);">收到流萤回复后自动朗读发声</label>
    </div>

    <div style="margin-top: 10px;">
      <button class="diag-btn" :disabled="form.testingVoice.value" @click="form.playVoiceSample()">{{ form.testingVoice.value ? '🔊 正在试听播放…' : '🔊 试听选定音色 (测试声线)' }}</button>
    </div>

    <div class="model-card" style="margin-top: 6px;">
      <div class="model-card-header">
        <span class="model-card-title">⚡ 流萤原声引擎</span>
        <button class="model-refresh-btn" :disabled="form.gsStatusLoading.value" @click="form.checkGsStatus()">{{ form.gsStatusLoading.value ? '检测中…' : '🔄 刷新状态' }}</button>
      </div>
      <p class="diag-desc">GPT-SoVITS 加载约占用 <strong>2-4 GB</strong> 内存/显存。日常对话使用 Edge-TTS 即可，需要流萤原声时按需拉起。</p>
      <div class="model-status-overview">
        <div :class="['model-status-badge', form.gsStatus.value?.running ? 'ready' : 'missing']">{{ form.gsStatus.value?.running ? '🟢 运行中 (端口 9880)' : form.gsStatus.value === null ? '⏳ 未知' : '⚫ 已停止' }}</div>
      </div>
      <div class="model-card-actions" style="gap: 8px;">
        <button class="model-download-btn" :disabled="form.gsStarting.value || form.gsStatus.value?.running" @click="form.handleStartGs()" style="flex: 1.2">{{ form.gsStarting.value ? '⏳ 启动中 (加载模型约 22-80s)…' : '🚀 拉起原声引擎' }}</button>
        <button class="diag-btn" :disabled="form.gsStopping.value || !form.gsStatus.value?.running" @click="form.handleStopGs()" style="flex: 0.8; background: rgba(204,51,0,0.12); border-color: rgba(204,51,0,0.3); color: #cc3300;">{{ form.gsStopping.value ? '关闭中…' : '⬇️ 释放内存' }}</button>
      </div>
      <p v-if="form.gsActionMsg.value" class="diag-desc" style="margin-top: 4px; font-weight: 600;">{{ form.gsActionMsg.value }}</p>
    </div>

    <div class="model-card" style="margin-top: 6px;">
      <div class="model-card-header">
        <span class="model-card-title">🗄️ 语音缓存</span>
        <button class="model-refresh-btn" @click="form.loadCacheStats()">🔄 刷新</button>
      </div>
      <p class="diag-desc">对话中生成的音频文件统一缓存在 <code>data/audio_cache</code>，超过 7 天或超过 200MB 时自动清理。</p>
      <div class="model-status-overview">
        <div :class="['model-status-badge', form.audioCacheStats.value === null ? 'missing' : 'ready']">{{ form.audioCacheStats.value === null ? '⏳ 加载中…' : `${form.audioCacheStats.value.file_count} 个文件 · ${form.audioCacheStats.value.total_size_mb} MB` }}</div>
      </div>
      <div class="model-card-actions">
        <button class="diag-btn" :disabled="form.cacheCleaning.value || !form.audioCacheStats.value || form.audioCacheStats.value.file_count === 0" @click="form.handleCleanupCache()" style="background: rgba(204,153,0,0.12); border-color: rgba(204,153,0,0.3); color: #cc9900;">{{ form.cacheCleaning.value ? '🧹 清理中…' : '🧹 立即清理缓存' }}</button>
      </div>
      <p v-if="form.cacheMsg.value" class="diag-desc" style="margin-top: 4px; font-weight: 600;" :style="{ color: form.cacheMsg.value.startsWith('❌') ? '#cc3300' : '#339933' }">{{ form.cacheMsg.value }}</p>
    </div>
  </div>
</template>

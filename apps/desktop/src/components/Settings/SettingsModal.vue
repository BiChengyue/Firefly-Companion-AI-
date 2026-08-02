<script setup lang="ts">
/** SettingsModal.vue — 设置弹窗壳，只负责：标签切换 + 生命周期调度 + 底部按钮。 */
import { ref, watch, onMounted, provide } from 'vue'
import { useCompanionStore } from '@/stores/companion'
import { useSettingsForm } from '@/composables/useSettingsForm'

import SettingsTabGeneral from './SettingsTabGeneral.vue'
import SettingsTabModel from './SettingsTabModel.vue'
import SettingsTabVoice from './SettingsTabVoice.vue'
import SettingsTabNetwork from './SettingsTabNetwork.vue'
import SettingsTabExtensions from './SettingsTabExtensions.vue'

const companion = useCompanionStore()
const emit = defineEmits<{ close: [] }>()

// ── 标签页 ──
const tabs = ['通用', '模型', '语音', '网络', '扩展'] as const
const activeTab = ref<(typeof tabs)[number]>('通用')

const modalClass = companion.mode

// ── 集中表单状态 ──
const form = useSettingsForm()
provide('settingsForm', form)

// ── 标签切换时按需加载各 tab 数据 ──
watch(activeTab, (tab) => {
  if (tab === '扩展') {
    form.loadTools()
    form.loadMcpServers()
    form.loadSkillList()
  }
  if (tab === '语音') {
    form.loadVoices()
    form.checkModelStatus()
    form.checkEnvStatus()
    form.checkGsStatus()
    form.loadCacheStats()
  }
  if (tab === '通用') {
    form.loadAvatarLists()
  }
  if (tab === '模型') {
    if (!form.providersLoaded.value) form.loadProviders()
  }
})

onMounted(() => {
  if (activeTab.value === '扩展') form.loadTools()
  if (activeTab.value === '语音') { form.loadVoices(); form.checkModelStatus(); form.checkEnvStatus() }
  if (activeTab.value === '通用') form.loadAvatarLists()
  if (activeTab.value === '模型') form.loadProviders()
})

// ── 保存 / 取消 ──
async function handleSave() {
  await form.handleSave()
  emit('close')
}
function handleCancel() {
  emit('close')
}
</script>

<template>
  <div class="settings-overlay" :class="modalClass" @click.self="handleCancel">
    <div class="settings-modal" @click.stop>
      <div class="modal-header">
        <span class="modal-title">⚙ 系统设置</span>
        <button class="modal-close" @click="handleCancel">×</button>
      </div>

      <nav class="tabs-bar">
        <button
          v-for="tab in tabs"
          :key="tab"
          class="tab-btn"
          :class="{ active: activeTab === tab }"
          @click="activeTab = tab; form.diagResult.value = null"
        >
          {{ tab }}
        </button>
      </nav>

      <div class="tab-content">
        <SettingsTabGeneral v-show="activeTab === '通用'" />
        <SettingsTabModel v-show="activeTab === '模型'" />
        <SettingsTabVoice v-show="activeTab === '语音'" />
        <SettingsTabNetwork v-show="activeTab === '网络'" />
        <SettingsTabExtensions v-show="activeTab === '扩展'" />
      </div>

      <div class="modal-footer">
        <button class="btn-secondary" @click="handleCancel">取消</button>
        <button class="btn-primary" @click="handleSave">保存</button>
      </div>
    </div>
  </div>
</template>

<!-- 全局样式（非 scoped，子组件复用同一套 class 体系） -->
<style>
/* 主体 / 遮罩 */
.settings-overlay {
  position: fixed; inset: 0; z-index: 9000;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}
.settings-modal {
  width: 720px; max-width: 94vw; max-height: 88vh;
  background: var(--bg-panel, #fefefe);
  border-radius: 10px; display: flex; flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  overflow: hidden;
}

/* 标题栏 */
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 18px; border-bottom: 1px solid var(--border-subtle, #e8e8e8);
}
.modal-title { font-size: 17px; font-weight: 600; }
.modal-close {
  background: none; border: none; font-size: 22px; cursor: pointer;
  color: var(--text-subtle, #888);
}
.modal-close:hover { color: var(--text-primary, #222); }

/* 标签栏 */
.tabs-bar { display: flex; gap: 0; border-bottom: 1px solid var(--border-subtle, #e8e8e8); padding: 0 12px; }
.tab-btn {
  padding: 8px 16px; font-size: 13px; font-weight: 500;
  background: none; border: none; border-bottom: 2px solid transparent;
  cursor: pointer; color: var(--text-subtle, #777);
  transition: all 0.15s;
}
.tab-btn:hover { color: var(--accent, #2ecc71); }
.tab-btn.active { color: var(--accent, #2ecc71); border-bottom-color: var(--accent, #2ecc71); }

/* 内容区 */
.tab-content { flex: 1; overflow-y: auto; padding: 12px 18px; scrollbar-gutter: stable; }

/* 底部 */
.modal-footer {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 18px; border-top: 1px solid var(--border-subtle, #e8e8e8);
}
.btn-secondary, .btn-primary {
  padding: 7px 22px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid;
}
.btn-secondary {
  background: transparent; color: var(--text-subtle, #777);
  border-color: var(--border-subtle, #ccc);
}
.btn-primary {
  background: var(--accent, #2ecc71); color: #fff;
  border-color: var(--accent, #2ecc71);
}

/* ── 通用复用的子组件样式（子组件直接使用） ── */
.form-section { display: flex; flex-direction: column; gap: 6px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text-primary, #222); margin-top: 8px; }
.form-input {
  width: 100%; padding: 7px 10px; font-size: 13px;
  border: 1px solid var(--border-subtle, #ddd);
  border-radius: 5px; background: var(--bg-input, #fff);
  color: var(--text-primary, #222);
}
.form-input:focus { outline: none; border-color: var(--accent, #2ecc71); }

.diag-desc { font-size: 12px; color: var(--text-subtle, #999); margin: 0; }
.diag-desc.err { color: #e06055; }
.diag-desc code { font-size: 12px; background: var(--bg-code, #f0f0f0); padding: 1px 4px; border-radius: 3px; }

/* toggle */
.toggle-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 10px; }
.toggle-info { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.toggle-label { font-size: 13px; font-weight: 600; }
.toggle-desc { font-size: 11px; color: var(--text-subtle, #999); }
.toggle-switch {
  width: 44px; height: 24px; border-radius: 12px; border: none;
  background: var(--border-subtle, #ccc); cursor: pointer;
  position: relative; transition: background 0.2s;
}
.toggle-switch.active { background: var(--accent, #2ecc71); }
.toggle-knob {
  position: absolute; top: 2px; left: 2px;
  width: 20px; height: 20px; border-radius: 50%;
  background: #fff; transition: left 0.2s;
}
.toggle-switch.active .toggle-knob { left: 22px; }

.divider { height: 1px; background: var(--border-subtle, #e8e8e8); margin: 8px 0; }

/* 诊断 */
.diag-actions { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.diag-btn {
  padding: 7px 16px; font-size: 13px; border-radius: 5px;
  border: 1px solid var(--border-subtle, #ccc); background: var(--bg-btn, #f8f8f8);
  color: var(--text-primary, #222); cursor: pointer;
}
.diag-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.diag-result {
  margin-top: 10px; padding: 8px 12px; border-radius: 6px; font-size: 13px;
  display: flex; align-items: center; gap: 8px;
}
.diag-result.ok { background: rgba(46, 204, 113, 0.1); border: 1px solid rgba(46, 204, 113, 0.3); }
.diag-result.fail { background: rgba(224, 96, 85, 0.1); border: 1px solid rgba(224, 96, 85, 0.3); }
.diag-icon { font-size: 16px; }

/* 内置工具标签 */
.custom-banner {
  padding: 8px 12px; background: rgba(255, 200, 0, 0.12);
  border: 1px solid rgba(255, 200, 0, 0.4); border-radius: 5px;
  font-size: 12px; color: #b08000; margin-top: 6px; margin-bottom: 4px;
}
.custom-banner code { background: rgba(255,200,0,0.15); padding: 1px 4px; border-radius: 3px; }

/* 模型卡片 */
.model-card {
  border: 1px solid var(--border-subtle, #e0e0e0); border-radius: 6px;
  padding: 10px 12px; margin-top: 8px; background: var(--bg-card, #fafafa);
}
.model-card-good { border-color: rgba(46, 204, 113, 0.4); background: rgba(46, 204, 113, 0.04); }
.model-card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.model-card-title { font-size: 13px; font-weight: 600; }
.model-card-badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
.badge-ready { background: rgba(46,204,113,0.15); color: #2e8b57; }
.badge-warn { background: rgba(255,200,0,0.15); color: #b08000; }
.model-card-actions { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.model-refresh-btn {
  font-size: 12px; padding: 3px 10px; border: 1px solid var(--border-subtle, #ccc);
  border-radius: 4px; background: var(--bg-btn, #f8f8f8); cursor: pointer;
  color: var(--text-subtle, #666);
}
.model-refresh-btn:disabled { opacity: 0.5; }
.model-status-overview { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.model-status-badge { font-size: 12px; padding: 3px 10px; border-radius: 4px; }
.model-status-badge.ready { background: rgba(46,204,113,0.12); color: #2e8b57; }
.model-status-badge.missing { background: rgba(255,200,0,0.12); color: #b08000; }
.model-status-detail { font-size: 12px; color: var(--text-subtle, #888); }
.model-status-loading { font-size: 12px; color: var(--text-subtle, #999); }
.model-file-list { margin-bottom: 4px; }
.model-file-row { display: flex; align-items: center; gap: 6px; padding: 3px 0; font-size: 12px; }
.model-file-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.model-file-dot.exist { background: #2ecc71; }
.model-file-dot.miss { background: #e06055; }
.model-file-name { flex: 1; }
.model-file-tag { font-size: 10px; padding: 1px 5px; border-radius: 3px; }
.model-file-tag.bundled { background: rgba(100, 140, 220, 0.15); color: #4068a0; }
.model-file-size { color: var(--text-subtle, #999); font-size: 11px; white-space: nowrap; }
.model-download-progress { margin-top: 8px; }
.progress-label { display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 4px; }
.progress-bar-wrap {
  height: 6px; background: var(--border-subtle, #e0e0e0); border-radius: 3px; overflow: hidden;
}
.progress-bar-fill { height: 100%; background: var(--accent, #2ecc71); border-radius: 3px; transition: width 0.3s; }
.progress-sub { font-size: 11px; color: var(--text-subtle, #888); }
.model-download-log { margin-top: 8px; max-height: 120px; overflow-y: auto; font-size: 11px; background: rgba(0,0,0,0.03); border-radius: 4px; padding: 6px 8px; }
.log-line { margin: 2px 0; color: var(--text-subtle, #666); }
.model-download-btn {
  padding: 8px 18px; font-size: 13px; border-radius: 5px; cursor: pointer;
  border: 1px solid var(--accent, #2ecc71);
  background: rgba(46, 204, 113, 0.12); color: var(--accent, #2ecc71);
}
.model-download-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* 头像 */
.avatar-mgmt-section { margin-top: 2px; }
.avatar-category { border: 1px solid var(--border-subtle, #e8e8e8); border-radius: 6px; padding: 8px; }
.avatar-cat-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.avatar-cat-label { font-size: 13px; font-weight: 600; }
.avatar-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.avatar-item { position: relative; width: 52px; height: 52px; }
.avatar-thumb { width: 100%; height: 100%; object-fit: cover; border-radius: 6px; border: 1px solid var(--border-subtle, #ddd); }
.avatar-del-btn {
  position: absolute; top: -4px; right: -4px;
  width: 18px; height: 18px; border-radius: 50%; border: none;
  background: rgba(224, 96, 85, 0.9); color: #fff;
  font-size: 12px; line-height: 1; cursor: pointer; display: flex; align-items: center; justify-content: center;
}

/* 扩展 */
.ext-desc { font-size: 12px; color: var(--text-subtle, #999); margin: 0 0 8px; }
.ext-section-header { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; margin-bottom: 6px; }
.ext-section-title { font-size: 13px; font-weight: 600; }
.ext-section-actions { display: flex; gap: 6px; }
.ext-sm-btn {
  font-size: 12px; padding: 4px 10px; border-radius: 4px; border: 1px solid var(--border-subtle, #ccc);
  background: var(--bg-btn, #f8f8f8); cursor: pointer; color: var(--text-primary, #222);
}
.ext-sm-btn.primary { background: var(--accent, #2ecc71); color: #fff; border-color: var(--accent, #2ecc71); }
.ext-sm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.ext-hint { font-size: 12px; color: var(--text-subtle, #aaa); padding: 6px 0; }
.ext-action-msg { font-size: 12px; margin-top: 4px; color: #339933; }
.ext-action-msg.error { color: #e06055; }

.tool-list { max-height: 200px; overflow-y: auto; }
.tool-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; border-bottom: 1px solid var(--border-subtle, #f0f0f0); }
.tool-name { flex: 1; font-weight: 500; }
.tool-risk { font-size: 10px; padding: 1px 6px; border-radius: 3px; }
.tool-risk.low { background: rgba(46,204,113,0.15); color: #2e8b57; }
.tool-risk.medium { background: rgba(255,200,0,0.15); color: #b08000; }
.tool-risk.high { background: rgba(224,96,85,0.15); color: #c04050; }
.tool-source-tag { font-size: 10px; padding: 1px 5px; border-radius: 3px; }
.tool-source-tag.skill { background: rgba(46,204,113,0.12); color: #2e8b57; }
.tool-desc { width: 80px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-subtle, #999); }
.ext-icon-btn { background: none; border: none; cursor: pointer; font-size: 13px; padding: 2px 4px; }
.ext-icon-btn.danger { color: #c04050; }

/* MCP */
.mcp-json-editor { margin-bottom: 8px; }
.mcp-json-textarea {
  width: 100%; height: 180px; font-family: 'Courier New', monospace; font-size: 12px;
  padding: 8px; border: 1px solid var(--border-subtle, #ddd); border-radius: 5px;
  background: var(--bg-input, #fff); color: var(--text-primary, #222); resize: vertical;
}
.mcp-json-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
.mcp-server-list { max-height: 280px; overflow-y: auto; }
.mcp-server-card {
  border: 1px solid var(--border-subtle, #e0e0e0); border-radius: 6px; padding: 8px; margin-bottom: 6px;
}
.mcp-server-card.online { border-color: rgba(46,204,113,0.3); }
.mcp-server-header { display: flex; align-items: center; justify-content: space-between; cursor: pointer; }
.mcp-server-left { display: flex; align-items: center; gap: 6px; }
.mcp-status-dot { width: 8px; height: 8px; border-radius: 50%; }
.mcp-status-dot.on { background: #2ecc71; }
.mcp-status-dot.off { background: #e06055; }
.mcp-server-name { font-size: 13px; font-weight: 500; }
.mcp-server-type { font-size: 10px; padding: 1px 5px; border-radius: 3px; background: rgba(100,140,220,0.12); color: #4068a0; }
.mcp-server-actions { display: flex; gap: 6px; align-items: center; }
.mcp-tool-list { margin-top: 6px; border-top: 1px solid var(--border-subtle, #e8e8e8); padding-top: 6px; }
.mcp-tool-count { font-size: 11px; color: var(--text-subtle, #888); }
</style>

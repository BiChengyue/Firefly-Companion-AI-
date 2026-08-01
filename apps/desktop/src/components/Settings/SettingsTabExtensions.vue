<script setup lang="ts">
import { inject } from 'vue'
import { useSettingsForm } from '@/composables/useSettingsForm'

const form = inject<ReturnType<typeof useSettingsForm>>('settingsForm')!
</script>

<template>
  <div class="form-section">
    <p class="ext-desc">管理 Agent 工具与扩展能力。内置工具自动加载，Skill 和 MCP 支持动态导入。</p>

    <!-- 内置工具 -->
    <div class="ext-section-header">
      <span class="ext-section-title">📦 内置工具 ({{ form.builtinTools.value.length }})</span>
    </div>
    <div v-if="form.builtinTools.value.length" class="tool-list">
      <div v-for="t in form.builtinTools.value" :key="t.name" class="tool-row builtin">
        <span class="tool-name">{{ t.name }}</span>
        <span class="tool-risk" :class="t.riskLevel">{{ form.riskLabel(t.riskLevel) }}</span>
      </div>
    </div>
    <div v-else class="ext-hint">无法加载工具列表（后端未连接）</div>

    <!-- Skill -->
    <div class="ext-section-header" style="margin-top: 4px;">
      <span class="ext-section-title">🧩 用户 Skill ({{ form.skillList.value.length }})</span>
      <div class="ext-section-actions">
        <button class="ext-sm-btn" :disabled="form.skillReloading.value" @click="form.handleReloadSkills()">{{ form.skillReloading.value ? '🔄 …' : '🔄 重载' }}</button>
        <button class="ext-sm-btn" :disabled="form.skillImporting.value" @click="form.handleImportSkill()">{{ form.skillImporting.value ? '⏳ …' : '📄 导入 SKILL.md' }}</button>
        <button class="ext-sm-btn primary" :disabled="form.skillImporting.value" @click="form.handleImportSkillFolder()">{{ form.skillImporting.value ? '⏳ …' : '📂 导入文件夹' }}</button>
      </div>
    </div>
    <div v-if="form.skillList.value.length" class="tool-list">
      <div v-for="s in form.skillList.value" :key="s.name" class="tool-row skill">
        <span class="tool-name">{{ s.name }}</span>
        <span class="tool-source-tag skill">SKILL.md</span>
        <span class="tool-desc">{{ s.description }}</span>
        <button class="ext-icon-btn danger" title="删除 Skill" @click="form.handleDeleteSkill(s.name)">🗑</button>
      </div>
    </div>
    <div v-else class="ext-hint">暂无用户 Skill。点击「📂 导入文件夹」选择含 SKILL.md 的 Skill 目录（支持 scripts/、references/ 附属资源）。</div>
    <p v-if="form.skillMsg.value" class="ext-action-msg" :class="{ error: form.skillMsg.value.startsWith('❌') }">{{ form.skillMsg.value }}</p>

    <!-- MCP -->
    <div class="ext-section-header" style="margin-top: 4px;">
      <span class="ext-section-title">🔌 MCP 服务 ({{ form.mcpServers.value.length }})</span>
      <button class="ext-sm-btn primary" @click="form.toggleMcpJsonEditor()">{{ form.mcpJsonVisible.value ? '− 取消编辑' : '编辑 JSON 配置' }}</button>
    </div>

    <div v-if="form.mcpJsonVisible.value" class="mcp-json-editor">
      <textarea v-model="form.mcpJsonContent.value" class="mcp-json-textarea" spellcheck="false" />
      <div class="mcp-json-actions">
        <button class="ext-sm-btn" @click="form.loadMcpRawConfig()">⟳ 重载原始内容</button>
        <button class="ext-sm-btn primary" :disabled="form.mcpJsonSaving.value" @click="form.handleSaveMcpJson()">{{ form.mcpJsonSaving.value ? '保存中…' : '💾 保存并应用' }}</button>
      </div>
    </div>

    <div v-if="form.mcpServers.value.length" class="mcp-server-list">
      <div v-for="srv in form.mcpServers.value" :key="srv.name" class="mcp-server-card" :class="{ online: srv.online, expanded: form.expandedServer.value === srv.name }">
        <div class="mcp-server-header" @click="form.toggleServerExpand(srv.name)">
          <div class="mcp-server-left">
            <span :class="['mcp-status-dot', srv.online ? 'on' : 'off']"></span>
            <span class="mcp-server-name">{{ srv.name }}</span>
            <span class="mcp-server-type">{{ srv.type }}</span>
          </div>
          <div class="mcp-server-actions" @click.stop>
            <button class="ext-sm-btn" @click="form.handleRefreshMcp(srv.name)">🔄</button>
            <button class="ext-sm-btn" @click="form.handleDeleteMcp(srv.name)" style="color: #e06055;">🗑</button>
          </div>
        </div>
        <div v-if="form.expandedServer.value === srv.name" class="mcp-tool-list">
          <p class="mcp-tool-count">{{ srv.tools?.length ?? 0 }} 个工具</p>
          <div v-if="srv.tools?.length" class="tool-list">
            <div v-for="t in srv.tools" :key="t.name" class="tool-row">
              <span class="tool-name">{{ t.name }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-else class="ext-hint">暂无 MCP 服务连接。</div>
    <p v-if="form.mcpActionMsg.value" class="ext-action-msg" :class="{ error: form.mcpActionMsg.value.startsWith('❌') }">{{ form.mcpActionMsg.value }}</p>
  </div>
</template>

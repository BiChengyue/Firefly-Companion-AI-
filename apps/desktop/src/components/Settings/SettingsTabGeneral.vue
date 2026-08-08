<script setup lang="ts">
import { inject } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import { useCompanionStore } from '@/stores/companion'
import { useSettingsForm } from '@/composables/useSettingsForm'
import { photoUrl } from '@/services/api'

const companion = useCompanionStore()
const settings = useSettingsStore()
const form = inject<ReturnType<typeof useSettingsForm>>('settingsForm')!

// 判断某张头像是否为当前分类正在显示的头像（用于高亮选中态）
function isCurrentAvatar(category: 'daily' | 'work', filename: string): boolean {
  const list = category === 'work' ? companion.workAvatars : companion.dailyAvatars
  const idx = category === 'work' ? companion.avatarIndexWork : companion.avatarIndexDaily
  if (!list.length) return false
  return list[Math.abs(idx) % list.length] === photoUrl(filename)
}

// 点击缩略图即设为当前显示头像
function selectAvatar(category: 'daily' | 'work', filename: string) {
  companion.selectAvatar(category, filename)
}
</script>

<template>
  <div class="form-section">
    <label class="form-label">WebSocket 端口</label>
    <input v-model="form.formWsPort.value" type="text" class="form-input" placeholder="8765" />

    <label class="form-label">重连延迟（毫秒）</label>
    <input v-model.number="form.formReconnectDelay.value" type="number" class="form-input" placeholder="5000" />

    <div class="divider" />
    <div class="toggle-row">
      <div class="toggle-info">
        <span class="toggle-label">🔓 日常模式解除限制</span>
        <span class="toggle-desc">允许日常模式的流萤执行工作类任务（Agent 任务）</span>
      </div>
      <button class="toggle-switch" :class="{ active: settings.dailyUnlocked }" @click="form.toggleDailyUnlockHandler()">
        <span class="toggle-knob" />
      </button>
    </div>
    <p v-if="settings.dailyUnlocked" class="diag-desc" style="color: #5ebd82;">
      ✅ 已解除限制：日常模式当前可执行 Agent 任务。
    </p>

    <!-- T34：深色模式开关 -->
    <div class="toggle-row">
      <div class="toggle-info">
        <span class="toggle-label">🌙 深色模式</span>
        <span class="toggle-desc">聊天界面切换纯黑配色 + 星空背景（刷新后保持；work 模式保持萨姆暗红主题）</span>
      </div>
      <button class="toggle-switch" :class="{ active: companion.themeMode === 'dark' }" @click="companion.toggleThemeMode()">
        <span class="toggle-knob" />
      </button>
    </div>

    <div class="divider" />
    <div class="toggle-row">
      <div class="toggle-info">
        <span class="toggle-label">🎵 工作模式 BGM</span>
        <span class="toggle-desc">
          {{ companion.bgmEnabled ? '已开启：切换工作模式时自动播放《永不复焉》（20 秒后停止）' : '已关闭：切换工作模式不再自动播放 BGM' }}
        </span>
      </div>
      <button class="toggle-switch" :class="{ active: companion.bgmEnabled }" @click="companion.toggleBgmEnabled()">
        <span class="toggle-knob" />
      </button>
    </div>

    <div class="divider" />
    <!-- 阶段25：主动聊天设置 -->
    <div class="toggle-row">
      <div class="toggle-info">
        <span class="toggle-label">💬 流萤主动发起对话</span>
        <span class="toggle-desc">
          {{ form.formProactiveChatEnabled.value ? '已开启：长时间不发言时流萤会主动找你聊天' : '已关闭：流萤只在收到消息时回复' }}
        </span>
      </div>
      <button
        class="toggle-switch"
        :class="{ active: form.formProactiveChatEnabled.value }"
        @click="form.formProactiveChatEnabled.value = !form.formProactiveChatEnabled.value"
      >
        <span class="toggle-knob" />
      </button>
    </div>

    <template v-if="form.formProactiveChatEnabled.value">
      <div class="form-section" style="margin-top: 8px;">
        <label class="form-label">空闲触发间隔（分钟）</label>
        <div style="display: flex; align-items: center; gap: 10px;">
          <input
            v-model.number="form.formProactiveChatIdleMinutes.value"
            type="range" min="1" max="120" step="1"
            style="flex: 1;"
          />
          <span style="font-size: 13px; min-width: 40px; text-align: right;">
            {{ form.formProactiveChatIdleMinutes.value }} 分钟
          </span>
        </div>

        <label class="form-label">静音时段</label>
        <div style="display: flex; align-items: center; gap: 8px; font-size: 13px;">
          <input
            v-model.number="form.formProactiveChatQuietStart.value"
            type="number" min="0" max="23"
            class="form-input" style="width: 60px;"
          />
          <span>点 ~</span>
          <input
            v-model.number="form.formProactiveChatQuietEnd.value"
            type="number" min="0" max="23"
            class="form-input" style="width: 60px;"
          />
          <span>点 不打扰</span>
        </div>

        <label class="form-label">每日主动聊天上限</label>
        <div style="display: flex; align-items: center; gap: 10px;">
          <input
            v-model.number="form.formProactiveChatDailyLimit.value"
            type="range" min="1" max="10" step="1"
            style="flex: 1;"
          />
          <span style="font-size: 13px; min-width: 30px; text-align: right;">
            {{ form.formProactiveChatDailyLimit.value }} 次
          </span>
        </div>

        <div class="divider" style="margin: 12px 0;" />
        <button type="button" class="proactive-test-btn" @click.stop="form.triggerProactive()">
          🎲 立即测试 — 让流萤主动对我说句话
        </button>
        <span class="toggle-desc" style="margin-top: 4px;">
          点击后将立即触发一次主动聊天，可验证引擎 B 是否正常运作
        </span>
      </div>
    </template>

    <div class="divider" />
    <div class="avatar-mgmt-section">
      <span class="toggle-label">🖼️ 角色头像管理</span>
      <span class="toggle-desc" style="margin-bottom: 8px;">
        添加或删除流萤的聊天头像，点击缩略图可设为当前显示的头像（聊天页面右侧显示）
      </span>
      <p v-if="form.avatarMsg.value" class="ext-action-msg" :class="{ error: form.avatarMsg.value.startsWith('❌') }" style="margin-bottom: 4px;">
        {{ form.avatarMsg.value }}
      </p>

      <div class="avatar-category">
        <div class="avatar-cat-header">
          <span class="avatar-cat-label">☕ 日常模式 ({{ form.dailyAvatarList.value.length }})</span>
          <button class="ext-sm-btn primary" :disabled="form.avatarUploading.value" @click="form.handleAvatarUpload('daily')">＋ 添加</button>
        </div>
        <div v-if="form.dailyAvatarList.value.length" class="avatar-grid">
          <div v-for="av in form.dailyAvatarList.value" :key="av.filename"
               class="avatar-item" :class="{ 'avatar-selected': isCurrentAvatar('daily', av.filename) }"
               @click="selectAvatar('daily', av.filename)" title="点击设为当前头像">
            <img :src="photoUrl(av.filename)" class="avatar-thumb" :alt="av.filename" />
            <button class="avatar-del-btn" title="删除此头像" @click.stop="form.handleAvatarDelete('daily', av.filename)">×</button>
          </div>
        </div>
        <div v-else-if="form.avatarLoading.value" class="ext-hint">加载中…</div>
        <div v-else class="ext-hint">暂无头像</div>
      </div>

      <div class="avatar-category" style="margin-top: 10px;">
        <div class="avatar-cat-header">
          <span class="avatar-cat-label">💼 工作模式 ({{ form.workAvatarList.value.length }})</span>
          <button class="ext-sm-btn primary" :disabled="form.avatarUploading.value" @click="form.handleAvatarUpload('work')">＋ 添加</button>
        </div>
        <div v-if="form.workAvatarList.value.length" class="avatar-grid">
          <div v-for="av in form.workAvatarList.value" :key="av.filename"
               class="avatar-item" :class="{ 'avatar-selected': isCurrentAvatar('work', av.filename) }"
               @click="selectAvatar('work', av.filename)" title="点击设为当前头像">
            <img :src="photoUrl(av.filename)" class="avatar-thumb" :alt="av.filename" />
            <button class="avatar-del-btn" title="删除此头像" @click.stop="form.handleAvatarDelete('work', av.filename)">×</button>
          </div>
        </div>
        <div v-else-if="form.avatarLoading.value" class="ext-hint">加载中…</div>
        <div v-else class="ext-hint">暂无头像</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.proactive-test-btn {
  width: 100%;
  padding: 10px 0;
  background: linear-gradient(135deg, var(--color-primary), #5ebd82);
  color: #fff;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s, transform 0.15s;
}
.proactive-test-btn:hover {
  opacity: 0.9;
  transform: scale(1.01);
}
.proactive-test-btn:active {
  transform: scale(0.98);
}
</style>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { listSkills, reloadSkills, importSkillFile, importSkillFolder, type SkillMeta } from '@/services/api'
import { useCompanionStore } from '@/stores/companion'

const companion = useCompanionStore()

/** 面板主题 — 内联 style 全量控制 */
const panelTheme = computed(() => {
  const isDaily = companion.mode === 'daily'
  const c = isDaily
    ? {
        bg: 'rgba(245, 252, 247, 0.97)',
        accent: '#2ecc71',
        glow: 'rgba(46, 204, 113, 0.22)',
        hover: 'rgba(46, 204, 113, 0.08)',
        border: 'rgba(46, 204, 113, 0.4)',
        label: '#1a8c4a',
        text: '#1a3328',
        sub: '#557766',
        input: '#334d3d',
      }
    : {
        bg: 'rgba(252, 247, 245, 0.97)',
        accent: '#cc4422',
        glow: 'rgba(204, 68, 34, 0.22)',
        hover: 'rgba(204, 68, 34, 0.08)',
        border: 'rgba(204, 68, 34, 0.4)',
        label: '#993322',
        text: '#33221a',
        sub: '#775544',
        input: '#4d3322',
      }
  return {
    backgroundColor: c.bg,
    borderColor: c.border,
    color: c.text,
    boxShadow: `0 6px 28px rgba(0,0,0,0.15), 0 0 14px ${c.glow}`,
    '--sp-hover': c.hover,
    '--sp-border': c.border,
    '--sp-label': c.label,
    '--sp-accent': c.accent,
    '--sp-text': c.text,
    '--sp-sub': c.sub,
    '--sp-input': c.input,
  }
})

const search = ref('')
const skills = ref<SkillMeta[]>([])
const loading = ref(false)
const importing = ref(false)

// 首字母头像色循环
const AVATAR_COLORS = ['#ff5544', '#ffaa44', '#44aaff', '#44ddaa', '#cc44ff', '#ff66aa']

function avatarColor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash)
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length]
}

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return skills.value
  return skills.value.filter(
    s => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q),
  )
})

async function load() {
  loading.value = true
  try {
    const data = await listSkills()
    skills.value = data.skills
  } catch {
    skills.value = []
  } finally {
    loading.value = false
  }
}

const emit = defineEmits<{
  select: [skillName: string]
  close: []
}>()

function select(skill: SkillMeta) {
  emit('select', skill.name)
}

async function handleImport() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.md'
  input.onchange = async () => {
    const file = input.files?.[0]
    if (!file) return
    importing.value = true
    try {
      const text = await file.text()
      await importSkillFile(text)
      await load()
    } catch {
      // ignore
    } finally {
      importing.value = false
    }
  }
  input.click()
}

async function handleImportFolder() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.md'
  input.setAttribute('webkitdirectory', '')
  input.setAttribute('directory', '')
  input.onchange = async () => {
    const fileList = input.files
    if (!fileList || fileList.length === 0) return
    importing.value = true
    try {
      const files: { path: string; content: string }[] = []
      for (const f of Array.from(fileList)) {
        if (!f.name.endsWith('.md') && !f.name.endsWith('.MD')) continue
        // 相对路径取文件名所在目录最后一段作为 skill 目录名
        const rel = f.webkitRelativePath || f.name
        files.push({ path: rel, content: await f.text() })
      }
      if (files.length === 0) return
      await importSkillFolder(files)
      await load()
    } catch {
      // ignore
    } finally {
      importing.value = false
    }
  }
  input.click()
}

async function handleRefresh() {
  await reloadSkills()
  await load()
}

onMounted(load)
</script>

<template>
  <div class="skill-picker" :style="panelTheme" @click.stop>
    <!-- 搜索栏 -->
    <div class="sp-search-wrap">
      <span class="sp-search-icon">🔍</span>
      <input
        v-model="search"
        class="sp-search-input"
        placeholder="搜索 Skill..."
        autofocus
      />
    </div>

    <!-- 列表 -->
    <div class="sp-list">
      <template v-if="loading && skills.length === 0">
        <div class="sp-empty">加载中…</div>
      </template>
      <template v-else-if="filtered.length === 0">
        <div class="sp-empty">
          {{ search ? '无匹配 Skill' : '暂无 Skill，请先导入' }}
        </div>
      </template>
      <button
        v-for="s in filtered"
        :key="s.name"
        class="sp-item"
        @click="select(s)"
      >
        <span
          class="sp-avatar"
          :style="{ background: avatarColor(s.name) }"
        >{{ s.name[0].toUpperCase() }}</span>
        <span class="sp-meta">
          <span class="sp-name">{{ s.name }}</span>
          <span class="sp-desc">{{ s.description || '（无描述）' }}</span>
        </span>
      </button>
    </div>

    <!-- 底部操作 -->
    <div class="sp-footer">
      <button
        class="sp-footer-btn"
        :disabled="importing"
        @click="handleImport"
      >
        📄 单文件
      </button>
      <button
        class="sp-footer-btn"
        :disabled="importing"
        @click="handleImportFolder"
      >
        📁 文件夹
      </button>
      <button
        class="sp-footer-btn"
        :disabled="loading"
        @click="handleRefresh"
      >
        🔄
      </button>
    </div>
  </div>
</template>

<style scoped>
.skill-picker {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  width: 340px;
  max-height: 400px;
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  z-index: 200;
  backdrop-filter: blur(12px);
}

/* 搜索栏 */
.sp-search-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  flex-shrink: 0;
}
.sp-search-icon {
  font-size: 14px;
  opacity: 0.5;
  flex-shrink: 0;
}
.sp-search-input {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  outline: none;
}

/* 列表 */
.sp-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}
.sp-empty {
  text-align: center;
  font-size: 12px;
  padding: 24px 12px;
}

/* 列表项 */
.sp-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: background 0.15s;
}
.sp-avatar {
  width: 30px;
  height: 30px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: bold;
  color: #fff;
  flex-shrink: 0;
}
.sp-meta {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.sp-name {
  font-size: 13px;
  font-weight: 600;
}
.sp-desc {
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 底部 */
.sp-footer {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  flex-shrink: 0;
}
.sp-footer-btn {
  flex: 1;
  border-radius: 6px;
  padding: 4px 0;
  background: transparent;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s;
}
.sp-footer-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
</style>

<style>
/* ── 子元素主题色 ── */
.sp-search-wrap {
  border-bottom: 1px solid var(--sp-hover, rgba(204,51,0,0.10));
}
.sp-search-input { color: var(--sp-input, #333); }
.sp-search-input::placeholder { color: var(--sp-sub, #999); }
.sp-item:hover {
  background: var(--sp-hover, rgba(204,51,0,0.10));
}
.sp-name { color: var(--sp-text, #222); }
.sp-desc { color: var(--sp-sub, #777); }
.sp-footer {
  border-top: 1px solid var(--sp-hover, rgba(204,51,0,0.10));
}
.sp-footer-btn {
  border: 1px solid var(--sp-border, rgba(204,51,0,0.25));
  color: var(--sp-sub, #777);
}
.sp-footer-btn:hover:not(:disabled) {
  border-color: var(--sp-accent, rgba(204,51,0,0.35));
  color: var(--sp-label, #ff5544);
  background: var(--sp-hover, rgba(204,51,0,0.10));
}
.sp-empty {
  color: var(--sp-sub, #999);
}
</style>

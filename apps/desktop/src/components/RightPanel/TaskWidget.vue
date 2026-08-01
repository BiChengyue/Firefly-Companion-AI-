<script setup lang="ts">
/** 今日任务 — 与 LeftSidebar 共享 firefly_tasks 数据源（useTaskSync 单例）。 */
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useTaskSync } from '@/composables/useTaskSync'

const { tasks, toggleTask, deleteTask, addTask, onExternalChange } = useTaskSync()

let unsub: (() => void) | null = null
onMounted(() => { unsub = onExternalChange() })
onUnmounted(() => { if (unsub) unsub() })

// 任务添加状态
const showAddTask = ref(false)
const newTaskText = ref('')
const addTaskInput = ref<HTMLInputElement | null>(null)

function startAddTask() {
  showAddTask.value = !showAddTask.value
  if (showAddTask.value) {
    newTaskText.value = ''
    nextTick(() => { addTaskInput.value?.focus() })
  }
}

function cancelAddTask() {
  showAddTask.value = false
  newTaskText.value = ''
}

function commitNewTask() {
  const text = newTaskText.value.trim()
  if (text) addTask(text)
  newTaskText.value = ''
  showAddTask.value = false
}
</script>

<template>
  <div class="card">
    <div class="card-header">
      <span>今日任务</span>
      <button class="add-btn" title="添加任务" @click="startAddTask">＋</button>
    </div>

    <!-- 添加任务输入栏 -->
    <div v-if="showAddTask" class="add-task-row">
      <input
        ref="addTaskInput"
        v-model="newTaskText"
        type="text"
        placeholder="输入任务名..."
        class="add-task-input"
        @keydown.enter="commitNewTask"
        @keydown.escape="cancelAddTask"
      />
      <button class="confirm-btn" title="确认添加" @click="commitNewTask">✓</button>
      <button class="cancel-btn" title="取消" @click="cancelAddTask">✕</button>
    </div>

    <ul v-if="tasks.length" class="task-list">
      <li
        v-for="t in tasks"
        :key="t.id"
        class="task-item"
        :class="{ done: t.done }"
        @click="toggleTask(t.id)"
      >
        <span class="chk">{{ t.done ? '☑' : '☐' }}</span>
        <span class="txt">{{ t.text }}</span>
        <button class="del-btn" title="删除任务" @click.stop="deleteTask(t.id, $event)">×</button>
      </li>
    </ul>
    <div v-else class="empty">✓ 暂无任务</div>
  </div>
</template>

<style scoped>
.card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.add-btn {
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: bold;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  border-radius: 4px;
  transition: color var(--transition-fast), background var(--transition-fast);
}
.add-btn:hover {
  color: var(--accent);
  background: var(--accent-light);
}

/* 新增输入栏 */
.add-task-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
}
.add-task-input {
  flex: 1;
  min-width: 0;
  box-sizing: border-box;
  padding: 4px 8px;
  border: 1px solid var(--border-accent);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
}
.add-task-input:focus {
  border-color: var(--accent);
}
.confirm-btn,
.cancel-btn {
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: bold;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 4px;
  transition: color var(--transition-fast), background var(--transition-fast);
}
.confirm-btn:hover {
  color: #5ebd82;
  background: rgba(94, 189, 130, 0.15);
}
.cancel-btn:hover {
  color: #cc3300;
  background: rgba(204, 51, 0, 0.15);
}

.task-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow-y: auto;
}
.task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-primary);
  cursor: pointer;
  padding: 4px 6px;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast);
}
.task-item:hover {
  background: var(--bg-surface-hover);
}
.task-item.done .txt {
  text-decoration: line-through;
  color: var(--text-muted);
  opacity: 0.7;
}

.chk {
  flex-shrink: 0;
  font-size: 13px;
}
.txt {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.del-btn {
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  border-radius: 4px;
  opacity: 0;
  margin-left: auto;
  transition: opacity var(--transition-fast), color var(--transition-fast);
}
.task-item:hover .del-btn {
  opacity: 1;
}
.del-btn:hover {
  color: #cc3300;
  background: rgba(204, 51, 0, 0.12);
}

.empty {
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
  padding: 8px 0;
}
</style>

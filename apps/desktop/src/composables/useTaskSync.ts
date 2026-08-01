import { ref, computed } from 'vue'

/**
 * 共享 tasks 逻辑 — 消除 LeftSidebar 与 TaskWidget 之间的重复代码。
 * 数据持久化到 localStorage，通过 CustomEvent 跨组件同步。
 */

export interface Task {
  id: string
  text: string
  done: boolean
}

const STORAGE_KEY = 'firefly_tasks'
const EVENT_NAME = 'firefly-tasks-changed'

const DEFAULT_TASKS: Task[] = [
  { id: 't1', text: '完成委托 x 3', done: false },
  { id: 't2', text: '整理工作空间文件', done: false },
  { id: 't3', text: '检查能源储备', done: true },
]

function loadTasks(): Task[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : [...DEFAULT_TASKS]
  } catch {
    return [...DEFAULT_TASKS]
  }
}

function saveTasks(tasks: Task[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks))
  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: tasks }))
}

/* 全局共享实例（单例模式，所有组件共享同一个 ref） */
let _instance: ReturnType<typeof createTaskSync> | null = null

function createTaskSync() {
  const tasks = ref<Task[]>(loadTasks())

  const sortedTasks = computed(() => [
    ...tasks.value.filter((t) => !t.done),
    ...tasks.value.filter((t) => t.done),
  ])

  function toggleTask(id: string) {
    const t = tasks.value.find((t) => t.id === id)
    if (t) {
      t.done = !t.done
      saveTasks(tasks.value)
    }
  }

  function deleteTask(id: string, event?: MouseEvent) {
    event?.stopPropagation()
    tasks.value = tasks.value.filter((t) => t.id !== id)
    saveTasks(tasks.value)
  }

  function addTask(text: string) {
    tasks.value.unshift({ id: `t${Date.now()}`, text, done: false })
    saveTasks(tasks.value)
  }

  /**
   * 监听其他组件通过 CustomEvent 触发的任务变化（如 TaskWidget 的变更同步到 LeftSidebar）。
   * 返回 unsubscribe 函数。
   */
  function onExternalChange() {
    const handler = ((e: CustomEvent) => {
      if (e.detail && Array.isArray(e.detail)) {
        tasks.value = e.detail
      }
    }) as EventListener
    window.addEventListener(EVENT_NAME, handler)
    return () => window.removeEventListener(EVENT_NAME, handler)
  }

  return {
    tasks,
    sortedTasks,
    toggleTask,
    deleteTask,
    addTask,
    onExternalChange,
    STORAGE_KEY,
  }
}

/** 获取全局共享的 task sync 实例 */
export function useTaskSync() {
  if (!_instance) {
    _instance = createTaskSync()
  }
  return _instance
}

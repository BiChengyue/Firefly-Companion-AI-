import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getMemories, deleteMemory } from '@/services/api'

export interface ReminderItem {
  id: string
  text: string
  dueTimestamp: number // 到期时间戳 (ms)
  status: 'pending' | 'fired' | 'dismissed'
  fromApi?: boolean
  createdAt: number
}

const LOCAL_KEY = 'firefly_reminders'

function loadLocal(): ReminderItem[] {
  try {
    const raw = localStorage.getItem(LOCAL_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as Array<Record<string, unknown>>
    // 补全结构兼容（老旧版数据字段可能不同）
    return parsed.map((item) => ({
      id: `${item.id ?? `r_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`}`,
      text: `${item.text ?? item.content ?? '提醒'}`,
      dueTimestamp: Number(item.dueTimestamp)
        || (Number(item.createdAt) ? Number(item.createdAt) + 300000 : Date.now() + 300000),
      status: (`${item.status ?? 'pending'}` as 'pending' | 'fired' | 'dismissed'),
      fromApi: !!item.fromApi,
      createdAt: Number(item.createdAt) || Date.now(),
    }))
  } catch {
    return []
  }
}

function saveLocal(items: ReminderItem[]) {
  localStorage.setItem(LOCAL_KEY, JSON.stringify(items))
  window.dispatchEvent(new CustomEvent('firefly-reminders-changed', { detail: items }))
}

// 智能时间解析器
export function parseTimeExpression(input: string): { text: string; dueTimestamp: number } {
  let text = input.trim()
  let dueTimestamp = Date.now() + 5 * 60 * 1000 // 默认 5 分钟后

  // 清理聊天的消息尾部时间戳（如 21:26）
  text = text.replace(/\b\d{1,2}:\d{2}\b/g, '').trim()

  const now = new Date()
  const targetDate = new Date()
  let timeFound = false

  // 中文数字转阿拉伯数字（支持 十/十二/二十/五/零 等常用表达）
  const cnNumToInt = (s: string): number | null => {
    const t = s.trim()
    if (/^\d+$/.test(t)) return parseInt(t, 10)
    const cnMap: Record<string, number> = {
      零: 0, 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9,
    }
    if (cnMap[t] !== undefined) return cnMap[t]
    // 十/十几/几十/几十几
    const m = t.match(/^(十|([一二三四五六七八九])十)?([一二三四五六七八九])?$/)
    if (!m) return null
    let tens = 0
    if (m[1]) {
      if (m[1] === '十') tens = 10
      else if (m[2]) tens = cnMap[m[2]] * 10
    }
    const ones = m[3] ? cnMap[m[3]] : 0
    const val = tens + ones
    return val === 0 && t !== '零' ? null : val
  }

  // 1. 相对时间：秒 / 分 / 时（支持阿拉伯数字与中文数字，如"10秒""十秒""十二分钟"）
  const durationRe = /([0-9]+|[一二三四五六七八九十百]+)\s*(秒后?|s|sec|分钟后?|分后|m|min|小时后?|时后|h|hour)/i
  const durationMatch = text.match(durationRe)

  if (durationMatch) {
    const numVal = cnNumToInt(durationMatch[1])
    const unit = durationMatch[2].toLowerCase()
    if (numVal !== null) {
      const unitChar = unit.includes('秒') || unit === 's' || unit === 'sec' ? 's'
        : (unit.includes('分') || unit === 'm' || unit === 'min') ? 'm'
        : 'h'
      const secs = numVal * (unitChar === 's' ? 1 : unitChar === 'm' ? 60 : 3600)
      dueTimestamp = Date.now() + secs * 1000
      text = text.replace(durationMatch[0], '').trim()
      timeFound = true
    }
  }

  // 2. 绝对时间：明天/今天/后天 + 时间点 (如 早上八点, 8点, 08:00)
  if (!timeFound) {
    let daysAdd = 0
    if (text.includes('明天')) {
      daysAdd = 1
      text = text.replace('明天', '')
    } else if (text.includes('后天')) {
      daysAdd = 2
      text = text.replace('后天', '')
    } else if (text.includes('今天')) {
      daysAdd = 0
      text = text.replace('今天', '')
    }

    const cnNumMap: Record<string, number> = {
      一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10, '08': 8, '8': 8
    }

    const cnTimeMatch = text.match(/(早上|上午|中午|下午|晚上)?\s*(\d+|一|二|三|四|五|六|七|八|九|十)+点(半|\d+分)?/)
    const clockMatch = text.match(/(\d{1,2})[:：](\d{2})/)

    if (cnTimeMatch) {
      const period = cnTimeMatch[1] || ''
      const numStr = cnTimeMatch[2]
      const minStr = cnTimeMatch[3] || ''

      let h = /^\d+$/.test(numStr) ? parseInt(numStr, 10) : (cnNumMap[numStr] || 8)
      if ((period === '下午' || period === '晚上') && h < 12) {
        h += 12
      } else if ((period === '早上' || period === '上午') && h === 12) {
        h = 0
      }

      let m = 0
      if (minStr.includes('半')) {
        m = 30
      } else {
        const cleanM = minStr.replace(/\D/g, '')
        if (cleanM) m = parseInt(cleanM, 10)
      }

      targetDate.setDate(targetDate.getDate() + daysAdd)
      targetDate.setHours(h, m, 0, 0)

      if (targetDate.getTime() <= now.getTime() && daysAdd === 0) {
        targetDate.setDate(targetDate.getDate() + 1)
      }

      dueTimestamp = targetDate.getTime()
      text = text.replace(cnTimeMatch[0], '').trim()
      timeFound = true
    } else if (clockMatch) {
      const h = parseInt(clockMatch[1], 10)
      const m = parseInt(clockMatch[2], 10)

      targetDate.setDate(targetDate.getDate() + daysAdd)
      targetDate.setHours(h, m, 0, 0)

      if (targetDate.getTime() <= now.getTime() && daysAdd === 0) {
        targetDate.setDate(targetDate.getDate() + 1)
      }

      dueTimestamp = targetDate.getTime()
      text = text.replace(clockMatch[0], '').trim()
      timeFound = true
    }
  }

  // 清理常见的前缀口令
  text = text.replace(/^(流萤[，,]*)?\s*(请)?(帮我)?(提醒我|提醒|定闹钟|叫醒)\s*/, '').trim()
  text = text.replace(/^(告诉我|去|做)\s*/, '').trim()

  if (!text) text = '定时提醒'

  return { text, dueTimestamp }
}

const items = ref<ReminderItem[]>(loadLocal())
let timerId: number | null = null

export function useReminderScheduler() {
  const pendingReminders = computed(() =>
    items.value
      .filter((i) => i.status === 'pending')
      .sort((a, b) => a.dueTimestamp - b.dueTimestamp)
  )

  const firedReminders = computed(() =>
    items.value
      .filter((i) => i.status === 'fired')
      .sort((a, b) => b.dueTimestamp - a.dueTimestamp)
  )

  const activeToast = ref<ReminderItem | null>(null)

  function startScheduler() {
    if (timerId !== null) return
    timerId = window.setInterval(checkReminders, 1000)
  }

  function stopScheduler() {
    if (timerId !== null) {
      clearInterval(timerId)
      timerId = null
    }
  }

  function checkReminders() {
    const now = Date.now()
    let updated = false

    items.value.forEach((item) => {
      if (item.status === 'pending' && now >= item.dueTimestamp) {
        item.status = 'fired'
        updated = true
        triggerAlert(item)
      }
    })

    if (updated) {
      saveLocal([...items.value])
    }
  }

  function triggerAlert(item: ReminderItem) {
    activeToast.value = item

    // 1. 发送桌面系统通知
    try {
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification('🔔 流萤提醒到期！', {
          body: item.text,
          icon: '/favicon.ico',
        })
      } else if ('Notification' in window && Notification.permission !== 'denied') {
        Notification.requestPermission().then((permission) => {
          if (permission === 'granted') {
            new Notification('🔔 流萤提醒到期！', { body: item.text })
          }
        })
      }
    } catch {
      /* ignore */
    }

    // 2. 派发全局跨窗口与组件到期事件 (Tauri 跨窗口广播 + 本地 DOM 事件)
    window.dispatchEvent(new CustomEvent('firefly-reminder-fired', { detail: item }))
    try {
      import('@tauri-apps/api/event').then(({ emit }) => {
        emit('firefly-reminder-fired', { text: item.text, id: item.id })
      })
    } catch {
      /* 非 Tauri 环境退回到 DOM 事件 */
    }
  }

  function addReminder(textOrExpr: string, customDelayMs?: number, fromApi = false) {
    let text = textOrExpr.trim()
    let dueTimestamp = Date.now() + 5 * 60 * 1000

    if (customDelayMs && customDelayMs > 0) {
      dueTimestamp = Date.now() + customDelayMs
    } else {
      const parsed = parseTimeExpression(textOrExpr)
      text = parsed.text
      dueTimestamp = parsed.dueTimestamp
    }

    // 去重：发送消息时前端会即时建一张本地卡（fromApi=false），后端解析同一句话后
    // 又会经 WebSocket 推送 reminder_created（fromApi=true）。两者是同一条提醒，
    // 故收到后端确认时，把时间相近的本地即时卡「升级」为权威版本，而非重复建卡/重复响铃。
    if (fromApi) {
      const DEDUP_WINDOW_MS = 60 * 1000
      const candidate = items.value
        .filter(
          (i) =>
            i.status === 'pending' &&
            !i.fromApi &&
            Math.abs(i.dueTimestamp - dueTimestamp) <= DEDUP_WINDOW_MS,
        )
        // 优先升级最近创建的本地卡（即本次发送触发的即时卡，而非更早的手工胶囊）
        .sort((a, b) => b.createdAt - a.createdAt)[0]
      if (candidate) {
        candidate.text = text
        candidate.dueTimestamp = dueTimestamp
        candidate.fromApi = true
        saveLocal([...items.value])
        return candidate
      }
    }

    const newItem: ReminderItem = {
      id: `r_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      text,
      dueTimestamp,
      status: 'pending',
      fromApi,
      createdAt: Date.now(),
    }

    items.value.unshift(newItem)
    saveLocal([...items.value])
    return newItem
  }

  function removeReminder(id: string, fromApi = false) {
    items.value = items.value.filter((i) => i.id !== id)
    saveLocal([...items.value])
    if (fromApi) {
      deleteMemory(id).catch(() => {})
    }
  }

  function dismissReminder(id: string) {
    const item = items.value.find((i) => i.id === id)
    if (item) {
      item.status = 'dismissed'
      if (activeToast.value?.id === id) {
        activeToast.value = null
      }
      saveLocal([...items.value])
    }
  }

  function snoozeReminder(id: string, snoozeMinutes = 5) {
    const item = items.value.find((i) => i.id === id)
    if (item) {
      item.dueTimestamp = Date.now() + snoozeMinutes * 60 * 1000
      item.status = 'pending'
      if (activeToast.value?.id === id) {
        activeToast.value = null
      }
      saveLocal([...items.value])
    }
  }

  async function syncBackendReminders() {
    try {
      const mems = await getMemories(undefined, 'daily')
      const promises = mems.filter((m) => m.type === 'promise')
      let added = false
      for (const m of promises) {
        if (!items.value.find((i) => i.id === m.id)) {
          const parsed = parseTimeExpression(m.content)
          items.value.unshift({
            id: m.id,
            text: parsed.text,
            dueTimestamp: parsed.dueTimestamp,
            status: 'pending',
            fromApi: true,
            createdAt: Date.now(),
          })
          added = true
        }
      }
      if (added) saveLocal([...items.value])
    } catch {
      /* 后端不可用 */
    }
  }

  onMounted(() => {
    startScheduler()
    syncBackendReminders()

    window.addEventListener('firefly-reminders-changed', ((e: CustomEvent) => {
      if (e.detail && Array.isArray(e.detail)) {
        items.value = e.detail
      }
    }) as EventListener)
  })

  onUnmounted(() => {
    stopScheduler()
  })

  return {
    items,
    pendingReminders,
    firedReminders,
    activeToast,
    addReminder,
    removeReminder,
    dismissReminder,
    snoozeReminder,
    parseTimeExpression,
  }
}

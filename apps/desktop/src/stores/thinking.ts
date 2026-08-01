/** useThinkingStore — 思考/规划流状态管理。

负责：
- 流式思考文本（currentThinking / currentPlanning）
- 思考历史记录（SAM HUD idle 模式回看）
- HUD 面板可见性切换
*/
import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 思考历史记录 */
export interface ThinkingRecord {
  id: string
  content: string
  timestamp: number
  type: 'thinking' | 'planning'
}

export const useThinkingStore = defineStore('thinking', () => {
  // === 状态 ===
  const currentThinking = ref('')
  const currentPlanning = ref('')
  const thinkingHistory = ref<ThinkingRecord[]>([])
  const MAX_THINKING_HISTORY = 20

  /** 追加思考记录到历史 */
  function appendThinking(record: ThinkingRecord) {
    thinkingHistory.value = [record, ...thinkingHistory.value].slice(0, MAX_THINKING_HISTORY)
  }

  /** 清空当前思考流 */
  function clearThinking() {
    currentThinking.value = ''
    currentPlanning.value = ''
  }

  /** 清空历史 */
  function clearHistory() {
    thinkingHistory.value = []
  }

  // === 暴露 ===
  return {
    currentThinking,
    currentPlanning,
    thinkingHistory,
    appendThinking,
    clearThinking,
    clearHistory,
  }
})

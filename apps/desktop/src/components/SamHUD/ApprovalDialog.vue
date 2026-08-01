<script setup lang="ts">
/**
 * 高危操作授权弹窗 — 对应 spec 阶段4。
 *
 * 当 Agent 执行 risk_level="high" 的工具时弹出，要求用户确认。
 * 显示工具名、参数和风险说明，用户选择"授权"或"拒绝"。
 */
import { useCompanionStore } from '@/stores/companion'
import { wsClient } from '@/services/ws'
import type { WsServerMessage } from '@shared/index'

const companion = useCompanionStore()

// 监听 tool_call 消息中 requiresApproval=true 的事件
// 这里通过 store 的 approvalPendingId 来判断是否有待审批项
// 实际的 tool_call 数据存储在最后收到的 tool_call 消息中
let pendingTool: WsServerMessage & { type: 'tool_call' } | null = null

function handleToolCall(msg: WsServerMessage) {
  if (msg.type === 'tool_call' && msg.requiresApproval) {
    pendingTool = msg as WsServerMessage & { type: 'tool_call' }
    companion.setApprovalPending(msg.stepId || null)
  }
}

// 注册 + 注销监听
const unsubscribe = wsClient.onMessage((msg) => {
  if (msg.type === 'tool_call' && msg.requiresApproval) {
    handleToolCall(msg)
  }
})

import { onUnmounted } from 'vue'
onUnmounted(() => unsubscribe())

function approve() {
  if (!pendingTool || !pendingTool.stepId) return
  wsClient.send({ type: 'approval_response', stepId: pendingTool.stepId, approved: true })
  companion.setApprovalPending(null)
  pendingTool = null
}

function deny() {
  if (!pendingTool || !pendingTool.stepId) return
  wsClient.send({ type: 'approval_response', stepId: pendingTool.stepId, approved: false })
  companion.setApprovalPending(null)
  pendingTool = null
}
</script>

<template>
  <Teleport to="body">
    <div v-if="companion.approvalPendingId" class="overlay" @click.self="deny">
      <div class="dialog">
        <div class="dialog-header">
          <span class="warn-icon">⚠</span>
          <span class="title">高危操作授权</span>
        </div>

        <div class="dialog-body">
          <div class="info-row">
            <span class="label">工具</span>
            <span class="value">{{ pendingTool?.name || '—' }}</span>
          </div>
          <div class="info-row">
            <span class="label">参数</span>
            <span class="value code">{{ JSON.stringify(pendingTool?.args || {}) }}</span>
          </div>
          <div v-if="pendingTool?.description" class="info-row">
            <span class="label">说明</span>
            <span class="value dim">{{ pendingTool?.description }}</span>
          </div>
        </div>

        <div class="dialog-warn">
          ⚠ 此操作涉及高危命令执行，可能影响系统文件。
        </div>

        <div class="dialog-actions">
          <button class="btn-deny" @click="deny">拒绝</button>
          <button class="btn-approve" @click="approve">授权执行</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.dialog {
  background: rgba(20, 4, 0, 0.95);
  border: 1px solid rgba(204, 51, 0, 0.5);
  border-radius: 12px;
  padding: 20px 24px;
  width: 420px;
  max-width: 90vw;
  box-shadow: 0 0 40px rgba(204, 51, 0, 0.2);
  font-family: 'Courier New', Courier, monospace;
}

.dialog-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.warn-icon {
  font-size: 22px;
  animation: pulse-warn 1.5s infinite;
}

@keyframes pulse-warn {
  0%, 100% { opacity: 0.6; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.15); }
}

.title {
  color: #ff5544;
  font-size: 15px;
  font-weight: bold;
  letter-spacing: 1px;
  text-shadow: 0 0 8px rgba(204, 51, 0, 0.5);
}

.dialog-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}

.info-row {
  display: flex;
  gap: 8px;
  font-size: 12px;
}

.label {
  color: rgba(204, 51, 0, 0.5);
  flex-shrink: 0;
  width: 36px;
}

.value {
  color: #ffccaa;
  word-break: break-all;
}

.value.code {
  color: #ffaa66;
  font-family: 'Courier New', monospace;
  font-size: 11px;
}

.value.dim {
  color: rgba(255, 170, 130, 0.5);
  font-style: italic;
}

.dialog-warn {
  font-size: 11px;
  color: #ff6644;
  padding: 8px 10px;
  background: rgba(204, 51, 0, 0.1);
  border: 1px solid rgba(204, 51, 0, 0.2);
  border-radius: 6px;
  margin-bottom: 16px;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

.btn-deny, .btn-approve {
  padding: 8px 18px;
  border-radius: 6px;
  border: 1px solid;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-deny {
  background: transparent;
  color: rgba(255, 170, 130, 0.6);
  border-color: rgba(204, 51, 0, 0.3);
}

.btn-deny:hover {
  background: rgba(204, 51, 0, 0.1);
  color: #ffccaa;
}

.btn-approve {
  background: rgba(204, 51, 0, 0.2);
  color: #ff5544;
  border-color: rgba(204, 51, 0, 0.5);
  font-weight: bold;
}

.btn-approve:hover {
  background: rgba(204, 51, 0, 0.35);
  color: #fff;
  box-shadow: 0 0 12px rgba(204, 51, 0, 0.3);
}
</style>

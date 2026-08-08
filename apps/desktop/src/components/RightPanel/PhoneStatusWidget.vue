<script setup lang="ts">
/**
 * 手机状态卡（占位）— 📱
 * 手机端（Android 客户端）尚未落地。此卡片先占位并留好接口：
 * 未来数据源 = hub `GET /api/v1/phone-state`（battery / charging / dnd / loc_bucket），
 * 与 sensor_context._fmt_phone 同构；接入时填 `details` 列表即可，卡片结构无需改动。
 */
</script>

<template>
  <div class="phone-card">
    <div class="head">
      <span class="title">📱 手机状态</span>
    </div>
    <div class="unavailable">手机端待接入</div>
    <!-- future: <ul class="details"> 电量 / 充电 / 勿扰 / 位置 </ul> -->
    <div class="foot">更新于 —</div>
  </div>
</template>

<style scoped>
.phone-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  font-size: 12px;
  color: var(--text-secondary);
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary);
}
.unavailable {
  font-size: 11px;
  color: var(--text-muted);
  padding: 6px 0;
}
.foot {
  margin-top: 6px;
  font-size: 9px;
  color: var(--text-muted);
  font-family: 'Courier New', monospace;
}
</style>

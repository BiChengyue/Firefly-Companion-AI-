<script setup lang="ts">
import { computed } from 'vue'
import { useCompanionStore } from '@/stores/companion'

const companion = useCompanionStore()

const imgSrc = computed(() => companion.getCurrentAvatar())

function handlePrev(e: Event) {
  e.stopPropagation()
  companion.rotateAvatarLeft()
}

function handleNext(e: Event) {
  e.stopPropagation()
  companion.rotateAvatar()
}
</script>

<template>
  <div class="char-card" title="更换头像">
    <div class="char-img">
      <img :src="imgSrc" :alt="companion.isWork ? '流萤 SAM MODE' : '流萤 Daily'" class="char-pic" />
      <!-- 左右箭头按钮 -->
      <button class="arrow-btn arrow-left-btn" @click="handlePrev" title="上一张">◂</button>
      <button class="arrow-btn arrow-right-btn" @click="handleNext" title="下一张">▸</button>
      <div class="char-label">
        <span class="char-name">流萤</span>
        <span v-if="companion.isWork" class="char-sub">● SAM MODE</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.char-card {
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: var(--shadow-md);
  flex-shrink: 0;
  cursor: pointer;
  transition: opacity var(--transition-fast);
}
.char-card:hover {
  opacity: 0.92;
}

.char-img {
  position: relative;
  aspect-ratio: 3 / 4;
  overflow: hidden;
  background: var(--bg-surface);
}

.char-pic {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.3s ease;
}
.char-card:hover .char-pic {
  transform: scale(1.03);
}

/* 左右箭头按钮 */
.arrow-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  font-size: 18px;
  color: #fff;
  background: rgba(0, 0, 0, 0.35);
  border: none;
  border-radius: 50%;
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.2s, background 0.2s;
  z-index: 2;
}
.char-card:hover .arrow-btn {
  opacity: 1;
}
.arrow-btn:hover {
  background: rgba(0, 0, 0, 0.6);
}
.arrow-left-btn {
  left: 6px;
}
.arrow-right-btn {
  right: 6px;
}

.char-label {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 8px 12px;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.6));
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.char-name {
  font-size: 18px;
  font-weight: 700;
  color: #fff;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.5);
}

.char-sub {
  font-size: 10px;
  font-family: 'Courier New', monospace;
  color: var(--accent);
  text-shadow: 0 0 6px var(--accent-glow);
  letter-spacing: 1px;
}
</style>

<template>
  <nav class="doc-outline" aria-label="文档目录">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="doc-outline-item"
      :class="{ 'is-active': item.key === activeKey }"
      :style="{ paddingLeft: `${8 + (item.level - 1) * 14}px` }"
      :title="item.number ? `${item.number} ${item.text}` : item.text"
      @click="emit('jump', item.key)"
    >
      <span v-if="item.number" class="doc-outline-number">{{ item.number }}</span>
      <span class="doc-outline-text">{{ item.text }}</span>
    </button>
  </nav>
</template>

<script setup lang="ts">
interface DocOutlineItem {
  key: string
  text: string
  level: number
  number?: string
}

defineProps<{
  items: DocOutlineItem[]
  activeKey: string
}>()

const emit = defineEmits<{
  (e: 'jump', key: string): void
}>()
</script>

<style scoped>
.doc-outline {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.doc-outline-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
  width: 100%;
  min-width: 0;
  border: 0;
  border-left: 2px solid transparent;
  background: transparent;
  color: #697386;
  padding: 5px 8px;
  font-size: 13px;
  line-height: 18px;
  text-align: left;
  cursor: pointer;
}

.doc-outline-item:hover {
  color: #1f2937;
  background: #f6f8fb;
}

.doc-outline-item.is-active {
  border-left-color: #409eff;
  color: #1f2937;
  background: #eef5ff;
  font-weight: 600;
}

.doc-outline-number {
  flex: 0 0 auto;
  color: #909399;
  font-variant-numeric: tabular-nums;
}

.doc-outline-item.is-active .doc-outline-number {
  color: #409eff;
}

.doc-outline-text {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

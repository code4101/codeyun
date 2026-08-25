<script setup lang="ts">
import { computed } from 'vue'

import type { RichTextOutlineItem } from './document'

const props = defineProps<{
  items: RichTextOutlineItem[]
  activeId?: string
  documentTitle?: string
  heading?: string
  emptyText?: string
}>()

const emit = defineEmits<{
  select: [id: string]
}>()

function normalizedTitle(value: string | undefined) {
  return (value ?? '').normalize('NFKC').replace(/\s+/g, '')
}

const outlineItems = computed(() => {
  const items = props.items
  const documentTitle = normalizedTitle(props.documentTitle)
  if (!items.length || !documentTitle) {
    return items
  }
  const firstItem = items[0]
  const firstIsDocumentHeading = firstItem?.level === 1
    || normalizedTitle(firstItem?.title) === documentTitle
  return firstIsDocumentHeading ? items.slice(1) : items
})
const minimumLevel = computed(() => (
  outlineItems.value.length
    ? Math.min(...outlineItems.value.map((item) => item.level))
    : 1
))
</script>

<template>
  <aside class="rich-text-outline" aria-label="大纲">
    <div class="rich-text-outline-heading">{{ heading ?? '大纲' }}</div>
    <nav v-if="outlineItems.length" class="rich-text-outline-list">
      <button
        v-for="item in outlineItems"
        :key="item.id"
        type="button"
        class="rich-text-outline-item library-reader-single-line-title"
        :class="{ active: item.id === activeId }"
        :style="{ '--outline-depth': String(Math.max(0, item.level - minimumLevel)) }"
        :title="item.title"
        @click="emit('select', item.id)"
      >
        {{ item.title }}
      </button>
    </nav>
    <div v-else class="rich-text-outline-empty">{{ emptyText ?? '没有下级标题' }}</div>
  </aside>
</template>

<style scoped>
.rich-text-outline {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 16px 12px;
  border-left: 1px solid var(--reader-border, #e4e9ef);
  background: var(--reader-panel, #fafbfc);
  overflow: hidden;
}

.rich-text-outline-heading {
  padding: 0 8px 9px;
  color: var(--reader-heading, #172033);
  font-size: 13px;
  font-weight: 700;
}

.rich-text-outline-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.rich-text-outline-item {
  display: block;
  min-width: 0;
  width: 100%;
  overflow: hidden;
  border: 0;
  border-left: 2px solid transparent;
  background: transparent;
  padding: 5px 8px 5px calc(8px + var(--outline-depth) * 12px);
  color: var(--reader-muted, #66758a);
  font-size: 12px;
  line-height: 18px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.rich-text-outline-item:hover {
  color: var(--reader-text, #344256);
  background: var(--reader-hover, #f0f3f7);
}

.rich-text-outline-item.active {
  border-left-color: var(--reader-active-text, #409eff);
  color: var(--reader-active-text, #1f5fbe);
  font-weight: 700;
}

.rich-text-outline-empty {
  padding: 8px;
  color: var(--reader-muted, #a0a9b6);
  font-size: 12px;
}
</style>

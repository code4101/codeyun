<script setup lang="ts">
import { computed } from 'vue'

import {
  renderRichTextDocument,
  type RichTextDocument,
} from './document'

const props = defineProps<{
  document: RichTextDocument | null
}>()

const emit = defineEmits<{
  'link-activate': [payload: { href: string; event: MouseEvent }]
}>()

const renderedHtml = computed(() => renderRichTextDocument(props.document))

function handleContentClick(event: MouseEvent) {
  const anchor = (event.target as HTMLElement | null)?.closest<HTMLAnchorElement>('a')
  const href = anchor?.getAttribute('href') ?? ''
  if (href) emit('link-activate', { href, event })
}
</script>

<template>
  <article
    class="rich-text-document-reader"
    :data-document-id="document?.id"
    :data-document-revision="document?.revision"
    v-html="renderedHtml"
    @click="handleContentClick"
  ></article>
</template>

<style scoped>
.rich-text-document-reader {
  box-sizing: border-box;
  color: #273447;
  font-size: 15px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.rich-text-document-reader :deep(h1),
.rich-text-document-reader :deep(h2),
.rich-text-document-reader :deep(h3),
.rich-text-document-reader :deep(h4) {
  color: #172033;
  line-height: 1.35;
}

.rich-text-document-reader :deep(h1) {
  margin: 0 0 24px;
  font-size: 26px;
}

.rich-text-document-reader :deep(h2) {
  margin: 32px 0 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid #e8ecf1;
  font-size: 20px;
}

.rich-text-document-reader :deep(h3) {
  margin: 24px 0 10px;
  font-size: 17px;
}

.rich-text-document-reader :deep(p),
.rich-text-document-reader :deep(ul),
.rich-text-document-reader :deep(ol),
.rich-text-document-reader :deep(pre),
.rich-text-document-reader :deep(blockquote),
.rich-text-document-reader :deep(figure) {
  margin: 0 0 14px;
}

.rich-text-document-reader :deep(img),
.rich-text-document-reader :deep(video) {
  max-width: 100%;
  height: auto;
}

.rich-text-document-reader :deep(code) {
  border-radius: 3px;
  background: #f1f4f7;
  padding: 1px 4px;
  color: #9a3412;
  font-family: Consolas, 'SFMono-Regular', monospace;
  font-size: 0.9em;
}

.rich-text-document-reader :deep(pre) {
  padding: 14px 16px;
  border: 1px solid #e1e7ee;
  background: #f7f9fb;
  white-space: pre-wrap;
  overflow: auto;
}

.rich-text-document-reader :deep(pre code) {
  background: transparent;
  padding: 0;
  color: #273447;
}

.rich-text-document-reader :deep(a) {
  color: #2368d1;
}

.rich-text-document-reader :deep(table) {
  display: block;
  max-width: 100%;
  border-collapse: collapse;
  overflow-x: auto;
}

.rich-text-document-reader :deep(th),
.rich-text-document-reader :deep(td) {
  padding: 7px 10px;
  border: 1px solid #dfe5ec;
  text-align: left;
}

.rich-text-document-reader :deep(blockquote) {
  padding-left: 14px;
  border-left: 3px solid #d8e0e9;
  color: #58677a;
}
</style>

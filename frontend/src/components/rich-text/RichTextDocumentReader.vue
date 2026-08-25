<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import type { LibraryAnnotation } from '@/api/libraryAnnotations'
import {
  renderRichTextDocument,
  type RichTextDocument,
  type RichTextSelection,
} from './document'

const props = withDefaults(defineProps<{
  document: RichTextDocument | null
  annotations?: LibraryAnnotation[]
  anchorText?: string
  editable?: boolean
}>(), {
  annotations: () => [],
  anchorText: '',
  editable: false,
})

const emit = defineEmits<{
  'link-activate': [payload: { href: string; event: MouseEvent }]
  'annotation-create': [payload: { selection: RichTextSelection; withComment: boolean }]
  'annotation-activate': [annotation: LibraryAnnotation]
  'annotation-delete': [annotation: LibraryAnnotation]
  'content-change': [html: string]
}>()

const rootRef = ref<HTMLElement | null>(null)
const initializedEditableDocumentId = ref('')
const selectionToolbar = ref({
  visible: false,
  left: 0,
  top: 0,
  selection: null as RichTextSelection | null,
})
const renderedHtml = computed(() => renderRichTextDocument(props.document))

function textNodes(root: HTMLElement) {
  const nodes: Text[] = []
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement
      if (!node.textContent || parent?.closest('script, style')) return NodeFilter.FILTER_REJECT
      return NodeFilter.FILTER_ACCEPT
    },
  })
  while (walker.nextNode()) nodes.push(walker.currentNode as Text)
  return nodes
}

function matchingQuoteOffset(
  source: string,
  quote: string,
  prefix: string,
  suffix: string,
) {
  let fallback = -1
  let offset = 0
  while (offset <= source.length - quote.length) {
    const index = source.indexOf(quote, offset)
    if (index < 0) break
    if (fallback < 0) fallback = index
    const prefixMatches = !prefix || source.slice(Math.max(0, index - prefix.length), index).endsWith(prefix)
    const suffixMatches = !suffix || source.slice(index + quote.length, index + quote.length + suffix.length).startsWith(suffix)
    if (prefixMatches && suffixMatches) return index
    offset = index + Math.max(1, quote.length)
  }
  return fallback
}

function wrapTextRange(root: HTMLElement, start: number, end: number, annotation: LibraryAnnotation) {
  if (end <= start) return
  const nodes = textNodes(root)
  let cursor = 0
  const pieces: Array<{ node: Text; start: number; end: number }> = []
  for (const node of nodes) {
    const nodeStart = cursor
    const nodeEnd = cursor + node.data.length
    const pieceStart = Math.max(start, nodeStart)
    const pieceEnd = Math.min(end, nodeEnd)
    if (pieceEnd > pieceStart) {
      pieces.push({
        node,
        start: pieceStart - nodeStart,
        end: pieceEnd - nodeStart,
      })
    }
    cursor = nodeEnd
  }
  for (const piece of pieces.reverse()) {
    const after = piece.node.splitText(piece.end)
    const selected = piece.node.splitText(piece.start)
    const mark = root.ownerDocument.createElement('mark')
    mark.className = `library-annotation is-${annotation.color}`
    mark.dataset.annotationId = annotation.id
    mark.title = annotation.comment_text || '高亮'
    selected.replaceWith(mark)
    mark.append(selected)
    void after
  }
}

function applyAnnotations() {
  const root = rootRef.value
  if (!root) return
  if (props.editable) {
    selectionToolbar.value.visible = false
    const documentId = props.document?.id ?? ''
    if (initializedEditableDocumentId.value !== documentId) {
      root.innerHTML = renderedHtml.value
      initializedEditableDocumentId.value = documentId
    }
    return
  }
  initializedEditableDocumentId.value = ''
  // Annotation marks are a display overlay. Rebuild from the sanitized source
  // before every pass so reactive updates never nest marks into one another.
  root.innerHTML = renderedHtml.value
  if (!props.annotations.length) return
  const pageText = root.textContent || ''
  const placements = props.annotations
    .map(annotation => ({
      annotation,
      offset: matchingQuoteOffset(
        pageText,
        annotation.quote_text,
        annotation.prefix_text,
        annotation.suffix_text,
      ),
    }))
    .filter(item => item.offset >= 0)
    .sort((left, right) => right.offset - left.offset)
  for (const item of placements) {
    wrapTextRange(
      root,
      item.offset,
      item.offset + item.annotation.quote_text.length,
      item.annotation,
    )
  }
}

function scheduleAnnotationRender() {
  void nextTick(applyAnnotations)
}

watch([renderedHtml, () => props.annotations, () => props.editable], scheduleAnnotationRender, {
  immediate: true,
  deep: true,
})

watch(() => props.editable, (editable) => {
  if (!editable) return
  void nextTick(() => rootRef.value?.focus({ preventScroll: true }))
}, { immediate: true })

function selectionOffsets(root: HTMLElement, range: Range) {
  const before = root.ownerDocument.createRange()
  before.selectNodeContents(root)
  before.setEnd(range.startContainer, range.startOffset)
  const start = before.toString().length
  return { start, end: start + range.toString().length }
}

function showSelectionToolbar(event?: MouseEvent) {
  if (props.editable) {
    selectionToolbar.value.visible = false
    return false
  }
  const root = rootRef.value
  const browserSelection = window.getSelection()
  if (!root || !browserSelection || browserSelection.rangeCount < 1 || browserSelection.isCollapsed) {
    selectionToolbar.value.visible = false
    return false
  }
  const range = browserSelection.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) {
    selectionToolbar.value.visible = false
    return false
  }
  const selectionContainer = range.commonAncestorContainer instanceof Element
    ? range.commonAncestorContainer
    : range.commonAncestorContainer.parentElement
  if (selectionContainer?.closest('.rich-text-page-footnotes')) {
    selectionToolbar.value.visible = false
    return false
  }
  const quoteText = range.toString()
  if (!quoteText.trim()) {
    selectionToolbar.value.visible = false
    return false
  }
  const pageText = root.textContent || ''
  const pageOffsets = selectionOffsets(root, range)
  const prefixText = pageText.slice(Math.max(0, pageOffsets.start - 48), pageOffsets.start)
  const suffixText = pageText.slice(pageOffsets.end, pageOffsets.end + 48)
  const sourceText = props.anchorText || pageText
  const sourceOffset = matchingQuoteOffset(sourceText, quoteText, prefixText, suffixText)
  const bounds = range.getBoundingClientRect()
  selectionToolbar.value = {
    visible: true,
    left: event?.clientX ?? bounds.left + bounds.width / 2,
    top: Math.max(8, (event?.clientY ?? bounds.top) - 42),
    selection: {
      quoteText,
      prefixText,
      suffixText,
      startOffset: Math.max(0, sourceOffset),
      endOffset: Math.max(0, sourceOffset) + quoteText.length,
    },
  }
  return true
}

function createAnnotation(withComment: boolean) {
  const selection = selectionToolbar.value.selection
  if (!selection) return
  emit('annotation-create', { selection, withComment })
  selectionToolbar.value.visible = false
  window.getSelection()?.removeAllRanges()
}

function handleContentClick(event: MouseEvent) {
  if (props.editable) {
    if ((event.target as HTMLElement | null)?.closest('a')) event.preventDefault()
    return
  }
  const annotationElement = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-annotation-id]')
  if (annotationElement) {
    const annotation = props.annotations.find(item => item.id === annotationElement.dataset.annotationId)
    if (annotation) {
      emit('annotation-activate', annotation)
      return
    }
  }
  const anchor = (event.target as HTMLElement | null)?.closest<HTMLAnchorElement>('a')
  const href = anchor?.getAttribute('href') ?? ''
  if (href) emit('link-activate', { href, event })
}

function handleContextMenu(event: MouseEvent) {
  if (props.editable) return
  const annotationElement = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-annotation-id]')
  if (annotationElement) {
    const annotation = props.annotations.find(item => item.id === annotationElement.dataset.annotationId)
    if (annotation) {
      event.preventDefault()
      emit('annotation-delete', annotation)
      return
    }
  }
  if (props.document?.capabilities.canAnnotate) {
    if (showSelectionToolbar(event)) event.preventDefault()
  }
}

function handleContentInput() {
  if (!props.editable || !rootRef.value) return
  emit('content-change', rootRef.value.innerHTML)
}
</script>

<template>
  <div class="rich-text-reader-shell">
    <article
      v-if="editable"
      ref="rootRef"
      class="rich-text-document-reader is-editable"
      :data-document-id="document?.id"
      :data-document-revision="document?.revision"
      contenteditable="true"
      role="textbox"
      aria-label="编辑正文"
      aria-multiline="true"
      spellcheck="true"
      @click="handleContentClick"
      @input="handleContentInput"
    ></article>
    <article
      v-else
      ref="rootRef"
      class="rich-text-document-reader"
      :data-document-id="document?.id"
      :data-document-revision="document?.revision"
      v-html="renderedHtml"
      @click="handleContentClick"
      @contextmenu="handleContextMenu"
      @mouseup="showSelectionToolbar()"
      @keyup="showSelectionToolbar()"
    ></article>
    <div
      v-if="selectionToolbar.visible && document?.capabilities.canAnnotate"
      class="rich-text-selection-toolbar"
      :style="{ left: `${selectionToolbar.left}px`, top: `${selectionToolbar.top}px` }"
      role="toolbar"
      aria-label="文本批注"
      @mousedown.prevent
    >
      <button type="button" @click="createAnnotation(false)">高亮</button>
      <button type="button" @click="createAnnotation(true)">批注</button>
    </div>
  </div>
</template>

<style scoped>
.rich-text-reader-shell {
  display: contents;
}

.rich-text-document-reader {
  box-sizing: border-box;
  color: var(--reader-text, #273447);
  font-size: 15px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.rich-text-selection-toolbar {
  position: fixed;
  z-index: 4000;
  display: inline-flex;
  transform: translateX(-50%);
  overflow: hidden;
  border: 1px solid var(--reader-border, #d7dde5);
  border-radius: 5px;
  background: var(--reader-surface, #fff);
  box-shadow: 0 5px 16px rgb(31 45 61 / 18%);
}

.rich-text-selection-toolbar button {
  border: 0;
  border-right: 1px solid var(--reader-border, #e5e9ef);
  background: transparent;
  padding: 7px 11px;
  color: var(--reader-text, #344256);
  cursor: pointer;
}

.rich-text-selection-toolbar button:last-child {
  border-right: 0;
}

.rich-text-selection-toolbar button:hover {
  background: var(--reader-hover, #f1f5f9);
  color: var(--reader-active-text, #1f5fbe);
}

.rich-text-document-reader :deep(mark.library-annotation) {
  border-radius: 2px;
  color: inherit;
  cursor: pointer;
}

.rich-text-document-reader :deep(mark.library-annotation.is-yellow) { background: #fff1a8; }
.rich-text-document-reader :deep(mark.library-annotation.is-green) { background: #ccefd8; }
.rich-text-document-reader :deep(mark.library-annotation.is-blue) { background: #cfe5ff; }
.rich-text-document-reader :deep(mark.library-annotation.is-pink) { background: #f8d4e1; }

.rich-text-document-reader :deep(h1),
.rich-text-document-reader :deep(h2),
.rich-text-document-reader :deep(h3),
.rich-text-document-reader :deep(h4) {
  color: var(--reader-heading, #172033);
  line-height: 1.35;
}

.rich-text-document-reader :deep(h1) { margin: 0 0 24px; font-size: 26px; }
.rich-text-document-reader :deep(h2) { margin: 32px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--reader-border, #e8ecf1); font-size: 20px; }
.rich-text-document-reader :deep(h3) { margin: 24px 0 10px; font-size: 17px; }

.rich-text-document-reader :deep(p),
.rich-text-document-reader :deep(ul),
.rich-text-document-reader :deep(ol),
.rich-text-document-reader :deep(pre),
.rich-text-document-reader :deep(blockquote),
.rich-text-document-reader :deep(figure) { margin: 0 0 14px; }

.rich-text-document-reader :deep(img),
.rich-text-document-reader :deep(video) { max-width: 100%; height: auto; }
.rich-text-document-reader :deep(.imported-book-image) { margin: 0 0 14px; line-height: 0; }
.rich-text-document-reader :deep(.imported-book-image-link) { display: inline-block; max-width: 100%; line-height: 0; }
.rich-text-document-reader :deep(.imported-book-image img) { display: block; margin: 0; }
.rich-text-document-reader :deep(code) { border-radius: 3px; background: var(--reader-code, #f1f4f7); padding: 1px 4px; color: var(--reader-code-text, #9a3412); font-family: Consolas, 'SFMono-Regular', monospace; font-size: 0.9em; }
.rich-text-document-reader :deep(pre) { padding: 14px 16px; border: 1px solid var(--reader-border, #e1e7ee); background: var(--reader-panel, #f7f9fb); white-space: pre-wrap; overflow: auto; }
.rich-text-document-reader :deep(pre code) { background: transparent; padding: 0; color: var(--reader-text, #273447); }
.rich-text-document-reader :deep(a) { color: var(--reader-link, #2368d1); }
.rich-text-document-reader :deep(.rich-text-footnote-ref) {
  color: var(--reader-link, #315f9d);
  font-size: 0.72em;
  line-height: 0;
  text-decoration: none;
  vertical-align: super;
}

.rich-text-document-reader.is-editable {
  cursor: text;
  caret-color: var(--reader-active-text, #1f5fbe);
}

.rich-text-document-reader.is-editable:focus {
  outline: none;
}
.rich-text-document-reader :deep(.rich-text-footnote-ref.is-unresolved) {
  color: var(--reader-muted, #8a96a8);
  cursor: help;
}
.rich-text-document-reader :deep(.rich-text-page-footnotes) {
  margin-top: 28px;
  border-top: 1px solid var(--reader-border, #dfe5ec);
  padding-top: 12px;
  color: var(--reader-muted, #58677a);
  font-size: 0.84em;
  line-height: 1.65;
}
.rich-text-document-reader :deep(.rich-text-page-footnotes ol) {
  margin: 0;
  padding: 0;
  list-style: none;
}
.rich-text-document-reader :deep(.rich-text-page-footnotes li) {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 0.55em;
  margin: 0 0 8px;
  scroll-margin-top: 18px;
}
.rich-text-document-reader :deep(.rich-text-page-footnotes li:last-child) { margin-bottom: 0; }
.rich-text-document-reader :deep(.rich-text-footnote-backref),
.rich-text-document-reader :deep(.rich-text-footnote-label) {
  color: var(--reader-link, #315f9d);
  text-decoration: none;
  white-space: nowrap;
}
.rich-text-document-reader :deep(.rich-text-footnote-content > :last-child) { margin-bottom: 0; }
.rich-text-document-reader :deep(table) { display: block; max-width: 100%; border-collapse: collapse; overflow-x: auto; }
.rich-text-document-reader :deep(th),
.rich-text-document-reader :deep(td) { padding: 7px 10px; border: 1px solid var(--reader-border, #dfe5ec); text-align: left; }
.rich-text-document-reader :deep(blockquote) { padding-left: 14px; border-left: 3px solid var(--reader-border, #d8e0e9); color: var(--reader-muted, #58677a); }
.rich-text-document-reader :deep(.katex-display) { margin: 20px 0; overflow-x: auto; overflow-y: hidden; padding: 4px 0; }
.rich-text-document-reader :deep(.katex) { color: var(--reader-heading, #172033); font-size: 1.05em; }
</style>

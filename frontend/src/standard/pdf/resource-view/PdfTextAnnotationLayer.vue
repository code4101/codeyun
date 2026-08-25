<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  TextLayer,
  type PageViewport,
  type TextContent,
} from 'pdfjs-dist'

import {
  createLibraryAnnotation,
  deleteLibraryAnnotation,
  fetchLibraryAnnotations,
  updateLibraryAnnotation,
  type LibraryAnnotation,
} from '@/api/libraryAnnotations'

const props = defineProps<{
  pdfId: number
  pageNumber: number
  sourceRevision: string
  textContent: TextContent | null
  viewport: PageViewport | null
}>()

const layerRef = ref<HTMLElement | null>(null)
const annotations = ref<LibraryAnnotation[]>([])
const selectionToolbar = ref({
  visible: false,
  left: 0,
  top: 0,
  quoteText: '',
  prefixText: '',
  suffixText: '',
  startOffset: 0,
  endOffset: 0,
})
let textLayer: TextLayer | null = null
let renderSequence = 0

function textNodes(root: HTMLElement) {
  const nodes: Text[] = []
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) {
    const node = walker.currentNode as Text
    if (node.data) nodes.push(node)
  }
  return nodes
}

function quoteOffset(source: string, annotation: LibraryAnnotation) {
  let fallback = -1
  let cursor = 0
  while (cursor <= source.length - annotation.quote_text.length) {
    const index = source.indexOf(annotation.quote_text, cursor)
    if (index < 0) break
    if (fallback < 0) fallback = index
    const prefixMatches = !annotation.prefix_text
      || source.slice(Math.max(0, index - annotation.prefix_text.length), index).endsWith(annotation.prefix_text)
    const suffixMatches = !annotation.suffix_text
      || source.slice(
        index + annotation.quote_text.length,
        index + annotation.quote_text.length + annotation.suffix_text.length,
      ).startsWith(annotation.suffix_text)
    if (prefixMatches && suffixMatches) return index
    cursor = index + Math.max(1, annotation.quote_text.length)
  }
  return fallback
}

function wrapTextRange(root: HTMLElement, start: number, end: number, annotation: LibraryAnnotation) {
  const pieces: Array<{ node: Text; start: number; end: number }> = []
  let cursor = 0
  for (const node of textNodes(root)) {
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
    piece.node.splitText(piece.end)
    const selected = piece.node.splitText(piece.start)
    const mark = root.ownerDocument.createElement('mark')
    mark.className = `pdf-library-annotation is-${annotation.color}`
    mark.dataset.annotationId = annotation.id
    mark.title = annotation.comment_text || '高亮'
    selected.replaceWith(mark)
    mark.append(selected)
  }
}

function applyAnnotations() {
  const root = layerRef.value
  if (!root) return
  const text = root.textContent || ''
  const placements = annotations.value
    .map(annotation => ({ annotation, offset: quoteOffset(text, annotation) }))
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

async function renderLayer() {
  const root = layerRef.value
  const content = props.textContent
  const viewport = props.viewport
  const sequence = ++renderSequence
  textLayer?.cancel()
  textLayer = null
  if (!root) return
  root.replaceChildren()
  if (!content || !viewport || !content.items.length) return
  const nextLayer = new TextLayer({
    textContentSource: content,
    container: root,
    viewport,
  })
  textLayer = nextLayer
  await nextLayer.render()
  if (sequence !== renderSequence) return
  applyAnnotations()
}

async function loadAnnotations() {
  if (!props.pdfId || !props.pageNumber) return
  try {
    annotations.value = await fetchLibraryAnnotations(
      'pdf',
      String(props.pdfId),
      `page:${props.pageNumber}`,
    )
  } catch (error) {
    console.warn('Failed to load PDF annotations:', error)
    annotations.value = []
  }
  await nextTick()
  await renderLayer()
}

watch(
  () => [props.pdfId, props.pageNumber, props.textContent, props.viewport] as const,
  () => void loadAnnotations(),
  { immediate: true },
)

function rangeOffset(root: HTMLElement, range: Range) {
  const before = root.ownerDocument.createRange()
  before.selectNodeContents(root)
  before.setEnd(range.startContainer, range.startOffset)
  return before.toString().length
}

function showSelectionToolbar(event?: MouseEvent) {
  const root = layerRef.value
  const selection = window.getSelection()
  if (!root || !selection || selection.rangeCount < 1 || selection.isCollapsed) {
    selectionToolbar.value.visible = false
    return false
  }
  const range = selection.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) {
    selectionToolbar.value.visible = false
    return false
  }
  const quoteText = range.toString()
  if (!quoteText.trim()) return false
  const source = root.textContent || ''
  const startOffset = rangeOffset(root, range)
  const endOffset = startOffset + quoteText.length
  const bounds = range.getBoundingClientRect()
  selectionToolbar.value = {
    visible: true,
    left: event?.clientX ?? bounds.left + bounds.width / 2,
    top: Math.max(8, (event?.clientY ?? bounds.top) - 42),
    quoteText,
    prefixText: source.slice(Math.max(0, startOffset - 48), startOffset),
    suffixText: source.slice(endOffset, endOffset + 48),
    startOffset,
    endOffset,
  }
  return true
}

async function createAnnotation(withComment: boolean) {
  let commentText = ''
  if (withComment) {
    try {
      const result = await ElMessageBox.prompt('写下批注', '添加批注', {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputType: 'textarea',
      })
      commentText = result.value.trim()
    } catch {
      return
    }
  }
  try {
    const created = await createLibraryAnnotation({
      resource_type: 'pdf',
      resource_id: String(props.pdfId),
      chapter_id: `page:${props.pageNumber}`,
      kind: commentText ? 'comment' : 'highlight',
      color: 'yellow',
      quote_text: selectionToolbar.value.quoteText,
      prefix_text: selectionToolbar.value.prefixText,
      suffix_text: selectionToolbar.value.suffixText,
      start_offset: selectionToolbar.value.startOffset,
      end_offset: selectionToolbar.value.endOffset,
      source_revision: props.sourceRevision,
      comment_text: commentText,
    })
    annotations.value = [...annotations.value, created]
    selectionToolbar.value.visible = false
    window.getSelection()?.removeAllRanges()
    await renderLayer()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '批注保存失败')
  }
}

async function editAnnotation(annotation: LibraryAnnotation) {
  try {
    const result = await ElMessageBox.prompt('批注内容（留空则保留为纯高亮）', '编辑批注', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValue: annotation.comment_text,
    })
    const updated = await updateLibraryAnnotation(annotation.id, {
      comment_text: result.value,
    })
    annotations.value = annotations.value.map(item => item.id === updated.id ? updated : item)
    await renderLayer()
  } catch {
    // 用户取消编辑时保持原批注。
  }
}

async function removeAnnotation(annotation: LibraryAnnotation) {
  try {
    await ElMessageBox.confirm(
      annotation.comment_text ? '删除这条高亮和批注？' : '删除这处高亮？',
      '删除批注',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteLibraryAnnotation(annotation.id)
    annotations.value = annotations.value.filter(item => item.id !== annotation.id)
    await renderLayer()
  } catch {
    // 用户取消删除时保持原批注。
  }
}

function annotationAt(event: MouseEvent) {
  const element = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-annotation-id]')
  return annotations.value.find(item => item.id === element?.dataset.annotationId)
}

function handleClick(event: MouseEvent) {
  const annotation = annotationAt(event)
  if (annotation) void editAnnotation(annotation)
}

function handleContextMenu(event: MouseEvent) {
  const annotation = annotationAt(event)
  if (annotation) {
    event.preventDefault()
    void removeAnnotation(annotation)
    return
  }
  if (showSelectionToolbar(event)) event.preventDefault()
}

onBeforeUnmount(() => {
  renderSequence += 1
  textLayer?.cancel()
  textLayer = null
})
</script>

<template>
  <div
    ref="layerRef"
    class="pdf-text-annotation-layer textLayer"
    @click="handleClick"
    @contextmenu="handleContextMenu"
    @mouseup="showSelectionToolbar()"
    @keyup="showSelectionToolbar()"
  ></div>
  <div
    v-if="selectionToolbar.visible"
    class="pdf-selection-toolbar"
    :style="{ left: `${selectionToolbar.left}px`, top: `${selectionToolbar.top}px` }"
    role="toolbar"
    aria-label="PDF 文本批注"
    @mousedown.prevent
  >
    <button type="button" @click="createAnnotation(false)">高亮</button>
    <button type="button" @click="createAnnotation(true)">批注</button>
  </div>
</template>

<style scoped>
.pdf-text-annotation-layer {
  --min-font-size: 1;
  --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
  --min-font-size-inv: calc(1 / var(--min-font-size));
  position: absolute;
  z-index: 1;
  inset: 0;
  overflow: clip;
  line-height: 1;
  text-align: initial;
  transform-origin: 0 0;
}

.pdf-text-annotation-layer :deep(span),
.pdf-text-annotation-layer :deep(br) {
  position: absolute;
  color: transparent;
  white-space: pre;
  cursor: text;
  transform-origin: 0 0;
}

.pdf-text-annotation-layer :deep(> :not(.markedContent)),
.pdf-text-annotation-layer :deep(.markedContent span:not(.markedContent)) {
  --font-height: 0;
  --scale-x: 1;
  --rotate: 0deg;
  z-index: 1;
  font-size: calc(var(--text-scale-factor) * var(--font-height));
  transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}

.pdf-text-annotation-layer :deep(.markedContent) { display: contents; }
.pdf-text-annotation-layer :deep(::selection) { background: rgb(37 99 235 / 28%); }
.pdf-text-annotation-layer :deep(mark.pdf-library-annotation) { border-radius: 2px; color: transparent; cursor: pointer; }
.pdf-text-annotation-layer :deep(mark.pdf-library-annotation.is-yellow) { background: rgb(250 204 21 / 42%); }
.pdf-text-annotation-layer :deep(mark.pdf-library-annotation.is-green) { background: rgb(34 197 94 / 32%); }
.pdf-text-annotation-layer :deep(mark.pdf-library-annotation.is-blue) { background: rgb(59 130 246 / 30%); }
.pdf-text-annotation-layer :deep(mark.pdf-library-annotation.is-pink) { background: rgb(236 72 153 / 28%); }

.pdf-selection-toolbar {
  position: fixed;
  z-index: 4000;
  display: inline-flex;
  transform: translateX(-50%);
  overflow: hidden;
  border: 1px solid #d7dde5;
  border-radius: 5px;
  background: #fff;
  box-shadow: 0 5px 16px rgb(31 45 61 / 18%);
}

.pdf-selection-toolbar button {
  border: 0;
  border-right: 1px solid #e5e9ef;
  background: transparent;
  padding: 7px 11px;
  color: #344256;
  cursor: pointer;
}

.pdf-selection-toolbar button:last-child { border-right: 0; }
.pdf-selection-toolbar button:hover { background: #f1f5f9; color: #1f5fbe; }
</style>

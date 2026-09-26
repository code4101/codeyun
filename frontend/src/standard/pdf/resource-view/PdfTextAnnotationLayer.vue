<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  TextLayer,
  type PageViewport,
} from 'pdfjs-dist'
import type { TextContent } from 'pdfjs-dist/types/src/display/api'
import { getPdfPageOcr, type PdfPageOcr } from '@/api/pdfDocuments'
import { renderOcrSelection, formatOcrSelection } from './ocrTextLayer'

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
const emit = defineEmits<{ 'text-ready': [root: HTMLElement | null] }>()

const layerRef = ref<HTMLElement | null>(null)
const selectionRects = ref<Array<{ x: number; y: number; width: number; height: number }>>([])

// Paint opaque rectangles in one translucent SVG group. Applying alpha to each
// character separately darkens overlapping OCR boxes into vertical stripes.
function updateSelectionPaint() {
  const root = layerRef.value
  const selection = window.getSelection()
  selectionRects.value = []
  if (!root || !selection?.rangeCount || selection.isCollapsed) return
  const range = selection.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return
  const bounds = root.getBoundingClientRect()
  selectionRects.value = Array.from(range.getClientRects())
    .filter(rect => rect.width > 0 && rect.height > 0)
    .map(rect => ({ x: rect.left - bounds.left, y: rect.top - bounds.top,
      width: rect.width, height: rect.height }))
}

onMounted(() => {
  document.addEventListener('selectionchange', updateSelectionPaint)
  document.addEventListener('copy', handleCopy)
})
const annotations = ref<LibraryAnnotation[]>([])
const selectionToolbar = ref({
  visible: false,
  left: 0,
  top: 0,
  quoteText: '',
  clipboardText: '',
  prefixText: '',
  suffixText: '',
  startOffset: 0,
  endOffset: 0,
})
let textLayer: TextLayer | null = null
let renderSequence = 0
let annotationSequence = 0
let ocrController: AbortController | null = null
let ocrResult: PdfPageOcr | null = null
let ocrKey = ''
const ocrStatus = ref<'idle' | 'loading' | 'error'>('idle')

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
  for (const mark of root.querySelectorAll('mark.pdf-library-annotation')) {
    mark.replaceWith(...mark.childNodes)
  }
  root.normalize()
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
  emit('text-ready', null)
  const root = layerRef.value
  const content = props.textContent
  const viewport = props.viewport
  const sequence = ++renderSequence
  textLayer?.cancel()
  textLayer = null
  selectionToolbar.value.visible = false
  selectionRects.value = []
  if (!root) return
  root.replaceChildren()
  if (!content || !viewport) return
  const hasNativeText = content.items.some(item => 'str' in item && item.str.trim())
  if (!hasNativeText) {
    const key = `${props.pdfId}:${props.sourceRevision}:${props.pageNumber}`
    ocrController?.abort()
    const controller = new AbortController()
    ocrController = controller
    ocrStatus.value = 'loading'
    try {
      const result = key === ocrKey && ocrResult ? ocrResult : await getPdfPageOcr(props.pdfId, props.pageNumber, controller.signal)
      if (sequence !== renderSequence) return
      ocrKey = key
      ocrResult = result
      renderOcrSelection(root, result, viewport)
      ocrStatus.value = 'idle'
      applyAnnotations()
      emit('text-ready', root)
    } catch (error) {
      if (sequence === renderSequence && !controller.signal.aborted) ocrStatus.value = 'error'
    }
    return
  }
  ocrController?.abort()
  ocrStatus.value = 'idle'
  const nextLayer = new TextLayer({
    textContentSource: content,
    container: root,
    viewport,
  })
  textLayer = nextLayer
  await nextLayer.render()
  if (sequence !== renderSequence) return
  applyAnnotations()
  emit('text-ready', root)
}

async function loadAnnotations() {
  if (!props.pdfId || !props.pageNumber) return
  const sequence = ++annotationSequence
  annotations.value = []
  // Selection must not wait for the annotations request.
  await nextTick()
  if (sequence !== annotationSequence) return
  void renderLayer()
  try {
    const result = await fetchLibraryAnnotations(
      'pdf',
      String(props.pdfId),
      `page:${props.pageNumber}`,
    )
    if (sequence !== annotationSequence) return
    annotations.value = result
  } catch (error) {
    console.warn('Failed to load PDF annotations:', error)
    if (sequence !== annotationSequence) return
    annotations.value = []
  }
  // renderLayer applies these after OCR; native text may have finished already.
  if (layerRef.value?.childNodes.length) {
    applyAnnotations()
    emit('text-ready', layerRef.value)
  }
}

watch(
  () => [props.pdfId, props.pageNumber, props.sourceRevision, props.textContent, props.viewport] as const,
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
  const clipboardText = ocrResult && root.querySelector('.ocr-selectable-text')
    ? formatOcrSelection(root, startOffset, endOffset, ocrResult) : quoteText
  const bounds = range.getBoundingClientRect()
  selectionToolbar.value = {
    visible: true,
    left: event?.clientX ?? bounds.left + bounds.width / 2,
    top: Math.max(8, (event?.clientY ?? bounds.top) - 42),
    quoteText,
    clipboardText,
    prefixText: source.slice(Math.max(0, startOffset - 48), startOffset),
    suffixText: source.slice(endOffset, endOffset + 48),
    startOffset,
    endOffset,
  }
  return true
}

function handleCopy(event: ClipboardEvent) {
  const root = layerRef.value
  const selection = window.getSelection()
  if (!root || !ocrResult || !root.querySelector('.ocr-selectable-text') || !selection?.rangeCount || selection.isCollapsed || !event.clipboardData) return
  const range = selection.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return
  const start = rangeOffset(root, range)
  event.clipboardData.setData('text/plain', formatOcrSelection(root, start, start + range.toString().length, ocrResult))
  event.preventDefault()
}

async function copySelection() {
  try {
    await navigator.clipboard.writeText(selectionToolbar.value.clipboardText)
    selectionToolbar.value.visible = false
  } catch {
    ElMessage.warning('请使用 Ctrl+C 复制选中文字')
  }
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
  emit('text-ready', null)
  document.removeEventListener('selectionchange', updateSelectionPaint)
  document.removeEventListener('copy', handleCopy)
  renderSequence += 1
  annotationSequence += 1
  ocrController?.abort()
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
  <svg v-if="selectionRects.length" class="pdf-selection-paint" aria-hidden="true">
    <rect v-for="(rect, index) in selectionRects" :key="index"
      :x="rect.x" :y="rect.y" :width="rect.width" :height="rect.height" />
  </svg>
  <div v-if="ocrStatus !== 'idle'" class="pdf-ocr-status">
    <span v-if="ocrStatus === 'loading'">正在准备可选文字…</span>
    <button v-else type="button" @click="renderLayer">文字识别失败，重试</button>
  </div>
  <div
    v-if="selectionToolbar.visible"
    class="pdf-selection-toolbar"
    :style="{ left: `${selectionToolbar.left}px`, top: `${selectionToolbar.top}px` }"
    role="toolbar"
    aria-label="PDF 文本批注"
    @mousedown.prevent
  >
    <button type="button" @click="copySelection">复制</button>
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
  user-select: text;
  -webkit-user-select: text;
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
.pdf-text-annotation-layer :deep(::selection) { background: transparent; }
.pdf-selection-paint { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 1; pointer-events: none; fill: #2563eb; opacity: .28; }
.pdf-text-annotation-layer :deep(.ocr-selectable-text::selection) { color: transparent; }
.pdf-ocr-status { position: absolute; top: 12px; right: 12px; z-index: 2; padding: 6px 10px; border-radius: 6px; background: #fffffff0; color: #64748b; font-size: 12px; }
.pdf-ocr-status button { background: none; border: 0; color: #2563eb; cursor: pointer; }
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

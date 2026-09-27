<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import type { PDFDocumentProxy, RenderTask, PageViewport } from 'pdfjs-dist'
import type { TextContent } from 'pdfjs-dist/types/src/display/api'
import PdfPageCrop from './PdfPageCrop.vue'
import type { PageCrop } from './pdfPageCrop'
import PdfTextAnnotationLayer from './PdfTextAnnotationLayer.vue'
const props = defineProps<{ document: PDFDocumentProxy; page: number; pdfId: number; revision: string; width: number; height: number; zoom: string; active: boolean; cropEnabled: boolean }>()
const emit = defineEmits<{ crop: [crop: PageCrop]; size: [size: { width: number; height: number }]; 'text-ready': [root: HTMLElement | null]; scale: [percent: number] }>()
const canvas = ref<HTMLCanvasElement>()
const viewport = shallowRef<PageViewport | null>(null)
const text = shallowRef<TextContent | null>(null)
const error = ref('')
const ready = ref(false)
const selectionRoot = shallowRef<HTMLElement | null>(null)
function textReady(root: HTMLElement | null) {
  selectionRoot.value = root
  if (props.active) emit('text-ready', root)
}
// Changing the current page changes search ownership, not the lifetime of its text layer.
watch(() => props.active, active => {
  if (active) {
    emit('text-ready', selectionRoot.value)
    if (viewport.value) emit('scale', Math.round(viewport.value.scale / (96 / 72) * 100))
  }
})
const retry = ref(0)
let version = 0
let task: RenderTask | undefined
watch(() => [props.document, props.page, props.width, props.height, props.zoom, retry.value], async () => {
  const generation = ++version
  const previous = task
  previous?.cancel()
  if (previous) await previous.promise.catch(() => undefined)
  if (generation !== version) return
  ready.value = false; error.value = ''; text.value = null; viewport.value = null
  try {
    const page = await props.document.getPage(props.page)
    if (generation !== version) return
    const base = page.getViewport({ scale: 1 })
    emit('size', { width: base.width, height: base.height })
    const scale = /^\d+$/.test(props.zoom) ? Math.max(.25, Math.min(4, Number(props.zoom) / 100)) * 96 / 72
      : Math.max(1 / 3, Math.min(props.width / base.width, props.zoom === 'page-fit' ? props.height / base.height : Infinity, 16 / 3))
    const view = page.getViewport({ scale })
    await nextTick()
    if (generation !== version || !canvas.value) return
    const outputScale = Math.min(devicePixelRatio || 1, 2)
    canvas.value.width = Math.floor(view.width * outputScale)
    canvas.value.height = Math.floor(view.height * outputScale)
    canvas.value.style.width = `${view.width}px`; canvas.value.style.height = `${view.height}px`
    const context = canvas.value.getContext('2d')!
    task = page.render({ canvas: canvas.value, canvasContext: context, viewport: view, transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0] })
    await task.promise
    if (generation !== version) return
    text.value = await page.getTextContent()
    if (generation !== version) return
    viewport.value = view; ready.value = true
    if (props.active) emit('scale', Math.round(scale / (96 / 72) * 100))
  } catch (reason) {
    if (generation === version && (reason as Error).name !== 'RenderingCancelledException') error.value = '页面加载失败'
  }
}, { immediate: true, flush: 'post' })
onBeforeUnmount(() => { version++; task?.cancel(); if (canvas.value) { canvas.value.width = 0; canvas.value.height = 0 } })
</script>
<template>
  <PdfPageCrop :enabled="cropEnabled" :revision="viewport" @crop="emit('crop', $event)">
  <canvas ref="canvas" class="continuous-canvas" :class="{ pending: !ready }" />
  <PdfTextAnnotationLayer v-if="ready" :pdf-id="pdfId" :page-number="page" :source-revision="revision" :text-content="text" :viewport="viewport" @text-ready="textReady" />
  <div v-if="error" class="page-status">{{ error }} <button @click="retry++">重试</button></div>
  <div v-else-if="!ready" class="page-status">第 {{ page }} 页加载中…</div>
  </PdfPageCrop>
</template>
<style scoped>
.continuous-canvas { display: block; filter: var(--preview-page-filter); }
.pending { visibility: hidden; }
.page-status { position: absolute; top: 16px; left: 16px; color: var(--reader-muted); font-size: 12px; }
</style>

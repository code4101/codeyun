<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { FULL_PAGE, type PageCrop } from './pdfPageCrop'
import PdfContinuousPage from './PdfContinuousPage.vue'
const props = defineProps<{ document: PDFDocumentProxy; pdfId: number; revision: string; start: number; end: number; page: number; zoom: string; cropEnabled: boolean }>()
const emit = defineEmits<{ page: [page: number]; 'text-ready': [root: HTMLElement | null]; scale: [percent: number] }>()
const root = ref<HTMLElement>()
const near = ref(new Set<number>())
const crops = ref<Record<number, PageCrop>>({})
const sizes = ref<Record<number, { width: number; height: number }>>({})
const available = ref({ width: 700, height: 700 })
const pages = computed(() => Array.from({ length: props.end - props.start + 1 }, (_, index) => props.start + index))
let observer: IntersectionObserver | undefined
let resize: ResizeObserver | undefined
let frame = 0
let mounted = false
let sizingAnchor: { element: HTMLElement; top: number } | undefined
function setSize(page: number, size: { width: number; height: number }) {
  // Learning a scanned page's actual proportions must not move the text being read.
  const element = root.value?.querySelector<HTMLElement>(`[data-page="${props.page}"]`)
  const restore = !sizingAnchor && Boolean(element)
  if (restore && element) {
    sizingAnchor = { element, top: element.getBoundingClientRect().top }
  }
  sizes.value[page] = size
  if (restore) {
    void nextTick(() => {
      if (sizingAnchor && root.value && sizingAnchor.element.isConnected) root.value.scrollTop += sizingAnchor.element.getBoundingClientRect().top - sizingAnchor.top
      sizingAnchor = undefined
    })
  }
}
function style(page: number) {
  const size = sizes.value[page] ?? { width: 612, height: 792 }
  const scale = /^\d+$/.test(props.zoom) ? Math.max(.25, Math.min(4, Number(props.zoom) / 100)) * 96 / 72
    : Math.max(1 / 3, Math.min(available.value.width / size.width, props.zoom === 'page-fit' ? available.value.height / size.height : Infinity, 16 / 3))
  const crop = props.cropEnabled ? crops.value[page] ?? FULL_PAGE : FULL_PAGE
  return { width: `${size.width * scale * (1 - crop.left - crop.right)}px`, height: `${size.height * scale * (1 - crop.top - crop.bottom)}px` }
}
function scrollToPage(page: number) {
  const element = root.value?.querySelector<HTMLElement>(`[data-page="${page}"]`)
  if (element && root.value) root.value.scrollTop += element.getBoundingClientRect().top - root.value.getBoundingClientRect().top - 12
}
function scroll() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => {
    if (!root.value) return
    const top = root.value.getBoundingClientRect().top + Math.min(100, root.value.clientHeight / 4)
    const elements = [...root.value.querySelectorAll<HTMLElement>('[data-page]')]
    const current = elements.find(element => element.getBoundingClientRect().bottom > top)
    if (current) emit('page', Number(current.dataset.page))
  })
}
async function observePages() {
  if (!mounted) return
  observer?.disconnect(); near.value = new Set()
  await nextTick()
  observer = new IntersectionObserver(entries => {
    const next = new Set(near.value)
    for (const entry of entries) { const page = Number((entry.target as HTMLElement).dataset.page); if (entry.isIntersecting) next.add(page); else next.delete(page) }
    near.value = next
  }, { root: root.value, rootMargin: '800px 0px' })
  root.value?.querySelectorAll('[data-page]').forEach(element => observer!.observe(element))
  scrollToPage(Math.max(props.start, Math.min(props.end, props.page)))
}
watch(() => [props.start, props.end, props.document], observePages)
watch(() => [props.zoom, props.cropEnabled], async () => { const page = props.page; await nextTick(); scrollToPage(page) })
onMounted(() => {
  mounted = true
  resize = new ResizeObserver(() => {
    if (!root.value) return
    const page = props.page
    available.value = { width: Math.max(240, root.value.clientWidth - 48), height: Math.max(240, root.value.clientHeight - 24) }
    void nextTick(() => scrollToPage(page))
  })
  resize.observe(root.value!); void observePages()
})
onBeforeUnmount(() => { observer?.disconnect(); resize?.disconnect(); cancelAnimationFrame(frame) })
defineExpose({ scrollToPage })
</script>
<template>
  <div ref="root" class="pdf-continuous-scroll" @scroll.passive="scroll">
    <section v-for="number in pages" :key="number" :data-page="number" class="continuous-page" :style="style(number)" :aria-label="`第 ${number} 页`">
      <PdfContinuousPage v-if="near.has(number)" :document="document" :page="number" :pdf-id="pdfId" :revision="revision" :width="available.width" :height="available.height" :zoom="zoom" :active="page === number" :crop-enabled="cropEnabled"
        @crop="crops[number] = $event" @size="setSize(number, $event)" @text-ready="page === number && emit('text-ready', $event)" @scale="emit('scale', $event)" />
    </section>
  </div>
</template>
<style scoped>
.pdf-continuous-scroll { flex: 1; min-height: 0; overflow: auto; padding: 12px 24px; overflow-anchor: auto; }
.continuous-page { position: relative; margin: 0 auto 20px; background: var(--reader-content); box-shadow: 0 4px 18px #0002; }
</style>

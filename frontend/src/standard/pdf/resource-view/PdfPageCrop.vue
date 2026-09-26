<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { detectPageCrop, FULL_PAGE, type PageCrop } from './pdfPageCrop'
const props = defineProps<{ enabled: boolean; revision?: unknown }>()
const emit = defineEmits<{ crop: [crop: PageCrop] }>()
const layer = ref<HTMLElement>()
const size = ref({ width: 0, height: 0 })
const crop = ref<PageCrop>({ ...FULL_PAGE })
let observer: ResizeObserver | undefined
let generation = 0
async function measure() {
  const version = ++generation
  await nextTick()
  if (version !== generation) return
  const source = layer.value?.querySelector<HTMLCanvasElement | HTMLImageElement>('canvas:not([style*="display: none"]), img')
  let next = { ...FULL_PAGE }
  if (props.enabled && source) {
    const width = source instanceof HTMLCanvasElement ? source.width : source.naturalWidth
    const height = source instanceof HTMLCanvasElement ? source.height : source.naturalHeight
    size.value = { width: source.clientWidth, height: source.clientHeight }
    if (width && height) {
      const scale = Math.min(1, 800 / width, 1100 / height)
      const sample = document.createElement('canvas')
      sample.width = Math.max(1, Math.round(width * scale)); sample.height = Math.max(1, Math.round(height * scale))
      try {
        const context = sample.getContext('2d', { willReadFrequently: true })!
        context.drawImage(source, 0, 0, sample.width, sample.height)
        next = detectPageCrop(context.getImageData(0, 0, sample.width, sample.height).data, sample.width, sample.height)
      } catch { /* Unreadable images keep their full page. */ }
    }
  }
  crop.value = next; emit('crop', next)
}
const outer = computed(() => props.enabled && size.value.width ? {
  width: `${size.value.width * (1 - crop.value.left - crop.value.right)}px`,
  height: `${size.value.height * (1 - crop.value.top - crop.value.bottom)}px`, overflow: 'hidden',
} : {})
const inner = computed(() => props.enabled && size.value.width ? {
  width: `${size.value.width}px`, height: `${size.value.height}px`,
  transform: `translate(${-size.value.width * crop.value.left}px, ${-size.value.height * crop.value.top}px)`,
} : {})
watch(() => [props.enabled, props.revision], measure, { flush: 'post' })
onMounted(() => { observer = new ResizeObserver(measure); observer.observe(layer.value!); void measure() })
onBeforeUnmount(() => { generation++; observer?.disconnect() })
</script>
<template><div class="pdf-page-crop" :class="{ 'is-cropped': enabled }" :style="outer"><div ref="layer" class="pdf-page-crop-layer" :style="inner" @load.capture="measure"><slot /></div></div></template>
<style scoped>
.pdf-page-crop, .pdf-page-crop-layer { position: relative; width: fit-content; }
</style>

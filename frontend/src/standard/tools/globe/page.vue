<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { Map, NavigationControl, FullscreenControl, ScaleControl, setWorkerUrl } from 'maplibre-gl'
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'

// Vite must bundle the module worker and its shared imports for both dev and production.
setWorkerUrl(mapWorkerUrl)

const container = ref<HTMLDivElement>()
const frame = ref<HTMLElement>()
const projection = ref<'globe' | 'mercator'>('globe')
const ready = ref(false)
const error = ref('')
let map: Map | undefined
let resizeObserver: ResizeObserver | undefined
let loadingTimer: ReturnType<typeof setTimeout> | undefined

function switchProjection(value: 'globe' | 'mercator') {
  projection.value = value
  map?.setProjection({ type: value })
}

function resetView() {
  map?.flyTo({ center: [105, 25], zoom: 1.2, bearing: 0, pitch: 0, duration: 900 })
}

function initialize() {
  if (!container.value) return
  clearTimeout(loadingTimer)
  map?.remove()
  ready.value = false
  error.value = ''
  try {
    const instance = new Map({
      container: container.value,
      style: 'https://tiles.openfreemap.org/styles/liberty',
      center: [105, 25],
      zoom: 1.2,
      minZoom: -1,
      maxZoom: 18,
      locale: {
        'NavigationControl.ZoomIn': '放大',
        'NavigationControl.ZoomOut': '缩小',
        'NavigationControl.ResetBearing': '朝向正北',
        'FullscreenControl.Enter': '全屏',
        'FullscreenControl.Exit': '退出全屏',
      },
    })
    map = instance
    instance.addControl(new NavigationControl(), 'top-right')
    instance.addControl(new FullscreenControl({ container: frame.value }), 'top-right')
    instance.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left')
    instance.on('style.load', () => {
      instance.setProjection({ type: projection.value })
    })
    instance.on('load', () => {
      clearTimeout(loadingTimer)
      ready.value = true
      error.value = ''
    })
    instance.on('error', () => {
      error.value = '部分地图资源加载失败，请检查网络后重试。'
    })
    instance.on('idle', () => { if (ready.value) error.value = '' })
    loadingTimer = setTimeout(() => {
      if (!ready.value) error.value = '地图加载较慢，请检查网络或重试。'
    }, 20000)
  } catch {
    error.value = '地球仪启动失败，请确认浏览器已开启硬件加速后重试。'
  }
}

onMounted(() => {
  initialize()
  resizeObserver = new ResizeObserver(() => map?.resize())
  if (container.value) resizeObserver.observe(container.value)
})
onBeforeUnmount(() => {
  clearTimeout(loadingTimer)
  resizeObserver?.disconnect()
  map?.remove()
})
</script>

<template>
  <main class="globe-page">
    <header class="page-heading">
      <div><h1>地球仪</h1><p>转动地球，探索世界。</p></div>
      <span class="gesture-hint">拖动旋转 · 滚轮缩放</span>
    </header>
    <section ref="frame" class="globe-frame" aria-label="交互式世界地图">
      <div ref="container" class="map-canvas" />
      <div class="map-toolbar">
        <div class="projection-switch" aria-label="地图投影">
          <button :class="{ active: projection === 'globe' }" :aria-pressed="projection === 'globe'" :disabled="!ready" @click="switchProjection('globe')">地球</button>
          <button :class="{ active: projection === 'mercator' }" :aria-pressed="projection === 'mercator'" :disabled="!ready" @click="switchProjection('mercator')">平面</button>
        </div>
        <button class="reset-button" :disabled="!ready" @click="resetView">复位</button>
      </div>
      <div v-if="error || !ready" class="map-status" role="status">
        <span>{{ error || '正在加载世界地图…' }}</span>
        <button v-if="error" @click="initialize">重试</button>
      </div>
    </section>
  </main>
</template>

<style scoped>
.globe-page { padding: 24px; max-width: 1680px; margin: 0 auto; }
.page-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 18px; }
h1 { margin: 0 0 6px; font-size: 24px; color: #172b46; font-weight: 650; }
p, .gesture-hint { margin: 0; color: #748399; font-size: 13px; }
.globe-frame { position: relative; height: clamp(440px, calc(100dvh - 210px), 1000px); overflow: hidden; border: 1px solid #dae3ed; border-radius: 14px; background: radial-gradient(ellipse at center, #e0eaf4, #f5f8fc 75%); }
.globe-frame:fullscreen { height: 100dvh; border: 0; border-radius: 0; }
.map-canvas { position: absolute; inset: 0; }
.map-toolbar { position: absolute; top: 16px; left: 16px; display: flex; gap: 8px; }
.projection-switch, .reset-button { padding: 4px; background: #ffffffed; border: 1px solid #dbe3ec; border-radius: 8px; box-shadow: 0 2px 8px #263d5510; }
button { font: inherit; font-size: 13px; cursor: pointer; color: #52647b; }
.projection-switch button { border: 0; background: transparent; border-radius: 5px; padding: 7px 16px; }
.projection-switch button.active { background: #e8f1ff; color: #1767cf; }
.reset-button { padding: 7px 14px; }
button:disabled { opacity: .55; cursor: default; }
button:focus-visible { outline: 2px solid #3984e5; outline-offset: 2px; }
.map-status { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); display: flex; align-items: center; gap: 12px; padding: 14px 18px; background: #fffffff2; border: 1px solid #dbe3ec; border-radius: 10px; font-size: 13px; color: #52647b; max-width: 85%; }
.map-status button { flex-shrink: 0; border: 0; background: #e8f1ff; border-radius: 5px; padding: 6px 12px; color: #1767cf; }
@media (max-width: 640px) {
  .globe-page { padding: 14px; }
  .gesture-hint { display: none; }
  .globe-frame { height: max(440px, calc(100dvh - 185px)); }
}
</style>

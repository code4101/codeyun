<script setup lang="ts">
import { defineAsyncComponent, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { Map, LngLat, NavigationControl, ScaleControl, setWorkerUrl, type ExpressionSpecification, type FilterSpecification } from 'maplibre-gl'
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { constrainGlobeCamera } from './cameraConstraints'
import WorldClock from './WorldClock.vue'
import CountryInfo from './CountryInfo.vue'
import { countries, countryAt } from './countryData'

// Vite must bundle the module worker and its shared imports for both dev and production.
setWorkerUrl(mapWorkerUrl)

const container = ref<HTMLDivElement>()
const EqualEarthMap = defineAsyncComponent(() => import('./EqualEarthMap.vue'))
const projection = ref<'globe' | 'equalEarth'>('globe')
const selectedCountry = ref('')
// Keep the last selected country while navigating the map, including clicks on the ocean.
function selectCountry(country: string) {
  if (country) selectedCountry.value = country
}
function updateSelection() {
  if (map?.getLayer('country-selection')) {
    map.setFilter('country-selection', ['==', ['get', 'group'], selectedCountry.value])
  }
}
watch(selectedCountry, updateSelection)
const ready = ref(false)
const error = ref('')
let map: Map | undefined
let resizeObserver: ResizeObserver | undefined
let loadingTimer: ReturnType<typeof setTimeout> | undefined

async function switchProjection(value: 'globe' | 'equalEarth') {
  projection.value = value
  await nextTick()
  if (value === 'globe') map?.resize()
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
      bearing: 0,
      roll: 0,
      minPitch: 0,
      maxPitch: 0,
      pitch: 0,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      // 在相机更新前统一约束，覆盖鼠标、触摸、键盘及动画，避免事后纠正产生抖动。
      transformCameraUpdate: ({ center, zoom }) => {
        const constrained = constrainGlobeCamera(center.lat, zoom)
        return {
          center: new LngLat(center.lng, constrained.latitude),
          zoom: constrained.zoom,
          bearing: constrained.bearing,
          roll: constrained.roll,
          pitch: constrained.pitch,
        }
      },
      localIdeographFontFamily: 'Microsoft YaHei, Noto Sans CJK SC, sans-serif',
      locale: {
        'NavigationControl.ZoomIn': '放大',
        'NavigationControl.ZoomOut': '缩小',
        'NavigationControl.ResetBearing': '朝向正北',
      },
    })
    map = instance
    instance.touchZoomRotate.disableRotation()
    instance.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    instance.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left')
    instance.on('style.load', () => {
      instance.setProjection({ type: 'globe' })
      instance.addSource('country-details', { type: 'geojson', data: countries })
      instance.addLayer({
        id: 'country-selection', type: 'fill', source: 'country-details',
        filter: ['==', ['get', 'group'], selectedCountry.value],
        paint: { 'fill-color': '#3b9778', 'fill-opacity': 0.25 },
      }, instance.getStyle().layers.find(layer => layer.type === 'symbol')?.id)
      // 本应用采用中国地图标注口径：台湾使用地区层级，不能沿用底图的 country 层级。
      const taiwan: ExpressionSpecification = ['any', ['==', ['get', 'iso_a2'], 'TW'], ['==', ['get', 'name:en'], 'Taiwan'], ['==', ['get', 'name_en'], 'Taiwan']]
      // OpenFreeMap carries translated names; only replace name labels, retaining road numbers.
      for (const layer of instance.getStyle().layers) {
        if (layer.type !== 'symbol' || !JSON.stringify(layer.layout?.['text-field'] ?? '').includes('name')) continue
        instance.setLayoutProperty(layer.id, 'text-field', [
          'case', taiwan, '中国台湾', [
          'coalesce',
          ['get', 'name:zh-Hans'],
          ['get', 'name:zh'],
          ['get', 'name'],
          ['get', 'name:en'],
          ],
        ])
        if (layer.id.startsWith('label_country_')) {
          instance.setFilter(layer.id, ['all', layer.filter!, ['!', taiwan]] as FilterSpecification)
        } else if (layer.id === 'label_state') {
          instance.setFilter(layer.id, ['any', layer.filter!, ['all', ['==', ['get', 'class'], 'country'], taiwan]] as FilterSpecification)
        }
      }
    })
    instance.on('click', event => {
      selectCountry(countryAt(event.lngLat.lng, event.lngLat.lat))
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
  <main class="globe-page" aria-label="地球仪">
    <WorldClock />
    <section aria-labelledby="globe-heading">
      <header class="section-heading">
        <div><h2 id="globe-heading">地球仪</h2><p>转动地球，点击国家了解各地。</p></div>
        <span class="gesture-hint">{{ projection === 'globe' ? '北向锁定 · 上下左右拖动 · 滚轮或双指缩放' : '拖动浏览 · 滚轮缩放' }}</span>
      </header>
      <section class="globe-frame" aria-label="交互式世界地图">
        <div v-show="projection === 'globe'" ref="container" class="map-canvas" />
        <EqualEarthMap v-if="projection === 'equalEarth'" :selected-country="selectedCountry" @select-country="selectCountry" />
        <div class="map-toolbar">
          <div class="projection-switch" aria-label="地图投影">
            <button :class="{ active: projection === 'globe' }" :aria-pressed="projection === 'globe'" @click="switchProjection('globe')">地球</button>
            <button :class="{ active: projection === 'equalEarth' }" :aria-pressed="projection === 'equalEarth'" @click="switchProjection('equalEarth')">平面</button>
          </div>
        </div>
        <div v-if="projection === 'globe' && (error || !ready)" class="map-status" role="status">
          <span>{{ error || '正在加载世界地图…' }}</span>
          <button v-if="error" @click="initialize">重试</button>
        </div>
      </section>
      <CountryInfo v-if="selectedCountry" :country="selectedCountry" />
    </section>
  </main>
</template>

<style scoped>
.globe-page { padding: 24px; max-width: 1680px; margin: 0 auto; }
.section-heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; margin-bottom: 14px; }
h2 { margin: 0; font-size: 18px; line-height: 1.25; color: #17243b; }
.section-heading p { margin: 10px 0 0; color: #61728a; font-size: 14px; }
.gesture-hint { color: #748399; font-size: 13px; }
.globe-frame { position: relative; height: clamp(440px, calc(100dvh - 210px), 1000px); overflow: hidden; border: 1px solid #dae3ed; border-radius: 14px; background: radial-gradient(ellipse at center, #e0eaf4, #f5f8fc 75%); }
.map-canvas { position: absolute; inset: 0; }
.map-toolbar { position: absolute; top: 16px; left: 16px; display: flex; gap: 8px; }
.projection-switch { padding: 4px; background: #ffffffed; border: 1px solid #dbe3ec; border-radius: 8px; box-shadow: 0 2px 8px #263d5510; }
button { font: inherit; font-size: 13px; cursor: pointer; color: #52647b; }
.projection-switch button { border: 0; background: transparent; border-radius: 5px; padding: 7px 16px; }
.projection-switch button.active { background: #e8f1ff; color: #1767cf; }
button:focus-visible { outline: 2px solid #3984e5; outline-offset: 2px; }
.map-status { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); display: flex; align-items: center; gap: 12px; padding: 14px 18px; background: #fffffff2; border: 1px solid #dbe3ec; border-radius: 10px; font-size: 13px; color: #52647b; max-width: 85%; }
.map-status button { flex-shrink: 0; border: 0; background: #e8f1ff; border-radius: 5px; padding: 6px 12px; color: #1767cf; }
@media (max-width: 640px) {
  .globe-page { padding: 14px; }
  .gesture-hint { display: none; }
  .globe-frame { height: max(440px, calc(100dvh - 185px)); }
}
</style>

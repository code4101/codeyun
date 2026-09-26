<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { Map, LngLat, ScaleControl, setWorkerUrl, type GeoJSONSource, type ExpressionSpecification, type FilterSpecification } from 'maplibre-gl'
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { chooseDragAxis, constrainGlobeCamera, preserveGlobeScale, GLOBE_CENTER_LATITUDE, type DragAxis } from './cameraConstraints'
import { fitGlobeZoom } from './fitGlobe'
import WorldClock from './WorldClock.vue'
import CountryInfo from './CountryInfo.vue'
import { countries, countryAt } from './countryData'
import MapDisplayMenu from './MapDisplayMenu.vue'
import { showBasemapLabel, displayGroup, displayStorageKey, parseMapDisplay, type DisplayKey } from './mapDisplay'
import { useAdminRegions } from './useAdminRegions'
import { adminSource, type AdminFeature, type AdminViewport } from './adminRegions'
import EncyclopediaSummary from './EncyclopediaSummary.vue'

// Vite must bundle the module worker and its shared imports for both dev and production.
setWorkerUrl(mapWorkerUrl)

const container = ref<HTMLDivElement>()
const EqualEarthMap = defineAsyncComponent(() => import('./EqualEarthMap.vue'))
const projection = ref<'globe' | 'equalEarth'>('globe')
const selectedCountry = ref('')
const selectedRegion = ref<AdminFeature | null>(null)
// Country context remains available, but only the deepest selection is highlighted.
const highlightedCountry = computed(() => selectedRegion.value ? '' : selectedCountry.value)
const admin = useAdminRegions()
const adminFeatures = admin.features
const adminError = admin.error
let flatViewport: AdminViewport | null = null
function selectRegion(region: AdminFeature) {
  selectedRegion.value = region
  selectedCountry.value = '中国'
  if (map?.getLayer('admin-selection')) map.setFilter('admin-selection', ['==', ['get', 'code'], region.properties.code])
}
function updateAdminView() {
  if (selectedCountry.value !== '中国' || !displaySettings.value.autoRegions) {
    admin.update(null, false)
    return
  }
  if (projection.value === 'equalEarth') { admin.update(flatViewport, displaySettings.value.autoRegions); return }
  if (!map || !container.value || !ready.value) return
  const instance = map
  admin.update({ width: container.value.clientWidth, height: container.value.clientHeight, unproject(x, y) {
    const point = instance.unproject([x,y])
    const check = instance.project(point)
    return Number.isFinite(point.lng) && Number.isFinite(point.lat) && Math.hypot(check.x-x,check.y-y)<2 ? [point.lng,point.lat] : null
  } }, displaySettings.value.autoRegions)
}
function flatViewChanged(view: AdminViewport) { flatViewport = view; if (projection.value === 'equalEarth') updateAdminView() }
function syncAdminLayers() {
  if (projection.value !== 'globe' || !map?.getSource('admin-regions')) return
  const all = adminFeatures.value
  ;(map.getSource('admin-regions') as GeoJSONSource).setData({ type: 'FeatureCollection', features: all })
  const parents = new Set(all.map(feature => feature.properties.parent))
  ;(map.getSource('admin-labels') as GeoJSONSource).setData({ type: 'FeatureCollection', features: all.filter(feature => !parents.has(feature.properties.code)).map(feature => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: feature.properties.label }, properties: feature.properties,
  })) })
  map.setLayoutProperty('admin-borders', 'visibility', displaySettings.value.adminBoundaries ? 'visible' : 'none')
  map.setLayoutProperty('admin-names', 'visibility', displaySettings.value.adminNames ? 'visible' : 'none')
}
watch(adminFeatures, () => { syncAdminLayers(); applyDisplaySettings() })
const displaySettings = ref(parseMapDisplay(null))
try { displaySettings.value = parseMapDisplay(localStorage.getItem(displayStorageKey)) } catch { /* Storage may be disabled. */ }
const displayMenu = ref<{ x: number; y: number } | null>(null)
async function openDisplayMenu(event: MouseEvent) {
  displayMenu.value = null
  await nextTick()
  displayMenu.value = { x: event.clientX, y: event.clientY }
}
function applyDisplaySettings() {
  for (const layer of map?.getStyle()?.layers ?? []) {
    // In automatic hierarchy mode, only our selected-country overlays own subdivisions.
    if (layer.id === 'boundary_3') {
      map!.setLayoutProperty(layer.id, 'visibility', displaySettings.value.autoRegions ? 'none' : 'visible')
    }
    const key = displayGroup(layer)
    if (key) map!.setLayoutProperty(layer.id, 'visibility', showBasemapLabel(key, displaySettings.value, adminFeatures.value.length > 0) ? 'visible' : 'none')
  }
}
function toggleDisplay(key: DisplayKey) {
  displaySettings.value[key] = !displaySettings.value[key]
  applyDisplaySettings()
  syncAdminLayers()
  updateAdminView()
  try { localStorage.setItem(displayStorageKey, JSON.stringify(displaySettings.value)) } catch { /* Keep session preferences usable. */ }
}
// Keep the last selected country while navigating the map, including clicks on the ocean.
function selectCountry(country: string) {
  if (country) {
    selectedCountry.value = country
    selectedRegion.value = null
    if (map?.getLayer('admin-selection')) map.setFilter('admin-selection', ['==', ['get', 'code'], ''])
  }
}
function updateSelection() {
  if (map?.getLayer('country-selection')) {
    map.setFilter('country-selection', ['==', ['get', 'group'], highlightedCountry.value])
  }
}
watch(highlightedCountry, updateSelection)
watch(selectedCountry, updateAdminView)
const ready = ref(false)
const error = ref('')
let map: Map | undefined
let resizeObserver: ResizeObserver | undefined
let loadingTimer: ReturnType<typeof setTimeout> | undefined
let autoFit = true
const pointers = new Set<number>()
let drag: { id: number; x: number; y: number; latitude: number; longitude: number; axis: DragAxis | null } | undefined
let suppressClick = false
let dragFrame = 0
let pendingDrag: { x:number; y:number } | undefined
function flushDrag() {
  dragFrame=0
  const point=pendingDrag; pendingDrag=undefined
  if (!point || !drag || !map) return
  const dx=point.x-drag.x, dy=point.y-drag.y
  map.panBy(drag.axis === 'horizontal' ? [-dx,0] : [0,-dy], {duration:0})
  drag.x=point.x; drag.y=point.y
}

// Use public panBy with a single screen axis; native free dragging is disabled.
// Multiple fingers cancel dragging; wheel is the only zoom gesture.
function startDrag(event: PointerEvent) {
  if (event.button !== 0 || !map || event.target !== map.getCanvas()) return
  map.getCanvas().setPointerCapture(event.pointerId)
  pointers.add(event.pointerId)
  admin.setInteracting(true)
  if (pointers.size !== 1) {
    cancelAnimationFrame(dragFrame); dragFrame=0; pendingDrag=undefined
    drag = undefined; suppressClick = true; return
  }
  suppressClick = false
  const center = map.getCenter()
  drag = { id: event.pointerId, x: event.clientX, y: event.clientY, latitude: center.lat, longitude: center.lng, axis: null }
}

function moveDrag(event: PointerEvent) {
  if (!drag || drag.id !== event.pointerId || !map) return
  const dx = event.clientX - drag.x
  const dy = event.clientY - drag.y
  drag.axis ??= chooseDragAxis(dx, dy)
  if (!drag.axis) return
  autoFit = false
  suppressClick = true
  pendingDrag={x:event.clientX,y:event.clientY}
  if (!dragFrame) dragFrame=requestAnimationFrame(flushDrag)
}

function endDrag(event: PointerEvent) {
  cancelAnimationFrame(dragFrame)
  flushDrag()
  pointers.delete(event.pointerId)
  if (drag?.id === event.pointerId) drag = undefined
  const canvas = map?.getCanvas()
  if (canvas?.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId)
  if (!pointers.size) { admin.setInteracting(false); updateAdminView() }
}

function fitInitialGlobe() {
  if (!map || !container.value || !autoFit || projection.value !== 'globe') return
  const zoom = fitGlobeZoom(container.value.clientWidth, container.value.clientHeight, map.getCenter().lat, map.getVerticalFieldOfView())
  if (zoom !== null) map.jumpTo({ zoom: Math.max(map.getMinZoom(), Math.min(map.getMaxZoom(), zoom)) })
}

async function switchProjection(value: 'globe' | 'equalEarth') {
  displayMenu.value = null
  projection.value = value
  await nextTick()
  if (value === 'globe') { map?.resize(); syncAdminLayers() }
  updateAdminView()
}

function initialize() {
  if (!container.value) return
  clearTimeout(loadingTimer)
  map?.remove()
  ready.value = false
  error.value = ''
  autoFit = true
  try {
    const instance = new Map({
      container: container.value,
      attributionControl: false,
      style: 'https://tiles.openfreemap.org/styles/liberty',
      center: [105, GLOBE_CENTER_LATITUDE],
      zoom: 1.2,
      minZoom: -1,
      maxZoom: 18,
      // Bound fill-rate on high-DPI screens; camera updates are also limited to one per frame.
      pixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
      cancelPendingTileRequestsWhileZooming: true,
      bearing: 0,
      roll: 0,
      minPitch: 0,
      maxPitch: 0,
      pitch: 0,
      dragRotate: false,
      dragPan: false,
      pitchWithRotate: false,
      touchPitch: false,
      touchZoomRotate: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      // 在相机更新前统一约束，覆盖鼠标、触摸、键盘及动画，避免事后纠正产生抖动。
      transformCameraUpdate: ({ center, zoom }) => {
        const constrained = constrainGlobeCamera(drag?.axis === 'horizontal' ? drag.latitude : center.lat, zoom)
        return {
          center: new LngLat(drag?.axis === 'vertical' ? drag.longitude : center.lng, constrained.latitude),
          zoom: drag?.axis ? preserveGlobeScale(zoom, center.lat, constrained.latitude) : constrained.zoom,
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
    // Only user camera changes end automatic fitting; initialization/resizing must not.
    instance.on('movestart', event => { if (event.originalEvent) { autoFit = false; admin.setInteracting(true) } })
    fitInitialGlobe()
    instance.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left')
    instance.on('style.load', () => {
      instance.setProjection({ type: 'globe' })
      fitInitialGlobe()
      instance.addSource('country-details', { type: 'geojson', data: countries })
      const empty = { type: 'FeatureCollection' as const, features: [] }
      instance.addSource('admin-regions', { type: 'geojson', data: empty })
      instance.addSource('admin-labels', { type: 'geojson', data: empty })
      instance.addLayer({ id: 'admin-hit', type: 'fill', source: 'admin-regions', paint: { 'fill-opacity': 0 } })
      instance.addLayer({ id: 'admin-selection', type: 'fill', source: 'admin-regions', filter: ['==', ['get', 'code'], selectedRegion.value?.properties.code ?? ''], paint: { 'fill-color': '#489e8b', 'fill-opacity': .22 } })
      instance.addLayer({ id: 'admin-borders', type: 'line', source: 'admin-regions', paint: { 'line-color': '#528391', 'line-width': ['match', ['get','level'], 1, 1.4, 2, 1, .7], 'line-opacity': .85 } })
      instance.addLayer({ id: 'admin-names', type: 'symbol', source: 'admin-labels', layout: { 'text-field': ['get','name'], 'text-size': 12, 'text-font': ['Noto Sans Regular'] }, paint: { 'text-color': '#315768', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 } })
      instance.addLayer({
        id: 'country-selection', type: 'fill', source: 'country-details',
        filter: ['==', ['get', 'group'], highlightedCountry.value],
        paint: { 'fill-color': '#3b9778', 'fill-opacity': 0.25 },
      }, instance.getStyle().layers.find(layer => layer.type === 'symbol')?.id)
      // 本应用采用中国地图标注口径：台湾使用地区层级，不能沿用底图的 country 层级。
      const taiwan: ExpressionSpecification = ['any', ['==', ['get', 'iso_a2'], 'TW'], ['==', ['get', 'name:en'], 'Taiwan'], ['==', ['get', 'name_en'], 'Taiwan']]
      // OpenFreeMap carries translated names; only replace name labels, retaining road numbers.
      for (const layer of instance.getStyle().layers) {
        if (layer.id.startsWith('admin-')) continue
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
      applyDisplaySettings()
      syncAdminLayers()
    })
    instance.on('click', event => {
      if (suppressClick) return
      const hits = instance.queryRenderedFeatures(event.point, { layers: ['admin-hit'] }).sort((a,b) => Number(b.properties.level)-Number(a.properties.level))
      const region = adminFeatures.value.find(feature => feature.properties.code === hits[0]?.properties.code)
      if (region) { selectRegion(region); return }
      selectCountry(countryAt(event.lngLat.lng, event.lngLat.lat))
    })
    instance.on('load', () => {
      applyDisplaySettings()
      clearTimeout(loadingTimer)
      ready.value = true
      updateAdminView()
      error.value = ''
    })
    instance.on('error', () => {
      error.value = '部分地图资源加载失败，请检查网络后重试。'
    })
    instance.on('idle', () => { if (ready.value) error.value = '' })
    instance.on('moveend', () => { if (!pointers.size) { admin.setInteracting(false); updateAdminView() } })
    loadingTimer = setTimeout(() => {
      if (!ready.value) error.value = '地图加载较慢，请检查网络或重试。'
    }, 20000)
  } catch {
    error.value = '地球仪启动失败，请确认浏览器已开启硬件加速后重试。'
  }
}

onMounted(() => {
  initialize()
  resizeObserver = new ResizeObserver(() => {
    map?.resize()
    fitInitialGlobe()
    updateAdminView()
  })
  if (container.value) resizeObserver.observe(container.value)
})
onBeforeUnmount(() => {
  cancelAnimationFrame(dragFrame)
  pendingDrag=undefined
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
        <h2 id="globe-heading">地球仪</h2>
      </header>
      <section class="globe-frame" aria-label="交互式世界地图" @contextmenu.prevent="openDisplayMenu">
        <div v-show="projection === 'globe'" ref="container" class="map-canvas"
          @pointerdown="startDrag" @pointermove="moveDrag" @pointerup="endDrag" @pointercancel="endDrag" @lostpointercapture="endDrag" />
        <EqualEarthMap v-if="projection === 'equalEarth'" :selected-country="selectedCountry" :show-labels="displaySettings.countries"
          :admin-features="adminFeatures" :admin-boundaries="displaySettings.adminBoundaries" :admin-names="displaySettings.adminNames" :selected-region="selectedRegion?.properties.code ?? ''"
          @select-country="selectCountry" @select-region="selectRegion" @viewport="flatViewChanged" @interacting="admin.setInteracting" />
        <MapDisplayMenu v-if="displayMenu" :x="displayMenu.x" :y="displayMenu.y" :flat="projection === 'equalEarth'"
          :settings="displaySettings" @toggle="toggleDisplay" @close="displayMenu = null" />
        <details v-if="projection === 'globe'" class="map-attribution">
          <summary aria-label="地图数据来源" title="地图数据来源">ⓘ</summary>
          <div>
            <a href="https://openfreemap.org/" target="_blank" rel="noopener noreferrer">OpenFreeMap</a> ·
            © <a href="https://openmaptiles.org/" target="_blank" rel="noopener noreferrer">OpenMapTiles</a> ·
            © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>
          </div>
        </details>
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
        <div v-if="adminFeatures.length || adminError" class="admin-source">
          <a :href="adminSource" target="_blank" rel="noopener noreferrer">行政区划 · DataV</a>
          <button v-if="adminError" @click="updateAdminView">{{ adminError }} · 重试</button>
        </div>
      </section>
      <article v-if="selectedRegion" class="region-info">
        <h2>{{ selectedRegion.properties.name }}</h2>
        <p>中国 · {{ ['国家级', '省级', '地级', '区县级'][selectedRegion.properties.level] }} · {{ selectedRegion.properties.code }}</p>
        <EncyclopediaSummary :key="selectedRegion.properties.code" :title="selectedRegion.properties.name" />
      </article>
      <CountryInfo v-else-if="selectedCountry" :country="selectedCountry" />
    </section>
  </main>
</template>

<style scoped>
.globe-page { padding: 24px; max-width: 1680px; margin: 0 auto; }
.section-heading { margin-bottom: 14px; }
h2 { margin: 0; font-size: 18px; line-height: 1.25; color: #17243b; }
.globe-frame { position: relative; height: clamp(440px, calc(100dvh - 210px), 1000px); overflow: hidden; border: 1px solid #dae3ed; border-radius: 14px; background: radial-gradient(ellipse at center, #e0eaf4, #f5f8fc 75%); }
.map-canvas { position: absolute; inset: 0; }
.admin-source { position: absolute; left: 12px; bottom: 36px; font-size: 11px; background: #ffffffdc; padding: 3px 7px; border-radius: 4px; }
.admin-source a, .region-info a { color: #467eae; }
.admin-source button { margin-left: 8px; border: 0; background: transparent; }
.region-info { margin-top: 20px; padding: 24px; border: 1px solid #dbe4ee; border-radius: 14px; }
.region-info p { color: #61728a; font-size: 13px; }
.region-info a { margin-right: 18px; font-size: 13px; }
.map-attribution { position: absolute; bottom: 10px; right: 10px; z-index: 2; font-size: 11px; color: #617487; }
.map-attribution summary { float: right; list-style: none; cursor: pointer; background: #ffffffdc; border-radius: 50%; width: 22px; height: 22px; text-align: center; line-height: 22px; font-size: 16px; }
.map-attribution summary::-webkit-details-marker { display: none; }
.map-attribution div { margin-right: 28px; padding: 4px 8px; border-radius: 5px; background: #fffffff2; max-width: min(360px, 65vw); }
.map-attribution a { color: inherit; }
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
  .globe-frame { height: max(440px, calc(100dvh - 185px)); }
}
</style>

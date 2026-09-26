<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { geoEqualEarth, geoGraticule10, geoPath } from 'd3-geo'
import { select } from 'd3-selection'
import { zoom, zoomIdentity, type ZoomBehavior } from 'd3-zoom'
import { countries, countryGroup } from './countryData'
import type { AdminFeature, AdminViewport } from './adminRegions'

// Natural Earth 5.1.2, public domain. Geometry retained; Chinese display names and groups normalized locally.
// Source: https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_admin_0_countries.geojson
const props = defineProps<{ selectedCountry: string; showLabels: boolean; adminFeatures: AdminFeature[]; adminBoundaries: boolean; adminNames: boolean; selectedRegion: string }>()
const emit = defineEmits<{ selectCountry: [country: string]; selectRegion: [region: AdminFeature]; viewport: [view: AdminViewport]; interacting: [active: boolean] }>()
const svg = ref<SVGSVGElement>()
const geography = ref<SVGGElement>()
const interacting = ref(false)
let liveTransform = zoomIdentity
let frame = 0
const width = ref(1000)
const height = ref(600)
const transform = ref(zoomIdentity)
const hovered = ref('')
const sphere = { type: 'Sphere' } as const
const projection = computed(() => geoEqualEarth().fitExtent(
  [[24, 76], [Math.max(25, width.value - 24), Math.max(77, height.value - 45)]], sphere,
))
const path = computed(() => geoPath(projection.value))
const outline = computed(() => path.value(sphere) ?? '')
const graticule = computed(() => path.value(geoGraticule10()) ?? '')
const shapes = computed(() => countries.features.map((feature, id) => ({
  id, name: feature.properties.name, path: path.value(feature) ?? '',
  position: projection.value(feature.properties.label), area: path.value.area(feature),
})))
const adminShapes = computed(() => props.adminFeatures.map(feature => ({
  feature, path: path.value(feature) ?? '', area: path.value.area(feature),
  position: projection.value(feature.properties.label),
})))
const adminLabels = computed(() => {
  const parents = new Set(props.adminFeatures.map(feature => feature.properties.parent))
  const boxes: number[][] = []
  return adminShapes.value.flatMap(({ feature, area, position: point }) => {
    if (parents.has(feature.properties.code) || area*transform.value.k**2 < 1400) return []
    if (!point) return []
    const [x, y] = transform.value.apply(point)
    const half = feature.properties.name.length*6+4
    if (x < 0 || x > width.value || y < 0 || y > height.value) return []
    const box = [x-half,y-9,x+half,y+9]
    if (boxes.some(b => box[0]!<b[2]! && box[2]!>b[0]! && box[1]!<b[3]! && box[3]!>b[1]!)) return []
    boxes.push(box)
    return [{ code: feature.properties.code, name: feature.properties.name, x, y }]
  })
})
watch([transform, width, height], () => {
  const p = projection.value, t = transform.value
  emit('viewport', { width: width.value, height: height.value, unproject(x, y) {
    const position = p.invert?.(t.invert([x,y]))
    if (!position || !position.every(Number.isFinite)) return null
    const check = p(position)
    if (!check) return null
    const screen = t.apply(check)
    return Math.hypot(screen[0]-x,screen[1]-y) < 2 ? position : null
  } })
}, { flush: 'post', immediate: true })
// Greedy screen-space label placement keeps the overview legible and reveals smaller places on zoom.
const labels = computed(() => {
  const boxes: number[][] = []
  return [...shapes.value].sort((a, b) => b.area - a.area).filter(country => {
    if (!country.position || country.area * transform.value.k ** 2 < 180) return false
    const [x, y] = transform.value.apply(country.position)
    const half = country.name.length * 6 + 4
    if (x < half || x > width.value - half || y < 60 || y > height.value - 30) return false
    const box = [x - half, y - 9, x + half, y + 9]
    if (boxes.some(b => box[0]! < b[2]! && box[2]! > b[0]! && box[1]! < b[3]! && box[3]! > b[1]!)) return false
    boxes.push(box)
    return true
  })
})
let behavior: ZoomBehavior<SVGSVGElement, unknown> | undefined
let observer: ResizeObserver | undefined
function reset() {
  if (svg.value && behavior) select(svg.value).call(behavior.transform, zoomIdentity)
}
function keydown(event: KeyboardEvent) {
  if (!svg.value || !behavior) return
  const arrows: Record<string, [number, number]> = { ArrowLeft: [50, 0], ArrowRight: [-50, 0], ArrowUp: [0, 50], ArrowDown: [0, -50] }
  if (arrows[event.key]) {
    event.preventDefault()
    const [x, y] = arrows[event.key]!
    select(svg.value).call(behavior.translateBy, x / transform.value.k, y / transform.value.k)
  } else if (event.key === 'Home') {
    event.preventDefault()
    reset()
  }
}
onMounted(() => {
  if (!svg.value) return
  behavior = zoom<SVGSVGElement, unknown>().scaleExtent([1, 128])
    .on('start', () => { interacting.value = true; emit('interacting', true) })
    .on('end', () => {
      cancelAnimationFrame(frame); frame = 0
      geography.value?.setAttribute('transform', liveTransform.toString())
      transform.value = liveTransform
      interacting.value = false
      emit('interacting', false)
    })
    .touchable(false)
    .filter(event => event.type === 'wheel' || (event.type === 'mousedown' && event.button === 0 && !event.ctrlKey))
    .on('zoom', event => {
      // Move the existing SVG as one group. Vue and label layout only catch up after the gesture.
      liveTransform = event.transform
      if (!frame) frame = requestAnimationFrame(() => {
        frame = 0
        geography.value?.setAttribute('transform', liveTransform.toString())
      })
    })
  select(svg.value).call(behavior)
  observer = new ResizeObserver(entries => {
    const rect = entries[0]?.contentRect
    if (!rect || rect.width === 0 || rect.height === 0) return
    width.value = rect.width
    height.value = rect.height
    behavior?.extent([[0, 0], [rect.width, rect.height]])
      .translateExtent([[0, 0], [rect.width, rect.height]])
    reset()
  })
  observer.observe(svg.value)
})
onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  emit('interacting', false)
  observer?.disconnect()
  if (svg.value) select(svg.value).on('.zoom', null)
})
</script>

<template>
  <div class="equal-earth">
    <svg ref="svg" tabindex="0" role="group" aria-label="平面世界地图，点击国家查看介绍，可拖动或使用方向键移动，滚轮缩放，Home 复位" @keydown="keydown">
      <g ref="geography" :transform="transform.toString()">
        <path :d="outline" fill="#dcecf7" stroke="#acc9df" vector-effect="non-scaling-stroke" />
        <path :d="graticule" fill="none" stroke="#c5dce9" stroke-width="0.6" vector-effect="non-scaling-stroke" pointer-events="none" />
        <path v-for="country in shapes" :key="country.id" :d="country.path" class="country"
          :class="{ hovered: !selectedRegion && hovered && countryGroup(hovered) === countryGroup(country.name), selected: !selectedRegion && selectedCountry === countryGroup(country.name) }" vector-effect="non-scaling-stroke"
          role="button" tabindex="0" :aria-label="`查看${country.name}介绍`" :aria-pressed="!selectedRegion && selectedCountry === countryGroup(country.name)"
          @mouseenter="!interacting && (hovered = country.name)" @mouseleave="!interacting && (hovered = '')" @click="emit('selectCountry', countryGroup(country.name))"
          @keydown.enter.stop.prevent="emit('selectCountry', countryGroup(country.name))" @keydown.space.stop.prevent="emit('selectCountry', countryGroup(country.name))">
          <title>{{ country.name }}</title>
        </path>
        <path v-for="item in adminShapes" :key="item.feature.properties.code" :d="item.path" class="admin-region"
          :fill="selectedRegion === item.feature.properties.code ? '#8fc7b980' : 'transparent'"
          :stroke="adminBoundaries ? '#608d99' : 'none'" :stroke-width="item.feature.properties.level === 1 ? 1.2 : .7"
          vector-effect="non-scaling-stroke" role="button" tabindex="0" :aria-label="item.feature.properties.name"
          @click.stop="emit('selectRegion', item.feature)" @keydown.enter.stop.prevent="emit('selectRegion', item.feature)">
          <title>{{ item.feature.properties.name }}</title>
        </path>
      </g>
      <g v-if="adminNames && !interacting" class="labels" aria-hidden="true">
        <text v-for="label in adminLabels" :key="label.code" :x="label.x" :y="label.y">{{ label.name }}</text>
      </g>
      <g v-if="showLabels && !interacting" class="labels" aria-hidden="true">
        <text v-for="country in labels" :key="country.id"
          :x="transform.apply(country.position!)[0]" :y="transform.apply(country.position!)[1]">{{ country.name }}</text>
      </g>
    </svg>
    <div class="map-caption">{{ hovered || selectedCountry || '点击国家查看介绍' }}</div>
    <div class="attribution"><a href="https://www.naturalearthdata.com/" target="_blank" rel="noopener noreferrer">Natural Earth</a> · <a href="https://d3js.org/d3-geo/cylindrical#geoEqualEarth" target="_blank" rel="noopener noreferrer">D3</a></div>
  </div>
</template>

<style scoped>
.equal-earth { position: absolute; inset: 0; background: #f6f9fc; }
svg { display: block; width: 100%; height: 100%; cursor: grab; touch-action: none; }
svg:active { cursor: grabbing; }
svg:focus-visible { outline: 2px solid #3984e5; outline-offset: -3px; }
.country { fill: #e9eddf; stroke: #839b98; stroke-width: .55; }
.country { cursor: pointer; }
.country.hovered { fill: #cfe3da; }
.country.selected { fill: #a6cebf; stroke: #377b69; stroke-width: 1.2; }
/* SVG focus must follow the geography instead of drawing a rectangular bounding box. */
.country:focus, .admin-region:focus { outline: none; box-shadow: none; }
.country:focus-visible, .admin-region:focus-visible { stroke: #1767cf; stroke-width: 2; }
.labels { pointer-events: none; font: 12px 'Microsoft YaHei', sans-serif; fill: #354f5c; text-anchor: middle; dominant-baseline: middle; paint-order: stroke; stroke: #f8faf3; stroke-width: 3px; stroke-linejoin: round; }
.map-caption, .attribution { position: absolute; bottom: 10px; font-size: 12px; background: #ffffffdc; color: #617487; padding: 3px 7px; border-radius: 4px; }
.map-caption { left: 10px; pointer-events: none; }
.attribution { right: 10px; }
a { color: inherit; }
</style>

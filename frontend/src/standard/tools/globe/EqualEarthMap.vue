<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { geoEqualEarth, geoGraticule10, geoPath } from 'd3-geo'
import { select } from 'd3-selection'
import { zoom, zoomIdentity, type ZoomBehavior } from 'd3-zoom'
import { countries, countryGroup } from './countryData'

// Natural Earth 5.1.2, public domain. Geometry retained; Chinese display names and groups normalized locally.
// Source: https://github.com/nvkelso/natural-earth-vector/blob/v5.1.2/geojson/ne_110m_admin_0_countries.geojson
defineProps<{ selectedCountry: string }>()
const emit = defineEmits<{ selectCountry: [country: string] }>()
const svg = ref<SVGSVGElement>()
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
function scaleBy(factor: number) {
  if (svg.value && behavior) select(svg.value).call(behavior.scaleBy, factor)
}
function keydown(event: KeyboardEvent) {
  if (!svg.value || !behavior) return
  const arrows: Record<string, [number, number]> = { ArrowLeft: [50, 0], ArrowRight: [-50, 0], ArrowUp: [0, 50], ArrowDown: [0, -50] }
  if (arrows[event.key]) {
    event.preventDefault()
    const [x, y] = arrows[event.key]!
    select(svg.value).call(behavior.translateBy, x / transform.value.k, y / transform.value.k)
  } else if (['+', '=', '-', 'Home'].includes(event.key)) {
    event.preventDefault()
    if (event.key === 'Home') reset()
    else scaleBy(event.key === '-' ? 1 / 1.5 : 1.5)
  }
}
onMounted(() => {
  if (!svg.value) return
  behavior = zoom<SVGSVGElement, unknown>().scaleExtent([1, 10])
    .on('zoom', event => { transform.value = event.transform })
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
  observer?.disconnect()
  if (svg.value) select(svg.value).on('.zoom', null)
})
</script>

<template>
  <div class="equal-earth">
    <svg ref="svg" tabindex="0" role="group" aria-label="平面世界地图，点击国家查看介绍，可拖动或使用方向键移动，加减键缩放，Home 复位" @keydown="keydown">
      <g :transform="transform.toString()">
        <path :d="outline" fill="#dcecf7" stroke="#acc9df" vector-effect="non-scaling-stroke" />
        <path :d="graticule" fill="none" stroke="#c5dce9" stroke-width="0.6" vector-effect="non-scaling-stroke" pointer-events="none" />
        <path v-for="country in shapes" :key="country.id" :d="country.path" class="country"
          :class="{ hovered: hovered && countryGroup(hovered) === countryGroup(country.name), selected: selectedCountry === countryGroup(country.name) }" vector-effect="non-scaling-stroke"
          role="button" tabindex="0" :aria-label="`查看${country.name}介绍`" :aria-pressed="selectedCountry === countryGroup(country.name)"
          @mouseenter="hovered = country.name" @mouseleave="hovered = ''" @click="emit('selectCountry', countryGroup(country.name))"
          @keydown.enter.stop.prevent="emit('selectCountry', countryGroup(country.name))" @keydown.space.stop.prevent="emit('selectCountry', countryGroup(country.name))">
          <title>{{ country.name }}</title>
        </path>
      </g>
      <g class="labels" aria-hidden="true">
        <text v-for="country in labels" :key="country.id"
          :x="transform.apply(country.position!)[0]" :y="transform.apply(country.position!)[1]">{{ country.name }}</text>
      </g>
    </svg>
    <div class="zoom-controls" aria-label="地图缩放">
      <button title="放大" aria-label="放大" @click="scaleBy(1.5)">＋</button>
      <button title="缩小" aria-label="缩小" @click="scaleBy(1 / 1.5)">−</button>
    </div>
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
.country:focus-visible { outline: none; stroke: #1767cf; stroke-width: 2; }
.labels { pointer-events: none; font: 12px 'Microsoft YaHei', sans-serif; fill: #354f5c; text-anchor: middle; dominant-baseline: middle; paint-order: stroke; stroke: #f8faf3; stroke-width: 3px; stroke-linejoin: round; }
.zoom-controls { position: absolute; right: 10px; top: 10px; display: grid; background: white; border-radius: 5px; box-shadow: 0 0 0 2px #00000018; overflow: hidden; }
button { width: 30px; height: 30px; border: 0; background: white; cursor: pointer; color: #394754; font-size: 22px; }
button + button { border-top: 1px solid #ddd; }
button:hover { background: #eef4fa; }
.map-caption, .attribution { position: absolute; bottom: 10px; font-size: 12px; background: #ffffffdc; color: #617487; padding: 3px 7px; border-radius: 4px; }
.map-caption { left: 10px; pointer-events: none; }
.attribution { right: 10px; }
a { color: inherit; }
</style>

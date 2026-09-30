<script setup lang="ts">
import { computed, ref } from 'vue'
import MaleBody from './MaleBody.vue'
import { views, type BodyMark, type BodyView } from './bodyMap'

/** 通用人体查看器：只负责视角、缩放、坐标拾取和叠加显示，由调用方解释标注内容。 */
const props = withDefaults(defineProps<{ marks?: BodyMark[]; selectedId?: string }>(), {
  marks: () => [], selectedId: '',
})
const emit = defineEmits<{
  add: [point: { view: BodyView; x: number; y: number }]
  select: [id: string]
  message: [text: string]
}>()
const svg = ref<SVGSVGElement>()
const view = ref<BodyView>('front')
const zoom = ref(1)
const center = ref({ x: 200, y: 400 })
const mode = ref<'mark' | 'pan'>('pan')
const visibleMarks = computed(() => props.marks.filter(m => m.view === view.value))
const width = computed(() => 500 / zoom.value)
const height = computed(() => 820 / zoom.value)
const box = computed(() => `${center.value.x - width.value / 2} ${center.value.y - height.value / 2} ${width.value} ${height.value}`)
function focusHead() { zoom.value = 3.7; center.value = { x: 200, y: 85 } }
function fullBody() { zoom.value = 1; center.value = { x: 200, y: 400 } }
function changeView(next: BodyView) {
  const wasTop = view.value === 'top'
  view.value = next
  if (next === 'top' || wasTop) focusHead()
}
function changeZoom(value: number) { zoom.value = Math.max(1, Math.min(7, value)) }
function point(event: PointerEvent | WheelEvent) {
  const matrix = svg.value?.getScreenCTM()
  if (!matrix) return null
  return new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse())
}
let drag: { id: number; x: number; y: number; cx: number; cy: number; scale: number; moved: boolean; pan: boolean } | null = null
function pointerDown(event: PointerEvent) {
  if (!event.isPrimary || event.button !== 0) return
  const matrix = svg.value?.getScreenCTM()
  if (!matrix) return
  drag = { id: event.pointerId, x: event.clientX, y: event.clientY, cx: center.value.x, cy: center.value.y, scale: matrix.a, moved: false, pan: mode.value === 'pan' }
  svg.value?.setPointerCapture(event.pointerId)
}
function pointerMove(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.id) return
  const dx = event.clientX - drag.x, dy = event.clientY - drag.y
  if (Math.hypot(dx, dy) > 5) drag.moved = true
  if (drag.pan) center.value = { x: drag.cx - dx / drag.scale, y: drag.cy - dy / drag.scale }
}
function pointerUp(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.id) return
  const add = !drag.moved && !drag.pan
  drag = null
  svg.value?.releasePointerCapture(event.pointerId)
  if (!add) return
  const p = point(event)
  if (!p || p.x < 50 || p.x > 350 || p.y < 15 || p.y > (view.value === 'top' ? 150 : 790)) {
    emit('message', '请在人体附近标注。'); return
  }
  emit('add', { view: view.value, x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10 })
}
function wheel(event: WheelEvent) {
  const p = point(event)
  if (!p) return
  const before = zoom.value
  changeZoom(before * (event.deltaY < 0 ? 1.12 : 1 / 1.12))
  const ratio = before / zoom.value
  center.value = { x: p.x - (p.x - center.value.x) * ratio, y: p.y - (p.y - center.value.y) * ratio }
}
function focusMark(mark: BodyMark) {
  view.value = mark.view
  if (mark.y < 150 || mark.view === 'top') focusHead()
  else fullBody()
}
defineExpose({ focusMark })
function download() {
  if (!svg.value) return
  const clone = svg.value.cloneNode(true) as SVGSVGElement
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', '1000'); clone.setAttribute('height', '1640')
  // SVG 内直接携带所有绘图属性，下载后不依赖网页样式或外部资源。
  const title = document.createElementNS('http://www.w3.org/2000/svg', 'text')
  title.setAttribute('x', String(center.value.x)); title.setAttribute('y', String(center.value.y - height.value / 2 + 16 / zoom.value))
  title.setAttribute('font-size', String(12 / zoom.value)); title.setAttribute('text-anchor', 'middle'); title.setAttribute('fill', '#374151')
  title.textContent = `男性体表 · ${views.find(v => v.id === view.value)?.label} · 左右按本人`
  clone.appendChild(title)
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml;charset=utf-8' }))
  const a = document.createElement('a'); a.href = url; a.download = `身体标注-${view.value}.svg`; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  emit('message', '已下载当前视角。')
}
</script>
<template>
      <section class="viewer" aria-label="人体标注画布">
        <nav class="view-tabs" aria-label="观察方向">
          <button v-for="item in views" :key="item.id" :aria-pressed="view === item.id" :class="{ active: view === item.id }" @click="changeView(item.id)">{{ item.label }}</button>
        </nav>
        <div class="toolbar">
          <button class="head-button" @click="focusHead">放大头部</button>
          <button :disabled="view === 'top'" @click="fullBody">全身</button>
          <span class="toolbar-divider"></span>
          <button aria-label="缩小" @click="changeZoom(zoom / 1.3)">−</button><span class="zoom-label">{{ zoom.toFixed(1) }}×</span><button aria-label="放大" @click="changeZoom(zoom * 1.3)">＋</button>
          <span class="toolbar-divider"></span>
          <button :aria-pressed="mode === 'mark'" :class="{ active: mode === 'mark' }" @click="mode = 'mark'">点选标注</button>
          <button :aria-pressed="mode === 'pan'" :class="{ active: mode === 'pan' }" @click="mode = 'pan'">拖动查看</button>
        </div>
        <div class="canvas-wrap">
          <div class="orientation"><span>{{ view === 'front' ? '本人右侧' : view === 'back' || view === 'top' ? '本人左侧' : '面部朝向 ←' }}</span><span>{{ view === 'front' ? '本人左侧' : view === 'back' || view === 'top' ? '本人右侧' : views.find(v => v.id === view)?.label }}</span></div>
          <svg ref="svg" class="body-canvas" :class="{ panning: mode === 'pan' }" :viewBox="box" aria-label="男性人体位置标注图" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="drag = null" @wheel.prevent="wheel">
            <rect :x="center.x - width / 2" :y="center.y - height / 2" :width="width" :height="height" fill="#f7f8f5" />
            <MaleBody :view="view" />
            <g v-for="mark in visibleMarks" :key="mark.id" role="button" tabindex="0" :aria-label="`标注 ${marks.indexOf(mark) + 1}：${mark.region}`" style="cursor: pointer" @pointerdown.stop @pointerup.stop="emit('select', mark.id)" @keydown.enter.prevent="emit('select', mark.id)" @keydown.space.prevent="emit('select', mark.id)">
              <circle :cx="mark.x" :cy="mark.y" :r="mark.radius" fill="#e07151" fill-opacity="0.26" :stroke="selectedId === mark.id ? '#983d28' : '#cf6848'" :stroke-width="selectedId === mark.id ? 2 : 1" />
              <circle :cx="mark.x" :cy="mark.y" :r="7 / Math.sqrt(zoom)" fill="#b74830" />
              <text :x="mark.x" :y="mark.y" text-anchor="middle" dominant-baseline="central" fill="white" :font-size="9 / Math.sqrt(zoom)" font-family="Arial, sans-serif" pointer-events="none">{{ marks.indexOf(mark) + 1 }}</text>
            </g>
          </svg>
          <div class="canvas-hint">{{ view === 'top' ? '头顶视角：上方为额头，下方为后脑' : mode === 'mark' ? '点击添加位置 · 滚轮缩放 · 支持多个标注' : '拖动画面查看 · 切回「点选标注」添加位置' }}</div>
        </div>
        <footer class="viewer-footer"><span>左右均按本人。体表示意仅用于描述位置。</span><button @click="download">下载当前图</button></footer>
      </section>
</template>
<style scoped>
.viewer{background:white;border:1px solid var(--line);border-radius:16px;overflow:hidden}
.view-tabs{display:flex;padding:13px 16px 10px;gap:5px;border-bottom:1px solid var(--line);flex-wrap:wrap}
button,input,select,textarea{font:inherit}
button{cursor:pointer;border:1px solid var(--line);border-radius:7px;background:white;color:var(--ink);font-size:12px;padding:8px 11px}
button:hover{background:#f0f5ef;border-color:#b7c6bd}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--green);outline-offset:2px}
button:disabled{opacity:.4;cursor:default}
.view-tabs button{border-color:transparent;flex:1;white-space:nowrap}
.view-tabs button.active,.toolbar button.active{background:#e8f0ea;color:var(--green);border-color:#ccdcd0}
.toolbar{display:flex;align-items:center;gap:6px;padding:11px 16px;flex-wrap:wrap}
.toolbar .head-button{background:var(--green);color:white;border-color:var(--green)}
.toolbar-divider{height:18px;border-left:1px solid var(--line);margin:0 3px}
.zoom-label{min-width:34px;text-align:center;font-size:12px;color:var(--muted)}
.canvas-wrap{position:relative;background:#f7f8f5}
.body-canvas{display:block;width:100%;height:clamp(460px,64vh,760px);touch-action:none;cursor:crosshair;user-select:none}
.body-canvas.panning{cursor:grab}
.body-canvas.panning:active{cursor:grabbing}
.orientation{position:absolute;left:22px;right:22px;top:16px;display:flex;justify-content:space-between;font-size:11px;letter-spacing:1px;color:#79897f;pointer-events:none}
.canvas-hint{position:absolute;bottom:13px;width:100%;text-align:center;font-size:11px;color:var(--muted);pointer-events:none}
.viewer-footer{padding:11px 16px;display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:11px;color:var(--muted)}
button{font:inherit;font-size:12px}
@media(max-width:1000px){.toolbar{padding:10px}.toolbar button{padding:7px 8px}}
@media(max-width:720px){.body-canvas{height:510px}.view-tabs{padding:9px 7px}.view-tabs button{padding:8px 6px;font-size:11px}.viewer-footer span{max-width:65%}}
</style>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { dockSides, type DockSide } from './dockLayout'
import type { DockController } from './useDockLayout'
import WorkbenchPane from '../layout/WorkbenchPane.vue'
import { dockGeometry, BOTTOM_DOCK_RAIL_SIZE } from './dockGeometry'
import DockRegion from './DockRegion.vue'
import DockToolMenu from './DockToolMenu.vue'
const props = withDefaults(defineProps<{
  dock: DockController
  contentOnly?: boolean
  /** 默认长文由中央 Pane 滚动；canvas 等明确自管滚动的组件才选择 hidden。 */
  contentScroll?: 'auto' | 'hidden'
}>(), { contentScroll: 'auto' })
const emit = defineEmits<{ resized: []; contextMenu: [event: MouseEvent] }>()
const workspaceId = `dock-${useId().replace(/[^\w-]/g, '')}`
const root = ref<HTMLElement>(), content = ref<HTMLElement>()
const toolMenu = ref<InstanceType<typeof DockToolMenu>>()
const ready = ref(false), dragging = ref<string | null>(null), resizing = ref(false)
const width = ref(1200), height = ref(800)
const mountedTools = ref(new Set<string>())
watch(() => dockSides.flatMap(side => props.dock.state.value.regions[side].active), ids => {
  for (const id of ids) if (id) mountedTools.value.add(id)
}, { immediate: true })
const geometry = computed(() => dockGeometry(props.dock.state.value, width.value, height.value, props.contentOnly, Boolean(dragging.value)))
function sideSize(side: DockSide) {
  const tracks = side === 'bottom' ? geometry.value.gridTemplateRows : geometry.value.gridTemplateColumns
  return Number.parseFloat(side === 'left' ? tracks.split(' ')[0]! : tracks.split(' ').slice(-1)[0]!)
}
let observer: ResizeObserver | undefined
let disposed = false
let frame = 0
function changed() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => { if (!resizing.value) emit('resized') })
}
let stopResize: (() => void) | undefined
function resize(event: PointerEvent, side: DockSide, pair?: [string, string]) {
  if (event.button !== 0) return
  event.preventDefault()
  stopResize?.()
  const target = event.currentTarget as HTMLElement
  const rect = target.parentElement!.getBoundingClientRect()
  const axis = pair ? (side === 'bottom' ? 'clientX' : 'clientY') : (side === 'bottom' ? 'clientY' : 'clientX')
  const origin = event[axis]
  const region = props.dock.state.value.regions[side]
  const weight = (id: string) => region.weights[id] ?? 1
  const pairWeight = pair ? weight(pair[0]) + weight(pair[1]) : 1
  const total = region.active.reduce((sum, id) => sum + weight(id), 0)
  const initial = pair ? weight(pair[0]) / pairWeight : sideSize(side) - (side === 'bottom' ? BOTTOM_DOCK_RAIL_SIZE : 38)
  const length = (side === 'bottom' ? rect.width : rect.height) * (pair ? pairWeight / total : 1)
  resizing.value = true
  function move(e: PointerEvent) {
    if (e.pointerId !== event.pointerId) return
    if (!(e.buttons & 1)) { finish(e); return }
    const delta = e[axis] - origin
    if (pair) props.dock.split(side, pair[0], pair[1], initial + delta / Math.max(1, length))
    else props.dock.resize(side, initial + delta * (side === 'left' ? 1 : -1))
  }
  function finish(e?: PointerEvent) {
    if (e && e.pointerId !== event.pointerId) return
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', finish)
    window.removeEventListener('pointercancel', finish)
    window.removeEventListener('blur', cancel)
    target.removeEventListener('lostpointercapture', finish)
    document.removeEventListener('visibilitychange', visibilityChanged)
    if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId)
    resizing.value = false; stopResize = undefined
    if (!disposed) changed()
  }
  function cancel() { finish() }
  function visibilityChanged() { if (document.hidden) finish() }
  stopResize = finish
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', finish)
  window.addEventListener('pointercancel', finish)
  window.addEventListener('blur', cancel)
  document.addEventListener('visibilitychange', visibilityChanged)
  target.addEventListener('lostpointercapture', finish)
  // Keep the full press/move/release sequence on the separator, even over an iframe.
  target.setPointerCapture(event.pointerId)
}
function placement(id: string) {
  const side = props.dock.position(id)!
  const region = props.dock.state.value.regions[side]
  const index = region.tools.filter(x => region.active.includes(x)).indexOf(id) + 1
  return { gridRow: side === 'bottom' ? 1 : Math.max(1, index), gridColumn: side === 'bottom' ? Math.max(1, index) : 1 }
}
onMounted(async () => {
  // 每个工具的 Teleport 身份不变，只改变目标；移动、切换与隐藏不会重建业务组件。
  // Measure before the first paint; the provisional width must not resize a newly opened tab.
  const initialRect = root.value?.getBoundingClientRect()
  if (initialRect?.width) { width.value = initialRect.width; height.value = initialRect.height }
  ready.value = true
  await nextTick()
  if (disposed) return
  observer = new ResizeObserver(entries => {
    for (const entry of entries) if (entry.target === root.value && entry.contentRect.width > 0 && entry.contentRect.height > 0) { width.value = entry.contentRect.width; height.value = entry.contentRect.height }
    changed()
  })
  if (root.value) observer.observe(root.value)
  if (content.value) observer.observe(content.value)
})
onBeforeUnmount(() => { disposed = true; observer?.disconnect(); stopResize?.(); cancelAnimationFrame(frame) })
</script>
<template>
  <div ref="root" class="dock-workspace" :class="{ 'is-resizing': resizing }" :style="geometry" @keydown.esc="dragging = null" v-context-menu="(event: MouseEvent) => emit('contextMenu', event)">
    <DockRegion v-for="side in dockSides" v-show="!contentOnly" :key="side" :dock="dock" :side="side" :workspace-id="workspaceId" :dragging="dragging"
      @drag="dragging = $event" @resize="resize" @menu="(event, id) => toolMenu?.open(event, id)" />
    <DockToolMenu ref="toolMenu" :dock="dock" />
    <main ref="content" class="reader-content">
      <WorkbenchPane :scroll="contentScroll">
        <template v-if="$slots['content-header']" #header><slot name="content-header" /></template>
        <slot />
      </WorkbenchPane>
    </main>
    <template v-if="ready">
      <template v-for="tool in dock.tools" :key="tool.id">
        <Teleport v-if="mountedTools.has(tool.id)" :to="`#${workspaceId}-${dock.position(tool.id)}`">
          <WorkbenchPane v-show="dock.visible(tool.id)" class="dock-tool" :style="placement(tool.id)" :data-dock-tool="tool.id" :aria-label="tool.title">
            <template v-if="dock.position(tool.id) !== 'bottom' || $slots[`${tool.id}-actions`]" #header><header class="dock-tool-heading">
              <strong>{{ tool.title }}</strong>
              <slot :name="`${tool.id}-actions`" />
            </header></template>
            <slot :name="tool.id" :active="dock.visible(tool.id)" />
          </WorkbenchPane>
        </Teleport>
      </template>
    </template>
  </div>
</template>
<style scoped>
.dock-workspace { --reader-content-top-inset: 12px; display: grid; grid-template-areas: 'left content right' 'left bottom right'; flex: 1; height: 100%; min-height: 0; min-width: 0; overflow: hidden; color: var(--reader-text, #273447); background: var(--reader-content, #fff); }
.reader-content { grid-area: content; position: relative; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }
.dock-tool-heading { display: flex; gap: 4px; align-items: center; flex: none; min-height: 34px; padding: 0 8px; border-bottom: 1px solid var(--reader-border, #e4e9ef);  }
.dock-tool-heading strong { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.is-resizing { user-select: none; }
</style>

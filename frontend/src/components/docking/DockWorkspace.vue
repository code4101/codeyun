<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { dockSides, type DockSide } from './dockLayout'
import type { DockController } from './useDockLayout'
import DockRegion from './DockRegion.vue'
import DockToolMenu from './DockToolMenu.vue'
const props = defineProps<{ dock: DockController; contentOnly?: boolean }>()
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
function sideSize(side: DockSide) {
  if (props.contentOnly) return 0
  const region = props.dock.state.value.regions[side]
  if (!region.visible) return 0
  const hasTools = region.tools.length > 0
  if (!hasTools && !dragging.value) return 0
  if (dragging.value && !props.dock.regionOpen(side)) return side === 'bottom' ? 80 : 110
  const rail = side === 'bottom' || width.value < 760 ? 32 : 38
  if (width.value < 760) return props.dock.regionOpen(side) || dragging.value ? height.value * .23 : rail
  if (!props.dock.regionOpen(side)) return rail
  return Math.min(region.size + rail, side === 'bottom' ? height.value * .45 : Math.max(rail, (width.value - 180) / 2))
}
const geometry = computed(() => width.value < 760 ? {
  gridTemplateColumns: 'minmax(0, 1fr)',
  gridTemplateRows: `${sideSize('left')}px minmax(0, 1fr) ${sideSize('right')}px ${sideSize('bottom')}px`,
} : {
  gridTemplateColumns: `${sideSize('left')}px minmax(0, 1fr) ${sideSize('right')}px`,
  gridTemplateRows: `minmax(0, 1fr) ${sideSize('bottom')}px`,
})
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
  const initial = pair ? weight(pair[0]) / pairWeight : sideSize(side) - (side === 'bottom' ? 32 : 38)
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
  <div ref="root" class="dock-workspace" :class="{ 'is-resizing': resizing, compact: width < 760 }" :style="geometry" @keydown.esc="dragging = null" v-context-menu="(event: MouseEvent) => emit('contextMenu', event)">
    <DockRegion v-for="side in dockSides" v-show="!contentOnly" :key="side" :dock="dock" :side="side" :workspace-id="workspaceId" :dragging="dragging"
      @drag="dragging = $event" @resize="resize" @menu="(event, id) => toolMenu?.open(event, id)" />
    <DockToolMenu ref="toolMenu" :dock="dock" />
    <main ref="content" class="reader-content"><slot /></main>
    <template v-if="ready">
      <template v-for="tool in dock.tools" :key="tool.id">
        <Teleport v-if="mountedTools.has(tool.id)" :to="`#${workspaceId}-${dock.position(tool.id)}`">
          <section v-show="dock.visible(tool.id)" class="dock-tool" :style="placement(tool.id)" :data-dock-tool="tool.id" :aria-label="tool.title">
            <header class="dock-tool-heading">
              <strong>{{ tool.title }}</strong>
            </header>
            <div class="dock-tool-content"><slot :name="tool.id" :active="dock.visible(tool.id)" /></div>
          </section>
        </Teleport>
      </template>
    </template>
  </div>
</template>
<style scoped>
.dock-workspace { --reader-content-top-inset: 12px; display: grid; grid-template-areas: 'left content right' 'left bottom right'; flex: 1; height: 100%; min-height: 0; min-width: 0; overflow: hidden; color: var(--reader-text, #273447); background: var(--reader-content, #fff); }
.reader-content { grid-area: content; position: relative; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }
.dock-tool { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.dock-tool-heading { display: flex; gap: 4px; align-items: center; flex: none; min-height: 34px; padding: 0 8px; border-bottom: 1px solid var(--reader-border, #e4e9ef);  }
.dock-tool-heading strong { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.dock-tool-content { display: flex; flex: 1; flex-direction: column; min-height: 0; overflow: auto; }
.dock-tool-content > :slotted(*) { min-width: 0; }
.is-resizing { user-select: none; }
.dock-workspace.compact { grid-template-areas: 'left' 'content' 'right' 'bottom'; }
.compact :deep(.dock-region) { flex-direction: column-reverse; }
.compact :deep(.dock-region.bottom) { flex-direction: column; }
.compact :deep(.dock-region .dock-rail) { width: 100%; height: 32px; flex-basis: 32px; flex-direction: row; border-left: 0; border-right: 0; border-top: 1px solid var(--dock-rail-border); }
.compact :deep(.dock-region.bottom .dock-rail) { border-top: 0; border-bottom: 1px solid var(--dock-rail-border); }
.compact :deep(.dock-rail-group) { flex-direction: row; min-height: 0; }
.compact :deep(.dock-rail button) { width: 38px; height: 31px; }
.compact :deep(.dock-edge) { display: none; }
</style>

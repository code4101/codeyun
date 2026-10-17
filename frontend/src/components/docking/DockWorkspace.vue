<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { dockSides, type DockSide } from './dockLayout'
import type { DockController } from './useDockLayout'
import WorkbenchPane from '../layout/WorkbenchPane.vue'
import { dockGeometry, BOTTOM_DOCK_RAIL_SIZE } from './dockGeometry'
import DockRegion from './DockRegion.vue'
import DockToolMenu from './DockToolMenu.vue'
import DockIcon from './DockIcon.vue'
const props = withDefaults(defineProps<{
  dock: DockController
  contentOnly?: boolean
  /** 窄屏工具改为临时浮层，保留桌面持久化布局。 */
  mobileTools?: boolean
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
const compact = computed(() => props.mobileTools && !props.contentOnly && width.value <= 600)
const mobileTool = ref<string | null>(null)
const mobilePanel = ref<HTMLElement>()
const mobileButtons = ref<HTMLElement>()
function closeMobileTool() {
  const id = mobileTool.value
  mobileTool.value = null
  nextTick(() => mobileButtons.value?.querySelector<HTMLButtonElement>(`[data-mobile-tool="${id}"]`)?.focus())
}
function openMobileTool(id: string) {
  mobileTool.value = mobileTool.value === id ? null : id
  if (mobileTool.value) {
    mountedTools.value.add(id)
    nextTick(() => mobilePanel.value?.focus())
  }
}
watch(compact, () => { mobileTool.value = null })
watch(props.dock.toolRequest, request => {
  if (!compact.value || !request) return
  mobileTool.value = request.id
  mountedTools.value.add(request.id)
  nextTick(() => mobilePanel.value?.focus())
})
const toolVisible = (id: string) => compact.value ? mobileTool.value === id : props.dock.visible(id)
watch(() => dockSides.flatMap(side => props.dock.state.value.regions[side].active), ids => {
  for (const id of ids) if (id) mountedTools.value.add(id)
}, { immediate: true })
const geometry = computed(() => dockGeometry(props.dock.state.value, width.value, height.value, props.contentOnly || compact.value, Boolean(dragging.value)))
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
  <div ref="root" class="dock-workspace" :class="{ 'is-resizing': resizing, 'is-mobile-tools': compact }" :style="geometry" @keydown.esc="dragging = null; mobileTool && closeMobileTool()" v-context-menu="(event: MouseEvent) => emit('contextMenu', event)">
    <DockRegion v-for="side in dockSides" v-show="!contentOnly && !compact" :key="side" :dock="dock" :side="side" :workspace-id="workspaceId" :dragging="dragging"
      @drag="dragging = $event" @resize="resize" @menu="(event, id) => toolMenu?.open(event, id)" />
    <DockToolMenu ref="toolMenu" :dock="dock" />
    <main ref="content" class="reader-content">
      <nav v-if="compact" ref="mobileButtons" class="mobile-tools-bar" aria-label="阅读工具">
        <button v-for="tool in dock.tools" :key="tool.id" type="button" :data-mobile-tool="tool.id" :title="tool.title" :aria-label="tool.title" :aria-expanded="mobileTool === tool.id" :aria-controls="`${workspaceId}-mobile-panel`" @click="openMobileTool(tool.id)">
          <DockIcon :icon="tool.icon" /><span>{{ tool.title }}</span>
        </button>
      </nav>
      <WorkbenchPane :scroll="contentScroll">
        <template v-if="$slots['content-header']" #header><slot name="content-header" /></template>
        <slot />
      </WorkbenchPane>
    </main>
    <div v-show="compact && mobileTool" class="mobile-tool-overlay">
      <button class="mobile-tool-backdrop" type="button" aria-label="收起阅读工具" tabindex="-1" @click="closeMobileTool" />
      <section :id="`${workspaceId}-mobile-panel`" ref="mobilePanel" class="mobile-tool-panel" role="region" :aria-label="dock.tool(mobileTool)?.title" tabindex="-1">
        <header class="dock-tool-heading"><strong>{{ dock.tool(mobileTool)?.title }}</strong><button type="button" class="mobile-tool-close" aria-label="收起工具，返回阅读" @click="closeMobileTool">返回阅读 ×</button></header>
        <div :id="`${workspaceId}-mobile`" class="mobile-tool-content" />
      </section>
    </div>
    <template v-if="ready">
      <template v-for="tool in dock.tools" :key="tool.id">
        <Teleport v-if="mountedTools.has(tool.id)" :to="`#${workspaceId}-${compact ? 'mobile' : dock.position(tool.id)}`">
          <WorkbenchPane v-show="toolVisible(tool.id)" class="dock-tool" :style="compact ? {} : placement(tool.id)" :data-dock-tool="tool.id" :aria-label="tool.title">
            <template v-if="compact ? $slots[`${tool.id}-actions`] : dock.position(tool.id) !== 'bottom' || $slots[`${tool.id}-actions`]" #header><header class="dock-tool-heading">
              <strong>{{ tool.title }}</strong>
              <slot :name="`${tool.id}-actions`" />
            </header></template>
            <slot :name="tool.id" :active="toolVisible(tool.id)" />
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
.is-mobile-tools { position: relative; }
.mobile-tools-bar { display: flex; flex: none; gap: 2px; min-width: 0; height: 42px; overflow-x: auto; border-bottom: 1px solid var(--reader-border, #e4e9ef); background: var(--reader-panel, #f7f9fb); }
.mobile-tools-bar button { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 2px; flex: 1 0 auto; min-width: 40px; padding: 3px 4px; border: 0; background: transparent; color: var(--reader-text, #273447); font: inherit; font-size: 10px; white-space: nowrap; cursor: pointer; }
.mobile-tools-bar button[aria-expanded=true] { background: var(--reader-hover, #edf2f7); color: var(--reader-link, #2368d1); }
.mobile-tool-overlay { position: absolute; inset: 42px 0 0; z-index: 25; }
.mobile-tool-backdrop { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; background: #0004; }
.mobile-tool-panel { position: absolute; inset: 0 0 0 auto; display: flex; flex-direction: column; width: min(100%, 420px); min-height: 0; background: var(--reader-panel, #f7f9fb); box-shadow: -4px 0 20px #0002; }
.mobile-tool-content { display: flex; flex: 1; flex-direction: column; min-height: 0; min-width: 0; overflow: hidden; }
.mobile-tool-content > .dock-tool { flex: 1; min-height: 0; }
.mobile-tool-close { min-height: 38px; padding: 0 10px; border: 0; color: var(--reader-link, #2368d1); background: transparent; cursor: pointer; }
</style>

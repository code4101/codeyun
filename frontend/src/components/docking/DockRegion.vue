<script setup lang="ts">
import { computed } from 'vue'
import { dockSideLabels, type DockSide } from './dockLayout'
import type { DockController } from './useDockLayout'
import DockIcon from './DockIcon.vue'
const props = defineProps<{ dock: DockController; side: DockSide; workspaceId: string; dragging: string | null }>()
const emit = defineEmits<{ menu: [event: MouseEvent, id: string]; drag: [id: string | null]; resize: [event: PointerEvent, side: DockSide, pair?: [string, string]] }>()
const region = computed(() => props.dock.state.value.regions[props.side])
const opened = computed(() => region.value.tools.filter(id => region.value.active.includes(id)))
const grid = computed(() => ({
  [props.side === 'bottom' ? 'gridTemplateColumns' : 'gridTemplateRows']: opened.value.map(id => `minmax(0, ${region.value.weights[id] ?? 1}fr)`).join(' '),
}))
const boundaries = computed(() => {
  const total = opened.value.reduce((sum, id) => sum + (region.value.weights[id] ?? 1), 0)
  let offset = 0
  return opened.value.slice(0, -1).map((id, index) => {
    const next = opened.value[index + 1]!
    const weight = region.value.weights[id] ?? 1
    offset += weight
    return { id, next, offset: offset / total * 100, ratio: weight / (weight + (region.value.weights[next] ?? 1)) }
  })
})
function startDrag(event: DragEvent, id: string) {
  if (!event.dataTransfer) return
  event.dataTransfer.setData('text/x-codeyun-dock', id)
  event.dataTransfer.effectAllowed = 'move'
  emit('drag', id)
}
function drop(event: DragEvent, before?: string) {
  if (props.dragging && event.dataTransfer?.getData('text/x-codeyun-dock') === props.dragging) props.dock.move(props.dragging, props.side, before)
  emit('drag', null)
}
</script>
<template>
  <section v-show="region.visible && (region.tools.length || dragging)" class="dock-region" :class="[side, { 'has-open': dock.regionOpen(side) }]" :aria-label="`${dockSideLabels[side]}工具区`">
    <nav class="dock-rail" :aria-label="`${dockSideLabels[side]}工具栏`" @dragover.prevent @drop.prevent="drop($event)">
      <div class="dock-rail-group">
        <button v-for="id in region.tools" :key="id" type="button" draggable="true" :title="`${dock.tool(id)?.title}（Ctrl＋单击添加或收起）`"
          v-context-menu="(event: MouseEvent) => emit('menu', event, id)"
          :aria-label="dock.tool(id)?.title" :aria-pressed="dock.visible(id)" @click="dock.toggle(id, $event.ctrlKey || $event.metaKey)"
          @dragstart="startDrag($event, id)" @dragend="emit('drag', null)" @dragover.prevent @drop.stop.prevent="drop($event, id)">
          <DockIcon :icon="dock.tool(id)!.icon" />
        </button>
      </div>
    </nav>
    <div :id="`${workspaceId}-${side}`" v-show="dock.regionOpen(side)" class="dock-groups" :style="grid">
      <div v-for="boundary in boundaries" :key="boundary.id" class="dock-splitter" :class="side" role="separator" tabindex="0" :aria-label="`调整${dock.tool(boundary.id)?.title}与${dock.tool(boundary.next)?.title}比例`"
        :aria-orientation="side === 'bottom' ? 'vertical' : 'horizontal'" :aria-valuenow="Math.round(boundary.ratio * 100)" :aria-valuemin="10" :aria-valuemax="90"
        :style="{ [side === 'bottom' ? 'left' : 'top']: `${boundary.offset}%` }" @pointerdown="emit('resize', $event, side, [boundary.id, boundary.next])"
        @keydown.down.prevent="dock.split(side, boundary.id, boundary.next, boundary.ratio + .05)" @keydown.right.prevent="dock.split(side, boundary.id, boundary.next, boundary.ratio + .05)"
        @keydown.up.prevent="dock.split(side, boundary.id, boundary.next, boundary.ratio - .05)" @keydown.left.prevent="dock.split(side, boundary.id, boundary.next, boundary.ratio - .05)"
        @keydown.home.prevent="dock.split(side, boundary.id, boundary.next, .5)" @dblclick="dock.split(side, boundary.id, boundary.next, .5)" />
    </div>
    <div v-if="dock.regionOpen(side)" class="dock-edge" :class="side" role="separator" tabindex="0" :aria-label="`调整${dockSideLabels[side]}区域大小`"
      :aria-orientation="side === 'bottom' ? 'horizontal' : 'vertical'" :aria-valuenow="region.size" :aria-valuemin="160" :aria-valuemax="900"
      @pointerdown="emit('resize', $event, side)" @keydown.right.prevent="dock.resize(side, region.size + (side === 'right' ? -20 : 20))"
      @keydown.left.prevent="dock.resize(side, region.size + (side === 'right' ? 20 : -20))"
      @keydown.up.prevent="dock.resize(side, region.size + 20)" @keydown.down.prevent="dock.resize(side, region.size - 20)" />
    <div v-if="dragging" class="dock-drop-zones" @dragover.prevent @drop.prevent="drop($event)"><div>{{ dockSideLabels[side] }}</div></div>
  </section>
</template>
<style scoped>
.dock-region { --dock-rail-border: color-mix(in srgb, var(--reader-text, #273447) 22%, var(--reader-panel, #f7f9fb)); display: flex; position: relative; min-height: 0; min-width: 0; background: var(--reader-panel, #f7f9fb); }
.dock-region.left { grid-area: left; border-right: 1px solid var(--reader-border, #e4e9ef); }
.dock-region.right { grid-area: right; flex-direction: row-reverse; border-left: 1px solid var(--reader-border, #e4e9ef); }
.dock-region.bottom { grid-area: bottom; flex-direction: column-reverse; border-top: 1px solid var(--reader-border, #e4e9ef); }
.dock-rail { box-sizing: border-box; display: flex; flex-direction: column; flex: 0 0 38px; width: 38px; overflow: auto; background: color-mix(in srgb, var(--reader-panel, #f7f9fb) 94%, var(--reader-text, #273447)); }
.left .dock-rail { border-right: 1px solid var(--dock-rail-border); }
.right .dock-rail { border-left: 1px solid var(--dock-rail-border); }
.bottom .dock-rail { border-top: 1px solid var(--dock-rail-border); }
.dock-rail-group { display: flex; flex-direction: column; flex: 1; min-height: 38px; }
.dock-rail button { display: grid; place-items: center; flex: 0 0 38px; width: 100%; height: 38px; border: 0; border-left: 2px solid transparent; background: transparent; color: var(--reader-muted, #657286); cursor: pointer; }
.dock-rail button[aria-pressed=true] { color: var(--reader-active-text, #2368d1); border-left-color: currentColor; background: var(--reader-hover, #edf2f7); }
.bottom .dock-rail { width: 100%; height: 32px; flex-basis: 32px; flex-direction: row; }
.bottom .dock-rail-group { flex-direction: row; min-height: 0; }
.bottom .dock-rail button { width: 38px; height: 31px; }
.dock-groups { display: grid; position: relative; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.dock-edge, .dock-splitter { position: absolute; z-index: 10; touch-action: none; }
.dock-edge.left { top: 0; bottom: 0; right: -4px; width: 8px; cursor: col-resize; }
.dock-edge.right { top: 0; bottom: 0; left: -4px; width: 8px; cursor: col-resize; }
.dock-edge.bottom { top: -4px; left: 0; right: 0; height: 8px; cursor: row-resize; }
.dock-splitter { left: 0; right: 0; height: 6px; transform: translateY(-3px); cursor: row-resize; background: var(--reader-border, #e4e9ef); }
.dock-splitter.bottom { top: 0; bottom: 0; right: auto; width: 6px; height: auto; transform: translateX(-3px); cursor: col-resize; }
.dock-edge:hover, .dock-splitter:hover, [role=separator]:focus-visible { background: var(--reader-active-text, #2368d1); opacity: .5; }
button:hover { background: var(--reader-hover, #edf2f7); }
button:focus-visible { outline: 2px solid var(--reader-link, #2368d1); outline-offset: -2px; }
.dock-drop-zones { position: absolute; inset: 0 0 0 38px; z-index: 20; display: flex; flex-direction: column; gap: 6px; padding: 6px; }
.right .dock-drop-zones { inset: 0 38px 0 0; }
.bottom .dock-drop-zones { inset: 0 0 32px; flex-direction: row; }
.dock-drop-zones > div { display: grid; place-items: center; flex: 1; min-width: 0; font-size: 12px; border: 2px dashed var(--reader-link, #2368d1); color: var(--reader-text, #273447); background: var(--reader-panel, #f7f9fb); opacity: .9; }
.dock-drop-zones > div:hover { background: var(--reader-hover, #edf2f7); }
</style>

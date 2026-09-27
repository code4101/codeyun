<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { dockSideLabels, dockSides, type DockSide } from './dockLayout'
import type { DockController } from './useDockLayout'
const props = defineProps<{ dock: DockController }>()
const target = ref<string | null>(null)
const position = ref({ x: 0, y: 0 })
const root = ref<HTMLElement>()
const submenu = ref<HTMLElement>()
const expanded = ref(false)
const opensLeft = ref(false)
let trigger: HTMLElement | null = null
const destinations = computed(() => dockSides.filter(side => side !== (target.value ? props.dock.position(target.value) : undefined)))
function close() { target.value = null; expanded.value = false }
async function open(event: MouseEvent, id: string) {
  event.preventDefault(); event.stopPropagation()
  trigger = event.currentTarget as HTMLElement
  position.value = { x: Math.max(4, Math.min(event.clientX, window.innerWidth - 168)), y: Math.max(4, Math.min(event.clientY, window.innerHeight - 100)) }
  expanded.value = false
  opensLeft.value = position.value.x + 320 > window.innerWidth - 4
  target.value = id
  await nextTick()
  root.value?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
}
async function expand(focus = false) {
  expanded.value = true
  if (focus) { await nextTick(); submenu.value?.querySelector<HTMLElement>('button')?.focus() }
}
function collapse() { expanded.value = false; root.value?.querySelector<HTMLElement>('button')?.focus() }
function navigate(event: KeyboardEvent) {
  const buttons = Array.from(submenu.value?.querySelectorAll<HTMLButtonElement>('button') ?? [])
  const index = buttons.indexOf(event.target as HTMLButtonElement)
  buttons[(index + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length]?.focus()
}
function select(side: string) {
  if (target.value && destinations.value.includes(side as DockSide)) props.dock.move(target.value, side as DockSide)
  close()
}
function outside(event: Event) {
  const node = event.target
  if (node instanceof Element && root.value?.contains(node)) return
  close()
}
function key(event: KeyboardEvent) { if (event.key === 'Escape' && target.value) { close(); trigger?.focus() } }
onMounted(() => {
  window.addEventListener('pointerdown', outside)
  window.addEventListener('keydown', key)
  window.addEventListener('resize', close)
  window.addEventListener('scroll', outside, true)
})
onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', outside)
  window.removeEventListener('keydown', key)
  window.removeEventListener('resize', close)
  window.removeEventListener('scroll', outside, true)
})
defineExpose({ open })
</script>
<template>
  <Teleport to="body">
    <div v-if="target" ref="root" class="dock-tool-menu" role="menu" :style="{ left: `${position.x}px`, top: `${position.y}px` }" :aria-label="`${dock.tool(target)?.title}工具菜单`" @contextmenu.prevent @mouseleave="expanded = false">
      <button type="button" role="menuitem" aria-haspopup="menu" :aria-expanded="expanded" class="dock-menu-item" @mouseenter="expand()" @click="expand(true)" @keydown.right.prevent="expand(true)" @keydown.down.prevent="expand(true)">
        <span>移动</span><span aria-hidden="true">›</span>
      </button>
      <div v-if="expanded" ref="submenu" class="dock-move-submenu" :class="{ 'opens-left': opensLeft }" role="menu" aria-label="移动到" @keydown.left.prevent.stop="collapse" @keydown.up.prevent="navigate" @keydown.down.prevent="navigate">
        <button v-for="side in destinations" :key="side" type="button" role="menuitem" class="dock-menu-item" @click="select(side)">{{ dockSideLabels[side] }}</button>
      </div>
    </div>
  </Teleport>
</template>
<style scoped>
.dock-tool-menu, .dock-move-submenu { box-sizing: border-box; width: 160px; padding: 4px; border: 1px solid var(--el-border-color-light, #ddd); border-radius: 5px; background: var(--el-bg-color-overlay, #fff); color: var(--el-text-color-primary, #303133); box-shadow: var(--el-box-shadow-light, 0 4px 18px #0002); }
.dock-tool-menu { position: fixed; z-index: 10000; }
/* 子菜单独立侧向弹出；贴合父菜单边缘，鼠标移动时没有断开的悬停区域。 */
.dock-move-submenu { position: absolute; left: 100%; top: -1px; }
.dock-move-submenu.opens-left { left: auto; right: 100%; }
.dock-menu-item { display: flex; align-items: center; justify-content: space-between; width: 100%; min-height: 34px; padding: 6px 12px; border: 0; border-radius: 3px; background: transparent; color: inherit; text-align: left; font: inherit; font-size: 13px; cursor: pointer; }
.dock-menu-item:hover, .dock-menu-item:focus-visible, .dock-menu-item[aria-expanded=true] { background: var(--el-fill-color-light, #f5f7fa); outline: none; }
</style>

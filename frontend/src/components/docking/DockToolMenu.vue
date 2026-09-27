<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { dockSideLabels, dockSides, type DockSide } from './dockLayout'
import type { DockController } from './useDockLayout'
const props = defineProps<{ dock: DockController }>()
const target = ref<string | null>(null)
const visible = ref(false)
const theme = ref<Record<string, string>>({})
const position = ref({ x: 0, y: 0 })
const root = ref<HTMLElement>()
const submenu = ref<HTMLElement>()
const expanded = ref(false)
const opensLeft = ref(false)
let trigger: HTMLElement | null = null
const destinations = computed(() => dockSides.filter(side => side !== (target.value ? props.dock.position(target.value) : undefined)))
function close() { visible.value = false; target.value = null; expanded.value = false }
async function open(event: MouseEvent, id?: string) {
  event.preventDefault(); event.stopPropagation()
  trigger = event.currentTarget as HTMLElement
  position.value = { x: Math.max(4, Math.min(event.clientX, window.innerWidth - 168)), y: Math.max(4, Math.min(event.clientY, window.innerHeight - 100)) }
  expanded.value = false
  opensLeft.value = position.value.x + 320 > window.innerWidth - 4
  target.value = id ?? null
  const source = event.currentTarget instanceof HTMLElement ? event.currentTarget : event.target as HTMLElement
  const style = getComputedStyle(source)
  theme.value = Object.fromEntries(['--reader-panel', '--reader-text', '--reader-border', '--reader-hover'].map(name => [name, style.getPropertyValue(name).trim()]))
  visible.value = true
  await nextTick()
  const bounds = root.value?.getBoundingClientRect()
  if (bounds) position.value = { x: Math.max(4, Math.min(position.value.x, window.innerWidth - bounds.width - 4)), y: Math.max(4, Math.min(position.value.y, window.innerHeight - bounds.height - 4)) }
  root.value?.querySelector<HTMLElement>('button')?.focus()
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
function key(event: KeyboardEvent) { if (event.key === 'Escape' && visible.value) { close(); trigger?.focus() } }
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
    <div v-if="visible" ref="root" class="dock-tool-menu" role="menu" :style="{ ...theme, left: `${position.x}px`, top: `${position.y}px` }" :aria-label="target ? `${dock.tool(target)?.title}工具菜单` : '活动栏菜单'" @contextmenu.prevent @mouseleave="expanded = false">
      <button v-if="target" type="button" role="menuitem" aria-haspopup="menu" :aria-expanded="expanded" class="dock-menu-item" @mouseenter="expand()" @click="expand(true)" @keydown.right.prevent="expand(true)" @keydown.down.prevent="expand(true)">
        <span>移动</span><span aria-hidden="true">›</span>
      </button>
      <template v-if="!target">
        <button v-for="side in dockSides" :key="side" type="button" role="menuitemcheckbox" :aria-checked="dock.state.value.regions[side].visible" class="dock-menu-item" @click="dock.toggleRegion(side); close()">
          <span>{{ dockSideLabels[side] }}区域</span><span aria-hidden="true">{{ dock.state.value.regions[side].visible ? '✓' : '' }}</span>
        </button>
        <button type="button" role="menuitem" class="dock-menu-item" @click="dock.reset(); close()">恢复默认布局</button>
      </template>
      <div v-if="expanded" ref="submenu" class="dock-move-submenu" :class="{ 'opens-left': opensLeft }" role="menu" aria-label="移动到" @keydown.left.prevent.stop="collapse" @keydown.up.prevent="navigate" @keydown.down.prevent="navigate">
        <button v-for="side in destinations" :key="side" type="button" role="menuitem" class="dock-menu-item" @click="select(side)">{{ dockSideLabels[side] }}</button>
      </div>
    </div>
  </Teleport>
</template>
<style scoped>
.dock-tool-menu, .dock-move-submenu { box-sizing: border-box; width: 160px; padding: 4px; border: 1px solid var(--reader-border, var(--el-border-color-light, #ddd)); border-radius: 5px; background: var(--reader-panel, var(--el-bg-color-overlay, #fff)); color: var(--reader-text, var(--el-text-color-primary, #303133)); box-shadow: var(--el-box-shadow-light, 0 4px 18px #0002); }
.dock-tool-menu { position: fixed; z-index: 10000; }
/* 子菜单独立侧向弹出；贴合父菜单边缘，鼠标移动时没有断开的悬停区域。 */
.dock-move-submenu { position: absolute; left: 100%; top: -1px; }
.dock-move-submenu.opens-left { left: auto; right: 100%; }
.dock-menu-item { display: flex; align-items: center; justify-content: space-between; width: 100%; min-height: 34px; padding: 6px 12px; border: 0; border-radius: 3px; background: transparent; color: inherit; text-align: left; font: inherit; font-size: 13px; cursor: pointer; }
.dock-menu-item:hover, .dock-menu-item:focus-visible, .dock-menu-item[aria-expanded=true] { background: var(--reader-hover, var(--el-fill-color-light, #f5f7fa)); outline: none; }
</style>

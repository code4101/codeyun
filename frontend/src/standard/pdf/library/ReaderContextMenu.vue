<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMenu, ElMenuItem, ElSubMenu } from 'element-plus'
import { LIBRARY_READER_THEME_OPTIONS, libraryReaderTheme, libraryReaderThemeClass } from './readerTheme'
import { dockSides, dockSideLabels, type DockSide } from '@/components/docking/dockLayout'
import type { DockController } from '@/components/docking/useDockLayout'

const props = defineProps<{ dock?: DockController; fontControls?: boolean; canIncrease?: boolean; canDecrease?: boolean; canEdit?: boolean; cropControls?: boolean; cropEnabled?: boolean }>()
const emit = defineEmits<{ font: [delta: number]; edit: []; crop: [] }>()
const visible = ref(false)
const position = ref({ x: 0, y: 0 })
const root = ref<HTMLElement>()
function close() { visible.value = false }
async function open(event: MouseEvent) {
  // Text selection and annotation actions keep their own context handling.
  if (event.defaultPrevented || (event.target as HTMLElement)?.closest('textarea,input,[contenteditable="true"]')) return
  event.preventDefault()
  position.value = { x: Math.max(4, Math.min(event.clientX, window.innerWidth - 204)), y: Math.max(4, Math.min(event.clientY, window.innerHeight - 150)) }
  visible.value = true
  await nextTick()
  const height = root.value?.getBoundingClientRect().height ?? 0
  position.value.y = Math.max(4, Math.min(position.value.y, window.innerHeight - height - 4))
  root.value?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
}
function select(command: string) {
  if (command === 'layout:reset') props.dock?.reset()
  else if (command.startsWith('layout:region:')) {
    const side = command.slice('layout:region:'.length) as DockSide
    if (dockSides.includes(side)) props.dock?.toggleRegion(side)
  }
  else if (command.startsWith('layout:tool:')) props.dock?.toggle(command.slice('layout:tool:'.length), true)
  else if (command === 'larger') emit('font', 1)
  else if (command === 'smaller') emit('font', -1)
  else if (command === 'edit') emit('edit')
  else if (command === 'crop') emit('crop')
  else {
    const theme = LIBRARY_READER_THEME_OPTIONS.find(item => item.value === command)
    if (theme) libraryReaderTheme.value = theme.value
  }
  close()
}
function outside(event: Event) {
  const target = event.target
  if (target instanceof Element && (root.value?.contains(target) || target.closest('.reader-context-submenu'))) return
  close()
}
function keydown(event: KeyboardEvent) { if (event.key === 'Escape') close() }
onMounted(() => {
  window.addEventListener('pointerdown', outside)
  window.addEventListener('keydown', keydown)
  window.addEventListener('resize', close)
  window.addEventListener('scroll', outside, true)
})
onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', outside)
  window.removeEventListener('keydown', keydown)
  window.removeEventListener('resize', close)
  window.removeEventListener('scroll', outside, true)
})
defineExpose({ open })
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" ref="root" class="reader-context-menu library-reader-theme-dialog" :class="libraryReaderThemeClass" :style="{ left: `${position.x}px`, top: `${position.y}px` }" @contextmenu.prevent>
      <ElMenu collapse :collapse-transition="false" :default-active="libraryReaderTheme" @select="select">
        <ElSubMenu v-if="dock" index="layout" :popper-class="`reader-context-submenu library-reader-theme-dialog ${libraryReaderThemeClass}`" :show-timeout="0">
          <template #title><div class="submenu-title">阅读布局 <span>›</span></div></template>
          <ElMenuItem v-for="side in dockSides" :key="side" :index="`layout:region:${side}`" role="menuitemcheckbox" :aria-checked="dock.state.value.regions[side].visible">
            <span class="layout-check" aria-hidden="true">{{ dock.state.value.regions[side].visible ? '✓' : '' }}</span>{{ dockSideLabels[side] }}区域
          </ElMenuItem>
          <ElMenuItem v-for="(tool, index) in dock.tools" :key="tool.id" :index="`layout:tool:${tool.id}`" :class="{ 'layout-divider': index === 0 }" role="menuitemcheckbox" :aria-checked="dock.visible(tool.id)">
            <span class="layout-check" aria-hidden="true">{{ dock.visible(tool.id) ? '✓' : '' }}</span>{{ tool.title }}
          </ElMenuItem>
          <ElMenuItem index="layout:reset" class="layout-divider">恢复默认布局</ElMenuItem>
        </ElSubMenu>
        <ElSubMenu v-if="fontControls" index="font" :popper-class="`reader-context-submenu library-reader-theme-dialog ${libraryReaderThemeClass}`" :show-timeout="0">
          <template #title><div class="submenu-title">字体大小 <span>›</span></div></template>
          <ElMenuItem index="larger" :disabled="!canIncrease">放大</ElMenuItem>
          <ElMenuItem index="smaller" :disabled="!canDecrease">缩小</ElMenuItem>
        </ElSubMenu>
        <ElSubMenu index="theme" :popper-class="`reader-context-submenu library-reader-theme-dialog ${libraryReaderThemeClass}`" :show-timeout="0">
          <template #title><div class="submenu-title">阅读主题 <span>›</span></div></template>
          <ElMenuItem v-for="theme in LIBRARY_READER_THEME_OPTIONS" :key="theme.value" :index="theme.value">{{ theme.label }}</ElMenuItem>
        </ElSubMenu>
        <ElMenuItem v-if="canEdit" index="edit">编辑正文</ElMenuItem>
        <ElMenuItem v-if="cropControls" index="crop">{{ cropEnabled ? '恢复完整页面' : '裁剪空白边缘' }}</ElMenuItem>
      </ElMenu>
    </div>
  </Teleport>
</template>

<style scoped>
.reader-context-menu { position: fixed; z-index: 10000; width: 196px; border: 1px solid var(--reader-border); border-radius: 5px; padding: 4px 0; box-shadow: 0 4px 18px #0002; }
.reader-context-menu :deep(.el-menu) { border: 0; width: 100%; }
.submenu-title { display: flex; justify-content: space-between; width: 100%; }
.layout-check { display: inline-block; width: 20px; flex: none; }
.layout-divider { border-top: 1px solid var(--reader-border, #e4e9ef); }
:global(.reader-context-menu), :global(.reader-context-submenu) { --el-menu-item-height: 36px; --el-menu-sub-item-height: 36px; --el-menu-bg-color: var(--reader-surface); --el-menu-text-color: var(--reader-text); --el-menu-hover-bg-color: var(--reader-hover); --el-menu-active-color: var(--reader-active-text); }
</style>

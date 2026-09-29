<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMenu } from 'element-plus'
import WorkspaceMenuItems from './WorkspaceMenuItems.vue'
import type { WorkspaceMenuItem } from './workspaceMenu'
const props = defineProps<{ items: WorkspaceMenuItem[]; disabled?: boolean }>()
const emit = defineEmits<{ select: [id: string]; refresh: [] }>()
const menu = ref<InstanceType<typeof ElMenu>>()
const root = ref<HTMLElement>()
let pinned: string[] = []
const timers = new Map<string, ReturnType<typeof setTimeout>>()
function clearTimers() { timers.forEach(clearTimeout); timers.clear() }
function close() { clearTimers(); pinned = []; props.items.forEach(item => menu.value?.close(item.id)) }
function enter(path: string[]) {
  path.forEach(id => { clearTimeout(timers.get(id)); timers.delete(id) })
  // A pinned branch stays visible when the pointer happens to cross its siblings.
  if (pinned.some((id, index) => index < path.length && path[index] !== id)) return
  emit('refresh')
  path.forEach(id => menu.value?.open(id))
}
function leave(path: string[]) {
  const id = path[path.length - 1]
  if (pinned.includes(id)) return
  clearTimeout(timers.get(id))
  timers.set(id, setTimeout(() => { menu.value?.close(id); timers.delete(id) }, 200))
}
function pin(event: MouseEvent) {
  const title = (event.target as Element).closest('.el-sub-menu__title')
  const raw = title?.parentElement?.dataset.menuPath
  if (!raw) return
  event.preventDefault(); event.stopPropagation(); clearTimers()
  const path = JSON.parse(raw) as string[]
  if (pinned.at(-1) === path.at(-1)) { close(); return }
  pinned = path
  path.forEach(id => menu.value?.open(id))
}
function outside(event: PointerEvent) { if (!root.value?.contains(event.target as Node)) close() }
function escape(event: KeyboardEvent) { if (event.key === 'Escape') close() }
function select(id: string) { close(); emit('select', id) }
onMounted(() => { window.addEventListener('blur', close); document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape) })
onBeforeUnmount(() => { clearTimers(); window.removeEventListener('blur', close); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) })
</script>
<template>
  <header ref="root" class="workspace-menu" @click.capture="pin" @pointerdown="$emit('refresh')" @focusin="$emit('refresh')">
    <ElMenu ref="menu" mode="horizontal" :ellipsis="false" :default-active="''" :unique-opened="true" menu-trigger="click" aria-label="工作区菜单" @select="select">
      <WorkspaceMenuItems :items="items" :disabled="disabled" @enter="enter" @leave="leave" />
    </ElMenu>
  </header>
</template>
<style scoped>
.workspace-menu{flex:0 0 32px;height:32px;position:relative;z-index:30;background:var(--reader-panel);color:var(--reader-text);border-bottom:1px solid var(--reader-border);--el-menu-horizontal-height:32px;--el-menu-bg-color:var(--reader-panel);--el-menu-text-color:var(--reader-text);--el-menu-hover-bg-color:var(--reader-hover);--el-menu-active-color:var(--reader-text);--el-menu-hover-text-color:var(--reader-text);--el-bg-color-overlay:var(--reader-panel);--el-border-color-light:var(--reader-border);--el-text-color-primary:var(--reader-text)}
.workspace-menu :deep(.el-menu){border:0;background:var(--reader-panel)}
.workspace-menu :deep(.el-sub-menu__title),.workspace-menu :deep(.el-menu-item){font-size:13px;height:32px;line-height:32px;border-bottom:0!important}
.workspace-menu :deep(.el-menu--horizontal > .el-sub-menu > .el-sub-menu__title){padding:0 12px}
.workspace-menu :deep(.el-menu--horizontal > .el-sub-menu > .el-sub-menu__title .el-sub-menu__icon-arrow){display:none}
.workspace-menu :deep(.el-popper){border-color:var(--reader-border)}
.workspace-menu :deep(.workspace-menu-separator){height:1px;margin:4px 8px;background:var(--reader-border)}
</style>

<script setup lang="ts">
import { computed, inject } from 'vue'
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue'
import { dockWindowMenuItems, executeDockWindowCommand, type WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu'
import { useReaderWorkspace } from './useReaderWorkspace'
import { readerCentralViewContext, readerSurfaceContext } from './readerWorkspaceContext'
import { LIBRARY_READER_THEME_OPTIONS, libraryReaderTheme } from './readerTheme'

const workspace = useReaderWorkspace()
const surface = inject(readerSurfaceContext, null)
const centralView = inject(readerCentralViewContext, null)
const dock = computed(() => centralView?.dock.value ?? workspace.dock)
const tools = computed(() => dock.value.tools.filter(tool => workspace.state.tabs.length || ['library', 'settings'].includes(tool.id)))
const activeKey = computed(() => workspace.shelfActive ? 'view:bookshelf' : workspace.state.active)
const checked = (label: string, selected: boolean) => `${selected ? '✓ ' : ''}${label}`
// 菜单只编排已有工作区命令；以后按阅读器能力补充正文操作。
const items = computed<WorkspaceMenuItem[]>(() => [
  { id: 'file', label: '文件', children: [
    { id: 'shelf', label: '打开书架' },
    { id: 'close-tab', label: '关闭当前标签', disabled: !activeKey.value },
  ] },
  { id: 'settings', label: '设置', children: [
    { id: 'settings-panel', label: '打开配置' },
    { id: 'theme', label: '阅读主题', children: LIBRARY_READER_THEME_OPTIONS.map(theme => ({ id: `theme:${theme.value}`, label: checked(theme.label, theme.value === libraryReaderTheme.value) })) },
  ] },
  { id: 'window', label: '窗口', children: [
    ...tools.value.map(tool => ({ id: `tool:${tool.id}`, label: checked(tool.title, dock.value.visible(tool.id)) })),
    { id: 'close-tools', label: '关闭所有工具窗口' },
    { id: 'window-divider', label: '', separator: true },
    ...dockWindowMenuItems(workspace.dock),
    { id: 'open-standalone', label: '单独打开本页', disabled: !surface?.pageHref.value },
  ] },
])
function select(id: string) {
  if (executeDockWindowCommand(workspace.dock, id)) return
  if (id === 'shelf') workspace.openShelf()
  else if (id === 'close-tab' && activeKey.value) void workspace.closeTab(activeKey.value)
  else if (id === 'settings-panel') workspace.dock.open('settings')
  else if (id === 'open-standalone' && surface?.pageHref.value) window.open(surface.pageHref.value, '_blank', 'noopener,noreferrer')
  else if (id === 'close-tools') tools.value.forEach(tool => dock.value.close(tool.id))
  else if (id.startsWith('tool:')) {
    const tool = tools.value.find(tool => id === `tool:${tool.id}`)
    if (tool) {
      if (dock.value.visible(tool.id)) dock.value.close(tool.id)
      else dock.value.open(tool.id)
    }
  }
  else if (id.startsWith('theme:')) {
    const theme = LIBRARY_READER_THEME_OPTIONS.find(theme => id === `theme:${theme.value}`)
    if (theme) libraryReaderTheme.value = theme.value
  }
}
</script>
<template><WorkspaceMenu :items="items" @select="select" /></template>

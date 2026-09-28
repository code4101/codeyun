<script setup lang="ts">
import { computed, inject } from 'vue'
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue'
import { dockWindowMenuItems, executeDockWindowCommand, type WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu'
import { useReaderWorkspace } from './useReaderWorkspace'
import { readerSurfaceContext } from './readerWorkspaceContext'
import { LIBRARY_READER_THEME_OPTIONS, libraryReaderTheme } from './readerTheme'

const workspace = useReaderWorkspace()
const surface = inject(readerSurfaceContext, null)
const checked = (label: string, selected: boolean) => `${selected ? '✓ ' : ''}${label}`
// 菜单只编排已有工作区命令；以后按阅读器能力补充正文操作。
const items = computed<WorkspaceMenuItem[]>(() => [
  { id: 'file', label: '文件', children: [
    { id: 'shelf', label: '打开书架' },
  ] },
  { id: 'settings', label: '设置', children: [
    { id: 'open-settings', label: '打开设置' },
    { id: 'theme', label: '阅读主题', children: LIBRARY_READER_THEME_OPTIONS.map(theme => ({ id: `theme:${theme.value}`, label: checked(theme.label, theme.value === libraryReaderTheme.value) })) },
  ] },
  { id: 'window', label: '窗口', children: [
    ...dockWindowMenuItems(workspace.dock),
    { id: 'open-standalone', label: '单独打开本页', disabled: !surface?.pageHref.value },
  ] },
])
function select(id: string) {
  if (executeDockWindowCommand(workspace.dock, id)) return
  if (id === 'shelf') workspace.openShelf()
  else if (id === 'open-settings') workspace.openSettings()
  else if (id === 'open-standalone' && surface?.pageHref.value) window.open(surface.pageHref.value, '_blank', 'noopener,noreferrer')
  else if (id.startsWith('theme:')) {
    const theme = LIBRARY_READER_THEME_OPTIONS.find(theme => id === `theme:${theme.value}`)
    if (theme) libraryReaderTheme.value = theme.value
  }
}
</script>
<template><WorkspaceMenu :items="items" @select="select" /></template>

<script setup lang="ts">
import { useUiPresentation } from '@/router/useUiPresentation'
const { showWorkbench } = useUiPresentation()
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { buildStandaloneRouteLocation } from '@/router/standalone'
import ReaderSettingsPanel from '@/standard/pdf/library/ReaderSettingsPanel.vue'
import { LIBRARY_READER_THEME_OPTIONS, type LibraryReaderTheme } from '@/standard/pdf/library/readerTheme'
import GalleryTool from './GalleryTool.vue'
import type { GallerySnapshot, GalleryCommand, GalleryCanvasDrag, GalleryDrop } from './gallery'
import NodeDetailsTool from './NodeDetailsTool.vue'
import GraphShareDialog from './GraphShareDialog.vue'
import ProjectGraphEditor from './ProjectGraphEditor.vue'
import { graphBaseName, graphFileName } from './fileName'
import { createGraphLibrary, type GraphDocument, type GraphFolder, type GraphNodeDetails } from './storage'

import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import { useDockLayout } from '@/components/docking/useDockLayout'
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue'
import { dockWindowMenuItems, executeDockWindowCommand, type WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu'
import EditorTabs from '@/components/editor-workspace/EditorTabs.vue'
import ResourceExplorer from '@/components/resource-explorer/ResourceExplorer.vue'
import type { ResourceNode } from '@/components/resource-explorer/resourceTree'
import { graphResourceTree } from './resourceTree'
import { canManageResource, canMoveResource, resourceDestination } from './resourceOperations'

const library = createGraphLibrary()
const editorKey = ref('')
const graphStorage = library.storage
const changeGraphLibrary = library.change
const tabsKey = `codeyun.project-graph.tabs:${library.ownerId}`
const themeKey = `codeyun.project-graph.theme:${library.ownerId}`
const theme = ref<LibraryReaderTheme>('dark')
try { const saved = localStorage.getItem(themeKey); if (LIBRARY_READER_THEME_OPTIONS.some(option => option.value === saved)) theme.value = saved as LibraryReaderTheme } catch { /* Default dark. */ }
watch(theme, value => { try { localStorage.setItem(themeKey, value) } catch { /* Session preference. */ } })
const dock = useDockLayout(`codeyun.project-graph.dock:${library.ownerId}`, [
  { id: 'files', title: '资源管理器', icon: 'library', position: 'left', open: true },
  { id: 'gallery', title: '图库', icon: 'library', position: 'right' },
  { id: 'details', title: '正文', icon: 'document', position: 'right' },
])
const opened = ref<string[]>([])
try { const saved = JSON.parse(localStorage.getItem(tabsKey) || '[]'); if (Array.isArray(saved)) opened.value = saved.filter((id): id is string => typeof id === 'string') } catch { /* Empty workspace. */ }
watch(opened, ids => { try { localStorage.setItem(tabsKey, JSON.stringify(ids)) } catch { /* Session remains usable. */ } }, { deep: true })
const expanded = ref(new Set<string>())
const route = useRoute(), router = useRouter()
const emptyMenus: WorkspaceMenuItem[] = [{ id: 'file', label: '文件', children: [{ id: 'newPrgAtCurrentDir', label: '新建.prg' }, { id: 'openFile', label: '导入 .prg' }] }]
const menus = ref<WorkspaceMenuItem[]>(emptyMenus)
const menuReady = ref(false)
const workspaceMenus = computed<WorkspaceMenuItem[]>(() => {
  const items = mounted.value ? menus.value : emptyMenus
  const windowItems = [...dockWindowMenuItems(dock), { id: 'open-standalone', label: '单独打开本页' }]
  const windowMenu = items.find(item => item.id === 'window')
  return windowMenu
    ? items.map(item => item === windowMenu ? { ...item, children: [...(item.children ?? []), ...windowItems] } : item)
    : [...items, { id: 'window', label: '窗口', children: windowItems }]
})
function receiveMenu(items: WorkspaceMenuItem[]) { menus.value = items; menuReady.value = true }
function selectMenu(id: string) {
  if (executeDockWindowCommand(dock, id)) return
  if (id === 'open-standalone') {
    const target = buildStandaloneRouteLocation(route)
    if (target) window.open(router.resolve(target).href, '_blank', 'noopener,noreferrer')
    return
  }
  const host: Record<string, string> = { newPrgAtCurrentDir: 'new', openFile: 'import', openCurrentProjectFileFolder: 'files', saveFile: 'save', saveAs: 'copy', manualBackup: 'download', openAppearanceSettings: 'settings', nodeDetails: 'details', toggleFullscreen: 'fullscreen' }
  if (host[id]) menuCommand(host[id])
  else if (menuReady.value) activeEditor.value?.executeMenu(id)
}
const editor = ref<InstanceType<typeof ProjectGraphEditor>>()
const activeEditor = computed(() => editor.value)
const activeGallery = ref<GallerySnapshot | null>(null)
const galleryTool = ref<InstanceType<typeof GalleryTool>>()
const canvasGalleryDrag = ref<(GalleryCanvasDrag & { destination?: string }) | null>(null)
function galleryDrag(value: GalleryCanvasDrag) {
  if (busy.value || auxiliary.value.active) { galleryTool.value?.canvasDrag({ ...value, phase: 'cancel' }); canvasGalleryDrag.value = null; return }
  if (value.phase === 'move' && !dock.visible('gallery')) dock.open('gallery')
  const destination = galleryTool.value?.canvasDrag(value)
  canvasGalleryDrag.value = value.phase === 'move' ? { ...value, destination } : null
}
const galleryScope = computed(() => mounted.value ? documentId.value : '')
function receiveGallery(value: GallerySnapshot) { activeGallery.value = value }
async function galleryAction(command: GalleryCommand) {
  const target = activeEditor.value
  if (!target || auxiliary.value.active) return
  await run(async () => { await detailsTool.value?.flush(); await target.changeGallery(command) })
}
function galleryDrop(value: GalleryDrop) {
  if (value.documentId === galleryScope.value) void galleryAction({ action: 'take', itemId: value.itemId, position: value.position })
}
const auxiliary = ref<{ tabs: { id: string; title: string }[]; active: string }>({ tabs: [], active: '' })
const details = ref<GraphNodeDetails | null>(null)
const detailsTool = ref<InstanceType<typeof NodeDetailsTool>>()
function editDetails(id: string, value: unknown[]) { editor.value?.updateDetails(id, value) }
const documentId = computed(() => typeof route.query.doc === 'string' ? route.query.doc : '')
const title = ref(''), documents = ref<GraphDocument[]>([]), folders = ref<GraphFolder[]>([])
const folderId = ref(''), error = ref(''), busy = ref(false), mounted = ref(false)
const input = ref<HTMLInputElement>()
const dialog = ref(''), name = ref(''), targetFolder = ref('')
const explorerSelection = ref<ResourceNode[]>([]), operationNodes = ref<ResourceNode[]>([])
const clipboard = ref<{ action: 'move' | 'copy'; nodes: ResourceNode[] } | null>(null)
const cutIds = computed(() => clipboard.value?.action === 'move' ? clipboard.value.nodes.map(node => node.id) : [])
const contextMenu = ref<{ x: number; y: number; folder: boolean; document?: GraphDocument; tab?: boolean } | null>(null)
const contextElement = ref<HTMLElement>()
async function showContext(event: MouseEvent, id?: string, doc?: GraphDocument, tab = false) {
  if (busy.value) return
  if (!tab) folderId.value = id ?? doc?.folderId ?? ''
  contextMenu.value = { x: event.clientX, y: event.clientY, folder: id !== undefined && !!id, document: doc, tab }
  await nextTick()
  const bounds = contextElement.value?.getBoundingClientRect()
  if (bounds && contextMenu.value) {
    contextMenu.value.x = Math.max(8, Math.min(event.clientX, window.innerWidth - bounds.width - 8))
    contextMenu.value.y = Math.max(8, Math.min(event.clientY, window.innerHeight - bounds.height - 8))
    contextElement.value?.querySelector('button')?.focus()
  }
}
function tabContext(event: MouseEvent, id: string) {
  const doc = documents.value.find(item => item.id === id)
  if (!doc) return
  event.preventDefault()
  void showContext(event, undefined, doc, true)
}
/** A personal camera preference; resetting an inactive tab needs no document load. */
function viewStateKey(id: string) { return `codeyun.project-graph.view:${library.ownerId}:${id}` }
async function resetView() {
  const doc = contextMenu.value?.document
  if (!doc) return
  await run(async () => {
    if (doc.id === documentId.value && mounted.value) activeEditor.value?.resetView()
    else localStorage.removeItem(viewStateKey(doc.id))
  })
}
function contextKeys(event: KeyboardEvent) {
  if (event.key === 'Escape' || event.key === 'Tab') { contextMenu.value = null; return }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const items = Array.from(contextElement.value?.querySelectorAll('button') ?? [])
  const index = items.indexOf(document.activeElement as HTMLButtonElement)
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
  items[next]?.focus()
}
const current = computed(() => documents.value.find(item => item.id === documentId.value))
const shareDocument = ref<GraphDocument>()
const currentTabId = documentId
function ensureDocumentTab(id: string) { if (id && !opened.value.includes(id)) opened.value.push(id) }
const fileTabs = computed(() => opened.value.map(id => ({ id, title: graphFileName(documents.value.find(doc => doc.id === id)?.title ?? (id === documentId.value ? title.value : id)) })))
const settingsActive = ref(false)
const selectionKey = `codeyun.project-graph.selection:${library.ownerId}`
let restoredSelection: { active?: string; document?: string } = {}
try {
  const saved = JSON.parse(localStorage.getItem(selectionKey) || 'null')
  if (saved && typeof saved === 'object' && !Array.isArray(saved)) restoredSelection = saved
} catch { /* 无有效历史时使用默认标签。 */ }
let selectionReady = false
watch([currentTabId, settingsActive], () => {
  if (!selectionReady) return
  try { localStorage.setItem(selectionKey, JSON.stringify({ active: settingsActive.value ? 'view:settings' : currentTabId.value, document: currentTabId.value })) } catch { /* 会话仍可使用。 */ }
}, { flush: 'sync' })
function openSettings() { settingsActive.value = true }
const tabs = computed(() => [
  ...fileTabs.value,
  ...auxiliary.value.tabs.map(tab => ({ ...tab, id: `aux:${tab.id}` })),
  ...(settingsActive.value ? [{ id: 'view:settings', title: '设置' }] : []),
])
const tree = computed(() => graphResourceTree(folders.value, documents.value, expanded.value))
function toggleFolders(nodes: ResourceNode[], expand: boolean) {
  for (const node of nodes) { if (expand) expanded.value.add(node.id); else expanded.value.delete(node.id) }
  folderId.value = nodes[nodes.length - 1]?.id.slice(7) ?? ''
}
function openNode(node: ResourceNode) { const doc = documents.value.find(doc => `file:${doc.id}` === node.id); if (doc) void open(doc) }
function canDragFile(node: ResourceNode) {
  return !busy.value && canManageResource(node, documents.value, folders.value)
}
function canDropFile(source: ResourceNode, target: ResourceNode | null) {
  return !busy.value && canMoveResource(source, target, documents.value, folders.value)
}
/** One library transaction for drag, cut/paste or deletion; preserve open canvases. */
async function operateSelection(action: 'move' | 'copy' | 'delete', nodes: ResourceNode[], destination = '') {
  await run(async () => {
    await flush()
    const result = await library.operate(action, nodes.map(node => node.id.slice(node.kind === 'directory' ? 7 : 5)), destination)
    await refreshList()
    if (action === 'delete') {
      opened.value = opened.value.filter(id => !result.ids.includes(id))
      if (result.ids.includes(documentId.value)) {
        const next = documents.value.find(doc => opened.value.includes(doc.id))
        await mountDocument(next?.id ?? '', next?.title ?? '')
      }
      if (!folders.value.some(folder => folder.id === folderId.value)) folderId.value = ''
    } else if (action === 'move') {
      folderId.value = documents.value.find(doc => doc.id === documentId.value)?.folderId ?? folderId.value
    }
    let parent = destination
    while (parent) {
      expanded.value.add(`folder:${parent}`)
      parent = folders.value.find(folder => folder.id === parent)?.parentId ?? ''
    }
    clipboard.value = action === 'move' || action === 'delete' ? null : clipboard.value
    dialog.value = ''
  })
}
async function moveFile(sources: ResourceNode[], target: ResourceNode | null) {
  if (!sources.length || !sources.every(source => canDropFile(source, target))) return
  await operateSelection('move', sources, resourceDestination(target, documents.value))
}
function selectResources(nodes: ResourceNode[]) {
  explorerSelection.value = nodes
  if (!nodes.length) folderId.value = ''
  if (nodes.length === 1) folderId.value = resourceDestination(nodes[0]!, documents.value)
}
function explorerCommand(command: string, nodes = explorerSelection.value, target: ResourceNode | null = null) {
  if (busy.value) return
  contextMenu.value = null
  if (command === 'cancel') { clipboard.value = null; return }
  if (command === 'paste') {
    if (clipboard.value) void operateSelection(clipboard.value.action, clipboard.value.nodes, target ? resourceDestination(target, documents.value) : folderId.value)
    return
  }
  if (command === 'new-file' || command === 'new-folder') { ask(command === 'new-file' ? 'new' : 'folder'); return }
  if (!nodes.length) return
  if (command !== 'copy' && !nodes.every(node => canManageResource(node, documents.value, folders.value))) return
  if (command === 'cut' || command === 'copy') { clipboard.value = { action: command === 'cut' ? 'move' : 'copy', nodes }; return }
  if (command === 'rename' && nodes.length === 1) {
    operationNodes.value = nodes
    name.value = nodes[0]!.kind === 'file' ? graphBaseName(nodes[0]!.name) : nodes[0]!.name
    dialog.value = 'explorer-rename'
  } else if (command === 'delete') { operationNodes.value = nodes; dialog.value = 'explorer-delete' }
  else if (command === 'move') { operationNodes.value = nodes; targetFolder.value = folderId.value; dialog.value = 'explorer-move' }
}
function treeContext(event: MouseEvent, node: ResourceNode, selection: ResourceNode[]) {
  event.preventDefault(); event.stopPropagation()
  explorerSelection.value = selection
  if (node.kind === 'directory') void showContext(event, node.id.slice(7))
  else void showContext(event, undefined, documents.value.find(doc => `file:${doc.id}` === node.id))
}
function activateTab(id: string) {
  if (id === 'view:settings') { settingsActive.value = true; return }
  settingsActive.value = false
  if (id.startsWith('aux:')) { activeEditor.value?.focusAuxiliary(id.slice(4)); return }
  const doc = documents.value.find(doc => doc.id === id); if (doc) void open(doc)
}
function moveTab(id: string, before: string) {
  if (id === before) return
  const remaining = opened.value.filter(item => item !== id)
  const index = remaining.indexOf(before)
  if (!opened.value.includes(id) || index < 0) return
  remaining.splice(index, 0, id); opened.value = remaining
}
async function closeTab(id: string) {
  if (id === 'view:settings') { settingsActive.value = false; return }
  if (id.startsWith('aux:')) { activeEditor.value?.closeAuxiliary(id.slice(4)); return }
  await run(async () => {
    const active = id === currentTabId.value
    if (active) { await flush(); id = currentTabId.value }
    const index = opened.value.indexOf(id)
    opened.value = opened.value.filter(item => item !== id)
    if (active) {
      const nextId = opened.value[Math.max(0, Math.min(index, opened.value.length - 1))]
      const next = documents.value.find(doc => doc.id === nextId); await mountDocument(next?.id ?? '', next?.title ?? '')
    }
  })
}
const folderRows = computed(() => {
  const result: (GraphFolder & { depth: number })[] = []
  const walk = (parent: string, depth: number) => {
    for (const folder of folders.value.filter(item => item.parentId === parent).sort((a, b) => a.title.localeCompare(b.title))) {
      result.push({ ...folder, depth }); walk(folder.id, depth + 1)
    }
  }
  walk('', 0); return result
})
function folderPath(id: string): string {
  const folder = folders.value.find(item => item.id === id)
  return folder ? `${folderPath(folder.parentId)} / ${folder.title}` : '文件'
}
async function refreshList() {
  const result = await library.list(); documents.value = result.documents; folders.value = result.folders
}
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; contextMenu.value = null; error.value = ''
  try { await action() } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function flush() { await detailsTool.value?.flush(); if (mounted.value) await editor.value?.flush() }
async function mountDocument(id: string, fileTitle: string) {
  activeGallery.value = null
  canvasGalleryDrag.value = null
  auxiliary.value = { tabs: [], active: '' }
  details.value = null
  settingsActive.value = false
  menuReady.value = false
  mounted.value = false; await nextTick()
  editorKey.value = id
  await router.replace({ query: { ...route.query, doc: id || undefined } })
  if (id) ensureDocumentTab(id)
  title.value = fileTitle; mounted.value = !!id
  await nextTick()
}
async function open(doc: GraphDocument) {
  if (doc.id === documentId.value) { activeEditor.value?.focusAuxiliary(''); return }
  await run(async () => { await flush(); await mountDocument(doc.id, doc.title) })
}
function ask(kind: string) {
  contextMenu.value = null; dialog.value = kind
  name.value = kind === 'rename' ? graphBaseName(title.value) : kind === 'rename-folder' ? folders.value.find(item => item.id === folderId.value)?.title ?? '' : kind === 'new' ? '未命名图' : kind === 'copy' ? `${graphBaseName(title.value)} 副本` : '新建文件夹'
  targetFolder.value = current.value?.folderId ?? ''
}
/** Bind commands to the right-clicked file, not whichever editor was open. */
async function fileAction(kind: string) {
  const doc = contextMenu.value?.document
  if (!doc) return
  contextMenu.value = null
  if (kind === 'share') { shareDocument.value = doc; return }
  if (kind === 'collaborate' || kind === 'stop-collaboration') {
    await run(async () => {
      await flush()
      // Closing our socket before disabling also lets the server check that no
      // other collaborator is still editing before materializing an ordinary PRG.
      mounted.value = false; await nextTick()
      try { await library.setCollaborative(doc.id, kind === 'collaborate') }
      finally { await refreshList(); await mountDocument(doc.id, doc.title) }
    })
    return
  }
  if (doc.id === documentId.value) {
    if (kind === 'download') activeEditor.value?.exportDocument()
    else ask(kind)
    return
  }
  await run(async () => {
    await flush()
    await mountDocument(doc.id, doc.title)
    if (kind === 'download') { await flush(); activeEditor.value?.exportDocument() }
    else ask(kind)
  })
}
async function submit() {
  if (dialog.value === 'explorer-delete') { await operateSelection('delete', operationNodes.value); return }
  if (dialog.value === 'explorer-move') { await operateSelection('move', operationNodes.value, targetFolder.value); return }
  if (dialog.value === 'explorer-rename') {
    const node = operationNodes.value[0]!
    const text = node.kind === 'file' ? graphBaseName(name.value) : name.value.trim()
    if (!text) return
    await run(async () => {
      const id = node.id.slice(node.kind === 'directory' ? 7 : 5)
      if (node.kind === 'directory') {
        const folder = folders.value.find(item => item.id === id)!
        await changeGraphLibrary({ type: 'folder', folder: { ...folder, title: text } })
      } else {
        await flush()
        await changeGraphLibrary({ type: 'document', id, title: text })
        if (id === documentId.value) title.value = text
      }
      await refreshList(); dialog.value = ''
    })
    return
  }
  const kind = dialog.value, text = ['new', 'copy', 'rename'].includes(kind) ? graphBaseName(name.value) : name.value.trim()
  if (!text && !['move', 'delete', 'delete-folder'].includes(kind)) return
  await run(async () => {
    if (kind === 'folder' || kind === 'rename-folder') {
      const existing = folders.value.find(item => item.id === folderId.value)
      await changeGraphLibrary({ type: 'folder', folder: { id: kind === 'folder' ? '' : folderId.value, title: text, parentId: kind === 'folder' ? folderId.value : existing?.parentId ?? '' } })
    } else if (kind === 'delete-folder') {
      const parent = folders.value.find(item => item.id === folderId.value)?.parentId ?? ''
      await changeGraphLibrary({ type: 'remove-folder', id: folderId.value }); folderId.value = parent
    } else {
      await flush()
      if (kind === 'new' || kind === 'copy') {
        const source = kind === 'copy' ? await graphStorage.read(documentId.value) : undefined
        if (kind === 'copy' && !source) throw new Error('原文件不存在')
        const doc = await library.create(text, source?.bytes, folderId.value)
        await mountDocument(doc.id, doc.title); await flush()
      } else if (kind === 'rename') {
        await changeGraphLibrary({ type: 'document', id: documentId.value, title: text })
        await mountDocument(documentId.value, text)
      } else if (kind === 'move') {
        await changeGraphLibrary({ type: 'document', id: documentId.value, folderId: targetFolder.value })
        folderId.value = targetFolder.value
      } else if (kind === 'delete') {
        const tabId = currentTabId.value
        await changeGraphLibrary({ type: 'document', id: documentId.value, remove: true })
        await refreshList()
        const index = opened.value.indexOf(tabId)
        opened.value = opened.value.filter(id => id !== tabId)
        const nextId = opened.value[Math.max(0, Math.min(index, opened.value.length - 1))]
        const next = documents.value.find(doc => doc.id === nextId); await mountDocument(next?.id ?? '', next?.title ?? '')
      }
    }
    await refreshList(); dialog.value = ''
  })
}
async function importDocument(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  await run(async () => {
    await flush()
    const doc = await library.create(graphBaseName(file.name), new Uint8Array(await file.arrayBuffer()), folderId.value, true)
    await refreshList(); await mountDocument(doc.id, doc.title)
  })
  if (input.value) input.value.value = ''
}
// Same actions as the explorer and dock tools; the iframe never writes files itself.
function menuCommand(command: string) {
  if (busy.value) return
  if (command === 'settings') openSettings()
  else if (['files', 'details'].includes(command)) dock.open(command)
  else if (['new', 'copy'].includes(command)) ask(command)
  else if (command === 'import') input.value?.click()
  else if (command === 'save') void run(flush)
  else if (command === 'download') void run(async () => { await flush(); activeEditor.value?.exportDocument() })
  else if (command === 'fullscreen') void run(async () => {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await document.documentElement.requestFullscreen()
  })
}
function onStatus(value: string) { if (value === 'saved') error.value = '' }
onMounted(() => run(async () => {
  const migrated = await library.migrateBrowser() ?? {}
  if (!opened.value.length) {
    try {
      const saved = JSON.parse(localStorage.getItem(tabsKey) || '[]')
      if (Array.isArray(saved)) opened.value = saved.filter((id): id is string => typeof id === 'string')
    } catch { /* Empty workspace. */ }
  }
  await refreshList()
  opened.value = opened.value.filter(id => documents.value.some(doc => doc.id === id))
  // 显式链接优先；普通入口恢复上次活动标签，失效标签回退到有效资源。
  const explicitDocument = !!documentId.value
  const savedDocument = typeof restoredSelection.document === 'string' && opened.value.includes(restoredSelection.document) ? restoredSelection.document : ''
  const initialDocument = documentId.value || savedDocument || opened.value[0]
  selectionReady = true
  const doc = documents.value.find(item => item.id === (migrated[initialDocument ?? ''] ?? initialDocument)) ?? documents.value.find(item => item.id === opened.value[0]) ?? documents.value[0]
  if (doc) {
    folderId.value = doc.folderId ?? ''
    let parent = folderId.value
    while (parent && !expanded.value.has(`folder:${parent}`)) { expanded.value.add(`folder:${parent}`); parent = folders.value.find(folder => folder.id === parent)?.parentId ?? '' }
    await mountDocument(doc.id, doc.title)
  }
  if (!explicitDocument && restoredSelection.active === 'view:settings') openSettings()
}))
onBeforeRouteLeave(async () => {
  try { await flush(); return true } catch (reason) { error.value = String(reason); return false }
})
const dialogTitles: Record<string, string> = { new: '新建.prg', folder: '新建文件夹', rename: '重命名', 'rename-folder': '重命名文件夹', 'delete-folder': '删除文件夹', move: '移动到', copy: '另存为副本', delete: '删除图文件', 'explorer-rename': '重命名', 'explorer-delete': '删除所选资源', 'explorer-move': '移动到' }
</script>

<template>
  <main class="graph-workspace library-reader-theme-dialog" :class="`is-reader-theme-${theme}`" :aria-busy="busy">
    <Teleport to="body"><div v-if="canvasGalleryDrag" class="gallery-drag-preview" :class="{ accepted: canvasGalleryDrag.destination }" :style="{ left: `${canvasGalleryDrag.x + 16}px`, top: `${canvasGalleryDrag.y + 16}px` }"><strong>{{ canvasGalleryDrag.title }}</strong><small>{{ canvasGalleryDrag.destination ? `移入 ${canvasGalleryDrag.destination}` : '拖入图库分组' }}</small></div></Teleport>
    <WorkspaceMenu v-show="showWorkbench" :items="workspaceMenus" :disabled="busy || (mounted && !menuReady)" @refresh="activeEditor?.refreshMenu()" @select="selectMenu" />
    <DockWorkspace :dock="dock" :content-only="!showWorkbench">
      <template #files>
        <div class="graph-files" v-context-menu.prevent="($event: MouseEvent) => showContext($event)">
          <ResourceExplorer :nodes="tree" :selected-id="`file:${documentId}`" multi-select :cut-ids="cutIds" :can-drag="canDragFile" :can-drop="canDropFile" @selection="selectResources" @command="explorerCommand" @move="moveFile" @open="openNode" @toggle="toggleFolders" @contextmenu="treeContext" />
        </div>
      </template>
      <template #gallery-actions>
        <button class="gallery-header-action" title="搜索子图" aria-label="搜索子图" :aria-pressed="galleryTool?.searchOpen" @click="galleryTool?.toggleSearch()"><svg viewBox="0 0 24 24"><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></svg></button>
        <button class="gallery-header-action" title="添加分组" aria-label="添加分组" :aria-pressed="galleryTool?.creating" :disabled="!galleryTool || galleryTool.locked" @click="galleryTool?.toggleCreate()"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>
      </template>
      <template #gallery>
        <GalleryTool ref="galleryTool" :state="activeGallery" :scope="galleryScope" :disabled="busy || !mounted || !!auxiliary.active" @command="galleryAction" @item-drag="value => activeEditor?.previewGalleryItem(value)" />
      </template>
      <template #details>
        <NodeDetailsTool :key="documentId" ref="detailsTool" :node="details" :read-only="current?.role === 'viewer'" @change="editDetails" />
      </template>
      <EditorTabs v-show="showWorkbench" :tabs="tabs" :evict="closeTab" :eviction-paused="busy" :active="settingsActive ? 'view:settings' : auxiliary.active ? `aux:${auxiliary.active}` : currentTabId" @activate="activateTab" @close="closeTab" @move="moveTab" @contextmenu="tabContext" />
      <div v-if="error" class="error" role="alert">{{ error }} <button v-if="mounted" @click="run(flush)">重试保存</button><button v-if="mounted" @click="activeEditor?.exportDocument()">下载文件</button></div>
      <div v-show="!settingsActive" class="canvas">
        <ProjectGraphEditor v-if="mounted" :key="editorKey" ref="editor" :document-id="documentId" :title="title" :storage="graphStorage" :view-state-key="viewStateKey(documentId)" :gallery-active="dock.visible('gallery')" @gallery="receiveGallery" @gallery-drag="galleryDrag" @gallery-drop="galleryDrop" :details-active="dock.visible('details')" @auxiliary="auxiliary = $event" @menu="receiveMenu" @command="menuCommand" @details="details = $event" @status="onStatus" @error="error = $event" @saved="refreshList" />
        <div v-else class="welcome"><div class="welcome-icon">◇</div><h2>从一张图开始</h2><p>把想法连接起来，给每个节点写下正文。</p><button class="primary" :disabled="busy" @click="ask('new')">新建.prg</button><button :disabled="busy" @click="input?.click()">导入 .prg</button></div>
        <div v-if="busy" class="busy">正在处理…</div>
      </div>
      <div v-if="settingsActive" class="graph-settings"><ReaderSettingsPanel :theme="theme" appearance-label="外观" theme-description="画布与正文共用此主题。" @theme="theme = $event" /></div>
    </DockWorkspace>
    <input ref="input" type="file" accept=".prg" hidden @change="importDocument">
    <template v-if="contextMenu">
      <div class="menu-dismiss" @pointerdown="contextMenu = null" @contextmenu.prevent="contextMenu = null"></div>
      <div ref="contextElement" class="menu-items library-context" role="menu" aria-label="文件区菜单" :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }" @keydown="contextKeys" @contextmenu.prevent>
        <button v-if="contextMenu.document" role="menuitem" :disabled="contextMenu.document.id === documentId && !menuReady" @click="resetView">重置比例与位置</button>
        <template v-if="!contextMenu.tab">
          <button role="menuitem" @click="ask('new')">新建.prg</button>
          <button role="menuitem" @click="ask('folder')">新建文件夹</button>
          <button role="menuitem" @click="contextMenu = null; input?.click()">导入 .prg</button>
          <button role="menuitem" :disabled="!clipboard" @click="explorerCommand('paste')">粘贴 <small>Ctrl+V</small></button>
          <template v-if="contextMenu.document || contextMenu.folder">
            <hr>
            <button role="menuitem" :disabled="!explorerSelection.length || !explorerSelection.every(canDragFile)" @click="explorerCommand('cut')">剪切 <small>Ctrl+X</small></button>
            <button role="menuitem" @click="explorerCommand('copy')">复制 <small>Ctrl+C</small></button>
            <button role="menuitem" :disabled="explorerSelection.length !== 1 || !explorerSelection.every(canDragFile)" @click="explorerCommand('rename')">重命名 <small>F2</small></button>
            <button role="menuitem" :disabled="!explorerSelection.length || !explorerSelection.every(canDragFile)" @click="explorerCommand('move')">移动到…</button>
            <button role="menuitem" class="danger" :disabled="!explorerSelection.length || !explorerSelection.every(canDragFile)" @click="explorerCommand('delete')">删除 <small>Delete</small></button>
          </template>
          <template v-if="contextMenu.document">
            <hr>
            <button v-if="contextMenu.document.role === 'manager'" role="menuitem" @click="fileAction('share')">分享权限…</button>
            <button v-if="contextMenu.document.role === 'manager' && !contextMenu.document.collaborative" role="menuitem" @click="fileAction('collaborate')">启用多人协作</button>
            <button v-if="contextMenu.document.role === 'manager' && contextMenu.document.collaborative" role="menuitem" @click="fileAction('stop-collaboration')">结束协作并保存为普通文件</button>
            <button role="menuitem" @click="fileAction('copy')">另存为副本</button>
            <button role="menuitem" @click="fileAction('download')">下载文件</button>
          </template>
        </template>
      </div>
    </template>
    <GraphShareDialog v-if="shareDocument" :document-id="shareDocument.id" :title="shareDocument.title" :library="library" @close="shareDocument = undefined" />
    <div v-if="dialog" class="modal-backdrop" @keydown.esc="!busy && (dialog = '')">
      <form class="modal" role="dialog" aria-modal="true" :aria-label="dialogTitles[dialog]" @submit.prevent="submit">
        <h3>{{ dialogTitles[dialog] }}</h3>
        <template v-if="dialog === 'move' || dialog === 'explorer-move'"><label for="graph-folder">目标文件夹</label><select id="graph-folder" v-model="targetFolder"><option value="">根目录</option><option v-for="folder in folderRows" :key="folder.id" :value="folder.id" :disabled="dialog === 'explorer-move' && operationNodes.some(node => !canDropFile(node, { id: `folder:${folder.id}`, name: folder.title, kind: 'directory' }))">{{ folderPath(folder.id) }}</option></select></template>
        <p v-else-if="dialog === 'delete'">删除“{{ graphFileName(title) }}”？此操作无法撤销。</p>
        <p v-else-if="dialog === 'delete-folder'">删除当前空文件夹？</p>
        <p v-else-if="dialog === 'explorer-delete'">删除 {{ operationNodes.map(node => `“${node.name}”`).join('、') }}？文件夹内的全部内容将一并删除，此操作无法撤销。</p>
        <template v-else><label for="graph-name">名称</label><input id="graph-name" v-model="name" autofocus autocomplete="off" maxlength="120"></template>
        <p v-if="error" class="dialog-error">{{ error }}</p>
        <div class="dialog-actions"><button type="button" :disabled="busy" @click="dialog = ''">取消</button><button class="primary" :disabled="busy" type="submit">{{ busy ? '处理中…' : '确定' }}</button></div>
      </form>
    </div>
  </main>
</template>

<style scoped>
.gallery-drag-preview{position:fixed;z-index:10000;pointer-events:none;display:flex;flex-direction:column;gap:4px;max-width:220px;padding:9px 12px;border:1px solid #657286;border-radius:7px;background:#242b34;color:#dce5f1;box-shadow:0 4px 16px #0003;font-size:12px}
.gallery-drag-preview strong{white-space:nowrap;text-overflow:ellipsis;overflow:hidden;font-weight:500}
.gallery-drag-preview small{color:#9aa9bc;font-size:10px}
.gallery-drag-preview.accepted{border-color:#76a9ff}.gallery-drag-preview.accepted small{color:#76a9ff}
.gallery-header-action{display:flex;align-items:center;justify-content:center;width:26px;height:26px;border:0;border-radius:4px;background:transparent;color:var(--reader-muted);cursor:pointer}
.gallery-header-action:hover,.gallery-header-action[aria-pressed="true"]{background:var(--reader-hover);color:var(--reader-text)}
.gallery-header-action:disabled{opacity:.4;cursor:default}
.gallery-header-action svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round}
.menu-items.library-context{position:fixed;right:auto;max-width:calc(100vw - 28px);max-height:calc(100dvh - 16px);overflow-y:auto}
.graph-workspace{height:100%;width:100%;min-height:0;min-width:0;overflow:hidden;display:flex;font-size:14px}.graph-files{padding:4px;flex:1;min-height:0}button,input,select{font:inherit;color:inherit}button{cursor:pointer;border:1px solid var(--reader-border);border-radius:6px;background:var(--reader-content);padding:7px 12px}button:hover{background:var(--reader-hover)}button:disabled{opacity:.5;cursor:default}a{color:inherit;text-decoration:none}.menu-dismiss{position:fixed;inset:0;z-index:20}.menu-items{position:absolute;top:36px;right:0;width:172px;padding:6px;background:var(--reader-content);border:1px solid var(--reader-border);border-radius:8px;box-shadow:0 10px 28px #17203320;z-index:21}.menu-items button{display:block;width:100%;border:0;text-align:left}.menu-items hr{border:0;border-top:1px solid #eef1f5;margin:5px}.danger{color:#be3737}.canvas{flex:1;min-height:0;position:relative;background:var(--reader-content)}.error{padding:10px 16px;background:#fff0ec;color:#a33725;font-size:12px}.error button{margin-left:10px;font-size:12px}.welcome{height:100%;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px}.welcome-icon{font-size:64px;color:#6898e8}.welcome h2{margin:0;font-size:22px}.welcome p{color:var(--reader-muted);margin:0 0 12px}.primary{background:#3269d9;color:white;border-color:#3269d9}.primary:hover{background:#285abf}.busy{position:absolute;inset:0;display:grid;place-items:center;background:var(--reader-content);z-index:10;pointer-events:auto}.modal-backdrop{position:fixed;inset:0;background:#0f172a55;display:grid;place-items:center;z-index:50}.modal{background:var(--reader-content);border-radius:12px;padding:24px;width:min(380px,85vw);box-shadow:0 20px 80px #0003}.modal h3{margin:0 0 22px;font-size:18px}.modal label{display:block;color:var(--reader-muted);font-size:12px;margin-bottom:8px}.modal input,.modal select{width:100%;box-sizing:border-box;background:var(--reader-content);border:1px solid var(--reader-border);padding:9px;border-radius:6px}.dialog-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:24px}.dialog-error{color:#b43d2c;font-size:12px}


.error{max-height:30%;overflow:auto;overflow-wrap:anywhere}
.welcome{min-height:0;overflow:auto;text-align:center;padding:12px;box-sizing:border-box}
.graph-settings{flex:1;min-height:0;overflow:auto;background:var(--reader-content)}

</style>

<style scoped>
.graph-workspace{flex-direction:column}.graph-workspace > :deep(.dock-workspace){flex:1;min-height:0;width:100%}
.graph-files{display:flex;flex-direction:column}.graph-files :deep(.resource-tree){flex:1}
.menu-items small{float:right;color:var(--reader-muted);font-size:10px;line-height:20px}
</style>

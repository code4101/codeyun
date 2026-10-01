<script setup lang="ts">
import { useUiPresentation } from '@/router/useUiPresentation'
const { showWorkbench } = useUiPresentation()
import { computed, nextTick, onMounted, ref, shallowReactive, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { buildStandaloneRouteLocation } from '@/router/standalone'
import ReaderSettingsPanel from '@/standard/pdf/library/ReaderSettingsPanel.vue'
import { LIBRARY_READER_THEME_OPTIONS, type LibraryReaderTheme } from '@/standard/pdf/library/readerTheme'
import GalleryTool from './GalleryTool.vue'
import type { GallerySnapshot, GalleryCommand } from './gallery'
import NodeDetailsTool from './NodeDetailsTool.vue'
import GraphShareDialog from './GraphShareDialog.vue'
import ProjectGraphEditor from './ProjectGraphEditor.vue'
import JournalNavigation from './JournalNavigation.vue'
import JournalDayCanvas from './JournalDayCanvas.vue'
import { dayLabel, localDay } from './journal'
import { graphBaseName, graphFileName } from './fileName'
import { createGraphLibrary, type GraphDocument, type GraphFolder, type GraphStorage } from './storage'

import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import { useDockLayout } from '@/components/docking/useDockLayout'
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue'
import { dockWindowMenuItems, executeDockWindowCommand, type WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu'
import EditorTabs from '@/components/editor-workspace/EditorTabs.vue'
import ResourceExplorer from '@/components/resource-explorer/ResourceExplorer.vue'
import type { ResourceNode } from '@/components/resource-explorer/resourceTree'
import { graphResourceTree } from './resourceTree'

const library = createGraphLibrary()
const journalTabId = 'view:journal'
const draftJournal = ref<GraphDocument>()
const editorKey = ref('')
// Preview identity is in-memory only. First authored content creates the file
// atomically; adopting its real ID preserves the editor and undo history.
const graphStorage: GraphStorage = {
  ...library.storage,
  async read(id) { return id === draftJournal.value?.id ? draftJournal.value : library.storage.read(id) },
  async write(id, title, bytes, revision) {
    const draft = draftJournal.value
    if (!draft || id !== draft.id) return library.storage.write(id, title, bytes, revision)
    const saved = await library.saveJournal(draft.journalDate!, bytes)
    if (!saved) return { ...draft, bytes }
    await refreshList()
    await router.replace({ query: { ...route.query, doc: saved.id } })
    ensureDocumentTab(saved.id)
    draftJournal.value = undefined
    return saved
  },
}
const changeGraphLibrary = library.change
const tabsKey = `codeyun.project-graph.tabs:${library.ownerId}`
const themeKey = `codeyun.project-graph.theme:${library.ownerId}`
const theme = ref<LibraryReaderTheme>('dark')
try { const saved = localStorage.getItem(themeKey); if (LIBRARY_READER_THEME_OPTIONS.some(option => option.value === saved)) theme.value = saved as LibraryReaderTheme } catch { /* Default dark. */ }
watch(theme, value => { try { localStorage.setItem(themeKey, value) } catch { /* Session preference. */ } })
const dock = useDockLayout(`codeyun.project-graph.dock:${library.ownerId}`, [
  { id: 'files', title: '资源管理器', icon: 'library', position: 'left', open: true },
  { id: 'journal', title: '每日记录', icon: 'calendar', position: 'left' },
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
const extraDays = ref<string[]>([])
const journalEmpty = ref(false)
const paneHeights = ref<Record<string, number>>({})
const resizingDays = ref(false)
let resizePair: { top: string; bottom: string; y: number; topHeight: number; bottomHeight: number } | undefined
const minPaneHeight = 180
function paneStyle(day: string) {
  return { order: selectedJournalDays.value.indexOf(day) * 2, flexGrow: extraDays.value.length ? (paneHeights.value[day] ?? 1) : undefined }
}
function startDayResize(event: PointerEvent, bottom: string) {
  if (event.button !== 0) return
  const handle = event.currentTarget as HTMLElement
  const panes = Array.from(handle.parentElement!.querySelectorAll<HTMLElement>('.day-pane'))
  const top = selectedJournalDays.value[selectedJournalDays.value.indexOf(bottom) - 1]
  const topPane = panes.find(pane => pane.dataset.journalDay === top)
  const bottomPane = panes.find(pane => pane.dataset.journalDay === bottom)
  if (!topPane || !bottomPane) return
  for (const pane of panes) paneHeights.value[pane.dataset.journalDay!] = pane.getBoundingClientRect().height
  resizePair = { top, bottom, y: event.clientY, topHeight: topPane.getBoundingClientRect().height, bottomHeight: bottomPane.getBoundingClientRect().height }
  handle.setPointerCapture(event.pointerId)
  resizingDays.value = true
  event.preventDefault()
}
function moveDayResize(event: PointerEvent) {
  if (!resizePair) return
  const { top, bottom, y, topHeight, bottomHeight } = resizePair
  const minimum = Math.min(minPaneHeight, (topHeight + bottomHeight) / 4)
  const delta = Math.max(minimum - topHeight, Math.min(bottomHeight - minimum, event.clientY - y))
  paneHeights.value[top] = topHeight + delta
  paneHeights.value[bottom] = bottomHeight - delta
}
function stopDayResize() { resizePair = undefined; resizingDays.value = false }
function keyboardDayResize(event: KeyboardEvent, bottom: string) {
  if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return
  const handle = event.currentTarget as HTMLElement
  const panes = Array.from(handle.parentElement!.querySelectorAll<HTMLElement>('.day-pane'))
  const top = selectedJournalDays.value[selectedJournalDays.value.indexOf(bottom) - 1]
  const a = panes.find(pane => pane.dataset.journalDay === top)?.getBoundingClientRect().height
  const b = panes.find(pane => pane.dataset.journalDay === bottom)?.getBoundingClientRect().height
  if (a == null || b == null) return
  for (const pane of panes) paneHeights.value[pane.dataset.journalDay!] = pane.getBoundingClientRect().height
  const minimum = Math.min(minPaneHeight, (a + b) / 4)
  const delta = Math.max(minimum - a, Math.min(b - minimum, event.key === 'ArrowUp' ? -24 : 24))
  paneHeights.value[top] = a + delta; paneHeights.value[bottom] = b - delta
  event.preventDefault()
}

const activeExtraDay = ref('')
const keyboardHints = shallowReactive<Record<string, { keys: string[]; items: { displayKey: string; title: string }[]; page: string }>>({})
const activeKeyboardHints = computed(() => keyboardHints[activeExtraDay.value])
const canvasModes = shallowReactive<Record<string, { mode: string; readOnly: boolean; color: number[] }>>({})
const activeCanvasMode = computed(() => canvasModes[activeExtraDay.value])
const modeOptions = [
  { id: 'selectAndMove', label: '选择和移动', path: 'm4 3 7 18 2-8 8-2Z' },
  { id: 'draw', label: '自由绘制', path: 'm16 3 5 5-13 13H3v-5ZM14 5l5 5' },
  { id: 'connectAndCut', label: '连接和切断', path: 'M8 6h8M6 8v8m2 2h8m2-2V8M8 6a2 2 0 1 0-4 0 2 2 0 0 0 4 0Zm12 0a2 2 0 1 0-4 0 2 2 0 0 0 4 0ZM8 18a2 2 0 1 0-4 0 2 2 0 0 0 4 0Zm12 0a2 2 0 1 0-4 0 2 2 0 0 0 4 0Z' },
]
const penColor = computed(() => '#' + (activeCanvasMode.value?.color ?? [0, 0, 0]).slice(0, 3).map(value => Math.round(value).toString(16).padStart(2, '0')).join(''))
function setPenColor(event: Event) {
  const hex = (event.target as HTMLInputElement).value.slice(1)
  activeEditor.value?.setMode('draw', [0, 2, 4].map(offset => parseInt(hex.slice(offset, offset + 2), 16)).concat(activeCanvasMode.value?.color[3] ?? 1))
}

const extraEditors = shallowReactive<Record<string, InstanceType<typeof JournalDayCanvas>>>({})
const paneMenus = new Map<string, WorkspaceMenuItem[]>()
const activeEditor = computed(() => activeExtraDay.value ? extraEditors[activeExtraDay.value] : editor.value)
function setExtraEditor(day: string, value: unknown) {
  if (value) extraEditors[day] = value as InstanceType<typeof JournalDayCanvas>
  else delete extraEditors[day]
}
function focusDay(day = '') {
  if (activeExtraDay.value === day) return
  // Scoped node keys keep late rich-text edits attached to their original pane.
  void Promise.resolve(detailsTool.value?.flush()).catch(reason => { error.value = String(reason) })
  activeExtraDay.value = day; details.value = null; auxiliary.value = { tabs: [], active: '' }
  menus.value = paneMenus.get(day) ?? emptyMenus
  activeEditor.value?.refreshMenu()
  activeEditor.value?.requestMode()
}
function paneMenu(day: string, items: WorkspaceMenuItem[]) {
  paneMenus.set(day, items)
  if (activeExtraDay.value === day) receiveMenu(items)
}

const galleryStates = ref<Record<string, GallerySnapshot>>({})
const activeGallery = computed(() => galleryStates.value[activeExtraDay.value] ?? null)
const galleryScope = computed(() => mounted.value ? `${documentId.value}|${activeExtraDay.value}` : '')
function receiveGallery(day: string, value: GallerySnapshot) { galleryStates.value[day] = value }
async function galleryAction(command: GalleryCommand) {
  const target = activeEditor.value
  if (!target || auxiliary.value.active) return
  await run(async () => { await detailsTool.value?.flush(); await target.changeGallery(command) })
}
function galleryDrop(day: string, value: { documentId: string; itemId: string }) {
  if (day === activeExtraDay.value && value.documentId === galleryScope.value) void galleryAction({ action: 'take', itemId: value.itemId })
}
const auxiliary = ref<{ tabs: { id: string; title: string }[]; active: string }>({ tabs: [], active: '' })
const details = ref<{ id: string; title: string; value: unknown[] } | null>(null)
const detailsTool = ref<InstanceType<typeof NodeDetailsTool>>()
const scopedDetails = computed(() => details.value ? { ...details.value, id: JSON.stringify([activeExtraDay.value, details.value.id]) } : null)
function editDetails(key: string, value: unknown[]) {
  const [day, id] = JSON.parse(key) as [string, string]
  ;(day ? extraEditors[day] : editor.value)?.updateDetails(id, value)
}
const documentId = computed(() => typeof route.query.doc === 'string' ? route.query.doc : '')
const journalDayKey = `codeyun.project-graph.journal-day:${library.ownerId}`
const selectedJournalDay = ref(localDay())
try { const day = localStorage.getItem(journalDayKey); if (day && /^\d{4}-\d{2}-\d{2}$/.test(day)) selectedJournalDay.value = day } catch { /* Today. */ }
watch(selectedJournalDay, day => { try { localStorage.setItem(journalDayKey, day) } catch { /* Session only. */ } })
const title = ref(''), documents = ref<GraphDocument[]>([]), folders = ref<GraphFolder[]>([])
const folderId = ref(''), error = ref(''), busy = ref(false), mounted = ref(false)
const input = ref<HTMLInputElement>()
const dialog = ref(''), name = ref(''), targetFolder = ref('')
const contextMenu = ref<{ x: number; y: number; folder: boolean; document?: GraphDocument } | null>(null)
const contextElement = ref<HTMLElement>()
async function showContext(event: MouseEvent, id?: string, doc?: GraphDocument) {
  if (busy.value) return
  if (id !== undefined) folderId.value = id
  contextMenu.value = { x: event.clientX, y: event.clientY, folder: id !== undefined && !!id, document: doc }
  await nextTick()
  const bounds = contextElement.value?.getBoundingClientRect()
  if (bounds && contextMenu.value) {
    contextMenu.value.x = Math.max(8, Math.min(event.clientX, window.innerWidth - bounds.width - 8))
    contextMenu.value.y = Math.max(8, Math.min(event.clientY, window.innerHeight - bounds.height - 8))
    contextElement.value?.querySelector('button')?.focus()
  }
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
const current = computed(() => documents.value.find(item => item.id === documentId.value) ?? draftJournal.value)
const shareDocument = ref<GraphDocument>()
const journalDocuments = computed(() => documents.value.filter(doc => doc.journalDate && !doc.shared))
const currentJournalDate = computed(() => current.value?.shared ? null : current.value?.journalDate)
const selectedJournalDays = computed(() => journalEmpty.value ? [] : currentJournalDate.value ? [currentJournalDate.value, ...extraDays.value].sort() : [selectedJournalDay.value])
const currentTabId = computed(() => currentJournalDate.value || journalEmpty.value ? journalTabId : documentId.value)
function documentTabId(id: string) {
  const doc = documents.value.find(item => item.id === id)
  return id.startsWith('journal:') || (doc?.journalDate && !doc.shared) ? journalTabId : id
}
function ensureDocumentTab(id: string) {
  const tab = documentTabId(id)
  if (tab && !opened.value.includes(tab)) opened.value.push(tab)
}
watch(currentJournalDate, day => {
  if (day) { selectedJournalDay.value = day; revealJournal(); ensureDocumentTab(documentId.value) }
  else if (mounted.value && current.value) ensureDocumentTab(documentId.value)
})
function revealJournal() { if (!dock.visible('journal')) dock.toggle('journal') }
async function showJournal(day: string) {
  let doc = await library.openJournal(day)
  if (!doc) {
    doc = { id: `journal:${day}`, title: day, journalDate: day, role: 'manager', shared: false, bytes: new Uint8Array(), revision: 0, updatedAt: 0 }
    draftJournal.value = doc
  }
  await refreshList()
  if (!mounted.value || doc.id !== documentId.value) await mountDocument(doc.id, doc.title)
  else { settingsActive.value = false; activeEditor.value?.focusAuxiliary('') }
  selectedJournalDay.value = day; revealJournal()
}
async function openJournal(day: string, additive = false) {
  await run(async () => {
    await flush()
    if (additive && currentJournalDate.value) {
      if (day === currentJournalDate.value) {
        const next = extraDays.value[0]
        if (!next) { await mountDocument('', ''); journalEmpty.value = true; return }
        extraDays.value = extraDays.value.filter(value => value !== next)
        focusDay(); await showJournal(next)
      } else if (extraDays.value.includes(day)) {
        if (activeExtraDay.value === day) focusDay()
        extraDays.value = extraDays.value.filter(value => value !== day)
      } else {
        const weights = selectedJournalDays.value.map(value => paneHeights.value[value] ?? 1)
        paneHeights.value[day] = weights.reduce((sum, value) => sum + value, 0) / weights.length
        extraDays.value = [...extraDays.value, day].sort()
      }
      return
    }
    extraDays.value = []; focusDay()
    await showJournal(day)
  })
}
async function removeJournal(doc: GraphDocument) {
  await run(async () => {
    await flush()
    await changeGraphLibrary({ type: 'document', id: doc.id, remove: true })
    await refreshList()
    if (extraDays.value.includes(doc.journalDate!)) {
      if (activeExtraDay.value === doc.journalDate) focusDay()
      extraDays.value = extraDays.value.filter(day => day !== doc.journalDate)
    }
    if (documentId.value === doc.id) await showJournal(doc.journalDate!)
  })
}
const fileTabs = computed(() => opened.value.map(id => ({ id, title: id === journalTabId ? '每日记录' : graphFileName(documents.value.find(doc => doc.id === id)?.title ?? (id === documentId.value ? title.value : id)) })))
const settingsActive = ref(false)
function openSettings() { settingsActive.value = true }
const tabs = computed(() => [
  ...fileTabs.value,
  ...auxiliary.value.tabs.map(tab => ({ ...tab, id: `aux:${tab.id}` })),
  ...(settingsActive.value ? [{ id: 'view:settings', title: '设置' }] : []),
])
const tree = computed(() => graphResourceTree(folders.value, documents.value.filter(doc => !doc.journalDate || doc.shared), expanded.value))
function toggleFolders(nodes: ResourceNode[], expand: boolean) {
  for (const node of nodes) { if (expand) expanded.value.add(node.id); else expanded.value.delete(node.id) }
  folderId.value = nodes[nodes.length - 1]?.id.slice(7) ?? ''
}
function openNode(node: ResourceNode) { const doc = documents.value.find(doc => `file:${doc.id}` === node.id); if (doc) void open(doc) }
function treeContext(event: MouseEvent, node: ResourceNode) {
  event.preventDefault(); event.stopPropagation()
  if (node.kind === 'directory') void showContext(event, node.id.slice(7))
  else void showContext(event, undefined, documents.value.find(doc => `file:${doc.id}` === node.id))
}
function activateTab(id: string) {
  if (id === 'view:settings') { settingsActive.value = true; return }
  settingsActive.value = false
  if (id === journalTabId) { if (currentJournalDate.value) activeEditor.value?.focusAuxiliary(''); else void openJournal(selectedJournalDay.value); return }
  if (id === draftJournal.value?.id) { activeEditor.value?.focusAuxiliary(''); return }
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
      if (nextId === journalTabId) await showJournal(selectedJournalDay.value)
      else { const next = documents.value.find(doc => doc.id === nextId); await mountDocument(next?.id ?? '', next?.title ?? '') }
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
  // Collapse restored daily file tabs as well as newly opened ones.
  opened.value = [...new Set(opened.value.map(documentTabId))]
}
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; contextMenu.value = null; error.value = ''
  try { await action() } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function flush() { await detailsTool.value?.flush(); if (mounted.value) await editor.value?.flush(); await Promise.all(Object.values(extraEditors).map(pane => pane.flush())) }
async function mountDocument(id: string, fileTitle: string) {
  galleryStates.value = {}
  journalEmpty.value = false
  focusDay()
  if (documentTabId(id) !== journalTabId) extraDays.value = []
  auxiliary.value = { tabs: [], active: '' }
  details.value = null
  settingsActive.value = false
  menuReady.value = false
  mounted.value = false; await nextTick()
  if (id !== draftJournal.value?.id) draftJournal.value = undefined
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
  name.value = kind === 'rename' ? graphFileName(title.value) : kind === 'rename-folder' ? folders.value.find(item => item.id === folderId.value)?.title ?? '' : kind === 'new' ? '未命名图.prg' : kind === 'copy' ? `${graphBaseName(activeExtraDay.value || title.value)} 副本.prg` : '新建文件夹'
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
        const source = kind === 'copy' ? (activeExtraDay.value ? await extraEditors[activeExtraDay.value]?.read() : await graphStorage.read(documentId.value)) : undefined
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
        if (tabId === journalTabId) await showJournal(selectedJournalDay.value)
        else {
          const index = opened.value.indexOf(tabId)
          opened.value = opened.value.filter(id => id !== tabId)
          const nextId = opened.value[Math.max(0, Math.min(index, opened.value.length - 1))]
          if (nextId === journalTabId) await showJournal(selectedJournalDay.value)
          else { const next = documents.value.find(doc => doc.id === nextId); await mountDocument(next?.id ?? '', next?.title ?? '') }
        }
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
  opened.value = opened.value.filter(id => id === journalTabId || documents.value.some(doc => doc.id === id))
  const preview = /^journal:(\d{4}-\d{2}-\d{2})$/.exec(documentId.value)
  if (preview) { await showJournal(preview[1]!); return }
  if (!documentId.value && opened.value[0] === journalTabId) { await showJournal(selectedJournalDay.value); return }
  const doc = documents.value.find(item => item.id === (migrated[documentId.value] ?? documentId.value)) ?? documents.value.find(item => item.id === opened.value[0]) ?? documents.value[0]
  if (doc) {
    folderId.value = doc.folderId ?? ''
    let parent = folderId.value
    while (parent && !expanded.value.has(`folder:${parent}`)) { expanded.value.add(`folder:${parent}`); parent = folders.value.find(folder => folder.id === parent)?.parentId ?? '' }
    await mountDocument(doc.id, doc.title)
  }
}))
onBeforeRouteLeave(async () => {
  try { await flush(); return true } catch (reason) { error.value = String(reason); return false }
})
const dialogTitles: Record<string, string> = { new: '新建.prg', folder: '新建文件夹', rename: '重命名', 'rename-folder': '重命名文件夹', 'delete-folder': '删除文件夹', move: '移动到', copy: '另存为副本', delete: '删除图文件' }
</script>

<template>
  <main class="graph-workspace library-reader-theme-dialog" :class="`is-reader-theme-${theme}`" :aria-busy="busy">
    <WorkspaceMenu v-show="showWorkbench" :items="workspaceMenus" :disabled="busy || (mounted && !menuReady)" @refresh="activeEditor?.refreshMenu()" @select="selectMenu" />
    <DockWorkspace :dock="dock" :content-only="!showWorkbench">
      <template #journal>
        <JournalNavigation :documents="journalDocuments" :selected="selectedJournalDay" :selected-days="selectedJournalDays" :disabled="busy" @open="openJournal" @remove="removeJournal" />
      </template>
      <template #files>
        <div class="graph-files" v-context-menu.prevent="($event: MouseEvent) => showContext($event)">
          <ResourceExplorer :nodes="tree" :selected-id="`file:${documentId}`" @open="openNode" @toggle="toggleFolders" @contextmenu="treeContext" />
        </div>
      </template>
      <template #gallery>
        <GalleryTool :state="activeGallery" :scope="galleryScope" :disabled="busy || !mounted || !!auxiliary.active" @command="galleryAction" />
      </template>
      <template #details>
        <NodeDetailsTool :key="documentId" ref="detailsTool" :node="scopedDetails" :read-only="current?.role === 'viewer'" @change="editDetails" />
      </template>
      <EditorTabs v-show="showWorkbench" :tabs="tabs" :active="settingsActive ? 'view:settings' : auxiliary.active ? `aux:${auxiliary.active}` : currentTabId" @activate="activateTab" @close="closeTab" @move="moveTab" />
      <div v-if="error" class="error" role="alert">{{ error }} <button v-if="mounted" @click="run(flush)">重试保存</button><button v-if="mounted" @click="activeEditor?.exportDocument()">下载文件</button></div>
      <div v-show="!settingsActive" class="canvas" :class="{ 'multi-day': extraDays.length > 0 }">
        <div v-if="extraDays.length && !auxiliary.active" class="shared-mode-toolbar" role="toolbar" aria-label="画布模式">
          <button v-for="mode in modeOptions" :key="mode.id" :title="mode.label" :aria-label="mode.label" :aria-pressed="activeCanvasMode?.mode === mode.id" :disabled="busy || !activeCanvasMode || activeCanvasMode.readOnly" @click="activeEditor?.setMode(mode.id)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path :d="mode.path" /></svg></button>
          <input v-if="activeCanvasMode?.mode === 'draw'" type="color" aria-label="画笔颜色" :value="penColor" :disabled="activeCanvasMode.readOnly" @input="setPenColor">
        </div>
        <aside v-if="extraDays.length && !auxiliary.active && activeKeyboardHints?.keys.length" class="shared-keyboard-hints" aria-label="快捷键提示">
          <small v-if="activeKeyboardHints.page">{{ activeKeyboardHints.page }}</small>
          <div v-for="item in activeKeyboardHints.items" :key="item.displayKey + item.title" class="shortcut-hint"><strong>{{ item.displayKey }}</strong><span>{{ item.title }}</span></div>
          <div class="pressed-keys">{{ activeKeyboardHints.keys.join(' + ') }}</div>
        </aside>
        <div v-if="resizingDays" class="day-resize-shield" />
        <div v-for="day in (extraDays.length ? selectedJournalDays.slice(1) : [])" :key="`resize:${day}`" class="day-divider" role="separator" tabindex="0" aria-orientation="horizontal" :aria-label="`调整${dayLabel(day)}上方分隔线`" :style="{ order: selectedJournalDays.indexOf(day) * 2 - 1 }"
          @pointerdown="startDayResize($event, day)" @pointermove="moveDayResize" @pointerup="stopDayResize" @pointercancel="stopDayResize" @lostpointercapture="stopDayResize" @keydown="keyboardDayResize($event, day)" />
        <section v-if="mounted" class="day-pane" :class="{ 'focused-day': !activeExtraDay }" :style="paneStyle(currentJournalDate || '')" :data-journal-day="currentJournalDate || undefined">
          <header v-if="extraDays.length" class="day-heading" @click="focusDay()">{{ dayLabel(currentJournalDate!) }}</header>
          <ProjectGraphEditor :key="editorKey" ref="editor" :shared-toolbar="extraDays.length > 0" @hints="keyboardHints[''] = $event" @mode="canvasModes[''] = $event" :document-id="documentId" :title="title" :storage="graphStorage" :view-state-key="`codeyun.project-graph.view:${library.ownerId}:${documentId}`" :gallery-active="dock.visible('gallery')" @gallery="receiveGallery('', $event)" @gallery-drop="galleryDrop('', $event)" :details-active="dock.visible('details') && !activeExtraDay" @auxiliary="!activeExtraDay && (auxiliary = $event)" @menu="paneMenu('', $event)" @command="focusDay(); menuCommand($event)" @details="!activeExtraDay && (details = $event)" @focus="focusDay()" @status="onStatus" @error="error = $event" @saved="refreshList" />
        </section>
        <section v-for="day in extraDays" :key="day" class="day-pane" :class="{ 'focused-day': activeExtraDay === day }" :style="paneStyle(day)" :data-journal-day="day">
          <header class="day-heading" @click="focusDay(day)">{{ dayLabel(day) }}</header>
          <JournalDayCanvas :ref="value => setExtraEditor(day, value)" :day="day" :library="library" :gallery-active="dock.visible('gallery')" @gallery="receiveGallery(day, $event)" @gallery-drop="galleryDrop(day, $event)" :details-active="dock.visible('details') && activeExtraDay === day"
            @hints="keyboardHints[day] = $event" @mode="canvasModes[day] = $event" @focus="focusDay(day)" @menu="paneMenu(day, $event)" @command="focusDay(day); menuCommand($event)" @details="activeExtraDay === day && (details = $event)" @auxiliary="activeExtraDay === day && (auxiliary = $event)" @saved="refreshList" @error="error = $event" />
        </section>
        <div v-if="journalEmpty" class="welcome"><p>选择日期查看每日记录</p></div>
        <div v-else-if="!mounted" class="welcome"><div class="welcome-icon">◇</div><h2>从一张图开始</h2><p>把想法连接起来，给每个节点写下正文。</p><button class="primary" :disabled="busy" @click="ask('new')">新建.prg</button><button :disabled="busy" @click="input?.click()">导入 .prg</button></div>
        <div v-if="busy" class="busy">正在处理…</div>
      </div>
      <div v-if="settingsActive" class="graph-settings"><ReaderSettingsPanel :theme="theme" appearance-label="外观" theme-description="画布与正文共用此主题。" @theme="theme = $event" /></div>
    </DockWorkspace>
    <input ref="input" type="file" accept=".prg" hidden @change="importDocument">
    <template v-if="contextMenu">
      <div class="menu-dismiss" @pointerdown="contextMenu = null" @contextmenu.prevent="contextMenu = null"></div>
      <div ref="contextElement" class="menu-items library-context" role="menu" aria-label="文件区菜单" :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }" @keydown="contextKeys" @contextmenu.prevent>
        <button role="menuitem" @click="ask('new')">新建.prg</button>
        <button role="menuitem" @click="ask('folder')">新建文件夹</button>
        <button role="menuitem" @click="contextMenu = null; input?.click()">导入 .prg</button>
        <template v-if="contextMenu.document">
          <hr>
          <button v-if="contextMenu.document.role === 'manager'" role="menuitem" @click="fileAction('share')">分享权限…</button>
          <button v-if="contextMenu.document.role === 'manager' && !contextMenu.document.collaborative" role="menuitem" @click="fileAction('collaborate')">启用多人协作</button>
          <button v-if="contextMenu.document.role === 'manager' && contextMenu.document.collaborative" role="menuitem" @click="fileAction('stop-collaboration')">结束协作并保存为普通文件</button>
          <button v-if="contextMenu.document.role === 'manager'" role="menuitem" @click="fileAction('rename')">重命名</button>
          <button v-if="contextMenu.document.role === 'manager'" role="menuitem" @click="fileAction('move')">移动到…</button>
          <button role="menuitem" @click="fileAction('copy')">另存为副本</button>
          <button role="menuitem" @click="fileAction('download')">下载文件</button>
          <template v-if="contextMenu.document.role === 'manager'"><hr><button role="menuitem" class="danger" @click="fileAction('delete')">删除</button></template>
        </template>
        <template v-if="contextMenu.folder"><hr><button role="menuitem" @click="ask('rename-folder')">重命名文件夹</button><button role="menuitem" class="danger" @click="ask('delete-folder')">删除文件夹</button></template>
      </div>
    </template>
    <GraphShareDialog v-if="shareDocument" :document-id="shareDocument.id" :title="shareDocument.title" :library="library" @close="shareDocument = undefined" />
    <div v-if="dialog" class="modal-backdrop" @keydown.esc="!busy && (dialog = '')">
      <form class="modal" role="dialog" aria-modal="true" :aria-label="dialogTitles[dialog]" @submit.prevent="submit">
        <h3>{{ dialogTitles[dialog] }}</h3>
        <template v-if="dialog === 'move'"><label for="graph-folder">目标文件夹</label><select id="graph-folder" v-model="targetFolder"><option value="">文件</option><option v-for="folder in folderRows" :key="folder.id" :value="folder.id">{{ folderPath(folder.id) }}</option></select></template>
        <p v-else-if="dialog === 'delete'">删除“{{ graphFileName(title) }}”？此操作无法撤销。</p>
        <p v-else-if="dialog === 'delete-folder'">删除当前空文件夹？</p>
        <template v-else><label for="graph-name">名称</label><input id="graph-name" v-model="name" autofocus autocomplete="off" maxlength="120"></template>
        <p v-if="error" class="dialog-error">{{ error }}</p>
        <div class="dialog-actions"><button type="button" :disabled="busy" @click="dialog = ''">取消</button><button class="primary" :disabled="busy" type="submit">{{ busy ? '处理中…' : '确定' }}</button></div>
      </form>
    </div>
  </main>
</template>

<style scoped>
.menu-items.library-context{position:fixed;right:auto;max-width:calc(100vw - 28px);max-height:calc(100dvh - 16px);overflow-y:auto}
.graph-workspace{height:100%;width:100%;min-height:0;min-width:0;overflow:hidden;display:flex;font-size:14px}.graph-files{padding:4px;flex:1;min-height:0}button,input,select{font:inherit;color:inherit}button{cursor:pointer;border:1px solid var(--reader-border);border-radius:6px;background:var(--reader-content);padding:7px 12px}button:hover{background:var(--reader-hover)}button:disabled{opacity:.5;cursor:default}a{color:inherit;text-decoration:none}.menu-dismiss{position:fixed;inset:0;z-index:20}.menu-items{position:absolute;top:36px;right:0;width:172px;padding:6px;background:var(--reader-content);border:1px solid var(--reader-border);border-radius:8px;box-shadow:0 10px 28px #17203320;z-index:21}.menu-items button{display:block;width:100%;border:0;text-align:left}.menu-items hr{border:0;border-top:1px solid #eef1f5;margin:5px}.danger{color:#be3737}.canvas{flex:1;min-height:0;position:relative;background:var(--reader-content)}.error{padding:10px 16px;background:#fff0ec;color:#a33725;font-size:12px}.error button{margin-left:10px;font-size:12px}.welcome{height:100%;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px}.welcome-icon{font-size:64px;color:#6898e8}.welcome h2{margin:0;font-size:22px}.welcome p{color:var(--reader-muted);margin:0 0 12px}.primary{background:#3269d9;color:white;border-color:#3269d9}.primary:hover{background:#285abf}.busy{position:absolute;inset:0;display:grid;place-items:center;background:var(--reader-content);z-index:10;pointer-events:auto}.modal-backdrop{position:fixed;inset:0;background:#0f172a55;display:grid;place-items:center;z-index:50}.modal{background:var(--reader-content);border-radius:12px;padding:24px;width:min(380px,85vw);box-shadow:0 20px 80px #0003}.modal h3{margin:0 0 22px;font-size:18px}.modal label{display:block;color:var(--reader-muted);font-size:12px;margin-bottom:8px}.modal input,.modal select{width:100%;box-sizing:border-box;background:var(--reader-content);border:1px solid var(--reader-border);padding:9px;border-radius:6px}.dialog-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:24px}.dialog-error{color:#b43d2c;font-size:12px}


.error{max-height:30%;overflow:auto;overflow-wrap:anywhere}
.welcome{min-height:0;overflow:auto;text-align:center;padding:12px;box-sizing:border-box}
.graph-settings{flex:1;min-height:0;overflow:auto;background:var(--reader-content)}
.journal-navigation{flex:1;min-height:0}
.day-pane{height:100%;min-height:0;display:flex;flex-direction:column}.day-pane :deep(iframe){flex:1;min-height:0}.canvas.multi-day{display:flex;flex-direction:column;overflow:hidden;gap:0}.multi-day .day-pane{box-sizing:border-box;flex:1 1 0;min-height:0;overflow:hidden;border:0}.shared-keyboard-hints{position:absolute;bottom:64px;left:12px;z-index:4;pointer-events:none;color:var(--reader-text);max-width:calc(100% - 24px);max-height:calc(100% - 80px);overflow:hidden;text-shadow:0 1px 3px var(--reader-content);font-size:12px}.shortcut-hint{display:flex;align-items:baseline;gap:15px;line-height:28px}.shortcut-hint strong{font-size:16px;font-weight:500;white-space:nowrap}.shortcut-hint span{color:var(--reader-muted)}.pressed-keys{font-size:30px;margin-top:24px}.shared-mode-toolbar{position:absolute;bottom:0;left:50%;transform:translateX(-50%);z-index:5;display:flex;align-items:center;gap:3px;padding:6px 8px;background:var(--reader-panel);border:1px solid var(--reader-border);border-radius:12px 12px 0 0}.shared-mode-toolbar button{border:0;padding:7px;display:flex;opacity:.5;background:transparent}.shared-mode-toolbar button[aria-pressed=true]{opacity:1;color:var(--reader-active-text)}.shared-mode-toolbar svg{width:20px;height:20px}.shared-mode-toolbar input{width:28px;height:28px;padding:2px;border:0}.day-divider{flex:0 0 9px;margin:-4px 0;cursor:row-resize;touch-action:none;position:relative;z-index:2;outline:none}.day-divider::after{content:"";position:absolute;left:0;right:0;top:4px;height:1px;background:var(--reader-border)}.day-divider:hover::after,.day-divider:focus-visible::after{background:var(--reader-active-text)}.day-resize-shield{position:fixed;inset:0;z-index:100;cursor:row-resize;user-select:none}.multi-day .focused-day .day-heading{color:var(--reader-active-text)}.day-heading{padding:6px 12px;background:var(--reader-content);font-size:12px;color:var(--reader-muted);cursor:pointer;flex:none}

</style>

<style scoped>
.graph-workspace{flex-direction:column}.graph-workspace > :deep(.dock-workspace){flex:1;min-height:0;width:100%}
</style>

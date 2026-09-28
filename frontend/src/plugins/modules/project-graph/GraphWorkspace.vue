<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { buildStandaloneRouteLocation } from '@/router/standalone'
import ReaderSettingsPanel from '@/standard/pdf/library/ReaderSettingsPanel.vue'
import { LIBRARY_READER_THEME_OPTIONS, type LibraryReaderTheme } from '@/standard/pdf/library/readerTheme'
import NodeDetailsTool from './NodeDetailsTool.vue'
import ProjectGraphEditor from './ProjectGraphEditor.vue'
import { graphBaseName, graphFileName } from './fileName'
import { createGraphLibrary, type GraphDocument, type GraphFolder } from './storage'

import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import { useDockLayout } from '@/components/docking/useDockLayout'
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue'
import { dockWindowMenuItems, executeDockWindowCommand, type WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu'
import EditorTabs from '@/components/editor-workspace/EditorTabs.vue'
import ResourceExplorer from '@/components/resource-explorer/ResourceExplorer.vue'
import type { ResourceNode } from '@/components/resource-explorer/resourceTree'
import { graphResourceTree } from './resourceTree'

const library = createGraphLibrary()
const graphStorage = library.storage
const changeGraphLibrary = library.change
const tabsKey = `codeyun.project-graph.tabs:${library.ownerId}`
const themeKey = `codeyun.project-graph.theme:${library.ownerId}`
const theme = ref<LibraryReaderTheme>('dark')
try { const saved = localStorage.getItem(themeKey); if (LIBRARY_READER_THEME_OPTIONS.some(option => option.value === saved)) theme.value = saved as LibraryReaderTheme } catch { /* Default dark. */ }
watch(theme, value => { try { localStorage.setItem(themeKey, value) } catch { /* Session preference. */ } })
const dock = useDockLayout(`codeyun.project-graph.dock:${library.ownerId}`, [
  { id: 'files', title: '资源管理器', icon: 'library', position: 'left', open: true },
  { id: 'details', title: '节点正文', icon: 'document', position: 'right' },
  { id: 'settings', title: '配置', icon: 'settings', position: 'left' },
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
  else if (menuReady.value) editor.value?.executeMenu(id)
}
const editor = ref<InstanceType<typeof ProjectGraphEditor>>()
const auxiliary = ref<{ tabs: { id: string; title: string }[]; active: string }>({ tabs: [], active: '' })
const details = ref<{ id: string; title: string; value: unknown[] } | null>(null)
const detailsTool = ref<InstanceType<typeof NodeDetailsTool>>()
function editDetails(id: string, value: unknown[]) { editor.value?.updateDetails(id, value) }
const documentId = computed(() => typeof route.query.doc === 'string' ? route.query.doc : '')
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
const current = computed(() => documents.value.find(item => item.id === documentId.value))
const fileTabs = computed(() => opened.value.map(id => ({ id, title: graphFileName(documents.value.find(doc => doc.id === id)?.title ?? (id === documentId.value ? title.value : id)) })))
const tabs = computed(() => [...fileTabs.value, ...auxiliary.value.tabs.map(tab => ({ ...tab, id: `aux:${tab.id}` }))])
const tree = computed(() => graphResourceTree(folders.value, documents.value, expanded.value))
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
function activateTab(id: string) { if (id.startsWith('aux:')) { editor.value?.focusAuxiliary(id.slice(4)); return }; const doc = documents.value.find(doc => doc.id === id); if (doc) void open(doc) }
function moveTab(id: string, before: string) {
  if (id === before) return
  const remaining = opened.value.filter(item => item !== id)
  const index = remaining.indexOf(before)
  if (!opened.value.includes(id) || index < 0) return
  remaining.splice(index, 0, id); opened.value = remaining
}
async function closeTab(id: string) {
  if (id.startsWith('aux:')) { editor.value?.closeAuxiliary(id.slice(4)); return }
  await run(async () => {
    if (id === documentId.value) await flush()
    const index = opened.value.indexOf(id)
    opened.value = opened.value.filter(item => item !== id)
    if (id === documentId.value) {
      const next = documents.value.find(doc => doc.id === opened.value[Math.min(index, opened.value.length - 1)])
      await mountDocument(next?.id ?? '', next?.title ?? '')
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
async function refreshList() { const result = await library.list(); documents.value = result.documents; folders.value = result.folders }
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; contextMenu.value = null; error.value = ''
  try { await action() } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function flush() { await detailsTool.value?.flush(); if (mounted.value) await editor.value?.flush() }
async function mountDocument(id: string, fileTitle: string) {
  auxiliary.value = { tabs: [], active: '' }
  details.value = null
  menuReady.value = false
  mounted.value = false; await nextTick()
  await router.replace({ query: { ...route.query, doc: id || undefined } })
  if (id && !opened.value.includes(id)) opened.value.push(id)
  title.value = fileTitle; mounted.value = !!id
  await nextTick()
}
async function open(doc: GraphDocument) {
  if (doc.id === documentId.value) { editor.value?.focusAuxiliary(''); return }
  await run(async () => { await flush(); await mountDocument(doc.id, doc.title) })
}
function ask(kind: string) {
  contextMenu.value = null; dialog.value = kind
  name.value = kind === 'rename' ? graphFileName(title.value) : kind === 'rename-folder' ? folders.value.find(item => item.id === folderId.value)?.title ?? '' : kind === 'new' ? '未命名图.prg' : kind === 'copy' ? `${graphBaseName(title.value)} 副本.prg` : '新建文件夹'
  targetFolder.value = current.value?.folderId ?? ''
}
/** Bind commands to the right-clicked file, not whichever editor was open. */
async function fileAction(kind: string) {
  const doc = contextMenu.value?.document
  if (!doc) return
  contextMenu.value = null
  if (doc.id === documentId.value) {
    if (kind === 'download') editor.value?.exportDocument()
    else ask(kind)
    return
  }
  await run(async () => {
    await flush()
    await mountDocument(doc.id, doc.title)
    if (kind === 'download') { await flush(); editor.value?.exportDocument() }
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
        await changeGraphLibrary({ type: 'document', id: documentId.value, remove: true })
        const index = opened.value.indexOf(documentId.value)
        opened.value = opened.value.filter(id => id !== documentId.value)
        const next = documents.value.find(doc => doc.id === opened.value[Math.min(index, opened.value.length - 1)])
        await mountDocument(next?.id ?? '', next?.title ?? '')
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
  if (['files', 'details', 'settings'].includes(command)) dock.open(command)
  else if (['new', 'copy'].includes(command)) ask(command)
  else if (command === 'import') input.value?.click()
  else if (command === 'save') void run(flush)
  else if (command === 'download') void run(async () => { await flush(); editor.value?.exportDocument() })
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
    <WorkspaceMenu :items="workspaceMenus" :disabled="busy || (mounted && !menuReady)" @refresh="editor?.refreshMenu()" @select="selectMenu" />
    <DockWorkspace :dock="dock">
      <template #files>
        <div class="graph-files" v-context-menu.prevent="($event: MouseEvent) => showContext($event)">
          <ResourceExplorer :nodes="tree" :selected-id="`file:${documentId}`" @open="openNode" @toggle="toggleFolders" @contextmenu="treeContext" />
        </div>
      </template>
      <template #settings><ReaderSettingsPanel :theme="theme" appearance-label="外观" theme-description="画布与节点正文共用此主题。" @theme="theme = $event" /></template>
      <template #details>
        <NodeDetailsTool v-if="details" :key="`${documentId}:${details.id}`" ref="detailsTool" :node="details" @change="editDetails" />
        <p v-else class="details-empty">单击一个节点，查看和编辑正文。</p>
      </template>
      <EditorTabs :tabs="tabs" :active="auxiliary.active ? `aux:${auxiliary.active}` : documentId" @activate="activateTab" @close="closeTab" @move="moveTab" />
      <div v-if="error" class="error" role="alert">{{ error }} <button v-if="mounted" @click="run(flush)">重试保存</button><button v-if="mounted" @click="editor?.exportDocument()">下载文件</button></div>
      <div class="canvas">
        <ProjectGraphEditor v-if="mounted" :key="documentId" ref="editor" :document-id="documentId" :title="title" :storage="graphStorage" :view-state-key="`codeyun.project-graph.view:${library.ownerId}:${documentId}`" :details-active="dock.visible('details')" @auxiliary="auxiliary = $event" @menu="receiveMenu" @command="menuCommand" @details="details = $event" @status="onStatus" @error="error = $event" @saved="refreshList" />
        <div v-else class="welcome"><div class="welcome-icon">◇</div><h2>从一张图开始</h2><p>把想法连接起来，给每个节点写下正文。</p><button class="primary" :disabled="busy" @click="ask('new')">新建.prg</button><button :disabled="busy" @click="input?.click()">导入 .prg</button></div>
        <div v-if="busy" class="busy">正在处理…</div>
      </div>
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
          <button role="menuitem" @click="fileAction('rename')">重命名</button>
          <button role="menuitem" @click="fileAction('move')">移动到…</button>
          <button role="menuitem" @click="fileAction('copy')">另存为副本</button>
          <button role="menuitem" @click="fileAction('download')">下载文件</button>
          <hr><button role="menuitem" class="danger" @click="fileAction('delete')">删除</button>
        </template>
        <template v-if="contextMenu.folder"><hr><button role="menuitem" @click="ask('rename-folder')">重命名文件夹</button><button role="menuitem" class="danger" @click="ask('delete-folder')">删除文件夹</button></template>
      </div>
    </template>
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
.details-empty { padding:8px;font-size:12px; }

.menu-items.library-context{position:fixed;right:auto;max-width:calc(100vw - 28px);max-height:calc(100dvh - 16px);overflow-y:auto}
.graph-workspace{height:100%;width:100%;min-height:0;min-width:0;overflow:hidden;display:flex;font-size:14px}.graph-files{padding:4px;flex:1;min-height:0}button,input,select{font:inherit;color:inherit}button{cursor:pointer;border:1px solid var(--reader-border);border-radius:6px;background:var(--reader-content);padding:7px 12px}button:hover{background:var(--reader-hover)}button:disabled{opacity:.5;cursor:default}a{color:inherit;text-decoration:none}.menu-dismiss{position:fixed;inset:0;z-index:20}.menu-items{position:absolute;top:36px;right:0;width:172px;padding:6px;background:var(--reader-content);border:1px solid var(--reader-border);border-radius:8px;box-shadow:0 10px 28px #17203320;z-index:21}.menu-items button{display:block;width:100%;border:0;text-align:left}.menu-items hr{border:0;border-top:1px solid #eef1f5;margin:5px}.danger{color:#be3737}.canvas{flex:1;min-height:0;position:relative;background:var(--reader-content)}.error{padding:10px 16px;background:#fff0ec;color:#a33725;font-size:12px}.error button{margin-left:10px;font-size:12px}.welcome{height:100%;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px}.welcome-icon{font-size:64px;color:#6898e8}.welcome h2{margin:0;font-size:22px}.welcome p{color:var(--reader-muted);margin:0 0 12px}.primary{background:#3269d9;color:white;border-color:#3269d9}.primary:hover{background:#285abf}.busy{position:absolute;inset:0;display:grid;place-items:center;background:var(--reader-content);z-index:10;pointer-events:auto}.modal-backdrop{position:fixed;inset:0;background:#0f172a55;display:grid;place-items:center;z-index:50}.modal{background:var(--reader-content);border-radius:12px;padding:24px;width:min(380px,85vw);box-shadow:0 20px 80px #0003}.modal h3{margin:0 0 22px;font-size:18px}.modal label{display:block;color:var(--reader-muted);font-size:12px;margin-bottom:8px}.modal input,.modal select{width:100%;box-sizing:border-box;background:var(--reader-content);border:1px solid var(--reader-border);padding:9px;border-radius:6px}.dialog-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:24px}.dialog-error{color:#b43d2c;font-size:12px}


.error{max-height:30%;overflow:auto;overflow-wrap:anywhere}
.welcome{min-height:0;overflow:auto;text-align:center;padding:12px;box-sizing:border-box}

</style>

<style scoped>
.graph-workspace{flex-direction:column}.graph-workspace > :deep(.dock-workspace){flex:1;min-height:0;width:100%}
</style>

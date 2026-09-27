<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import ProjectGraphEditor from './ProjectGraphEditor.vue'
import { graphBaseName, graphFileName } from './fileName'
import { browserGraphStorage, listBrowserGraphDocuments, listGraphFolders, changeGraphLibrary, type GraphDocument, type GraphFolder } from './storage'

const route = useRoute(), router = useRouter()
const editor = ref<InstanceType<typeof ProjectGraphEditor>>()
const documentId = computed(() => typeof route.query.doc === 'string' ? route.query.doc : '')
const title = ref(''), documents = ref<GraphDocument[]>([]), folders = ref<GraphFolder[]>([])
const folderId = ref(''), status = ref('loading'), error = ref(''), busy = ref(false), mounted = ref(false)
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
const labels: Record<string, string> = { loading: '正在打开…', saved: '已保存', saving: '保存中…', unsaved: '待保存' }
const current = computed(() => documents.value.find(item => item.id === documentId.value))
const visibleFiles = computed(() => documents.value.filter(item => (item.folderId ?? '') === folderId.value))
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
async function refreshList() { [documents.value, folders.value] = await Promise.all([listBrowserGraphDocuments(), listGraphFolders()]) }
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; contextMenu.value = null; error.value = ''
  try { await action() } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function flush() { if (mounted.value) await editor.value?.flush() }
async function mountDocument(id: string, fileTitle: string) {
  mounted.value = false; await nextTick()
  await router.replace({ query: { ...route.query, doc: id || undefined } })
  title.value = fileTitle; status.value = 'loading'; mounted.value = !!id
  await nextTick()
}
async function open(doc: GraphDocument) {
  if (doc.id === documentId.value) return
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
      await changeGraphLibrary({ type: 'folder', folder: { id: kind === 'folder' ? crypto.randomUUID() : folderId.value, title: text, parentId: kind === 'folder' ? folderId.value : existing?.parentId ?? '' } })
    } else if (kind === 'delete-folder') {
      const parent = folders.value.find(item => item.id === folderId.value)?.parentId ?? ''
      await changeGraphLibrary({ type: 'remove-folder', id: folderId.value }); folderId.value = parent
    } else {
      await flush()
      if (kind === 'new' || kind === 'copy') {
        const id = crypto.randomUUID()
        if (kind === 'copy') {
          const source = await browserGraphStorage.read(documentId.value)
          if (!source) throw new Error('原文件不存在')
          await browserGraphStorage.write(id, text, source.bytes, 0)
        }
        await mountDocument(id, text); await flush()
        await changeGraphLibrary({ type: 'document', id, folderId: folderId.value })
      } else if (kind === 'rename') {
        await changeGraphLibrary({ type: 'document', id: documentId.value, title: text })
        await mountDocument(documentId.value, text)
      } else if (kind === 'move') {
        await changeGraphLibrary({ type: 'document', id: documentId.value, folderId: targetFolder.value })
        folderId.value = targetFolder.value
      } else if (kind === 'delete') {
        await changeGraphLibrary({ type: 'document', id: documentId.value, remove: true })
        await mountDocument('', '')
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
    const id = crypto.randomUUID(), fileTitle = graphBaseName(file.name)
    await browserGraphStorage.write(id, fileTitle, new Uint8Array(await file.arrayBuffer()), 0)
    await changeGraphLibrary({ type: 'document', id, folderId: folderId.value })
    await refreshList(); await mountDocument(id, fileTitle)
  })
  if (input.value) input.value.value = ''
}
function onStatus(value: string) { status.value = value; if (value === 'saved') error.value = '' }
onMounted(() => run(async () => {
  await refreshList()
  const doc = documents.value.find(item => item.id === documentId.value) ?? documents.value[0]
  if (doc) { folderId.value = doc.folderId ?? ''; await mountDocument(doc.id, doc.title) }
}))
onBeforeRouteLeave(async () => {
  try { await flush(); return true } catch (reason) { error.value = String(reason); return false }
})
const dialogTitles: Record<string, string> = { new: '新建图文件', folder: '新建文件夹', rename: '重命名', 'rename-folder': '重命名文件夹', 'delete-folder': '删除文件夹', move: '移动到', copy: '另存为副本', delete: '删除图文件' }
</script>

<template>
  <main class="graph-workspace" :aria-busy="busy">
    <aside class="library" v-context-menu.prevent="($event: MouseEvent) => (showContext($event))">
      <nav aria-label="文件夹" class="folders">
        <button :class="{ selected: !folderId }" @click="folderId = ''" v-context-menu.stop.prevent="($event: MouseEvent) => (showContext($event, ''))">▱ 文件</button>
        <button v-for="folder in folderRows" :key="folder.id" :class="{ selected: folderId === folder.id }" :style="{ paddingLeft: `${14 + folder.depth * 16}px` }" @click="folderId = folder.id" v-context-menu.stop.prevent="($event: MouseEvent) => (showContext($event, folder.id))">▱ {{ folder.title }}</button>
      </nav>
      <nav class="files" aria-label="图文件">
        <button v-for="doc in visibleFiles" :key="doc.id" :disabled="busy" :title="graphFileName(doc.title)" :class="{ active: documentId === doc.id }" @click="open(doc)" v-context-menu.stop.prevent="($event: MouseEvent) => (showContext($event, undefined, doc))"><img src="./icon.png" width="18" height="18" alt="" style="flex-shrink:0"><span class="file-name">{{ graphFileName(doc.title) }}</span></button>
        <p v-if="!visibleFiles.length" class="empty-folder">此文件夹还没有图文件</p>
      </nav>
      <footer><span title="文件保存在当前浏览器，尚未同步到服务器">本浏览器 · 自动保存</span><span v-if="mounted" role="status" class="save-status">{{ error ? '保存或打开失败' : labels[status] || status }}</span><div><a href="/plugins/project-graph/source.zip" download>源码</a><a href="/plugins/project-graph/LICENSE.txt" target="_blank">GPL-3.0</a></div></footer>
    </aside>
    <section class="workspace">

      <div v-if="error" class="error" role="alert">{{ error }} <button v-if="mounted" @click="run(flush)">重试保存</button><button v-if="mounted" @click="editor?.exportDocument()">下载文件</button></div>
      <div class="canvas">
        <ProjectGraphEditor v-if="mounted" :key="documentId" ref="editor" :document-id="documentId" :title="title" :storage="browserGraphStorage" @status="onStatus" @error="error = $event" @saved="refreshList" />
        <div v-else class="welcome"><div class="welcome-icon">◇</div><h2>从一张图开始</h2><p>把想法连接起来，给每个节点写下正文。</p><button class="primary" :disabled="busy" @click="ask('new')">新建图文件</button><button :disabled="busy" @click="input?.click()">导入 .prg</button></div>
        <div v-if="busy" class="busy">正在处理…</div>
      </div>
    </section>
    <input ref="input" type="file" accept=".prg" hidden @change="importDocument">
    <template v-if="contextMenu">
      <div class="menu-dismiss" @pointerdown="contextMenu = null" @contextmenu.prevent="contextMenu = null"></div>
      <div ref="contextElement" class="menu-items library-context" role="menu" aria-label="文件区菜单" :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }" @keydown="contextKeys" @contextmenu.prevent>
        <button role="menuitem" @click="ask('new')">新建图文件</button>
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
.save-status{display:block;margin-top:6px}
.menu-items.library-context{position:fixed;right:auto;max-width:calc(100vw - 28px);max-height:calc(100dvh - 16px);overflow-y:auto}
.graph-workspace{height:100%;width:100%;min-height:0;min-width:0;box-sizing:border-box;overflow:hidden;display:flex;background:#f8fafc;color:#263248;font-size:14px}.library{width:232px;flex-shrink:0;display:flex;flex-direction:column;border-right:1px solid #e2e8f0;background:#f8fafc}.brand{height:66px;display:flex;align-items:center;justify-content:space-between;padding:0 18px}.brand strong{font-size:18px}.brand button{font-size:24px;border:0;background:transparent;padding:0 6px}button,input,select{font:inherit;color:inherit}button{cursor:pointer;border:1px solid #dbe2ec;border-radius:6px;background:white;padding:7px 12px}button:hover{background:#edf3ff}button:disabled{opacity:.5;cursor:default}.library-actions{display:flex;gap:8px;padding:0 14px 16px}.library-actions button{font-size:12px}.folders{max-height:30%;overflow:auto;padding:0 8px 12px;border-bottom:1px solid #e2e8f0}.folders button,.files button{display:flex;width:100%;text-align:left;border:0;background:transparent;padding:10px 12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.folders button.selected{background:#e8eef6}.section-label{padding:12px 16px 6px;color:#7b8799;font-size:12px;display:flex;align-items:center;justify-content:space-between}.section-label button{border:0;background:transparent;padding:0 5px}.files{padding:4px 8px;flex:1;overflow:auto}.files button{gap:10px;align-items:center}.file-name{overflow:hidden;text-overflow:ellipsis}.files button.active{background:#e7efff;color:#2563eb}.file-icon{font-size:20px}.empty-folder{color:#94a3b8;text-align:center;font-size:12px;margin-top:24px}footer{padding:16px;font-size:11px;color:#8995a7;border-top:1px solid #e2e8f0}footer div{display:flex;gap:12px;margin-top:8px}a{color:inherit;text-decoration:none}.workspace{flex:1;min-width:0;display:flex;flex-direction:column}.file-bar{height:66px;box-sizing:border-box;display:flex;align-items:center;gap:16px;padding:10px 20px;background:white;border-bottom:1px solid #e2e8f0}.file-info{display:flex;flex-direction:column;gap:4px;min-width:0;flex:1}.file-info strong{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.breadcrumb{font-size:11px;color:#94a3b8}.status{font-size:12px;color:#7b8c82;white-space:nowrap}.status.failed{color:#c24132}.file-menu{position:relative}.menu-dismiss{position:fixed;inset:0;z-index:20}.menu-items{position:absolute;top:36px;right:0;width:172px;padding:6px;background:white;border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 10px 28px #17203320;z-index:21}.menu-items button{display:block;width:100%;border:0;text-align:left}.menu-items hr{border:0;border-top:1px solid #eef1f5;margin:5px}.danger{color:#be3737}.canvas{flex:1;min-height:0;position:relative}.error{padding:10px 16px;background:#fff0ec;color:#a33725;font-size:12px}.error button{margin-left:10px;font-size:12px}.welcome{height:100%;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px}.welcome-icon{font-size:64px;color:#6898e8}.welcome h2{margin:0;font-size:22px}.welcome p{color:#94a3b8;margin:0 0 12px}.primary{background:#3269d9;color:white;border-color:#3269d9}.primary:hover{background:#285abf}.busy{position:absolute;inset:0;display:grid;place-items:center;background:#f8fafc55;z-index:10;pointer-events:auto}.modal-backdrop{position:fixed;inset:0;background:#0f172a55;display:grid;place-items:center;z-index:50}.modal{background:white;border-radius:12px;padding:24px;width:min(380px,85vw);box-shadow:0 20px 80px #0003}.modal h3{margin:0 0 22px;font-size:18px}.modal label{display:block;color:#64748b;font-size:12px;margin-bottom:8px}.modal input,.modal select{width:100%;box-sizing:border-box;border:1px solid #cdd7e5;padding:9px;border-radius:6px}.dialog-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:24px}.dialog-error{color:#b43d2c;font-size:12px}@media(max-width:720px){.library{width:175px}.brand{padding:0 10px}.brand strong{font-size:16px}.file-bar{padding:10px;gap:8px}.library-actions{padding:0 8px 12px;gap:4px}.status{font-size:10px}}
/* Fit the host content area (which already excludes the CodeYun header).
   Only the folder/file lists and document body scroll within their own panes. */
.library{width:clamp(120px,24%,232px);max-width:40%;min-width:0;min-height:0;overflow:hidden;box-sizing:border-box}
.workspace,.files{min-height:0;min-width:0}
.brand,.file-bar,footer{flex-shrink:0}
.brand{overflow:hidden}.brand strong{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.folders,.files{overflow-x:hidden;overflow-y:auto}
.folders button,.files button{box-sizing:border-box;min-width:0}
.error{max-height:30%;overflow:auto;overflow-wrap:anywhere}
.welcome{min-height:0;overflow:auto;text-align:center;padding:12px;box-sizing:border-box}
@media(max-width:540px){.file-bar{gap:6px}.status{max-width:64px;overflow:hidden;text-overflow:ellipsis}.file-menu>button{padding:6px}.brand strong{font-size:14px}}
</style>

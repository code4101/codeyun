<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  Edit,
  MoreFilled,
  Share,
} from '@element-plus/icons-vue'

import {
  createWorkbook,
  deleteWorkbook,
  fetchWorkbooks,
  saveAsWorkbook,
  updateWorkbook,
  type NoteSheetResourceRole,
  type WorkbookSummary,
} from '@/api/noteSheets'
import { useUserStore } from '@/store/userStore'
import NoteSheetAccessDialog from '../components/NoteSheetAccessDialog.vue'

import DockWorkspace from '@/components/docking/DockWorkspace.vue'
import { useDockLayout } from '@/components/docking/useDockLayout'
import EditorTabs from '@/components/editor-workspace/EditorTabs.vue'
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue'
import type { WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu'
import ResourceExplorer from '@/components/resource-explorer/ResourceExplorer.vue'
import ReaderSettingsPanel from '@/standard/pdf/library/ReaderSettingsPanel.vue'
import { LIBRARY_READER_THEME_OPTIONS, type LibraryReaderTheme } from '@/standard/pdf/library/readerTheme'
const WorkbookEditorHost = defineAsyncComponent(() => import('./WorkbookEditorHost.vue').then(module => module.default))

type WorkbookFilter = 'all' | 'mine' | 'other'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const ownerKey = `codeyun.sheets:${userStore.user?.id ?? 'session'}`
const dock = useDockLayout(`${ownerKey}:dock`, [
  { id: 'files', title: '工作簿', icon: 'library', position: 'left', open: true },
  { id: 'details', title: '工作簿信息', icon: 'document', position: 'right' },
])
const theme = ref<LibraryReaderTheme>('standard')
try {
  const saved = localStorage.getItem(`${ownerKey}:theme`)
  if (LIBRARY_READER_THEME_OPTIONS.some(option => option.value === saved)) theme.value = saved as LibraryReaderTheme
} catch { /* 会话内仍可使用。 */ }
watch(theme, value => { try { localStorage.setItem(`${ownerKey}:theme`, value) } catch { /* 会话偏好。 */ } })
const opened = ref<number[]>([])
let restoredTabs: number[] = []
try {
  const saved = JSON.parse(localStorage.getItem(`${ownerKey}:tabs`) || '[]')
  if (Array.isArray(saved)) restoredTabs = saved.filter(id => Number.isInteger(id) && id > 0)
} catch { /* 无有效历史时从工作簿库开始。 */ }
watch(opened, ids => { try { localStorage.setItem(`${ownerKey}:tabs`, JSON.stringify(ids)) } catch { /* 会话仍可使用。 */ } }, { deep: true })
const activeId = ref<number | null>(null)
const settingsActive = ref(false)
function openSettings() { settingsActive.value = true }
const sheetByWorkbook = ref<Record<number, string | undefined>>({})
const editor = ref<InstanceType<typeof WorkbookEditorHost>>()
const switching = ref(false)
const errorText = ref('')
const loading = ref(false)
const workbooks = ref<WorkbookSummary[]>([])
const searchText = ref('')
const workbookFilter = ref<WorkbookFilter>('all')

const accessDialogVisible = ref(false)
const accessDialogWorkbook = ref<WorkbookSummary | null>(null)

const currentUserId = computed(() => userStore.user?.id ?? null)
const normalizedSearchText = computed(() => searchText.value.trim().toLowerCase())

const totalSheetCount = computed(() => (
  workbooks.value.reduce((total, workbook) => total + workbook.sheet_count, 0)
))

const filteredWorkbooks = computed(() => {
  const query = normalizedSearchText.value
  return workbooks.value.filter((workbook) => {
    const isMine = workbook.owner_user_id != null && workbook.owner_user_id === currentUserId.value
    if (workbookFilter.value === 'mine' && !isMine) {
      return false
    }
    if (workbookFilter.value === 'other' && isMine) {
      return false
    }
    if (!query) {
      return true
    }
    return workbook.title.toLowerCase().includes(query)
      || String(workbook.id).includes(query)
      || accessRoleLabel(workbook.access?.role).toLowerCase().includes(query)
  })
})

const filterOptions: Array<{ value: WorkbookFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'mine', label: '我创建的' },
  { value: 'other', label: '其他可访问' },
]

function canManageWorkbook(workbook: WorkbookSummary) {
  return workbook.access?.capabilities.can_manage_access ?? true
}

function accessRoleLabel(role?: NoteSheetResourceRole | null) {
  switch (role) {
    case 'manager':
      return '可管理'
    case 'editor':
      return '可编辑'
    case 'viewer':
      return '只读'
    case 'deny':
      return '无权限'
    default:
      return '未知'
  }
}

function formatDateTime(timestamp: number) {
  if (!timestamp) {
    return '-'
  }
  const date = new Date(timestamp * 1000)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:${minute}`
}

function normalizePositiveInt(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value
  const numeric = Number(raw)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

async function reloadWorkbooks() {
  loading.value = true
  try {
    workbooks.value = await fetchWorkbooks()
    errorText.value = ''
  } catch (error) {
    console.warn('Failed to load note sheet workbooks:', error)
    errorText.value = '加载工作簿失败，请重试'
    ElMessage.error(errorText.value)
  } finally {
    loading.value = false
  }
}

async function initializeLibraryPage() {
  if (userStore.isAuthenticated && !userStore.user && !userStore.loading) {
    await userStore.fetchUserProfile()
  }
  await reloadWorkbooks()
  opened.value = [...new Set(restoredTabs)].filter(id => workbooks.value.some(book => book.id === id))
  const id = normalizePositiveInt(route.query.workbook)
  if (id != null && workbooks.value.some(book => book.id === id)) {
    await openById(id, typeof route.query.sheet === 'string' ? route.query.sheet : undefined)
  }
}

function resolveWorkbookHref(workbookId: number, sheetId?: number | null) {
  return router.resolve({
    path: `/workbook/${workbookId}`,
    query: sheetId != null ? { sheet: String(sheetId) } : undefined,
  }).href
}

function openWorkbook(workbook: WorkbookSummary, sheetId?: number | null) {
  void openById(workbook.id, sheetId == null ? undefined : String(sheetId))
}

async function handleCreateWorkbook() {
  try {
    const { value } = await ElMessageBox.prompt('请输入工作簿名称', '新建工作簿', {
      inputValue: '',
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    const workbook = await createWorkbook({ title: value.trim() })
    await reloadWorkbooks()
    openWorkbook(workbook)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '操作失败，请重试')
  }
}

async function handleRenameWorkbook(workbook: WorkbookSummary) {
  if (!canManageWorkbook(workbook)) {
    ElMessage.warning('没有权限重命名该工作簿')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入工作簿名称', '重命名工作簿', {
      inputValue: workbook.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    const nextTitle = value.trim()
    if (nextTitle === workbook.title) {
      return
    }
    await updateWorkbook(workbook.id, { title: nextTitle })
    await reloadWorkbooks()
    if (activeId.value === workbook.id) await editor.value?.refresh()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '操作失败，请重试')
  }
}

async function handleSaveAsWorkbook(workbook: WorkbookSummary, mode: 'template' | 'duplicate') {
  const modeLabel = mode === 'template' ? '模版' : '副本'
  const defaultTitle = `${workbook.title} ${modeLabel}`

  try {
    const { value } = await ElMessageBox.prompt('请输入新工作簿名称', `另存为${modeLabel}`, {
      inputValue: defaultTitle,
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    if (activeId.value === workbook.id) await editor.value?.flush()
    const nextWorkbook = await saveAsWorkbook(workbook.id, {
      mode,
      title: value.trim(),
    })
    await reloadWorkbooks()
    openWorkbook(nextWorkbook, nextWorkbook.sheets[0]?.id ?? null)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '操作失败，请重试')
  }
}

async function handleDeleteWorkbook(workbook: WorkbookSummary) {
  if (!canManageWorkbook(workbook)) {
    ElMessage.warning('没有权限删除该工作簿')
    return
  }

  try {
    await ElMessageBox.confirm(
      `工作簿“${workbook.title}”会移入回收站，其中未被其它工作簿引用的工作表也会一起移入回收站。`,
      '删除工作簿',
      {
        confirmButtonText: '移入回收站',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    if (activeId.value === workbook.id) await editor.value?.flush()
    await deleteWorkbook(workbook.id)
    opened.value = opened.value.filter(id => id !== workbook.id)
    if (activeId.value === workbook.id) { activeId.value = null; await syncAddress() }
    await reloadWorkbooks()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error instanceof Error ? error.message : '操作失败，请重试')
  }
}

function openAccessDialog(workbook: WorkbookSummary) {
  if (!canManageWorkbook(workbook)) {
    ElMessage.warning('没有权限管理该工作簿')
    return
  }

  accessDialogWorkbook.value = workbook
  accessDialogVisible.value = true
}

function handleWorkbookCommand(command: string | number | object, workbook: WorkbookSummary) {
  switch (command) {
    case 'rename':
      void handleRenameWorkbook(workbook)
      break
    case 'access':
      void openAccessDialog(workbook)
      break
    case 'template':
      void handleSaveAsWorkbook(workbook, 'template')
      break
    case 'duplicate':
      void handleSaveAsWorkbook(workbook, 'duplicate')
      break
    case 'delete':
      void handleDeleteWorkbook(workbook)
      break
  }
}

const current = computed(() => workbooks.value.find(book => book.id === activeId.value))
const tabs = computed(() => [
  { id: 'library', title: '工作簿库', closable: false },
  ...opened.value.map(id => ({ id: String(id), title: workbooks.value.find(book => book.id === id)?.title ?? `工作簿 ${id}` })),
  ...(settingsActive.value ? [{ id: 'view:settings', title: '设置' }] : []),
])
const tree = computed(() => filteredWorkbooks.value.map(book => ({ id: String(book.id), name: book.title, kind: 'file' as const })))
const menus = computed<WorkspaceMenuItem[]>(() => [
  { id: 'file', label: '文件', children: [
    { id: 'new', label: '新建工作簿' },
    { id: 'library', label: '工作簿库' },
    { id: 'window', label: '在新窗口打开', disabled: !current.value },
    { id: 'rename', label: '重命名', disabled: !current.value || !canManageWorkbook(current.value) },
    { id: 'duplicate', label: '另存为副本', disabled: !current.value },
    { id: 'template', label: '另存为模版', disabled: !current.value },
    { id: 'file-separator', label: '', separator: true },
    { id: 'trash', label: '回收站' },
  ] },
  { id: 'view', label: '视图', children: [
    { id: 'files', label: '工作簿侧栏' }, { id: 'details', label: '工作簿信息' },
    { id: 'settings', label: '打开设置' }, { id: 'reset', label: '重置布局' },
    { id: 'refresh', label: '刷新工作簿列表' },
  ] },
])
async function syncAddress() {
  await router.replace({ query: { ...route.query, workbook: activeId.value == null ? undefined : String(activeId.value), sheet: activeId.value == null ? undefined : sheetByWorkbook.value[activeId.value] } })
}
async function openById(id: number | null, sheet?: string) {
  settingsActive.value = false
  if (switching.value || (id === activeId.value && sheet === undefined)) return
  switching.value = true
  try {
    await editor.value?.flush()
    if (id != null && !workbooks.value.some(book => book.id === id)) await reloadWorkbooks()
    if (id != null && !workbooks.value.some(book => book.id === id)) throw new Error('工作簿不存在或无访问权限')
    if (id != null && !opened.value.includes(id)) opened.value.push(id)
    if (id != null && sheet !== undefined) sheetByWorkbook.value[id] = sheet
    activeId.value = id
    errorText.value = ''
    await syncAddress()
  } catch (error) { errorText.value = error instanceof Error ? error.message : String(error) }
  finally { switching.value = false }
}
function activateTab(key: string) {
  if (key === 'view:settings') { openSettings(); return }
  void openById(key === 'library' ? null : Number(key))
}
async function closeTab(key: string) {
  if (key === 'view:settings') { settingsActive.value = false; return }
  if (key === 'library' || switching.value) return
  const id = Number(key)
  if (id === activeId.value) {
    await openById(null)
    if (activeId.value === id) return
  }
  opened.value = opened.value.filter(item => item !== id)
}
function moveTab(key: string, before: string) {
  if (key === 'library' || key === 'view:settings' || before === 'library' || before === 'view:settings' || key === before) return
  const ids = opened.value.filter(id => id !== Number(key))
  const index = ids.indexOf(Number(before))
  if (index < 0) return
  ids.splice(index, 0, Number(key)); opened.value = ids
}
function selectMenu(command: string) {
  if (command === 'new') void handleCreateWorkbook()
  else if (command === 'library') void openById(null)
  else if (command === 'settings') openSettings()
  else if (command === 'files' || command === 'details') dock.open(command)
  else if (command === 'reset') dock.reset()
  else if (command === 'refresh') void reloadWorkbooks()
  else if (command === 'trash') void router.push('/notes/trash')
  else if (command === 'window' && current.value) window.open(resolveWorkbookHref(current.value.id, normalizePositiveInt(sheetByWorkbook.value[current.value.id])), '_blank', 'noopener,noreferrer')
  else if (current.value) handleWorkbookCommand(command, current.value)
}
async function leaveWorkbook() {
  const id = activeId.value
  await openById(null)
  if (activeId.value == null && id != null) opened.value = opened.value.filter(item => item !== id)
  await reloadWorkbooks()
}
function rememberSheet(sheet?: string) {
  if (activeId.value == null) return
  sheetByWorkbook.value[activeId.value] = sheet
  void syncAddress()
}
onBeforeRouteLeave(async () => {
  try { await editor.value?.flush(); return true }
  catch (error) { errorText.value = String(error); return false }
})

onMounted(() => {
  void initializeLibraryPage()
})
</script>

<template>
  <main class="sheets-workspace library-reader-theme-dialog" :class="`is-reader-theme-${theme}`" :aria-busy="switching">
    <WorkspaceMenu :items="menus" :disabled="switching" @select="selectMenu" />
    <DockWorkspace :dock="dock">
      <template #files>
        <div class="explorer-tools">
          <input v-model="searchText" type="search" placeholder="搜索工作簿" aria-label="搜索工作簿">
          <button title="新建工作簿" aria-label="新建工作簿" @click="handleCreateWorkbook">＋</button>
        </div>
        <div class="explorer-filters" aria-label="工作簿范围">
          <button v-for="option in filterOptions" :key="option.value" :aria-pressed="workbookFilter === option.value" @click="workbookFilter = option.value">{{ option.label }}</button>
        </div>
        <ResourceExplorer :nodes="tree" :selected-id="String(activeId ?? '')" label="星云表格工作簿" @open="openById(Number($event.id))" />
        <p v-if="!tree.length" class="explorer-empty">{{ loading ? '正在加载…' : '没有匹配的工作簿' }}</p>
      </template>
      <template #details>
        <section v-if="current" class="workbook-details">
          <h2>{{ current.title }}</h2>
          <dl><dt>权限</dt><dd>{{ accessRoleLabel(current.access?.role) }}</dd><dt>工作表</dt><dd>{{ current.sheet_count }} 张</dd><dt>更新于</dt><dd>{{ formatDateTime(current.updated_at) }}</dd></dl>
          <button v-if="canManageWorkbook(current)" @click="openAccessDialog(current)">设置权限</button>
          <button v-if="canManageWorkbook(current)" @click="handleRenameWorkbook(current)">重命名</button>
          <button @click="handleSaveAsWorkbook(current, 'duplicate')">另存为副本</button>
          <button v-if="canManageWorkbook(current)" class="danger" @click="handleDeleteWorkbook(current)">移入回收站</button>
        </section>
        <p v-else class="explorer-empty">打开工作簿后查看信息。</p>
      </template>
      <EditorTabs :tabs="tabs" :active="settingsActive ? 'view:settings' : String(activeId ?? 'library')" @activate="activateTab" @close="closeTab" @move="moveTab" />
      <div v-if="errorText" class="workspace-error" role="alert">{{ errorText }} <button @click="reloadWorkbooks">重试加载列表</button></div>
      <div v-if="settingsActive" class="sheets-settings"><ReaderSettingsPanel :theme="theme" appearance-label="工作区外观" theme-description="设置菜单、资源侧栏和标签页的主题。" @theme="theme = $event" /></div>
      <WorkbookEditorHost v-if="activeId != null" v-show="!settingsActive" :key="activeId" ref="editor" class="workbook-editor" :workbook-id="activeId" :sheet="sheetByWorkbook[activeId]" @open="openById" @sheet="rememberSheet" @exit="leaveWorkbook" @changed="reloadWorkbooks" @error="errorText = $event" />
      <section v-else v-show="!settingsActive" class="catalog" v-loading="loading">
        <header class="catalog-heading"><div><h1>星云表格</h1><p>从工作簿开始，整理与协作你的数据。</p></div><button class="primary" @click="handleCreateWorkbook">＋ 新建工作簿</button></header>
    <section class="workbook-table" aria-label="星云表格工作簿文件库">
      <div v-if="filteredWorkbooks.length" class="workbook-table-scroll">
        <table class="workbook-table-inner">
          <thead>
            <tr>
              <th scope="col">工作簿</th>
              <th scope="col">权限</th>
              <th scope="col">工作表</th>
              <th scope="col">更新时间</th>
              <th scope="col" class="workbook-actions-heading">操作</th>
              <th scope="col" class="workbook-spacer-cell" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="workbook in filteredWorkbooks"
              :key="workbook.id"
              class="workbook-row"
            >
              <td class="workbook-name-cell">
                <a
                  class="workbook-title-button"
                  :href="resolveWorkbookHref(workbook.id)"
                  @click.prevent="openWorkbook(workbook)"
                  rel="noopener noreferrer"
                  :title="workbook.title"
                >
                  <span class="workbook-subtitle">
                    #{{ workbook.id }}
                  </span>
                  <span class="workbook-title">{{ workbook.title }}</span>
                </a>
              </td>
              <td class="workbook-role">{{ accessRoleLabel(workbook.access?.role) }}</td>
              <td class="workbook-sheet-count">{{ workbook.sheet_count }}</td>
              <td class="workbook-updated">{{ formatDateTime(workbook.updated_at) }}</td>
              <td class="workbook-actions-cell">
                <div class="workbook-row-actions">
                  <el-dropdown trigger="click" @command="(command) => handleWorkbookCommand(command, workbook)">
                    <el-button size="small" :icon="MoreFilled" title="更多操作" />
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item v-if="canManageWorkbook(workbook)" command="rename" :icon="Edit">
                          重命名
                        </el-dropdown-item>
                        <el-dropdown-item v-if="canManageWorkbook(workbook)" command="access" :icon="Share">
                          设置权限
                        </el-dropdown-item>
                        <el-dropdown-item command="template">
                          另存为模版
                        </el-dropdown-item>
                        <el-dropdown-item command="duplicate">
                          另存为副本
                        </el-dropdown-item>
                        <el-dropdown-item v-if="canManageWorkbook(workbook)" divided command="delete" :icon="Delete">
                          删除工作簿
                        </el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </div>
              </td>
              <td class="workbook-spacer-cell" aria-hidden="true"></td>
            </tr>
          </tbody>
        </table>
      </div>

      <el-empty
        v-else-if="!loading"
        class="workbook-empty"
        :description="workbooks.length ? '没有匹配的工作簿' : '暂无工作簿'"
      />
    </section>

      </section>
    </DockWorkspace>
    <footer class="workspace-status"><span>{{ workbooks.length }} 个工作簿 · {{ totalSheetCount }} 个工作表</span><span>{{ current ? accessRoleLabel(current.access?.role) : '工作簿库' }}</span></footer>
    <NoteSheetAccessDialog v-model="accessDialogVisible" resource-type="workbook" :resource-id="accessDialogWorkbook?.id ?? null" :title="accessDialogWorkbook?.title ?? ''" @saved="reloadWorkbooks" />
  </main>
</template>
<style scoped>
.sheets-workspace { height:100%; min-height:0; min-width:0; display:flex; flex-direction:column; overflow:hidden; font-size:13px; }
button, input { font:inherit; color:inherit; }
button { cursor:pointer; border:1px solid var(--reader-border); border-radius:4px; background:var(--reader-content); padding:6px 10px; }
button:hover { background:var(--reader-hover); }
button:focus-visible, a:focus-visible, input:focus-visible { outline:2px solid var(--reader-active-text); outline-offset:-2px; }
.explorer-tools { display:flex; gap:4px; padding:8px; }
.explorer-tools input { width:0; flex:1; min-width:0; padding:6px 8px; background:var(--reader-content); border:1px solid var(--reader-border); border-radius:4px; }
.explorer-filters { display:flex; gap:2px; padding:0 8px 8px; flex-wrap:wrap; }
.explorer-filters button { border:0; font-size:11px; padding:5px 7px; background:transparent; }
.explorer-filters button[aria-pressed=true] { background:var(--reader-active); color:var(--reader-active-text); }
.explorer-empty { color:var(--reader-muted); padding:8px 12px; font-size:12px; }
.catalog { flex:1; display:flex; flex-direction:column; min-height:0; overflow:hidden; }
.sheets-settings { flex:1; min-height:0; overflow:auto; background:var(--reader-content); }
.catalog-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:28px 28px 24px; }
.catalog-heading h1 { font-size:22px; font-weight:600; margin:0 0 8px; color:var(--reader-heading); }
.catalog-heading p { margin:0; color:var(--reader-muted); }
.primary { color:var(--reader-active-text); background:var(--reader-active); white-space:nowrap; }
.workbook-table { flex:1; min-height:0; display:flex; flex-direction:column; }
.workbook-table-scroll { flex:1; overflow:auto; }
.workbook-table-inner { width:100%; border-collapse:collapse; font-size:12px; }
.workbook-table-inner th, .workbook-table-inner td { text-align:left; padding:0 16px; white-space:nowrap; border-bottom:1px solid var(--reader-border); height:42px; }
.workbook-table-inner th { position:sticky; top:0; background:var(--reader-panel); color:var(--reader-muted); font-weight:500; height:32px; }
.workbook-row:hover { background:var(--reader-hover); }
.workbook-title-button { display:flex; align-items:center; gap:10px; text-decoration:none; color:var(--reader-text); max-width:420px; }
.workbook-title { overflow:hidden; text-overflow:ellipsis; }
.workbook-title-button:hover { color:var(--reader-active-text); }
.workbook-subtitle, .workbook-updated, .workbook-role { color:var(--reader-muted); }
.workbook-spacer-cell { padding:0 !important; }
.workbook-empty { flex:1; }
.workbook-editor { flex:1; min-height:0; }
.workbook-details { padding:14px; font-size:12px; }
.workbook-details h2 { margin:0 0 20px; font-size:15px; overflow-wrap:anywhere; }
.workbook-details dl { display:grid; grid-template-columns:auto 1fr; gap:12px; margin-bottom:24px; }
.workbook-details dt { color:var(--reader-muted); }
.workbook-details dd { margin:0; overflow-wrap:anywhere; }
.workbook-details button { display:block; margin:8px 0; width:100%; text-align:left; }
.danger { color:#c44848; }
.workspace-status { display:flex; justify-content:space-between; flex:none; gap:12px; padding:5px 12px; border-top:1px solid var(--reader-border); background:var(--reader-panel); color:var(--reader-muted); font-size:11px; }
.workspace-error { padding:8px 12px; color:#b44336; border-bottom:1px solid var(--reader-border); }
/* 表格保留单元格自身颜色；工作簿导航跟随统一工作区主题。 */
.workbook-editor :deep(.resource-tabs-bar) { background:var(--reader-panel); border-color:var(--reader-border); padding:0 8px; min-height:34px; align-items:center; }
.workbook-editor :deep(.resource-workbook-title) { color:var(--reader-muted); font-size:12px; padding:0; }
.workbook-editor :deep(.resource-sheet-tab) { border:0; border-radius:0; background:transparent; color:var(--reader-text); font-size:12px; font-weight:400; padding:8px 12px; }
.workbook-editor :deep(.resource-sheet-tab.active) { background:var(--reader-active); color:var(--reader-active-text); box-shadow:inset 0 -2px var(--reader-active-text); }
.workbook-editor :deep(.resource-user-slot) { display:none; }
.workbook-editor :deep(.note-sheet-workspace) { padding:0; gap:0; background:var(--reader-content); }
.workbook-editor :deep(.sheet-formula-bar) { border:0; border-bottom:1px solid var(--reader-border); border-radius:0; background:var(--reader-content); color:var(--reader-text); }
.workbook-editor :deep(.sheet-frame) { border:0; border-radius:0; }
.workbook-editor :deep(.sheet-pagination-bar) { padding:5px 12px; background:var(--reader-panel); color:var(--reader-muted); }
@media(max-width:760px) { .catalog-heading { padding:16px; flex-wrap:wrap; } .workbook-title-button { max-width:220px; } }
</style>

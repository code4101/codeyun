<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createNoteSheet,
  deleteNoteSheet,
  fetchNoteSheet,
  fetchWorkbook,
  removeSheetFromWorkbook,
  reorderWorkbookSheets,
  updateNoteSheet,
  type NoteSheetResourceAccess,
  type WorkbookDetail,
  type WorkbookRefItem,
} from '@/api/noteSheets'
import { useSortableList } from '@/utils/useSortableList'
import NoteSheetAccessDialog from '../components/NoteSheetAccessDialog.vue'
import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

const APP_TITLE = 'CodeYun'
const SHEET_TAB_CONTEXT_MENU_WIDTH = 148
const SHEET_TAB_CONTEXT_MENU_HEIGHT = 320

type SheetTabContextMenuCommand =
  | 'create'
  | 'rename'
  | 'duplicate'
  | 'configure'
  | 'access'
  | 'share'
  | 'remove'
  | 'delete'

type SheetTabContextMenuItem = {
  command: SheetTabContextMenuCommand
  label: string
  danger?: boolean
  divided?: boolean
}

type SheetWorkspaceRouteView = 'lookup' | 'sheet'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const workbook = ref<WorkbookDetail | null>(null)
const activeSheetId = ref<number | null>(null)
const sheetTabsRef = ref<HTMLElement | null>(null)
const sheetWorkspaceRef = ref<InstanceType<typeof NoteSheetWorkspace> | null>(null)
const standaloneSheetTitle = ref('')
const standaloneWorkbookTitle = ref('')
const errorText = ref('')
const sheetTabReorderSaving = ref(false)
let sheetTabClickSuppressedUntil = 0
const sheetTabContextMenu = ref({
  visible: false,
  sheetId: null as number | null,
  left: 0,
  top: 0,
})
const sheetAccessDialogVisible = ref(false)
const sheetAccessDialogSheet = ref<WorkbookDetail['sheets'][number] | null>(null)

const isWorkbookMode = computed(() => String(route.name ?? '') === 'PublicWorkbookResource')
const workbookId = computed(() => normalizePositiveInt(route.params.workbookId))
const sheetId = computed(() => normalizePositiveInt(route.params.sheetId))
const querySheetId = computed(() => normalizePositiveInt(route.query.sheet))
const routeWorkspaceView = computed(() => normalizeWorkspaceViewQuery(route.query.view ?? route.query.mode ?? route.query.sheetView))
const activeSheet = computed(() => (
  workbook.value?.sheets.find((sheet) => sheet.id === activeSheetId.value) ?? null
))
const sheetTabContextMenuSheet = computed(() => (
  workbook.value?.sheets.find((sheet) => sheet.id === sheetTabContextMenu.value.sheetId) ?? null
))
const canEditWorkbookSheets = computed(() => (
  workbook.value?.access?.capabilities.can_edit_config !== false
))
const canManageWorkbookSheets = computed(() => (
  workbook.value?.access?.capabilities.can_manage_access !== false
))
const canReorderWorkbookSheets = computed(() => (
  canEditWorkbookSheets.value && (workbook.value?.sheets.length ?? 0) > 1
))
const canEditSheetTabContextMenuSheet = computed(() => (
  sheetTabContextMenuSheet.value?.access?.capabilities.can_edit_config !== false
))
const canManageSheetTabContextMenuSheet = computed(() => (
  sheetTabContextMenuSheet.value?.access?.capabilities.can_manage_access !== false
))
const sheetTabContextMenuItems = computed<SheetTabContextMenuItem[]>(() => {
  const sheet = sheetTabContextMenuSheet.value
  const sheetCount = workbook.value?.sheets.length ?? 0
  const items: Array<SheetTabContextMenuItem & { enabled: boolean }> = [
    { command: 'create', label: '新建工作表', enabled: canEditWorkbookSheets.value },
    { command: 'rename', label: '重命名', enabled: !!sheet && canEditSheetTabContextMenuSheet.value },
    {
      command: 'duplicate',
      label: '复制工作表',
      enabled: !!sheet && canEditWorkbookSheets.value && canEditSheetTabContextMenuSheet.value,
    },
    { command: 'configure', label: '配置', enabled: !!sheet && canEditSheetTabContextMenuSheet.value },
    { command: 'access', label: '共享权限', enabled: !!sheet && canManageSheetTabContextMenuSheet.value },
    { command: 'share', label: '分享链接', enabled: !!sheet },
    {
      command: 'remove',
      label: '移出工作簿',
      divided: true,
      enabled: !!sheet && canManageWorkbookSheets.value && canManageSheetTabContextMenuSheet.value && sheetCount > 1,
    },
    {
      command: 'delete',
      label: '删除工作表',
      danger: true,
      enabled: !!sheet && canManageSheetTabContextMenuSheet.value,
    },
  ]
  return items
    .filter((item) => item.enabled)
    .map((item) => ({
      command: item.command,
      label: item.label,
      danger: item.danger,
      divided: item.divided,
    }))
})
const pageDocumentTitle = computed(() => {
  if (isWorkbookMode.value) {
    const workbookTitle = String(workbook.value?.title || '').trim()
    const sheetTitle = String(activeSheet.value?.title || '').trim()
    const segments = [sheetTitle, workbookTitle].filter(Boolean)
    return segments.length ? `${segments.join(' - ')} - ${APP_TITLE}` : APP_TITLE
  }

  const sheetTitle = standaloneSheetTitle.value.trim()
  const workbookTitle = standaloneWorkbookTitle.value.trim()
  const resourceTitle = workbookTitle && sheetTitle
    ? `${workbookTitle}/${sheetTitle}`
    : sheetTitle || workbookTitle
  return resourceTitle ? `${resourceTitle} - ${APP_TITLE}` : APP_TITLE
})

function normalizePositiveInt(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value
  const numeric = Number(raw)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function normalizeWorkspaceViewQuery(value: unknown): SheetWorkspaceRouteView | null {
  const raw = Array.isArray(value) ? value[0] : value
  const text = String(raw ?? '').trim().toLowerCase()
  if (['lookup', 'quick', 'search', '速查'].includes(text)) {
    return 'lookup'
  }
  if (['sheet', 'table', 'grid', '表格'].includes(text)) {
    return 'sheet'
  }
  return null
}

function resolveSheetId() {
  const sheets = workbook.value?.sheets ?? []
  const validIds = new Set(sheets.map((sheet) => sheet.id))
  return [querySheetId.value, activeSheetId.value, sheets[0]?.id ?? null]
    .find((id) => id != null && validIds.has(id)) ?? null
}

async function loadWorkbookResource() {
  if (!isWorkbookMode.value) {
    return
  }
  if (workbookId.value == null) {
    errorText.value = '工作簿地址无效'
    workbook.value = null
    activeSheetId.value = null
    return
  }

  loading.value = true
  errorText.value = ''
  try {
    const detail = await fetchWorkbook(workbookId.value)
    if (!detail) {
      errorText.value = '工作簿不存在或不可访问'
      workbook.value = null
      activeSheetId.value = null
      return
    }
    workbook.value = detail
    activeSheetId.value = resolveSheetId()
    if (activeSheetId.value != null && activeSheetId.value !== querySheetId.value) {
      void router.replace({
        path: `/workbook/${detail.id}`,
        query: { ...route.query, sheet: String(activeSheetId.value) },
      })
    }
  } catch (error) {
    console.warn('Failed to load public workbook resource:', error)
    errorText.value = '没有权限访问该工作簿'
    workbook.value = null
    activeSheetId.value = null
  } finally {
    loading.value = false
  }
}

function selectSheet(nextSheetId: number) {
  closeSheetTabContextMenu()
  if (!workbook.value || nextSheetId === activeSheetId.value) {
    return
  }
  activeSheetId.value = nextSheetId
  void router.push({
    path: `/workbook/${workbook.value.id}`,
    query: { ...route.query, sheet: String(nextSheetId) },
  })
}

function handleSheetTabClick(nextSheetId: number) {
  if (Date.now() < sheetTabClickSuppressedUntil) {
    return
  }
  selectSheet(nextSheetId)
}

async function handleSheetTabReorder(oldIndex: number, newIndex: number) {
  const currentWorkbook = workbook.value
  if (!currentWorkbook || !canReorderWorkbookSheets.value || sheetTabReorderSaving.value) {
    return
  }
  if (
    oldIndex < 0
    || newIndex < 0
    || oldIndex >= currentWorkbook.sheets.length
    || newIndex >= currentWorkbook.sheets.length
  ) {
    return
  }

  sheetTabClickSuppressedUntil = Date.now() + 250
  closeSheetTabContextMenu()
  const previousSheets = [...currentWorkbook.sheets]
  const nextSheets = [...currentWorkbook.sheets]
  const [movedSheet] = nextSheets.splice(oldIndex, 1)
  if (!movedSheet) {
    return
  }
  nextSheets.splice(newIndex, 0, movedSheet)
  workbook.value = { ...currentWorkbook, sheets: nextSheets }

  sheetTabReorderSaving.value = true
  try {
    const detail = await reorderWorkbookSheets(currentWorkbook.id, {
      sheet_ids: nextSheets.map((sheet) => sheet.id),
    })
    workbook.value = detail
    const validIds = new Set(detail.sheets.map((sheet) => sheet.id))
    if (activeSheetId.value != null && !validIds.has(activeSheetId.value)) {
      activeSheetId.value = resolveSheetId()
    }
  } catch (error) {
    console.warn('Failed to reorder workbook sheets:', error)
    workbook.value = { ...currentWorkbook, sheets: previousSheets }
    ElMessage.error('保存工作表顺序失败')
  } finally {
    sheetTabReorderSaving.value = false
  }
}

useSortableList({
  listRef: sheetTabsRef,
  getDeps: () => [
    workbook.value?.id ?? null,
    canReorderWorkbookSheets.value,
    ...((workbook.value?.sheets ?? []).map((sheet) => sheet.id)),
  ],
  isEnabled: () => canReorderWorkbookSheets.value,
  handle: '.resource-sheet-tab',
  ghostClass: 'resource-sheet-tab-sortable-ghost',
  onReorder: handleSheetTabReorder,
})

async function refreshWorkbookAfterSheetMutation(preferredSheetId?: number | null) {
  if (!workbookId.value) {
    return
  }
  const detail = await fetchWorkbook(workbookId.value)
  if (!detail) {
    workbook.value = null
    activeSheetId.value = null
    errorText.value = '工作簿不存在或不可访问'
    return
  }

  workbook.value = detail
  const validIds = new Set(detail.sheets.map((sheet) => sheet.id))
  const nextSheetId = [
    preferredSheetId ?? null,
    activeSheetId.value,
    detail.sheets[0]?.id ?? null,
  ].find((id) => id != null && validIds.has(id)) ?? null
  activeSheetId.value = nextSheetId
  const nextQuery = { ...route.query }
  if (nextSheetId != null) {
    nextQuery.sheet = String(nextSheetId)
  } else {
    delete nextQuery.sheet
  }
  void router.replace({
    path: `/workbook/${detail.id}`,
    query: nextQuery,
  })
}

function closeSheetTabContextMenu() {
  sheetTabContextMenu.value.visible = false
}

function positionSheetTabContextMenu(event: MouseEvent) {
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight
  sheetTabContextMenu.value.left = Math.max(8, Math.min(event.clientX, viewportWidth - SHEET_TAB_CONTEXT_MENU_WIDTH - 8))
  sheetTabContextMenu.value.top = Math.max(8, Math.min(event.clientY, viewportHeight - SHEET_TAB_CONTEXT_MENU_HEIGHT - 8))
}

function openSheetTabContextMenu(event: MouseEvent, sheet: WorkbookDetail['sheets'][number]) {
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()

  if (sheet.id !== activeSheetId.value) {
    selectSheet(sheet.id)
  }

  positionSheetTabContextMenu(event)
  sheetTabContextMenu.value.sheetId = sheet.id
  sheetTabContextMenu.value.visible = true
}

async function waitForSheetWorkspaceRef() {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await nextTick()
    if (sheetWorkspaceRef.value) {
      return sheetWorkspaceRef.value
    }
    await new Promise((resolve) => window.requestAnimationFrame(resolve))
  }
  return sheetWorkspaceRef.value
}

async function configureSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  closeSheetTabContextMenu()
  if (!sheet || !canEditSheetTabContextMenuSheet.value) {
    return
  }
  if (sheet.id !== activeSheetId.value) {
    selectSheet(sheet.id)
  }
  const workspace = await waitForSheetWorkspaceRef()
  workspace?.openSheetSettings?.()
}

function openSheetShareLinkFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  closeSheetTabContextMenu()
  if (!sheet) {
    return
  }
  void router.push(`/sheet/${sheet.id}`)
}

function openSheetAccessFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  closeSheetTabContextMenu()
  if (!sheet || !canManageSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限管理该工作表')
    return
  }
  sheetAccessDialogSheet.value = sheet
  sheetAccessDialogVisible.value = true
}

function handleSheetAccessSaved(access: NoteSheetResourceAccess) {
  const currentWorkbook = workbook.value
  const sheet = sheetAccessDialogSheet.value
  if (!currentWorkbook || !sheet) {
    return
  }
  workbook.value = {
    ...currentWorkbook,
    sheets: currentWorkbook.sheets.map((item) => (
      item.id === sheet.id
        ? { ...item, access }
        : item
    )),
  }
}

function handleSheetTabContextMenuCommand(command: SheetTabContextMenuCommand) {
  switch (command) {
    case 'create':
      void createSheetFromTabContextMenu()
      break
    case 'rename':
      void renameSheetFromTabContextMenu()
      break
    case 'duplicate':
      void duplicateSheetFromTabContextMenu()
      break
    case 'configure':
      void configureSheetFromTabContextMenu()
      break
    case 'access':
      openSheetAccessFromTabContextMenu()
      break
    case 'share':
      openSheetShareLinkFromTabContextMenu()
      break
    case 'remove':
      void removeSheetFromTabContextMenu()
      break
    case 'delete':
      void deleteSheetFromTabContextMenu()
      break
  }
}

async function createSheetFromTabContextMenu() {
  closeSheetTabContextMenu()
  if (!workbook.value || !canEditWorkbookSheets.value) {
    ElMessage.warning('没有权限新建工作表')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入工作表名称', '新建工作表', {
      inputValue: '未命名工作表',
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作表名称不能为空',
    })
    const sheet = await createNoteSheet({
      title: value.trim(),
      workbook_id: workbook.value.id,
    })
    await refreshWorkbookAfterSheetMutation(sheet.id)
  } catch {
    return
  }
}

async function renameSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  closeSheetTabContextMenu()
  if (!sheet || !canEditSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限重命名该工作表')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入工作表名称', '重命名工作表', {
      inputValue: sheet.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作表名称不能为空',
    })
    const nextTitle = value.trim()
    if (nextTitle === sheet.title) {
      return
    }
    await updateNoteSheet(sheet.id, { title: nextTitle }, { workbookId: workbook.value?.id ?? null })
    await refreshWorkbookAfterSheetMutation(sheet.id)
  } catch {
    return
  }
}

async function duplicateSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  const currentWorkbook = workbook.value
  closeSheetTabContextMenu()
  if (!sheet || !currentWorkbook || !canEditWorkbookSheets.value || !canEditSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限复制该工作表')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入新工作表名称', '复制工作表', {
      inputValue: `${sheet.title} 副本`,
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作表名称不能为空',
    })
    const detail = await fetchNoteSheet(sheet.id, { workbookId: currentWorkbook.id })
    if (!detail) {
      ElMessage.error('工作表不存在或不可访问')
      return
    }
    const nextSheet = await createNoteSheet({
      title: value.trim(),
      workbook_id: currentWorkbook.id,
      document_json: detail.document_json,
    })
    await refreshWorkbookAfterSheetMutation(nextSheet.id)
  } catch {
    return
  }
}

async function removeSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  const currentWorkbook = workbook.value
  closeSheetTabContextMenu()
  if (!sheet || !currentWorkbook || !canManageWorkbookSheets.value || !canManageSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限移出该工作表')
    return
  }
  if (currentWorkbook.sheets.length <= 1) {
    ElMessage.warning('工作簿至少保留一个工作表')
    return
  }

  try {
    await ElMessageBox.confirm(
      `将“${sheet.title}”从当前工作簿中移出，工作表本身不会删除。`,
      '移出工作簿',
      {
        confirmButtonText: '确认移出',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await removeSheetFromWorkbook(currentWorkbook.id, sheet.id)
    await refreshWorkbookAfterSheetMutation(activeSheetId.value === sheet.id ? null : activeSheetId.value)
  } catch {
    return
  }
}

async function deleteSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  const currentWorkbook = workbook.value
  closeSheetTabContextMenu()
  if (!sheet || !currentWorkbook || !canManageSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限删除该工作表')
    return
  }

  try {
    await ElMessageBox.confirm(
      `删除工作表“${sheet.title}”后，会从所有工作簿中移除并删除其共享权限。此操作不可恢复。`,
      '删除工作表',
      {
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteNoteSheet(sheet.id)
    await refreshWorkbookAfterSheetMutation(activeSheetId.value === sheet.id ? null : activeSheetId.value)
  } catch {
    return
  }
}

function handleGlobalMouseDown(event: MouseEvent) {
  if (!sheetTabContextMenu.value.visible) {
    return
  }
  const target = event.target
  if (target instanceof HTMLElement && target.closest('.sheet-tab-context-menu')) {
    return
  }
  closeSheetTabContextMenu()
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeSheetTabContextMenu()
  }
}

function handleSheetMissing() {
  errorText.value = '工作表不存在或不可访问'
}

function handleSheetSync(payload: {
  id: number
  title: string
  version: number
  updatedAt: number
  workbookItems?: WorkbookRefItem[]
}) {
  if (!isWorkbookMode.value) {
    standaloneSheetTitle.value = payload.title || ''
    standaloneWorkbookTitle.value = payload.workbookItems?.[0]?.title || ''
    return
  }
  if (!workbook.value) {
    return
  }
  const sheet = workbook.value.sheets.find((item) => item.id === payload.id)
  if (sheet) {
    sheet.title = payload.title
    sheet.updated_at = payload.updatedAt
  }
}

watch(
  pageDocumentTitle,
  (title) => {
    document.title = title
  },
  { immediate: true },
)

watch(
  sheetId,
  (nextSheetId, previousSheetId) => {
    if (nextSheetId !== previousSheetId && !isWorkbookMode.value) {
      standaloneSheetTitle.value = ''
      standaloneWorkbookTitle.value = ''
    }
  },
)

watch(
  () => route.fullPath,
  () => {
    document.title = pageDocumentTitle.value
  },
)

watch(
  [workbookId, querySheetId, isWorkbookMode],
  () => {
    void loadWorkbookResource()
  },
)

onMounted(() => {
  document.addEventListener('mousedown', handleGlobalMouseDown)
  document.addEventListener('keydown', handleGlobalKeydown)
  if (isWorkbookMode.value) {
    void loadWorkbookResource()
  } else if (sheetId.value == null) {
    errorText.value = '工作表地址无效'
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleGlobalMouseDown)
  document.removeEventListener('keydown', handleGlobalKeydown)
})
</script>

<template>
  <div class="sheet-resource-page" v-loading="loading">
    <template v-if="isWorkbookMode">
      <div v-if="workbook" class="resource-tabs-bar">
        <div class="resource-workbook-title" :title="workbook.title">{{ workbook.title }}</div>
        <div
          ref="sheetTabsRef"
          class="resource-sheet-tabs"
          :class="{ 'is-sortable': canReorderWorkbookSheets }"
        >
          <button
            v-for="sheet in workbook.sheets"
            :key="sheet.id"
            type="button"
            class="resource-sheet-tab"
            :class="{ active: sheet.id === activeSheetId }"
            :title="canReorderWorkbookSheets ? `${sheet.title}（拖拽调整顺序）` : sheet.title"
            @click="handleSheetTabClick(sheet.id)"
            @contextmenu.capture="(event) => openSheetTabContextMenu(event, sheet)"
          >
            {{ sheet.title }}
          </button>
        </div>
      </div>
      <div
        v-if="sheetTabContextMenu.visible"
        class="sheet-tab-context-menu"
        :style="{ left: `${sheetTabContextMenu.left}px`, top: `${sheetTabContextMenu.top}px` }"
        @contextmenu.prevent.stop
        @mousedown.stop
      >
        <template v-for="item in sheetTabContextMenuItems" :key="item.command">
          <div v-if="item.divided" class="sheet-tab-context-menu-separator"></div>
          <button
            type="button"
            class="sheet-tab-context-menu-item"
            :class="{ danger: item.danger }"
            @click="handleSheetTabContextMenuCommand(item.command)"
          >
            {{ item.label }}
          </button>
        </template>
      </div>

      <NoteSheetAccessDialog
        v-model="sheetAccessDialogVisible"
        resource-type="sheet"
        :resource-id="sheetAccessDialogSheet?.id ?? null"
        :title="sheetAccessDialogSheet?.title ?? ''"
        @saved="handleSheetAccessSaved"
      />

      <NoteSheetWorkspace
        v-if="activeSheetId"
        ref="sheetWorkspaceRef"
        class="resource-sheet-workspace"
        :key="`${workbookId}:${activeSheetId}`"
        :workbook-id="workbookId"
        :sheet-id="activeSheetId"
        :initial-workspace-view="routeWorkspaceView"
        default-height-mode="fill"
        :access-capabilities="activeSheet?.access?.capabilities ?? null"
        :show-title-input="false"
        empty-text="请选择工作表"
        @missing="handleSheetMissing"
        @sheet-sync="handleSheetSync"
      />
      <el-empty v-else-if="workbook" :description="errorText || '没有可访问的工作表'" />
    </template>

    <template v-else>
      <NoteSheetWorkspace
        v-if="sheetId"
        class="resource-sheet-workspace"
        :key="`sheet:${sheetId}`"
        :sheet-id="sheetId"
        :initial-workspace-view="routeWorkspaceView"
        default-height-mode="fill"
        :show-title-input="false"
        empty-text="工作表不存在或不可访问"
        @missing="handleSheetMissing"
        @sheet-sync="handleSheetSync"
      />
      <el-empty v-else :description="errorText || '工作表地址无效'" />
    </template>

    <el-empty v-if="errorText && !loading && isWorkbookMode && !workbook" :description="errorText" />
  </div>
</template>

<style scoped>
.sheet-resource-page {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #fff;
  overflow: hidden;
  overscroll-behavior: none;
}

.resource-tabs-bar {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 0;
  min-height: 46px;
  padding: 8px 16px 0;
  border-bottom: 1px solid #efe4d3;
  overflow-x: auto;
}

.resource-workbook-title {
  flex: 0 0 auto;
  max-width: 320px;
  margin-right: 12px;
  padding: 0 2px 10px;
  color: #4b5563;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.resource-sheet-tabs {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 0;
}

.resource-sheet-tab {
  flex: 0 0 auto;
  border: 1px solid #ebe2d4;
  border-bottom: 0;
  border-radius: 8px 8px 0 0;
  background: #faf7f1;
  color: #6b5a44;
  padding: 7px 16px 8px;
  font-size: 14px;
  font-weight: 600;
  line-height: 20px;
  cursor: pointer;
}

.resource-sheet-tabs.is-sortable .resource-sheet-tab {
  cursor: grab;
}

.resource-sheet-tabs.is-sortable .resource-sheet-tab:active {
  cursor: grabbing;
}

.resource-sheet-tab:hover {
  background: #f1e7d9;
  color: #5c4932;
}

.resource-sheet-tab.active {
  background: #fff;
  color: #2f2414;
  box-shadow: inset 0 3px 0 #5b8def;
}

.resource-sheet-tab-sortable-ghost {
  opacity: 0.64;
  background: #eff6ff;
}

.sheet-tab-context-menu {
  position: fixed;
  z-index: 3000;
  box-sizing: border-box;
  min-width: 120px;
  padding: 4px 0;
  border: 1px solid #d8dce5;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 8px 20px rgb(15 23 42 / 16%);
}

.sheet-tab-context-menu-item {
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 7px 18px;
  color: #1f2937;
  font-size: 14px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
}

.sheet-tab-context-menu-item:hover:not(:disabled) {
  background: #f5f7fa;
}

.sheet-tab-context-menu-item.danger {
  color: #c2410c;
}

.sheet-tab-context-menu-item:disabled {
  color: #b4b8c0;
  cursor: not-allowed;
}

.sheet-tab-context-menu-separator {
  height: 1px;
  margin: 4px 0;
  background: #edf0f5;
}

.resource-sheet-workspace {
  flex: 1 1 auto;
  min-height: 0;
}
</style>

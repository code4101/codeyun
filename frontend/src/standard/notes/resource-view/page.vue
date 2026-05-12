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
  updateNoteSheet,
  type WorkbookDetail,
  type WorkbookRefItem,
} from '@/api/noteSheets'
import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

const APP_TITLE = 'CodeYun'
const SHEET_TAB_CONTEXT_MENU_WIDTH = 148
const SHEET_TAB_CONTEXT_MENU_HEIGHT = 246

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const workbook = ref<WorkbookDetail | null>(null)
const activeSheetId = ref<number | null>(null)
const sheetWorkspaceRef = ref<InstanceType<typeof NoteSheetWorkspace> | null>(null)
const standaloneSheetTitle = ref('')
const standaloneWorkbookTitle = ref('')
const errorText = ref('')
const sheetTabContextMenu = ref({
  visible: false,
  sheetId: null as number | null,
  left: 0,
  top: 0,
})

const isWorkbookMode = computed(() => String(route.name ?? '') === 'PublicWorkbookResource')
const workbookId = computed(() => normalizePositiveInt(route.params.workbookId))
const sheetId = computed(() => normalizePositiveInt(route.params.sheetId))
const querySheetId = computed(() => normalizePositiveInt(route.query.sheet))
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
const canEditSheetTabContextMenuSheet = computed(() => (
  sheetTabContextMenuSheet.value?.access?.capabilities.can_edit_config !== false
))
const canManageSheetTabContextMenuSheet = computed(() => (
  sheetTabContextMenuSheet.value?.access?.capabilities.can_manage_access !== false
))
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
        <button
          v-for="sheet in workbook.sheets"
          :key="sheet.id"
          type="button"
          class="resource-sheet-tab"
          :class="{ active: sheet.id === activeSheetId }"
          @click="selectSheet(sheet.id)"
          @contextmenu.capture="(event) => openSheetTabContextMenu(event, sheet)"
        >
          {{ sheet.title }}
        </button>
      </div>
      <div
        v-if="sheetTabContextMenu.visible"
        class="sheet-tab-context-menu"
        :style="{ left: `${sheetTabContextMenu.left}px`, top: `${sheetTabContextMenu.top}px` }"
        @contextmenu.prevent.stop
        @mousedown.stop
      >
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          :disabled="!canEditWorkbookSheets"
          @click="createSheetFromTabContextMenu"
        >
          新建工作表
        </button>
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          :disabled="!canEditSheetTabContextMenuSheet"
          @click="renameSheetFromTabContextMenu"
        >
          重命名
        </button>
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          :disabled="!canEditWorkbookSheets || !canEditSheetTabContextMenuSheet"
          @click="duplicateSheetFromTabContextMenu"
        >
          复制工作表
        </button>
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          :disabled="!canEditSheetTabContextMenuSheet"
          @click="configureSheetFromTabContextMenu"
        >
          配置
        </button>
        <div class="sheet-tab-context-menu-separator"></div>
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          :disabled="!canManageWorkbookSheets || !canManageSheetTabContextMenuSheet || (workbook?.sheets.length ?? 0) <= 1"
          @click="removeSheetFromTabContextMenu"
        >
          移出工作簿
        </button>
        <button
          type="button"
          class="sheet-tab-context-menu-item danger"
          :disabled="!canManageSheetTabContextMenuSheet"
          @click="deleteSheetFromTabContextMenu"
        >
          删除工作表
        </button>
      </div>

      <NoteSheetWorkspace
        v-if="activeSheetId"
        ref="sheetWorkspaceRef"
        class="resource-sheet-workspace"
        :key="`${workbookId}:${activeSheetId}`"
        :workbook-id="workbookId"
        :sheet-id="activeSheetId"
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

.resource-sheet-tab:hover {
  background: #f1e7d9;
  color: #5c4932;
}

.resource-sheet-tab.active {
  background: #fff;
  color: #2f2414;
  box-shadow: inset 0 3px 0 #5b8def;
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

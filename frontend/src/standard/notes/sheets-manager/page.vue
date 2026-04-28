<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import {
  createNoteSheet,
  createWorkbook,
  deleteNoteSheet,
  deleteWorkbook,
  fetchNoteSheets,
  fetchWorkbook,
  fetchWorkbooks,
  removeSheetFromWorkbook,
  saveAsWorkbook,
  updateNoteSheet,
  type NoteSheetSummary,
  type WorkbookDetail,
  type WorkbookSummary,
} from '@/api/noteSheets'
import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

type SheetSyncPayload = {
  id: number
  title: string
  version: number
  updatedAt: number
}

type WorkbookContextMenuState = {
  kind: 'workbook'
  row: WorkbookSummary
}

type SheetContextMenuState = {
  kind: 'sheet'
  row: NoteSheetSummary
}

type WorkbookPanelContextMenuState = {
  kind: 'workbook-panel'
}

type ListContextMenuState =
  | WorkbookContextMenuState
  | SheetContextMenuState
  | WorkbookPanelContextMenuState

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const initialized = ref(false)
const workbooks = ref<WorkbookSummary[]>([])
const sheets = ref<NoteSheetSummary[]>([])
const selectedWorkbookId = ref<number | null>(null)
const selectedWorkbookDetail = ref<WorkbookDetail | null>(null)
const activeSheetId = ref<number | null>(null)
const editingSheetId = ref<number | null>(null)
const editingSheetTitle = ref('')
const editingSheetInputRef = ref<HTMLInputElement | null>(null)
const savingSheetRenameId = ref<number | null>(null)
const workspaceRenderKey = ref(0)
const workspaceRef = ref<{ openSheetSettings: () => void } | null>(null)
const pendingSheetSettingsId = ref<number | null>(null)
const listContextMenuRef = ref<HTMLElement | null>(null)
const listContextMenu = ref<{
  visible: boolean
  x: number
  y: number
  payload: ListContextMenuState | null
}>({
  visible: false,
  x: 0,
  y: 0,
  payload: null,
})
let textMeasureCanvas: HTMLCanvasElement | null = null

const queryWorkbookId = computed(() => normalizePositiveInt(route.query.workbook))
const querySheetId = computed(() => normalizePositiveInt(route.query.sheet))
const selectedWorkbookSummary = computed(() => (
  workbooks.value.find((item) => item.id === selectedWorkbookId.value) ?? null
))
const selectedWorkbookLabel = computed(() => selectedWorkbookSummary.value?.title || '工作簿')
const workbookSwitcherWidth = computed(() => {
  const textWidth = measureTextWidth(selectedWorkbookLabel.value, "500 16px 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif")
  return Math.min(Math.max(Math.ceil(textWidth) + 46, 152), 360)
})
const editingSheetInputWidth = computed(() => {
  const text = editingSheetTitle.value || ' '
  const textWidth = measureTextWidth(text, "600 13px 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif")
  return `${Math.min(Math.max(Math.ceil(textWidth) + 12, 20), 420)}px`
})

const visibleSheets = computed(() => (
  selectedWorkbookDetail.value?.sheets ?? []
))

const listContextMenuStyle = computed(() => ({
  left: `${listContextMenu.value.x}px`,
  top: `${listContextMenu.value.y}px`,
}))

function normalizePositiveInt(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function measureTextWidth(text: string, font: string) {
  if (typeof document === 'undefined') {
    return text.length * 16
  }

  textMeasureCanvas ??= document.createElement('canvas')
  const context = textMeasureCanvas.getContext('2d')
  if (!context) {
    return text.length * 16
  }

  context.font = font
  return context.measureText(text).width
}

function buildManagerQuery(workbookId: number | null, sheetId: number | null) {
  const nextQuery = { ...route.query }

  if (workbookId == null) {
    delete nextQuery.workbook
  } else {
    nextQuery.workbook = String(workbookId)
  }

  if (sheetId == null) {
    delete nextQuery.sheet
  } else {
    nextQuery.sheet = String(sheetId)
  }

  return nextQuery
}

function updateManagerRoute(workbookId: number | null, sheetId: number | null, replace = false) {
  const nextQuery = buildManagerQuery(workbookId, sheetId)
  const currentWorkbook = String(route.query.workbook ?? '')
  const currentSheet = String(route.query.sheet ?? '')
  const nextWorkbook = String(nextQuery.workbook ?? '')
  const nextSheet = String(nextQuery.sheet ?? '')

  if (currentWorkbook === nextWorkbook && currentSheet === nextSheet) {
    return
  }

  void router[replace ? 'replace' : 'push']({
    path: '/notes/sheets',
    query: nextQuery,
  })
}

function closeListContextMenu() {
  listContextMenu.value.visible = false
  listContextMenu.value.payload = null
}

function bindContextMenuPosition(clientX: number, clientY: number) {
  listContextMenu.value.x = clientX
  listContextMenu.value.y = clientY

  void nextTick(() => {
    const menuEl = listContextMenuRef.value
    if (!menuEl) {
      return
    }
    const margin = 12
    const rect = menuEl.getBoundingClientRect()
    const maxX = Math.max(margin, window.innerWidth - rect.width - margin)
    const maxY = Math.max(margin, window.innerHeight - rect.height - margin)
    listContextMenu.value.x = Math.min(clientX, maxX)
    listContextMenu.value.y = Math.min(clientY, maxY)
  })
}

function openListContextMenu(payload: ListContextMenuState, event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  listContextMenu.value.visible = true
  listContextMenu.value.payload = payload
  bindContextMenuPosition(event.clientX, event.clientY)
}

function resolveSheetId(candidates: Array<number | null | undefined>, availableItems: NoteSheetSummary[]) {
  const validIds = new Set(availableItems.map((item) => item.id))
  return candidates.find((id) => id != null && validIds.has(id)) ?? null
}

async function loadWorkbooks() {
  workbooks.value = await fetchWorkbooks()
}

async function loadSheets() {
  sheets.value = await fetchNoteSheets()
}

async function loadSelectedWorkbookDetail() {
  if (!selectedWorkbookId.value) {
    selectedWorkbookDetail.value = null
    return
  }

  selectedWorkbookDetail.value = await fetchWorkbook(selectedWorkbookId.value)
  if (!selectedWorkbookDetail.value) {
    selectedWorkbookId.value = null
  }
}

async function syncSelectionFromRoute(replaceInvalid = false) {
  const workbookIds = new Set(workbooks.value.map((item) => item.id))
  const sheetFromQuery = querySheetId.value == null
    ? null
    : sheets.value.find((item) => item.id === querySheetId.value) ?? null
  const workbookFromSheetQuery = sheetFromQuery?.workbook_items.find((item) => workbookIds.has(item.id))?.id ?? null
  const nextWorkbookId = queryWorkbookId.value != null && workbookIds.has(queryWorkbookId.value)
    ? queryWorkbookId.value
    : workbookFromSheetQuery
      ?? workbooks.value[0]?.id
      ?? null
  const workbookChanged = nextWorkbookId !== selectedWorkbookId.value

  selectedWorkbookId.value = nextWorkbookId

  if (nextWorkbookId == null) {
    selectedWorkbookDetail.value = null
  } else if (workbookChanged || selectedWorkbookDetail.value?.id !== nextWorkbookId) {
    await loadSelectedWorkbookDetail()
  }

  const availableItems = visibleSheets.value
  const nextSheetId = resolveSheetId(
    [querySheetId.value, activeSheetId.value, availableItems[0]?.id ?? null],
    availableItems,
  )
  activeSheetId.value = nextSheetId

  if (editingSheetId.value != null && !availableItems.some((item) => item.id === editingSheetId.value)) {
    cancelInlineRenameSheet()
  }

  if (!replaceInvalid) {
    return
  }

  const shouldNormalizeQuery = nextWorkbookId !== (queryWorkbookId.value ?? null)
    || nextSheetId !== (querySheetId.value ?? null)
  if (shouldNormalizeQuery) {
    updateManagerRoute(nextWorkbookId, nextSheetId, true)
  }
}

async function reloadAll() {
  loading.value = true
  try {
    await Promise.all([
      loadWorkbooks(),
      loadSheets(),
    ])
    await syncSelectionFromRoute(true)
  } finally {
    loading.value = false
  }
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
    await Promise.all([loadWorkbooks(), loadSheets()])
    updateManagerRoute(workbook.id, null)
  } catch {
    return
  }
}

async function handleCreateSheet(targetWorkbookId = selectedWorkbookId.value) {
  try {
    const { value } = await ElMessageBox.prompt('请输入表格名称', '新建表格', {
      inputValue: '',
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '表格名称不能为空',
    })
    const sheet = await createNoteSheet({
      title: value.trim(),
      workbook_id: targetWorkbookId,
    })
    await reloadAll()
    updateManagerRoute(targetWorkbookId, sheet.id)
  } catch {
    return
  }
}

function handleWorkbookSwitchChange(value: number | string) {
  const workbookId = Number(value)
  if (!Number.isInteger(workbookId) || workbookId <= 0) {
    return
  }
  updateManagerRoute(workbookId, null)
}

function handleWorkbookSwitchContextMenu(event: MouseEvent) {
  const currentWorkbook = selectedWorkbookSummary.value
  if (currentWorkbook) {
    openListContextMenu({ kind: 'workbook', row: currentWorkbook }, event)
    return
  }
  openListContextMenu({ kind: 'workbook-panel' }, event)
}

function handleSheetRowClick(sheet: NoteSheetSummary) {
  const nextWorkbookId = selectedWorkbookId.value ?? sheet.workbook_items[0]?.id ?? null
  updateManagerRoute(nextWorkbookId, sheet.id)
}

function handleSheetTabContextMenu(sheet: NoteSheetSummary, event: MouseEvent) {
  openListContextMenu({ kind: 'sheet', row: sheet }, event)
}

function focusEditingSheetInput() {
  void nextTick(() => {
    editingSheetInputRef.value?.focus()
    editingSheetInputRef.value?.select()
  })
}

function cancelInlineRenameSheet() {
  editingSheetId.value = null
  editingSheetTitle.value = ''
}

function startInlineRenameSheet(sheet: NoteSheetSummary) {
  closeListContextMenu()
  editingSheetId.value = sheet.id
  editingSheetTitle.value = sheet.title
  focusEditingSheetInput()
}

async function handleDeleteWorkbook(workbook: WorkbookSummary) {
  closeListContextMenu()
  try {
    await ElMessageBox.confirm(
      `删除工作簿“${workbook.title}”后，只会解除打包关系，不会删除里面的表格。`,
      '删除工作簿',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteWorkbook(workbook.id)
    await reloadAll()
  } catch {
    return
  }
}

async function handleSaveAsWorkbook(workbook: WorkbookSummary, mode: 'template' | 'duplicate') {
  closeListContextMenu()
  const modeLabel = mode === 'template' ? '模版' : '副本'
  const defaultTitle = `${workbook.title} ${modeLabel}`

  try {
    const { value } = await ElMessageBox.prompt('请输入新工作簿名称', `另存为${modeLabel}`, {
      inputValue: defaultTitle,
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    const nextWorkbook = await saveAsWorkbook(workbook.id, {
      mode,
      title: value.trim(),
    })
    await reloadAll()
    updateManagerRoute(nextWorkbook.id, nextWorkbook.sheets[0]?.id ?? null)
  } catch {
    return
  }
}

async function handleDeleteSheet(sheet: NoteSheetSummary) {
  closeListContextMenu()
  try {
    await ElMessageBox.confirm(
      `删除表格“${sheet.title}”后不可恢复，并会从所有工作簿中移除。`,
      '删除表格',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteNoteSheet(sheet.id)
    await reloadAll()
  } catch {
    return
  }
}

async function commitInlineRenameSheet(sheet: NoteSheetSummary) {
  if (editingSheetId.value !== sheet.id || savingSheetRenameId.value === sheet.id) {
    return
  }

  const nextTitle = editingSheetTitle.value.trim()
  if (!nextTitle || nextTitle === sheet.title) {
    cancelInlineRenameSheet()
    return
  }

  try {
    savingSheetRenameId.value = sheet.id
    const saved = await updateNoteSheet(sheet.id, {
      title: nextTitle,
    })
    handleSheetSync({
      id: saved.id,
      title: saved.title,
      version: saved.version,
      updatedAt: saved.updated_at,
    })

    if (activeSheetId.value === sheet.id) {
      workspaceRenderKey.value += 1
    }
    cancelInlineRenameSheet()
  } catch {
    focusEditingSheetInput()
  } finally {
    if (savingSheetRenameId.value === sheet.id) {
      savingSheetRenameId.value = null
    }
  }
}

async function handleDetachSheet(sheetId: number) {
  closeListContextMenu()
  if (selectedWorkbookId.value == null) {
    return
  }

  selectedWorkbookDetail.value = await removeSheetFromWorkbook(selectedWorkbookId.value, sheetId)
  await Promise.all([loadWorkbooks(), loadSheets()])

  const nextSheetId = resolveSheetId(
    [activeSheetId.value, selectedWorkbookDetail.value.sheets[0]?.id ?? null],
    selectedWorkbookDetail.value.sheets,
  )
  activeSheetId.value = nextSheetId
  updateManagerRoute(selectedWorkbookId.value, nextSheetId, true)
}

function handleSheetMissing() {
  pendingSheetSettingsId.value = null
  void reloadAll()
}

function handleContextMenuOpenSingleSheet(sheetId: number) {
  closeListContextMenu()
  void router.push(`/notes/sheets/${sheetId}`)
}

function handleContextMenuOpenSheetSettings(sheet: NoteSheetSummary) {
  closeListContextMenu()
  if (sheet.id === activeSheetId.value) {
    void nextTick(() => {
      workspaceRef.value?.openSheetSettings()
    })
    return
  }

  pendingSheetSettingsId.value = sheet.id
  handleSheetRowClick(sheet)
}

function handleContextMenuCreateWorkbook() {
  closeListContextMenu()
  void handleCreateWorkbook()
}

function handleCreateSheetCurrent() {
  void handleCreateSheet()
}

function handleWindowInteraction() {
  closeListContextMenu()
}

function handleSheetSync(payload: SheetSyncPayload) {
  const updateEntry = (item: NoteSheetSummary) => {
    item.title = payload.title
    item.updated_at = payload.updatedAt
  }

  sheets.value.find((item) => item.id === payload.id) && updateEntry(
    sheets.value.find((item) => item.id === payload.id)!,
  )
  selectedWorkbookDetail.value?.sheets.find((item) => item.id === payload.id) && updateEntry(
    selectedWorkbookDetail.value.sheets.find((item) => item.id === payload.id)!,
  )

  if (pendingSheetSettingsId.value === payload.id) {
    pendingSheetSettingsId.value = null
    void nextTick(() => {
      workspaceRef.value?.openSheetSettings()
    })
  }
}

watch(
  [() => queryWorkbookId.value, () => querySheetId.value],
  async ([nextWorkbookId], [previousWorkbookId]) => {
    if (!initialized.value) {
      return
    }

    const shouldShowLoading = nextWorkbookId !== previousWorkbookId
    if (shouldShowLoading) {
      loading.value = true
    }
    try {
      await syncSelectionFromRoute(true)
    } finally {
      if (shouldShowLoading) {
        loading.value = false
      }
    }
  },
)

onMounted(async () => {
  window.addEventListener('mousedown', handleWindowInteraction)
  window.addEventListener('resize', handleWindowInteraction)
  window.addEventListener('scroll', handleWindowInteraction, true)
  await reloadAll()
  initialized.value = true
})

onBeforeUnmount(() => {
  window.removeEventListener('mousedown', handleWindowInteraction)
  window.removeEventListener('resize', handleWindowInteraction)
  window.removeEventListener('scroll', handleWindowInteraction, true)
})
</script>

<template>
  <div class="sheet-manager-page">
    <div class="manager-grid" v-loading="loading">
      <section class="content-panel">
        <div class="sheet-tabs-bar">
          <div class="sheet-flow">
            <div
              class="workbook-switcher-shell"
              :style="{ width: `${workbookSwitcherWidth}px` }"
              @contextmenu.prevent.stop="handleWorkbookSwitchContextMenu"
            >
              <el-select
                :model-value="selectedWorkbookId ?? undefined"
                class="workbook-switcher"
                @change="handleWorkbookSwitchChange"
              >
                <el-option
                  v-for="workbook in workbooks"
                  :key="workbook.id"
                  :value="workbook.id"
                  :label="workbook.title"
                />
              </el-select>
            </div>

            <div
              v-for="sheet in visibleSheets"
              :key="sheet.id"
              class="sheet-tab"
              :class="{ active: sheet.id === activeSheetId, editing: sheet.id === editingSheetId }"
              @click="sheet.id !== editingSheetId && handleSheetRowClick(sheet)"
              @dblclick.stop="startInlineRenameSheet(sheet)"
              @contextmenu="handleSheetTabContextMenu(sheet, $event)"
            >
              <input
                v-if="sheet.id === editingSheetId"
                ref="editingSheetInputRef"
                v-model="editingSheetTitle"
                class="sheet-tab-input"
                :style="{ width: editingSheetInputWidth }"
                maxlength="120"
                :disabled="sheet.id === savingSheetRenameId"
                @click.stop
                @mousedown.stop
                @dblclick.stop
                @keydown.enter.prevent="void commitInlineRenameSheet(sheet)"
                @keydown.esc.prevent="cancelInlineRenameSheet"
                @blur="void commitInlineRenameSheet(sheet)"
              >
              <template v-else>
                {{ sheet.title }}
              </template>
            </div>
            <button
              type="button"
              class="sheet-create-button"
              :disabled="!selectedWorkbookId"
              @click="handleCreateSheetCurrent"
            >
              +
            </button>
          </div>
        </div>

        <div class="content-panel-body">
          <NoteSheetWorkspace
            ref="workspaceRef"
            :key="`${activeSheetId ?? 'empty'}-${workspaceRenderKey}`"
            :sheet-id="activeSheetId"
            :show-title-input="false"
            empty-text="请选择工作表"
            @missing="handleSheetMissing"
            @sheet-sync="handleSheetSync"
          />
        </div>
      </section>
    </div>

    <Teleport to="body">
      <div
        v-if="listContextMenu.visible && listContextMenu.payload"
        ref="listContextMenuRef"
        class="list-context-menu"
        :style="listContextMenuStyle"
        @mousedown.stop
        @contextmenu.prevent
      >
        <template v-if="listContextMenu.payload.kind === 'workbook-panel'">
          <button
            type="button"
            class="list-context-menu-item"
            @click="handleContextMenuCreateWorkbook"
          >
            新建工作簿
          </button>
        </template>

        <template v-else-if="listContextMenu.payload.kind === 'workbook'">
          <button
            type="button"
            class="list-context-menu-item"
            @click="handleContextMenuCreateWorkbook"
          >
            新建工作簿
          </button>
          <div class="list-context-submenu-shell">
            <button
              type="button"
              class="list-context-menu-item list-context-menu-item--submenu"
            >
              <span>另存为</span>
              <span class="list-context-menu-caret">›</span>
            </button>
            <div class="list-context-submenu">
              <button
                type="button"
                class="list-context-menu-item"
                @click="handleSaveAsWorkbook(listContextMenu.payload.row, 'template')"
              >
                模版
              </button>
              <button
                type="button"
                class="list-context-menu-item"
                @click="handleSaveAsWorkbook(listContextMenu.payload.row, 'duplicate')"
              >
                副本
              </button>
            </div>
          </div>
          <button
            type="button"
            class="list-context-menu-item danger"
            @click="handleDeleteWorkbook(listContextMenu.payload.row)"
          >
            删除工作簿
          </button>
        </template>

        <template v-else-if="listContextMenu.payload.kind === 'sheet'">
          <button
            type="button"
            class="list-context-menu-item"
            @click="handleContextMenuOpenSheetSettings(listContextMenu.payload.row)"
          >
            设置
          </button>
          <button
            type="button"
            class="list-context-menu-item"
            @click="handleContextMenuOpenSingleSheet(listContextMenu.payload.row.id)"
          >
            单表打开
          </button>
          <button
            v-if="selectedWorkbookDetail"
            type="button"
            class="list-context-menu-item"
            @click="handleDetachSheet(listContextMenu.payload.row.id)"
          >
            移出当前工作簿
          </button>
          <button
            type="button"
            class="list-context-menu-item danger"
            @click="handleDeleteSheet(listContextMenu.payload.row)"
          >
            删除表格
          </button>
        </template>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.sheet-manager-page {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 12px 16px 16px;
  overflow: hidden;
  gap: 12px;
}

.manager-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}

.content-panel {
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.content-panel-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.sheet-tabs-bar {
  padding: 8px 12px 0;
  border-bottom: 1px solid #efe4d3;
}

.sheet-flow {
  display: flex;
  align-items: flex-end;
  align-content: flex-end;
  flex-wrap: wrap;
  gap: 8px 0;
  margin-bottom: -1px;
}

.workbook-switcher-shell {
  flex: 0 0 auto;
  max-width: 100%;
}

.workbook-switcher {
  width: 100%;
}

.sheet-tab {
  flex: 0 0 auto;
  border: 1px solid #ebe2d4;
  border-bottom: 0;
  border-radius: 10px 10px 0 0;
  background: #f7f1e8;
  padding: 10px 15px 11px;
  color: #7b654a;
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 120ms ease, color 120ms ease, box-shadow 120ms ease, border-color 120ms ease;
}

.sheet-tab.editing {
  background: #fff;
  color: #2f2414;
  cursor: text;
}

.sheet-tab:hover {
  background: #f1e7d9;
  color: #5c4932;
}

.sheet-tab.active {
  border-color: #e7dcc9;
  background: #fff;
  color: #2f2414;
  box-shadow: inset 0 3px 0 #5b8def;
  font-weight: 700;
}

.sheet-tab-input {
  border: 0;
  outline: none;
  background: transparent;
  padding: 0;
  margin: 0;
  color: inherit;
  font: inherit;
  line-height: 1;
}

.sheet-create-button {
  flex: 0 0 auto;
  min-width: 36px;
  height: 32px;
  border: 1px solid #d8dfeb;
  border-radius: 8px;
  background: #fff;
  padding: 0 10px;
  color: #6f7f94;
  font-size: 20px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  transition: border-color 120ms ease, color 120ms ease, background-color 120ms ease;
}

.sheet-create-button:hover {
  border-color: #7aa7f7;
  color: #3c74dd;
  background: #f7fbff;
}

.sheet-create-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.list-context-menu {
  position: fixed;
  z-index: 2200;
  min-width: 156px;
  padding: 6px;
  border: 1px solid rgba(209, 221, 232, 0.92);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.16);
  backdrop-filter: blur(10px);
}

.list-context-menu-item {
  width: 100%;
  border: 0;
  border-radius: 10px;
  background: transparent;
  padding: 9px 12px;
  text-align: left;
  color: #173042;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.list-context-menu-item--submenu {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.list-context-menu-caret {
  color: #8b7355;
  font-size: 14px;
  line-height: 1;
}

.list-context-menu-item:hover {
  background: rgba(64, 158, 255, 0.08);
}

.list-context-submenu-shell {
  position: relative;
}

.list-context-submenu {
  position: absolute;
  top: 0;
  left: calc(100% + 8px);
  display: none;
  min-width: 108px;
  padding: 6px;
  border: 1px solid rgba(209, 221, 232, 0.92);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.16);
  backdrop-filter: blur(10px);
}

.list-context-submenu-shell:hover .list-context-submenu,
.list-context-submenu-shell:focus-within .list-context-submenu {
  display: block;
}

.list-context-menu-item.danger {
  color: #c45656;
}

@media (max-width: 1100px) {
  .sheet-flow {
    align-items: flex-start;
  }
}

@media (max-width: 900px) {
  .sheet-manager-page {
    padding: 12px;
  }
}
</style>

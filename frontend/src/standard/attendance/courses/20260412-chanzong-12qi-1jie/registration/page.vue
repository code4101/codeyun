<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { HotTable } from '@handsontable/vue3'
import { registerAllModules } from 'handsontable/registry'
import type Handsontable from 'handsontable/base'
import { registerLanguageDictionary, zhCN } from 'handsontable/i18n'
import {
  fetchAttendanceSheetDocumentByOwner,
  upsertAttendanceSheetDocument,
} from '@/api/attendance'

import 'handsontable/styles/handsontable.css'
import 'handsontable/styles/ht-theme-main.css'

registerAllModules()
registerLanguageDictionary(zhCN)

const DEFAULT_REGISTRATION_TABLE_COLUMNS = [
  '组号',
  '序号',
  '备注',
  '提交时间',
  '姓名',
  '微信昵称',
  '手机号',
  '错误手机号',
  '微信支付订单号',
  '订单日期',
  '商户订单号',
  '订单金额',
  '已返款',
  '用户ID',
  '匹配得分',
  '参考信息',
] as const

const STORAGE_KEY = 'attendance.course.20260412-chanzong-12qi-1jie.registration.v1'
const CUSTOM_COLUMN_PREFIX = '自定义字段'
const REMOTE_SAVE_DEBOUNCE_MS = 1200
const SHEET_OWNER_TYPE = 'course_session'
const SHEET_OWNER_KEY = '20260412-chanzong-12qi-1jie'
const SHEET_KEY = 'registration'
const SHEET_TITLE = '报名表'

type RegistrationRow = string[]

type RegistrationSheetDocument = {
  schema_version: 1
  columns: string[]
  rows: RegistrationRow[]
}

type RegistrationDraftPayload = {
  version: 3
  updatedAt: number
  sheetId?: string | null
  sheetVersion?: number | null
  document: RegistrationSheetDocument
}

const hotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const sheetFrameRef = ref<HTMLElement | null>(null)
const columnHeaders = ref<string[]>([...DEFAULT_REGISTRATION_TABLE_COLUMNS])
const rows = ref<RegistrationRow[]>([createEmptyRow(DEFAULT_REGISTRATION_TABLE_COLUMNS.length)])
const sheetDocumentId = ref<string | null>(null)
const sheetDocumentVersion = ref<number>(0)
const sheetViewportHeight = ref<number | 'auto'>('auto')

const colWidths = computed(() => columnHeaders.value.map((header) => getColumnWidth(header)))

let suppressPersistence = false
let saveTimer: ReturnType<typeof setTimeout> | null = null
let changeSerial = 0
let lastQueuedSerial = 0
let lastStartedSerial = 0
let saveInFlight = false
let sheetLayoutObserver: ResizeObserver | null = null

const contextMenu = {
  items: {
    row_above: {},
    row_below: {},
    hsep1: '---------',
    insert_col_left: {
      name: '左方插入列',
      callback: () => {
        insertColumnFromSelection('left')
      },
    },
    insert_col_right: {
      name: '右方插入列',
      callback: () => {
        insertColumnFromSelection('right')
      },
    },
    rename_field: {
      name: '重命名字段',
      disabled: () => !hasSingleColumnSelection(),
      callback: () => {
        void renameSelectedColumn()
      },
    },
    remove_col: {},
    remove_row: {},
    hsep2: '---------',
    undo: {},
    redo: {},
  },
}

function createEmptyRow(columnCount = columnHeaders.value.length): RegistrationRow {
  return Array.from({ length: columnCount }, () => '')
}

function normalizeCellValue(value: unknown): string {
  return value == null ? '' : String(value)
}

function normalizeTimestampMs(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return 0
  }
  return numeric < 10000000000 ? numeric * 1000 : numeric
}

function createFallbackHeader(index: number) {
  return DEFAULT_REGISTRATION_TABLE_COLUMNS[index] ?? `${CUSTOM_COLUMN_PREFIX}${index + 1}`
}

function ensureUniqueHeaderName(value: string, used: Set<string>) {
  let candidate = value.trim()
  if (!candidate) {
    candidate = `${CUSTOM_COLUMN_PREFIX}${used.size + 1}`
  }

  if (!used.has(candidate)) {
    used.add(candidate)
    return candidate
  }

  let suffix = 2
  while (used.has(`${candidate}${suffix}`)) {
    suffix += 1
  }

  const uniqueValue = `${candidate}${suffix}`
  used.add(uniqueValue)
  return uniqueValue
}

function normalizeHeaders(source: unknown): string[] {
  if (!Array.isArray(source) || !source.length) {
    return [...DEFAULT_REGISTRATION_TABLE_COLUMNS]
  }

  const used = new Set<string>()
  return source.map((item, index) => ensureUniqueHeaderName(
    normalizeCellValue(item).trim() || createFallbackHeader(index),
    used,
  ))
}

function normalizeRow(row: unknown, headers: string[]): RegistrationRow {
  if (Array.isArray(row)) {
    const normalized = row.slice(0, headers.length).map(normalizeCellValue)
    while (normalized.length < headers.length) {
      normalized.push('')
    }
    return normalized
  }

  if (row && typeof row === 'object') {
    const source = row as Record<string, unknown>
    return headers.map((header) => normalizeCellValue(source[header]))
  }

  return createEmptyRow(headers.length)
}

function isMeaningfulRow(row: RegistrationRow): boolean {
  return row.some((cell) => cell.trim() !== '')
}

function trimTrailingBlankRows(sourceRows: RegistrationRow[]): RegistrationRow[] {
  let end = sourceRows.length
  while (end > 0 && !isMeaningfulRow(sourceRows[end - 1] ?? [])) {
    end -= 1
  }
  return sourceRows.slice(0, end)
}

function createDefaultDocument(): RegistrationSheetDocument {
  return {
    schema_version: 1,
    columns: [...DEFAULT_REGISTRATION_TABLE_COLUMNS],
    rows: [],
  }
}

function normalizeSheetDocument(source: unknown): RegistrationSheetDocument {
  if (!source || typeof source !== 'object') {
    return createDefaultDocument()
  }

  const record = source as Record<string, unknown>
  const headers = normalizeHeaders(record.columns)
  const sourceRows = Array.isArray(record.rows) ? record.rows : []
  const normalizedRows = trimTrailingBlankRows(sourceRows.map((row) => normalizeRow(row, headers)))

  return {
    schema_version: 1,
    columns: headers,
    rows: normalizedRows,
  }
}

function buildCurrentDocument(): RegistrationSheetDocument {
  const headers = normalizeHeaders(columnHeaders.value)
  const normalizedRows = rows.value.map((row) => normalizeRow(row, headers))
  return {
    schema_version: 1,
    columns: headers,
    rows: trimTrailingBlankRows(normalizedRows),
  }
}

function isDefaultEmptyDocument(document: RegistrationSheetDocument): boolean {
  return document.columns.length === DEFAULT_REGISTRATION_TABLE_COLUMNS.length
    && document.columns.every((header, index) => header === DEFAULT_REGISTRATION_TABLE_COLUMNS[index])
    && !document.rows.some(isMeaningfulRow)
}

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function clearSaveTimer() {
  if (!saveTimer) {
    return
  }
  clearTimeout(saveTimer)
  saveTimer = null
}

function clearDraftStorage() {
  if (!canUseLocalStorage()) {
    return
  }
  window.localStorage.removeItem(STORAGE_KEY)
}

function persistDraftDocument(document: RegistrationSheetDocument, updatedAt = Date.now()) {
  if (!canUseLocalStorage()) {
    return
  }

  if (!sheetDocumentId.value && isDefaultEmptyDocument(document)) {
    window.localStorage.removeItem(STORAGE_KEY)
    return
  }

  const payload: RegistrationDraftPayload = {
    version: 3,
    updatedAt,
    sheetId: sheetDocumentId.value,
    sheetVersion: sheetDocumentVersion.value || null,
    document,
  }

  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
  } catch (error) {
    console.warn('Failed to persist registration draft', error)
  }
}

function parseDraftPayload(raw: string): RegistrationDraftPayload | null {
  const parsed = JSON.parse(raw) as unknown

  if (Array.isArray(parsed)) {
    return {
      version: 3,
      updatedAt: Date.now(),
      document: {
        schema_version: 1,
        columns: [...DEFAULT_REGISTRATION_TABLE_COLUMNS],
        rows: trimTrailingBlankRows(parsed.map((row) => normalizeRow(row, [...DEFAULT_REGISTRATION_TABLE_COLUMNS]))),
      },
    }
  }

  if (!parsed || typeof parsed !== 'object') {
    return null
  }

  const payload = parsed as Record<string, unknown>
  const maybeDocument = payload.document && typeof payload.document === 'object'
    ? payload.document
    : { columns: payload.columns, rows: payload.rows }

  return {
    version: 3,
    updatedAt: normalizeTimestampMs(payload.updatedAt) || Date.now(),
    sheetId: typeof payload.sheetId === 'string' ? payload.sheetId : null,
    sheetVersion: Number.isFinite(Number(payload.sheetVersion)) ? Number(payload.sheetVersion) : null,
    document: normalizeSheetDocument(maybeDocument),
  }
}

function readDraftPayload(): RegistrationDraftPayload | null {
  if (!canUseLocalStorage()) {
    return null
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return null
    }
    return parseDraftPayload(raw)
  } catch (error) {
    console.warn('Failed to restore registration draft', error)
    window.localStorage.removeItem(STORAGE_KEY)
    return null
  }
}

function getHotInstance() {
  return hotTableRef.value?.hotInstance ?? null
}

function getMainScrollContainer() {
  return sheetFrameRef.value?.closest('.page-shell-main') as HTMLElement | null
}

function estimateSheetContentHeight() {
  const rowCount = Math.max(rows.value.length, 1)
  const headerHeight = 34
  const rowHeight = 32
  return headerHeight + rowCount * rowHeight + 2
}

async function updateSheetViewportHeight() {
  await nextTick()

  const sheetFrame = sheetFrameRef.value
  const scrollContainer = getMainScrollContainer()
  if (!sheetFrame || !scrollContainer) {
    sheetViewportHeight.value = 'auto'
    return
  }

  const containerRect = scrollContainer.getBoundingClientRect()
  const frameRect = sheetFrame.getBoundingClientRect()
  const availableHeight = Math.floor(containerRect.bottom - frameRect.top - 18)
  if (!Number.isFinite(availableHeight) || availableHeight <= 0) {
    return
  }

  const estimatedContentHeight = estimateSheetContentHeight()
  const minViewportHeight = Math.min(280, availableHeight)
  sheetViewportHeight.value = Math.max(
    Math.min(estimatedContentHeight, availableHeight),
    minViewportHeight,
  )

  getHotInstance()?.render()
}

function handleWindowResize() {
  void updateSheetViewportHeight()
}

function bindSheetLayoutObserver() {
  sheetLayoutObserver?.disconnect()
  sheetLayoutObserver = null

  if (typeof ResizeObserver === 'undefined') {
    return
  }

  const scrollContainer = getMainScrollContainer()
  if (!scrollContainer) {
    return
  }

  sheetLayoutObserver = new ResizeObserver(() => {
    void updateSheetViewportHeight()
  })
  sheetLayoutObserver.observe(scrollContainer)
}

function syncRowsFromGrid() {
  const hot = getHotInstance()
  if (!hot) {
    return rows.value
  }

  const sourceRows = hot.getSourceData() as unknown[]
  rows.value = sourceRows.map((row) => normalizeRow(row, columnHeaders.value))
  return rows.value
}

function isDefaultStructure(headers: string[]) {
  return headers.length === DEFAULT_REGISTRATION_TABLE_COLUMNS.length
    && headers.every((header, index) => header === DEFAULT_REGISTRATION_TABLE_COLUMNS[index])
}

function refreshGridStructure() {
  const hot = getHotInstance()
  if (!hot) {
    return
  }

  hot.updateSettings({
    colHeaders: [...columnHeaders.value],
    colWidths: [...colWidths.value],
  })
  hot.render()
}

function loadSheet(nextHeaders: string[], nextRows: RegistrationRow[]) {
  const normalizedHeaders = normalizeHeaders(nextHeaders)
  const normalizedRows = nextRows.length
    ? nextRows.map((row) => normalizeRow(row, normalizedHeaders))
    : [createEmptyRow(normalizedHeaders.length)]

  columnHeaders.value = normalizedHeaders
  rows.value = normalizedRows

  const hot = getHotInstance()
  if (hot) {
    hot.updateSettings({
      data: normalizedRows,
      colHeaders: [...normalizedHeaders],
      colWidths: normalizedHeaders.map((header) => getColumnWidth(header)),
    })
    hot.render()
  }
}

function setSheetDocumentMeta(id: string | null, version: number | null = null) {
  sheetDocumentId.value = id
  sheetDocumentVersion.value = version && Number.isFinite(version) ? version : 0
}

function handleAfterChange(_changes: unknown, source?: string) {
  if (source === 'loadData' || source === 'external-update') {
    return
  }
  syncRowsFromGrid()
}

function handleAfterCreateRow() {
  syncRowsFromGrid()
}

function handleAfterRemoveRow() {
  syncRowsFromGrid()
  if (!rows.value.length) {
    loadSheet([...columnHeaders.value], [createEmptyRow(columnHeaders.value.length)])
  }
}

async function flushRemoteSave() {
  clearSaveTimer()
  if (saveInFlight || lastQueuedSerial <= lastStartedSerial) {
    return
  }

  const serial = lastQueuedSerial
  const document = buildCurrentDocument()
  if (!sheetDocumentId.value && isDefaultEmptyDocument(document)) {
    return
  }

  saveInFlight = true
  lastStartedSerial = serial

  try {
    const saved = await upsertAttendanceSheetDocument({
      owner_type: SHEET_OWNER_TYPE,
      owner_key: SHEET_OWNER_KEY,
      sheet_key: SHEET_KEY,
      title: SHEET_TITLE,
      engine: 'handsontable',
      document_json: document,
    })

    setSheetDocumentMeta(saved.id, Number(saved.version || 1))

    if (serial === changeSerial) {
      clearDraftStorage()
    } else {
      persistDraftDocument(buildCurrentDocument())
    }
  } catch (error) {
    console.warn('Failed to save registration sheet document', error)
    persistDraftDocument(document)
  } finally {
    saveInFlight = false
    if (lastQueuedSerial > serial) {
      void flushRemoteSave()
    }
  }
}

function scheduleRemoteSave(delayMs = REMOTE_SAVE_DEBOUNCE_MS) {
  if (suppressPersistence) {
    return
  }

  const document = buildCurrentDocument()
  persistDraftDocument(document)

  if (!sheetDocumentId.value && isDefaultEmptyDocument(document)) {
    clearSaveTimer()
    return
  }

  changeSerial += 1
  lastQueuedSerial = changeSerial
  clearSaveTimer()
  saveTimer = setTimeout(() => {
    void flushRemoteSave()
  }, Math.max(0, delayMs))
}

async function restoreInitialDocument() {
  suppressPersistence = true

  try {
    const localDraft = readDraftPayload()
    let remoteDocumentRecord = null

    try {
      remoteDocumentRecord = await fetchAttendanceSheetDocumentByOwner({
        owner_type: SHEET_OWNER_TYPE,
        owner_key: SHEET_OWNER_KEY,
        sheet_key: SHEET_KEY,
      })
    } catch (error) {
      console.warn('Failed to load registration sheet document from backend', error)
    }

    const remoteDocument = remoteDocumentRecord
      ? normalizeSheetDocument(remoteDocumentRecord.document_json)
      : null

    if (remoteDocumentRecord) {
      setSheetDocumentMeta(remoteDocumentRecord.id, Number(remoteDocumentRecord.version || 1))
    }

    let activeDocument = remoteDocument ?? createDefaultDocument()
    let shouldSyncLocalDraft = false

    if (localDraft?.document) {
      if (!remoteDocumentRecord) {
        activeDocument = localDraft.document
        shouldSyncLocalDraft = !isDefaultEmptyDocument(localDraft.document)
      } else {
        const remoteUpdatedAt = normalizeTimestampMs(remoteDocumentRecord.updated_at)
        if (localDraft.updatedAt > remoteUpdatedAt) {
          activeDocument = localDraft.document
          shouldSyncLocalDraft = true
        } else {
          activeDocument = remoteDocument ?? localDraft.document
          clearDraftStorage()
        }
      }
    } else if (remoteDocumentRecord) {
      clearDraftStorage()
    }

    loadSheet(activeDocument.columns, activeDocument.rows)
    changeSerial = 0
    lastQueuedSerial = 0
    lastStartedSerial = 0

    suppressPersistence = false

    if (shouldSyncLocalDraft) {
      scheduleRemoteSave(0)
    }
  } finally {
    suppressPersistence = false
  }
}

function handleAfterCreateCol(index: number, amount: number) {
  const nextHeaders = [...columnHeaders.value]
  nextHeaders.splice(index, 0, ...createCustomColumnNames(amount, nextHeaders))
  columnHeaders.value = nextHeaders
  refreshGridStructure()
  syncRowsFromGrid()
}

function handleBeforeRemoveCol(_index: number, amount: number) {
  return columnHeaders.value.length - amount >= 1
}

function handleAfterRemoveCol(index: number, amount: number) {
  const nextHeaders = [...columnHeaders.value]
  nextHeaders.splice(index, amount)
  columnHeaders.value = nextHeaders.length ? nextHeaders : [createFallbackHeader(0)]
  refreshGridStructure()
  syncRowsFromGrid()
}

function getSelectionColumnBounds() {
  const hot = getHotInstance()
  const selection = hot?.getSelectedLast()
  if (!selection) {
    return null
  }

  const startCol = Math.min(selection[1], selection[3])
  const endCol = Math.max(selection[1], selection[3])
  if (endCol < 0) {
    return null
  }

  return {
    start: Math.max(startCol, 0),
    end: endCol,
  }
}

function hasSingleColumnSelection() {
  const bounds = getSelectionColumnBounds()
  return !!bounds && bounds.start === bounds.end
}

function getNextCustomColumnNumber(headers: string[]) {
  const usedNumbers = headers
    .map((header) => header.match(new RegExp(`^${CUSTOM_COLUMN_PREFIX}(\\d+)$`)))
    .filter((match): match is RegExpMatchArray => !!match)
    .map((match) => Number(match[1]))
    .filter((value) => Number.isFinite(value))

  return usedNumbers.length ? Math.max(...usedNumbers) + 1 : 1
}

function createCustomColumnNames(amount: number, existingHeaders = columnHeaders.value) {
  const nextHeaders = [...existingHeaders]
  const names: string[] = []
  let nextNumber = getNextCustomColumnNumber(nextHeaders)

  while (names.length < amount) {
    const candidate = `${CUSTOM_COLUMN_PREFIX}${nextNumber}`
    nextNumber += 1
    if (nextHeaders.includes(candidate) || names.includes(candidate)) {
      continue
    }
    names.push(candidate)
  }

  return names
}

function insertColumnFromSelection(side: 'left' | 'right') {
  const hot = getHotInstance()
  if (!hot) {
    return
  }

  const bounds = getSelectionColumnBounds()
  const targetIndex = bounds
    ? (side === 'left' ? bounds.start : bounds.end + 1)
    : columnHeaders.value.length

  hot.alter('insert_col_start', targetIndex, 1)
}

async function renameSelectedColumn() {
  const bounds = getSelectionColumnBounds()
  if (!bounds || bounds.start !== bounds.end) {
    return
  }

  const columnIndex = bounds.start
  const currentHeader = columnHeaders.value[columnIndex] ?? createFallbackHeader(columnIndex)

  try {
    const { value } = await ElMessageBox.prompt('请输入字段名', '重命名字段', {
      inputValue: currentHeader,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => {
        const nextValue = inputValue.trim()
        if (!nextValue) {
          return '字段名不能为空'
        }
        if (columnHeaders.value.some((header, index) => index !== columnIndex && header === nextValue)) {
          return '字段名不能重复'
        }
        return true
      },
    })

    const nextHeaders = [...columnHeaders.value]
    nextHeaders[columnIndex] = value.trim()
    columnHeaders.value = nextHeaders
    refreshGridStructure()
  } catch {
    return
  }
}

function getColumnWidth(header: string) {
  if (header === '组号' || header === '序号') {
    return 80
  }
  if (header === '备注') {
    return 140
  }
  if (header === '提交时间') {
    return 170
  }
  if (header === '姓名') {
    return 100
  }
  if (header === '微信昵称' || header === '手机号' || header === '错误手机号') {
    return 130
  }
  if (header === '微信支付订单号' || header === '商户订单号') {
    return 190
  }
  if (header === '订单日期') {
    return 130
  }
  if (header === '订单金额' || header === '已返款' || header === '匹配得分') {
    return 110
  }
  if (header === '用户ID') {
    return 120
  }
  if (header === '参考信息') {
    return 220
  }
  return 140
}

watch(
  [rows, columnHeaders],
  () => {
    scheduleRemoteSave()
  },
  { deep: true },
)

watch(
  [() => rows.value.length, () => columnHeaders.value.length],
  () => {
    void updateSheetViewportHeight()
  },
)

onMounted(() => {
  bindSheetLayoutObserver()
  window.addEventListener('resize', handleWindowResize)
  void restoreInitialDocument().finally(() => {
    void updateSheetViewportHeight()
  })
})

onBeforeUnmount(() => {
  clearSaveTimer()
  window.removeEventListener('resize', handleWindowResize)
  sheetLayoutObserver?.disconnect()
  sheetLayoutObserver = null
})
</script>

<template>
  <div class="attendance-registration-page">
    <div ref="sheetFrameRef" class="sheet-frame">
      <HotTable
        ref="hotTableRef"
        :data="rows"
        :language="'zh-CN'"
        :col-headers="columnHeaders"
        :col-widths="colWidths"
        :row-headers="true"
        :manual-column-resize="true"
        :manual-row-resize="true"
        :copy-paste="true"
        :context-menu="contextMenu"
        :auto-row-size="true"
        :auto-wrap-row="true"
        :auto-wrap-col="true"
        :min-spare-rows="3"
        :render-all-rows="false"
        :height="sheetViewportHeight"
        :stretch-h="'none'"
        :selection-mode="'multiple'"
        :outside-click-deselects="false"
        :theme-name="'ht-theme-main'"
        :license-key="'non-commercial-and-evaluation'"
        :after-change="handleAfterChange"
        :after-create-row="handleAfterCreateRow"
        :after-remove-row="handleAfterRemoveRow"
        :after-create-col="handleAfterCreateCol"
        :before-remove-col="handleBeforeRemoveCol"
        :after-remove-col="handleAfterRemoveCol"
      />
    </div>
  </div>
</template>

<style scoped>
.attendance-registration-page {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  min-height: 0;
  padding: 10px 18px 18px;
  overflow: hidden;
}

.sheet-frame {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  border: 1px solid #e7dcc9;
  border-radius: 10px;
  background: #fff;
}

.sheet-frame :deep(.handsontable) {
  font-size: 13px;
}

.sheet-frame :deep(.htCore td),
.sheet-frame :deep(.htCore th) {
  vertical-align: top;
}

@media (max-width: 900px) {
  .attendance-registration-page {
    padding: 12px;
  }
}
</style>

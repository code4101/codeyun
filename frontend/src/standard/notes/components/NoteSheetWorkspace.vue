<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { HotTable } from '@handsontable/vue3'
import { registerAllModules } from 'handsontable/registry'
import type Handsontable from 'handsontable/base'
import { registerLanguageDictionary, zhCN } from 'handsontable/i18n'

import {
  fetchNoteSheet,
  type NoteSheetPaginationState,
  sortNoteSheet,
  updateNoteSheet,
  type NoteSheetDetail,
} from '@/api/noteSheets'
import { mixWeightedColors, toHex } from '@/utils/colorToolkit'
import { stableHash32 } from '@/utils/stableVisualColor'

import 'handsontable/styles/handsontable.css'
import 'handsontable/styles/ht-theme-main.css'

registerAllModules()
registerLanguageDictionary(zhCN)

const DEFAULT_SHEET_COLUMNS = ['列1', '列2', '列3'] as const
const CUSTOM_COLUMN_PREFIX = '自定义字段'
const REMOTE_SAVE_DEBOUNCE_MS = 1200
const DEFAULT_PAGE_SIZE = 100
const TABLE_FONT = '400 14px Inter, "Segoe UI", sans-serif'
const TABLE_HEADER_FONT = '600 13px Inter, "Segoe UI", sans-serif'
const TABLE_LINE_HEIGHT = 20
const TABLE_CELL_VERTICAL_PADDING = 4
const TABLE_CELL_HORIZONTAL_PADDING = 8
const TABLE_CELL_BORDER_WIDTH = 1
const DEFAULT_TABLE_ROW_HEIGHT =
  TABLE_LINE_HEIGHT + TABLE_CELL_VERTICAL_PADDING * 2 + TABLE_CELL_BORDER_WIDTH
const MIN_COLUMN_WIDTH = 88
const MAX_COLUMN_WIDTH = 360
const HEADER_WIDTH_PADDING = 34
const INLINE_HEADER_INPUT_PADDING = 16
const INVALID_VALUE_HIGHLIGHT_COLOR = '#E98296'
const DUPLICATE_HIGHLIGHT_PALETTE = Object.freeze([
  '#4F83F1',
  '#2F9E8F',
  '#C97A1F',
  '#C95A8A',
  '#7D5AF0',
  '#2F7FD1',
  '#C2553D',
  '#6F9B2D',
  '#9A56C8',
  '#1C8A70',
  '#B66A17',
  '#4C6DE0',
])

type SheetRow = string[]

type ColumnDisplayMode = 'wrap' | 'single_line'
type ColumnMarkerStyle = 'letters' | 'numbers'
type ColumnMarkerMode = 'none' | 'letters' | 'numbers'
type RowMarkerMode = 'none' | 'numbers'
type SortDirection = 'asc' | 'desc'
type ColumnWidthMode = 'adaptive' | 'fixed'
type ColumnValueType = 'text' | 'number' | 'phone'

type SheetColumnConfig = {
  value_type?: ColumnValueType
  allow_empty?: boolean
  display_mode?: ColumnDisplayMode
  trim_whitespace?: boolean
  duplicate_value_highlight?: boolean
  width_mode?: ColumnWidthMode
  hidden?: boolean
  restore_index?: number
  note?: string
}

type ColumnSettingsDraft = {
  value_type: ColumnValueType
  allow_empty: boolean
  display_mode: ColumnDisplayMode
  trim_whitespace: boolean
  duplicate_value_highlight: boolean
  width_mode: ColumnWidthMode
  width_value: number
  note: string
}

type SheetViewSettings = {
  show_row_numbers?: boolean
  show_column_markers?: boolean
  column_marker_style?: ColumnMarkerStyle
  pagination?: {
    enabled?: boolean
    page_size?: number
  }
}

type SheetDocument = {
  schema_version: 1
  columns: string[]
  rows: SheetRow[]
  column_configs?: Record<string, SheetColumnConfig>
  column_widths?: number[]
  view_settings?: SheetViewSettings
}

type SheetDraftPayload = {
  version: 1
  updatedAt: number
  sheetVersion?: number | null
  title: string
  document: SheetDocument
  pageState?: SheetPagePatchState | null
}

type SheetWorkspaceSyncPayload = {
  id: number
  title: string
  version: number
  updatedAt: number
}

type SheetPagePatchState = {
  paginationEnabled: boolean
  page: number
  pageSize: number
  rowOffset: number
  loadedRowCount: number
}

interface Props {
  sheetId: number | null
  showBackButton?: boolean
  showTitleInput?: boolean
  backTo?: string
  backLabel?: string
  emptyText?: string
}

const props = withDefaults(defineProps<Props>(), {
  showBackButton: false,
  showTitleInput: true,
  backTo: '/notes/sheets',
  backLabel: '返回表格管理',
  emptyText: '请选择表格',
})

const emit = defineEmits<{
  missing: [sheetId: number]
  sheetSync: [payload: SheetWorkspaceSyncPayload]
}>()

const router = useRouter()

const currentPage = ref(1)
const pageSize = ref(DEFAULT_PAGE_SIZE)
const pageCount = ref(1)
const totalRowCount = ref(0)
const pageRowOffset = ref(0)
const pageLoadedRowCount = ref(0)

const storageKey = computed(() => (
  `notes.sheet.${props.sheetId ?? 'empty'}.page.${currentPage.value}.size.${pageSize.value}.draft.v2`
))

let textMeasureContext: CanvasRenderingContext2D | null = null
const textMeasureCache = new Map<string, number>()
const duplicateHighlightStyleCache = new Map<string, { backgroundColor: string }>()

const hotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const sheetFrameRef = ref<HTMLElement | null>(null)
const columnNotePopoverRef = ref<HTMLElement | null>(null)
const columnHeaders = ref<string[]>([...DEFAULT_SHEET_COLUMNS])
const columnConfigs = ref<Record<string, SheetColumnConfig>>({})
const sheetViewSettings = ref<Required<SheetViewSettings>>(createDefaultSheetViewSettings())
const columnWidths = ref<number[]>(DEFAULT_SHEET_COLUMNS.map((header) => getAdaptiveColumnWidth(header)))
const editingColumnIndex = ref<number | null>(null)
const editingColumnTitle = ref('')
const rows = ref<SheetRow[]>([createEmptyRow(DEFAULT_SHEET_COLUMNS.length)])
const sheetTitle = ref('未命名表格')
const sheetVersion = ref<number>(0)
const sheetViewportHeight = ref<number | 'auto'>('auto')
const sheetSettingsDialogVisible = ref(false)
const sheetSettingsDraft = ref<Required<SheetViewSettings>>(createDefaultSheetViewSettings())
const sheetSettingsRowMarkerMode = computed<RowMarkerMode>({
  get() {
    return sheetSettingsDraft.value.show_row_numbers ? 'numbers' : 'none'
  },
  set(mode) {
    sheetSettingsDraft.value.show_row_numbers = mode === 'numbers'
  },
})
const sheetSettingsColumnMarkerMode = computed<ColumnMarkerMode>({
  get() {
    if (!sheetSettingsDraft.value.show_column_markers) {
      return 'none'
    }
    return sheetSettingsDraft.value.column_marker_style
  },
  set(mode) {
    if (mode === 'none') {
      sheetSettingsDraft.value.show_column_markers = false
      return
    }
    sheetSettingsDraft.value.show_column_markers = true
    sheetSettingsDraft.value.column_marker_style = mode
  },
})
const columnSettingsDialogVisible = ref(false)
const columnSettingsColumnIndex = ref<number | null>(null)
const columnSettingsDraft = ref<ColumnSettingsDraft>({
  value_type: 'text',
  allow_empty: true,
  display_mode: 'wrap',
  trim_whitespace: true,
  duplicate_value_highlight: false,
  width_mode: 'adaptive',
  width_value: 120,
  note: '',
})
const columnNotePopover = ref<{
  visible: boolean
  x: number
  y: number
  title: string
  note: string
  columnIndex: number | null
}>({
  visible: false,
  x: 0,
  y: 0,
  title: '',
  note: '',
  columnIndex: null,
})
const workspaceLoading = ref(false)
const pageWorkingRowCount = computed(() => trimTrailingBlankRows(
  rows.value.map((row) => normalizeRow(row, columnHeaders.value)),
).length)
const paginationEnabled = computed(() => sheetViewSettings.value.pagination.enabled)
const pageStatusText = computed(() => {
  if (props.sheetId == null) {
    return ''
  }

  if (!paginationEnabled.value) {
    return `共 ${pageWorkingRowCount.value} 行`
  }

  const extra = pageWorkingRowCount.value - pageSize.value
  if (extra === 0) {
    return `当前页 ${pageWorkingRowCount.value} 行`
  }
  return `当前页 ${pageWorkingRowCount.value} 行 / 标准 ${pageSize.value}`
})

const duplicateHighlightMap = computed(() => {
  const highlightMap = new Map<string, string>()

  columnHeaders.value.forEach((header, columnIndex) => {
    const columnConfig = normalizeColumnConfig(columnConfigs.value[header])
    if (!columnConfig.duplicate_value_highlight) {
      return
    }

    const duplicateRows = new Map<string, number[]>()
    rows.value.forEach((row, rowIndex) => {
      const normalizedValue = normalizeCellValue(row?.[columnIndex] ?? '').trim()
      if (!normalizedValue) {
        return
      }

      const matchedRows = duplicateRows.get(normalizedValue)
      if (matchedRows) {
        matchedRows.push(rowIndex)
      } else {
        duplicateRows.set(normalizedValue, [rowIndex])
      }
    })

    duplicateRows.forEach((rowIndexes, cellValue) => {
      if (rowIndexes.length < 2) {
        return
      }

      const baseColor = getDuplicateHighlightBaseColor(cellValue)
      if (!baseColor) {
        return
      }

      rowIndexes.forEach((rowIndex) => {
        highlightMap.set(`${rowIndex}:${columnIndex}`, baseColor)
      })
    })
  })

  return highlightMap
})

const invalidValueHighlightMap = computed(() => {
  const highlightMap = new Map<string, string>()

  columnHeaders.value.forEach((header, columnIndex) => {
    const columnConfig = normalizeColumnConfig(columnConfigs.value[header])
    if (columnConfig.value_type === 'text' && columnConfig.allow_empty) {
      return
    }

    rows.value.forEach((row, rowIndex) => {
      const cellValue = normalizeCellValue(row?.[columnIndex] ?? '')
      if (isColumnValueValidByType(cellValue, columnConfig.value_type, columnConfig.allow_empty)) {
        return
      }
      highlightMap.set(`${rowIndex}:${columnIndex}`, INVALID_VALUE_HIGHLIGHT_COLOR)
    })
  })

  return highlightMap
})

const columnNotePopoverStyle = computed(() => ({
  left: `${columnNotePopover.value.x}px`,
  top: `${columnNotePopover.value.y}px`,
}))

const hiddenColumnIndexes = computed(() => (
  columnHeaders.value
    .map((header, index) => (columnConfigs.value[header]?.hidden === true ? index : -1))
    .filter((index) => index >= 0)
))

const hiddenColumnsForSettings = computed(() => (
  columnHeaders.value
    .map((header, index) => ({
      header,
      index,
      hidden: columnConfigs.value[header]?.hidden === true,
    }))
    .filter((item) => item.hidden)
))

const nestedHeaders = computed(() => [
  ...(
    sheetViewSettings.value.show_column_markers
      ? [columnHeaders.value.map((_, index) => ({
        label: getColumnMarkerLabel(index),
        headerClassName: 'sheet-col-marker',
      }))]
      : []
  ),
  [...columnHeaders.value],
])

let suppressPersistence = false
let saveTimer: ReturnType<typeof setTimeout> | null = null
let changeSerial = 0
let lastQueuedSerial = 0
let saveInFlight = false
let sheetLayoutObserver: ResizeObserver | null = null
let editingHeaderInputEl: HTMLInputElement | null = null

const contextMenu = {
  items: {
    row_above: {
      name: '上方插入行',
      hidden: () => !shouldShowRowActions(),
      callback: () => {
        insertRowFromSelection('above')
      },
    },
    row_below: {
      name: '下方插入行',
      hidden: () => !shouldShowRowActions(),
      callback: () => {
        insertRowFromSelection('below')
      },
    },
    hsep1: {
      name: '---------',
      hidden: () => !shouldShowRowActions() || !shouldShowColumnActions(),
    },
    insert_col_left: {
      name: '左方插入列',
      hidden: () => !shouldShowColumnActions(),
      callback: () => {
        insertColumnFromSelection('left')
      },
    },
    insert_col_right: {
      name: '右方插入列',
      hidden: () => !shouldShowColumnActions(),
      callback: () => {
        insertColumnFromSelection('right')
      },
    },
    column_sort: {
      name: '排序',
      hidden: () => !hasSingleColumnHeaderSelection(),
      submenu: {
        items: [
          {
            key: 'column_sort:asc',
            name: '升序',
            callback: () => {
              void handleSelectedColumnSort('asc')
            },
          },
          {
            key: 'column_sort:desc',
            name: '降序',
            callback: () => {
              void handleSelectedColumnSort('desc')
            },
          },
        ],
      },
    },
    column_settings: {
      name: '设置',
      hidden: () => !hasSingleColumnHeaderSelection(),
      callback: () => {
        openSelectedColumnSettings()
      },
    },
    hide_column: {
      name: '隐藏字段',
      hidden: () => !shouldShowHideColumnAction(),
      callback: () => {
        hideSelectedColumns()
      },
    },
    remove_col: {
      name: '移除该列',
      hidden: () => !shouldShowRemoveColumnAction(),
      callback: () => {
        removeSelectedColumns()
      },
    },
    remove_row: {
      name: '移除多行',
      hidden: () => !shouldShowRemoveRowAction(),
      callback: () => {
        removeSelectedRows()
      },
    },
    hsep2: {
      name: '---------',
      hidden: () => !shouldShowUndoAction() && !shouldShowRedoAction(),
    },
    undo: {
      name: '撤销',
      hidden: () => !shouldShowUndoAction(),
      callback: () => {
        getUndoRedoPlugin()?.undo()
      },
    },
    redo: {
      name: '恢复',
      hidden: () => !shouldShowRedoAction(),
      callback: () => {
        getUndoRedoPlugin()?.redo()
      },
    },
  },
}

function createEmptyRow(columnCount = columnHeaders.value.length): SheetRow {
  return Array.from({ length: columnCount }, () => '')
}

function closeColumnNotePopover() {
  columnNotePopover.value.visible = false
  columnNotePopover.value.columnIndex = null
}

function bindColumnNotePopoverPosition(anchorRect: DOMRect) {
  const margin = 12
  columnNotePopover.value.x = anchorRect.left
  columnNotePopover.value.y = anchorRect.bottom + 8

  void nextTick(() => {
    const popoverEl = columnNotePopoverRef.value
    if (!popoverEl) {
      return
    }
    const rect = popoverEl.getBoundingClientRect()
    const maxX = Math.max(margin, window.innerWidth - rect.width - margin)
    const maxY = Math.max(margin, window.innerHeight - rect.height - margin)
    columnNotePopover.value.x = Math.min(Math.max(anchorRect.left, margin), maxX)
    columnNotePopover.value.y = Math.min(Math.max(anchorRect.bottom + 8, margin), maxY)
  })
}

function openColumnNotePopover(columnIndex: number, anchorRect: DOMRect) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    closeColumnNotePopover()
    return
  }

  const note = normalizeColumnNote(columnConfigs.value[header]?.note)
  if (!note) {
    closeColumnNotePopover()
    return
  }

  const shouldToggleClose = (
    columnNotePopover.value.visible
    && columnNotePopover.value.columnIndex === columnIndex
  )
  if (shouldToggleClose) {
    closeColumnNotePopover()
    return
  }

  columnNotePopover.value.visible = true
  columnNotePopover.value.columnIndex = columnIndex
  columnNotePopover.value.title = header
  columnNotePopover.value.note = note
  bindColumnNotePopoverPosition(anchorRect)
}

function handleGlobalMouseDown(event: MouseEvent) {
  if (!columnNotePopover.value.visible) {
    return
  }

  const target = event.target as Node | null
  if (target && columnNotePopoverRef.value?.contains(target)) {
    return
  }
  closeColumnNotePopover()
}

function resetWorkspaceState() {
  clearEditingColumnState()
  closeColumnNotePopover()
  columnHeaders.value = [...DEFAULT_SHEET_COLUMNS]
  columnConfigs.value = {}
  sheetViewSettings.value = createDefaultSheetViewSettings()
  columnWidths.value = DEFAULT_SHEET_COLUMNS.map((header) => getAdaptiveColumnWidth(header))
  rows.value = [createEmptyRow(DEFAULT_SHEET_COLUMNS.length)]
  sheetTitle.value = '未命名表格'
  sheetVersion.value = 0
  sheetSettingsDialogVisible.value = false
  sheetSettingsDraft.value = createDefaultSheetViewSettings()
  columnSettingsDialogVisible.value = false
  columnSettingsColumnIndex.value = null
  columnSettingsDraft.value = {
    value_type: 'text',
    allow_empty: true,
    display_mode: 'wrap',
    trim_whitespace: true,
    duplicate_value_highlight: false,
    width_mode: 'adaptive',
    width_value: 120,
    note: '',
  }
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

function normalizePositivePageNumber(value: unknown, fallback: number) {
  const numeric = Number(value)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : fallback
}

function normalizeNonNegativeInt(value: unknown, fallback = 0) {
  const numeric = Number(value)
  return Number.isInteger(numeric) && numeric >= 0 ? numeric : fallback
}

function createFallbackHeader(index: number) {
  return DEFAULT_SHEET_COLUMNS[index] ?? `${CUSTOM_COLUMN_PREFIX}${index + 1}`
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
    return [...DEFAULT_SHEET_COLUMNS]
  }

  const used = new Set<string>()
  return source.map((item, index) => ensureUniqueHeaderName(
    normalizeCellValue(item).trim() || createFallbackHeader(index),
    used,
  ))
}

function normalizeColumnDisplayMode(value: unknown): ColumnDisplayMode {
  return value === 'single_line' ? 'single_line' : 'wrap'
}

function normalizeColumnValueType(value: unknown): ColumnValueType {
  if (value === 'number' || value === 'phone') {
    return value
  }
  return 'text'
}

function normalizeColumnWidthMode(value: unknown): ColumnWidthMode {
  return value === 'fixed' ? 'fixed' : 'adaptive'
}

function normalizeColumnNote(value: unknown) {
  return normalizeCellValue(value).trim()
}

function isColumnHiddenConfigValue(value: unknown) {
  return value === true
}

function createDefaultSheetViewSettings(): Required<SheetViewSettings> {
  return {
    show_row_numbers: true,
    show_column_markers: true,
    column_marker_style: 'letters',
    pagination: {
      enabled: false,
      page_size: DEFAULT_PAGE_SIZE,
    },
  }
}

function normalizeColumnMarkerStyle(value: unknown): ColumnMarkerStyle {
  return value === 'numbers' ? 'numbers' : 'letters'
}

function normalizeSheetPageSize(value: unknown) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return DEFAULT_PAGE_SIZE
  }
  return Math.min(Math.max(Math.round(numeric), 1), 1000)
}

function normalizeSheetViewSettings(source: unknown): Required<SheetViewSettings> {
  const defaults = createDefaultSheetViewSettings()
  if (!source || typeof source !== 'object') {
    return defaults
  }

  const record = source as Record<string, unknown>
  return {
    show_row_numbers: record.show_row_numbers !== false,
    show_column_markers: record.show_column_markers !== false,
    column_marker_style: normalizeColumnMarkerStyle(record.column_marker_style),
    pagination: (() => {
      const pagination = record.pagination
      if (!pagination || typeof pagination !== 'object') {
        return { ...defaults.pagination }
      }
      const paginationRecord = pagination as Record<string, unknown>
      return {
        enabled: paginationRecord.enabled === true,
        page_size: normalizeSheetPageSize(paginationRecord.page_size),
      }
    })(),
  }
}

function normalizeColumnConfigs(source: unknown, headers: string[]): Record<string, SheetColumnConfig> {
  if (!source || typeof source !== 'object') {
    return {}
  }

  const record = source as Record<string, unknown>
  const normalized: Record<string, SheetColumnConfig> = {}

  for (const header of headers) {
    const config = record[header]
    if (!config || typeof config !== 'object') {
      continue
    }

    const configRecord = config as Record<string, unknown>
    const valueType = normalizeColumnValueType(configRecord.value_type)
    const allowEmpty = configRecord.allow_empty !== false
    const displayMode = normalizeColumnDisplayMode(configRecord.display_mode)
    const trimWhitespace = configRecord.trim_whitespace !== false
    const duplicateValueHighlight = configRecord.duplicate_value_highlight === true
    const widthMode = normalizeColumnWidthMode(configRecord.width_mode)
    const hidden = isColumnHiddenConfigValue(configRecord.hidden)
    const restoreIndex = normalizeNonNegativeInt(configRecord.restore_index, -1)
    const note = normalizeColumnNote(configRecord.note)

    if (
      valueType !== 'text'
      || allowEmpty === false
      || displayMode !== 'wrap'
      || trimWhitespace === false
      || duplicateValueHighlight
      || widthMode !== 'adaptive'
      || hidden
      || restoreIndex >= 0
      || note
    ) {
      normalized[header] = {}
      if (valueType !== 'text') {
        normalized[header].value_type = valueType
      }
      if (!allowEmpty) {
        normalized[header].allow_empty = false
      }
      if (displayMode !== 'wrap') {
        normalized[header].display_mode = displayMode
      }
      if (!trimWhitespace) {
        normalized[header].trim_whitespace = false
      }
      if (duplicateValueHighlight) {
        normalized[header].duplicate_value_highlight = true
      }
      if (widthMode !== 'adaptive') {
        normalized[header].width_mode = widthMode
      }
      if (hidden) {
        normalized[header].hidden = true
      }
      if (restoreIndex >= 0) {
        normalized[header].restore_index = restoreIndex
      }
      if (note) {
        normalized[header].note = note
      }
    }
  }

  return normalized
}

function normalizeColumnConfig(source: unknown): ColumnSettingsDraft {
  if (!source || typeof source !== 'object') {
    return {
      value_type: 'text',
      allow_empty: true,
      display_mode: 'wrap',
      trim_whitespace: true,
      duplicate_value_highlight: false,
      width_mode: 'adaptive',
      width_value: 120,
      note: '',
    }
  }

  const record = source as Record<string, unknown>
  return {
    value_type: normalizeColumnValueType(record.value_type),
    allow_empty: record.allow_empty !== false,
    display_mode: normalizeColumnDisplayMode(record.display_mode),
    trim_whitespace: record.trim_whitespace !== false,
    duplicate_value_highlight: record.duplicate_value_highlight === true,
    width_mode: normalizeColumnWidthMode(record.width_mode),
    width_value: 120,
    note: normalizeColumnNote(record.note),
  }
}

function getVisibleColumnCount() {
  return columnHeaders.value.length - hiddenColumnIndexes.value.length
}

function shouldShowHideColumnAction() {
  const bounds = getSelectionColumnBounds()
  if (!shouldShowColumnActions() || !bounds || !isSelectedByColumnHeader() || isSelectedByCorner()) {
    return false
  }
  const selectedCount = bounds.end - bounds.start + 1
  return getVisibleColumnCount() - selectedCount >= 1
}

function getDuplicateHighlightColorSeed(value: string) {
  return value.trim()
}

function getDuplicateHighlightBaseColor(seed: string) {
  const normalizedSeed = getDuplicateHighlightColorSeed(seed)
  if (!normalizedSeed) {
    return null
  }

  const colorIndex = stableHash32(normalizedSeed) % DUPLICATE_HIGHLIGHT_PALETTE.length
  return DUPLICATE_HIGHLIGHT_PALETTE[colorIndex] ?? DUPLICATE_HIGHLIGHT_PALETTE[0]
}

function mixCellAccentStyle(sourceColors: string[]) {
  const normalizedSourceColors = sourceColors.filter(Boolean)
  if (!normalizedSourceColors.length) {
    return null
  }

  const cacheKey = normalizedSourceColors.join('|')
  const cached = duplicateHighlightStyleCache.get(cacheKey)
  if (cached) {
    return cached
  }

  const mixedColor = mixWeightedColors(
    normalizedSourceColors.map((color) => ({
      color,
      weight: 100,
    })),
    { fillColor: '#FFFFFF', fillToWeight: normalizedSourceColors.length * 100 + 220 },
  )

  if (!mixedColor) {
    return null
  }

  const style = {
    backgroundColor: toHex(mixedColor),
  }
  duplicateHighlightStyleCache.set(cacheKey, style)
  return style
}

function normalizeRow(row: unknown, headers: string[]): SheetRow {
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

function isMeaningfulRow(row: SheetRow): boolean {
  return row.some((cell) => cell.trim() !== '')
}

function trimTrailingBlankRows(sourceRows: SheetRow[]): SheetRow[] {
  let end = sourceRows.length
  while (end > 0 && !isMeaningfulRow(sourceRows[end - 1] ?? [])) {
    end -= 1
  }
  return sourceRows.slice(0, end)
}

function createDefaultDocument(): SheetDocument {
  return {
    schema_version: 1,
    columns: [...DEFAULT_SHEET_COLUMNS],
    rows: [],
    column_configs: {},
    column_widths: DEFAULT_SHEET_COLUMNS.map((header) => getAdaptiveColumnWidth(header)),
    view_settings: createDefaultSheetViewSettings(),
  }
}

function normalizeSheetDocument(source: unknown): SheetDocument {
  if (!source || typeof source !== 'object') {
    return createDefaultDocument()
  }

  const record = source as Record<string, unknown>
  const headers = normalizeHeaders(record.columns)
  const sourceRows = Array.isArray(record.rows) ? record.rows : []
  const normalizedRows = trimTrailingBlankRows(sourceRows.map((row) => normalizeRow(row, headers)))
  const sourceWidths = Array.isArray(record.column_widths) ? record.column_widths : []
  const normalizedColumnConfigs = normalizeColumnConfigs(record.column_configs, headers)
  const normalizedWidths = headers.map((_, index) => {
    const width = Number(sourceWidths[index])
    const widthMode = normalizeColumnConfig(normalizedColumnConfigs[headers[index]]).width_mode
    if (widthMode === 'fixed' && Number.isFinite(width) && width > 0) {
      return normalizeColumnWidthValue(width)
    }
    return getAutoColumnWidth(index, headers, normalizedRows)
  })
  return {
    schema_version: 1,
    columns: headers,
    rows: normalizedRows,
    column_configs: normalizedColumnConfigs,
    column_widths: normalizedWidths,
    view_settings: normalizeSheetViewSettings(record.view_settings),
  }
}

function buildCurrentDocument(): SheetDocument {
  const headers = normalizeHeaders(columnHeaders.value)
  const normalizedRows = trimTrailingBlankRows(rows.value.map((row) => normalizeRow(row, headers)))
  return {
    schema_version: 1,
    columns: headers,
    rows: normalizedRows,
    column_configs: normalizeColumnConfigs(columnConfigs.value, headers),
    column_widths: headers.map((_, index) => columnWidths.value[index] ?? getAutoColumnWidth(index, headers, normalizedRows)),
    view_settings: normalizeSheetViewSettings(sheetViewSettings.value),
  }
}

function buildCurrentPagePatchState(): SheetPagePatchState {
  return {
    paginationEnabled: paginationEnabled.value,
    page: currentPage.value,
    pageSize: pageSize.value,
    rowOffset: pageRowOffset.value,
    loadedRowCount: pageLoadedRowCount.value,
  }
}

function normalizeDraftPageState(source: unknown): SheetPagePatchState | null {
  if (!source || typeof source !== 'object') {
    return null
  }

  const record = source as Record<string, unknown>
  return {
    paginationEnabled: record.paginationEnabled === true,
    page: normalizePositivePageNumber(record.page, currentPage.value),
    pageSize: normalizePositivePageNumber(record.pageSize, pageSize.value),
    rowOffset: normalizeNonNegativeInt(record.rowOffset, pageRowOffset.value),
    loadedRowCount: normalizeNonNegativeInt(record.loadedRowCount, pageLoadedRowCount.value),
  }
}

function isSamePagePatchState(pageState: SheetPagePatchState | null) {
  if (!pageState) {
    return false
  }

  return (
    pageState.paginationEnabled === paginationEnabled.value
    && (
      !paginationEnabled.value
      || (
    pageState.page === currentPage.value
    && pageState.pageSize === pageSize.value
      )
    )
  )
}

function readDraftPayload(): SheetDraftPayload | null {
  if (typeof window === 'undefined' || props.sheetId == null) {
    return null
  }

  try {
    const raw = window.localStorage.getItem(storageKey.value)
    if (!raw) {
      return null
    }
    const payload = JSON.parse(raw) as Record<string, unknown>
    return {
      version: 1,
      updatedAt: normalizeTimestampMs(payload.updatedAt),
      sheetVersion: payload.sheetVersion == null ? null : Number(payload.sheetVersion),
      title: String(payload.title ?? '').trim() || '未命名表格',
      document: normalizeSheetDocument(payload.document),
      pageState: normalizeDraftPageState(payload.pageState),
    }
  } catch (error) {
    console.warn('Failed to restore note sheet draft', error)
    window.localStorage.removeItem(storageKey.value)
    return null
  }
}

function persistDraftDocument(document: SheetDocument) {
  if (typeof window === 'undefined' || props.sheetId == null) {
    return
  }

  const payload: SheetDraftPayload = {
    version: 1,
    updatedAt: Date.now(),
    sheetVersion: sheetVersion.value || null,
    title: sheetTitle.value.trim() || '未命名表格',
    document,
    pageState: buildCurrentPagePatchState(),
  }
  window.localStorage.setItem(storageKey.value, JSON.stringify(payload))
}

function clearDraftStorage() {
  if (typeof window === 'undefined' || props.sheetId == null) {
    return
  }
  window.localStorage.removeItem(storageKey.value)
}

function clearEditingColumnState() {
  editingColumnIndex.value = null
  editingColumnTitle.value = ''
  editingHeaderInputEl = null
}

function getHotInstance() {
  return hotTableRef.value?.hotInstance ?? null
}

function getUndoRedoPlugin() {
  return getHotInstance()?.getPlugin('undoRedo') as {
    isUndoAvailable?: () => boolean
    isRedoAvailable?: () => boolean
    undo?: () => void
    redo?: () => void
  } | null
}

async function refreshComputedRowHeights() {
  const hot = getHotInstance()
  if (!hot) {
    return
  }

  hot.updateSettings({
    cells: resolveCellMeta,
    rowHeights: resolveRowHeight,
  })
  await nextTick()
  hot.render()
  void updateSheetViewportHeight()
}

function hasSelection() {
  return !!getHotInstance()?.getSelectedLast()
}

function isSelectedByColumnHeader() {
  return !!getHotInstance()?.selection?.isSelectedByColumnHeader?.()
}

function isSelectedByRowHeader() {
  return !!getHotInstance()?.selection?.isSelectedByRowHeader?.()
}

function isSelectedByCorner() {
  return !!getHotInstance()?.selection?.isSelectedByCorner?.()
}

function shouldShowRowActions() {
  return hasSelection() && (!isSelectedByColumnHeader() || isSelectedByCorner())
}

function shouldShowColumnActions() {
  return hasSelection() && (!isSelectedByRowHeader() || isSelectedByCorner())
}

function shouldShowUndoAction() {
  return !!getUndoRedoPlugin()?.isUndoAvailable?.()
}

function shouldShowRedoAction() {
  return !!getUndoRedoPlugin()?.isRedoAvailable?.()
}

function getMainScrollContainer() {
  return sheetFrameRef.value?.closest('.page-shell-main') as HTMLElement | null
}

function getTextMeasureContext() {
  if (textMeasureContext) {
    return textMeasureContext
  }

  if (typeof document === 'undefined') {
    return null
  }

  const canvas = document.createElement('canvas')
  textMeasureContext = canvas.getContext('2d')
  if (textMeasureContext) {
    textMeasureContext.font = TABLE_FONT
  }
  return textMeasureContext
}

function measureTextWidth(text: string, font = TABLE_FONT) {
  if (!text) {
    return 0
  }

  const cacheKey = `${font}::${text}`
  const cached = textMeasureCache.get(cacheKey)
  if (cached != null) {
    return cached
  }

  const context = getTextMeasureContext()
  if (context) {
    context.font = font
  }
  const width = context ? context.measureText(text).width : text.length * 14
  textMeasureCache.set(cacheKey, width)
  return width
}

function getAdaptiveColumnWidth(header: string) {
  const width = Math.ceil(measureTextWidth(header.trim() || '列', TABLE_HEADER_FONT)) + HEADER_WIDTH_PADDING
  return Math.min(Math.max(width, MIN_COLUMN_WIDTH), MAX_COLUMN_WIDTH)
}

function normalizeColumnWidthValue(value: unknown) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return MIN_COLUMN_WIDTH
  }
  return Math.min(Math.max(Math.round(numeric), MIN_COLUMN_WIDTH), MAX_COLUMN_WIDTH)
}

function getAutoColumnWidth(columnIndex: number, headers = columnHeaders.value, sourceRows = rows.value) {
  const header = headers[columnIndex] ?? createFallbackHeader(columnIndex)
  let width = getAdaptiveColumnWidth(header)
  const sampleLimit = Math.min(sourceRows.length, 40)

  for (let rowIndex = 0; rowIndex < sampleLimit; rowIndex += 1) {
    const cellText = normalizeCellValue(sourceRows[rowIndex]?.[columnIndex] ?? '').trim()
    if (!cellText) {
      continue
    }
    width = Math.max(
      width,
      Math.min(
        Math.ceil(measureTextWidth(cellText, TABLE_FONT)) + HEADER_WIDTH_PADDING,
        MAX_COLUMN_WIDTH,
      ),
    )
  }

  return Math.min(Math.max(width, MIN_COLUMN_WIDTH), MAX_COLUMN_WIDTH)
}

function getEditingColumnInputWidth(title: string) {
  const width = Math.ceil(measureTextWidth(title || ' ', TABLE_HEADER_FONT)) + INLINE_HEADER_INPUT_PADDING
  return `${Math.min(Math.max(width, 32), MAX_COLUMN_WIDTH)}px`
}

function getEffectiveColumnWidth(columnIndex: number) {
  const hot = getHotInstance()
  const liveWidth = hot?.getColWidth(columnIndex)
  if (typeof liveWidth === 'number' && Number.isFinite(liveWidth) && liveWidth > 0) {
    return liveWidth
  }

  return columnWidths.value[columnIndex] ?? MIN_COLUMN_WIDTH
}

function getWrappedLineCount(text: string, availableWidth: number) {
  if (!text) {
    return 1
  }

  if (!Number.isFinite(availableWidth) || availableWidth <= 0) {
    return 1
  }

  return text
    .split(/\r?\n/)
    .reduce((sum, segment) => {
      if (!segment) {
        return sum + 1
      }

      const normalizedSegment = segment.trim()
      if (!normalizedSegment) {
        return sum + 1
      }

      const containsCjk = /[\u3400-\u9FFF\uF900-\uFAFF]/.test(normalizedSegment)
      const containsWrapSeparator = /\s/.test(normalizedSegment)
      if (!containsCjk && !containsWrapSeparator) {
        return sum + 1
      }

      return sum + Math.max(1, Math.ceil(measureTextWidth(segment) / availableWidth))
    }, 0)
}

function resolveRowHeight(rowIndex: number) {
  const row = rows.value[rowIndex] ?? []
  let maxLines = 1

  for (let columnIndex = 0; columnIndex < columnHeaders.value.length; columnIndex += 1) {
    if (columnConfigs.value[columnHeaders.value[columnIndex] ?? '']?.hidden === true) {
      continue
    }
    if (getColumnDisplayMode(columnIndex) === 'single_line') {
      continue
    }

    const cellText = normalizeCellValue(row[columnIndex] ?? '')
    if (!cellText) {
      continue
    }

    const availableWidth = Math.max(
      getEffectiveColumnWidth(columnIndex) - TABLE_CELL_HORIZONTAL_PADDING * 2,
      12,
    )
    maxLines = Math.max(maxLines, getWrappedLineCount(cellText, availableWidth))
  }

  return DEFAULT_TABLE_ROW_HEIGHT + (maxLines - 1) * TABLE_LINE_HEIGHT
}

async function updateSheetViewportHeight() {
  await nextTick()

  const sheetFrame = sheetFrameRef.value
  if (!sheetFrame || props.sheetId == null) {
    sheetViewportHeight.value = 'auto'
    return
  }

  const frameRect = sheetFrame.getBoundingClientRect()
  const availableHeight = Math.floor(sheetFrame.clientHeight || frameRect.height)
  if (!Number.isFinite(availableHeight) || availableHeight <= 0) {
    return
  }

  sheetViewportHeight.value = availableHeight

  getHotInstance()?.render()
}

function handleWindowResize() {
  closeColumnNotePopover()
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

function refreshGridStructure() {
  const hot = getHotInstance()
  if (!hot) {
    return
  }

  hot.updateSettings({
    colHeaders: [...columnHeaders.value],
    nestedHeaders: nestedHeaders.value,
    colWidths: [...columnWidths.value],
    rowHeaders: sheetViewSettings.value.show_row_numbers,
    hiddenColumns: {
      columns: [...hiddenColumnIndexes.value],
      indicators: false,
    },
  })
  hot.render()
}

function normalizeMovedColumnIndexes(movedColumns: number[]) {
  return Array.from(new Set(
    movedColumns
      .filter((index) => Number.isInteger(index) && index >= 0 && index < columnHeaders.value.length),
  )).sort((left, right) => left - right)
}

function normalizeMovedRowIndexes(movedRows: number[]) {
  return Array.from(new Set(
    movedRows
      .filter((index) => Number.isInteger(index) && index >= 0 && index < rows.value.length),
  )).sort((left, right) => left - right)
}

function countMoveFinalIndex(movedColumns: number[], dropIndex: number | undefined, fallbackFinalIndex: number) {
  if (dropIndex == null) {
    return fallbackFinalIndex
  }

  let lowerThanDropIndex = 0
  for (const columnIndex of movedColumns) {
    if (columnIndex < dropIndex) {
      lowerThanDropIndex += 1
    }
  }

  return dropIndex - lowerThanDropIndex
}

function getEffectiveMovedColumnsForDrag(movedColumns: number[]) {
  const normalizedMovedColumns = normalizeMovedColumnIndexes(movedColumns)
  const bounds = getSelectionColumnBounds()

  if (!bounds || bounds.start === bounds.end || isSelectedByCorner()) {
    return normalizedMovedColumns
  }

  if (!normalizedMovedColumns.length) {
    return normalizedMovedColumns
  }

  const selectionContainsDraggedColumn = normalizedMovedColumns.every((columnIndex) => (
    columnIndex >= bounds.start && columnIndex <= bounds.end
  ))
  if (!selectionContainsDraggedColumn) {
    return normalizedMovedColumns
  }

  return Array.from({ length: bounds.end - bounds.start + 1 }, (_, offset) => bounds.start + offset)
}

function getEffectiveMovedRowsForDrag(movedRows: number[]) {
  const normalizedMovedRows = normalizeMovedRowIndexes(movedRows)
  const bounds = getSelectionRowBounds()

  if (!bounds || bounds.start === bounds.end || isSelectedByCorner()) {
    return normalizedMovedRows
  }

  if (!normalizedMovedRows.length) {
    return normalizedMovedRows
  }

  const selectionContainsDraggedRow = normalizedMovedRows.every((rowIndex) => (
    rowIndex >= bounds.start && rowIndex <= bounds.end
  ))
  if (!selectionContainsDraggedRow) {
    return normalizedMovedRows
  }

  return Array.from({ length: bounds.end - bounds.start + 1 }, (_, offset) => bounds.start + offset)
}

function reorderItemsByMove<T>(items: T[], movedIndexes: number[], finalIndex: number) {
  if (!items.length || !movedIndexes.length) {
    return [...items]
  }

  const normalizedIndexes = Array.from(new Set(
    movedIndexes
      .filter((index) => Number.isInteger(index) && index >= 0 && index < items.length),
  )).sort((left, right) => left - right)

  if (!normalizedIndexes.length) {
    return [...items]
  }

  const movedIndexSet = new Set(normalizedIndexes)
  const movedItems = normalizedIndexes.map((index) => items[index] as T)
  const remainingItems = items.filter((_, index) => !movedIndexSet.has(index))
  const insertionIndex = Math.min(Math.max(finalIndex, 0), remainingItems.length)

  remainingItems.splice(insertionIndex, 0, ...movedItems)
  return remainingItems
}

function handleBeforeColumnMove(
  movedColumns: number[],
  finalIndex: number,
  dropIndex: number | undefined,
  movePossible: boolean,
) {
  const effectiveMovedColumns = getEffectiveMovedColumnsForDrag(movedColumns)
  const effectiveFinalIndex = countMoveFinalIndex(effectiveMovedColumns, dropIndex, finalIndex)
  const effectiveMovePossible = (
    movePossible
    && effectiveMovedColumns.length > 0
    && effectiveFinalIndex >= 0
    && effectiveFinalIndex + effectiveMovedColumns.length <= columnHeaders.value.length
  )

  if (!effectiveMovePossible) {
    return false
  }

  const nextHeaders = reorderItemsByMove(columnHeaders.value, effectiveMovedColumns, effectiveFinalIndex)
  const nextWidths = reorderItemsByMove(columnWidths.value, effectiveMovedColumns, effectiveFinalIndex)
  const nextRows = rows.value.map((row) => reorderItemsByMove(
    normalizeRow(row, columnHeaders.value),
    effectiveMovedColumns,
    effectiveFinalIndex,
  ))

  const headersChanged = nextHeaders.some((header, index) => header !== columnHeaders.value[index])
  if (!headersChanged) {
    return false
  }

  clearEditingColumnState()
  columnHeaders.value = nextHeaders
  columnWidths.value = nextWidths
  rows.value = nextRows
  columnConfigs.value = normalizeColumnConfigs(columnConfigs.value, nextHeaders)
  const movedRangeStart = effectiveFinalIndex
  const movedRangeEnd = effectiveFinalIndex + effectiveMovedColumns.length - 1
  const hot = getHotInstance()
  if (hot) {
    hot.updateSettings({
      data: nextRows,
      colHeaders: [...nextHeaders],
      nestedHeaders: nestedHeaders.value,
      colWidths: [...nextWidths],
    })
    hot.render()
    void nextTick(() => {
      hot.selectColumns(movedRangeStart, movedRangeEnd)
    })
  } else {
    refreshGridStructure()
  }
  void refreshComputedRowHeights()

  return false
}

function handleBeforeRowMove(
  movedRows: number[],
  finalIndex: number,
  dropIndex: number | undefined,
  movePossible: boolean,
) {
  const effectiveMovedRows = getEffectiveMovedRowsForDrag(movedRows)
  const effectiveFinalIndex = countMoveFinalIndex(effectiveMovedRows, dropIndex, finalIndex)
  const effectiveMovePossible = (
    movePossible
    && effectiveMovedRows.length > 0
    && effectiveFinalIndex >= 0
    && effectiveFinalIndex + effectiveMovedRows.length <= rows.value.length
  )

  if (!effectiveMovePossible) {
    return false
  }

  const nextRows = reorderItemsByMove(rows.value, effectiveMovedRows, effectiveFinalIndex)
  const rowsChanged = nextRows.some((row, index) => row !== rows.value[index])
  if (!rowsChanged) {
    return false
  }

  clearEditingColumnState()
  rows.value = nextRows
  const movedRangeStart = effectiveFinalIndex
  const movedRangeEnd = effectiveFinalIndex + effectiveMovedRows.length - 1
  const hot = getHotInstance()
  if (hot) {
    hot.updateSettings({
      data: nextRows,
    })
    hot.render()
    void nextTick(() => {
      hot.selectRows(movedRangeStart, movedRangeEnd)
    })
  }
  void refreshComputedRowHeights()

  return false
}

function focusEditingColumnInput(select = true) {
  void nextTick(() => {
    const input = editingHeaderInputEl
    if (!input) {
      return
    }
    input.focus()
    if (select) {
      input.select()
    }
  })
}

function startInlineRenameColumn(columnIndex: number) {
  if (columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return
  }

  editingColumnIndex.value = columnIndex
  editingColumnTitle.value = columnHeaders.value[columnIndex] ?? createFallbackHeader(columnIndex)
  refreshGridStructure()
  focusEditingColumnInput()
}

function commitInlineRenameColumn() {
  const columnIndex = editingColumnIndex.value
  if (columnIndex == null || columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    clearEditingColumnState()
    return
  }

  const currentHeader = columnHeaders.value[columnIndex] ?? createFallbackHeader(columnIndex)
  const nextHeader = editingColumnTitle.value.trim()

  if (!nextHeader) {
    ElMessage.warning('字段名不能为空')
    focusEditingColumnInput()
    return
  }

  if (columnHeaders.value.some((header, index) => index !== columnIndex && header === nextHeader)) {
    ElMessage.warning('字段名不能重复')
    focusEditingColumnInput()
    return
  }

  if (nextHeader === currentHeader) {
    clearEditingColumnState()
    refreshGridStructure()
    return
  }

  const nextHeaders = [...columnHeaders.value]
  nextHeaders[columnIndex] = nextHeader
  columnHeaders.value = nextHeaders
  const nextWidths = [...columnWidths.value]
  nextWidths[columnIndex] = Math.max(nextWidths[columnIndex] ?? MIN_COLUMN_WIDTH, getAdaptiveColumnWidth(nextHeader))
  columnWidths.value = nextWidths

  const nextConfigs = { ...columnConfigs.value }
  const currentConfig = nextConfigs[currentHeader]
  delete nextConfigs[currentHeader]
  if (currentConfig) {
    nextConfigs[nextHeader] = { ...currentConfig }
  }
  columnConfigs.value = nextConfigs

  clearEditingColumnState()
  refreshGridStructure()
}

function cancelInlineRenameColumn() {
  clearEditingColumnState()
  refreshGridStructure()
}

function handleHeaderMouseDown(event: MouseEvent, coords: { row: number; col: number }) {
  if (coords.col < 0 || coords.row !== -1 || event.detail < 2) {
    return
  }

  startInlineRenameColumn(coords.col)
}

function handleAfterGetColHeader(column: number, th: HTMLTableHeaderCellElement, headerLevel: number) {
  const editableHeaderLevel = nestedHeaders.value.length - 1

  if (
    column < 0
    || !sheetFrameRef.value
    || th.classList.contains('sheet-col-marker')
    || headerLevel !== editableHeaderLevel
  ) {
    return
  }

  const headerContent = th.querySelector('.colHeader') as HTMLElement | null
  if (!headerContent) {
    return
  }

  const headerTitle = columnHeaders.value[column] ?? createFallbackHeader(column)
  if (editingColumnIndex.value !== column) {
    if (editingHeaderInputEl && editingHeaderInputEl.closest('th') === th) {
      editingHeaderInputEl = null
    }
    headerContent.textContent = ''
    const label = document.createElement('span')
    label.className = 'sheet-header-label'

    const titleEl = document.createElement('span')
    titleEl.className = 'sheet-header-title'
    titleEl.textContent = headerTitle
    const note = getColumnNote(column)
    if (note) {
      titleEl.title = note
      titleEl.setAttribute('aria-label', note)
    }
    label.appendChild(titleEl)

    headerContent.appendChild(label)
    return
  }

  const currentInput = headerContent.querySelector('.sheet-header-rename-input') as HTMLInputElement | null
  if (currentInput) {
    currentInput.value = editingColumnTitle.value
    currentInput.style.width = getEditingColumnInputWidth(editingColumnTitle.value)
    editingHeaderInputEl = currentInput
    return
  }

  headerContent.textContent = ''
  const input = document.createElement('input')
  input.className = 'sheet-header-rename-input'
  input.value = editingColumnTitle.value
  input.style.width = getEditingColumnInputWidth(editingColumnTitle.value)
  input.setAttribute('maxlength', '120')
  input.addEventListener('input', () => {
    editingColumnTitle.value = input.value
    input.style.width = getEditingColumnInputWidth(editingColumnTitle.value)
  })
  input.addEventListener('click', (event) => event.stopPropagation())
  input.addEventListener('mousedown', (event) => event.stopPropagation())
  input.addEventListener('dblclick', (event) => event.stopPropagation())
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      commitInlineRenameColumn()
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      cancelInlineRenameColumn()
    }
  })
  input.addEventListener('blur', () => {
    commitInlineRenameColumn()
  })
  headerContent.appendChild(input)
  editingHeaderInputEl = input
  focusEditingColumnInput()
}

function getColumnMarkerLabel(index: number) {
  return sheetViewSettings.value.column_marker_style === 'numbers'
    ? String(index + 1)
    : getExcelColumnLabel(index)
}

function areSheetViewSettingsEqual(left: SheetViewSettings, right: SheetViewSettings) {
  const normalizedLeft = normalizeSheetViewSettings(left)
  const normalizedRight = normalizeSheetViewSettings(right)
  return (
    normalizedLeft.show_row_numbers === normalizedRight.show_row_numbers
    && normalizedLeft.show_column_markers === normalizedRight.show_column_markers
    && normalizedLeft.column_marker_style === normalizedRight.column_marker_style
    && normalizedLeft.pagination.enabled === normalizedRight.pagination.enabled
    && normalizedLeft.pagination.page_size === normalizedRight.pagination.page_size
  )
}

function openSheetSettings() {
  if (props.sheetId == null) {
    return
  }
  sheetSettingsDraft.value = {
    ...sheetViewSettings.value,
    pagination: { ...sheetViewSettings.value.pagination },
  }
  sheetSettingsDialogVisible.value = true
}

function closeSheetSettings() {
  sheetSettingsDialogVisible.value = false
}

async function applySheetSettings() {
  const nextSettings = normalizeSheetViewSettings(sheetSettingsDraft.value)
  closeSheetSettings()

  if (areSheetViewSettingsEqual(sheetViewSettings.value, nextSettings)) {
    return
  }

  const previousSettings = {
    ...sheetViewSettings.value,
    pagination: { ...sheetViewSettings.value.pagination },
  }

  const paginationChanged = (
    sheetViewSettings.value.pagination.enabled !== nextSettings.pagination.enabled
    || sheetViewSettings.value.pagination.page_size !== nextSettings.pagination.page_size
  )

  sheetViewSettings.value = nextSettings
  refreshGridStructure()
  await refreshComputedRowHeights()
  await updateSheetViewportHeight()

  if (paginationChanged) {
    try {
      const saved = await saveDocumentSnapshot(buildCurrentDocument())
      if (saved) {
        sheetTitle.value = saved.title || sheetTitle.value
        sheetVersion.value = Number(saved.version || 1)
        emitSheetSync(saved)
        clearDraftStorage()
      }
    } catch (error) {
      console.warn('Failed to save sheet settings', error)
      sheetViewSettings.value = previousSettings
      refreshGridStructure()
      await refreshComputedRowHeights()
      await updateSheetViewportHeight()
      ElMessage.error('保存表格设置失败')
      return
    }
    currentPage.value = 1
    pageSize.value = nextSettings.pagination.page_size
    await restoreInitialDocument()
    void updateSheetViewportHeight()
    return
  }

  scheduleRemoteSave()
}

function loadSheetDocument(document: SheetDocument) {
  clearEditingColumnState()
  closeColumnNotePopover()
  columnSettingsDialogVisible.value = false
  columnSettingsColumnIndex.value = null
  const normalizedHeaders = normalizeHeaders(document.columns)
  const normalizedRows = document.rows.length
    ? document.rows.map((row) => normalizeRow(row, normalizedHeaders))
    : [createEmptyRow(normalizedHeaders.length)]

  columnHeaders.value = normalizedHeaders
  columnConfigs.value = normalizeColumnConfigs(document.column_configs, normalizedHeaders)
  sheetViewSettings.value = normalizeSheetViewSettings(document.view_settings)
  pageSize.value = sheetViewSettings.value.pagination.page_size
  columnWidths.value = document.column_widths?.length
    ? document.column_widths.slice(0, normalizedHeaders.length)
    : normalizedHeaders.map((_, index) => getAutoColumnWidth(index, normalizedHeaders, normalizedRows))
  rows.value = normalizedRows

  const hot = getHotInstance()
  if (hot) {
    hot.updateSettings({
      data: normalizedRows,
      colHeaders: [...normalizedHeaders],
      nestedHeaders: nestedHeaders.value,
      colWidths: [...columnWidths.value],
      rowHeaders: sheetViewSettings.value.show_row_numbers,
      hiddenColumns: {
        columns: [...hiddenColumnIndexes.value],
        indicators: false,
      },
      cells: resolveCellMeta,
      rowHeights: resolveRowHeight,
    })
    hot.render()
    void refreshComputedRowHeights()
  }
}

function clearSaveTimer() {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
}

function applyPaginationState(pagination?: NoteSheetPaginationState | null) {
  if (!pagination) {
    currentPage.value = 1
    pageCount.value = 1
    totalRowCount.value = trimTrailingBlankRows(rows.value.map((row) => normalizeRow(row, columnHeaders.value))).length
    pageRowOffset.value = 0
    pageLoadedRowCount.value = totalRowCount.value
    return
  }

  currentPage.value = normalizePositivePageNumber(pagination.page, currentPage.value)
  pageSize.value = normalizePositivePageNumber(pagination.page_size, pageSize.value)
  pageCount.value = normalizePositivePageNumber(pagination.page_count, 1)
  totalRowCount.value = normalizeNonNegativeInt(pagination.total_rows)
  pageRowOffset.value = normalizeNonNegativeInt(pagination.row_offset)
  pageLoadedRowCount.value = normalizeNonNegativeInt(pagination.loaded_row_count)
}

function shouldUsePagedPatchForSave() {
  return pageLoadedRowCount.value < totalRowCount.value
}

function buildUpdatePagePatch() {
  if (!shouldUsePagedPatchForSave()) {
    return undefined
  }

  return {
    page: currentPage.value,
    page_size: pageSize.value,
    row_offset: pageRowOffset.value,
    loaded_row_count: pageLoadedRowCount.value,
  }
}

async function saveDocumentSnapshot(document: SheetDocument) {
  if (props.sheetId == null) {
    return null
  }

  return updateNoteSheet(props.sheetId, {
    title: sheetTitle.value.trim() || '未命名表格',
    document_json: document,
    page_patch: buildUpdatePagePatch(),
  })
}

function emitSheetSync(detail: NoteSheetDetail) {
  emit('sheetSync', {
    id: detail.id,
    title: detail.title || '未命名表格',
    version: Number(detail.version || 1),
    updatedAt: Number(detail.updated_at || 0),
  })
}

function applyRemoteSheetDetail(detail: NoteSheetDetail) {
  suppressPersistence = true
  try {
    sheetTitle.value = detail.title || '未命名表格'
    sheetVersion.value = Number(detail.version || 1)
    applyPaginationState(detail.pagination)
    emitSheetSync(detail)
    loadSheetDocument(normalizeSheetDocument(detail.document_json))
    changeSerial = 0
    lastQueuedSerial = 0
    clearDraftStorage()
  } finally {
    suppressPersistence = false
  }
}

async function flushRemoteSave() {
  if (saveInFlight || suppressPersistence || props.sheetId == null) {
    return
  }

  clearSaveTimer()
  saveInFlight = true
  const serial = lastQueuedSerial
  const document = buildCurrentDocument()

  try {
    const saved = await saveDocumentSnapshot(document)
    if (!saved) {
      return
    }
    sheetTitle.value = saved.title || sheetTitle.value
    sheetVersion.value = Number(saved.version || 1)
    applyPaginationState(saved.pagination)
    emitSheetSync(saved)

    if (serial === changeSerial) {
      clearDraftStorage()
    } else {
      persistDraftDocument(buildCurrentDocument())
    }
  } catch (error) {
    console.warn('Failed to save note sheet', error)
    persistDraftDocument(document)
  } finally {
    saveInFlight = false
    if (lastQueuedSerial > serial) {
      void flushRemoteSave()
    }
  }
}

function scheduleRemoteSave(delayMs = REMOTE_SAVE_DEBOUNCE_MS) {
  if (suppressPersistence || props.sheetId == null) {
    return
  }

  persistDraftDocument(buildCurrentDocument())
  changeSerial += 1
  lastQueuedSerial = changeSerial
  clearSaveTimer()
  saveTimer = setTimeout(() => {
    void flushRemoteSave()
  }, Math.max(0, delayMs))
}

async function sortColumn(columnIndex: number, direction: SortDirection) {
  if (props.sheetId == null) {
    return
  }

  workspaceLoading.value = true
  try {
    await flushRemoteSave()
    const detail = await sortNoteSheet(props.sheetId, {
      column_index: columnIndex,
      direction,
    })
    applyRemoteSheetDetail(detail)
    void updateSheetViewportHeight()
  } catch (error) {
    console.warn('Failed to sort note sheet', error)
    ElMessage.error('排序失败')
  } finally {
    workspaceLoading.value = false
  }
}

async function handleSelectedColumnSort(direction: SortDirection) {
  const columnIndex = getSingleSelectedColumnIndex()
  if (columnIndex == null) {
    return
  }

  await sortColumn(columnIndex, direction)
}

function resolveFetchPaginationPreference(localDraft: SheetDraftPayload | null) {
  if (localDraft) {
    const localSettings = normalizeSheetViewSettings(localDraft.document.view_settings)
    return {
      paginate: localSettings.pagination.enabled,
      pageSize: localSettings.pagination.page_size,
    }
  }

  if (sheetVersion.value > 0) {
    return {
      paginate: sheetViewSettings.value.pagination.enabled,
      pageSize: sheetViewSettings.value.pagination.page_size,
    }
  }

  return null
}

async function restoreInitialDocument() {
  if (props.sheetId == null) {
    return
  }

  workspaceLoading.value = true
  suppressPersistence = true
  try {
    const localDraft = readDraftPayload()
    const paginationPreference = resolveFetchPaginationPreference(localDraft)
    let remote = await fetchNoteSheet(props.sheetId, paginationPreference
      ? {
        page: paginationPreference.paginate ? currentPage.value : undefined,
        pageSize: paginationPreference.paginate ? paginationPreference.pageSize : undefined,
        paginate: paginationPreference.paginate,
      }
      : undefined)
    if (!remote) {
      emit('missing', props.sheetId)
      return
    }

    let remoteDocument = normalizeSheetDocument(remote.document_json)
    const remoteSettings = normalizeSheetViewSettings(remoteDocument.view_settings)
    if (
      paginationPreference
      && paginationPreference.paginate !== remoteSettings.pagination.enabled
    ) {
      remote = await fetchNoteSheet(props.sheetId, remoteSettings.pagination.enabled
        ? {
          page: currentPage.value,
          pageSize: remoteSettings.pagination.page_size,
          paginate: true,
        }
        : {
          paginate: false,
        })
      if (!remote) {
        emit('missing', props.sheetId)
        return
      }
      remoteDocument = normalizeSheetDocument(remote.document_json)
    }

    sheetTitle.value = remote.title || '未命名表格'
    sheetVersion.value = Number(remote.version || 1)
    applyPaginationState(remote.pagination)
    emitSheetSync(remote)

    let activeDocument = remoteDocument
    let shouldSyncLocalDraft = false

    if (localDraft?.document && isSamePagePatchState(localDraft.pageState)) {
      const remoteUpdatedAt = normalizeTimestampMs(remote.updated_at)
      if (localDraft.updatedAt > remoteUpdatedAt) {
        activeDocument = localDraft.document
        sheetTitle.value = localDraft.title || sheetTitle.value
        if (localDraft.pageState) {
          pageRowOffset.value = localDraft.pageState.rowOffset
          pageLoadedRowCount.value = localDraft.pageState.loadedRowCount
        }
        shouldSyncLocalDraft = true
      } else {
        clearDraftStorage()
      }
    }

    loadSheetDocument(activeDocument)
    changeSerial = 0
    lastQueuedSerial = 0
    suppressPersistence = false

    if (shouldSyncLocalDraft) {
      scheduleRemoteSave(0)
    }
  } finally {
    suppressPersistence = false
    workspaceLoading.value = false
  }
}

async function handlePageChange(nextPage: number) {
  if (!paginationEnabled.value) {
    return
  }

  const normalizedPage = normalizePositivePageNumber(nextPage, currentPage.value)
  if (normalizedPage === currentPage.value || props.sheetId == null || workspaceLoading.value) {
    return
  }

  await flushRemoteSave()
  currentPage.value = normalizedPage
  await restoreInitialDocument()
  void updateSheetViewportHeight()
}

function handleAfterChange(_changes: unknown, source?: string) {
  if (source === 'loadData' || source === 'external-update') {
    return
  }
  syncRowsFromGrid()
}

function shouldTrimWhitespaceForColumn(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return true
  }
  return normalizeColumnConfig(columnConfigs.value[header]).trim_whitespace
}

function handleBeforeChange(changes: unknown, source?: string) {
  if (
    !Array.isArray(changes)
    || source === 'loadData'
    || source === 'external-update'
  ) {
    return
  }

  for (const change of changes) {
    if (!Array.isArray(change) || change.length < 4) {
      continue
    }

    const columnIndex = Number(change[1])
    if (!Number.isInteger(columnIndex) || columnIndex < 0 || !shouldTrimWhitespaceForColumn(columnIndex)) {
      continue
    }

    const nextValue = change[3]
    if (typeof nextValue === 'string') {
      change[3] = nextValue.trim()
    }
  }
}

function handleAfterCreateRow() {
  syncRowsFromGrid()
}

function handleAfterRemoveRow() {
  syncRowsFromGrid()
}

function handleAfterCreateCol(index: number, amount: number) {
  const nextHeaders = [...columnHeaders.value]
  nextHeaders.splice(index, 0, ...createCustomColumnNames(amount, nextHeaders))
  columnHeaders.value = nextHeaders
  const nextWidths = [...columnWidths.value]
  nextWidths.splice(
    index,
    0,
    ...Array.from({ length: amount }, (_, offset) => getAdaptiveColumnWidth(nextHeaders[index + offset] ?? createFallbackHeader(index + offset))),
  )
  columnWidths.value = nextWidths
  columnConfigs.value = normalizeColumnConfigs(columnConfigs.value, nextHeaders)
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
  const nextWidths = [...columnWidths.value]
  nextWidths.splice(index, amount)
  columnWidths.value = columnHeaders.value.length
    ? nextWidths.slice(0, columnHeaders.value.length)
    : [getAdaptiveColumnWidth(columnHeaders.value[0])]
  columnConfigs.value = normalizeColumnConfigs(columnConfigs.value, columnHeaders.value)
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

function getSelectionRowBounds() {
  const hot = getHotInstance()
  const selection = hot?.getSelectedLast()
  if (!selection) {
    return null
  }

  const startRow = Math.min(selection[0], selection[2])
  const endRow = Math.max(selection[0], selection[2])
  if (endRow < 0) {
    return null
  }

  return {
    start: Math.max(startRow, 0),
    end: endRow,
  }
}

function hasSingleColumnSelection() {
  const bounds = getSelectionColumnBounds()
  return !!bounds && bounds.start === bounds.end
}

function hasSingleColumnHeaderSelection() {
  return isSelectedByColumnHeader() && hasSingleColumnSelection()
}

function getSingleSelectedColumnIndex() {
  const bounds = getSelectionColumnBounds()
  if (!bounds || bounds.start !== bounds.end || !isSelectedByColumnHeader()) {
    return null
  }
  return bounds.start
}

function getColumnDisplayMode(columnIndex: number): ColumnDisplayMode {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return 'wrap'
  }
  return normalizeColumnDisplayMode(columnConfigs.value[header]?.display_mode)
}

function isColumnValueValidByType(value: string, type: ColumnValueType, allowEmpty = true) {
  if (!value) {
    return allowEmpty
  }

  switch (type) {
    case 'number':
      return /^[-+]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(value)
    case 'phone':
      return /^\d{11}$/.test(value)
    default:
      return true
  }
}

function getColumnNote(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return ''
  }
  return normalizeColumnNote(columnConfigs.value[header]?.note)
}

function getColumnSettingsTitle() {
  const columnIndex = columnSettingsColumnIndex.value
  if (columnIndex == null) {
    return '字段设置'
  }

  const header = columnHeaders.value[columnIndex]
  return header ? `字段设置：${header}` : '字段设置'
}

function openColumnSettings(columnIndex: number) {
  if (columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return
  }

  const header = columnHeaders.value[columnIndex]
  columnSettingsColumnIndex.value = columnIndex
  const nextDraft = normalizeColumnConfig(header ? columnConfigs.value[header] : null)
  nextDraft.width_value = normalizeColumnWidthValue(getEffectiveColumnWidth(columnIndex))
  columnSettingsDraft.value = nextDraft
  columnSettingsDialogVisible.value = true
}

function openSelectedColumnSettings() {
  const columnIndex = getSingleSelectedColumnIndex()
  if (columnIndex == null) {
    return
  }
  openColumnSettings(columnIndex)
}

function closeColumnSettings() {
  columnSettingsDialogVisible.value = false
}

function applyColumnSettings() {
  const columnIndex = columnSettingsColumnIndex.value
  if (columnIndex == null || columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    closeColumnSettings()
    return
  }

  const header = columnHeaders.value[columnIndex]
  if (!header) {
    closeColumnSettings()
    return
  }

  const nextConfig = normalizeColumnConfig(columnSettingsDraft.value)
  const currentConfig = normalizeColumnConfig(columnConfigs.value[header])
  const currentWidthValue = normalizeColumnWidthValue(columnWidths.value[columnIndex] ?? getEffectiveColumnWidth(columnIndex))
  const nextWidthValue = normalizeColumnWidthValue(columnSettingsDraft.value.width_value)
  closeColumnSettings()
  closeColumnNotePopover()

  if (
    nextConfig.value_type === currentConfig.value_type
    && nextConfig.allow_empty === currentConfig.allow_empty
    && nextConfig.display_mode === currentConfig.display_mode
    && nextConfig.trim_whitespace === currentConfig.trim_whitespace
    && nextConfig.duplicate_value_highlight === currentConfig.duplicate_value_highlight
    && nextConfig.width_mode === currentConfig.width_mode
    && nextConfig.note === currentConfig.note
    && (nextConfig.width_mode !== 'fixed' || nextWidthValue === currentWidthValue)
  ) {
    return
  }

  const nextConfigs = { ...columnConfigs.value }
  if (
    nextConfig.value_type === 'text'
    && nextConfig.allow_empty
    && nextConfig.display_mode === 'wrap'
    && nextConfig.trim_whitespace
    && !nextConfig.duplicate_value_highlight
    && nextConfig.width_mode === 'adaptive'
    && !nextConfig.note
  ) {
    delete nextConfigs[header]
  } else {
    nextConfigs[header] = {}
    if (nextConfig.value_type !== 'text') {
      nextConfigs[header].value_type = nextConfig.value_type
    }
    if (!nextConfig.allow_empty) {
      nextConfigs[header].allow_empty = false
    }
    if (nextConfig.display_mode !== 'wrap') {
      nextConfigs[header].display_mode = nextConfig.display_mode
    }
    if (!nextConfig.trim_whitespace) {
      nextConfigs[header].trim_whitespace = false
    }
    if (nextConfig.duplicate_value_highlight) {
      nextConfigs[header].duplicate_value_highlight = true
    }
    if (nextConfig.width_mode !== 'adaptive') {
      nextConfigs[header].width_mode = nextConfig.width_mode
    }
    if (nextConfig.note) {
      nextConfigs[header].note = nextConfig.note
    }
  }

  columnConfigs.value = nextConfigs
  if (nextWidthValue !== currentWidthValue) {
    const nextWidths = [...columnWidths.value]
    nextWidths[columnIndex] = nextWidthValue
    columnWidths.value = nextWidths
  }
  refreshGridStructure()
  void refreshComputedRowHeights()
}

function shouldShowRemoveColumnAction() {
  return shouldShowColumnActions() && !!getSelectionColumnBounds() && columnHeaders.value.length > 1
}

function shouldShowRemoveRowAction() {
  const hot = getHotInstance()
  return shouldShowRowActions() && !!getSelectionRowBounds() && !!hot?.countSourceRows()
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

function insertRowFromSelection(side: 'above' | 'below') {
  const hot = getHotInstance()
  if (!hot) {
    return
  }

  const bounds = getSelectionRowBounds()
  const targetIndex = bounds
    ? (side === 'above' ? bounds.start : bounds.end + 1)
    : hot.countSourceRows()

  hot.alter('insert_row_above', targetIndex, 1)
}

function removeSelectedColumns() {
  const hot = getHotInstance()
  const bounds = getSelectionColumnBounds()
  if (!hot || !bounds || columnHeaders.value.length <= 1) {
    return
  }

  hot.alter('remove_col', bounds.start, bounds.end - bounds.start + 1)
}

function hideSelectedColumns() {
  const bounds = getSelectionColumnBounds()
  if (!bounds || !shouldShowHideColumnAction()) {
    return
  }

  const nextConfigs = normalizeColumnConfigs(columnConfigs.value, columnHeaders.value)
  for (let index = bounds.start; index <= bounds.end; index += 1) {
    const header = columnHeaders.value[index]
    if (!header) {
      continue
    }
    nextConfigs[header] = {
      ...(nextConfigs[header] ?? {}),
      hidden: true,
    }
    delete nextConfigs[header].restore_index
  }

  clearEditingColumnState()
  closeColumnNotePopover()
  columnConfigs.value = nextConfigs
  refreshGridStructure()
  void refreshComputedRowHeights()
}

function restoreHiddenColumn(header: string) {
  if (!header || columnConfigs.value[header]?.hidden !== true) {
    return
  }

  const currentIndex = columnHeaders.value.indexOf(header)
  if (currentIndex < 0) {
    return
  }

  const restoreIndex = normalizeNonNegativeInt(columnConfigs.value[header]?.restore_index, -1)
  let nextHeaders = [...columnHeaders.value]
  let nextWidths = [...columnWidths.value]
  let nextRows = rows.value.map((row) => normalizeRow(row, columnHeaders.value))

  if (restoreIndex >= 0 && currentIndex !== restoreIndex) {
    const visibleCount = getVisibleColumnCount()
    const targetIndex = Math.min(Math.max(restoreIndex, 0), visibleCount)
    nextHeaders = reorderItemsByMove(columnHeaders.value, [currentIndex], targetIndex)
    nextWidths = reorderItemsByMove(columnWidths.value, [currentIndex], targetIndex)
    nextRows = rows.value.map((row) => reorderItemsByMove(
      normalizeRow(row, columnHeaders.value),
      [currentIndex],
      targetIndex,
    ))
  }

  const nextConfigs = normalizeColumnConfigs(columnConfigs.value, nextHeaders)
  if (nextConfigs[header]) {
    delete nextConfigs[header].hidden
    delete nextConfigs[header].restore_index
    if (Object.keys(nextConfigs[header]).length === 0) {
      delete nextConfigs[header]
    }
  }

  clearEditingColumnState()
  closeColumnNotePopover()
  columnHeaders.value = nextHeaders
  columnWidths.value = nextWidths
  rows.value = nextRows
  columnConfigs.value = nextConfigs
  refreshGridStructure()
  void refreshComputedRowHeights()
  scheduleRemoteSave()
}

function removeSelectedRows() {
  const hot = getHotInstance()
  const bounds = getSelectionRowBounds()
  if (!hot || !bounds) {
    return
  }

  hot.alter('remove_row', bounds.start, bounds.end - bounds.start + 1)
}

function resolveCellMeta(_row: number, col: number) {
  if (col < 0) {
    return {
      wordWrap: true,
      textEllipsis: false,
    }
  }

  if (getColumnDisplayMode(col) === 'single_line') {
    return {
      wordWrap: false,
      textEllipsis: true,
    }
  }

  return {
    wordWrap: true,
    textEllipsis: false,
  }
}

function getCellAccentStyle(rowIndex: number, columnIndex: number) {
  const key = `${rowIndex}:${columnIndex}`
  const sourceColors = [
    duplicateHighlightMap.value.get(key),
    invalidValueHighlightMap.value.get(key),
  ].filter((color): color is string => !!color)

  if (!sourceColors.length) {
    return null
  }

  return mixCellAccentStyle(sourceColors)
}

function handleAfterRenderer(
  TD: HTMLTableCellElement,
  row: number,
  column: number,
  _prop: string | number,
  value: string,
) {
  TD.style.removeProperty('background-color')
  TD.style.removeProperty('color')

  if (column >= 0) {
    const accentStyle = getCellAccentStyle(row, column)
    if (accentStyle) {
      TD.style.backgroundColor = accentStyle.backgroundColor
    }
  }

  if (column < 0 || getColumnDisplayMode(column) !== 'single_line') {
    TD.removeAttribute('title')
    return
  }

  const fullText = normalizeCellValue(value)
  if (!fullText) {
    TD.removeAttribute('title')
    return
  }

  const ellipsisElement = TD.querySelector('.htTextEllipsis') as HTMLElement | null
  const targetElement = ellipsisElement ?? TD
  const isOverflowing = (
    targetElement.scrollWidth > targetElement.clientWidth + 1
    || targetElement.scrollHeight > targetElement.clientHeight + 1
  )

  if (isOverflowing) {
    TD.title = fullText
  } else {
    TD.removeAttribute('title')
  }
}

function getExcelColumnLabel(index: number) {
  let current = index
  let label = ''

  do {
    label = String.fromCharCode(65 + (current % 26)) + label
    current = Math.floor(current / 26) - 1
  } while (current >= 0)

  return label
}

function handleAfterColumnResize(newSize: number, column: number) {
  if (column >= 0 && column < columnWidths.value.length && Number.isFinite(newSize) && newSize > 0) {
    const nextWidths = [...columnWidths.value]
    nextWidths[column] = normalizeColumnWidthValue(newSize)
    columnWidths.value = nextWidths
  }
  void refreshComputedRowHeights()
}

function handleAfterRowResize() {
  void updateSheetViewportHeight()
}

watch(
  [rows, columnHeaders, columnConfigs, columnWidths],
  () => {
    scheduleRemoteSave()
  },
  { deep: true },
)

watch(
  () => sheetTitle.value,
  () => {
    scheduleRemoteSave()
  },
)

watch(
  () => props.sheetId,
  (nextSheetId, previousSheetId) => {
    clearSaveTimer()
    if (!nextSheetId) {
      suppressPersistence = true
      resetWorkspaceState()
      currentPage.value = 1
      pageCount.value = 1
      totalRowCount.value = 0
      pageRowOffset.value = 0
      pageLoadedRowCount.value = 0
      suppressPersistence = false
      void updateSheetViewportHeight()
      return
    }
    if (nextSheetId === previousSheetId) {
      return
    }
    currentPage.value = 1
    pageCount.value = 1
    totalRowCount.value = 0
    pageRowOffset.value = 0
    pageLoadedRowCount.value = 0
    void restoreInitialDocument().finally(() => {
      void updateSheetViewportHeight()
    })
  },
)

watch(
  [() => rows.value.length, () => columnHeaders.value.length, () => props.sheetId],
  () => {
    void updateSheetViewportHeight()
  },
)

onMounted(() => {
  bindSheetLayoutObserver()
  window.addEventListener('resize', handleWindowResize)
  window.addEventListener('mousedown', handleGlobalMouseDown)
  if (props.sheetId != null) {
    void restoreInitialDocument().finally(() => {
      void updateSheetViewportHeight()
    })
  } else {
    void updateSheetViewportHeight()
  }
})

onBeforeUnmount(() => {
  clearSaveTimer()
  window.removeEventListener('resize', handleWindowResize)
  window.removeEventListener('mousedown', handleGlobalMouseDown)
  sheetLayoutObserver?.disconnect()
  sheetLayoutObserver = null
})

defineExpose({
  openSheetSettings,
})
</script>

<template>
  <div class="note-sheet-workspace">
    <div v-if="sheetId != null && (showTitleInput || showBackButton)" class="sheet-topbar">
      <el-input v-if="showTitleInput" v-model="sheetTitle" placeholder="表格名称" class="sheet-title-input" />
      <el-button v-if="showBackButton" @click="router.push(backTo)">{{ backLabel }}</el-button>
    </div>

    <div ref="sheetFrameRef" class="sheet-frame" :class="{ 'is-empty': sheetId == null }">
      <HotTable
        v-if="sheetId != null"
        ref="hotTableRef"
        :data="rows"
        :language="'zh-CN'"
        :col-headers="columnHeaders"
        :nested-headers="nestedHeaders"
        :col-widths="columnWidths"
        :row-headers="sheetViewSettings.show_row_numbers"
        :hidden-columns="{ columns: hiddenColumnIndexes, indicators: false }"
        :manual-column-resize="true"
        :manual-column-move="true"
        :manual-row-resize="true"
        :manual-row-move="true"
        :copy-paste="true"
        :context-menu="contextMenu"
        :cells="resolveCellMeta"
        :row-heights="resolveRowHeight"
        :auto-row-size="false"
        :auto-wrap-row="true"
        :auto-wrap-col="true"
        :min-spare-rows="0"
        :render-all-rows="false"
        :height="sheetViewportHeight"
        :stretch-h="'none'"
        :selection-mode="'multiple'"
        :outside-click-deselects="false"
        :theme-name="'ht-theme-main'"
        :license-key="'non-commercial-and-evaluation'"
        :before-change="handleBeforeChange"
        :after-change="handleAfterChange"
        :after-create-row="handleAfterCreateRow"
        :after-remove-row="handleAfterRemoveRow"
        :after-create-col="handleAfterCreateCol"
        :before-remove-col="handleBeforeRemoveCol"
        :after-remove-col="handleAfterRemoveCol"
        :after-column-resize="handleAfterColumnResize"
        :after-row-resize="handleAfterRowResize"
        :before-column-move="handleBeforeColumnMove"
        :before-row-move="handleBeforeRowMove"
        :after-on-cell-mouse-down="handleHeaderMouseDown"
        :after-get-col-header="handleAfterGetColHeader"
        :after-renderer="handleAfterRenderer"
      />

      <div v-else class="sheet-empty-state">{{ emptyText }}</div>
    </div>

    <div v-if="sheetId != null && paginationEnabled" class="sheet-pagination-bar">
      <div class="sheet-pagination-status">{{ pageStatusText }}</div>
      <el-pagination
        small
        background
        layout="prev, pager, next"
        :current-page="currentPage"
        :page-size="pageSize"
        :page-count="pageCount"
        :disabled="workspaceLoading || saveInFlight"
        @current-change="handlePageChange"
      />
    </div>

    <el-dialog
      v-model="sheetSettingsDialogVisible"
      title="表格设置"
      width="360px"
      destroy-on-close
      append-to-body
    >
      <div class="sheet-settings-form">
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">行标</div>
          <el-select v-model="sheetSettingsRowMarkerMode" class="sheet-settings-inline-select">
            <el-option label="无行标" value="none" />
            <el-option label="数字（1 / 2 / 3）" value="numbers" />
          </el-select>
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">列标</div>
          <el-select v-model="sheetSettingsColumnMarkerMode" class="sheet-settings-inline-select">
            <el-option label="无列标" value="none" />
            <el-option label="字母（A / B / C）" value="letters" />
            <el-option label="数字（1 / 2 / 3）" value="numbers" />
          </el-select>
        </div>
        <div class="sheet-settings-field">
          <el-checkbox v-model="sheetSettingsDraft.pagination.enabled">启用分页</el-checkbox>
          <div v-if="sheetSettingsDraft.pagination.enabled" class="sheet-settings-pagination-inline">
            <span class="sheet-settings-label">每页条目</span>
            <el-input-number
              v-model="sheetSettingsDraft.pagination.page_size"
              :min="1"
              :max="1000"
              :step="1"
              controls-position="right"
            />
          </div>
        </div>
        <div v-if="hiddenColumnsForSettings.length" class="sheet-settings-field">
          <div class="sheet-settings-label">已隐藏字段</div>
          <div class="sheet-settings-hidden-columns">
            <button
              v-for="item in hiddenColumnsForSettings"
              :key="item.header"
              type="button"
              class="sheet-settings-hidden-column-chip"
              @click="restoreHiddenColumn(item.header)"
            >
              <span class="sheet-settings-hidden-column-title">{{ item.header }}</span>
              <span class="sheet-settings-hidden-column-action">恢复</span>
            </button>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="sheet-settings-footer">
          <el-button @click="closeSheetSettings">取消</el-button>
          <el-button type="primary" @click="applySheetSettings">保存</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="columnSettingsDialogVisible"
      :title="getColumnSettingsTitle()"
      width="360px"
      destroy-on-close
      append-to-body
    >
      <div class="sheet-settings-form">
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">类型</div>
          <el-select v-model="columnSettingsDraft.value_type" class="sheet-settings-inline-select">
            <el-option label="文本" value="text" />
            <el-option label="数值" value="number" />
            <el-option label="手机号（11位数字）" value="phone" />
          </el-select>
        </div>
        <el-checkbox v-model="columnSettingsDraft.allow_empty">允许空值</el-checkbox>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">内容显示</div>
          <el-select v-model="columnSettingsDraft.display_mode" class="sheet-settings-inline-select">
            <el-option label="自动换行" value="wrap" />
            <el-option label="单行显示（超长省略）" value="single_line" />
          </el-select>
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">列宽</div>
          <div class="sheet-settings-width-inline">
            <el-select v-model="columnSettingsDraft.width_mode" class="sheet-settings-width-mode">
              <el-option label="自适应" value="adaptive" />
              <el-option label="固定列宽" value="fixed" />
            </el-select>
            <template v-if="columnSettingsDraft.width_mode === 'fixed'">
              <el-input-number
                v-model="columnSettingsDraft.width_value"
                class="sheet-settings-width-input"
                :min="MIN_COLUMN_WIDTH"
                :max="MAX_COLUMN_WIDTH"
                :step="1"
                controls-position="right"
              />
              <span class="sheet-settings-width-unit">px</span>
            </template>
          </div>
        </div>
        <div class="sheet-settings-field">
          <div class="sheet-settings-label">备注</div>
          <el-input
            v-model="columnSettingsDraft.note"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 6 }"
            resize="none"
            placeholder="填写该字段的补充说明"
          />
        </div>
        <el-checkbox v-model="columnSettingsDraft.trim_whitespace">去除首尾空白</el-checkbox>
        <el-checkbox v-model="columnSettingsDraft.duplicate_value_highlight">重复值校验</el-checkbox>
      </div>
      <template #footer>
        <div class="sheet-settings-footer">
          <el-button @click="closeColumnSettings">取消</el-button>
          <el-button type="primary" @click="applyColumnSettings">保存</el-button>
        </div>
      </template>
    </el-dialog>

    <Teleport to="body">
      <div
        v-if="columnNotePopover.visible"
        ref="columnNotePopoverRef"
        class="sheet-column-note-popover"
        :style="columnNotePopoverStyle"
        @mousedown.stop
      >
        <div class="sheet-column-note-popover-title">{{ columnNotePopover.title }}</div>
        <div class="sheet-column-note-popover-body">{{ columnNotePopover.note }}</div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.note-sheet-workspace {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  min-height: 0;
  padding: 10px 18px 18px;
  overflow: hidden;
  gap: 10px;
}

.sheet-topbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.sheet-pagination-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  min-height: 28px;
}

.sheet-pagination-status {
  color: #8b7355;
  font-size: 12px;
  line-height: 1.2;
}

.sheet-title-input {
  width: 280px;
  max-width: 100%;
}

.sheet-settings-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sheet-settings-inline-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.sheet-settings-inline-option {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.sheet-settings-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sheet-settings-inline-field {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sheet-settings-inline-select {
  flex: 1;
  min-width: 0;
}

.sheet-settings-pagination-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.sheet-settings-width-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.sheet-settings-width-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  flex-wrap: nowrap;
}

.sheet-settings-width-mode {
  width: 132px;
  flex: 0 0 auto;
}

.sheet-settings-width-input {
  width: 92px;
  flex: 0 0 auto;
}

.sheet-settings-width-unit {
  flex: 0 0 auto;
  color: #8b7355;
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
}

.sheet-settings-hidden-columns {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.sheet-settings-hidden-column-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid #e7dcc9;
  border-radius: 999px;
  background: #fffdfa;
  color: #6d5a43;
  cursor: pointer;
}

.sheet-settings-hidden-column-chip:hover {
  border-color: #d6c7af;
  background: #fff6ea;
}

.sheet-settings-hidden-column-title {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
}

.sheet-settings-hidden-column-action {
  color: #8b7355;
  font-size: 12px;
  line-height: 1.2;
}

.sheet-settings-label {
  color: #6d5a43;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.3;
}

.sheet-settings-radio-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.sheet-settings-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.sheet-frame {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  border: 1px solid #e7dcc9;
  border-radius: 10px;
  background: #fff;
}

.sheet-frame.is-empty {
  display: flex;
}

.sheet-empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 18px;
  color: #8c7a62;
  font-size: 13px;
}

.sheet-frame :deep(.handsontable) {
  font-size: 13px;
}

.sheet-frame :deep(.htCore td),
.sheet-frame :deep(.htCore th) {
  vertical-align: top;
}

.sheet-frame :deep(th.sheet-col-marker) {
  color: #8c7a62;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.sheet-frame :deep(th.sheet-col-marker .colHeader) {
  opacity: 0.9;
}

.sheet-frame :deep(.sheet-header-label) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.sheet-frame :deep(.sheet-header-title) {
  min-width: 0;
}

.sheet-frame :deep(.sheet-header-note-trigger) {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 1px solid #d6c7af;
  border-radius: 999px;
  background: #fff9ef;
  color: #8b7355;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  cursor: help;
}

.sheet-frame :deep(.sheet-header-note-trigger:hover) {
  border-color: #c7b08b;
  background: #fdf2df;
  color: #6d5a43;
}

.sheet-frame :deep(.sheet-header-rename-input) {
  min-width: 32px;
  border: 0;
  outline: none;
  background: transparent;
  padding: 0;
  margin: 0;
  color: inherit;
  font: inherit;
  font-weight: 700;
  line-height: 1.2;
}

.sheet-column-note-popover {
  position: fixed;
  z-index: 2400;
  max-width: min(320px, calc(100vw - 24px));
  padding: 10px 12px;
  border: 1px solid #e7dcc9;
  border-radius: 10px;
  background: #fffdfa;
  box-shadow: 0 10px 28px rgba(79, 56, 24, 0.14);
}

.sheet-column-note-popover-title {
  color: #5d4c37;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.3;
}

.sheet-column-note-popover-body {
  margin-top: 6px;
  color: #6d5a43;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 900px) {
  .note-sheet-workspace {
    padding: 12px;
  }

  .sheet-title-input {
    width: 100%;
  }
}
</style>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { HotTable } from '@handsontable/vue3'
import { registerAllModules } from 'handsontable/registry'
import type Handsontable from 'handsontable/base'
import { registerLanguageDictionary, zhCN } from 'handsontable/i18n'

import {
  fetchNoteSheet,
  fetchAttendanceCourseScriptStatuses,
  generateAttendanceCourseScript,
  generateAttendanceCourseTemplate,
  organizeAttendanceCourseScripts,
  setAttendanceRowCompleted,
  updateAttendanceLinkCounts,
  type AttendanceLinkCountFieldKey,
  type AttendanceCourseScriptStatusItem,
  type NoteSheetAccessCapabilities,
  type NoteSheetPaginationState,
  sortNoteSheet,
  updateNoteSheet,
  type NoteSheetDetail,
} from '@/api/noteSheets'
import { StandardColorPickerPopover } from '@/features/color-tools'
import { mixWeightedColors, toHex } from '@/utils/colorToolkit'
import {
  getStableVisualToken,
  resolveStableVisualTokens,
  stableHash32,
  type StableVisualColorOptions,
  type StableVisualToken,
} from '@/utils/stableVisualColor'

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
const DEFAULT_COLUMN_FONT_SIZE = 13
const MIN_COLUMN_FONT_SIZE = 10
const MAX_COLUMN_FONT_SIZE = 32
const DEFAULT_COLUMN_DISPLAY_MODE: ColumnDisplayMode = 'single_line'
const DEFAULT_COLUMN_TEXT_ALIGN: ColumnTextAlign = 'auto'
const DEFAULT_DATE_DISPLAY_FORMAT = 'yyyy/m/d'
const DEFAULT_PERCENT_DISPLAY_FORMAT = '0%'
const EXCEL_DATE_UNIX_EPOCH_SERIAL = 25569
const MS_PER_DAY = 24 * 60 * 60 * 1000
const COLUMN_MARKER_ROW_HEIGHT = 28
const ROW_HEADER_MARKER_WIDTH = 50
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
const HEADER_THEME_MIN_ADJACENT_HUE_DISTANCE = 56
const HEADER_THEME_MIN_USED_HUE_DISTANCE = 24
const HASH_COLOR_MIN_ADJACENT_HUE_DISTANCE = 84
const HASH_COLOR_MIN_USED_HUE_DISTANCE = 22
const HASH_COLOR_SEED_COLLATOR = new Intl.Collator('zh-Hans-CN', { numeric: true, sensitivity: 'base' })
const DEFAULT_CELL_TEXT_COLOR = '#111827'
const DEFAULT_CELL_BACKGROUND_COLOR = '#FEF3C7'
const FORMULA_CELL_REFERENCE_RE = /(^|[^A-Za-z0-9_.$])(\$?)([A-Za-z]{1,3})(\$?)(\d+)(?![A-Za-z0-9_(!])/g
const MULTI_TEXT_ITEM_SEPARATOR_RE = /[,\uFF0C\u3001;\uFF1B\r\n]+/g
const ATTENDANCE_SUMMARY_WORKBOOK_ID = 2
const ATTENDANCE_SUMMARY_SHEET_ID = 4
const ATTENDANCE_COMPLETED_ROW_BACKGROUND = '#f2f2f2'
const PAGED_ROW_EXPANSION_MESSAGE = '分页模式下不能跨页粘贴并自动新增行。请切到最后一页追加，或关闭分页后再粘贴。'
const PAGED_AUTO_ROW_INSERT_MESSAGE = '分页模式只允许在最后一页自动新增行。请切到最后一页追加，或关闭分页后再操作。'
const ATTENDANCE_FIELD_BINDINGS = {
  courseType: { header: '课程类型', fallbackIndex: 0 },
  onlineSheet: { header: '在线考勤表', fallbackIndex: 2 },
  lessonLinks: { header: '课次链接', fallbackIndex: 4 },
  clockinLinks: { header: '打卡链接', fallbackIndex: 5 },
  completedDate: { header: '考勤实际完成结点', fallbackIndex: 10 },
} as const
const FULL_ACCESS_CAPABILITIES: NoteSheetAccessCapabilities = {
  can_read: true,
  can_use_local_view: true,
  can_edit_data: true,
  editable_data_columns: [],
  can_edit_config: true,
  can_run_sheet_actions: true,
  can_manage_access: true,
}
const COLUMN_SETTINGS_KEYS = [
  'value_type',
  'display_format',
  'allow_empty',
  'display_mode',
  'align',
  'trim_whitespace',
  'duplicate_value_highlight',
  'hash_color_mode',
  'hash_color_tone',
  'width_mode',
  'width_value',
  'font_size',
  'note',
] as const satisfies readonly ColumnSettingsDraftKey[]

type SheetRow = string[]

type ColumnDisplayMode = 'wrap' | 'single_line'
type ColumnMarkerStyle = 'letters' | 'numbers'
type ColumnMarkerMode = 'none' | 'letters' | 'numbers'
type RowMarkerNumbering = 'page' | 'global'
type RowMarkerMode = 'none' | 'page_numbers' | 'global_numbers'
type SortDirection = 'asc' | 'desc'
type ColumnWidthMode = 'adaptive' | 'fixed'
type ColumnValueType = 'text' | 'multi_text' | 'number' | 'percent' | 'date' | 'phone'
type ColumnTextAlign = 'auto' | 'left' | 'center' | 'right'
type ColumnHashColorMode = 'none' | 'text' | 'background'
type ColumnHashColorTone = 'light' | 'dark'

type SheetHeaderCellStyle = {
  background_color?: string
  text_color?: string
}

type SheetHeaderThemeCell = {
  seed: string
  hue: number | null
}

type SheetHeaderGroupCell = SheetHeaderCellStyle & {
  label: string
  colspan?: number
}

type SheetCellLink = {
  url: string
  title?: string
}

type SheetCellStyle = {
  background_color?: string
  text_color?: string
}

type HashColorStyle = {
  backgroundColor?: string
  color?: string
}

type SheetCellMeta = {
  link?: SheetCellLink
  style?: SheetCellStyle
}

type SheetCellMetaMap = Record<string, SheetCellMeta>

type SheetCellStyleDraft = {
  background_color: string
  text_color: string
}

type SheetCellStyleField = keyof SheetCellStyleDraft

type SheetCellStyleDraftTouched = Record<SheetCellStyleField, boolean>

type SelectedDataCell = {
  row: number
  column: number
  documentRow: number
}

type FormulaBarInputExpose = {
  input?: HTMLInputElement | HTMLTextAreaElement
  focus?: () => void
  blur?: () => void
}

type SheetActiveEditor = {
  row?: number
  col?: number
  TEXTAREA?: HTMLElement
  isOpened?: () => boolean
  getValue?: () => unknown
  setValue?: (value: unknown) => void
  focus?: () => void
}

type CellMouseSelectionController = {
  row?: boolean
  column?: boolean
  cell?: boolean
}

type FormulaReferenceEditTarget =
  | { kind: 'formula-bar' }
  | { kind: 'inline'; editor: SheetActiveEditor }

type FormulaReferenceInsertionTarget = FormulaReferenceEditTarget & {
  currentValue: string
  selectionStart: number
  selectionEnd: number
}

type FormulaReferenceRangeState = {
  target: FormulaReferenceEditTarget
  prefix: string
  suffix: string
  startRow: number
  startColumn: number
  currentRow: number
  currentColumn: number
  cursorPosition: number
}

type FormulaReferenceRangeBounds = {
  startRow: number
  startColumn: number
  currentRow: number
  currentColumn: number
}

type FormulaReferenceReplacementSpan = {
  target: FormulaReferenceEditTarget
  start: number
  end: number
  text: string
}

type FormulaReferenceArrowDirection = {
  rowDelta: number
  columnDelta: number
}

type SheetColumnConfig = {
  value_type?: ColumnValueType
  display_format?: string
  allow_empty?: boolean
  display_mode?: ColumnDisplayMode
  align?: Exclude<ColumnTextAlign, 'auto'>
  trim_whitespace?: boolean
  duplicate_value_highlight?: boolean
  hash_color_mode?: Exclude<ColumnHashColorMode, 'none'>
  hash_color_tone?: ColumnHashColorTone
  width_mode?: ColumnWidthMode
  font_size?: number
  hidden?: boolean
  restore_index?: number
  header_background_color?: string
  header_text_color?: string
  note?: string
}

type ColumnSettingsDraft = {
  value_type: ColumnValueType
  display_format: string
  allow_empty: boolean
  display_mode: ColumnDisplayMode
  align: ColumnTextAlign
  trim_whitespace: boolean
  duplicate_value_highlight: boolean
  hash_color_mode: ColumnHashColorMode
  hash_color_tone: ColumnHashColorTone
  width_mode: ColumnWidthMode
  width_value: number
  font_size: number
  note: string
}

type ColumnSettingsDraftKey = keyof ColumnSettingsDraft
type ColumnSettingsTouchedState = Record<ColumnSettingsDraftKey, boolean>
type ColumnSettingsMixedState = Record<ColumnSettingsDraftKey, boolean>

type SheetViewSettings = {
  show_row_numbers?: boolean
  row_marker_numbering?: RowMarkerNumbering
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
  header_groups?: SheetHeaderGroupCell[][]
  cell_meta?: SheetCellMetaMap
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

type FormulaCellModel = {
  formula: string
  value: unknown
  value2: unknown
  text: string
}

type FormulaDisplayState = {
  cells: Array<Array<FormulaCellModel | null>>
  errorKeys: Set<string>
}

type FormulaEngineInstance = {
  getCellValue: (address: { sheet: number; row: number; col: number }) => unknown
  destroy: () => void
}

type FormulaEngineClass = {
  buildFromArray: (data: string[][], config: { licenseKey: string }) => FormulaEngineInstance
  getFunctionPlugin?: (functionId: string) => unknown
  registerFunctionPlugin?: (plugin: unknown, translations?: Record<string, Record<string, string>>) => void
}

type SheetAutofillDirection = 'up' | 'down' | 'left' | 'right'

type SheetAutofillRange = {
  getTopStartCorner: () => { row: number; col: number }
  getBottomEndCorner: () => { row: number; col: number }
}

type SheetCopyPasteRange = {
  startRow: number
  startColumn: number
  endRow: number
  endColumn: number
}

type SheetInternalClipboard = {
  sheetId: number | null
  sourceStartRow: number
  sourceStartColumn: number
  rawData: string[][]
  displayData: string[][]
  createdAt: number
}

interface Props {
  sheetId: number | null
  workbookId?: number | null
  accessCapabilities?: NoteSheetAccessCapabilities | null
  showBackButton?: boolean
  showTitleInput?: boolean
  backTo?: string
  backLabel?: string
  emptyText?: string
}

const props = withDefaults(defineProps<Props>(), {
  workbookId: null,
  accessCapabilities: null,
  showBackButton: false,
  showTitleInput: true,
  backTo: '/notes/sheets',
  backLabel: '返回星云表格',
  emptyText: '请选择表格',
})

const emit = defineEmits<{
  missing: [sheetId: number]
  sheetSync: [payload: SheetWorkspaceSyncPayload]
}>()

const router = useRouter()

const remoteAccessCapabilities = ref<NoteSheetAccessCapabilities | null>(null)
const effectiveAccessCapabilities = computed(() => (
  props.accessCapabilities
    ?? remoteAccessCapabilities.value
    ?? FULL_ACCESS_CAPABILITIES
))
const canUseLocalView = computed(() => effectiveAccessCapabilities.value.can_use_local_view)
const canEditData = computed(() => effectiveAccessCapabilities.value.can_edit_data)
const editableDataColumnSet = computed(() => new Set(
  (effectiveAccessCapabilities.value.editable_data_columns ?? [])
    .map((index) => Number(index))
    .filter((index) => Number.isInteger(index) && index >= 0),
))
const canEditPartialData = computed(() => editableDataColumnSet.value.size > 0)
const canEditConfig = computed(() => effectiveAccessCapabilities.value.can_edit_config)
const canRunSheetActions = computed(() => effectiveAccessCapabilities.value.can_run_sheet_actions)
const canPersistSheet = computed(() => canEditData.value || canEditPartialData.value || canEditConfig.value)

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
const cellDisplayTextCache = new Map<string, string>()
const duplicateHighlightStyleCache = new Map<string, { backgroundColor: string }>()
const cellHashColorStyleCache = new Map<string, HashColorStyle | null>()

const hotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const sheetFrameRef = ref<HTMLElement | null>(null)
const contextMenuFallbackButtonRef = ref<HTMLButtonElement | null>(null)
const columnMarkerContentRef = ref<HTMLElement | null>(null)
const columnNotePopoverRef = ref<HTMLElement | null>(null)
const columnHeaders = ref<string[]>([...DEFAULT_SHEET_COLUMNS])
const headerGroups = ref<SheetHeaderGroupCell[][]>([])
const columnConfigs = ref<Record<string, SheetColumnConfig>>({})
const cellMeta = ref<SheetCellMetaMap>({})
const attendanceCourseScriptStatuses = ref<Record<number, AttendanceCourseScriptStatusItem>>({})
const formulaEngineClass = shallowRef<FormulaEngineClass | null>(null)
const sheetViewSettings = ref<Required<SheetViewSettings>>(createDefaultSheetViewSettings())
const columnWidths = ref<number[]>(DEFAULT_SHEET_COLUMNS.map((header) => getAdaptiveColumnWidth(header)))
const editingColumnIndex = ref<number | null>(null)
const editingColumnTitle = ref('')
const rows = ref<SheetRow[]>([createEmptyRow(DEFAULT_SHEET_COLUMNS.length)])
const sheetTitle = ref('未命名表格')
const sheetVersion = ref<number>(0)
const sheetViewportHeight = ref<number | 'auto'>('auto')
const sheetScrollLeft = ref(0)
const columnMarkerResizingIndex = ref<number | null>(null)
const sheetSettingsDialogVisible = ref(false)
const sheetSettingsDraft = ref<Required<SheetViewSettings>>(createDefaultSheetViewSettings())
const sheetSettingsRowMarkerMode = computed<RowMarkerMode>({
  get() {
    if (!sheetSettingsDraft.value.show_row_numbers) {
      return 'none'
    }
    return sheetSettingsDraft.value.row_marker_numbering === 'global' ? 'global_numbers' : 'page_numbers'
  },
  set(mode) {
    if (mode === 'none') {
      sheetSettingsDraft.value.show_row_numbers = false
      return
    }
    sheetSettingsDraft.value.show_row_numbers = true
    sheetSettingsDraft.value.row_marker_numbering = mode === 'global_numbers' ? 'global' : 'page'
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
const columnSettingsSelectionBounds = ref<{ start: number; end: number } | null>(null)
const columnSettingsDraft = ref<ColumnSettingsDraft>(createDefaultColumnSettingsDraft())
const columnSettingsTouched = ref<ColumnSettingsTouchedState>(createColumnSettingsTouchedState())
const columnSettingsMixed = ref<ColumnSettingsMixedState>(createColumnSettingsMixedState())
const cellLinkDialogVisible = ref(false)
const cellLinkDialogCell = ref<{ row: number; column: number } | null>(null)
const cellLinkDraftUrl = ref('')
const cellStyleDialogVisible = ref(false)
const cellStyleDialogCells = ref<SelectedDataCell[]>([])
const activeCellStyleColorField = ref<SheetCellStyleField | null>(null)
const cellStyleDraft = ref<SheetCellStyleDraft>({
  background_color: '',
  text_color: '',
})
const cellStyleDraftTouched = ref<SheetCellStyleDraftTouched>({
  background_color: false,
  text_color: false,
})
const formulaBarCell = ref<SelectedDataCell | null>(null)
const formulaBarDraft = ref('')
const formulaBarFocused = ref(false)
const formulaBarInputRef = ref<FormulaBarInputExpose | null>(null)
const touchContextMenuFallbackEnabled = ref(false)
const hasContextMenuFallbackSelection = ref(false)
const selectedColumnMarkerBounds = ref<{ start: number; end: number } | null>(null)
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
const sheetContentReady = ref(false)
let formulaReferencePointerDown = false
let formulaReferencePointerDownResetTimer: number | null = null
let formulaReferenceRangeState: FormulaReferenceRangeState | null = null
let formulaReferenceRangeFinishTimer: number | null = null
let inlineEditorFormulaBarSyncTimer: number | null = null
let formulaBarDraftSyncFrame: number | null = null
let formulaReferenceReplacementSpan: FormulaReferenceReplacementSpan | null = null
const formulaReferencePreviewRange = ref<FormulaReferenceRangeBounds | null>(null)
let sheetInternalClipboard: SheetInternalClipboard | null = null
const columnSettingsSelectedCount = computed(() => {
  const bounds = columnSettingsSelectionBounds.value
  return bounds ? bounds.end - bounds.start + 1 : 0
})
const isColumnSettingsMultiSelection = computed(() => columnSettingsSelectedCount.value > 1)
const pageWorkingRowCount = computed(() => trimTrailingBlankRows(
  rows.value.map((row) => normalizeRow(row, columnHeaders.value)),
).length)
const paginationEnabled = computed(() => sheetViewSettings.value.pagination.enabled)
const effectivePaginationEnabled = computed(() => (
  paginationEnabled.value && (pageCount.value > 1 || totalRowCount.value > pageSize.value)
))
const shouldRenderSheetContent = computed(() => props.sheetId != null && sheetContentReady.value)
const showContextMenuFallbackButton = computed(() => (
  touchContextMenuFallbackEnabled.value
  && shouldRenderSheetContent.value
  && (
    hasContextMenuFallbackSelection.value
    || !!formulaBarCell.value
    || !!selectedColumnMarkerBounds.value
  )
  && !workspaceLoading.value
))
const pageStatusText = computed(() => {
  if (props.sheetId == null) {
    return ''
  }

  if (!effectivePaginationEnabled.value) {
    return `共 ${pageWorkingRowCount.value} 行`
  }

  return `每页 ${pageSize.value} 行`
})

const cellStyleDialogTitle = computed(() => {
  const count = cellStyleDialogCells.value.length
  return count > 1 ? `设置 ${count} 个单元格颜色` : '设置单元格颜色'
})

const formulaBarAddress = computed(() => {
  const cell = formulaBarCell.value
  if (!cell) {
    return ''
  }
  return `${getExcelColumnLabel(cell.column)}${cell.documentRow + 1}`
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
      const rawValue = row?.[columnIndex] ?? ''
      const cellValue = getCellSemanticValue(rowIndex, columnIndex, rawValue)
      if (isColumnValueValidByType(cellValue, columnConfig.value_type, columnConfig.allow_empty)) {
        return
      }
      highlightMap.set(`${rowIndex}:${columnIndex}`, INVALID_VALUE_HIGHLIGHT_COLOR)
    })
  })

  return highlightMap
})

const cellAccentStyleMap = computed(() => {
  const styleMap = new Map<string, { backgroundColor: string }>()
  const duplicateMap = duplicateHighlightMap.value
  const invalidMap = invalidValueHighlightMap.value
  const keys = new Set<string>([
    ...duplicateMap.keys(),
    ...invalidMap.keys(),
  ])

  keys.forEach((key) => {
    const sourceColors = [
      duplicateMap.get(key),
      invalidMap.get(key),
    ].filter((color): color is string => !!color)
    const accentStyle = mixCellAccentStyle(sourceColors)
    if (accentStyle) {
      styleMap.set(key, accentStyle)
    }
  })

  return styleMap
})

const hasFormulaExpressions = computed(() => {
  const headers = normalizeHeaders(columnHeaders.value)
  return rows.value.some((row) => normalizeRow(row, headers).some(isFormulaExpression))
})

const formulaDisplayState = shallowRef<FormulaDisplayState>(createEmptyFormulaDisplayState())

const columnHashColorStyleMap = computed(() => {
  const styleMap = new Map<string, HashColorStyle>()
  const headers = normalizeHeaders(columnHeaders.value)
  if (!headers.length || !rows.value.length) {
    return styleMap
  }

  const normalizedRows = rows.value.map((row) => normalizeRow(row, headers))
  const normalizedConfigs = normalizeColumnConfigs(columnConfigs.value, headers)
  const displayState = formulaDisplayState.value
  headers.forEach((header, columnIndex) => {
    const config = normalizedConfigs[header]
    const mode = normalizeColumnHashColorMode(config?.hash_color_mode)
    if (mode === 'none') {
      return
    }

    const tone = normalizeColumnHashColorTone(config?.hash_color_tone)
    const seeds = Array.from(new Set(normalizedRows
      .map((row, rowIndex) => getCellDisplayText(
        rowIndex,
        columnIndex,
        row[columnIndex] ?? '',
        displayState,
        headers,
        normalizedConfigs,
      ).trim())
      .filter(Boolean))).sort(compareHashColorSeeds)
    if (!seeds.length) {
      return
    }

    const tokens = resolveStableVisualTokens(seeds, {
      ...getHashColorTokenOptions(mode, tone),
      minAdjacentHueDistance: HASH_COLOR_MIN_ADJACENT_HUE_DISTANCE,
      minHueDistance: HASH_COLOR_MIN_USED_HUE_DISTANCE,
    })
    seeds.forEach((seed, index) => {
      const key = createColumnHashColorStyleKey(columnIndex, mode, tone, seed)
      if (styleMap.has(key)) {
        return
      }

      const token = tokens[index]
      const style = token ? createHashColorStyleFromToken(mode, tone, token) : null
      if (style) {
        styleMap.set(key, style)
      }
    })
  })

  return styleMap
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
      markerLabel: getColumnMarkerLabel(index),
      hidden: columnConfigs.value[header]?.hidden === true,
    }))
    .filter((item) => item.hidden)
))

const normalizedHeaderGroups = computed(() => normalizeHeaderGroups(headerGroups.value, columnHeaders.value.length))

const rowHeightLayoutState = computed(() => {
  let singleLineHeight = TABLE_LINE_HEIGHT
  let hasWrappedColumns = false
  const wrappedColumns: Array<{ index: number; fontSize: number; lineHeight: number }> = []

  columnHeaders.value.forEach((header, index) => {
    const config = columnConfigs.value[header]
    if (config?.hidden === true) {
      return
    }

    const fontSize = getColumnFontSizeFromConfig(config)
    const lineHeight = getColumnLineHeightFromFontSize(fontSize)
    singleLineHeight = Math.max(singleLineHeight, lineHeight)

    if (normalizeColumnDisplayMode(config?.display_mode) !== 'single_line') {
      hasWrappedColumns = true
      wrappedColumns.push({ index, fontSize, lineHeight })
    }
  })

  return {
    hasWrappedColumns,
    singleLineHeight: singleLineHeight + TABLE_CELL_VERTICAL_PADDING * 2 + TABLE_CELL_BORDER_WIDTH,
    wrappedColumns,
  }
})

const nestedHeaders = computed(() => [
  ...normalizedHeaderGroups.value.map((row) => row.map((cell) => (
    cell.colspan && cell.colspan > 1
      ? { label: cell.label, colspan: cell.colspan }
      : cell.label
  ))),
  [...columnHeaders.value],
])

const nestedHeaderStyleRows = computed<(SheetHeaderCellStyle | null)[][]>(() => {
  const rows: (SheetHeaderCellStyle | null)[][] = []
  const themeCells = getTopHeaderThemeCells(normalizedHeaderGroups.value, columnHeaders.value)
  const depth = normalizedHeaderGroups.value.length + 1
  const useAutoTheme = normalizedHeaderGroups.value.length > 0

  normalizedHeaderGroups.value.forEach((row, level) => {
    rows.push(resolveHeaderStyleRow(
      expandHeaderGroupStyles(row, columnHeaders.value.length),
      themeCells,
      level,
      depth,
      useAutoTheme,
    ))
  })

  rows.push(resolveHeaderStyleRow(
    columnHeaders.value.map((header) => getColumnHeaderStyle(columnConfigs.value[header])),
    themeCells,
    normalizedHeaderGroups.value.length,
    depth,
    useAutoTheme,
  ))
  return rows
})

const showColumnMarkerRow = computed(() => shouldRenderSheetContent.value && sheetViewSettings.value.show_column_markers)
const sheetRowHeaders = computed<false | ((index: number) => string)>(() => (
  sheetViewSettings.value.show_row_numbers ? getSheetRowHeaderLabel : false
))

function findColumnIndexByBinding(binding: { header: string, fallbackIndex: number }) {
  const headerIndex = columnHeaders.value.findIndex((header) => normalizeCellValue(header).trim() === binding.header)
  if (headerIndex >= 0) {
    return headerIndex
  }
  return binding.fallbackIndex >= 0 && binding.fallbackIndex < columnHeaders.value.length
    ? binding.fallbackIndex
    : -1
}

const attendanceCompletedColumnIndex = computed(() => findColumnIndexByBinding(ATTENDANCE_FIELD_BINDINGS.completedDate))

const sheetGridHeight = computed(() => {
  if (sheetViewportHeight.value === 'auto') {
    return 'auto'
  }
  const reservedHeight = showColumnMarkerRow.value ? COLUMN_MARKER_ROW_HEIGHT : 0
  return Math.max(sheetViewportHeight.value - reservedHeight, 80)
})

const visibleColumnMarkerCells = computed(() => (
  columnHeaders.value
    .map((header, index) => ({
      key: `${index}:${header}`,
      index,
      label: getColumnMarkerLabel(index),
      width: columnWidths.value[index] ?? getAdaptiveColumnWidth(header),
      hidden: columnConfigs.value[header]?.hidden === true,
    }))
    .filter((item) => !item.hidden)
))

const columnMarkerContentStyle = computed(() => ({
  width: `${visibleColumnMarkerCells.value.reduce((sum, item) => sum + item.width, 0)}px`,
  transform: `translate3d(${-sheetScrollLeft.value}px, 0, 0)`,
}))

const columnMarkerRowStyle = computed(() => ({
  height: `${COLUMN_MARKER_ROW_HEIGHT}px`,
  minHeight: `${COLUMN_MARKER_ROW_HEIGHT}px`,
  flexBasis: `${COLUMN_MARKER_ROW_HEIGHT}px`,
}))

const columnMarkerCornerStyle = computed(() => ({
  width: `${ROW_HEADER_MARKER_WIDTH}px`,
  flexBasis: `${ROW_HEADER_MARKER_WIDTH}px`,
}))

let suppressPersistence = false
let saveTimer: ReturnType<typeof setTimeout> | null = null
let changeSerial = 0
let lastQueuedSerial = 0
let saveInFlight = false
let sheetLayoutObserver: ResizeObserver | null = null
let editingHeaderInputEl: HTMLInputElement | null = null
let gridScrollElement: HTMLElement | null = null
let columnMarkerScrollFrame: number | null = null
let formulaEngineImportPromise: Promise<FormulaEngineClass | null> | null = null
let sheetFormulaPluginRegistered = false
let columnMarkerSelectionAnchor: number | null = null
let columnMarkerResizeState: {
  columnIndex: number
  startX: number
  startWidth: number
  previousUserSelect: string
  previousCursor: string
} | null = null

const contextMenu = {
  items: {
    row_above: {
      name: '上方插入行',
      hidden: () => !shouldShowRowActions(),
      disabled: () => !canEditData.value,
      callback: () => {
        insertRowFromSelection('above')
      },
    },
    row_below: {
      name: '下方插入行',
      hidden: () => !shouldShowRowActions(),
      disabled: () => !canEditData.value,
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
      disabled: () => !canEditConfig.value,
      callback: () => {
        insertColumnFromSelection('left')
      },
    },
    insert_col_right: {
      name: '右方插入列',
      hidden: () => !shouldShowColumnActions(),
      disabled: () => !canEditConfig.value,
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
      hidden: () => !hasColumnHeaderSelection(),
      disabled: () => !canEditConfig.value,
      callback: () => {
        openSelectedColumnSettings()
      },
    },
    hide_column: {
      name: '隐藏字段',
      hidden: () => !shouldShowHideColumnAction(),
      disabled: () => !canEditConfig.value,
      callback: () => {
        hideSelectedColumns()
      },
    },
    show_column: {
      name: '显示字段',
      hidden: () => !shouldShowShowColumnAction(),
      disabled: () => !canEditConfig.value,
      callback: () => {
        showHiddenColumnsFromSelection()
      },
    },
    remove_col: {
      name: '移除该列',
      hidden: () => !shouldShowRemoveColumnAction(),
      disabled: () => !canEditConfig.value,
      callback: () => {
        removeSelectedColumns()
      },
    },
    remove_row: {
      name: '删除选中行',
      hidden: () => !shouldShowRemoveRowAction(),
      disabled: () => !canEditData.value,
      callback: () => {
        removeSelectedRows()
      },
    },
    hsep_attendance_completion: {
      name: '---------',
      hidden: () => !canSetAttendanceCompletedFromSelection(),
    },
    set_attendance_completed: {
      name: () => (canRunSheetActions.value ? '设置完结' : '设置完结（只读）'),
      hidden: () => !canSetAttendanceCompletedFromSelection(),
      disabled: () => !canRunSheetActions.value,
      callback: () => {
        void handleSetAttendanceCompletedFromSelection()
      },
    },
    hsep_attendance_course_template: {
      name: '---------',
      hidden: () => !canGenerateAttendanceCourseTemplateFromSelection(),
    },
    generate_attendance_course_template: {
      name: () => (canRunSheetActions.value ? '生成新课模板' : '生成新课模板（只读）'),
      hidden: () => !canGenerateAttendanceCourseTemplateFromSelection(),
      disabled: () => !canRunSheetActions.value,
      callback: () => {
        void handleGenerateAttendanceCourseTemplateFromSelection()
      },
    },
    hsep_attendance_course_script: {
      name: '---------',
      hidden: () => !canGenerateAttendanceCourseScriptFromSelection() && !canOrganizeAttendanceCourseScriptsFromColumn(),
    },
    generate_attendance_course_script: {
      name: () => (canRunSheetActions.value ? '生成py脚本' : '生成py脚本（只读）'),
      hidden: () => !canGenerateAttendanceCourseScriptFromSelection(),
      disabled: () => !canRunSheetActions.value,
      callback: () => {
        void handleGenerateAttendanceCourseScriptFromSelection()
      },
    },
    organize_attendance_course_scripts: {
      name: () => (canRunSheetActions.value ? '整理py脚本' : '整理py脚本（只读）'),
      hidden: () => !canOrganizeAttendanceCourseScriptsFromColumn(),
      disabled: () => !canRunSheetActions.value,
      callback: () => {
        void handleOrganizeAttendanceCourseScriptsFromColumn()
      },
    },
    hsep_attendance_link_counts: {
      name: '---------',
      hidden: () => (
        !canUpdateAttendanceLinkCountsFromSelection('lesson_links')
        && !canUpdateAttendanceLinkCountsFromSelection('clockin_links')
      ),
    },
    update_attendance_lesson_link_counts: {
      name: () => (canRunSheetActions.value ? '更新数据' : '更新数据（只读）'),
      hidden: () => !canUpdateAttendanceLinkCountsFromSelection('lesson_links'),
      disabled: () => !canRunSheetActions.value,
      callback: () => {
        void handleUpdateAttendanceLinkCountsFromSelection('lesson_links')
      },
    },
    update_attendance_clockin_link_counts: {
      name: () => (canRunSheetActions.value ? '更新数据' : '更新数据（只读）'),
      hidden: () => !canUpdateAttendanceLinkCountsFromSelection('clockin_links'),
      disabled: () => !canRunSheetActions.value,
      callback: () => {
        void handleUpdateAttendanceLinkCountsFromSelection('clockin_links')
      },
    },
    hsep_style: {
      name: '---------',
      hidden: () => !hasDataCellSelection(),
    },
    set_cell_style: {
      name: () => (getSelectedDataCells().length > 1 ? '设置选区颜色' : '设置单元格颜色'),
      hidden: () => !hasDataCellSelection(),
      disabled: () => !canEditConfig.value,
      callback: () => {
        openSelectedCellStyleDialog()
      },
    },
    remove_cell_style: {
      name: () => (getSelectedDataCells().length > 1 ? '清除选区颜色' : '清除单元格颜色'),
      hidden: () => !hasSelectedCellStyle(),
      disabled: () => !canEditConfig.value,
      callback: () => {
        removeSelectedCellStyle()
      },
    },
    hsep_link: {
      name: '---------',
      hidden: () => !hasSingleDataCellSelection(),
    },
    set_cell_link: {
      name: '设置超链接',
      hidden: () => !hasSingleDataCellSelection(),
      disabled: () => !canEditConfig.value,
      callback: () => {
        openSelectedCellLinkDialog()
      },
    },
    open_cell_link: {
      name: '打开超链接',
      hidden: () => !hasSelectedCellLink(),
      callback: () => {
        openSelectedCellLink()
      },
    },
    remove_cell_link: {
      name: '移除超链接',
      hidden: () => !hasSelectedCellLink(),
      disabled: () => !canEditConfig.value,
      callback: () => {
        removeSelectedCellLink()
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

function warnReadOnlyAction() {
  ElMessage.warning('只读权限不能执行此操作')
}

function canEditDataColumn(columnIndex: number) {
  return canEditData.value || editableDataColumnSet.value.has(columnIndex)
}

function canEditFormulaBarCell() {
  const cell = formulaBarCell.value
  return !!cell && canEditDataColumn(cell.column)
}

function isColumnRangeEditable(startColumn: number, endColumn: number) {
  if (canEditData.value) {
    return true
  }
  for (let columnIndex = startColumn; columnIndex <= endColumn; columnIndex += 1) {
    if (!editableDataColumnSet.value.has(columnIndex)) {
      return false
    }
  }
  return true
}

function warnReadOnlyColumnAction() {
  ElMessage.warning('当前列只读，不能修改')
}

function ensureCanEditData() {
  if (canEditData.value) {
    return true
  }
  warnReadOnlyAction()
  return false
}

function ensureCanEditConfig() {
  if (canEditConfig.value) {
    return true
  }
  warnReadOnlyAction()
  return false
}

function ensureCanRunSheetActions() {
  if (canRunSheetActions.value) {
    return true
  }
  warnReadOnlyAction()
  return false
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
  const target = event.target as Node | null
  if (target && isFormulaReferenceEditableMouseTarget(target)) {
    clearFormulaReferenceReplacementSpan()
  }

  if (!columnNotePopover.value.visible) {
    return
  }

  if (target && columnNotePopoverRef.value?.contains(target)) {
    return
  }
  closeColumnNotePopover()
}

function resetWorkspaceState() {
  clearEditingColumnState()
  clearFormulaBarSelection()
  clearColumnMarkerSelection()
  hasContextMenuFallbackSelection.value = false
  sheetContentReady.value = false
  closeColumnNotePopover()
  columnHeaders.value = [...DEFAULT_SHEET_COLUMNS]
  headerGroups.value = []
  columnConfigs.value = {}
  cellMeta.value = {}
  attendanceCourseScriptStatuses.value = {}
  remoteAccessCapabilities.value = null
  sheetViewSettings.value = createDefaultSheetViewSettings()
  columnWidths.value = DEFAULT_SHEET_COLUMNS.map((header) => getAdaptiveColumnWidth(header))
  rows.value = [createEmptyRow(DEFAULT_SHEET_COLUMNS.length)]
  sheetTitle.value = '未命名表格'
  sheetVersion.value = 0
  sheetScrollLeft.value = 0
  sheetSettingsDialogVisible.value = false
  sheetSettingsDraft.value = createDefaultSheetViewSettings()
  columnSettingsDialogVisible.value = false
  columnSettingsColumnIndex.value = null
  columnSettingsSelectionBounds.value = null
  cellLinkDialogVisible.value = false
  cellLinkDialogCell.value = null
  cellLinkDraftUrl.value = ''
  cellStyleDialogVisible.value = false
  cellStyleDialogCells.value = []
  activeCellStyleColorField.value = null
  cellStyleDraft.value = {
    background_color: '',
    text_color: '',
  }
  cellStyleDraftTouched.value = {
    background_color: false,
    text_color: false,
  }
  columnSettingsDraft.value = {
    value_type: 'text',
    display_format: '',
    allow_empty: true,
    display_mode: DEFAULT_COLUMN_DISPLAY_MODE,
    align: DEFAULT_COLUMN_TEXT_ALIGN,
    trim_whitespace: true,
    duplicate_value_highlight: false,
    hash_color_mode: 'none',
    hash_color_tone: 'light',
    width_mode: 'adaptive',
    width_value: 120,
    font_size: DEFAULT_COLUMN_FONT_SIZE,
    note: '',
  }
}

function normalizeCellValue(value: unknown): string {
  return value == null ? '' : String(value)
}

function normalizeMultiTextValue(value: unknown) {
  const raw = normalizeCellValue(value).trim()
  if (!raw || isFormulaExpression(raw)) {
    return raw
  }
  return raw
    .split(MULTI_TEXT_ITEM_SEPARATOR_RE)
    .map((item) => item.trim())
    .filter(Boolean)
    .join(', ')
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

function normalizeCssColor(value: unknown) {
  if (typeof value !== 'string') {
    return ''
  }
  return value.trim().slice(0, 64)
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

function normalizeHeaderGroupCell(source: unknown, remainingColumnCount: number): SheetHeaderGroupCell | null {
  if (remainingColumnCount <= 0) {
    return null
  }

  if (typeof source === 'string' || typeof source === 'number') {
    return { label: normalizeCellValue(source), colspan: 1 }
  }

  if (!source || typeof source !== 'object') {
    return null
  }

  const record = source as Record<string, unknown>
  const label = normalizeCellValue(record.label)
  const rawColspan = Number(record.colspan)
  const colspan = Number.isInteger(rawColspan) && rawColspan > 0
    ? Math.min(rawColspan, remainingColumnCount)
    : 1
  const backgroundColor = normalizeCssColor(record.background_color)
  const textColor = normalizeCssColor(record.text_color)
  const cell: SheetHeaderGroupCell = { label, colspan }
  if (backgroundColor) {
    cell.background_color = backgroundColor
  }
  if (textColor) {
    cell.text_color = textColor
  }
  return cell
}

function normalizeHeaderGroups(source: unknown, columnCount: number): SheetHeaderGroupCell[][] {
  if (!Array.isArray(source) || columnCount <= 0) {
    return []
  }

  const normalizedRows: SheetHeaderGroupCell[][] = []
  for (const row of source) {
    if (!Array.isArray(row)) {
      continue
    }

    const normalizedRow: SheetHeaderGroupCell[] = []
    let remainingColumnCount = columnCount
    for (const cellSource of row) {
      const cell = normalizeHeaderGroupCell(cellSource, remainingColumnCount)
      if (!cell) {
        continue
      }
      normalizedRow.push(cell)
      remainingColumnCount -= cell.colspan ?? 1
      if (remainingColumnCount <= 0) {
        break
      }
    }

    if (remainingColumnCount > 0 && normalizedRow.length > 0) {
      normalizedRow.push({ label: '', colspan: remainingColumnCount })
    }
    if (normalizedRow.some((cell) => cell.label || cell.background_color || cell.text_color)) {
      normalizedRows.push(normalizedRow)
    }
  }

  return normalizedRows
}

function createCellMetaKey(rowIndex: number, columnIndex: number) {
  return `${rowIndex}:${columnIndex}`
}

function parseCellMetaKey(key: string) {
  const match = key.match(/^(\d+):(\d+)$/)
  if (!match) {
    return null
  }

  return {
    row: Number(match[1]),
    column: Number(match[2]),
  }
}

function normalizeCellLink(source: unknown): SheetCellLink | null {
  if (!source || typeof source !== 'object') {
    return null
  }

  const record = source as Record<string, unknown>
  const url = normalizeHyperlinkUrl(record.url)
  if (!url) {
    return null
  }

  const title = normalizeCellValue(record.title).trim()
  return title ? { url, title } : { url }
}

function normalizeCellStyle(source: unknown): SheetCellStyle | null {
  if (!source || typeof source !== 'object') {
    return null
  }

  const record = source as Record<string, unknown>
  const backgroundColor = normalizeCssColor(record.background_color)
  const textColor = normalizeCssColor(record.text_color)
  if (!backgroundColor && !textColor) {
    return null
  }

  const style: SheetCellStyle = {}
  if (backgroundColor) {
    style.background_color = backgroundColor
  }
  if (textColor) {
    style.text_color = textColor
  }
  return style
}

function normalizeCellMetaEntry(source: unknown): SheetCellMeta | null {
  if (!source || typeof source !== 'object') {
    return null
  }

  const record = source as Record<string, unknown>
  const link = normalizeCellLink(record.link)
  const style = normalizeCellStyle(record.style)
  if (!link && !style) {
    return null
  }

  const meta: SheetCellMeta = {}
  if (link) {
    meta.link = link
  }
  if (style) {
    meta.style = style
  }
  return meta
}

function normalizeCellMetaMap(source: unknown, columnCount: number): SheetCellMetaMap {
  if (!source || typeof source !== 'object' || columnCount <= 0) {
    return {}
  }

  const normalized: SheetCellMetaMap = {}
  for (const [key, value] of Object.entries(source as Record<string, unknown>)) {
    const position = parseCellMetaKey(key)
    if (!position || position.column >= columnCount) {
      continue
    }

    const meta = normalizeCellMetaEntry(value)
    if (meta) {
      normalized[createCellMetaKey(position.row, position.column)] = meta
    }
  }
  return normalized
}

function getColumnHeaderStyle(config: SheetColumnConfig | undefined): SheetHeaderCellStyle | null {
  if (!config?.header_background_color && !config?.header_text_color) {
    return null
  }
  return {
    background_color: config.header_background_color,
    text_color: config.header_text_color,
  }
}

function expandHeaderGroupStyles(row: SheetHeaderGroupCell[], columnCount: number): (SheetHeaderCellStyle | null)[] {
  const styles: (SheetHeaderCellStyle | null)[] = []
  for (const cell of row) {
    const colspan = Math.max(1, cell.colspan ?? 1)
    const style = cell.background_color || cell.text_color
      ? { background_color: cell.background_color, text_color: cell.text_color }
      : null
    for (let index = 0; index < colspan && styles.length < columnCount; index += 1) {
      styles.push(style)
    }
  }
  while (styles.length < columnCount) {
    styles.push(null)
  }
  return styles
}

function getTopHeaderThemeCells(groups: SheetHeaderGroupCell[][], headers: string[]): SheetHeaderThemeCell[] {
  const fallbackThemeCells = () => {
    const tokens = resolveStableVisualTokens(headers.map((header) => header.trim() || '列'))
    return headers.map((header, index) => ({
      seed: header.trim() || '列',
      hue: tokens[index]?.hue ?? null,
    }))
  }

  const topRow = groups[0]
  if (!topRow?.length) {
    return fallbackThemeCells()
  }

  const entries: Array<{ seed: string; colspan: number }> = []
  let columnIndex = 0
  for (const cell of topRow) {
    const colspan = Math.max(1, cell.colspan ?? 1)
    const seed = cell.label.trim() || headers[columnIndex]?.trim() || '列'
    entries.push({ seed, colspan })
    columnIndex += colspan
  }

  while (columnIndex < headers.length) {
    const seed = headers[columnIndex]?.trim() || '列'
    entries.push({ seed, colspan: 1 })
    columnIndex += 1
  }

  const tokens = resolveStableVisualTokens(entries.map((entry) => entry.seed), {
    minAdjacentHueDistance: HEADER_THEME_MIN_ADJACENT_HUE_DISTANCE,
    minHueDistance: HEADER_THEME_MIN_USED_HUE_DISTANCE,
  })
  const themeCells: SheetHeaderThemeCell[] = []
  entries.forEach((entry, index) => {
    const hue = tokens[index]?.hue ?? null
    for (let offset = 0; offset < entry.colspan && themeCells.length < headers.length; offset += 1) {
      themeCells.push({ seed: entry.seed, hue })
    }
  })

  return themeCells.length ? themeCells : fallbackThemeCells()
}

function getAutoHeaderStyle(themeCell: SheetHeaderThemeCell, level: number, depth: number): SheetHeaderCellStyle | null {
  const normalizedSeed = themeCell.seed.trim()
  if (!normalizedSeed || themeCell.hue == null) {
    return null
  }

  const hue = themeCell.hue
  const denominator = Math.max(depth - 1, 1)
  const lightness = Math.min(96, Math.round(84 + (level / denominator) * 10))
  const saturation = Math.max(44, 62 - level * 4)
  return {
    background_color: `hsl(${hue} ${saturation}% ${lightness}%)`,
    text_color: '#111827',
  }
}

function resolveHeaderStyleRow(
  explicitStyles: (SheetHeaderCellStyle | null)[],
  themeCells: SheetHeaderThemeCell[],
  level: number,
  depth: number,
  useAutoTheme: boolean,
) {
  return themeCells.map((themeCell, index) => {
    const explicitStyle = explicitStyles[index]
    if (!useAutoTheme) {
      return explicitStyle
    }

    const autoStyle = getAutoHeaderStyle(themeCell, level, depth)
    if (!autoStyle) {
      return explicitStyle
    }
    if (!explicitStyle) {
      return autoStyle
    }
    return {
      background_color: explicitStyle.background_color || autoStyle.background_color,
      text_color: explicitStyle.text_color || autoStyle.text_color,
    }
  })
}

function normalizeColumnDisplayMode(value: unknown): ColumnDisplayMode {
  return value === 'wrap' ? 'wrap' : DEFAULT_COLUMN_DISPLAY_MODE
}

function normalizeColumnTextAlign(value: unknown): ColumnTextAlign {
  if (value === 'left' || value === 'center' || value === 'right') {
    return value
  }
  return DEFAULT_COLUMN_TEXT_ALIGN
}

function normalizeColumnValueType(value: unknown): ColumnValueType {
  if (value === 'multi_text' || value === 'number' || value === 'percent' || value === 'date' || value === 'phone') {
    return value
  }
  return 'text'
}

function normalizeColumnDisplayFormat(value: unknown, valueType: ColumnValueType) {
  const raw = normalizeCellValue(value).trim()
  if (valueType === 'date') {
    return raw || DEFAULT_DATE_DISPLAY_FORMAT
  }
  if (valueType === 'number') {
    return raw
  }
  if (valueType === 'percent') {
    return raw || DEFAULT_PERCENT_DISPLAY_FORMAT
  }
  return ''
}

function isDefaultColumnDisplayFormat(valueType: ColumnValueType, displayFormat: string) {
  if (valueType === 'date') {
    return normalizeColumnDisplayFormat(displayFormat, valueType) === DEFAULT_DATE_DISPLAY_FORMAT
  }
  if (valueType === 'percent') {
    return normalizeColumnDisplayFormat(displayFormat, valueType) === DEFAULT_PERCENT_DISPLAY_FORMAT
  }
  return normalizeColumnDisplayFormat(displayFormat, valueType) === ''
}

function normalizeColumnHashColorMode(value: unknown): ColumnHashColorMode {
  if (value === 'text' || value === 'background') {
    return value
  }
  return 'none'
}

function normalizeColumnHashColorTone(value: unknown): ColumnHashColorTone {
  return value === 'dark' ? 'dark' : 'light'
}

function normalizeColumnWidthMode(value: unknown): ColumnWidthMode {
  return value === 'fixed' ? 'fixed' : 'adaptive'
}

function normalizeColumnFontSize(value: unknown) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return DEFAULT_COLUMN_FONT_SIZE
  }
  return Math.min(Math.max(Math.round(numeric), MIN_COLUMN_FONT_SIZE), MAX_COLUMN_FONT_SIZE)
}

function normalizeColumnNote(value: unknown) {
  return normalizeCellValue(value).trim()
}

function normalizeHyperlinkUrl(value: unknown) {
  const raw = normalizeCellValue(value).trim()
  if (!raw) {
    return ''
  }

  const compact = raw.slice(0, 2048)
  const lower = compact.toLowerCase()
  if (
    lower.startsWith('javascript:')
    || lower.startsWith('data:')
    || lower.startsWith('vbscript:')
  ) {
    return ''
  }

  if (
    /^[a-z][a-z\d+.-]*:/i.test(compact)
    || compact.startsWith('/')
    || compact.startsWith('#')
  ) {
    return compact
  }

  if (/^[^\s]+\.[^\s]+/.test(compact)) {
    return `https://${compact}`
  }

  return compact
}

function isColumnHiddenConfigValue(value: unknown) {
  return value === true
}

function createDefaultSheetViewSettings(): Required<SheetViewSettings> {
  return {
    show_row_numbers: true,
    row_marker_numbering: 'page',
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

function normalizeRowMarkerNumbering(value: unknown): RowMarkerNumbering {
  return value === 'global' ? 'global' : 'page'
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
    row_marker_numbering: normalizeRowMarkerNumbering(record.row_marker_numbering),
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
    const displayFormat = normalizeColumnDisplayFormat(configRecord.display_format, valueType)
    const allowEmpty = configRecord.allow_empty !== false
    const displayMode = normalizeColumnDisplayMode(configRecord.display_mode)
    const align = normalizeColumnTextAlign(configRecord.align)
    const trimWhitespace = configRecord.trim_whitespace !== false
    const duplicateValueHighlight = configRecord.duplicate_value_highlight === true
    const hashColorMode = normalizeColumnHashColorMode(configRecord.hash_color_mode)
    const hashColorTone = normalizeColumnHashColorTone(configRecord.hash_color_tone)
    const widthMode = normalizeColumnWidthMode(configRecord.width_mode)
    const fontSize = normalizeColumnFontSize(configRecord.font_size)
    const hidden = isColumnHiddenConfigValue(configRecord.hidden)
    const restoreIndex = normalizeNonNegativeInt(configRecord.restore_index, -1)
    const headerBackgroundColor = normalizeCssColor(configRecord.header_background_color)
    const headerTextColor = normalizeCssColor(configRecord.header_text_color)
    const note = normalizeColumnNote(configRecord.note)

    if (
      valueType !== 'text'
      || !isDefaultColumnDisplayFormat(valueType, displayFormat)
      || allowEmpty === false
      || displayMode !== DEFAULT_COLUMN_DISPLAY_MODE
      || align !== DEFAULT_COLUMN_TEXT_ALIGN
      || trimWhitespace === false
      || duplicateValueHighlight
      || hashColorMode !== 'none'
      || widthMode !== 'adaptive'
      || fontSize !== DEFAULT_COLUMN_FONT_SIZE
      || hidden
      || restoreIndex >= 0
      || headerBackgroundColor
      || headerTextColor
      || note
    ) {
      normalized[header] = {}
      if (valueType !== 'text') {
        normalized[header].value_type = valueType
      }
      if (!isDefaultColumnDisplayFormat(valueType, displayFormat)) {
        normalized[header].display_format = displayFormat
      }
      if (!allowEmpty) {
        normalized[header].allow_empty = false
      }
      if (displayMode !== DEFAULT_COLUMN_DISPLAY_MODE) {
        normalized[header].display_mode = displayMode
      }
      if (align !== DEFAULT_COLUMN_TEXT_ALIGN) {
        normalized[header].align = align
      }
      if (!trimWhitespace) {
        normalized[header].trim_whitespace = false
      }
      if (duplicateValueHighlight) {
        normalized[header].duplicate_value_highlight = true
      }
      if (hashColorMode !== 'none') {
        normalized[header].hash_color_mode = hashColorMode
        if (hashColorTone !== 'light') {
          normalized[header].hash_color_tone = hashColorTone
        }
      }
      if (widthMode !== 'adaptive') {
        normalized[header].width_mode = widthMode
      }
      if (fontSize !== DEFAULT_COLUMN_FONT_SIZE) {
        normalized[header].font_size = fontSize
      }
      if (hidden) {
        normalized[header].hidden = true
      }
      if (restoreIndex >= 0) {
        normalized[header].restore_index = restoreIndex
      }
      if (headerBackgroundColor) {
        normalized[header].header_background_color = headerBackgroundColor
      }
      if (headerTextColor) {
        normalized[header].header_text_color = headerTextColor
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
    return createDefaultColumnSettingsDraft()
  }

  const record = source as Record<string, unknown>
  const valueType = normalizeColumnValueType(record.value_type)
  const hashColorMode = normalizeColumnHashColorMode(record.hash_color_mode)
  return {
    value_type: valueType,
    display_format: normalizeColumnDisplayFormat(record.display_format, valueType),
    allow_empty: record.allow_empty !== false,
    display_mode: normalizeColumnDisplayMode(record.display_mode),
    align: normalizeColumnTextAlign(record.align),
    trim_whitespace: record.trim_whitespace !== false,
    duplicate_value_highlight: record.duplicate_value_highlight === true,
    hash_color_mode: hashColorMode,
    hash_color_tone: hashColorMode === 'none' ? 'light' : normalizeColumnHashColorTone(record.hash_color_tone),
    width_mode: normalizeColumnWidthMode(record.width_mode),
    width_value: 120,
    font_size: normalizeColumnFontSize(record.font_size),
    note: normalizeColumnNote(record.note),
  }
}

function createDefaultColumnSettingsDraft(): ColumnSettingsDraft {
  return {
    value_type: 'text',
    display_format: '',
    allow_empty: true,
    display_mode: DEFAULT_COLUMN_DISPLAY_MODE,
    align: DEFAULT_COLUMN_TEXT_ALIGN,
    trim_whitespace: true,
    duplicate_value_highlight: false,
    hash_color_mode: 'none',
    hash_color_tone: 'light',
    width_mode: 'adaptive',
    width_value: 120,
    font_size: DEFAULT_COLUMN_FONT_SIZE,
    note: '',
  }
}

function createColumnSettingsTouchedState(touched = false): ColumnSettingsTouchedState {
  return Object.fromEntries(
    COLUMN_SETTINGS_KEYS.map((key) => [key, touched]),
  ) as ColumnSettingsTouchedState
}

function createColumnSettingsMixedState(): ColumnSettingsMixedState {
  return Object.fromEntries(
    COLUMN_SETTINGS_KEYS.map((key) => [key, false]),
  ) as ColumnSettingsMixedState
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

function getHiddenColumnIndexesToShowFromSelection() {
  const bounds = getSelectionColumnBounds()
  const columnCount = columnHeaders.value.length
  if (!hasColumnHeaderSelection() || !bounds || columnCount <= 0) {
    return []
  }

  const start = Math.max(0, Math.min(bounds.start, columnCount - 1))
  const end = Math.max(start, Math.min(bounds.end, columnCount - 1))
  const isColumnHidden = (index: number) => {
    const header = columnHeaders.value[index]
    return !!header && columnConfigs.value[header]?.hidden === true
  }

  const indexes = new Set<number>()
  for (let index = start; index <= end; index += 1) {
    if (isColumnHidden(index)) {
      indexes.add(index)
    }
  }

  const visibleIndexes = columnHeaders.value
    .map((header, index) => (columnConfigs.value[header]?.hidden === true ? -1 : index))
    .filter((index) => index >= 0)
  const firstVisibleIndex = visibleIndexes[0]
  const lastVisibleIndex = visibleIndexes[visibleIndexes.length - 1]

  if (firstVisibleIndex != null && start <= firstVisibleIndex && firstVisibleIndex <= end) {
    for (let index = 0; index < firstVisibleIndex; index += 1) {
      if (isColumnHidden(index)) {
        indexes.add(index)
      }
    }
  }

  if (lastVisibleIndex != null && start <= lastVisibleIndex && lastVisibleIndex <= end) {
    for (let index = lastVisibleIndex + 1; index < columnCount; index += 1) {
      if (isColumnHidden(index)) {
        indexes.add(index)
      }
    }
  }

  return [...indexes].sort((left, right) => left - right)
}

function shouldShowShowColumnAction() {
  return getHiddenColumnIndexesToShowFromSelection().length > 0
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
    header_groups: [],
    cell_meta: {},
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
  const formulaDisplayForWidths = buildFormulaDisplayStateForRows(headers, normalizedRows, normalizedColumnConfigs)
  const normalizedWidths = headers.map((_, index) => {
    const width = Number(sourceWidths[index])
    const widthMode = normalizeColumnConfig(normalizedColumnConfigs[headers[index]]).width_mode
    if (widthMode === 'fixed' && Number.isFinite(width) && width > 0) {
      return normalizeColumnWidthValue(width)
    }
    return getAutoColumnWidth(index, headers, normalizedRows, normalizedColumnConfigs, formulaDisplayForWidths)
  })
  return {
    schema_version: 1,
    columns: headers,
    rows: normalizedRows,
    header_groups: normalizeHeaderGroups(record.header_groups, headers.length),
    cell_meta: normalizeCellMetaMap(record.cell_meta, headers.length),
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
    header_groups: normalizeHeaderGroups(headerGroups.value, headers.length),
    cell_meta: normalizeCellMetaMap(cellMeta.value, headers.length),
    column_configs: normalizeColumnConfigs(columnConfigs.value, headers),
    column_widths: headers.map((_, index) => columnWidths.value[index] ?? getAutoColumnWidth(
      index,
      headers,
      normalizedRows,
      normalizeColumnConfigs(columnConfigs.value, headers),
    )),
    view_settings: normalizeSheetViewSettings(sheetViewSettings.value),
  }
}

function isFormulaExpression(value: unknown) {
  return typeof value === 'string' && value.startsWith('=')
}

function getFormulaStringEndQuote(startQuote: string) {
  if (startQuote === '“') {
    return '”'
  }
  if (startQuote === '‘') {
    return '’'
  }
  return startQuote
}

function isFormulaStringQuote(char: string) {
  return char === '"' || char === '\'' || char === '“' || char === '”' || char === '‘' || char === '’'
}

function findNextNonWhitespaceIndex(value: string, startIndex: number) {
  for (let index = startIndex; index < value.length; index += 1) {
    if (!/\s/.test(value[index])) {
      return index
    }
  }
  return -1
}

function normalizeFormulaStringContentForDoubleQuote(content: string) {
  return content.replace(/"/g, '""')
}

function normalizeFormulaStringContentForSingleQuote(content: string) {
  return content.replace(/'/g, "''")
}

function readNormalizedFormulaStringToken(value: string, startIndex: number) {
  const startQuote = value[startIndex]
  const endQuote = getFormulaStringEndQuote(startQuote)
  let content = ''

  for (let index = startIndex + 1; index < value.length; index += 1) {
    const char = value[index]
    if (char === endQuote) {
      if ((startQuote === '"' || startQuote === '\'') && value[index + 1] === endQuote) {
        content += endQuote
        index += 1
        continue
      }

      const nextNonWhitespaceIndex = findNextNonWhitespaceIndex(value, index + 1)
      if (nextNonWhitespaceIndex >= 0 && value[nextNonWhitespaceIndex] === '!') {
        return {
          endIndex: index,
          value: `'${normalizeFormulaStringContentForSingleQuote(content)}'`,
        }
      }

      return {
        endIndex: index,
        value: `"${normalizeFormulaStringContentForDoubleQuote(content)}"`,
      }
    }

    content += char
  }

  return {
    endIndex: value.length - 1,
    value: value.slice(startIndex),
  }
}

function readFormulaFunctionAlias(value: string, startIndex: number) {
  const aliases = [
    { source: '日期解析', target: 'DATE_PARSE' },
  ]
  for (const alias of aliases) {
    if (!value.startsWith(alias.source, startIndex)) {
      continue
    }
    const nextIndex = findNextNonWhitespaceIndex(value, startIndex + alias.source.length)
    if (nextIndex >= 0 && (value[nextIndex] === '(' || value[nextIndex] === '（')) {
      return {
        endIndex: startIndex + alias.source.length - 1,
        value: alias.target,
      }
    }
  }
  return null
}

function normalizeFormulaInputExpression(value: string) {
  if (!isFormulaExpression(value)) {
    return value
  }

  let normalized = ''
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index]
    if (isFormulaStringQuote(char)) {
      const token = readNormalizedFormulaStringToken(value, index)
      normalized += token.value
      index = token.endIndex
      continue
    }

    const functionAlias = readFormulaFunctionAlias(value, index)
    if (functionAlias) {
      normalized += functionAlias.value
      index = functionAlias.endIndex
      continue
    }

    if (char === '，') {
      normalized += ','
      continue
    }
    if (char === '（') {
      normalized += '('
      continue
    }
    if (char === '）') {
      normalized += ')'
      continue
    }
    normalized += char
  }
  return normalized
}

function normalizeFormulaExpressionForEngine(value: string) {
  return normalizeFormulaInputExpression(value)
}

function isFormulaErrorValue(value: unknown): value is { value: string } {
  return (
    !!value
    && typeof value === 'object'
    && typeof (value as { value?: unknown }).value === 'string'
    && (value as { value: string }).value.startsWith('#')
  )
}

function createFormulaCellModel(
  formula: string,
  calculatedValue: unknown,
  config: SheetColumnConfig | ColumnSettingsDraft | undefined,
): FormulaCellModel {
  // Excel separates Formula, Value/Value2, and Text. JavaScript has no VBA
  // Date/Currency coercion layer, so Value and Value2 are equivalent for now.
  return {
    formula,
    value: calculatedValue,
    value2: calculatedValue,
    text: formatCellDisplayValueCached(calculatedValue, config),
  }
}

function getDisplayFormatCacheKey(config: SheetColumnConfig | ColumnSettingsDraft | undefined) {
  return [
    config?.value_type ?? 'text',
    config?.display_format ?? '',
  ].join(':')
}

function formatCellDisplayValueCached(
  value: unknown,
  config: SheetColumnConfig | ColumnSettingsDraft | undefined,
) {
  const normalizedValue = normalizeCellValue(value)
  const cacheKey = `${getDisplayFormatCacheKey(config)}:${normalizedValue}`
  const cached = cellDisplayTextCache.get(cacheKey)
  if (cached != null) {
    return cached
  }

  const formattedValue = formatCellDisplayValue(normalizedValue, config)
  if (cellDisplayTextCache.size > 5000) {
    cellDisplayTextCache.clear()
  }
  cellDisplayTextCache.set(cacheKey, formattedValue)
  return formattedValue
}

function normalizeRegexFlags(value: unknown) {
  const rawFlags = normalizeCellValue(value).trim()
  const allowedFlags = new Set(['d', 'g', 'i', 'm', 's', 'u', 'v', 'y'])
  let flags = ''
  for (const flag of rawFlags) {
    if (!allowedFlags.has(flag) || flags.includes(flag)) {
      continue
    }
    flags += flag
  }
  return flags
}

function buildGlobalRegexFlags(value: unknown) {
  const flags = normalizeRegexFlags(value)
  return flags.includes('g') ? flags : `${flags}g`
}

function normalizePythonRegexReplacement(value: unknown) {
  return normalizeCellValue(value)
    .replace(/\\g<(\d+)>/g, '$$$1')
    .replace(/\\([1-9]\d*)/g, '$$$1')
}

function replaceRegexWithCount(text: string, regex: RegExp, replacement: string, count: number) {
  if (count <= 0) {
    return text.replace(regex, replacement)
  }

  let replacedCount = 0
  return text.replace(regex, (...args: unknown[]) => {
    if (replacedCount >= count) {
      return normalizeCellValue(args[0])
    }
    replacedCount += 1
    return replacement.replace(/\$(\d+)/g, (_match, groupIndex: string) => (
      normalizeCellValue(args[Number(groupIndex)] ?? '')
    ))
  })
}

type FormulaDateParts = {
  year: number
  month: number
  day: number
  hour?: number
  minute?: number
  second?: number
  timePrecision?: 'minute' | 'second'
}

function normalizeDateParsePattern(value: unknown) {
  const pattern = normalizeCellValue(value).trim().toLowerCase().replace(/[^ymd]/g, '')
  return pattern || 'yyyymmdd'
}

function normalizeTwoDigitYear(value: number) {
  return value >= 70 ? 1900 + value : 2000 + value
}

function isValidDateParts(parts: FormulaDateParts) {
  if (
    !Number.isInteger(parts.year)
    || !Number.isInteger(parts.month)
    || !Number.isInteger(parts.day)
    || parts.year < 1
    || parts.month < 1
    || parts.month > 12
    || parts.day < 1
    || parts.day > 31
  ) {
    return false
  }
  const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day))
  return (
    date.getUTCFullYear() === parts.year
    && date.getUTCMonth() === parts.month - 1
    && date.getUTCDate() === parts.day
  )
}

function hasFormulaTimeParts(parts: FormulaDateParts) {
  return Number.isInteger(parts.hour) && Number.isInteger(parts.minute)
}

function isValidFormulaTimeParts(parts: FormulaDateParts) {
  if (!hasFormulaTimeParts(parts)) {
    return true
  }
  const hour = parts.hour ?? 0
  const minute = parts.minute ?? 0
  const second = parts.second ?? 0
  return (
    hour >= 0
    && hour <= 23
    && minute >= 0
    && minute <= 59
    && Number.isInteger(second)
    && second >= 0
    && second <= 59
  )
}

function parseOptionalFormulaTimeParts(hourValue: string | undefined, minuteValue: string | undefined, secondValue: string | undefined) {
  if (hourValue == null) {
    return {}
  }

  const hour = Number(hourValue)
  const minute = minuteValue == null ? 0 : Number(minuteValue)
  const second = secondValue == null ? 0 : Number(secondValue)
  const parts: Partial<FormulaDateParts> = {
    hour,
    minute,
    second,
    timePrecision: secondValue == null ? 'minute' : 'second',
  }
  return isValidFormulaTimeParts(parts as FormulaDateParts) ? parts : null
}

function formulaDatePartsToSerial(parts: FormulaDateParts) {
  const dateSerial = Math.floor(Date.UTC(parts.year, parts.month - 1, parts.day) / MS_PER_DAY) + EXCEL_DATE_UNIX_EPOCH_SERIAL
  if (!hasFormulaTimeParts(parts)) {
    return dateSerial
  }

  const seconds = (parts.hour ?? 0) * 60 * 60 + (parts.minute ?? 0) * 60 + (parts.second ?? 0)
  return dateSerial + seconds / (24 * 60 * 60)
}

function formulaDateSerialToParts(value: number): FormulaDateParts | null {
  if (!Number.isFinite(value)) {
    return null
  }

  const date = new Date(Math.round((value - EXCEL_DATE_UNIX_EPOCH_SERIAL) * MS_PER_DAY))
  const parts = {
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
    day: date.getUTCDate(),
    hour: date.getUTCHours(),
    minute: date.getUTCMinutes(),
    second: date.getUTCSeconds(),
  }
  if (!parts.hour && !parts.minute && !parts.second) {
    delete (parts as Partial<FormulaDateParts>).hour
    delete (parts as Partial<FormulaDateParts>).minute
    delete (parts as Partial<FormulaDateParts>).second
  } else {
    ;(parts as FormulaDateParts).timePrecision = 'second'
  }
  return isValidDateParts(parts) && isValidFormulaTimeParts(parts) ? parts : null
}

function parseCompactFormulaDate(value: unknown, patternValue: unknown) {
  const text = normalizeCellValue(value)
  const pattern = normalizeDateParsePattern(patternValue)
  let parts: FormulaDateParts | null = null

  if (pattern === 'yyyymmdd' || pattern === 'yymmdd') {
    const eightDigitMatch = text.match(/\d{8}/)
    if (eightDigitMatch) {
      const digits = eightDigitMatch[0]
      parts = {
        year: Number(digits.slice(0, 4)),
        month: Number(digits.slice(4, 6)),
        day: Number(digits.slice(6, 8)),
      }
    } else {
      const sixDigitMatch = text.match(/\d{6}/)
      if (sixDigitMatch) {
        const digits = sixDigitMatch[0]
        parts = {
          year: normalizeTwoDigitYear(Number(digits.slice(0, 2))),
          month: Number(digits.slice(2, 4)),
          day: Number(digits.slice(4, 6)),
        }
      }
    }
  }

  if (!parts || !isValidDateParts(parts)) {
    return null
  }
  return parts
}

function padDatePart(value: number) {
  return String(value).padStart(2, '0')
}

function formatFormulaTime(parts: FormulaDateParts) {
  if (!hasFormulaTimeParts(parts)) {
    return ''
  }
  const hour = padDatePart(parts.hour ?? 0)
  const minute = padDatePart(parts.minute ?? 0)
  const second = padDatePart(parts.second ?? 0)
  return parts.timePrecision === 'minute' && second === '00'
    ? `${hour}:${minute}`
    : `${hour}:${minute}:${second}`
}

function isMinuteFormatToken(format: string, index: number, tokenLength: number) {
  return format[index - 1] === ':' || format[index + tokenLength] === ':'
}

function readFormulaDateFormatToken(format: string, index: number, parts: FormulaDateParts) {
  const rest = format.slice(index).toLowerCase()
  if (rest.startsWith('yyyy')) {
    return { length: 4, value: String(parts.year), isTime: false }
  }
  if (rest.startsWith('yy')) {
    return { length: 2, value: padDatePart(parts.year % 100), isTime: false }
  }
  if (rest.startsWith('hh')) {
    return { length: 2, value: padDatePart(parts.hour ?? 0), isTime: true }
  }
  if (rest.startsWith('h')) {
    return { length: 1, value: String(parts.hour ?? 0), isTime: true }
  }
  if (rest.startsWith('ss')) {
    return { length: 2, value: padDatePart(parts.second ?? 0), isTime: true }
  }
  if (rest.startsWith('s')) {
    return { length: 1, value: String(parts.second ?? 0), isTime: true }
  }
  if (rest.startsWith('mm')) {
    const isTime = isMinuteFormatToken(format, index, 2)
    return {
      length: 2,
      value: padDatePart(isTime ? (parts.minute ?? 0) : parts.month),
      isTime,
    }
  }
  if (rest.startsWith('m')) {
    const isTime = isMinuteFormatToken(format, index, 1)
    return {
      length: 1,
      value: String(isTime ? (parts.minute ?? 0) : parts.month),
      isTime,
    }
  }
  if (rest.startsWith('dd')) {
    return { length: 2, value: padDatePart(parts.day), isTime: false }
  }
  if (rest.startsWith('d')) {
    return { length: 1, value: String(parts.day), isTime: false }
  }
  return null
}

function formatFormulaDate(parts: FormulaDateParts, formatValue: unknown, options: { appendSourceTime?: boolean } = {}) {
  const format = normalizeCellValue(formatValue).trim() || DEFAULT_DATE_DISPLAY_FORMAT
  let result = ''
  let hasTimeToken = false

  for (let index = 0; index < format.length;) {
    const token = readFormulaDateFormatToken(format, index, parts)
    if (token) {
      result += token.value
      hasTimeToken ||= token.isTime
      index += token.length
    } else {
      result += format[index]
      index += 1
    }
  }

  const sourceTime = formatFormulaTime(parts)
  return options.appendSourceTime && sourceTime && !hasTimeToken
    ? `${result} ${sourceTime}`
    : result
}

function splitDisplayFormatArguments(value: string) {
  const args: string[] = []
  let current = ''
  let depth = 0
  let quoteEnd = ''
  let quoteStart = ''

  for (let index = 0; index < value.length; index += 1) {
    const char = value[index]
    if (quoteEnd) {
      current += char
      if (char === quoteEnd) {
        if ((quoteStart === '"' || quoteStart === '\'') && value[index + 1] === quoteEnd) {
          current += value[index + 1]
          index += 1
          continue
        }
        quoteEnd = ''
        quoteStart = ''
      }
      continue
    }

    if (isFormulaStringQuote(char)) {
      quoteStart = char
      quoteEnd = getFormulaStringEndQuote(char)
      current += char
      continue
    }

    if (char === '(' || char === '（') {
      depth += 1
      current += char
      continue
    }
    if (char === ')' || char === '）') {
      depth = Math.max(0, depth - 1)
      current += char
      continue
    }
    if ((char === ',' || char === '，') && depth === 0) {
      args.push(current.trim())
      current = ''
      continue
    }
    current += char
  }

  args.push(current.trim())
  return args
}

function readDisplayFormatCaseArguments(value: string) {
  const trimmed = value.trim()
  const match = trimmed.match(/^case\s*\(/i)
  if (!match) {
    return null
  }

  const openIndex = match[0].lastIndexOf('(')
  let depth = 0
  let quoteEnd = ''
  let quoteStart = ''
  for (let index = openIndex; index < trimmed.length; index += 1) {
    const char = trimmed[index]
    if (quoteEnd) {
      if (char === quoteEnd) {
        if ((quoteStart === '"' || quoteStart === '\'') && trimmed[index + 1] === quoteEnd) {
          index += 1
          continue
        }
        quoteEnd = ''
        quoteStart = ''
      }
      continue
    }
    if (isFormulaStringQuote(char)) {
      quoteStart = char
      quoteEnd = getFormulaStringEndQuote(char)
      continue
    }
    if (char === '(' || char === '（') {
      depth += 1
      continue
    }
    if (char === ')' || char === '）') {
      depth -= 1
      if (depth === 0) {
        return trimmed.slice(index + 1).trim()
          ? null
          : splitDisplayFormatArguments(trimmed.slice(openIndex + 1, index))
      }
    }
  }
  return null
}

function unquoteDisplayFormatArgument(value: string) {
  const trimmed = value.trim()
  if (trimmed.length < 2) {
    return trimmed
  }

  const start = trimmed[0]
  const end = trimmed[trimmed.length - 1]
  if ((start === '"' && end === '"') || (start === '\'' && end === '\'')) {
    return trimmed.slice(1, -1).replace(new RegExp(`${start}${start}`, 'g'), start)
  }
  if ((start === '“' && end === '”') || (start === '‘' && end === '’')) {
    return trimmed.slice(1, -1)
  }
  return trimmed
}

function getNumericDisplayFormatValue(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  const text = normalizeCellValue(value).trim()
  return /^[-+]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(text) ? Number(text) : null
}

function evaluateDisplayFormatCaseCondition(
  condition: string,
  value: unknown,
  context: { valueType: ColumnValueType; dateParts: FormulaDateParts | null },
) {
  const normalized = condition.trim().toLowerCase().replace(/\s+/g, '')
  if (!normalized) {
    return false
  }

  if (normalized === 'is_current_year') {
    return !!context.dateParts && context.dateParts.year === new Date().getFullYear()
  }

  const comparisonMatch = normalized.match(/^(>=|<=|==|=|>|<|!=|<>)([-+]?(?:\d+(?:\.\d+)?|\.\d+))$/)
  if (comparisonMatch) {
    const numericValue = getNumericDisplayFormatValue(value)
    if (numericValue == null) {
      return false
    }
    const target = Number(comparisonMatch[2])
    switch (comparisonMatch[1]) {
      case '>':
        return numericValue > target
      case '<':
        return numericValue < target
      case '>=':
        return numericValue >= target
      case '<=':
        return numericValue <= target
      case '!=':
      case '<>':
        return numericValue !== target
      default:
        return numericValue === target
    }
  }

  return false
}

function resolveDisplayFormatPattern(
  displayFormat: string,
  value: unknown,
  context: { valueType: ColumnValueType; dateParts: FormulaDateParts | null },
) {
  const normalizedFormat = normalizeCellValue(displayFormat).trim()
  const caseArgs = readDisplayFormatCaseArguments(normalizedFormat)
  if (!caseArgs || caseArgs.length < 3) {
    return normalizedFormat
  }

  const hasDefault = caseArgs.length % 2 === 1
  const conditionArgCount = hasDefault ? caseArgs.length - 1 : caseArgs.length
  for (let index = 0; index + 1 < conditionArgCount; index += 2) {
    if (evaluateDisplayFormatCaseCondition(caseArgs[index], value, context)) {
      return unquoteDisplayFormatArgument(caseArgs[index + 1])
    }
  }

  return hasDefault ? unquoteDisplayFormatArgument(caseArgs[caseArgs.length - 1]) : ''
}

function parseDateDisplayValue(value: unknown): FormulaDateParts | null {
  if (typeof value === 'number') {
    return formulaDateSerialToParts(value)
  }

  const text = normalizeCellValue(value).trim()
  if (!text) {
    return null
  }

  if (/^-?\d+(?:\.\d+)?$/.test(text)) {
    const serialParts = formulaDateSerialToParts(Number(text))
    if (serialParts) {
      return serialParts
    }
  }

  const separatedMatch = text.match(/^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:[ T]+(\d{1,2})(?::(\d{1,2})(?::(\d{1,2}))?)?)?$/)
  if (separatedMatch) {
    const timeParts = parseOptionalFormulaTimeParts(separatedMatch[4], separatedMatch[5], separatedMatch[6])
    if (!timeParts) {
      return null
    }
    const parts = {
      year: Number(separatedMatch[1]),
      month: Number(separatedMatch[2]),
      day: Number(separatedMatch[3]),
      ...timeParts,
    }
    return isValidDateParts(parts) && isValidFormulaTimeParts(parts) ? parts : null
  }

  const compactMatch = text.match(/^(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?)?$/)
  if (compactMatch) {
    const timeParts = parseOptionalFormulaTimeParts(compactMatch[4], compactMatch[5], compactMatch[6])
    if (!timeParts) {
      return null
    }
    const parts = {
      year: Number(compactMatch[1]),
      month: Number(compactMatch[2]),
      day: Number(compactMatch[3]),
      ...timeParts,
    }
    return isValidDateParts(parts) && isValidFormulaTimeParts(parts) ? parts : null
  }

  return null
}

function formatBasicDisplayValue(value: unknown) {
  if (value == null) {
    return ''
  }
  if (isFormulaErrorValue(value)) {
    return value.value
  }
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(value) : '#NUM!'
  }
  if (typeof value === 'boolean') {
    return value ? 'TRUE' : 'FALSE'
  }
  return normalizeCellValue(value)
}

function parsePercentDisplayNumber(value: unknown) {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null
  }
  if (isFormulaErrorValue(value)) {
    return null
  }

  const text = normalizeCellValue(value).trim()
  if (!text) {
    return null
  }

  const percentMatch = text.match(/^([-+]?(?:\d+(?:\.\d+)?|\.\d+))%$/)
  if (percentMatch) {
    return Number(percentMatch[1]) / 100
  }

  if (/^[-+]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(text)) {
    return Number(text)
  }
  return null
}

function getPercentDisplayDecimalPlaces(displayFormat: string) {
  const normalized = normalizeCellValue(displayFormat).trim() || DEFAULT_PERCENT_DISPLAY_FORMAT
  const match = normalized.match(/\.(0+)%$/)
  return match ? match[1].length : 0
}

function formatPercentDisplayValue(value: unknown, displayFormat: string) {
  if (value == null || normalizeCellValue(value).trim() === '') {
    return ''
  }
  if (isFormulaErrorValue(value)) {
    return value.value
  }

  const numberValue = parsePercentDisplayNumber(value)
  if (numberValue == null) {
    return formatBasicDisplayValue(value)
  }

  const decimalPlaces = getPercentDisplayDecimalPlaces(displayFormat)
  const percentValue = numberValue * 100
  const rounded = Math.abs(percentValue) < 0.5 / (10 ** decimalPlaces)
    ? 0
    : percentValue
  return `${rounded.toFixed(decimalPlaces)}%`
}

function formatDateEditText(value: unknown) {
  const parts = parseDateDisplayValue(value)
  return parts
    ? formatFormulaDate(parts, hasFormulaTimeParts(parts)
      ? (parts.timePrecision === 'minute' ? 'yyyy/mm/dd hh:mm' : 'yyyy/mm/dd hh:mm:ss')
      : 'yyyy/mm/dd')
    : normalizeCellValue(value)
}

function normalizeDateInputValue(value: string) {
  const parts = parseDateDisplayValue(value)
  return parts ? String(formulaDatePartsToSerial(parts)) : value
}

function normalizePercentInputValue(value: string) {
  const numberValue = parsePercentDisplayNumber(value)
  return numberValue == null ? value : String(numberValue)
}

function formatCellDisplayValue(value: unknown, config: SheetColumnConfig | ColumnSettingsDraft | undefined) {
  const columnConfig = normalizeColumnConfig(config)
  if (columnConfig.value_type === 'date') {
    if (value == null || normalizeCellValue(value).trim() === '') {
      return ''
    }
    if (isFormulaErrorValue(value)) {
      return value.value
    }

    const parts = parseDateDisplayValue(value)
    if (parts) {
      const displayPattern = resolveDisplayFormatPattern(columnConfig.display_format, value, {
        valueType: columnConfig.value_type,
        dateParts: parts,
      })
      return formatFormulaDate(parts, displayPattern, { appendSourceTime: true })
    }
  }
  if (columnConfig.value_type === 'percent') {
    return formatPercentDisplayValue(value, columnConfig.display_format)
  }

  return formatBasicDisplayValue(value)
}

function registerSheetFormulaPlugins(module: unknown) {
  if (sheetFormulaPluginRegistered || !module || typeof module !== 'object') {
    return
  }

  const formulaModule = module as Record<string, unknown>
  const HyperFormula = formulaModule.HyperFormula as FormulaEngineClass | undefined
  const hasRegisteredSheetFormulas = (
    !!HyperFormula?.getFunctionPlugin?.('RE_SUB')
    && !!HyperFormula.getFunctionPlugin?.('DATE_PARSE')
  )
  if (!HyperFormula?.registerFunctionPlugin || hasRegisteredSheetFormulas) {
    sheetFormulaPluginRegistered = true
    return
  }

  const FunctionPlugin = formulaModule.FunctionPlugin as new (...args: unknown[]) => {
    runFunction: (
      args: unknown[],
      state: unknown,
      metadata: unknown,
      implementation: (...args: unknown[]) => unknown,
    ) => unknown
    metadata: (name: string) => unknown
  }
  const FunctionArgumentType = formulaModule.FunctionArgumentType as Record<string, unknown> | undefined
  const CellError = formulaModule.CellError as (new (type: unknown, message?: string) => unknown) | undefined
  const ErrorType = formulaModule.ErrorType as Record<string, unknown> | undefined
  if (!FunctionPlugin || !FunctionArgumentType?.STRING || !FunctionArgumentType?.NUMBER || !CellError || !ErrorType?.VALUE) {
    return
  }

  class SheetFormulaPlugin extends FunctionPlugin {
    static implementedFunctions = {
      RE_SUB: {
        method: 'reSub',
        parameters: [
          { argumentType: FunctionArgumentType.STRING },
          { argumentType: FunctionArgumentType.STRING },
          { argumentType: FunctionArgumentType.STRING },
          { argumentType: FunctionArgumentType.NUMBER, defaultValue: 0 },
          { argumentType: FunctionArgumentType.STRING, defaultValue: '' },
        ],
      },
      DATE_PARSE: {
        method: 'dateParse',
        parameters: [
          { argumentType: FunctionArgumentType.STRING },
          { argumentType: FunctionArgumentType.STRING, defaultValue: 'yyyymmdd' },
          { argumentType: FunctionArgumentType.STRING, defaultValue: '' },
        ],
      },
    }

    reSub(ast: { args: unknown[] }, state: unknown) {
      return this.runFunction(
        ast.args,
        state,
        this.metadata('RE_SUB'),
        (pattern: string, replacement: string, text: string, count = 0, flags = '') => {
          try {
            const regex = new RegExp(pattern, buildGlobalRegexFlags(flags))
            return replaceRegexWithCount(
              text,
              regex,
              normalizePythonRegexReplacement(replacement),
              Math.max(0, Math.floor(Number(count) || 0)),
            )
          } catch (error) {
            return new CellError(ErrorType.VALUE, error instanceof Error ? error.message : 'Invalid regular expression')
          }
        },
      )
    }

    dateParse(ast: { args: unknown[] }, state: unknown) {
      return this.runFunction(
        ast.args,
        state,
        this.metadata('DATE_PARSE'),
        (text: string, pattern = 'yyyymmdd') => {
          const parts = parseCompactFormulaDate(text, pattern)
          if (!parts) {
            return new CellError(ErrorType.VALUE, 'Invalid date text')
          }
          return formulaDatePartsToSerial(parts)
        },
      )
    }
  }

  HyperFormula.registerFunctionPlugin(SheetFormulaPlugin, {
    enGB: {
      RE_SUB: 'RE_SUB',
      DATE_PARSE: 'DATE_PARSE',
    },
  })
  sheetFormulaPluginRegistered = true
}

async function ensureFormulaEngineLoaded() {
  if (formulaEngineClass.value) {
    return formulaEngineClass.value
  }

  if (!formulaEngineImportPromise) {
    formulaEngineImportPromise = import('hyperformula')
      .then((module) => {
        registerSheetFormulaPlugins(module)
        formulaEngineClass.value = module.HyperFormula as FormulaEngineClass
        refreshFormulaDisplayState()
        getHotInstance()?.render()
        refreshAdaptiveFormulaColumnWidths()
        return formulaEngineClass.value
      })
      .catch((error) => {
        formulaEngineImportPromise = null
        console.warn('Failed to load sheet formula engine', error)
        return null
      })
  }

  return formulaEngineImportPromise
}

function createEmptyFormulaDisplayState(): FormulaDisplayState {
  return {
    cells: [],
    errorKeys: new Set(),
  }
}

function buildFormulaDisplayStateForRows(
  headers: string[],
  sourceRows: SheetRow[],
  sourceConfigs: Record<string, SheetColumnConfig> = columnConfigs.value,
): FormulaDisplayState {
  const normalizedRows = sourceRows.map((row) => normalizeRow(row, headers))
  const formulaCells: Array<{ row: number; column: number }> = []

  normalizedRows.forEach((row, rowIndex) => {
    row.forEach((cellValue, columnIndex) => {
      if (isFormulaExpression(cellValue)) {
        formulaCells.push({ row: rowIndex, column: columnIndex })
      }
    })
  })

  if (!formulaCells.length) {
    return {
      cells: [],
      errorKeys: new Set(),
    }
  }

  const FormulaEngine = formulaEngineClass.value
  if (!FormulaEngine) {
    void ensureFormulaEngineLoaded()
    return {
      cells: [],
      errorKeys: new Set(),
    }
  }

  const engineRows = normalizedRows.map((row) => row.map((cellValue) => (
    isFormulaExpression(cellValue)
      ? normalizeFormulaExpressionForEngine(cellValue)
      : cellValue
  )))
  const cells = normalizedRows.map((row) => row.map(() => null as FormulaCellModel | null))
  const errorKeys = new Set<string>()
  let engine: FormulaEngineInstance | null = null
  try {
    engine = FormulaEngine.buildFromArray(engineRows, { licenseKey: 'gpl-v3' })
    formulaCells.forEach(({ row, column }) => {
      const calculatedValue = engine?.getCellValue({ sheet: 0, row, col: column })
      cells[row][column] = createFormulaCellModel(
        normalizedRows[row]?.[column] ?? '',
        calculatedValue,
        sourceConfigs[headers[column] ?? ''],
      )
      if (isFormulaErrorValue(calculatedValue)) {
        errorKeys.add(createCellMetaKey(row, column))
      }
    })
  } catch (error) {
    console.warn('Failed to evaluate sheet formulas', error)
    formulaCells.forEach(({ row, column }) => {
      cells[row][column] = {
        formula: normalizedRows[row]?.[column] ?? '',
        value: '#ERROR!',
        value2: '#ERROR!',
        text: '#ERROR!',
      }
      errorKeys.add(createCellMetaKey(row, column))
    })
  } finally {
    engine?.destroy()
  }

  return { cells, errorKeys }
}

function buildFormulaDisplayState(): FormulaDisplayState {
  return buildFormulaDisplayStateForRows(normalizeHeaders(columnHeaders.value), rows.value, columnConfigs.value)
}

function refreshFormulaDisplayState() {
  formulaDisplayState.value = buildFormulaDisplayState()
  return formulaDisplayState.value
}

function getFormulaDisplayTextFromState(
  displayState: FormulaDisplayState | null,
  rowIndex: number,
  columnIndex: number,
) {
  return displayState?.cells[rowIndex]?.[columnIndex]?.text ?? ''
}

function getFormulaCellModel(rowIndex: number, columnIndex: number) {
  if (rowIndex < 0 || columnIndex < 0) {
    return null
  }
  const row = rows.value[rowIndex]
  if (!row || !isFormulaExpression(row[columnIndex])) {
    return null
  }
  return formulaDisplayState.value.cells[rowIndex]?.[columnIndex] ?? null
}

function getColumnConfigByIndex(
  columnIndex: number,
  headers = columnHeaders.value,
  sourceConfigs: Record<string, SheetColumnConfig> = columnConfigs.value,
) {
  return sourceConfigs[headers[columnIndex] ?? '']
}

function getCellDisplayText(
  rowIndex: number,
  columnIndex: number,
  rawValue: unknown,
  displayState?: FormulaDisplayState | null,
  headers = columnHeaders.value,
  sourceConfigs: Record<string, SheetColumnConfig> = columnConfigs.value,
) {
  const normalizedValue = normalizeCellValue(rawValue)
  if (isFormulaExpression(normalizedValue)) {
    const state = displayState === undefined ? formulaDisplayState.value : displayState
    return getFormulaDisplayTextFromState(state, rowIndex, columnIndex)
  }
  return formatCellDisplayValueCached(normalizedValue, getColumnConfigByIndex(columnIndex, headers, sourceConfigs))
}

function getCellSemanticValue(rowIndex: number, columnIndex: number, rawValue: unknown) {
  const normalizedValue = normalizeCellValue(rawValue)
  if (!isFormulaExpression(normalizedValue)) {
    return normalizedValue
  }
  return getFormulaCellModel(rowIndex, columnIndex)?.value2 ?? ''
}

function getCellTextForLayout(rowIndex: number, columnIndex: number, rawValue: unknown) {
  return getCellDisplayText(rowIndex, columnIndex, rawValue)
}

function getExcelColumnIndex(label: string) {
  let index = 0
  const normalizedLabel = label.toUpperCase()
  for (const char of normalizedLabel) {
    const charCode = char.charCodeAt(0)
    if (charCode < 65 || charCode > 90) {
      return null
    }
    index = index * 26 + charCode - 64
  }
  return index - 1
}

function applyFormulaReferenceColumnCase(label: string, sourceLabel: string) {
  return sourceLabel === sourceLabel.toLowerCase() ? label.toLowerCase() : label
}

function shiftFormulaCellReferences(formula: string, rowDelta: number, columnDelta: number) {
  if (!isFormulaExpression(formula) || (!rowDelta && !columnDelta)) {
    return formula
  }

  return formula.replace(
    FORMULA_CELL_REFERENCE_RE,
    (
      _match,
      prefix: string,
      columnAbsoluteMarker: string,
      columnLabel: string,
      rowAbsoluteMarker: string,
      rowLabel: string,
    ) => {
      const sourceColumnIndex = getExcelColumnIndex(columnLabel)
      const sourceRowNumber = Number(rowLabel)
      if (sourceColumnIndex == null || !Number.isInteger(sourceRowNumber) || sourceRowNumber < 1) {
        return `${prefix}#REF!`
      }

      const nextColumnIndex = columnAbsoluteMarker ? sourceColumnIndex : sourceColumnIndex + columnDelta
      const nextRowNumber = rowAbsoluteMarker ? sourceRowNumber : sourceRowNumber + rowDelta
      if (nextColumnIndex < 0 || nextRowNumber < 1) {
        return `${prefix}#REF!`
      }

      const nextColumnLabel = applyFormulaReferenceColumnCase(getExcelColumnLabel(nextColumnIndex), columnLabel)
      return `${prefix}${columnAbsoluteMarker}${nextColumnLabel}${rowAbsoluteMarker}${nextRowNumber}`
    },
  )
}

function remapFormulaCellReferences(
  value: unknown,
  rowIndexMapper?: (rowIndex: number) => number | null,
  columnIndexMapper?: (columnIndex: number) => number | null,
) {
  const formula = normalizeCellValue(value)
  if (!isFormulaExpression(formula)) {
    return formula
  }

  return formula.replace(
    FORMULA_CELL_REFERENCE_RE,
    (
      _match,
      prefix: string,
      columnAbsoluteMarker: string,
      columnLabel: string,
      rowAbsoluteMarker: string,
      rowLabel: string,
    ) => {
      const sourceColumnIndex = getExcelColumnIndex(columnLabel)
      const sourceRowNumber = Number(rowLabel)
      if (sourceColumnIndex == null || !Number.isInteger(sourceRowNumber) || sourceRowNumber < 1) {
        return `${prefix}#REF!`
      }

      const sourceRowIndex = sourceRowNumber - 1
      const nextColumnIndex = columnIndexMapper ? columnIndexMapper(sourceColumnIndex) : sourceColumnIndex
      const nextRowIndex = rowIndexMapper ? rowIndexMapper(sourceRowIndex) : sourceRowIndex
      if (nextColumnIndex == null || nextRowIndex == null || nextColumnIndex < 0 || nextRowIndex < 0) {
        return `${prefix}#REF!`
      }

      const nextColumnLabel = applyFormulaReferenceColumnCase(getExcelColumnLabel(nextColumnIndex), columnLabel)
      return `${prefix}${columnAbsoluteMarker}${nextColumnLabel}${rowAbsoluteMarker}${nextRowIndex + 1}`
    },
  )
}

function normalizeAutofillRange(range: SheetAutofillRange) {
  const topStart = range.getTopStartCorner()
  const bottomEnd = range.getBottomEndCorner()
  const startRow = Math.min(topStart.row, bottomEnd.row)
  const endRow = Math.max(topStart.row, bottomEnd.row)
  const startColumn = Math.min(topStart.col, bottomEnd.col)
  const endColumn = Math.max(topStart.col, bottomEnd.col)
  return {
    startRow,
    endRow,
    startColumn,
    endColumn,
    rowCount: endRow - startRow + 1,
    columnCount: endColumn - startColumn + 1,
  }
}

function getAutofillSourceOffset(
  targetOffset: number,
  targetLength: number,
  sourceLength: number,
  reverse: boolean,
) {
  if (sourceLength <= 0) {
    return 0
  }
  if (!reverse) {
    return targetOffset % sourceLength
  }

  const fillOffset = targetLength % sourceLength
  return (targetOffset + sourceLength - fillOffset) % sourceLength
}

function buildFormulaAutofillData(
  sourceRange: SheetAutofillRange,
  targetRange: SheetAutofillRange,
  direction: SheetAutofillDirection,
) {
  const sourceBounds = normalizeAutofillRange(sourceRange)
  const targetBounds = normalizeAutofillRange(targetRange)
  if (
    sourceBounds.rowCount <= 0
    || sourceBounds.columnCount <= 0
    || targetBounds.rowCount <= 0
    || targetBounds.columnCount <= 0
  ) {
    return null
  }

  const sourceHasFormula = Array.from({ length: sourceBounds.rowCount }).some((_, rowOffset) => (
    Array.from({ length: sourceBounds.columnCount }).some((__, columnOffset) => (
      isFormulaExpression(getRawCellValue(
        sourceBounds.startRow + rowOffset,
        sourceBounds.startColumn + columnOffset,
      ))
    ))
  ))
  if (!sourceHasFormula) {
    return null
  }

  return Array.from({ length: targetBounds.rowCount }, (_, targetRowOffset) => (
    Array.from({ length: targetBounds.columnCount }, (__, targetColumnOffset) => {
      const sourceRowOffset = getAutofillSourceOffset(
        targetRowOffset,
        targetBounds.rowCount,
        sourceBounds.rowCount,
        direction === 'up',
      )
      const sourceColumnOffset = getAutofillSourceOffset(
        targetColumnOffset,
        targetBounds.columnCount,
        sourceBounds.columnCount,
        direction === 'left',
      )
      const sourceRow = sourceBounds.startRow + sourceRowOffset
      const sourceColumn = sourceBounds.startColumn + sourceColumnOffset
      const targetRow = targetBounds.startRow + targetRowOffset
      const targetColumn = targetBounds.startColumn + targetColumnOffset
      const sourceValue = getRawCellValue(sourceRow, sourceColumn)
      if (!isFormulaExpression(sourceValue)) {
        return sourceValue
      }
      return shiftFormulaCellReferences(sourceValue, targetRow - sourceRow, targetColumn - sourceColumn)
    })
  ))
}

function setRenderedCellText(TD: HTMLTableCellElement, text: string) {
  const ellipsisElement = TD.querySelector('.htTextEllipsis') as HTMLElement | null
  if (ellipsisElement) {
    if (ellipsisElement.textContent !== text) {
      ellipsisElement.textContent = text
    }
    return
  }
  if (TD.textContent !== text) {
    TD.textContent = text
  }
}

function areDefaultSheetHeaders(headers: string[]) {
  return (
    headers.length === DEFAULT_SHEET_COLUMNS.length
    && headers.every((header, index) => header === DEFAULT_SHEET_COLUMNS[index])
  )
}

function hasMeaningfulSheetDocumentContent(document: SheetDocument) {
  const headers = normalizeHeaders(document.columns)
  const normalizedRows = trimTrailingBlankRows(document.rows.map((row) => normalizeRow(row, headers)))
  if (normalizedRows.length > 0 || !areDefaultSheetHeaders(headers)) {
    return true
  }

  return (
    normalizeHeaderGroups(document.header_groups, headers.length).length > 0
    || Object.keys(normalizeCellMetaMap(document.cell_meta, headers.length)).length > 0
    || Object.keys(normalizeColumnConfigs(document.column_configs, headers)).length > 0
  )
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
  if (!canPersistSheet.value) {
    clearDraftStorage()
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
  if (!canPersistSheet.value) {
    clearDraftStorage()
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

function getContextMenuPlugin() {
  return getHotInstance()?.getPlugin('contextMenu') as {
    open?: (
      position: { left: number; top: number } | Event,
      offset?: { above?: number; below?: number; left?: number; right?: number },
    ) => void
  } | null
}

function shouldEnableTouchContextMenuFallback() {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') {
    return false
  }

  const maxTouchPoints = Number(navigator.maxTouchPoints || 0)
  const coarsePointer = window.matchMedia?.('(pointer: coarse)').matches ?? false
  const userAgent = navigator.userAgent || ''
  const isSafari = /Safari/i.test(userAgent)
    && !/(Chrome|Chromium|CriOS|FxiOS|Edg|OPR|Android)/i.test(userAgent)
  const isAppleTouchDevice = /iPad|iPhone|iPod/.test(userAgent)
    || (navigator.platform === 'MacIntel' && maxTouchPoints > 1)
  return coarsePointer || isAppleTouchDevice || isSafari
}

function refreshContextMenuFallbackSelectionState() {
  hasContextMenuFallbackSelection.value = !!getHotInstance()?.getSelectedLast()
}

function openTouchContextMenuFallback(event: MouseEvent | TouchEvent) {
  event.preventDefault()
  event.stopPropagation()

  const hot = getHotInstance()
  const plugin = getContextMenuPlugin()
  if (!hot || !plugin?.open || !hasSelection()) {
    refreshContextMenuFallbackSelectionState()
    return
  }

  const rect = contextMenuFallbackButtonRef.value?.getBoundingClientRect()
  const left = rect ? rect.left : window.innerWidth - 64
  const top = rect ? rect.top : window.innerHeight - 96
  hot.listen()
  plugin.open({
    left: Math.max(12, Math.min(left, window.innerWidth - 12)),
    top: Math.max(12, Math.min(top, window.innerHeight - 12)),
  }, {
    above: 8,
    below: 8,
    left: 8,
    right: 8,
  })
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
  return hasSelection() && (isSelectedByRowHeader() || isSelectedByCorner())
}

function shouldShowColumnActions() {
  return hasSelection() && (isSelectedByColumnHeader() || isSelectedByCorner())
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

function getColumnCellFontFromSize(fontSize: number) {
  if (fontSize === DEFAULT_COLUMN_FONT_SIZE) {
    return TABLE_FONT
  }
  return `400 ${fontSize}px Inter, "Segoe UI", sans-serif`
}

function getColumnFontSizeFromConfig(config: SheetColumnConfig | undefined) {
  return normalizeColumnFontSize(config?.font_size)
}

function getColumnLineHeightFromFontSize(fontSize: number) {
  if (fontSize === DEFAULT_COLUMN_FONT_SIZE) {
    return TABLE_LINE_HEIGHT
  }
  return Math.max(TABLE_LINE_HEIGHT, Math.ceil(fontSize * 1.45))
}

function getAutoColumnWidth(
  columnIndex: number,
  headers = columnHeaders.value,
  sourceRows = rows.value,
  sourceConfigs: Record<string, SheetColumnConfig> = columnConfigs.value,
  sourceFormulaDisplayState?: FormulaDisplayState | null,
) {
  const header = headers[columnIndex] ?? createFallbackHeader(columnIndex)
  const cellFont = getColumnCellFontFromSize(getColumnFontSizeFromConfig(sourceConfigs[header]))
  let width = getAdaptiveColumnWidth(header)
  const sampleLimit = Math.min(sourceRows.length, 40)
  let formulaDisplayForWidths = sourceFormulaDisplayState

  for (let rowIndex = 0; rowIndex < sampleLimit; rowIndex += 1) {
    const rawCellText = normalizeCellValue(sourceRows[rowIndex]?.[columnIndex] ?? '')
    if (isFormulaExpression(rawCellText) && formulaDisplayForWidths === undefined) {
      formulaDisplayForWidths = buildFormulaDisplayStateForRows(headers, sourceRows, sourceConfigs)
    }
    const cellText = getCellDisplayText(
      rowIndex,
      columnIndex,
      rawCellText,
      formulaDisplayForWidths ?? null,
      headers,
      sourceConfigs,
    ).trim()
    if (!cellText) {
      continue
    }
    width = Math.max(
      width,
      Math.min(
        Math.ceil(measureTextWidth(cellText, cellFont)) + HEADER_WIDTH_PADDING,
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

type ColumnWidthUpdateOptions = {
  refreshRowHeights?: boolean
  commitFixedWidth?: boolean
  commitAdaptiveWidth?: boolean
  save?: boolean
}

function setColumnWidthMode(columnIndex: number, widthMode: ColumnWidthMode) {
  if (!canEditConfig.value) {
    return false
  }

  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return false
  }

  const currentNormalizedConfigs = normalizeColumnConfigs(columnConfigs.value, columnHeaders.value)
  const currentConfig = normalizeColumnConfig(currentNormalizedConfigs[header])
  if (currentConfig.width_mode === widthMode) {
    return false
  }

  const nextNormalizedConfigs = { ...currentNormalizedConfigs }
  const nextConfig = {
    ...currentConfig,
    width_mode: widthMode,
  }
  const storedConfig = createStoredColumnConfig(
    nextConfig,
    pickPreservedColumnConfig(currentNormalizedConfigs[header]),
  )
  if (storedConfig) {
    nextNormalizedConfigs[header] = storedConfig
  } else {
    delete nextNormalizedConfigs[header]
  }
  columnConfigs.value = nextNormalizedConfigs
  return true
}

function setColumnWidth(columnIndex: number, width: number, options: ColumnWidthUpdateOptions = {}) {
  if (columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return
  }

  const nextWidth = normalizeColumnWidthValue(width)
  const currentWidth = normalizeColumnWidthValue(columnWidths.value[columnIndex] ?? getEffectiveColumnWidth(columnIndex))
  let changed = false

  if (nextWidth !== currentWidth) {
    const nextWidths = [...columnWidths.value]
    nextWidths[columnIndex] = nextWidth
    columnWidths.value = nextWidths

    const hot = getHotInstance()
    if (hot) {
      hot.updateSettings({
        colWidths: [...nextWidths],
      })
      hot.render()
    }
    scheduleColumnMarkerScrollSync()
    changed = true
  }

  if (options.commitAdaptiveWidth) {
    changed = setColumnWidthMode(columnIndex, 'adaptive') || changed
  } else if (options.commitFixedWidth) {
    changed = setColumnWidthMode(columnIndex, 'fixed') || changed
  }

  if (!changed) {
    return
  }

  if (options.refreshRowHeights) {
    void refreshComputedRowHeights()
  }
  if (options.save) {
    scheduleRemoteSave(0)
  }
}

function columnHasFormulaValue(columnIndex: number, sourceRows = rows.value) {
  return sourceRows.some((row) => isFormulaExpression(normalizeCellValue(row?.[columnIndex] ?? '')))
}

function refreshAdaptiveFormulaColumnWidths() {
  if (!formulaEngineClass.value || !columnHeaders.value.length) {
    return
  }

  const headers = normalizeHeaders(columnHeaders.value)
  const normalizedRows = rows.value.map((row) => normalizeRow(row, headers))
  const normalizedConfigs = normalizeColumnConfigs(columnConfigs.value, headers)
  const formulaDisplayForWidths = buildFormulaDisplayStateForRows(headers, normalizedRows, normalizedConfigs)
  const nextWidths = [...columnWidths.value]
  let changed = false

  headers.forEach((header, columnIndex) => {
    const columnConfig = normalizeColumnConfig(columnConfigs.value[header])
    if (columnConfig.width_mode !== 'adaptive' || !columnHasFormulaValue(columnIndex, normalizedRows)) {
      return
    }

    const nextWidth = getAutoColumnWidth(
      columnIndex,
      headers,
      normalizedRows,
      normalizedConfigs,
      formulaDisplayForWidths,
    )
    if (normalizeColumnWidthValue(nextWidths[columnIndex] ?? MIN_COLUMN_WIDTH) !== nextWidth) {
      nextWidths[columnIndex] = nextWidth
      changed = true
    }
  })

  if (!changed) {
    return
  }

  columnWidths.value = nextWidths
  refreshGridStructure()
  void refreshComputedRowHeights()
}

function getWrappedLineCount(text: string, availableWidth: number, font = TABLE_FONT) {
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

      return sum + Math.max(1, Math.ceil(measureTextWidth(segment, font) / availableWidth))
    }, 0)
}

function resolveRowHeight(rowIndex: number) {
  const layoutState = rowHeightLayoutState.value
  if (!layoutState.hasWrappedColumns) {
    return layoutState.singleLineHeight
  }

  const row = rows.value[rowIndex] ?? []
  let maxContentHeight = layoutState.singleLineHeight - TABLE_CELL_VERTICAL_PADDING * 2 - TABLE_CELL_BORDER_WIDTH

  for (const columnLayout of layoutState.wrappedColumns) {
    const cellText = getCellTextForLayout(rowIndex, columnLayout.index, row[columnLayout.index] ?? '')
    if (!cellText) {
      maxContentHeight = Math.max(maxContentHeight, columnLayout.lineHeight)
      continue
    }

    const availableWidth = Math.max(
      getEffectiveColumnWidth(columnLayout.index) - TABLE_CELL_HORIZONTAL_PADDING * 2,
      12,
    )
    const cellFont = getColumnCellFontFromSize(columnLayout.fontSize)
    maxContentHeight = Math.max(
      maxContentHeight,
      getWrappedLineCount(cellText, availableWidth, cellFont) * columnLayout.lineHeight,
    )
  }

  return maxContentHeight + TABLE_CELL_VERTICAL_PADDING * 2 + TABLE_CELL_BORDER_WIDTH
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
  void nextTick(() => {
    bindGridScrollSync()
  })
}

function handleWindowResize() {
  closeColumnNotePopover()
  void updateSheetViewportHeight()
}

function syncColumnMarkerScroll() {
  const nextElement = gridScrollElement
    ?? sheetFrameRef.value?.querySelector('.ht_master .wtHolder') as HTMLElement | null
  const nextScrollLeft = nextElement?.scrollLeft ?? 0
  sheetScrollLeft.value = nextScrollLeft
  if (columnMarkerContentRef.value) {
    columnMarkerContentRef.value.style.transform = `translate3d(${-nextScrollLeft}px, 0, 0)`
  }
}

function handleGridScroll() {
  syncColumnMarkerScroll()
}

function scheduleColumnMarkerScrollSync() {
  if (columnMarkerScrollFrame != null) {
    return
  }
  columnMarkerScrollFrame = window.requestAnimationFrame(() => {
    columnMarkerScrollFrame = null
    syncColumnMarkerScroll()
  })
}

function unbindGridScrollSync() {
  if (columnMarkerScrollFrame != null) {
    window.cancelAnimationFrame(columnMarkerScrollFrame)
    columnMarkerScrollFrame = null
  }
  gridScrollElement?.removeEventListener('scroll', handleGridScroll)
  gridScrollElement = null
  sheetScrollLeft.value = 0
  if (columnMarkerContentRef.value) {
    columnMarkerContentRef.value.style.transform = 'translate3d(0, 0, 0)'
  }
}

function bindGridScrollSync() {
  const nextElement = sheetFrameRef.value?.querySelector('.ht_master .wtHolder') as HTMLElement | null
  if (nextElement === gridScrollElement) {
    syncColumnMarkerScroll()
    return
  }

  unbindGridScrollSync()
  gridScrollElement = nextElement
  if (gridScrollElement) {
    gridScrollElement.addEventListener('scroll', handleGridScroll, { passive: true })
    syncColumnMarkerScroll()
  }
}

function handleAfterScrollHorizontally() {
  syncColumnMarkerScroll()
}

function captureSheetScrollPosition() {
  const gridElement = gridScrollElement
    ?? sheetFrameRef.value?.querySelector('.ht_master .wtHolder') as HTMLElement | null
  const pageElement = getMainScrollContainer()
  return {
    gridLeft: gridElement?.scrollLeft ?? 0,
    gridTop: gridElement?.scrollTop ?? 0,
    pageLeft: pageElement?.scrollLeft ?? 0,
    pageTop: pageElement?.scrollTop ?? 0,
  }
}

function restoreSheetScrollPosition(position: ReturnType<typeof captureSheetScrollPosition>) {
  void nextTick(() => {
    bindGridScrollSync()
    const gridElement = gridScrollElement
      ?? sheetFrameRef.value?.querySelector('.ht_master .wtHolder') as HTMLElement | null
    const pageElement = getMainScrollContainer()
    if (gridElement) {
      gridElement.scrollLeft = position.gridLeft
      gridElement.scrollTop = position.gridTop
    }
    if (pageElement) {
      pageElement.scrollLeft = position.pageLeft
      pageElement.scrollTop = position.pageTop
    }
    syncColumnMarkerScroll()
  })
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
    rowHeaders: sheetRowHeaders.value,
    hiddenColumns: {
      columns: [...hiddenColumnIndexes.value],
      indicators: false,
    },
  })
  hot.render()
  void nextTick(() => {
    bindGridScrollSync()
  })
}

function cleanupColumnMarkerResize(refreshRowHeights = true) {
  if (columnMarkerResizeState) {
    const { previousUserSelect, previousCursor } = columnMarkerResizeState
    document.body.style.userSelect = previousUserSelect
    document.body.style.cursor = previousCursor
  }
  columnMarkerResizeState = null
  columnMarkerResizingIndex.value = null
  window.removeEventListener('mousemove', handleColumnMarkerResizeMouseMove)
  window.removeEventListener('mouseup', handleColumnMarkerResizeMouseUp)
  window.removeEventListener('blur', handleColumnMarkerResizeWindowBlur)
  if (refreshRowHeights) {
    void refreshComputedRowHeights()
  }
}

function handleColumnMarkerResizeMouseMove(event: MouseEvent) {
  const state = columnMarkerResizeState
  if (!state) {
    return
  }
  event.preventDefault()
  setColumnWidth(state.columnIndex, state.startWidth + event.clientX - state.startX)
}

function handleColumnMarkerResizeMouseUp(event: MouseEvent) {
  const state = columnMarkerResizeState
  if (state) {
    setColumnWidth(state.columnIndex, state.startWidth + event.clientX - state.startX, {
      commitFixedWidth: true,
      refreshRowHeights: true,
      save: true,
    })
  }
  cleanupColumnMarkerResize(false)
}

function handleColumnMarkerResizeWindowBlur() {
  cleanupColumnMarkerResize()
}

function selectColumnMarker(columnIndex: number, extendSelection = false) {
  const hot = getHotInstance()
  if (!hot || columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return false
  }

  clearEditingColumnState()
  closeColumnNotePopover()
  if (formulaBarFocused.value) {
    commitFormulaBarDraft()
  }
  clearFormulaBarSelection()
  const anchor = extendSelection
    ? (columnMarkerSelectionAnchor ?? selectedColumnMarkerBounds.value?.start ?? columnIndex)
    : columnIndex
  const startColumn = Math.min(anchor, columnIndex)
  const endColumn = Math.max(anchor, columnIndex)
  const selected = startColumn === endColumn
    ? hot.selectColumns(startColumn)
    : hot.selectColumns(startColumn, endColumn)
  if (selected) {
    columnMarkerSelectionAnchor = anchor
  }
  hot.listen()
  syncColumnMarkerSelection()
  refreshContextMenuFallbackSelectionState()
  return selected
}

function handleColumnMarkerMouseDown(event: MouseEvent, columnIndex: number) {
  if (event.button !== 0) {
    return
  }

  event.preventDefault()
  event.stopPropagation()
  selectColumnMarker(columnIndex, event.shiftKey)
}

function handleColumnMarkerContextMenu(event: MouseEvent, columnIndex: number) {
  event.preventDefault()
  event.stopPropagation()

  if (!isColumnMarkerSelected(columnIndex) && !selectColumnMarker(columnIndex, event.shiftKey)) {
    return
  }

  getContextMenuPlugin()?.open?.(event)
}

function startColumnMarkerResize(event: MouseEvent, columnIndex: number) {
  if (!ensureCanEditConfig()) {
    return
  }

  if (columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return
  }

  event.preventDefault()
  event.stopPropagation()
  cleanupColumnMarkerResize(false)
  clearEditingColumnState()
  closeColumnNotePopover()

  columnMarkerResizeState = {
    columnIndex,
    startX: event.clientX,
    startWidth: normalizeColumnWidthValue(columnWidths.value[columnIndex] ?? getEffectiveColumnWidth(columnIndex)),
    previousUserSelect: document.body.style.userSelect,
    previousCursor: document.body.style.cursor,
  }
  columnMarkerResizingIndex.value = columnIndex
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
  window.addEventListener('mousemove', handleColumnMarkerResizeMouseMove)
  window.addEventListener('mouseup', handleColumnMarkerResizeMouseUp)
  window.addEventListener('blur', handleColumnMarkerResizeWindowBlur)
}

function autoFitColumnFromMarker(event: MouseEvent, columnIndex: number) {
  event.preventDefault()
  event.stopPropagation()
  if (!ensureCanEditConfig()) {
    return
  }
  cleanupColumnMarkerResize(false)
  setColumnWidth(columnIndex, getAutoColumnWidth(columnIndex), {
    commitAdaptiveWidth: true,
    refreshRowHeights: true,
    save: true,
  })
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

function transformCellMetaPositions(
  source: SheetCellMetaMap,
  mapper: (position: { row: number; column: number }) => { row: number; column: number } | null,
) {
  const nextMeta: SheetCellMetaMap = {}
  for (const [key, meta] of Object.entries(source)) {
    const position = parseCellMetaKey(key)
    if (!position) {
      continue
    }

    const nextPosition = mapper(position)
    if (!nextPosition || nextPosition.row < 0 || nextPosition.column < 0) {
      continue
    }
    nextMeta[createCellMetaKey(nextPosition.row, nextPosition.column)] = meta
  }
  return normalizeCellMetaMap(nextMeta, columnHeaders.value.length)
}

function shiftCellMetaRows(startDocumentRow: number, amount: number) {
  if (amount === 0) {
    return
  }

  cellMeta.value = transformCellMetaPositions(cellMeta.value, ({ row, column }) => ({
    row: row >= startDocumentRow ? row + amount : row,
    column,
  }))
}

function removeCellMetaRows(startDocumentRow: number, amount: number) {
  if (amount <= 0) {
    return
  }
  const endDocumentRow = startDocumentRow + amount

  cellMeta.value = transformCellMetaPositions(cellMeta.value, ({ row, column }) => {
    if (row >= startDocumentRow && row < endDocumentRow) {
      return null
    }
    return {
      row: row >= endDocumentRow ? row - amount : row,
      column,
    }
  })
}

function shiftCellMetaColumns(startColumn: number, amount: number) {
  if (amount === 0) {
    return
  }

  cellMeta.value = transformCellMetaPositions(cellMeta.value, ({ row, column }) => ({
    row,
    column: column >= startColumn ? column + amount : column,
  }))
}

function removeCellMetaColumns(startColumn: number, amount: number) {
  if (amount <= 0) {
    return
  }
  const endColumn = startColumn + amount

  cellMeta.value = transformCellMetaPositions(cellMeta.value, ({ row, column }) => {
    if (column >= startColumn && column < endColumn) {
      return null
    }
    return {
      row,
      column: column >= endColumn ? column - amount : column,
    }
  })
}

function buildMoveIndexMap(length: number, movedIndexes: number[], finalIndex: number) {
  const sourceIndexes = Array.from({ length }, (_, index) => index)
  const nextOrder = reorderItemsByMove(sourceIndexes, movedIndexes, finalIndex)
  const indexMap = new Map<number, number>()
  nextOrder.forEach((sourceIndex, nextIndex) => {
    indexMap.set(sourceIndex, nextIndex)
  })
  return indexMap
}

function remapRowsWithFormulaReferenceMaps(
  sourceRows: SheetRow[],
  rowIndexMap?: Map<number, number>,
  columnIndexMap?: Map<number, number>,
) {
  const headers = columnHeaders.value
  const nextRows = Array.from({ length: sourceRows.length }, () => createEmptyRow(headers.length))

  sourceRows.forEach((row, sourceRowIndex) => {
    const normalizedRow = normalizeRow(row, headers)
    const targetRowIndex = rowIndexMap?.get(sourceRowIndex) ?? sourceRowIndex
    if (targetRowIndex < 0 || targetRowIndex >= nextRows.length) {
      return
    }

    normalizedRow.forEach((cellValue, sourceColumnIndex) => {
      const targetColumnIndex = columnIndexMap?.get(sourceColumnIndex) ?? sourceColumnIndex
      if (targetColumnIndex < 0 || targetColumnIndex >= headers.length) {
        return
      }

      nextRows[targetRowIndex][targetColumnIndex] = remapFormulaCellReferences(
        cellValue,
        rowIndexMap ? (rowIndex) => rowIndexMap.get(rowIndex) ?? rowIndex : undefined,
        columnIndexMap ? (columnIndex) => columnIndexMap.get(columnIndex) ?? columnIndex : undefined,
      )
    })
  })

  return nextRows
}

function remapFormulaReferencesInRows(
  rowIndexMapper?: (rowIndex: number) => number | null,
  columnIndexMapper?: (columnIndex: number) => number | null,
) {
  if (!rowIndexMapper && !columnIndexMapper) {
    return rows.value
  }

  let changed = false
  const headers = columnHeaders.value
  const nextRows = rows.value.map((row, rowIndex) => (
    normalizeRow(row, headers).map((cellValue, columnIndex) => {
      if (!isFormulaExpression(cellValue)) {
        return cellValue
      }

      const nextValue = remapFormulaCellReferences(cellValue, rowIndexMapper, columnIndexMapper)
      if (nextValue !== cellValue) {
        changed = true
      }
      return nextValue
    })
  ))

  if (!changed) {
    return rows.value
  }

  rows.value = nextRows
  getHotInstance()?.updateSettings({ data: nextRows })
  return nextRows
}

function moveCellMetaColumns(movedColumns: number[], finalIndex: number) {
  const indexMap = buildMoveIndexMap(columnHeaders.value.length, movedColumns, finalIndex)
  cellMeta.value = transformCellMetaPositions(cellMeta.value, ({ row, column }) => ({
    row,
    column: indexMap.get(column) ?? column,
  }))
}

function moveCellMetaRows(movedRows: number[], finalIndex: number) {
  const pageStart = getDocumentRowIndex(0)
  const indexMap = buildMoveIndexMap(rows.value.length, movedRows, finalIndex)
  const pageEnd = pageStart + rows.value.length

  cellMeta.value = transformCellMetaPositions(cellMeta.value, ({ row, column }) => {
    if (row < pageStart || row >= pageEnd) {
      return { row, column }
    }
    const localRow = row - pageStart
    return {
      row: pageStart + (indexMap.get(localRow) ?? localRow),
      column,
    }
  })
}

function remapVisiblePageCellMetaRows(rowIndexMap: Map<number, number>) {
  const pageStart = getDocumentRowIndex(0)
  const pageEnd = pageStart + rows.value.length
  cellMeta.value = transformCellMetaPositions(cellMeta.value, ({ row, column }) => {
    if (row < pageStart || row >= pageEnd) {
      return { row, column }
    }
    const localRow = row - pageStart
    return {
      row: pageStart + (rowIndexMap.get(localRow) ?? localRow),
      column,
    }
  })
}

function handleBeforeColumnMove(
  movedColumns: number[],
  finalIndex: number,
  dropIndex: number | undefined,
  movePossible: boolean,
) {
  if (!canEditConfig.value) {
    return false
  }

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
  const columnIndexMap = buildMoveIndexMap(columnHeaders.value.length, effectiveMovedColumns, effectiveFinalIndex)
  const nextRows = remapRowsWithFormulaReferenceMaps(rows.value, undefined, columnIndexMap)

  const headersChanged = nextHeaders.some((header, index) => header !== columnHeaders.value[index])
  if (!headersChanged) {
    return false
  }

  clearEditingColumnState()
  moveCellMetaColumns(effectiveMovedColumns, effectiveFinalIndex)
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
  if (!canEditData.value) {
    return false
  }

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

  const rowIndexMap = buildMoveIndexMap(rows.value.length, effectiveMovedRows, effectiveFinalIndex)
  const nextRows = remapRowsWithFormulaReferenceMaps(rows.value, rowIndexMap)
  const rowsChanged = nextRows.some((row, index) => row !== rows.value[index])
  if (!rowsChanged) {
    return false
  }

  clearEditingColumnState()
  moveCellMetaRows(effectiveMovedRows, effectiveFinalIndex)
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
  if (!ensureCanEditConfig()) {
    return
  }

  if (columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return
  }

  editingColumnIndex.value = columnIndex
  editingColumnTitle.value = columnHeaders.value[columnIndex] ?? createFallbackHeader(columnIndex)
  refreshGridStructure()
  focusEditingColumnInput()
}

function commitInlineRenameColumn() {
  if (!canEditConfig.value) {
    clearEditingColumnState()
    refreshGridStructure()
    return
  }

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

function handleBeforeCellMouseDown(
  event: MouseEvent,
  coords: { row: number; col: number },
  _td: HTMLTableCellElement,
  controller: CellMouseSelectionController,
) {
  if (coords.row < 0 || coords.col < 0 || event.button !== 0) {
    return
  }

  if (beginFormulaReferenceRange(coords.row, coords.col)) {
    stopFormulaReferenceCellSelection(event, controller)
  }
}

function handleBeforeCellMouseOver(
  event: MouseEvent,
  coords: { row: number; col: number },
  _td: HTMLTableCellElement,
  controller: CellMouseSelectionController,
) {
  if (!formulaReferenceRangeState || coords.row < 0 || coords.col < 0) {
    return
  }

  if (event.buttons !== 1) {
    finishFormulaReferenceRange({ restoreFocus: true })
    return
  }

  updateFormulaReferenceRange(coords.row, coords.col)
  stopFormulaReferenceCellSelection(event, controller)
}

function handleBeforeCellMouseUp(event: MouseEvent, coords: { row: number; col: number }) {
  if (!formulaReferenceRangeState) {
    return
  }

  if (coords.row >= 0 && coords.col >= 0) {
    updateFormulaReferenceRange(coords.row, coords.col)
  }
  finishFormulaReferenceRange({ restoreFocus: true })
  event.preventDefault()
}

function handleHeaderMouseDown(event: MouseEvent, coords: { row: number; col: number }) {
  if (coords.row >= 0 && coords.col >= 0 && event.button === 0 && isFormulaReferencePickMode()) {
    markFormulaReferencePointerDown()
    return
  }

  if (coords.row >= 0 && coords.col >= 0 && (event.ctrlKey || event.metaKey)) {
    const link = getCellLinkAt(getDocumentRowIndex(coords.row), coords.col)
    if (link) {
      event.preventDefault()
      event.stopPropagation()
      openCellLink(link)
    }
    return
  }

  if (coords.col < 0 || coords.row !== -1 || event.detail < 2) {
    return
  }

  startInlineRenameColumn(coords.col)
}

function applyHeaderCellStyle(column: number, th: HTMLTableHeaderCellElement, headerLevel: number) {
  th.style.backgroundColor = ''
  th.style.color = ''
  th.style.fontWeight = ''

  if (column < 0 || headerLevel < 0) {
    return
  }

  const style = nestedHeaderStyleRows.value[headerLevel]?.[column]
  if (!style) {
    return
  }

  if (style.background_color) {
    th.style.backgroundColor = style.background_color
  }
  if (style.text_color) {
    th.style.color = style.text_color
  }
  if (style.background_color || style.text_color) {
    th.style.fontWeight = '600'
  }
}

function handleAfterGetColHeader(column: number, th: HTMLTableHeaderCellElement, headerLevel: number) {
  applyHeaderCellStyle(column, th, headerLevel)
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
    && normalizedLeft.row_marker_numbering === normalizedRight.row_marker_numbering
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
  if (!ensureCanEditConfig()) {
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
  if (!ensureCanEditConfig()) {
    closeSheetSettings()
    return
  }

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
  clearFormulaBarSelection()
  clearColumnMarkerSelection()
  hasContextMenuFallbackSelection.value = false
  closeColumnNotePopover()
  columnSettingsDialogVisible.value = false
  columnSettingsColumnIndex.value = null
  columnSettingsSelectionBounds.value = null
  closeCellLinkDialog()
  closeCellStyleDialog()
  const normalizedHeaders = normalizeHeaders(document.columns)
  const normalizedRows = document.rows.length
    ? document.rows.map((row) => normalizeRow(row, normalizedHeaders))
    : [createEmptyRow(normalizedHeaders.length)]

  columnHeaders.value = normalizedHeaders
  headerGroups.value = normalizeHeaderGroups(document.header_groups, normalizedHeaders.length)
  columnConfigs.value = normalizeColumnConfigs(document.column_configs, normalizedHeaders)
  cellMeta.value = normalizeCellMetaMap(document.cell_meta, normalizedHeaders.length)
  sheetViewSettings.value = normalizeSheetViewSettings(document.view_settings)
  pageSize.value = sheetViewSettings.value.pagination.page_size
  const formulaDisplayForWidths = buildFormulaDisplayStateForRows(normalizedHeaders, normalizedRows, columnConfigs.value)
  columnWidths.value = document.column_widths?.length
    ? document.column_widths.slice(0, normalizedHeaders.length)
    : normalizedHeaders.map((_, index) => getAutoColumnWidth(
      index,
      normalizedHeaders,
      normalizedRows,
      columnConfigs.value,
      formulaDisplayForWidths,
    ))
  rows.value = normalizedRows
  refreshFormulaDisplayState()

  const hot = getHotInstance()
  if (hot) {
    hot.updateSettings({
      data: normalizedRows,
      colHeaders: [...normalizedHeaders],
      nestedHeaders: nestedHeaders.value,
      colWidths: [...columnWidths.value],
      rowHeaders: sheetRowHeaders.value,
      hiddenColumns: {
        columns: [...hiddenColumnIndexes.value],
        indicators: false,
      },
      cells: resolveCellMeta,
      rowHeights: resolveRowHeight,
    })
    hot.render()
    void nextTick(() => {
      bindGridScrollSync()
    })
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
  if (props.sheetId == null || !canPersistSheet.value) {
    return null
  }

  return updateNoteSheet(props.sheetId, {
    title: sheetTitle.value.trim() || '未命名表格',
    document_json: document,
    page_patch: buildUpdatePagePatch(),
  }, {
    workbookId: props.workbookId,
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
    remoteAccessCapabilities.value = detail.access?.capabilities ?? null
    sheetTitle.value = detail.title || '未命名表格'
    sheetVersion.value = Number(detail.version || 1)
    applyPaginationState(detail.pagination)
    emitSheetSync(detail)
    loadSheetDocument(normalizeSheetDocument(detail.document_json))
    sheetContentReady.value = true
    changeSerial = 0
    lastQueuedSerial = 0
    clearDraftStorage()
    void refreshAttendanceCourseScriptStatuses()
  } finally {
    suppressPersistence = false
  }
}

async function flushRemoteSave() {
  if (saveInFlight || suppressPersistence || props.sheetId == null) {
    return
  }
  if (!canPersistSheet.value) {
    clearSaveTimer()
    clearDraftStorage()
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
  if (!canPersistSheet.value) {
    clearSaveTimer()
    clearDraftStorage()
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

function normalizeSortText(value: unknown) {
  return normalizeCellValue(value).trim()
}

function parseSortNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  const text = normalizeSortText(value)
  if (!text) {
    return null
  }
  const numeric = Number(text.replace(/,/g, ''))
  return Number.isFinite(numeric) ? numeric : null
}

function compareLocalSortValues(left: unknown, right: unknown) {
  const leftText = normalizeSortText(left)
  const rightText = normalizeSortText(right)
  if (!leftText && !rightText) {
    return 0
  }
  if (!leftText) {
    return 1
  }
  if (!rightText) {
    return -1
  }

  const leftNumber = parseSortNumber(left)
  const rightNumber = parseSortNumber(right)
  if (leftNumber != null && rightNumber != null) {
    return leftNumber - rightNumber
  }

  return leftText.localeCompare(rightText, 'zh-Hans-CN', {
    numeric: true,
    sensitivity: 'base',
  })
}

function sortColumnLocally(columnIndex: number, direction: SortDirection) {
  const decoratedRows = rows.value.map((row, rowIndex) => ({
    row: normalizeRow(row, columnHeaders.value),
    rowIndex,
    value: getCellSemanticValue(rowIndex, columnIndex, row[columnIndex]),
  }))
  const directionFactor = direction === 'asc' ? 1 : -1
  decoratedRows.sort((left, right) => {
    const compared = compareLocalSortValues(left.value, right.value)
    return compared === 0
      ? left.rowIndex - right.rowIndex
      : compared * directionFactor
  })

  const rowIndexMap = new Map<number, number>()
  decoratedRows.forEach((item, nextIndex) => {
    rowIndexMap.set(item.rowIndex, nextIndex)
  })
  rows.value = decoratedRows.map((item) => item.row)
  remapVisiblePageCellMetaRows(rowIndexMap)

  const hot = getHotInstance()
  if (hot) {
    hot.updateSettings({ data: rows.value })
    hot.render()
  }
  syncRowsFromGrid()
  refreshFormulaDisplayState()
  syncFormulaBarDraftFromSelectedCell()
  void refreshComputedRowHeights()
}

async function sortColumn(columnIndex: number, direction: SortDirection) {
  if (props.sheetId == null) {
    return
  }

  if (!canEditData.value) {
    if (!canUseLocalView.value) {
      warnReadOnlyAction()
      return
    }
    sortColumnLocally(columnIndex, direction)
    return
  }

  workspaceLoading.value = true
  try {
    await flushRemoteSave()
    const detail = await sortNoteSheet(props.sheetId, {
      column_index: columnIndex,
      direction,
    }, {
      workbookId: props.workbookId,
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

function getAttendanceCourseTypeColumnIndex() {
  return findColumnIndexByBinding(ATTENDANCE_FIELD_BINDINGS.courseType)
}

function getAttendanceOnlineSheetColumnIndex() {
  return findColumnIndexByBinding(ATTENDANCE_FIELD_BINDINGS.onlineSheet)
}

function getAttendanceLinkCountColumnIndex(fieldKey: AttendanceLinkCountFieldKey) {
  const binding = fieldKey === 'lesson_links'
    ? ATTENDANCE_FIELD_BINDINGS.lessonLinks
    : ATTENDANCE_FIELD_BINDINGS.clockinLinks
  return columnHeaders.value.findIndex((header) => normalizeCellValue(header).trim() === binding.header)
}

async function refreshAttendanceCourseScriptStatuses() {
  if (
    props.sheetId == null
    || !isAttendanceSummarySheetPluginEnabled()
    || !canRunSheetActions.value
  ) {
    attendanceCourseScriptStatuses.value = {}
    return
  }

  try {
    const result = await fetchAttendanceCourseScriptStatuses(props.sheetId, {
      workbookId: props.workbookId,
    })
    const nextStatuses: Record<number, AttendanceCourseScriptStatusItem> = {}
    result.statuses.forEach((item) => {
      nextStatuses[item.row_index] = item
    })
    attendanceCourseScriptStatuses.value = nextStatuses
  } catch (error) {
    console.warn('Failed to fetch attendance course script statuses', error)
    attendanceCourseScriptStatuses.value = {}
  }
}

function getSelectedAttendanceCompletionCell() {
  if (!isAttendanceSummarySheetPluginEnabled()) {
    return null
  }
  const cell = getSingleSelectedDataCell()
  const completedColumnIndex = attendanceCompletedColumnIndex.value
  if (!cell || completedColumnIndex < 0 || cell.column !== completedColumnIndex) {
    return null
  }
  return cell
}

function canSetAttendanceCompletedFromSelection() {
  return !!getSelectedAttendanceCompletionCell()
}

async function handleSetAttendanceCompletedFromSelection() {
  if (props.sheetId == null || workspaceLoading.value) {
    return
  }
  if (!ensureCanRunSheetActions()) {
    return
  }

  const selectedCompletionCell = getSelectedAttendanceCompletionCell()
  if (!selectedCompletionCell) {
    return
  }

  workspaceLoading.value = true
  try {
    const scrollPosition = captureSheetScrollPosition()
    await flushRemoteSave()
    const result = await setAttendanceRowCompleted(props.sheetId, {
      row_index: selectedCompletionCell.documentRow,
    }, {
      workbookId: props.workbookId,
    })
    applyRemoteSheetDetail(result.sheet)
    await updateSheetViewportHeight()
    restoreSheetScrollPosition(scrollPosition)
    ElMessage.success('已设置完结')
  } catch (error) {
    console.warn('Failed to set attendance row completed', error)
    ElMessage.error('设置完结失败')
  } finally {
    workspaceLoading.value = false
  }
}

function getSelectedAttendanceCourseTypeCell() {
  if (!isAttendanceSummarySheetPluginEnabled()) {
    return null
  }
  const cell = getSingleSelectedDataCell()
  const courseTypeColumnIndex = getAttendanceCourseTypeColumnIndex()
  if (!cell || courseTypeColumnIndex < 0 || cell.column !== courseTypeColumnIndex) {
    return null
  }

  const courseType = normalizeCellValue(rows.value[cell.row]?.[cell.column] ?? '').trim()
  if (!courseType) {
    return null
  }
  return { ...cell, courseType }
}

function canGenerateAttendanceCourseTemplateFromSelection() {
  return !!getSelectedAttendanceCourseTypeCell()
}

function getSelectedAttendanceOnlineSheetCell() {
  if (!isAttendanceSummarySheetPluginEnabled()) {
    return null
  }
  const cell = getSingleSelectedDataCell()
  const onlineSheetColumnIndex = getAttendanceOnlineSheetColumnIndex()
  if (!cell || onlineSheetColumnIndex < 0 || cell.column !== onlineSheetColumnIndex) {
    return null
  }

  const link = getCellLinkAt(cell.documentRow, cell.column)
  if (!link?.url) {
    return null
  }
  return { ...cell, link }
}

function canGenerateAttendanceCourseScriptFromSelection() {
  const selectedOnlineSheetCell = getSelectedAttendanceOnlineSheetCell()
  if (!selectedOnlineSheetCell) {
    return false
  }

  const status = attendanceCourseScriptStatuses.value[selectedOnlineSheetCell.documentRow]
  return !status || (!status.exists && status.can_generate !== false)
}

function canOrganizeAttendanceCourseScriptsFromColumn() {
  if (!isAttendanceSummarySheetPluginEnabled()) {
    return false
  }
  const onlineSheetColumnIndex = getAttendanceOnlineSheetColumnIndex()
  const selectedColumnIndex = getSingleSelectedColumnIndex()
  return onlineSheetColumnIndex >= 0 && selectedColumnIndex === onlineSheetColumnIndex
}

function canUpdateAttendanceLinkCountsFromColumn(fieldKey: AttendanceLinkCountFieldKey) {
  if (!isAttendanceSummarySheetPluginEnabled()) {
    return false
  }
  const linkCountColumnIndex = getAttendanceLinkCountColumnIndex(fieldKey)
  const selectedColumnIndex = getSingleSelectedColumnIndex()
  return linkCountColumnIndex >= 0 && selectedColumnIndex === linkCountColumnIndex
}

function getSelectedAttendanceLinkCountCell(fieldKey: AttendanceLinkCountFieldKey) {
  if (!isAttendanceSummarySheetPluginEnabled()) {
    return null
  }
  const linkCountColumnIndex = getAttendanceLinkCountColumnIndex(fieldKey)
  const cell = getSingleSelectedDataCell()
  if (!cell || linkCountColumnIndex < 0 || cell.column !== linkCountColumnIndex) {
    return null
  }
  return cell
}

function canUpdateAttendanceLinkCountsFromSelection(fieldKey: AttendanceLinkCountFieldKey) {
  return canUpdateAttendanceLinkCountsFromColumn(fieldKey) || !!getSelectedAttendanceLinkCountCell(fieldKey)
}

async function handleGenerateAttendanceCourseTemplateFromSelection() {
  if (props.sheetId == null || workspaceLoading.value) {
    return
  }
  if (!ensureCanRunSheetActions()) {
    return
  }

  const selectedCourseTypeCell = getSelectedAttendanceCourseTypeCell()
  if (!selectedCourseTypeCell) {
    return
  }

  workspaceLoading.value = true
  try {
    await flushRemoteSave()
    const result = await generateAttendanceCourseTemplate(props.sheetId, {
      row_index: selectedCourseTypeCell.documentRow,
    }, {
      workbookId: props.workbookId,
    })
    applyRemoteSheetDetail(result.sheet)
    void updateSheetViewportHeight()

    if (result.generated.length > 0) {
      ElMessage.success(`已生成：${result.generated.map((item) => item.course_name || item.course_type).join('、')}`)
    } else if (result.skipped.every((item) => item.reason === '目标课程已存在')) {
      ElMessage.info('目标课程模板已存在，未重复生成')
    } else {
      const reasonText = result.skipped.map((item) => `${item.course_type}${item.reason ? `：${item.reason}` : ''}`).join('；')
      ElMessage.warning(reasonText || '没有生成新的课程模板')
    }
  } catch (error) {
    console.warn('Failed to generate attendance course template', error)
    ElMessage.error('生成新课模板失败')
  } finally {
    workspaceLoading.value = false
  }
}

async function handleGenerateAttendanceCourseScriptFromSelection() {
  if (props.sheetId == null || workspaceLoading.value) {
    return
  }
  if (!ensureCanRunSheetActions()) {
    return
  }

  const selectedOnlineSheetCell = getSelectedAttendanceOnlineSheetCell()
  if (!selectedOnlineSheetCell) {
    return
  }

  workspaceLoading.value = true
  try {
    await flushRemoteSave()
    const result = await generateAttendanceCourseScript(props.sheetId, {
      row_index: selectedOnlineSheetCell.documentRow,
    }, {
      workbookId: props.workbookId,
    })
    attendanceCourseScriptStatuses.value = {
      ...attendanceCourseScriptStatuses.value,
      [result.status.row_index]: result.status,
    }
    const targetName = result.status.target_filename || 'py脚本'
    const sourceName = result.source_filename ? `，模板：${result.source_filename}` : ''
    ElMessage.success(`已生成：${targetName}${sourceName}`)
  } catch (error) {
    console.warn('Failed to generate attendance course script', error)
    ElMessage.error('生成py脚本失败')
    void refreshAttendanceCourseScriptStatuses()
  } finally {
    workspaceLoading.value = false
  }
}

async function handleOrganizeAttendanceCourseScriptsFromColumn() {
  if (props.sheetId == null || workspaceLoading.value) {
    return
  }
  if (!ensureCanRunSheetActions()) {
    return
  }
  if (!canOrganizeAttendanceCourseScriptsFromColumn()) {
    return
  }

  workspaceLoading.value = true
  try {
    await flushRemoteSave()
    const result = await organizeAttendanceCourseScripts(props.sheetId, {
      workbookId: props.workbookId,
    })
    void refreshAttendanceCourseScriptStatuses()

    const movedCount = result.moved.length
    if (movedCount > 0) {
      ElMessage.success(`已整理 ${movedCount} 个py脚本`)
    } else {
      ElMessage.info('py脚本位置已是最新')
    }
  } catch (error) {
    console.warn('Failed to organize attendance course scripts', error)
    ElMessage.error('整理py脚本失败')
  } finally {
    workspaceLoading.value = false
  }
}

async function handleUpdateAttendanceLinkCountsFromSelection(fieldKey: AttendanceLinkCountFieldKey) {
  if (props.sheetId == null || workspaceLoading.value) {
    return
  }
  if (!ensureCanRunSheetActions()) {
    return
  }
  const selectedCell = getSelectedAttendanceLinkCountCell(fieldKey)
  if (!canUpdateAttendanceLinkCountsFromColumn(fieldKey) && !selectedCell) {
    return
  }

  workspaceLoading.value = true
  try {
    await flushRemoteSave()
    const result = await updateAttendanceLinkCounts(props.sheetId, {
      field_key: fieldKey,
      row_index: selectedCell?.documentRow,
    }, {
      workbookId: props.workbookId,
    })
    applyRemoteSheetDetail(result.sheet)
    void updateSheetViewportHeight()

    const updatedCount = result.updated.length
    if (updatedCount > 0) {
      ElMessage.success(`已更新 ${updatedCount} 行`)
    } else {
      const reasonText = result.skipped.map((item) => item.reason).filter(Boolean).join('；')
      ElMessage.info(reasonText || '没有可更新的数据')
    }
  } catch (error) {
    console.warn('Failed to update attendance link counts', error)
    ElMessage.error('更新数据失败')
  } finally {
    workspaceLoading.value = false
  }
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
  sheetContentReady.value = false
  suppressPersistence = true
  try {
    let localDraft = readDraftPayload()
    const paginationPreference = resolveFetchPaginationPreference(localDraft)
    let remote = await fetchNoteSheet(props.sheetId, paginationPreference
      ? {
        page: paginationPreference.paginate ? currentPage.value : undefined,
        pageSize: paginationPreference.paginate ? paginationPreference.pageSize : undefined,
        paginate: paginationPreference.paginate,
        workbookId: props.workbookId,
      }
      : {
        workbookId: props.workbookId,
      })
    if (!remote) {
      sheetContentReady.value = false
      emit('missing', props.sheetId)
      return
    }

    remoteAccessCapabilities.value = remote.access?.capabilities ?? null
    if (!canPersistSheet.value) {
      localDraft = null
      clearDraftStorage()
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
          workbookId: props.workbookId,
        }
        : {
          paginate: false,
          workbookId: props.workbookId,
        })
      if (!remote) {
        sheetContentReady.value = false
        emit('missing', props.sheetId)
        return
      }
      remoteAccessCapabilities.value = remote.access?.capabilities ?? null
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
      const localDraftIsNewer = localDraft.updatedAt > remoteUpdatedAt
      const localDraftIsMeaningful = hasMeaningfulSheetDocumentContent(localDraft.document)
      const remoteDocumentIsMeaningful = hasMeaningfulSheetDocumentContent(remoteDocument)
      if (localDraftIsNewer && (localDraftIsMeaningful || !remoteDocumentIsMeaningful)) {
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
    sheetContentReady.value = true
    changeSerial = 0
    lastQueuedSerial = 0
    suppressPersistence = false

    if (shouldSyncLocalDraft) {
      scheduleRemoteSave(0)
    }
    void refreshAttendanceCourseScriptStatuses()
  } finally {
    suppressPersistence = false
    workspaceLoading.value = false
  }
}

async function handlePageChange(nextPage: number) {
  if (!effectivePaginationEnabled.value) {
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
  finishFormulaReferenceRange()
  clearFormulaReferencePreviewRange()
  syncRowsFromGrid()
  refreshFormulaDisplayState()
  syncFormulaBarDraftFromSelectedCell()
  getHotInstance()?.render()
}

function readCopyPasteRangeNumber(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = Number(record[key])
    if (Number.isInteger(value)) {
      return value
    }
  }
  return null
}

function normalizeCopyPasteRange(coord: unknown): SheetCopyPasteRange | null {
  if (!coord || typeof coord !== 'object') {
    return null
  }

  const record = coord as Record<string, unknown>
  const startRow = readCopyPasteRangeNumber(record, ['startRow', 'fromRow', 'row'])
  const endRow = readCopyPasteRangeNumber(record, ['endRow', 'toRow', 'row'])
  const startColumn = readCopyPasteRangeNumber(record, ['startCol', 'startColumn', 'fromCol', 'fromColumn', 'col', 'column'])
  const endColumn = readCopyPasteRangeNumber(record, ['endCol', 'endColumn', 'toCol', 'toColumn', 'col', 'column'])
  if (startRow == null || endRow == null || startColumn == null || endColumn == null) {
    return null
  }

  return {
    startRow: Math.min(startRow, endRow),
    endRow: Math.max(startRow, endRow),
    startColumn: Math.min(startColumn, endColumn),
    endColumn: Math.max(startColumn, endColumn),
  }
}

function isMatrix(value: unknown): value is unknown[][] {
  return Array.isArray(value) && value.every((row) => Array.isArray(row))
}

function stringifyCopyPasteMatrix(matrix: unknown[][]) {
  return matrix.map((row) => row.map((cell) => normalizeCellValue(cell)))
}

function areCopyPasteMatricesEqual(left: string[][], right: unknown[][]) {
  if (left.length !== right.length) {
    return false
  }
  return left.every((leftRow, rowIndex) => {
    const rightRow = right[rowIndex]
    return Array.isArray(rightRow)
      && leftRow.length === rightRow.length
      && leftRow.every((leftCell, columnIndex) => leftCell === normalizeCellValue(rightRow[columnIndex]))
  })
}

function handleBeforeCopy(data: unknown[][], coords: unknown[]) {
  if (!isMatrix(data) || data.length <= 0 || !Array.isArray(coords) || coords.length <= 0) {
    sheetInternalClipboard = null
    return
  }

  const range = normalizeCopyPasteRange(coords[0])
  if (!range) {
    sheetInternalClipboard = null
    return
  }

  const rawData = data.map((row, rowOffset) => (
    row.map((_, columnOffset) => {
      const sourceRow = range.startRow + rowOffset
      const sourceColumn = range.startColumn + columnOffset
      return getRawCellValue(sourceRow, sourceColumn)
    })
  ))
  const displayData = rawData.map((row, rowOffset) => (
    row.map((rawValue, columnOffset) => {
      const sourceRow = range.startRow + rowOffset
      const sourceColumn = range.startColumn + columnOffset
      return getCellDisplayText(sourceRow, sourceColumn, rawValue)
    })
  ))

  sheetInternalClipboard = {
    sheetId: props.sheetId,
    sourceStartRow: range.startRow,
    sourceStartColumn: range.startColumn,
    rawData,
    displayData,
    createdAt: Date.now(),
  }

  displayData.forEach((row, rowIndex) => {
    if (!Array.isArray(data[rowIndex])) {
      return
    }
    row.forEach((cell, columnIndex) => {
      data[rowIndex][columnIndex] = cell
    })
  })
}

function getInternalClipboardForPaste(data: unknown[][]) {
  if (!sheetInternalClipboard || sheetInternalClipboard.sheetId !== props.sheetId) {
    return null
  }
  if (Date.now() - sheetInternalClipboard.createdAt > 5 * 60 * 1000) {
    sheetInternalClipboard = null
    return null
  }
  if (
    !areCopyPasteMatricesEqual(sheetInternalClipboard.displayData, data)
    && !areCopyPasteMatricesEqual(sheetInternalClipboard.rawData, data)
  ) {
    return null
  }
  return sheetInternalClipboard
}

function getGridRowCountForExpansionGuard() {
  const hot = getHotInstance()
  return Math.max(rows.value.length, hot?.countSourceRows() ?? 0)
}

function isPagedRowExpansionAllowed(requiredEndRow: number) {
  if (!effectivePaginationEnabled.value) {
    return true
  }
  if (!Number.isInteger(requiredEndRow) || requiredEndRow < 0) {
    return true
  }

  const currentRowCount = getGridRowCountForExpansionGuard()
  if (requiredEndRow < currentRowCount) {
    return true
  }

  return currentPage.value >= pageCount.value
}

function ensurePagedRowExpansionAllowed(requiredEndRow: number, message = PAGED_ROW_EXPANSION_MESSAGE) {
  if (isPagedRowExpansionAllowed(requiredEndRow)) {
    return true
  }

  ElMessage.error(message)
  return false
}

function getPasteRequiredEndRow(data: unknown[][], targetRange: SheetCopyPasteRange) {
  const pastedRowCount = data.length
  const selectionRowCount = targetRange.endRow - targetRange.startRow + 1
  const targetRowCount = Math.max(pastedRowCount, selectionRowCount)
  return targetRange.startRow + targetRowCount - 1
}

function getPasteRequiredEndColumn(data: unknown[][], targetRange: SheetCopyPasteRange) {
  const pastedColumnCount = Math.max(0, ...data.map((row) => row.length))
  const selectionColumnCount = targetRange.endColumn - targetRange.startColumn + 1
  const targetColumnCount = Math.max(pastedColumnCount, selectionColumnCount)
  return targetRange.startColumn + targetColumnCount - 1
}

function shouldGuardBulkRowExpansionSource(source?: string) {
  return source === 'CopyPaste.paste' || source === 'Autofill.fill'
}

function getChangesRequiredEndRow(changes: unknown[]) {
  let endRow = -1
  for (const change of changes) {
    if (!Array.isArray(change) || change.length < 4) {
      continue
    }

    const rowIndex = Number(change[0])
    if (Number.isInteger(rowIndex)) {
      endRow = Math.max(endRow, rowIndex)
    }
  }
  return endRow
}

function areChangedColumnsEditable(changes: unknown[]) {
  if (canEditData.value) {
    return true
  }
  for (const change of changes) {
    if (!Array.isArray(change) || change.length < 4) {
      continue
    }

    const columnIndex = Number(change[1])
    if (!Number.isInteger(columnIndex) || columnIndex < 0) {
      continue
    }
    if (!editableDataColumnSet.value.has(columnIndex)) {
      return false
    }
  }
  return true
}

function handleBeforePaste(data: unknown[][], coords: unknown[]) {
  if (!canEditData.value && !canEditPartialData.value) {
    warnReadOnlyAction()
    return false
  }

  if (!isMatrix(data) || !Array.isArray(coords) || coords.length <= 0) {
    return
  }

  const clipboard = getInternalClipboardForPaste(data)
  const targetRange = normalizeCopyPasteRange(coords[0])
  if (!targetRange) {
    return
  }
  if (!isColumnRangeEditable(targetRange.startColumn, getPasteRequiredEndColumn(data, targetRange))) {
    warnReadOnlyColumnAction()
    return false
  }
  if (!canEditData.value && getPasteRequiredEndRow(data, targetRange) >= getGridRowCountForExpansionGuard()) {
    warnReadOnlyAction()
    return false
  }
  if (!ensurePagedRowExpansionAllowed(getPasteRequiredEndRow(data, targetRange))) {
    return false
  }
  if (!clipboard) {
    return
  }

  clipboard.rawData.forEach((row, rowOffset) => {
    if (!Array.isArray(data[rowOffset])) {
      return
    }
    row.forEach((rawValue, columnOffset) => {
      const sourceRow = clipboard.sourceStartRow + rowOffset
      const sourceColumn = clipboard.sourceStartColumn + columnOffset
      const targetRow = targetRange.startRow + rowOffset
      const targetColumn = targetRange.startColumn + columnOffset
      data[rowOffset][columnOffset] = isFormulaExpression(rawValue)
        ? shiftFormulaCellReferences(rawValue, targetRow - sourceRow, targetColumn - sourceColumn)
        : rawValue
    })
  })
}

function shouldTrimWhitespaceForColumn(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return true
  }
  return normalizeColumnConfig(columnConfigs.value[header]).trim_whitespace
}

function getColumnValueType(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return 'text'
  }
  return normalizeColumnConfig(columnConfigs.value[header]).value_type
}

function normalizeCellInputValueForColumn(value: string, columnIndex: number) {
  const valueType = getColumnValueType(columnIndex)
  const trimmedValue = shouldTrimWhitespaceForColumn(columnIndex) ? value.trim() : value
  if (isFormulaExpression(trimmedValue)) {
    return normalizeFormulaInputExpression(trimmedValue)
  }
  if (valueType === 'multi_text') {
    return normalizeMultiTextValue(trimmedValue)
  }
  if (valueType === 'date') {
    return normalizeDateInputValue(trimmedValue)
  }
  if (valueType === 'percent') {
    return normalizePercentInputValue(trimmedValue)
  }
  return trimmedValue
}

function handleBeforeChange(changes: unknown, source?: string) {
  if (
    !canEditData.value
    && !canEditPartialData.value
    && source !== 'loadData'
    && source !== 'external-update'
  ) {
    return false
  }

  if (
    !Array.isArray(changes)
    || source === 'loadData'
    || source === 'external-update'
  ) {
    return
  }

  if (!areChangedColumnsEditable(changes)) {
    warnReadOnlyColumnAction()
    return false
  }
  if (!canEditData.value && getChangesRequiredEndRow(changes) >= getGridRowCountForExpansionGuard()) {
    warnReadOnlyAction()
    return false
  }

  if (
    shouldGuardBulkRowExpansionSource(source)
    && !ensurePagedRowExpansionAllowed(
      getChangesRequiredEndRow(changes),
      source === 'Autofill.fill' ? PAGED_AUTO_ROW_INSERT_MESSAGE : PAGED_ROW_EXPANSION_MESSAGE,
    )
  ) {
    return false
  }

  for (const change of changes) {
    if (!Array.isArray(change) || change.length < 4) {
      continue
    }

    const columnIndex = Number(change[1])
    if (!Number.isInteger(columnIndex) || columnIndex < 0) {
      continue
    }

    const nextValue = change[3]
    if (typeof nextValue === 'string') {
      change[3] = normalizeCellInputValueForColumn(nextValue, columnIndex)
    }
  }
}

function handleBeforeAutofill(
  selectionData: unknown[][],
  sourceRange: SheetAutofillRange,
  targetRange: SheetAutofillRange,
  direction: SheetAutofillDirection,
) {
  if (!canEditData.value && !canEditPartialData.value) {
    warnReadOnlyAction()
    return false
  }
  const targetBounds = normalizeAutofillRange(targetRange)
  if (!isColumnRangeEditable(targetBounds.startColumn, targetBounds.endColumn)) {
    warnReadOnlyColumnAction()
    return false
  }
  if (!canEditData.value && targetBounds.endRow >= getGridRowCountForExpansionGuard()) {
    warnReadOnlyAction()
    return false
  }
  if (!ensurePagedRowExpansionAllowed(targetBounds.endRow, PAGED_AUTO_ROW_INSERT_MESSAGE)) {
    return false
  }
  return buildFormulaAutofillData(sourceRange, targetRange, direction) ?? selectionData
}

function handleBeforeCreateRow(index = 0, amount = 1, source?: string) {
  if (!canEditData.value) {
    warnReadOnlyAction()
    return false
  }
  if (source !== 'auto') {
    return
  }

  const startRow = normalizeNonNegativeInt(index, getGridRowCountForExpansionGuard())
  const rowAmount = Math.max(1, normalizePositivePageNumber(amount, 1))
  const requiredEndRow = Math.max(getGridRowCountForExpansionGuard(), startRow) + rowAmount - 1
  if (!ensurePagedRowExpansionAllowed(requiredEndRow, PAGED_AUTO_ROW_INSERT_MESSAGE)) {
    return false
  }
}

function handleAfterCreateRow(index = 0, amount = 1) {
  shiftCellMetaRows(getDocumentRowIndex(index), amount)
  syncRowsFromGrid()
  remapFormulaReferencesInRows((rowIndex) => (rowIndex >= index ? rowIndex + amount : rowIndex))
}

function handleAfterRemoveRow(index = 0, amount = 1) {
  removeCellMetaRows(getDocumentRowIndex(index), amount)
  syncRowsFromGrid()
  const endIndex = index + amount
  remapFormulaReferencesInRows((rowIndex) => {
    if (rowIndex >= index && rowIndex < endIndex) {
      return null
    }
    return rowIndex >= endIndex ? rowIndex - amount : rowIndex
  })
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
  shiftCellMetaColumns(index, amount)
  refreshGridStructure()
  syncRowsFromGrid()
  remapFormulaReferencesInRows(undefined, (columnIndex) => (
    columnIndex >= index ? columnIndex + amount : columnIndex
  ))
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
  removeCellMetaColumns(index, amount)
  refreshGridStructure()
  syncRowsFromGrid()
  const endIndex = index + amount
  remapFormulaReferencesInRows(undefined, (columnIndex) => {
    if (columnIndex >= index && columnIndex < endIndex) {
      return null
    }
    return columnIndex >= endIndex ? columnIndex - amount : columnIndex
  })
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

function hasColumnHeaderSelection() {
  return isSelectedByColumnHeader() && !isSelectedByCorner() && !!getSelectionColumnBounds()
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

function areColumnMarkerBoundsEqual(
  left: { start: number; end: number } | null,
  right: { start: number; end: number } | null,
) {
  return left?.start === right?.start && left?.end === right?.end
}

function clearColumnMarkerSelection() {
  selectedColumnMarkerBounds.value = null
  columnMarkerSelectionAnchor = null
}

function syncColumnMarkerSelection() {
  if (!isSelectedByColumnHeader() || isSelectedByCorner()) {
    if (selectedColumnMarkerBounds.value || columnMarkerSelectionAnchor != null) {
      clearColumnMarkerSelection()
    }
    return
  }

  const nextBounds = getSelectionColumnBounds()
  if (!areColumnMarkerBoundsEqual(selectedColumnMarkerBounds.value, nextBounds)) {
    selectedColumnMarkerBounds.value = nextBounds
  }
  if (
    nextBounds
    && (
      columnMarkerSelectionAnchor == null
      || columnMarkerSelectionAnchor < nextBounds.start
      || columnMarkerSelectionAnchor > nextBounds.end
    )
  ) {
    columnMarkerSelectionAnchor = nextBounds.start
  }
}

function isColumnMarkerSelected(columnIndex: number) {
  const bounds = selectedColumnMarkerBounds.value
  return !!bounds && columnIndex >= bounds.start && columnIndex <= bounds.end
}

function getDocumentRowIndex(rowIndex: number) {
  return (effectivePaginationEnabled.value ? pageRowOffset.value : 0) + rowIndex
}

function getSheetRowHeaderLabel(rowIndex: number) {
  const numbering = sheetViewSettings.value.row_marker_numbering
  const offset = effectivePaginationEnabled.value && numbering === 'global' ? pageRowOffset.value : 0
  return String(offset + rowIndex + 1)
}

function clearFormulaBarSelection() {
  clearScheduledFormulaBarDraftSync()
  finishFormulaReferenceRange()
  clearFormulaReferencePreviewRange()
  clearFormulaReferenceReplacementSpan()
  clearScheduledInlineEditorFormulaBarSync()
  clearFormulaReferencePointerDownReset()
  formulaReferencePointerDown = false
  formulaBarCell.value = null
  formulaBarDraft.value = ''
  formulaBarFocused.value = false
}

function getRawCellValue(rowIndex: number, columnIndex: number) {
  return normalizeCellValue(rows.value[rowIndex]?.[columnIndex] ?? '')
}

function getCellEditText(columnIndex: number, rawValue: unknown) {
  const normalizedValue = normalizeCellValue(rawValue)
  if (isFormulaExpression(normalizedValue)) {
    return normalizedValue
  }

  const columnConfig = normalizeColumnConfig(getColumnConfigByIndex(columnIndex))
  if (columnConfig.value_type === 'date') {
    return formatDateEditText(normalizedValue)
  }
  if (columnConfig.value_type === 'percent') {
    return formatPercentDisplayValue(normalizedValue, columnConfig.display_format)
  }
  return normalizedValue
}

function syncFormulaBarDraftFromSelectedCell(force = false) {
  const cell = formulaBarCell.value
  if (!cell) {
    formulaBarDraft.value = ''
    return
  }
  if (formulaBarFocused.value && !force) {
    return
  }
  formulaBarDraft.value = getCellEditText(cell.column, getRawCellValue(cell.row, cell.column))
}

function clearScheduledFormulaBarDraftSync() {
  if (formulaBarDraftSyncFrame == null) {
    return
  }
  window.cancelAnimationFrame(formulaBarDraftSyncFrame)
  formulaBarDraftSyncFrame = null
}

function scheduleFormulaBarDraftFromSelectedCell(force = false) {
  clearScheduledFormulaBarDraftSync()
  formulaBarDraftSyncFrame = window.requestAnimationFrame(() => {
    formulaBarDraftSyncFrame = null
    syncFormulaBarDraftFromSelectedCell(force)
  })
}

function isSameFormulaBarCell(rowIndex: number, columnIndex: number) {
  const cell = formulaBarCell.value
  return !!cell && cell.row === rowIndex && cell.column === columnIndex
}

function isFormulaReferencePickMode() {
  return !!formulaBarCell.value && formulaBarFocused.value && formulaBarDraft.value.trimStart().startsWith('=')
}

function getCellReferenceLabel(rowIndex: number, columnIndex: number) {
  return `${getExcelColumnLabel(columnIndex)}${getDocumentRowIndex(rowIndex) + 1}`
}

function getCellReferenceRangeLabel(startRow: number, startColumn: number, endRow: number, endColumn: number) {
  const topRow = Math.min(startRow, endRow)
  const bottomRow = Math.max(startRow, endRow)
  const leftColumn = Math.min(startColumn, endColumn)
  const rightColumn = Math.max(startColumn, endColumn)
  const startReference = getCellReferenceLabel(topRow, leftColumn)
  const endReference = getCellReferenceLabel(bottomRow, rightColumn)
  return startReference === endReference ? startReference : `${startReference}:${endReference}`
}

function normalizeFormulaReferenceRangeBounds(bounds: FormulaReferenceRangeBounds | null) {
  if (!bounds) {
    return null
  }
  return {
    startRow: Math.min(bounds.startRow, bounds.currentRow),
    endRow: Math.max(bounds.startRow, bounds.currentRow),
    startColumn: Math.min(bounds.startColumn, bounds.currentColumn),
    endColumn: Math.max(bounds.startColumn, bounds.currentColumn),
  }
}

function areFormulaReferenceRangeBoundsEqual(
  left: FormulaReferenceRangeBounds | null,
  right: FormulaReferenceRangeBounds | null,
) {
  const normalizedLeft = normalizeFormulaReferenceRangeBounds(left)
  const normalizedRight = normalizeFormulaReferenceRangeBounds(right)
  return normalizedLeft?.startRow === normalizedRight?.startRow
    && normalizedLeft?.endRow === normalizedRight?.endRow
    && normalizedLeft?.startColumn === normalizedRight?.startColumn
    && normalizedLeft?.endColumn === normalizedRight?.endColumn
}

function isCellInFormulaReferencePreview(rowIndex: number, columnIndex: number) {
  const bounds = normalizeFormulaReferenceRangeBounds(formulaReferencePreviewRange.value)
  return !!bounds
    && rowIndex >= bounds.startRow
    && rowIndex <= bounds.endRow
    && columnIndex >= bounds.startColumn
    && columnIndex <= bounds.endColumn
}

function getFormulaBarNativeInput() {
  return formulaBarInputRef.value?.input ?? null
}

function getActiveOpenedEditor() {
  const editor = getHotInstance()?.getActiveEditor() as SheetActiveEditor | undefined
  if (!editor?.isOpened?.()) {
    return null
  }
  return editor
}

function getEditorTextInput(editor: SheetActiveEditor) {
  const input = editor.TEXTAREA
  return input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement ? input : null
}

function getActiveEditorDraft(editor: SheetActiveEditor) {
  const input = getEditorTextInput(editor)
  return normalizeCellValue(input?.value ?? editor.getValue?.() ?? '')
}

function normalizeTextSelection(input: HTMLInputElement | HTMLTextAreaElement | null, currentValue: string) {
  const selectionStart = input?.selectionStart ?? currentValue.length
  const selectionEnd = input?.selectionEnd ?? selectionStart
  return {
    selectionStart: Math.max(0, Math.min(selectionStart, currentValue.length)),
    selectionEnd: Math.max(0, Math.min(selectionEnd, currentValue.length)),
  }
}

function isFormulaReferenceTargetSame(
  left: FormulaReferenceEditTarget,
  right: FormulaReferenceEditTarget,
) {
  if (left.kind !== right.kind) {
    return false
  }
  return left.kind === 'formula-bar' || left.editor === (right as { kind: 'inline'; editor: SheetActiveEditor }).editor
}

function clearFormulaReferenceReplacementSpan() {
  formulaReferenceReplacementSpan = null
}

function setFormulaReferenceReplacementSpan(
  target: FormulaReferenceEditTarget,
  start: number,
  text: string,
) {
  formulaReferenceReplacementSpan = {
    target,
    start,
    end: start + text.length,
    text,
  }
}

function getFormulaReferenceReplacementSpan(
  target: FormulaReferenceEditTarget,
  currentValue: string,
  selectionStart: number,
  selectionEnd: number,
) {
  const span = formulaReferenceReplacementSpan
  if (!span) {
    return null
  }

  const validSpan = (
    isFormulaReferenceTargetSame(span.target, target)
    && span.start >= 0
    && span.end >= span.start
    && span.end <= currentValue.length
    && currentValue.slice(span.start, span.end) === span.text
  )
  if (!validSpan) {
    clearFormulaReferenceReplacementSpan()
    return null
  }

  if (selectionStart !== selectionEnd || selectionStart !== span.end) {
    return null
  }

  return span
}

function getFormulaReferenceReplacementBounds(target: FormulaReferenceInsertionTarget) {
  const selectionStart = Math.min(target.selectionStart, target.selectionEnd)
  const selectionEnd = Math.max(target.selectionStart, target.selectionEnd)
  const replacementSpan = getFormulaReferenceReplacementSpan(
    target,
    target.currentValue,
    selectionStart,
    selectionEnd,
  )
  return replacementSpan
    ? { selectionStart: replacementSpan.start, selectionEnd: replacementSpan.end }
    : { selectionStart, selectionEnd }
}

function isFormulaReferenceEditingKey(event: KeyboardEvent) {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return false
  }
  return event.key.length === 1 || event.key === 'Backspace' || event.key === 'Delete'
}

function isFormulaReferenceEditableMouseTarget(target: Node) {
  const formulaBarInput = getFormulaBarNativeInput()
  if (formulaBarInput && formulaBarInput.contains(target)) {
    return true
  }

  const activeEditor = getActiveOpenedEditor()
  const editorInput = activeEditor ? getEditorTextInput(activeEditor) : null
  return !!editorInput && editorInput.contains(target)
}

function getFormulaReferenceEditTarget(target: FormulaReferenceInsertionTarget): FormulaReferenceEditTarget {
  return target.kind === 'inline'
    ? { kind: 'inline', editor: target.editor }
    : { kind: 'formula-bar' }
}

function getFormulaReferenceTargetCell(target: FormulaReferenceEditTarget) {
  if (target.kind === 'inline' && typeof target.editor.row === 'number' && typeof target.editor.col === 'number') {
    return { row: target.editor.row, column: target.editor.col }
  }

  const cell = formulaBarCell.value
  return cell ? { row: cell.row, column: cell.column } : null
}

function getFormulaReferenceArrowDirection(event: KeyboardEvent): FormulaReferenceArrowDirection | null {
  if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) {
    return null
  }

  switch (event.key) {
    case 'ArrowUp':
      return { rowDelta: -1, columnDelta: 0 }
    case 'ArrowDown':
      return { rowDelta: 1, columnDelta: 0 }
    case 'ArrowLeft':
      return { rowDelta: 0, columnDelta: -1 }
    case 'ArrowRight':
      return { rowDelta: 0, columnDelta: 1 }
    default:
      return null
  }
}

function clampFormulaReferenceRow(rowIndex: number) {
  return Math.max(0, Math.min(rowIndex, Math.max(0, rows.value.length - 1)))
}

function clampFormulaReferenceColumn(columnIndex: number) {
  return Math.max(0, Math.min(columnIndex, Math.max(0, columnHeaders.value.length - 1)))
}

function moveFormulaReferenceBounds(
  bounds: FormulaReferenceRangeBounds,
  direction: FormulaReferenceArrowDirection,
) {
  const topRow = Math.min(bounds.startRow, bounds.currentRow)
  const bottomRow = Math.max(bounds.startRow, bounds.currentRow)
  const leftColumn = Math.min(bounds.startColumn, bounds.currentColumn)
  const rightColumn = Math.max(bounds.startColumn, bounds.currentColumn)
  const maxRow = Math.max(0, rows.value.length - 1)
  const maxColumn = Math.max(0, columnHeaders.value.length - 1)

  let rowDelta = direction.rowDelta
  let columnDelta = direction.columnDelta
  if (topRow + rowDelta < 0) {
    rowDelta = -topRow
  } else if (bottomRow + rowDelta > maxRow) {
    rowDelta = maxRow - bottomRow
  }
  if (leftColumn + columnDelta < 0) {
    columnDelta = -leftColumn
  } else if (rightColumn + columnDelta > maxColumn) {
    columnDelta = maxColumn - rightColumn
  }

  return {
    startRow: bounds.startRow + rowDelta,
    startColumn: bounds.startColumn + columnDelta,
    currentRow: bounds.currentRow + rowDelta,
    currentColumn: bounds.currentColumn + columnDelta,
  }
}

function extendFormulaReferenceBounds(
  bounds: FormulaReferenceRangeBounds,
  direction: FormulaReferenceArrowDirection,
) {
  return {
    ...bounds,
    currentRow: clampFormulaReferenceRow(bounds.currentRow + direction.rowDelta),
    currentColumn: clampFormulaReferenceColumn(bounds.currentColumn + direction.columnDelta),
  }
}

function getFormulaReferenceKeyboardBounds(
  target: FormulaReferenceInsertionTarget,
  direction: FormulaReferenceArrowDirection,
  extendRange: boolean,
) {
  const selectionStart = Math.min(target.selectionStart, target.selectionEnd)
  const selectionEnd = Math.max(target.selectionStart, target.selectionEnd)
  const replacementSpan = getFormulaReferenceReplacementSpan(
    target,
    target.currentValue,
    selectionStart,
    selectionEnd,
  )
  if (replacementSpan && formulaReferencePreviewRange.value) {
    return extendRange
      ? extendFormulaReferenceBounds(formulaReferencePreviewRange.value, direction)
      : moveFormulaReferenceBounds(formulaReferencePreviewRange.value, direction)
  }

  const targetCell = getFormulaReferenceTargetCell(target)
  if (!targetCell) {
    return null
  }

  const rowIndex = clampFormulaReferenceRow(targetCell.row + direction.rowDelta)
  const columnIndex = clampFormulaReferenceColumn(targetCell.column + direction.columnDelta)
  return {
    startRow: rowIndex,
    startColumn: columnIndex,
    currentRow: rowIndex,
    currentColumn: columnIndex,
  }
}

function setFormulaReferenceTargetCaret(target: FormulaReferenceEditTarget, cursorPosition: number) {
  const input = target.kind === 'inline'
    ? getEditorTextInput(target.editor)
    : getFormulaBarNativeInput()
  input?.setSelectionRange(cursorPosition, cursorPosition)
}

function applyFormulaReferenceRangeFromKeyboard(
  target: FormulaReferenceInsertionTarget,
  bounds: FormulaReferenceRangeBounds,
) {
  const editTarget = getFormulaReferenceEditTarget(target)
  const { selectionStart, selectionEnd } = getFormulaReferenceReplacementBounds(target)
  const reference = getCellReferenceRangeLabel(
    bounds.startRow,
    bounds.startColumn,
    bounds.currentRow,
    bounds.currentColumn,
  )
  const nextValue = `${target.currentValue.slice(0, selectionStart)}${reference}${target.currentValue.slice(selectionEnd)}`
  const cursorPosition = selectionStart + reference.length

  setFormulaReferenceTargetValue(editTarget, nextValue)
  setFormulaReferenceReplacementSpan(editTarget, selectionStart, reference)
  setFormulaReferencePreviewRange(bounds)
  getHotInstance()?.render()
  setFormulaReferenceTargetCaret(editTarget, cursorPosition)
}

function handleFormulaReferenceArrowKey(event: KeyboardEvent) {
  const direction = getFormulaReferenceArrowDirection(event)
  if (!direction) {
    return false
  }

  const target = getFormulaReferenceInsertionTarget()
  if (!target) {
    return false
  }

  const bounds = getFormulaReferenceKeyboardBounds(target, direction, event.shiftKey)
  if (!bounds) {
    return false
  }

  event.preventDefault()
  event.stopImmediatePropagation()
  applyFormulaReferenceRangeFromKeyboard(target, bounds)
  return true
}

function getFormulaReferenceInsertionTarget(): FormulaReferenceInsertionTarget | null {
  const editor = getActiveOpenedEditor()
  if (editor) {
    const currentValue = getActiveEditorDraft(editor)
    if (currentValue.trimStart().startsWith('=')) {
      const selection = normalizeTextSelection(getEditorTextInput(editor), currentValue)
      return {
        kind: 'inline',
        editor,
        currentValue,
        ...selection,
      }
    }
  }

  if (!isFormulaReferencePickMode()) {
    return null
  }

  const currentValue = formulaBarDraft.value
  const selection = normalizeTextSelection(getFormulaBarNativeInput(), currentValue)
  return {
    kind: 'formula-bar',
    currentValue,
    ...selection,
  }
}

function isInlineFormulaReferencePickMode() {
  const editor = getActiveOpenedEditor()
  if (!editor) {
    return false
  }
  return getActiveEditorDraft(editor).trimStart().startsWith('=')
}

function stopFormulaReferenceCellSelection(event: MouseEvent, controller?: CellMouseSelectionController) {
  if (controller) {
    controller.row = false
    controller.column = false
    controller.cell = false
  }
  event.preventDefault()
  event.stopImmediatePropagation()
}

function focusFormulaReferenceTarget(target: FormulaReferenceEditTarget, cursorPosition: number) {
  void nextTick(() => {
    if (target.kind === 'inline') {
      const input = getEditorTextInput(target.editor)
      input?.setSelectionRange(cursorPosition, cursorPosition)
      target.editor.focus?.()
      return
    }

    const input = getFormulaBarNativeInput()
    input?.setSelectionRange(cursorPosition, cursorPosition)
    formulaBarInputRef.value?.focus?.()
  })
}

function setFormulaReferenceTargetValue(
  target: FormulaReferenceEditTarget,
  value: string,
) {
  if (target.kind === 'inline') {
    target.editor.setValue?.(value)
    const input = getEditorTextInput(target.editor)
    if (input) {
      input.value = value
    }
    syncFormulaBarFromInlineEditor(target.editor, value)
  } else {
    formulaBarDraft.value = value
    const input = getFormulaBarNativeInput()
    if (input && input.value !== value) {
      input.value = value
    }
  }
}

function blurFormulaBarInput() {
  formulaBarFocused.value = false
  formulaBarInputRef.value?.blur?.()
  getFormulaBarNativeInput()?.blur()
}

function clearFormulaReferencePointerDownReset() {
  if (formulaReferencePointerDownResetTimer == null) {
    return
  }
  window.clearTimeout(formulaReferencePointerDownResetTimer)
  formulaReferencePointerDownResetTimer = null
}

function markFormulaReferencePointerDown() {
  formulaReferencePointerDown = true
  clearFormulaReferencePointerDownReset()
  formulaReferencePointerDownResetTimer = window.setTimeout(() => {
    formulaReferencePointerDown = false
    formulaReferencePointerDownResetTimer = null
    if (formulaBarFocused.value && document.activeElement !== getFormulaBarNativeInput()) {
      formulaBarFocused.value = false
      commitFormulaBarDraft()
    }
  }, 300)
}

function paintFormulaReferencePreview() {
  const root = sheetFrameRef.value
  if (!root) {
    return
  }

  root
    .querySelectorAll('.sheet-cell-formula-reference-preview')
    .forEach((cell) => cell.classList.remove('sheet-cell-formula-reference-preview'))

  const bounds = normalizeFormulaReferenceRangeBounds(formulaReferencePreviewRange.value)
  const hot = getHotInstance()
  if (!bounds || !hot) {
    return
  }

  for (let rowIndex = bounds.startRow; rowIndex <= bounds.endRow; rowIndex += 1) {
    for (let columnIndex = bounds.startColumn; columnIndex <= bounds.endColumn; columnIndex += 1) {
      hot.getCell(rowIndex, columnIndex)?.classList.add('sheet-cell-formula-reference-preview')
    }
  }
}

function setFormulaReferencePreviewRange(bounds: FormulaReferenceRangeBounds | null) {
  if (areFormulaReferenceRangeBoundsEqual(formulaReferencePreviewRange.value, bounds)) {
    return
  }
  formulaReferencePreviewRange.value = bounds
  paintFormulaReferencePreview()
}

function clearFormulaReferencePreviewRange() {
  if (!formulaReferencePreviewRange.value) {
    return
  }
  setFormulaReferencePreviewRange(null)
}

function renderFormulaReferenceRange(state: FormulaReferenceRangeState) {
  const reference = getCellReferenceRangeLabel(
    state.startRow,
    state.startColumn,
    state.currentRow,
    state.currentColumn,
  )
  const nextValue = `${state.prefix}${reference}${state.suffix}`
  const referenceStart = state.prefix.length
  state.cursorPosition = referenceStart + reference.length
  setFormulaReferenceTargetValue(state.target, nextValue)
  setFormulaReferenceReplacementSpan(state.target, referenceStart, reference)
  setFormulaReferencePreviewRange({
    startRow: state.startRow,
    startColumn: state.startColumn,
    currentRow: state.currentRow,
    currentColumn: state.currentColumn,
  })
}

function beginFormulaReferenceRange(rowIndex: number, columnIndex: number) {
  const target = getFormulaReferenceInsertionTarget()
  if (!target) {
    return false
  }

  clearScheduledFormulaReferenceRangeFinish()
  const { selectionStart, selectionEnd } = getFormulaReferenceReplacementBounds(target)
  formulaReferenceRangeState = {
    target: target.kind === 'inline'
      ? { kind: 'inline', editor: target.editor }
      : { kind: 'formula-bar' },
    prefix: target.currentValue.slice(0, selectionStart),
    suffix: target.currentValue.slice(selectionEnd),
    startRow: rowIndex,
    startColumn: columnIndex,
    currentRow: rowIndex,
    currentColumn: columnIndex,
    cursorPosition: selectionStart,
  }
  window.addEventListener('mouseup', handleFormulaReferenceWindowMouseUp, true)
  renderFormulaReferenceRange(formulaReferenceRangeState)
  return true
}

function updateFormulaReferenceRange(rowIndex: number, columnIndex: number) {
  if (!formulaReferenceRangeState) {
    return
  }

  if (
    formulaReferenceRangeState.currentRow === rowIndex
    && formulaReferenceRangeState.currentColumn === columnIndex
  ) {
    return
  }

  formulaReferenceRangeState.currentRow = rowIndex
  formulaReferenceRangeState.currentColumn = columnIndex
  renderFormulaReferenceRange(formulaReferenceRangeState)
}

function finishFormulaReferenceRange(options: { restoreFocus?: boolean } = {}) {
  clearScheduledFormulaReferenceRangeFinish()
  const finishedState = formulaReferenceRangeState
  formulaReferenceRangeState = null
  window.removeEventListener('mouseup', handleFormulaReferenceWindowMouseUp, true)
  if (options.restoreFocus && finishedState) {
    focusFormulaReferenceTarget(finishedState.target, finishedState.cursorPosition)
  }
}

function handleFormulaReferenceWindowMouseUp() {
  scheduleFormulaReferenceRangeFinish({ restoreFocus: true })
}

function clearScheduledFormulaReferenceRangeFinish() {
  if (formulaReferenceRangeFinishTimer == null) {
    return
  }
  window.clearTimeout(formulaReferenceRangeFinishTimer)
  formulaReferenceRangeFinishTimer = null
}

function scheduleFormulaReferenceRangeFinish(options: { restoreFocus?: boolean } = {}) {
  clearScheduledFormulaReferenceRangeFinish()
  formulaReferenceRangeFinishTimer = window.setTimeout(() => {
    formulaReferenceRangeFinishTimer = null
    finishFormulaReferenceRange(options)
  }, 0)
}

function insertFormulaReferenceIntoDraft(rowIndex: number, columnIndex: number) {
  const reference = getCellReferenceLabel(rowIndex, columnIndex)
  const input = getFormulaBarNativeInput()
  const currentValue = formulaBarDraft.value
  const selection = normalizeTextSelection(input, currentValue)
  const target: FormulaReferenceInsertionTarget = {
    kind: 'formula-bar',
    currentValue,
    ...selection,
  }
  const { selectionStart, selectionEnd } = getFormulaReferenceReplacementBounds(target)
  const nextValue = `${currentValue.slice(0, selectionStart)}${reference}${currentValue.slice(selectionEnd)}`

  formulaBarDraft.value = nextValue
  setFormulaReferenceReplacementSpan({ kind: 'formula-bar' }, selectionStart, reference)
  void nextTick(() => {
    const nextInput = getFormulaBarNativeInput()
    const cursorPosition = selectionStart + reference.length
    nextInput?.setSelectionRange(cursorPosition, cursorPosition)
    formulaBarInputRef.value?.focus?.()
  })
}

function syncFormulaBarFromInlineEditor(editor: SheetActiveEditor, value: string) {
  if (typeof editor.row !== 'number' || typeof editor.col !== 'number') {
    return
  }
  formulaBarCell.value = {
    row: editor.row,
    column: editor.col,
    documentRow: getDocumentRowIndex(editor.row),
  }
  formulaBarDraft.value = value
}

function syncInlineEditorToCellEditText(editor: SheetActiveEditor) {
  if (typeof editor.row !== 'number' || typeof editor.col !== 'number') {
    return
  }

  const rawValue = getRawCellValue(editor.row, editor.col)
  const editText = getCellEditText(editor.col, rawValue)
  if (editText === rawValue || getActiveEditorDraft(editor) !== rawValue) {
    return
  }

  editor.setValue?.(editText)
  const input = getEditorTextInput(editor)
  if (input) {
    input.value = editText
    input.select()
  }
}

function isEditorForFormulaBarCell(editor: SheetActiveEditor) {
  const cell = formulaBarCell.value
  return !!cell && editor.row === cell.row && editor.col === cell.column
}

function syncFormulaBarFromActiveInlineEditor() {
  const editor = getActiveOpenedEditor()
  if (!editor) {
    return false
  }

  syncFormulaBarFromInlineEditor(editor, getActiveEditorDraft(editor))
  return true
}

function clearScheduledInlineEditorFormulaBarSync() {
  if (inlineEditorFormulaBarSyncTimer == null) {
    return
  }
  window.clearTimeout(inlineEditorFormulaBarSyncTimer)
  inlineEditorFormulaBarSyncTimer = null
}

function scheduleInlineEditorFormulaBarSync() {
  clearScheduledInlineEditorFormulaBarSync()
  if (!getActiveOpenedEditor()) {
    return
  }
  inlineEditorFormulaBarSyncTimer = window.setTimeout(() => {
    inlineEditorFormulaBarSyncTimer = null
    syncFormulaBarFromActiveInlineEditor()
  }, 0)
}

function syncActiveInlineEditorFromFormulaBarDraft(value: string) {
  const editor = getActiveOpenedEditor()
  if (!editor || !isEditorForFormulaBarCell(editor)) {
    return
  }

  editor.setValue?.(value)
  const input = getEditorTextInput(editor)
  if (input && input.value !== value) {
    input.value = value
  }
}

function handleInlineEditorInput(event: Event) {
  const editor = getActiveOpenedEditor()
  const input = editor ? getEditorTextInput(editor) : null
  if (!editor || event.target !== input) {
    return
  }

  clearFormulaReferenceReplacementSpan()
  syncFormulaBarFromInlineEditor(editor, input.value)
}

function handleAfterBeginEditing() {
  const editor = getActiveOpenedEditor()
  if (editor) {
    syncInlineEditorToCellEditText(editor)
  }
  scheduleInlineEditorFormulaBarSync()
}

function handleBeforeKeyDown(event: KeyboardEvent) {
  if (handleFormulaReferenceArrowKey(event)) {
    return false
  }
  return undefined
}

function handleAfterDocumentKeyDown(event: KeyboardEvent) {
  if (isFormulaReferenceEditingKey(event)) {
    clearFormulaReferenceReplacementSpan()
  }
  scheduleInlineEditorFormulaBarSync()
}

function handleFormulaBarKeyDown(event: KeyboardEvent) {
  if (handleFormulaReferenceArrowKey(event)) {
    return
  }
  if (isFormulaReferenceEditingKey(event)) {
    clearFormulaReferenceReplacementSpan()
  }
}

function insertFormulaReferenceIntoActiveEditor(rowIndex: number, columnIndex: number) {
  const editor = getActiveOpenedEditor()
  if (!editor) {
    return false
  }

  const reference = getCellReferenceLabel(rowIndex, columnIndex)
  const input = getEditorTextInput(editor)
  const currentValue = getActiveEditorDraft(editor)
  const target: FormulaReferenceInsertionTarget = {
    kind: 'inline',
    editor,
    currentValue,
    ...normalizeTextSelection(input, currentValue),
  }
  const { selectionStart, selectionEnd } = getFormulaReferenceReplacementBounds(target)
  const nextValue = `${currentValue.slice(0, selectionStart)}${reference}${currentValue.slice(selectionEnd)}`

  editor.setValue?.(nextValue)
  if (input) {
    input.value = nextValue
  }
  syncFormulaBarFromInlineEditor(editor, nextValue)
  setFormulaReferenceReplacementSpan({ kind: 'inline', editor }, selectionStart, reference)

  void nextTick(() => {
    const nextInput = getEditorTextInput(editor)
    const cursorPosition = selectionStart + reference.length
    nextInput?.setSelectionRange(cursorPosition, cursorPosition)
    editor.focus?.()
  })
  return true
}

function commitFormulaBarDraft() {
  const cell = formulaBarCell.value
  if (!cell) {
    return
  }
  if (!canEditDataColumn(cell.column)) {
    syncFormulaBarDraftFromSelectedCell(true)
    clearFormulaReferenceReplacementSpan()
    clearFormulaReferencePreviewRange()
    return
  }

  const nextValue = normalizeCellInputValueForColumn(formulaBarDraft.value, cell.column)
  if (nextValue === getRawCellValue(cell.row, cell.column)) {
    formulaBarDraft.value = getCellEditText(cell.column, nextValue)
    clearFormulaReferenceReplacementSpan()
    clearFormulaReferencePreviewRange()
    return
  }

  const hot = getHotInstance()
  if (hot) {
    hot.setDataAtCell(cell.row, cell.column, nextValue, 'formula-bar')
  } else {
    const nextRows = rows.value.map((row) => normalizeRow(row, columnHeaders.value))
    if (!nextRows[cell.row]) {
      nextRows[cell.row] = createEmptyRow(columnHeaders.value.length)
    }
    nextRows[cell.row][cell.column] = nextValue
    rows.value = nextRows
  }

  syncRowsFromGrid()
  syncFormulaBarDraftFromSelectedCell(true)
  clearFormulaReferenceReplacementSpan()
  clearFormulaReferencePreviewRange()
  getHotInstance()?.render()
  void refreshComputedRowHeights()
}

function setFormulaBarCell(rowIndex: number, columnIndex: number) {
  if (
    !Number.isInteger(rowIndex)
    || !Number.isInteger(columnIndex)
    || rowIndex < 0
    || columnIndex < 0
    || rowIndex >= rows.value.length
    || columnIndex >= columnHeaders.value.length
  ) {
    clearFormulaBarSelection()
    return
  }

  if (formulaBarFocused.value && !isSameFormulaBarCell(rowIndex, columnIndex)) {
    commitFormulaBarDraft()
  }

  formulaBarCell.value = {
    row: rowIndex,
    column: columnIndex,
    documentRow: getDocumentRowIndex(rowIndex),
  }
  scheduleFormulaBarDraftFromSelectedCell(true)
}

function updateFormulaBarDraft(value: string) {
  if (!canEditFormulaBarCell()) {
    syncFormulaBarDraftFromSelectedCell(true)
    return
  }
  clearFormulaReferenceReplacementSpan()
  formulaBarDraft.value = value
  syncActiveInlineEditorFromFormulaBarDraft(value)
}

function handleFormulaBarFocus() {
  clearScheduledFormulaBarDraftSync()
  syncFormulaBarDraftFromSelectedCell(true)
  formulaBarFocused.value = true
}

function handleFormulaBarBlur() {
  if (formulaReferencePointerDown) {
    return
  }
  formulaBarFocused.value = false
  commitFormulaBarDraft()
}

function resetFormulaBarDraft() {
  clearFormulaReferenceReplacementSpan()
  syncFormulaBarDraftFromSelectedCell(true)
}

function commitFormulaBarDraftAndExit() {
  commitFormulaBarDraft()
  blurFormulaBarInput()
}

function resetFormulaBarDraftAndExit() {
  resetFormulaBarDraft()
  clearFormulaReferencePreviewRange()
  blurFormulaBarInput()
}

function handleAfterSelection(row: number, column: number) {
  refreshContextMenuFallbackSelectionState()
  if (formulaReferenceRangeState) {
    syncColumnMarkerSelection()
    return
  }

  if (formulaReferencePointerDown && row >= 0 && column >= 0 && isFormulaReferencePickMode()) {
    clearFormulaReferencePointerDownReset()
    formulaReferencePointerDown = false
    insertFormulaReferenceIntoDraft(row, column)
    syncColumnMarkerSelection()
    return
  }

  clearFormulaReferencePointerDownReset()
  formulaReferencePointerDown = false
  clearFormulaReferencePreviewRange()
  setFormulaBarCell(row, column)
  syncColumnMarkerSelection()
}

function handleAfterDeselect() {
  clearFormulaBarSelection()
  clearColumnMarkerSelection()
  hasContextMenuFallbackSelection.value = false
}

function getSingleSelectedDataCell() {
  const selection = getHotInstance()?.getSelectedLast()
  if (!selection) {
    return null
  }

  const startRow = Math.min(selection[0], selection[2])
  const endRow = Math.max(selection[0], selection[2])
  const startColumn = Math.min(selection[1], selection[3])
  const endColumn = Math.max(selection[1], selection[3])
  if (
    startRow < 0
    || startColumn < 0
    || startRow !== endRow
    || startColumn !== endColumn
  ) {
    return null
  }

  return {
    row: startRow,
    column: startColumn,
    documentRow: getDocumentRowIndex(startRow),
  }
}

function getSelectedDataCells(): SelectedDataCell[] {
  const hot = getHotInstance()
  const selections = hot?.getSelected?.() as number[][] | undefined
  if (!selections?.length || rows.value.length <= 0 || columnHeaders.value.length <= 0) {
    return []
  }

  const cells: SelectedDataCell[] = []
  const seen = new Set<string>()
  for (const selection of selections) {
    if (!Array.isArray(selection) || selection.length < 4) {
      continue
    }

    const startRow = Math.max(Math.min(selection[0], selection[2]), 0)
    const endRow = Math.min(Math.max(selection[0], selection[2]), rows.value.length - 1)
    const startColumn = Math.max(Math.min(selection[1], selection[3]), 0)
    const endColumn = Math.min(Math.max(selection[1], selection[3]), columnHeaders.value.length - 1)
    if (startRow > endRow || startColumn > endColumn) {
      continue
    }

    for (let row = startRow; row <= endRow; row += 1) {
      const documentRow = getDocumentRowIndex(row)
      for (let column = startColumn; column <= endColumn; column += 1) {
        const key = createCellMetaKey(documentRow, column)
        if (seen.has(key)) {
          continue
        }
        seen.add(key)
        cells.push({ row, column, documentRow })
      }
    }
  }

  return cells
}

function hasSingleDataCellSelection() {
  return !!getSingleSelectedDataCell()
}

function hasDataCellSelection() {
  return getSelectedDataCells().length > 0
}

function getCellMetaAt(documentRow: number, columnIndex: number) {
  return cellMeta.value[createCellMetaKey(documentRow, columnIndex)] ?? null
}

function getCellLinkAt(documentRow: number, columnIndex: number) {
  return getCellMetaAt(documentRow, columnIndex)?.link ?? null
}

function getCellStyleAt(documentRow: number, columnIndex: number) {
  return getCellMetaAt(documentRow, columnIndex)?.style ?? null
}

function hasSelectedCellLink() {
  const cell = getSingleSelectedDataCell()
  return !!cell && !!getCellLinkAt(cell.documentRow, cell.column)
}

function hasSelectedCellStyle() {
  return getSelectedDataCells().some((cell) => !!getCellStyleAt(cell.documentRow, cell.column))
}

function updateCellMetaEntry(
  documentRow: number,
  columnIndex: number,
  updater: (entry: SheetCellMeta) => SheetCellMeta,
) {
  const nextMeta = { ...cellMeta.value }
  const key = createCellMetaKey(documentRow, columnIndex)
  const nextEntry = normalizeCellMetaEntry(updater({ ...(nextMeta[key] ?? {}) }))
  if (nextEntry) {
    nextMeta[key] = nextEntry
  } else {
    delete nextMeta[key]
  }
  cellMeta.value = normalizeCellMetaMap(nextMeta, columnHeaders.value.length)
  getHotInstance()?.render()
}

function setCellLink(documentRow: number, columnIndex: number, url: string) {
  const normalizedUrl = normalizeHyperlinkUrl(url)
  updateCellMetaEntry(documentRow, columnIndex, (entry) => {
    const nextEntry = { ...entry }
    if (normalizedUrl) {
      nextEntry.link = { url: normalizedUrl }
    } else {
      delete nextEntry.link
    }
    return nextEntry
  })
}

function setCellStyle(documentRow: number, columnIndex: number, styleSource: unknown) {
  const normalizedStyle = normalizeCellStyle(styleSource)
  updateCellMetaEntry(documentRow, columnIndex, (entry) => {
    const nextEntry = { ...entry }
    if (normalizedStyle) {
      nextEntry.style = normalizedStyle
    } else {
      delete nextEntry.style
    }
    return nextEntry
  })
}

function updateCellMetaEntries(
  cells: SelectedDataCell[],
  updater: (entry: SheetCellMeta, cell: SelectedDataCell) => SheetCellMeta,
) {
  if (!cells.length) {
    return
  }

  const nextMeta = { ...cellMeta.value }
  cells.forEach((cell) => {
    const key = createCellMetaKey(cell.documentRow, cell.column)
    const nextEntry = normalizeCellMetaEntry(updater({ ...(nextMeta[key] ?? {}) }, cell))
    if (nextEntry) {
      nextMeta[key] = nextEntry
    } else {
      delete nextMeta[key]
    }
  })
  cellMeta.value = normalizeCellMetaMap(nextMeta, columnHeaders.value.length)
  getHotInstance()?.render()
}

function getCommonSelectedCellStyle(cells: SelectedDataCell[], field: SheetCellStyleField) {
  if (!cells.length) {
    return ''
  }

  const firstValue = getCellStyleAt(cells[0].documentRow, cells[0].column)?.[field] ?? ''
  const hasMixedValue = cells.some((cell) => (
    (getCellStyleAt(cell.documentRow, cell.column)?.[field] ?? '') !== firstValue
  ))
  return hasMixedValue ? '' : firstValue
}

function getCellStyleDraftModelValue(field: SheetCellStyleField) {
  return cellStyleDraft.value[field] || (
    field === 'text_color' ? DEFAULT_CELL_TEXT_COLOR : DEFAULT_CELL_BACKGROUND_COLOR
  )
}

function getCellStyleDraftSwatchStyle(field: SheetCellStyleField) {
  const color = normalizeCssColor(cellStyleDraft.value[field])
  return color
    ? { backgroundColor: color }
    : { backgroundImage: 'linear-gradient(135deg, transparent 0 46%, #dcdfe6 46% 54%, transparent 54% 100%)' }
}

function setCellStyleDraftColor(field: SheetCellStyleField, value: string) {
  cellStyleDraftTouched.value[field] = true
  cellStyleDraft.value = {
    ...cellStyleDraft.value,
    [field]: normalizeCssColor(value),
  }
}

function clearCellStyleDraftColor(field: SheetCellStyleField) {
  setCellStyleDraftColor(field, '')
}

function handleCellStyleColorPopoverVisibleChange(field: SheetCellStyleField, visible: boolean) {
  activeCellStyleColorField.value = visible ? field : null
}

function applyCellStyleToSelectedCells(cells: SelectedDataCell[]) {
  const touched = cellStyleDraftTouched.value
  if (!touched.background_color && !touched.text_color) {
    return
  }

  updateCellMetaEntries(cells, (entry) => {
    const nextEntry = { ...entry }
    const nextStyle: SheetCellStyle = { ...(nextEntry.style ?? {}) }
    if (touched.background_color) {
      const backgroundColor = normalizeCssColor(cellStyleDraft.value.background_color)
      if (backgroundColor) {
        nextStyle.background_color = backgroundColor
      } else {
        delete nextStyle.background_color
      }
    }
    if (touched.text_color) {
      const textColor = normalizeCssColor(cellStyleDraft.value.text_color)
      if (textColor) {
        nextStyle.text_color = textColor
      } else {
        delete nextStyle.text_color
      }
    }

    if (nextStyle.background_color || nextStyle.text_color) {
      nextEntry.style = nextStyle
    } else {
      delete nextEntry.style
    }
    return nextEntry
  })
}

function removeSelectedCellStyle() {
  if (!ensureCanEditConfig()) {
    return
  }
  const cells = getSelectedDataCells()
  if (!cells.length) {
    return
  }
  updateCellMetaEntries(cells, (entry) => {
    const nextEntry = { ...entry }
    delete nextEntry.style
    return nextEntry
  })
}

function openSelectedCellStyleDialog() {
  if (!ensureCanEditConfig()) {
    return
  }
  const cells = getSelectedDataCells()
  if (!cells.length) {
    return
  }

  cellStyleDialogCells.value = cells
  activeCellStyleColorField.value = null
  cellStyleDraft.value = {
    background_color: getCommonSelectedCellStyle(cells, 'background_color'),
    text_color: getCommonSelectedCellStyle(cells, 'text_color'),
  }
  cellStyleDraftTouched.value = {
    background_color: false,
    text_color: false,
  }
  cellStyleDialogVisible.value = true
}

function closeCellStyleDialog() {
  cellStyleDialogVisible.value = false
  cellStyleDialogCells.value = []
  activeCellStyleColorField.value = null
  cellStyleDraft.value = {
    background_color: '',
    text_color: '',
  }
  cellStyleDraftTouched.value = {
    background_color: false,
    text_color: false,
  }
}

function applyCellStyleDialog() {
  if (!ensureCanEditConfig()) {
    closeCellStyleDialog()
    return
  }
  const cells = [...cellStyleDialogCells.value]
  if (!cells.length) {
    closeCellStyleDialog()
    return
  }

  applyCellStyleToSelectedCells(cells)
  closeCellStyleDialog()
}

function clearCellStyleDialog() {
  if (!ensureCanEditConfig()) {
    closeCellStyleDialog()
    return
  }
  const cells = [...cellStyleDialogCells.value]
  if (cells.length) {
    updateCellMetaEntries(cells, (entry) => {
      const nextEntry = { ...entry }
      delete nextEntry.style
      return nextEntry
    })
  }
  closeCellStyleDialog()
}

function openCellLink(link: SheetCellLink | null) {
  if (!link?.url || typeof window === 'undefined') {
    return
  }

  window.open(link.url, '_blank', 'noopener,noreferrer')
}

function openSelectedCellLink() {
  const cell = getSingleSelectedDataCell()
  if (!cell) {
    return
  }
  openCellLink(getCellLinkAt(cell.documentRow, cell.column))
}

function removeSelectedCellLink() {
  if (!ensureCanEditConfig()) {
    return
  }
  const cell = getSingleSelectedDataCell()
  if (!cell) {
    return
  }
  setCellLink(cell.documentRow, cell.column, '')
}

function openSelectedCellLinkDialog() {
  if (!ensureCanEditConfig()) {
    return
  }
  const cell = getSingleSelectedDataCell()
  if (!cell) {
    return
  }

  cellLinkDialogCell.value = {
    row: cell.documentRow,
    column: cell.column,
  }
  cellLinkDraftUrl.value = getCellLinkAt(cell.documentRow, cell.column)?.url ?? ''
  cellLinkDialogVisible.value = true
}

function closeCellLinkDialog() {
  cellLinkDialogVisible.value = false
  cellLinkDialogCell.value = null
  cellLinkDraftUrl.value = ''
}

function applyCellLinkDialog() {
  if (!ensureCanEditConfig()) {
    closeCellLinkDialog()
    return
  }
  const cell = cellLinkDialogCell.value
  if (!cell) {
    closeCellLinkDialog()
    return
  }

  const normalizedUrl = normalizeHyperlinkUrl(cellLinkDraftUrl.value)
  if (!normalizedUrl) {
    ElMessage.warning('请输入有效的链接地址')
    return
  }

  setCellLink(cell.row, cell.column, normalizedUrl)
  closeCellLinkDialog()
}

function getColumnDisplayMode(columnIndex: number): ColumnDisplayMode {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return DEFAULT_COLUMN_DISPLAY_MODE
  }
  return normalizeColumnDisplayMode(columnConfigs.value[header]?.display_mode)
}

function resolveColumnTextAlign(config: SheetColumnConfig | ColumnSettingsDraft | undefined) {
  const align = normalizeColumnTextAlign(config?.align)
  if (align !== 'auto') {
    return align
  }

  const valueType = normalizeColumnValueType(config?.value_type)
  if (valueType === 'number' || valueType === 'percent' || valueType === 'date') {
    return 'right'
  }
  return 'left'
}

function isColumnValueValidByType(value: unknown, type: ColumnValueType, allowEmpty = true) {
  const text = normalizeCellValue(value)
  if (!text) {
    return allowEmpty
  }

  switch (type) {
    case 'number':
      return (typeof value === 'number' && Number.isFinite(value))
        || /^[-+]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(text)
    case 'percent':
      return parsePercentDisplayNumber(value) != null
    case 'date':
      return !!parseDateDisplayValue(value)
    case 'phone':
      return /^\d{11}$/.test(text)
    default:
      return true
  }
}

function normalizeColumnValuesForValueType(columnIndex: number, valueType: ColumnValueType) {
  if (valueType !== 'multi_text') {
    return false
  }

  let changed = false
  const nextRows = rows.value.map((row) => {
    const nextRow = normalizeRow(row, columnHeaders.value)
    const currentValue = normalizeCellValue(nextRow[columnIndex])
    const nextValue = normalizeMultiTextValue(currentValue)
    if (nextValue !== currentValue) {
      nextRow[columnIndex] = nextValue
      changed = true
    }
    return nextRow
  })

  if (!changed) {
    return false
  }

  rows.value = nextRows
  getHotInstance()?.updateSettings({ data: nextRows })
  return true
}

function getColumnSettingsDraftForColumn(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  const draft = normalizeColumnConfig(header ? columnConfigs.value[header] : null)
  draft.width_value = normalizeColumnWidthValue(getEffectiveColumnWidth(columnIndex))
  return draft
}

function getColumnSettingsIndexesFromBounds(bounds: { start: number; end: number } | null) {
  if (!bounds) {
    return []
  }

  const start = Math.max(0, Math.min(bounds.start, columnHeaders.value.length - 1))
  const end = Math.max(0, Math.min(bounds.end, columnHeaders.value.length - 1))
  const indexes: number[] = []
  for (let index = Math.min(start, end); index <= Math.max(start, end); index += 1) {
    indexes.push(index)
  }
  return indexes
}

function createColumnSettingsDraftForSelection(indexes: number[]) {
  if (!indexes.length) {
    return {
      draft: createDefaultColumnSettingsDraft(),
      mixed: createColumnSettingsMixedState(),
    }
  }

  const drafts = indexes.map((index) => getColumnSettingsDraftForColumn(index))
  const draft = { ...drafts[0] }
  const mixed = createColumnSettingsMixedState()
  for (const key of COLUMN_SETTINGS_KEYS) {
    mixed[key] = drafts.some((item) => item[key] !== draft[key])
  }
  return { draft, mixed }
}

function markColumnSettingTouched(key: ColumnSettingsDraftKey) {
  if (columnSettingsTouched.value[key]) {
    return
  }
  columnSettingsTouched.value = {
    ...columnSettingsTouched.value,
    [key]: true,
  }
}

function isColumnSettingMixed(key: ColumnSettingsDraftKey) {
  return isColumnSettingsMultiSelection.value
    && columnSettingsMixed.value[key]
    && !columnSettingsTouched.value[key]
}

function getColumnSettingSelectModel(key: ColumnSettingsDraftKey) {
  return isColumnSettingMixed(key) ? '' : String(columnSettingsDraft.value[key] ?? '')
}

function getColumnSettingTextModel(key: 'display_format' | 'note') {
  return isColumnSettingMixed(key) ? '' : columnSettingsDraft.value[key]
}

function getColumnSettingNumberModel(key: 'font_size' | 'width_value') {
  return isColumnSettingMixed(key) ? undefined : columnSettingsDraft.value[key]
}

function getColumnSettingCheckboxModel(
  key: 'allow_empty' | 'trim_whitespace' | 'duplicate_value_highlight',
) {
  return !isColumnSettingMixed(key) && columnSettingsDraft.value[key]
}

function setColumnSettingsValueType(value: unknown) {
  columnSettingsDraft.value.value_type = normalizeColumnValueType(value)
  handleColumnSettingsValueTypeChange(columnSettingsDraft.value.value_type)
}

function setColumnSettingsDisplayFormat(value: unknown) {
  columnSettingsDraft.value.display_format = String(value ?? '')
  markColumnSettingTouched('display_format')
}

function setColumnSettingsDisplayMode(value: unknown) {
  columnSettingsDraft.value.display_mode = normalizeColumnDisplayMode(value)
  markColumnSettingTouched('display_mode')
}

function setColumnSettingsAlign(value: unknown) {
  columnSettingsDraft.value.align = normalizeColumnTextAlign(value)
  markColumnSettingTouched('align')
}

function setColumnSettingsHashColorMode(value: unknown) {
  columnSettingsDraft.value.hash_color_mode = normalizeColumnHashColorMode(value)
  markColumnSettingTouched('hash_color_mode')
}

function setColumnSettingsHashColorTone(value: unknown) {
  columnSettingsDraft.value.hash_color_tone = normalizeColumnHashColorTone(value)
  markColumnSettingTouched('hash_color_tone')
}

function setColumnSettingsWidthMode(value: unknown) {
  columnSettingsDraft.value.width_mode = normalizeColumnWidthMode(value)
  markColumnSettingTouched('width_mode')
}

function setColumnSettingsNumberValue(key: 'font_size' | 'width_value', value: unknown) {
  if (key === 'font_size') {
    columnSettingsDraft.value.font_size = normalizeColumnFontSize(value)
  } else {
    columnSettingsDraft.value.width_value = normalizeColumnWidthValue(value)
  }
  markColumnSettingTouched(key)
}

function setColumnSettingsBooleanValue(
  key: 'allow_empty' | 'trim_whitespace' | 'duplicate_value_highlight',
  value: unknown,
) {
  columnSettingsDraft.value[key] = value === true
  markColumnSettingTouched(key)
}

function setColumnSettingsNote(value: unknown) {
  columnSettingsDraft.value.note = normalizeColumnNote(value)
  markColumnSettingTouched('note')
}

function mergeColumnSettingsDraft(
  currentConfig: ColumnSettingsDraft,
  draft: ColumnSettingsDraft,
  touched: ColumnSettingsTouchedState,
  applyAll: boolean,
) {
  if (applyAll) {
    return { ...draft }
  }

  const nextConfig = { ...currentConfig }
  for (const key of COLUMN_SETTINGS_KEYS) {
    if (touched[key]) {
      ;(nextConfig[key] as ColumnSettingsDraft[typeof key]) = draft[key]
    }
  }
  return nextConfig
}

function pickPreservedColumnConfig(currentRawConfig: SheetColumnConfig | undefined) {
  const preservedConfig: SheetColumnConfig = {}
  if (!currentRawConfig) {
    return preservedConfig
  }
  if (currentRawConfig.hidden) {
    preservedConfig.hidden = true
  }
  if (currentRawConfig.restore_index != null) {
    preservedConfig.restore_index = currentRawConfig.restore_index
  }
  if (currentRawConfig.header_background_color) {
    preservedConfig.header_background_color = currentRawConfig.header_background_color
  }
  if (currentRawConfig.header_text_color) {
    preservedConfig.header_text_color = currentRawConfig.header_text_color
  }
  return preservedConfig
}

function createStoredColumnConfig(
  nextConfig: ColumnSettingsDraft,
  preservedConfig: SheetColumnConfig,
) {
  if (
    nextConfig.value_type === 'text'
    && isDefaultColumnDisplayFormat(nextConfig.value_type, nextConfig.display_format)
    && nextConfig.allow_empty
    && nextConfig.display_mode === DEFAULT_COLUMN_DISPLAY_MODE
    && nextConfig.align === DEFAULT_COLUMN_TEXT_ALIGN
    && nextConfig.trim_whitespace
    && !nextConfig.duplicate_value_highlight
    && nextConfig.hash_color_mode === 'none'
    && nextConfig.width_mode === 'adaptive'
    && nextConfig.font_size === DEFAULT_COLUMN_FONT_SIZE
    && !nextConfig.note
    && Object.keys(preservedConfig).length === 0
  ) {
    return null
  }

  const storedConfig: SheetColumnConfig = { ...preservedConfig }
  if (nextConfig.value_type !== 'text') {
    storedConfig.value_type = nextConfig.value_type
  }
  if (!isDefaultColumnDisplayFormat(nextConfig.value_type, nextConfig.display_format)) {
    storedConfig.display_format = nextConfig.display_format
  }
  if (!nextConfig.allow_empty) {
    storedConfig.allow_empty = false
  }
  if (nextConfig.display_mode !== DEFAULT_COLUMN_DISPLAY_MODE) {
    storedConfig.display_mode = nextConfig.display_mode
  }
  if (nextConfig.align !== DEFAULT_COLUMN_TEXT_ALIGN) {
    storedConfig.align = nextConfig.align
  }
  if (!nextConfig.trim_whitespace) {
    storedConfig.trim_whitespace = false
  }
  if (nextConfig.duplicate_value_highlight) {
    storedConfig.duplicate_value_highlight = true
  }
  if (nextConfig.hash_color_mode !== 'none') {
    storedConfig.hash_color_mode = nextConfig.hash_color_mode
    if (nextConfig.hash_color_tone !== 'light') {
      storedConfig.hash_color_tone = nextConfig.hash_color_tone
    }
  }
  if (nextConfig.width_mode !== 'adaptive') {
    storedConfig.width_mode = nextConfig.width_mode
  }
  if (nextConfig.font_size !== DEFAULT_COLUMN_FONT_SIZE) {
    storedConfig.font_size = nextConfig.font_size
  }
  if (nextConfig.note) {
    storedConfig.note = nextConfig.note
  }
  return storedConfig
}

function getColumnNote(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return ''
  }
  return normalizeColumnNote(columnConfigs.value[header]?.note)
}

function getColumnSettingsTitle() {
  const selectedCount = columnSettingsSelectedCount.value
  if (selectedCount > 1) {
    return `字段设置：已选 ${selectedCount} 个字段`
  }

  const columnIndex = columnSettingsColumnIndex.value
  if (columnIndex == null) {
    return '字段设置'
  }

  const header = columnHeaders.value[columnIndex]
  return header ? `字段设置：${header}` : '字段设置'
}

function openColumnSettingsForBounds(bounds: { start: number; end: number }) {
  if (!ensureCanEditConfig()) {
    return
  }

  const indexes = getColumnSettingsIndexesFromBounds(bounds)
  if (!indexes.length) {
    return
  }

  const start = Math.min(...indexes)
  const end = Math.max(...indexes)
  const nextState = createColumnSettingsDraftForSelection(indexes)
  columnSettingsColumnIndex.value = indexes.length === 1 ? indexes[0] : null
  columnSettingsSelectionBounds.value = { start, end }
  columnSettingsDraft.value = nextState.draft
  columnSettingsMixed.value = nextState.mixed
  columnSettingsTouched.value = createColumnSettingsTouchedState()
  columnSettingsDialogVisible.value = true
}

function openColumnSettings(columnIndex: number) {
  if (!ensureCanEditConfig()) {
    return
  }

  if (columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return
  }
  openColumnSettingsForBounds({ start: columnIndex, end: columnIndex })
}

function openSelectedColumnSettings() {
  if (!ensureCanEditConfig()) {
    return
  }

  const bounds = getSelectionColumnBounds()
  if (!bounds || !isSelectedByColumnHeader()) {
    return
  }
  openColumnSettingsForBounds(bounds)
}

function closeColumnSettings() {
  columnSettingsDialogVisible.value = false
}

function handleColumnSettingsValueTypeChange(value: ColumnValueType) {
  markColumnSettingTouched('value_type')
  markColumnSettingTouched('display_format')
  if (value === 'date' || value === 'percent') {
    columnSettingsDraft.value.display_format = normalizeColumnDisplayFormat(
      columnSettingsDraft.value.display_format,
      value,
    )
    return
  }
  if (value !== 'number') {
    columnSettingsDraft.value.display_format = ''
  }
}

function applyColumnSettings() {
  if (!ensureCanEditConfig()) {
    closeColumnSettings()
    return
  }

  const indexes = getColumnSettingsIndexesFromBounds(columnSettingsSelectionBounds.value)
  if (!indexes.length) {
    closeColumnSettings()
    return
  }

  const applyAll = indexes.length === 1
  const touched = columnSettingsTouched.value
  if (!applyAll && !COLUMN_SETTINGS_KEYS.some((key) => touched[key])) {
    closeColumnSettings()
    return
  }

  const draft = normalizeColumnConfig(columnSettingsDraft.value)
  draft.width_value = normalizeColumnWidthValue(columnSettingsDraft.value.width_value)
  closeColumnSettings()
  closeColumnNotePopover()

  const currentNormalizedConfigs = normalizeColumnConfigs(columnConfigs.value, columnHeaders.value)
  const nextNormalizedConfigs = { ...currentNormalizedConfigs }
  const nextWidths = [...columnWidths.value]
  let configChanged = false
  let widthChanged = false
  let normalizedValuesChanged = false

  for (const columnIndex of indexes) {
    const header = columnHeaders.value[columnIndex]
    if (!header) {
      continue
    }

    const currentConfig = getColumnSettingsDraftForColumn(columnIndex)
    const nextConfig = mergeColumnSettingsDraft(currentConfig, draft, touched, applyAll)
    nextConfig.display_format = normalizeColumnDisplayFormat(nextConfig.display_format, nextConfig.value_type)
    nextConfig.width_value = normalizeColumnWidthValue(nextConfig.width_value)
    const currentWidthValue = normalizeColumnWidthValue(columnWidths.value[columnIndex] ?? getEffectiveColumnWidth(columnIndex))
    const nextWidthValue = normalizeColumnWidthValue(nextConfig.width_value)
    const columnValuesChanged = normalizeColumnValuesForValueType(columnIndex, nextConfig.value_type)
    normalizedValuesChanged ||= columnValuesChanged

    const preservedConfig = pickPreservedColumnConfig(currentNormalizedConfigs[header])
    const storedConfig = createStoredColumnConfig(nextConfig, preservedConfig)
    const currentStoredConfig = currentNormalizedConfigs[header] ?? null
    if (JSON.stringify(storedConfig ?? null) !== JSON.stringify(currentStoredConfig)) {
      configChanged = true
    }
    if (storedConfig) {
      nextNormalizedConfigs[header] = storedConfig
    } else {
      delete nextNormalizedConfigs[header]
    }

    const shouldRefreshAdaptiveWidth = (
      nextConfig.width_mode === 'adaptive'
      && (
        applyAll
        || touched.width_mode
        || touched.font_size
        || touched.display_format
        || touched.value_type
        || touched.align
      )
      && (
        nextConfig.width_mode !== currentConfig.width_mode
        || nextConfig.font_size !== currentConfig.font_size
        || nextConfig.display_format !== currentConfig.display_format
        || nextConfig.value_type !== currentConfig.value_type
        || nextConfig.align !== currentConfig.align
      )
    )
    if (shouldRefreshAdaptiveWidth) {
      const nextAutoWidth = getAutoColumnWidth(columnIndex, columnHeaders.value, rows.value, nextNormalizedConfigs)
      if (nextAutoWidth !== currentWidthValue) {
        nextWidths[columnIndex] = nextAutoWidth
        widthChanged = true
      }
    } else if ((applyAll || touched.width_value) && nextWidthValue !== currentWidthValue) {
      nextWidths[columnIndex] = nextWidthValue
      widthChanged = true
    }
  }

  if (!configChanged && !widthChanged && !normalizedValuesChanged) {
    return
  }

  if (configChanged) {
    columnConfigs.value = nextNormalizedConfigs
  }
  if (widthChanged) {
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
  if (!ensureCanEditConfig()) {
    return
  }

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
  if (!ensureCanEditData()) {
    return
  }

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
  if (!ensureCanEditConfig()) {
    return
  }

  const hot = getHotInstance()
  const bounds = getSelectionColumnBounds()
  if (!hot || !bounds || columnHeaders.value.length <= 1) {
    return
  }

  hot.alter('remove_col', bounds.start, bounds.end - bounds.start + 1)
}

function hideSelectedColumns() {
  if (!ensureCanEditConfig()) {
    return
  }

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
  scheduleRemoteSave(0)
}

function showHiddenColumnsFromSelection() {
  if (!ensureCanEditConfig()) {
    return
  }

  const indexes = getHiddenColumnIndexesToShowFromSelection()
  if (!indexes.length) {
    return
  }

  const nextConfigs = normalizeColumnConfigs(columnConfigs.value, columnHeaders.value)
  indexes.forEach((index) => {
    const header = columnHeaders.value[index]
    if (!header || nextConfigs[header]?.hidden !== true) {
      return
    }
    delete nextConfigs[header].hidden
    delete nextConfigs[header].restore_index
    if (Object.keys(nextConfigs[header]).length === 0) {
      delete nextConfigs[header]
    }
  })

  clearEditingColumnState()
  closeColumnNotePopover()
  columnConfigs.value = nextConfigs
  refreshGridStructure()
  void refreshComputedRowHeights()
  scheduleRemoteSave(0)
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
    const columnIndexMap = buildMoveIndexMap(columnHeaders.value.length, [currentIndex], targetIndex)
    nextHeaders = reorderItemsByMove(columnHeaders.value, [currentIndex], targetIndex)
    nextWidths = reorderItemsByMove(columnWidths.value, [currentIndex], targetIndex)
    nextRows = remapRowsWithFormulaReferenceMaps(rows.value, undefined, columnIndexMap)
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
  if (restoreIndex >= 0 && currentIndex !== restoreIndex) {
    moveCellMetaColumns([currentIndex], Math.min(Math.max(restoreIndex, 0), getVisibleColumnCount()))
  }
  columnHeaders.value = nextHeaders
  columnWidths.value = nextWidths
  rows.value = nextRows
  columnConfigs.value = nextConfigs
  refreshGridStructure()
  void refreshComputedRowHeights()
  scheduleRemoteSave(0)
}

function removeSelectedRows() {
  if (!ensureCanEditData()) {
    return
  }

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
      readOnly: true,
    }
  }

  if (getColumnDisplayMode(col) === 'single_line') {
    return {
      wordWrap: false,
      textEllipsis: true,
      readOnly: !canEditDataColumn(col),
    }
  }

  return {
    wordWrap: true,
    textEllipsis: false,
    readOnly: !canEditDataColumn(col),
  }
}

function getCellAccentStyle(rowIndex: number, columnIndex: number) {
  return cellAccentStyleMap.value.get(`${rowIndex}:${columnIndex}`) ?? null
}

function isAttendanceSummarySheetPluginEnabled() {
  return props.sheetId === ATTENDANCE_SUMMARY_SHEET_ID
    && (props.workbookId == null || props.workbookId === ATTENDANCE_SUMMARY_WORKBOOK_ID)
}

function isAttendanceCompletedRow(rowIndex: number) {
  if (!isAttendanceSummarySheetPluginEnabled() || rowIndex < 0) {
    return false
  }
  const completedColumnIndex = attendanceCompletedColumnIndex.value
  if (completedColumnIndex < 0) {
    return false
  }
  return normalizeCellValue(rows.value[rowIndex]?.[completedColumnIndex] ?? '').trim() !== ''
}

function getPluginRowBackgroundColor(rowIndex: number) {
  if (isAttendanceCompletedRow(rowIndex)) {
    return ATTENDANCE_COMPLETED_ROW_BACKGROUND
  }
  return ''
}

function getHashColorTokenOptions(
  mode: ColumnHashColorMode,
  tone: ColumnHashColorTone,
): StableVisualColorOptions {
  if (mode === 'background') {
    return {
      colorSpace: tone === 'dark' ? 'solid' : 'soft',
    }
  }
  return {}
}

function createHashColorStyleFromToken(
  mode: ColumnHashColorMode,
  toneName: ColumnHashColorTone,
  token: StableVisualToken,
): HashColorStyle | null {
  const { hue, tone } = token
  if (mode === 'background') {
    const textSaturation = Math.max(42, tone.saturation - 18)
    return {
      backgroundColor: `hsl(${hue} ${tone.saturation}% ${tone.backgroundLightness}%)`,
      color: `hsl(${hue} ${textSaturation}% ${tone.textLightness}%)`,
    }
  }

  if (mode === 'text') {
    const textLightness = toneName === 'dark' ? 26 : 44
    const textSaturation = Math.max(54, tone.saturation - 4)
    return {
      color: `hsl(${hue} ${textSaturation}% ${textLightness}%)`,
    }
  }

  return null
}

function createColumnHashColorStyleKey(
  columnIndex: number,
  mode: ColumnHashColorMode,
  tone: ColumnHashColorTone,
  seed: string,
) {
  return `${columnIndex}:${mode}:${tone}:${seed}`
}

function compareHashColorSeeds(a: string, b: string) {
  const naturalOrder = HASH_COLOR_SEED_COLLATOR.compare(a, b)
  if (naturalOrder !== 0) {
    return naturalOrder
  }
  return stableHash32(a) - stableHash32(b)
}

function getCellHashColorStyle(columnIndex: number, config: SheetColumnConfig | undefined, value: string) {
  const mode = normalizeColumnHashColorMode(config?.hash_color_mode)
  if (mode === 'none') {
    return null
  }

  const seed = normalizeCellValue(value).trim()
  if (!seed) {
    return null
  }

  const tone = normalizeColumnHashColorTone(config?.hash_color_tone)
  const columnCacheKey = createColumnHashColorStyleKey(columnIndex, mode, tone, seed)
  const columnStyle = columnHashColorStyleMap.value.get(columnCacheKey)
  if (columnStyle) {
    return columnStyle
  }

  const cacheKey = `${mode}:${tone}:${seed}`
  if (cellHashColorStyleCache.has(cacheKey)) {
    return cellHashColorStyleCache.get(cacheKey) ?? null
  }

  const token = getStableVisualToken(seed, getHashColorTokenOptions(mode, tone))
  if (!token) {
    cellHashColorStyleCache.set(cacheKey, null)
    return null
  }

  const style = createHashColorStyleFromToken(mode, tone, token)
  cellHashColorStyleCache.set(cacheKey, style)
  return style
}

function handleAfterRenderer(
  TD: HTMLTableCellElement,
  row: number,
  column: number,
  _prop: string | number,
  value: string,
) {
  const documentRow = row >= 0 ? getDocumentRowIndex(row) : -1
  const rawText = normalizeCellValue(value)
  const header = column >= 0 ? columnHeaders.value[column] ?? '' : ''
  const columnConfig = header ? columnConfigs.value[header] : undefined
  const link = documentRow >= 0 && column >= 0
    ? getCellLinkAt(documentRow, column)
    : null
  const linkDisplayText = rawText.trim() ? '' : (link?.title || link?.url || '')
  const formulaCell = row >= 0 && column >= 0 && isFormulaExpression(rawText)
    ? getFormulaCellModel(row, column)
    : null
  const formulaText = formulaCell?.text ?? null
  let renderedText = rawText
  if (formulaCell) {
    renderedText = formulaCell.text
  } else if (linkDisplayText) {
    renderedText = linkDisplayText
  } else if (row >= 0 && column >= 0) {
    renderedText = formatCellDisplayValueCached(rawText, columnConfig)
  }

  if (formulaText != null || renderedText !== rawText) {
    setRenderedCellText(TD, renderedText)
  }

  const hasFormula = formulaText != null
  const hasFormulaError = hasFormula && (isFormulaErrorValue(formulaCell?.value) || formulaCell.text.startsWith('#'))
  const isFormulaReferencePreview = row >= 0 && column >= 0 && isCellInFormulaReferencePreview(row, column)

  const cellStyle = documentRow >= 0 && column >= 0
    ? getCellStyleAt(documentRow, column)
    : null
  let backgroundColor = ''
  let textColor = ''
  let fontSizeStyle = ''
  let lineHeightStyle = ''
  let textAlignStyle = ''

  if (column >= 0) {
    backgroundColor = getPluginRowBackgroundColor(row)
    textAlignStyle = resolveColumnTextAlign(columnConfig)
    const fontSize = getColumnFontSizeFromConfig(columnConfig)
    if (fontSize !== DEFAULT_COLUMN_FONT_SIZE) {
      fontSizeStyle = `${fontSize}px`
      lineHeightStyle = `${getColumnLineHeightFromFontSize(fontSize)}px`
    }

    const hashColorStyle = getCellHashColorStyle(column, columnConfig, renderedText)
    if (hashColorStyle?.backgroundColor) {
      backgroundColor = hashColorStyle.backgroundColor
    }
    if (hashColorStyle?.color) {
      textColor = hashColorStyle.color
    }

    const accentStyle = getCellAccentStyle(row, column)
    if (accentStyle) {
      backgroundColor = accentStyle.backgroundColor
    }

    if (cellStyle?.background_color) {
      backgroundColor = cellStyle.background_color
    }
    if (cellStyle?.text_color) {
      textColor = cellStyle.text_color
    }
  }

  let title = ''

  const hasLink = !!link
  TD.classList.toggle('sheet-cell-has-link', hasLink)
  TD.classList.toggle('sheet-cell-formula', hasFormula)
  TD.classList.toggle('sheet-cell-formula-error', hasFormulaError)
  TD.classList.toggle('sheet-cell-formula-reference-preview', isFormulaReferencePreview)
  TD.style.backgroundColor = backgroundColor
  TD.style.color = textColor
  TD.style.fontSize = fontSizeStyle
  TD.style.lineHeight = lineHeightStyle
  TD.style.textAlign = textAlignStyle

  if (link) {
    TD.dataset.hyperlinkUrl = link.url
  } else if (TD.dataset.hyperlinkUrl) {
    delete TD.dataset.hyperlinkUrl
  }

  if (formulaText != null) {
    const formulaSource = rawText
    title = formulaSource && formulaSource !== formulaText
      ? `${formulaSource}\n= ${formulaText}`
      : formulaText
  } else if (renderedText !== rawText && rawText) {
    title = `${rawText}\n= ${renderedText}`
  }

  if (link) {
    title = title ? `${title}\n${link.url}` : link.url
  }
  if (title) {
    if (TD.title !== title) {
      TD.title = title
    }
  } else if (TD.title) {
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

function handleAfterColumnResize(newSize: number, column: number, isDoubleClick = false) {
  if (!canEditConfig.value) {
    return
  }
  if (column < 0 || column >= columnHeaders.value.length || !Number.isFinite(newSize) || newSize <= 0) {
    return
  }

  setColumnWidth(column, newSize, {
    commitAdaptiveWidth: isDoubleClick,
    commitFixedWidth: !isDoubleClick,
    refreshRowHeights: true,
    save: true,
  })
}

function handleAfterRowResize() {
  void updateSheetViewportHeight()
}

watch(
  hasFormulaExpressions,
  (hasFormula) => {
    if (hasFormula) {
      void ensureFormulaEngineLoaded()
    }
  },
  { immediate: true },
)

watch(
  [rows, columnHeaders, columnConfigs],
  () => {
    refreshFormulaDisplayState()
  },
  { deep: true, flush: 'post' },
)

watch(
  () => {
    const cell = formulaBarCell.value
    return cell ? getRawCellValue(cell.row, cell.column) : ''
  },
  () => {
    syncFormulaBarDraftFromSelectedCell()
  },
)

watch(
  [rows, columnHeaders, headerGroups, cellMeta, columnConfigs, columnWidths],
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
  effectiveAccessCapabilities,
  () => {
    if (!canPersistSheet.value) {
      clearSaveTimer()
      clearDraftStorage()
    }
    const hot = getHotInstance()
    if (hot) {
      hot.updateSettings({ cells: resolveCellMeta })
      hot.render()
    }
  },
  { deep: true },
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
  () => props.workbookId,
  (nextWorkbookId, previousWorkbookId) => {
    if (nextWorkbookId === previousWorkbookId || props.sheetId == null) {
      return
    }
    clearSaveTimer()
    void restoreInitialDocument().finally(() => {
      void updateSheetViewportHeight()
    })
  },
)

watch(
  [() => rows.value.length, () => columnHeaders.value.length, () => props.sheetId, showColumnMarkerRow],
  () => {
    void updateSheetViewportHeight()
  },
)

onMounted(() => {
  touchContextMenuFallbackEnabled.value = shouldEnableTouchContextMenuFallback()
  bindSheetLayoutObserver()
  window.addEventListener('resize', handleWindowResize)
  window.addEventListener('mousedown', handleGlobalMouseDown)
  document.addEventListener('input', handleInlineEditorInput, true)
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
  finishFormulaReferenceRange()
  clearScheduledFormulaBarDraftSync()
  clearScheduledInlineEditorFormulaBarSync()
  clearFormulaReferencePointerDownReset()
  cleanupColumnMarkerResize(false)
  window.removeEventListener('resize', handleWindowResize)
  window.removeEventListener('mousedown', handleGlobalMouseDown)
  document.removeEventListener('input', handleInlineEditorInput, true)
  unbindGridScrollSync()
  sheetLayoutObserver?.disconnect()
  sheetLayoutObserver = null
})

defineExpose({
  openSheetSettings,
})
</script>

<template>
  <div class="note-sheet-workspace">
    <div v-if="shouldRenderSheetContent && (showTitleInput || showBackButton)" class="sheet-topbar">
      <el-input
        v-if="showTitleInput"
        v-model="sheetTitle"
        placeholder="表格名称"
        class="sheet-title-input"
        :disabled="!canEditConfig"
      />
      <el-button v-if="showBackButton" @click="router.push(backTo)">{{ backLabel }}</el-button>
    </div>

    <div v-if="shouldRenderSheetContent" class="sheet-formula-bar">
      <div class="sheet-formula-address">{{ formulaBarAddress || '--' }}</div>
      <button
        v-if="showContextMenuFallbackButton"
        ref="contextMenuFallbackButtonRef"
        type="button"
        class="sheet-context-menu-fallback"
        aria-label="打开右键菜单"
        title="打开右键菜单"
        @click="openTouchContextMenuFallback"
        @mousedown.stop
        @touchstart.stop
      >
        右键菜单
      </button>
      <div class="sheet-formula-separator" />
      <div class="sheet-formula-prefix">fx</div>
      <el-input
        ref="formulaBarInputRef"
        :model-value="formulaBarDraft"
        class="sheet-formula-input"
        :disabled="!formulaBarCell"
        :readonly="!canEditFormulaBarCell()"
        clearable
        @update:model-value="value => updateFormulaBarDraft(String(value))"
        @focus="handleFormulaBarFocus"
        @blur="handleFormulaBarBlur"
        @keydown="handleFormulaBarKeyDown"
        @keydown.enter.exact.prevent="commitFormulaBarDraftAndExit"
        @keydown.esc.prevent="resetFormulaBarDraftAndExit"
      />
    </div>

    <div v-if="sheetId == null || shouldRenderSheetContent" ref="sheetFrameRef" class="sheet-frame" :class="{ 'is-empty': sheetId == null }">
      <div v-if="showColumnMarkerRow" class="sheet-column-marker-row" :style="columnMarkerRowStyle">
        <div
          v-if="sheetViewSettings.show_row_numbers"
          class="sheet-column-marker-corner"
          :style="columnMarkerCornerStyle"
        />
        <div class="sheet-column-marker-viewport">
          <div ref="columnMarkerContentRef" class="sheet-column-marker-content" :style="columnMarkerContentStyle">
            <div
              v-for="item in visibleColumnMarkerCells"
              :key="item.key"
              class="sheet-column-marker-cell"
              :class="{
                'is-resizing': columnMarkerResizingIndex === item.index,
                'is-selected': isColumnMarkerSelected(item.index),
              }"
              :style="{ width: `${item.width}px` }"
              @mousedown="handleColumnMarkerMouseDown($event, item.index)"
              @contextmenu="handleColumnMarkerContextMenu($event, item.index)"
            >
              <span class="sheet-column-marker-label">{{ item.label }}</span>
              <span
                v-if="canEditConfig"
                class="sheet-column-marker-resize-handle"
                title="拖拽调整列宽，双击自适应"
                aria-label="调整列宽"
                @mousedown="startColumnMarkerResize($event, item.index)"
                @dblclick="autoFitColumnFromMarker($event, item.index)"
              />
            </div>
          </div>
        </div>
      </div>
      <HotTable
        v-if="shouldRenderSheetContent"
        ref="hotTableRef"
        :data="rows"
        :language="'zh-CN'"
        :col-headers="columnHeaders"
        :nested-headers="nestedHeaders"
        :col-widths="columnWidths"
        :row-headers="sheetRowHeaders"
        :hidden-columns="{ columns: hiddenColumnIndexes, indicators: false }"
        :manual-column-resize="canEditConfig"
        :manual-column-move="canEditConfig"
        :manual-row-resize="true"
        :manual-row-move="canEditData"
        :copy-paste="true"
        :context-menu="contextMenu"
        :cells="resolveCellMeta"
        :row-heights="resolveRowHeight"
        :auto-row-size="false"
        :auto-wrap-row="false"
        :auto-wrap-col="false"
        :min-spare-rows="0"
        :render-all-rows="false"
        :height="sheetGridHeight"
        :stretch-h="'none'"
        :selection-mode="'multiple'"
        :outside-click-deselects="false"
        :theme-name="'ht-theme-main'"
        :license-key="'non-commercial-and-evaluation'"
        :before-change="handleBeforeChange"
        :before-autofill="handleBeforeAutofill"
        :before-copy="handleBeforeCopy"
        :before-paste="handleBeforePaste"
        :after-change="handleAfterChange"
        :before-create-row="handleBeforeCreateRow"
        :after-create-row="handleAfterCreateRow"
        :after-remove-row="handleAfterRemoveRow"
        :after-create-col="handleAfterCreateCol"
        :before-remove-col="handleBeforeRemoveCol"
        :after-remove-col="handleAfterRemoveCol"
        :after-column-resize="handleAfterColumnResize"
        :after-row-resize="handleAfterRowResize"
        :before-column-move="handleBeforeColumnMove"
        :before-row-move="handleBeforeRowMove"
        :before-on-cell-mouse-down="handleBeforeCellMouseDown"
        :before-on-cell-mouse-over="handleBeforeCellMouseOver"
        :before-on-cell-mouse-up="handleBeforeCellMouseUp"
        :after-on-cell-mouse-down="handleHeaderMouseDown"
        :after-begin-editing="handleAfterBeginEditing"
        :before-key-down="handleBeforeKeyDown"
        :after-document-key-down="handleAfterDocumentKeyDown"
        :after-selection="handleAfterSelection"
        :after-deselect="handleAfterDeselect"
        :after-get-col-header="handleAfterGetColHeader"
        :after-scroll-horizontally="handleAfterScrollHorizontally"
        :after-renderer="handleAfterRenderer"
      />

      <div v-else class="sheet-empty-state">{{ emptyText }}</div>
    </div>

    <div v-if="shouldRenderSheetContent && effectivePaginationEnabled" class="sheet-pagination-bar">
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
            <el-option label="数字（每页 1 / 2 / 3）" value="page_numbers" />
            <el-option label="数字（全局 101 / 102 / 103）" value="global_numbers" />
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
              <span class="sheet-settings-hidden-column-marker">{{ item.markerLabel }}</span>
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
        <div v-if="isColumnSettingsMultiSelection" class="sheet-settings-multi-hint">
          只批量覆盖本次修改的设置项，未修改项保持各字段原样。
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label-with-state">
            <span class="sheet-settings-label">类型</span>
            <span v-if="isColumnSettingMixed('value_type')" class="sheet-settings-mixed-badge">多个值</span>
          </div>
          <el-select
            :model-value="getColumnSettingSelectModel('value_type')"
            class="sheet-settings-inline-select"
            placeholder="多个值，选择后批量覆盖"
            @change="setColumnSettingsValueType"
          >
            <el-option label="文本" value="text" />
            <el-option label="多值文本" value="multi_text" />
            <el-option label="数值" value="number" />
            <el-option label="百分比" value="percent" />
            <el-option label="日期" value="date" />
            <el-option label="手机号（11位数字）" value="phone" />
          </el-select>
        </div>
        <div
          v-if="columnSettingsDraft.value_type === 'date' && !isColumnSettingMixed('value_type')"
          class="sheet-settings-inline-field"
        >
          <div
            class="sheet-settings-label-with-state"
            title="可选择预设，也可直接输入自定义格式，例如 case(is_current_year, &quot;mm/dd&quot;, &quot;yyyy/mm/dd&quot;) 或 yyyy/mm/dd hh:mm:ss 后回车"
          >
            <span class="sheet-settings-label">显示格式</span>
            <span v-if="isColumnSettingMixed('display_format')" class="sheet-settings-mixed-badge">多个值</span>
          </div>
          <el-select
            :model-value="getColumnSettingSelectModel('display_format')"
            class="sheet-settings-inline-select"
            filterable
            allow-create
            default-first-option
            placeholder="多个值，选择后批量覆盖"
            title="可输入 case(...) 条件格式；带时间的日期会自动保留时间，也可显式写 hh:mm 或 hh:mm:ss"
            @change="setColumnSettingsDisplayFormat"
          >
            <el-option label="年月日（2025/1/6）" value="yyyy/m/d" />
            <el-option label="月日（1/6）" value="m/d" />
            <el-option label="标准日期（2025-01-06）" value="yyyy-mm-dd" />
            <el-option label="补零月日（01/06）" value="mm/dd" />
            <el-option label="智能月日（今年01/06，否则2025/01/06；有时间则保留）" value="case(is_current_year, &quot;mm/dd&quot;, &quot;yyyy/mm/dd&quot;)" />
            <el-option label="智能月日到分钟（今年01/06 07:51，否则2025/01/06 07:51）" value="case(is_current_year, &quot;mm/dd hh:mm&quot;, &quot;yyyy/mm/dd hh:mm&quot;)" />
            <el-option label="中文月日（1月6日）" value="m月d日" />
          </el-select>
        </div>
        <div
          v-if="columnSettingsDraft.value_type === 'percent' && !isColumnSettingMixed('value_type')"
          class="sheet-settings-inline-field"
        >
          <div class="sheet-settings-label-with-state">
            <span class="sheet-settings-label">显示格式</span>
            <span v-if="isColumnSettingMixed('display_format')" class="sheet-settings-mixed-badge">多个值</span>
          </div>
          <el-select
            :model-value="getColumnSettingSelectModel('display_format')"
            class="sheet-settings-inline-select"
            filterable
            allow-create
            default-first-option
            placeholder="多个值，选择后批量覆盖"
            @change="setColumnSettingsDisplayFormat"
          >
            <el-option label="整数百分比（30%）" value="0%" />
            <el-option label="一位小数（29.6%）" value="0.0%" />
            <el-option label="两位小数（29.58%）" value="0.00%" />
          </el-select>
        </div>
        <el-checkbox
          :model-value="getColumnSettingCheckboxModel('allow_empty')"
          :indeterminate="isColumnSettingMixed('allow_empty')"
          @change="value => setColumnSettingsBooleanValue('allow_empty', value)"
        >
          允许空值
          <span v-if="isColumnSettingMixed('allow_empty')" class="sheet-settings-mixed-badge">多个值</span>
        </el-checkbox>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label-with-state">
            <span class="sheet-settings-label">内容显示</span>
            <span v-if="isColumnSettingMixed('display_mode')" class="sheet-settings-mixed-badge">多个值</span>
          </div>
          <el-select
            :model-value="getColumnSettingSelectModel('display_mode')"
            class="sheet-settings-inline-select"
            placeholder="多个值，选择后批量覆盖"
            @change="setColumnSettingsDisplayMode"
          >
            <el-option label="单行显示（超长省略）" value="single_line" />
            <el-option label="自动换行" value="wrap" />
          </el-select>
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label-with-state">
            <span class="sheet-settings-label">内容对齐</span>
            <span v-if="isColumnSettingMixed('align')" class="sheet-settings-mixed-badge">多个值</span>
          </div>
          <el-select
            :model-value="getColumnSettingSelectModel('align')"
            class="sheet-settings-inline-select"
            placeholder="多个值，选择后批量覆盖"
            @change="setColumnSettingsAlign"
          >
            <el-option label="自动" value="auto" />
            <el-option label="左对齐" value="left" />
            <el-option label="居中" value="center" />
            <el-option label="右对齐" value="right" />
          </el-select>
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label-with-state">
            <span class="sheet-settings-label">内容字号</span>
            <span v-if="isColumnSettingMixed('font_size')" class="sheet-settings-mixed-badge">多个值</span>
          </div>
          <div class="sheet-settings-number-inline">
            <el-input-number
              :model-value="getColumnSettingNumberModel('font_size')"
              class="sheet-settings-number-input"
              :min="MIN_COLUMN_FONT_SIZE"
              :max="MAX_COLUMN_FONT_SIZE"
              :step="1"
              controls-position="right"
              placeholder="多个值"
              @change="value => setColumnSettingsNumberValue('font_size', value)"
            />
            <span class="sheet-settings-width-unit">px</span>
          </div>
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label-with-state">
            <span class="sheet-settings-label">哈希颜色</span>
            <span
              v-if="isColumnSettingMixed('hash_color_mode') || isColumnSettingMixed('hash_color_tone')"
              class="sheet-settings-mixed-badge"
            >多个值</span>
          </div>
          <div class="sheet-settings-hash-color-inline">
            <el-select
              :model-value="getColumnSettingSelectModel('hash_color_mode')"
              class="sheet-settings-hash-mode"
              placeholder="多个值"
              @change="setColumnSettingsHashColorMode"
            >
              <el-option label="无" value="none" />
              <el-option label="哈希前景" value="text" />
              <el-option label="哈希背景" value="background" />
            </el-select>
            <el-select
              v-if="columnSettingsDraft.hash_color_mode !== 'none' && !isColumnSettingMixed('hash_color_mode')"
              :model-value="getColumnSettingSelectModel('hash_color_tone')"
              class="sheet-settings-hash-tone"
              placeholder="多个值"
              @change="setColumnSettingsHashColorTone"
            >
              <el-option label="浅色" value="light" />
              <el-option label="深色" value="dark" />
            </el-select>
          </div>
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label-with-state">
            <span class="sheet-settings-label">列宽</span>
            <span
              v-if="isColumnSettingMixed('width_mode') || isColumnSettingMixed('width_value')"
              class="sheet-settings-mixed-badge"
            >多个值</span>
          </div>
          <div class="sheet-settings-width-inline">
            <el-select
              :model-value="getColumnSettingSelectModel('width_mode')"
              class="sheet-settings-width-mode"
              placeholder="多个值"
              @change="setColumnSettingsWidthMode"
            >
              <el-option label="自适应" value="adaptive" />
              <el-option label="固定列宽" value="fixed" />
            </el-select>
            <template v-if="columnSettingsDraft.width_mode === 'fixed' && !isColumnSettingMixed('width_mode')">
              <el-input-number
                :model-value="getColumnSettingNumberModel('width_value')"
                class="sheet-settings-width-input"
                :min="MIN_COLUMN_WIDTH"
                :max="MAX_COLUMN_WIDTH"
                :step="1"
                controls-position="right"
                placeholder="多个值"
                @change="value => setColumnSettingsNumberValue('width_value', value)"
              />
              <span class="sheet-settings-width-unit">px</span>
            </template>
          </div>
        </div>
        <div class="sheet-settings-field">
          <div class="sheet-settings-label-with-state">
            <span class="sheet-settings-label">备注</span>
            <span v-if="isColumnSettingMixed('note')" class="sheet-settings-mixed-badge">多个值</span>
          </div>
          <el-input
            :model-value="getColumnSettingTextModel('note')"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 6 }"
            resize="none"
            placeholder="多个值，输入后批量覆盖"
            @input="setColumnSettingsNote"
          />
        </div>
        <el-checkbox
          :model-value="getColumnSettingCheckboxModel('trim_whitespace')"
          :indeterminate="isColumnSettingMixed('trim_whitespace')"
          @change="value => setColumnSettingsBooleanValue('trim_whitespace', value)"
        >
          去除首尾空白
          <span v-if="isColumnSettingMixed('trim_whitespace')" class="sheet-settings-mixed-badge">多个值</span>
        </el-checkbox>
        <el-checkbox
          :model-value="getColumnSettingCheckboxModel('duplicate_value_highlight')"
          :indeterminate="isColumnSettingMixed('duplicate_value_highlight')"
          @change="value => setColumnSettingsBooleanValue('duplicate_value_highlight', value)"
        >
          重复值校验
          <span v-if="isColumnSettingMixed('duplicate_value_highlight')" class="sheet-settings-mixed-badge">多个值</span>
        </el-checkbox>
      </div>
      <template #footer>
        <div class="sheet-settings-footer">
          <el-button @click="closeColumnSettings">取消</el-button>
          <el-button type="primary" @click="applyColumnSettings">保存</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="cellStyleDialogVisible"
      :title="cellStyleDialogTitle"
      width="420px"
      destroy-on-close
      append-to-body
    >
      <div class="sheet-settings-form">
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">文字颜色</div>
          <div class="sheet-color-setting-control">
            <StandardColorPickerPopover
              :model-value="getCellStyleDraftModelValue('text_color')"
              :visible="activeCellStyleColorField === 'text_color'"
              :reset-value="getCellStyleDraftModelValue('text_color')"
              placement="top-start"
              @update:model-value="value => setCellStyleDraftColor('text_color', value)"
              @update:visible="visible => handleCellStyleColorPopoverVisibleChange('text_color', visible)"
            >
              <template #reference>
                <button
                  type="button"
                  class="sheet-color-picker-trigger"
                  title="选择文字颜色"
                  aria-label="选择文字颜色"
                >
                  <span class="sheet-color-picker-swatch" :style="getCellStyleDraftSwatchStyle('text_color')" />
                </button>
              </template>
            </StandardColorPickerPopover>
            <el-input
              :model-value="cellStyleDraft.text_color"
              class="sheet-color-setting-input"
              placeholder="保持原值"
              clearable
              @update:model-value="value => setCellStyleDraftColor('text_color', String(value))"
            />
            <el-button text size="small" @click="clearCellStyleDraftColor('text_color')">清除</el-button>
          </div>
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">背景颜色</div>
          <div class="sheet-color-setting-control">
            <StandardColorPickerPopover
              :model-value="getCellStyleDraftModelValue('background_color')"
              :visible="activeCellStyleColorField === 'background_color'"
              :reset-value="getCellStyleDraftModelValue('background_color')"
              placement="top-start"
              @update:model-value="value => setCellStyleDraftColor('background_color', value)"
              @update:visible="visible => handleCellStyleColorPopoverVisibleChange('background_color', visible)"
            >
              <template #reference>
                <button
                  type="button"
                  class="sheet-color-picker-trigger"
                  title="选择背景颜色"
                  aria-label="选择背景颜色"
                >
                  <span class="sheet-color-picker-swatch" :style="getCellStyleDraftSwatchStyle('background_color')" />
                </button>
              </template>
            </StandardColorPickerPopover>
            <el-input
              :model-value="cellStyleDraft.background_color"
              class="sheet-color-setting-input"
              placeholder="保持原值"
              clearable
              @update:model-value="value => setCellStyleDraftColor('background_color', String(value))"
            />
            <el-button text size="small" @click="clearCellStyleDraftColor('background_color')">清除</el-button>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="sheet-settings-footer">
          <el-button @click="clearCellStyleDialog">清除颜色</el-button>
          <el-button @click="closeCellStyleDialog">取消</el-button>
          <el-button type="primary" @click="applyCellStyleDialog">保存</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="cellLinkDialogVisible"
      title="设置超链接"
      width="420px"
      destroy-on-close
      append-to-body
    >
      <div class="sheet-settings-form">
        <div class="sheet-settings-field">
          <div class="sheet-settings-label">链接地址</div>
          <el-input
            v-model="cellLinkDraftUrl"
            placeholder="https://..."
            clearable
            @keyup.enter="applyCellLinkDialog"
          />
        </div>
      </div>
      <template #footer>
        <div class="sheet-settings-footer">
          <el-button @click="closeCellLinkDialog">取消</el-button>
          <el-button type="primary" @click="applyCellLinkDialog">保存</el-button>
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

.sheet-formula-bar {
  display: flex;
  align-items: center;
  min-height: 34px;
  border: 1px solid #e7dcc9;
  border-radius: 8px;
  background: #fffdfa;
  overflow: hidden;
}

.sheet-formula-address {
  flex: 0 0 92px;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  height: 100%;
  padding: 0 10px;
  color: #1f2d3d;
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
}

.sheet-formula-separator {
  flex: 0 0 1px;
  align-self: stretch;
  margin: 6px 0;
  background: #eadfce;
}

.sheet-formula-prefix {
  flex: 0 0 42px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #1f2d3d;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 16px;
  font-style: italic;
  font-weight: 600;
  line-height: 1;
  border-right: 1px solid #e7dcc9;
}

.sheet-formula-input {
  flex: 1;
  min-width: 0;
}

.sheet-formula-input :deep(.el-input__wrapper) {
  min-height: 32px;
  padding: 0 10px;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.sheet-formula-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: inset 0 -1px 0 #409eff;
}

.sheet-formula-input :deep(.el-input__inner) {
  color: #1f2d3d;
  font-size: 13px;
}

.sheet-settings-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sheet-settings-multi-hint {
  padding: 7px 10px;
  border: 1px solid #eadfce;
  border-radius: 6px;
  background: #fffaf2;
  color: #8b7355;
  font-size: 12px;
  line-height: 1.4;
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

.sheet-settings-number-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.sheet-settings-number-input {
  width: 108px;
  flex: 0 0 auto;
}

.sheet-settings-hash-color-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  flex-wrap: nowrap;
}

.sheet-settings-hash-mode {
  width: 132px;
  flex: 0 0 auto;
}

.sheet-settings-hash-tone {
  width: 88px;
  flex: 0 0 auto;
}

.sheet-color-setting-control {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.sheet-color-picker-trigger {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 28px;
  padding: 0;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}

.sheet-color-picker-trigger:hover {
  border-color: #c6d7ee;
  background: #f8fbff;
}

.sheet-color-picker-swatch {
  width: 16px;
  height: 16px;
  border: 1px solid rgba(15, 23, 42, 0.14);
  border-radius: 4px;
  background-color: #fff;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.35);
}

.sheet-color-setting-input {
  flex: 1;
  min-width: 0;
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

.sheet-settings-hidden-column-marker {
  color: #9a7b4f;
  font-size: 12px;
  font-weight: 700;
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

.sheet-settings-label-with-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}

.sheet-settings-mixed-badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: #f1ede6;
  color: #8b7355;
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
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
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  overscroll-behavior: none;
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

.sheet-column-marker-row {
  display: flex;
  flex: 0 0 28px;
  height: 28px;
  min-height: 28px;
  overflow: hidden;
  border-bottom: 1px solid #e6e6e6;
  background: #f7f7f7;
}

.sheet-column-marker-corner {
  flex: 0 0 50px;
  width: 50px;
  border-right: 1px solid #e6e6e6;
  background: #f2f2f2;
}

.sheet-column-marker-viewport {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.sheet-column-marker-content {
  display: flex;
  height: 100%;
  will-change: transform;
}

.sheet-column-marker-cell {
  position: relative;
  flex: 0 0 auto;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  border-right: 1px solid #e6e6e6;
  color: #1f2d3d;
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
}

.sheet-column-marker-cell.is-selected {
  background: #e8f2ff;
  color: #1d4ed8;
}

.sheet-column-marker-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}

.sheet-column-marker-resize-handle {
  position: absolute;
  top: 0;
  right: -4px;
  z-index: 3;
  width: 8px;
  height: 100%;
  cursor: col-resize;
}

.sheet-column-marker-resize-handle:hover,
.sheet-column-marker-cell.is-resizing .sheet-column-marker-resize-handle {
  background: rgba(64, 158, 255, 0.18);
}

.sheet-frame :deep(.handsontable) {
  font-size: 13px;
}

.sheet-context-menu-fallback {
  flex: 0 0 auto;
  box-sizing: border-box;
  height: 30px;
  margin: 0 6px 0 4px;
  padding: 0 12px;
  border: 1px solid #d6c7af;
  border-radius: 6px;
  background: #fff9ef;
  color: #5f4b32;
  font-size: 13px;
  font-weight: 600;
  line-height: 28px;
  cursor: pointer;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
}

.sheet-context-menu-fallback:active {
  background: #f7ead7;
  transform: translateY(1px);
}

.sheet-frame :deep(.wtHolder) {
  overscroll-behavior: none;
  -webkit-overflow-scrolling: auto;
}

.sheet-frame :deep(.htCore td),
.sheet-frame :deep(.htCore th) {
  vertical-align: top;
}

.sheet-frame :deep(.handsontable td.sheet-cell-has-link) {
  color: #1d4ed8;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.sheet-frame :deep(.handsontable td.sheet-cell-formula-error) {
  color: #b91c1c;
  font-weight: 600;
}

.sheet-frame :deep(.handsontable td.sheet-cell-formula-reference-preview) {
  outline: 2px solid #409eff;
  outline-offset: -2px;
  background-color: rgba(64, 158, 255, 0.08) !important;
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

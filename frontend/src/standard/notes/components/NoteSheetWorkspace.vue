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
  fetchNoteSheetActiveRegistrationMatchRun,
  fetchNoteSheetRegistrationMatchRun,
  fetchAttendanceCourseScriptStatuses,
  generateAttendanceCourseScript,
  generateAttendanceCourseTemplate,
  importNoteSheetFromExcelReset,
  organizeAttendanceCourseScripts,
  setAttendanceRowCompleted,
  startNoteSheetRegistrationMatchRun,
  updateAttendanceLinkCounts,
  updateNoteSheetRegistrationOrderMatch,
  type AttendanceLinkCountFieldKey,
  type AttendanceCourseScriptStatusItem,
  type NoteSheetAccessCapabilities,
  type NoteSheetPaginationState,
  type NoteSheetRegistrationMatchRunResponse,
  type WorkbookRefItem,
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
const TABLE_FONT_FAMILY = 'Inter, "Segoe UI", sans-serif'
const TABLE_FONT = `400 14px ${TABLE_FONT_FAMILY}`
const TABLE_HEADER_FONT = '600 13px Inter, "Segoe UI", sans-serif'
const MONOSPACE_FONT_FAMILY = 'Consolas, "Cascadia Mono", "SFMono-Regular", Menlo, Monaco, "Liberation Mono", "Courier New", monospace'
const TABLE_LINE_HEIGHT = 20
const TABLE_CELL_VERTICAL_PADDING = 4
const TABLE_CELL_HORIZONTAL_PADDING = 8
const TABLE_CELL_BORDER_WIDTH = 1
const DEFAULT_COLUMN_FONT_SIZE = 13
const MAX_COLUMN_FONT_SIZE = 32
const DEFAULT_COLUMN_DISPLAY_MODE: ColumnDisplayMode = 'single_line'
const DEFAULT_COLUMN_TEXT_ALIGN: ColumnTextAlign = 'auto'
const DEFAULT_DATE_DISPLAY_FORMAT = 'yyyy/m/d'
const DEFAULT_PERCENT_DISPLAY_FORMAT = '0%'
const EXCEL_DATE_UNIX_EPOCH_SERIAL = 25569
const MS_PER_DAY = 24 * 60 * 60 * 1000
const MIN_COLUMN_WIDTH = 88
const MAX_COLUMN_WIDTH = 360
const ROW_MARKER_COLUMN_WIDTH = 76
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
const SHEET_CELL_ACTION_EXCEL_IMPORT_RESET = 'excel_import_reset'
const SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH = 'registration_order_match'
const SHEET_CELL_ACTION_REGISTRATION_USER_MATCH = 'registration_user_match'
const SHEET_CELL_ACTION_LABELS = {
  [SHEET_CELL_ACTION_EXCEL_IMPORT_RESET]: '导入excel',
  [SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH]: '更新订单匹配',
  [SHEET_CELL_ACTION_REGISTRATION_USER_MATCH]: '更新用户匹配',
} as const
const EXCEL_IMPORT_ACTION_LABEL = SHEET_CELL_ACTION_LABELS[SHEET_CELL_ACTION_EXCEL_IMPORT_RESET]
const REGISTRATION_MATCH_RUN_POLL_MS = 2000
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
const INLINE_READONLY_ACCESS_CAPABILITIES: NoteSheetAccessCapabilities = {
  can_read: true,
  can_use_local_view: true,
  can_edit_data: false,
  editable_data_columns: [],
  can_edit_config: false,
  can_run_sheet_actions: false,
  can_manage_access: false,
}
const COLUMN_SETTINGS_KEYS = [
  'value_type',
  'text_rule',
  'value_mode',
  'filter_enabled',
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
  'font_family',
  'font_size',
] as const satisfies readonly ColumnSettingsDraftKey[]

type SheetRow = string[]
type ColumnFilterState = {
  query: string
  excludedValues: string[]
}
type ColumnFilterOptionStat = {
  value: string
  label: string
  count: number
}
type ColumnFilterOptionView = ColumnFilterOptionStat & {
  selected: boolean
}
type SheetGridChangeRecord = {
  rowIndex: number
  hotColumnIndex: number
  columnIndex: number
  previousValue: unknown
  nextValue: unknown
}

type ColumnDisplayMode = 'wrap' | 'single_line'
type ColumnMarkerStyle = 'letters' | 'numbers'
type ColumnMarkerMode = 'none' | 'letters' | 'numbers'
type RowMarkerNumbering = 'page' | 'global'
type RowMarkerMode = 'none' | 'page_numbers' | 'global_numbers'
type RowMarkerOrigin = 'data' | 'sheet' | 'sheet_zero'
type ColumnNoteDisplayMode = 'hover' | 'row'
type FormulaReferenceOrigin = 'data' | 'sheet' | 'sheet_v2'
type SortDirection = 'asc' | 'desc'
type ColumnWidthMode = 'adaptive' | 'fixed'
type ColumnValueType = 'text' | 'multi_text' | 'number' | 'percent' | 'date'
type ColumnTextRule = 'none' | 'phone' | 'id_card'
type ColumnBaseType = 'text' | 'number' | 'time'
type ColumnSubType = 'plain_text' | 'multi_text' | 'phone' | 'id_card' | 'number' | 'percent' | 'date'
type ColumnValueMode = 'free' | 'fixed_options'
type ColumnFilterEnabledMode = 'enabled' | 'disabled'
type ColumnTextAlign = 'auto' | 'left' | 'center' | 'right'
type ColumnHashColorMode = 'none' | 'text' | 'background'
type ColumnHashColorTone = 'light' | 'dark'
type ColumnFontFamily = 'default' | 'monospace'
type CellFontFamily = ColumnFontFamily

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

type SheetMergedCell = {
  row: number
  col: number
  rowspan: number
  colspan: number
}

type SheetCellLink = {
  url: string
  title?: string
}

type SheetCellActionType = keyof typeof SHEET_CELL_ACTION_LABELS

type SheetCellAction = {
  type: SheetCellActionType
  label?: string
}

type SheetCellStyle = {
  background_color?: string
  text_color?: string
  font_family?: CellFontFamily
}

type HashColorStyle = {
  backgroundColor?: string
  color?: string
}

type SheetCellMeta = {
  link?: SheetCellLink
  action?: SheetCellAction
  style?: SheetCellStyle
}

type SheetCellMetaMap = Record<string, SheetCellMeta>

type SheetCellStyleDraft = {
  background_color: string
  text_color: string
  font_family: CellFontFamily | ''
}

type SheetCellStyleField = keyof SheetCellStyleDraft
type SheetCellColorField = 'background_color' | 'text_color'

type SheetCellStyleDraftTouched = Record<SheetCellStyleField, boolean>

type SelectedDataCell = {
  row: number
  column: number
  documentRow: number
}

type FormulaBarCell = {
  gridRow: number
  dataRow: number
  column: number
  documentGridRow: number
}

type SelectedSheetCell = {
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
  finishEditing?: (restoreOriginalValue?: boolean) => void
  getValue?: () => unknown
  setValue?: (value: unknown) => void
  focus?: () => void
}

type CellMouseSelectionController = {
  row?: boolean
  column?: boolean
  cell?: boolean
}

type SelectedSheetHeaderCell = {
  column: number
  headerLevel: number
}

type LinkDialogTarget =
  | { kind: 'cell'; row: number; column: number }
  | { kind: 'column_header'; column: number }

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
  text_rule?: Exclude<ColumnTextRule, 'none'>
  value_mode?: Exclude<ColumnValueMode, 'free'>
  filter_enabled?: boolean
  display_format?: string
  allow_empty?: boolean
  display_mode?: ColumnDisplayMode
  align?: Exclude<ColumnTextAlign, 'auto'>
  trim_whitespace?: boolean
  duplicate_value_highlight?: boolean
  hash_color_mode?: Exclude<ColumnHashColorMode, 'none'>
  hash_color_tone?: ColumnHashColorTone
  width_mode?: ColumnWidthMode
  font_family?: Exclude<ColumnFontFamily, 'default'>
  font_size?: number
  hidden?: boolean
  restore_index?: number
  header_background_color?: string
  header_text_color?: string
  note?: string
  header_link?: SheetCellLink
}

type ColumnSettingsDraft = {
  value_type: ColumnValueType
  text_rule: ColumnTextRule
  value_mode: ColumnValueMode
  filter_enabled: boolean
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
  font_family: ColumnFontFamily
  font_size: number
}

type ColumnSettingsDraftKey = keyof ColumnSettingsDraft
type ColumnSettingsTouchedState = Record<ColumnSettingsDraftKey, boolean>
type ColumnSettingsMixedState = Record<ColumnSettingsDraftKey, boolean>

type ColumnInsertionTemplate = {
  targetIndex: number
  width: number
  config: SheetColumnConfig | null
  cellStyles: {
    row: number
    style: SheetCellStyle
  }[]
}

type RowInsertionTemplate = {
  targetDocumentRow: number
  cellStyles: {
    column: number
    style: SheetCellStyle
  }[]
}

const COLUMN_FONT_FAMILY_OPTIONS = [
  { label: '默认', value: 'default' },
  { label: '等宽', value: 'monospace' },
] as const

const COLUMN_BASE_TYPE_OPTIONS = [
  { label: '文本', value: 'text' },
  { label: '数值', value: 'number' },
  { label: '时间', value: 'time' },
] as const satisfies readonly { label: string; value: ColumnBaseType }[]

const COLUMN_SUB_TYPE_OPTIONS = {
  text: [
    { label: '普通', value: 'plain_text' },
    { label: '多值', value: 'multi_text' },
    { label: '手机', value: 'phone' },
    { label: '身份证', value: 'id_card' },
  ],
  number: [
    { label: '普通', value: 'number' },
    { label: '百分比', value: 'percent' },
  ],
  time: [
    { label: '日期', value: 'date' },
  ],
} as const satisfies Record<ColumnBaseType, readonly { label: string; value: ColumnSubType }[]>

const COLUMN_VALUE_MODE_OPTIONS = [
  { label: '自由', value: 'free' },
  { label: '选项', value: 'fixed_options' },
] as const

const COLUMN_FILTER_ENABLED_OPTIONS = [
  { label: '关闭', value: 'disabled' },
  { label: '开启', value: 'enabled' },
] as const satisfies readonly { label: string; value: ColumnFilterEnabledMode }[]

const CELL_FONT_FAMILY_OPTIONS = [
  { label: '跟随字段', value: '' },
  ...COLUMN_FONT_FAMILY_OPTIONS,
] as const

type SheetViewSettings = {
  show_row_numbers?: boolean
  row_marker_numbering?: RowMarkerNumbering
  row_marker_origin?: RowMarkerOrigin
  show_column_markers?: boolean
  column_marker_style?: ColumnMarkerStyle
  column_note_display?: ColumnNoteDisplayMode
  frozen_column_count?: number
  pagination?: {
    enabled?: boolean
    page_size?: number
  }
}

type SheetDocument = {
  schema_version: 1
  columns: string[]
  rows: SheetRow[]
  grid_rows?: SheetRow[]
  data_start_row?: number
  field_row_index?: number
  merged_cells?: SheetMergedCell[]
  formula_reference_origin?: FormulaReferenceOrigin
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
  workbookItems: WorkbookRefItem[]
}

type SheetRowDetailItem = {
  columnIndex: number
  marker: string
  label: string
  value: string
  empty: boolean
  link?: SheetCellLink | null
}

type SheetRowDetail = {
  rowIndex: number
  rowLabel: string
  items: SheetRowDetailItem[]
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
  dataStartRow: number
}

type FormulaDisplayBuildOptions = {
  rowOffset?: number
  headerRows?: SheetRow[]
  headerRowCount?: number
}

type FormulaEngineInstance = {
  getCellValue: (address: { sheet: number; row: number; col: number }) => unknown
  destroy: () => void
}

type FormulaEngineCellValue = string | number | boolean | null

type FormulaEngineClass = {
  buildFromArray: (data: FormulaEngineCellValue[][], config: { licenseKey: string }) => FormulaEngineInstance
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
  inlineDocument?: Record<string, unknown> | null
  inlineTitle?: string
  accessCapabilities?: NoteSheetAccessCapabilities | null
  showBackButton?: boolean
  showTitleInput?: boolean
  backTo?: string
  backLabel?: string
  emptyText?: string
}

const props = withDefaults(defineProps<Props>(), {
  workbookId: null,
  inlineDocument: null,
  inlineTitle: '未命名表格',
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
const hasInlineDocument = computed(() => props.inlineDocument != null)
const effectiveAccessCapabilities = computed(() => (
  props.accessCapabilities
    ?? remoteAccessCapabilities.value
    ?? (hasInlineDocument.value ? INLINE_READONLY_ACCESS_CAPABILITIES : null)
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
const canPersistSheet = computed(() => (
  props.sheetId != null && (canEditData.value || canEditPartialData.value || canEditConfig.value)
))

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
const columnNotePopoverRef = ref<HTMLElement | null>(null)
const columnFilterPopoverRef = ref<HTMLElement | null>(null)
const columnHeaders = ref<string[]>([...DEFAULT_SHEET_COLUMNS])
const headerGroups = ref<SheetHeaderGroupCell[][]>([])
const mergedCells = ref<SheetMergedCell[]>([])
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
const sheetSettingsDialogVisible = ref(false)
const sheetSettingsDraft = ref<Required<SheetViewSettings>>(createDefaultSheetViewSettings())
const sheetSettingsColumnFilterDraft = ref<Record<string, boolean>>({})
const excelImportDialogVisible = ref(false)
const excelImportFileInputRef = ref<HTMLInputElement | null>(null)
const excelImportFile = ref<File | null>(null)
const excelImportInstruction = ref('')
const excelImportRunning = ref(false)
const excelImportActionCell = ref<{ documentRow: number; column: number } | null>(null)
const userMatchDialogVisible = ref(false)
const userMatchUseBrowserFallback = ref(false)
const userMatchStartPending = ref(false)
const userMatchRunStatus = ref<NoteSheetRegistrationMatchRunResponse | null>(null)
const sheetCellActionRunning = ref<SheetCellActionType | null>(null)
const rowDetailDialogVisible = ref(false)
const rowDetail = ref<SheetRowDetail | null>(null)
function isRegistrationMatchRunActive(run?: NoteSheetRegistrationMatchRunResponse | null) {
  return run?.status === 'pending' || run?.status === 'running'
}

const activeUserMatchRun = computed(() => isRegistrationMatchRunActive(userMatchRunStatus.value))
const userMatchRunSummary = computed(() => {
  const run = userMatchRunStatus.value
  if (!run || run.status === 'idle') {
    return ''
  }
  if (run.status === 'failed') {
    return run.error_message || run.message || '上次用户匹配失败'
  }
  if (run.status === 'cancelled') {
    return run.message || '上次用户匹配已取消'
  }
  if (run.status === 'completed') {
    const errorSuffix = run.error_count ? `，${run.error_count} 行异常` : ''
    return run.message || `已完成，更新 ${run.updated_count} 行，跳过 ${run.skipped_count} 行${errorSuffix}`
  }
  const total = run.total_count || 0
  const progress = total ? `${run.processed_count}/${total}` : `${run.processed_count}`
  const fallbackSuffix = run.use_browser_fallback ? '，含小鹅通兜底' : ''
  return `后台匹配中：${progress}，已更新 ${run.updated_count} 行${fallbackSuffix}`
})
const rowDetailDialogTitle = computed(() => (
  rowDetail.value?.rowLabel ? `第 ${rowDetail.value.rowLabel} 行` : '单独显式此条'
))
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
const sheetSettingsColumnFilterSummary = computed(() => {
  const total = columnHeaders.value.length
  const enabledCount = columnHeaders.value.filter((header) => sheetSettingsColumnFilterDraft.value[header] === true).length
  return `${enabledCount}/${total}`
})
const columnSettingsDialogVisible = ref(false)
const columnSettingsColumnIndex = ref<number | null>(null)
const columnSettingsSelectionBounds = ref<{ start: number; end: number } | null>(null)
const columnSettingsDraft = ref<ColumnSettingsDraft>(createDefaultColumnSettingsDraft())
const columnSettingsTouched = ref<ColumnSettingsTouchedState>(createColumnSettingsTouchedState())
const columnSettingsMixed = ref<ColumnSettingsMixedState>(createColumnSettingsMixedState())
const cellLinkDialogVisible = ref(false)
const cellLinkDialogTarget = ref<LinkDialogTarget | null>(null)
const cellLinkDraftUrl = ref('')
const cellStyleDialogVisible = ref(false)
const cellStyleDialogCells = ref<SelectedSheetCell[]>([])
const activeCellStyleColorField = ref<SheetCellColorField | null>(null)
const cellStyleDraft = ref<SheetCellStyleDraft>({
  background_color: '',
  text_color: '',
  font_family: '',
})
const cellStyleDraftTouched = ref<SheetCellStyleDraftTouched>({
  background_color: false,
  text_color: false,
  font_family: false,
})
const copiedCellFormat = ref<{ style: SheetCellStyle | null } | null>(null)
const formulaBarCell = ref<FormulaBarCell | null>(null)
const formulaBarDraft = ref('')
const formulaBarFocused = ref(false)
const formulaBarInputRef = ref<FormulaBarInputExpose | null>(null)
const touchContextMenuFallbackEnabled = ref(false)
const hasContextMenuFallbackSelection = ref(false)
const selectedColumnMarkerBounds = ref<{ start: number; end: number } | null>(null)
const selectedSheetHeaderCell = ref<SelectedSheetHeaderCell | null>(null)
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
const columnFilterPopover = ref<{
  visible: boolean
  x: number
  y: number
  columnIndex: number | null
  header: string
  draftQuery: string
  draftExcludedValues: string[]
}>({
  visible: false,
  x: 0,
  y: 0,
  columnIndex: null,
  header: '',
  draftQuery: '',
  draftExcludedValues: [],
})
const columnFilters = ref<Record<string, ColumnFilterState>>({})
const workspaceLoading = ref(false)
const sheetContentReady = ref(false)
let formulaReferencePointerDown = false
let formulaReferencePointerDownResetTimer: number | null = null
let formulaReferenceRangeState: FormulaReferenceRangeState | null = null
let formulaReferenceRangeFinishTimer: number | null = null
let inlineEditorFormulaBarSyncTimer: number | null = null
let formulaBarDraftSyncFrame: number | null = null
let formulaReferenceReplacementSpan: FormulaReferenceReplacementSpan | null = null
let rowMarkerSelectionTimer: number | null = null
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
const fixedColumnsStart = computed(() => normalizeFrozenColumnCount(
  sheetViewSettings.value.frozen_column_count,
  columnHeaders.value.length,
))
const effectivePaginationEnabled = computed(() => (
  paginationEnabled.value && (pageCount.value > 1 || totalRowCount.value > pageSize.value)
))
const shouldRenderSheetContent = computed(() => (
  (props.sheetId != null || hasInlineDocument.value) && sheetContentReady.value
))
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

  if (activeColumnFilterEntries.value.length > 0) {
    return `筛选 ${filteredVisibleRowCount.value}/${rows.value.length} 行`
  }

  if (!effectivePaginationEnabled.value) {
    return `共 ${pageWorkingRowCount.value} 行`
  }

  return `每页 ${pageSize.value} 行`
})

const cellStyleDialogTitle = computed(() => {
  const count = cellStyleDialogCells.value.length
  return count > 1 ? `设置 ${count} 个单元格格式` : '设置单元格格式'
})

const formulaBarAddress = computed(() => {
  const cell = formulaBarCell.value
  if (!cell) {
    return ''
  }
  return getCellReferenceLabelForSheetRow(cell.documentGridRow, cell.column)
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
    if (columnConfig.value_type === 'text' && columnConfig.text_rule === 'none' && columnConfig.allow_empty) {
      return
    }

    rows.value.forEach((row, rowIndex) => {
      const rawValue = row?.[columnIndex] ?? ''
      const cellValue = getCellSemanticValue(rowIndex, columnIndex, rawValue)
      if (isColumnValueValidByConfig(cellValue, columnConfig)) {
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
  return [
    ...getCurrentFormulaHeaderRows(headers),
    ...rows.value,
  ].some((row) => normalizeRow(row, headers).some(isFormulaExpression))
})

const formulaDisplayState = shallowRef<FormulaDisplayState>(createEmptyFormulaDisplayState())

const activeColumnFilterEntries = computed(() => getActiveColumnFilterEntries())
const filteredVisibleRowCount = computed(() => Math.max(0, rows.value.length - sheetFilterHiddenRows.value.length))

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

const columnFilterPopoverStyle = computed(() => ({
  left: `${columnFilterPopover.value.x}px`,
  top: `${columnFilterPopover.value.y}px`,
}))
const columnFilterPopoverIsOptionMode = computed(() => {
  const columnIndex = columnFilterPopover.value.columnIndex
  return columnIndex != null && isColumnFilterOptionMode(columnIndex)
})
const columnFilterPopoverOptions = computed<ColumnFilterOptionView[]>(() => {
  const columnIndex = columnFilterPopover.value.columnIndex
  if (columnIndex == null || !columnFilterPopoverIsOptionMode.value) {
    return []
  }

  const excludedSet = new Set(columnFilterPopover.value.draftExcludedValues)
  return getColumnFilterOptions(columnIndex).map((option) => ({
    ...option,
    selected: !excludedSet.has(option.value),
  }))
})
const columnFilterPopoverOptionRowCount = computed(() => (
  columnFilterPopoverOptions.value.reduce((total, option) => total + option.count, 0)
))

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
const columnNoteDisplayMode = computed(() => sheetViewSettings.value.column_note_display)
const hasColumnNotes = computed(() => columnHeaders.value.some((_, index) => getColumnNote(index).trim() !== ''))
const shouldShowColumnNoteRow = computed(() => columnNoteDisplayMode.value === 'row' && hasColumnNotes.value)
const columnHeaderLevel = computed(() => normalizedHeaderGroups.value.length)
const columnNoteHeaderLevel = computed(() => (shouldShowColumnNoteRow.value ? columnHeaderLevel.value + 1 : -1))
const sheetHeaderRowCount = computed(() => (
  normalizedHeaderGroups.value.length
  + 1
  + (shouldShowColumnNoteRow.value ? 1 : 0)
))
const sheetFilterHiddenRows = computed(() => {
  if (activeColumnFilterEntries.value.length === 0) {
    return []
  }

  const hiddenRows: number[] = []
  rows.value.forEach((row, rowIndex) => {
    if (!doesRowMatchColumnFilters(row, rowIndex)) {
      hiddenRows.push(sheetHeaderRowCount.value + rowIndex)
    }
  })
  return hiddenRows
})
const rowMarkerColumnCount = computed(() => (sheetViewSettings.value.show_row_numbers ? 1 : 0))

function isRowMarkerHotColumn(hotColumnIndex: number) {
  return rowMarkerColumnCount.value > 0 && hotColumnIndex === 0
}

function toHotColumnIndex(columnIndex: number) {
  return columnIndex + rowMarkerColumnCount.value
}

function toSheetColumnIndex(hotColumnIndex: number) {
  return hotColumnIndex - rowMarkerColumnCount.value
}

function getSheetColumnRangeFromHotRange(startHotColumn: number, endHotColumn: number) {
  const startHot = Math.min(startHotColumn, endHotColumn)
  const endHot = Math.max(startHotColumn, endHotColumn)
  const start = Math.max(toSheetColumnIndex(startHot), 0)
  const end = Math.min(toSheetColumnIndex(endHot), columnHeaders.value.length - 1)
  if (start > end) {
    return null
  }
  return { start, end }
}

function hotRangeIncludesRowMarker(startHotColumn: number, endHotColumn: number) {
  return rowMarkerColumnCount.value > 0
    && Math.min(startHotColumn, endHotColumn) <= 0
    && Math.max(startHotColumn, endHotColumn) >= 0
}

function getHotColumnCount() {
  return rowMarkerColumnCount.value + columnHeaders.value.length
}

const sheetColumnHeaders = computed<false | ((index: number) => string)>(() => {
  if (!sheetViewSettings.value.show_column_markers) {
    return false
  }
  return (hotColumnIndex: number) => {
    if (isRowMarkerHotColumn(hotColumnIndex)) {
      return ''
    }
    const columnIndex = toSheetColumnIndex(hotColumnIndex)
    return columnIndex >= 0 ? getColumnMarkerLabel(columnIndex) : ''
  }
})

const rowHeightLayoutState = computed(() => {
  let singleLineHeight = TABLE_LINE_HEIGHT
  let hasWrappedColumns = false
  const wrappedColumns: Array<{ index: number; fontSize: number; fontFamily: ColumnFontFamily; lineHeight: number }> = []

  columnHeaders.value.forEach((header, index) => {
    const config = columnConfigs.value[header]
    if (config?.hidden === true) {
      return
    }

    const fontSize = getColumnFontSizeFromConfig(config)
    const fontFamily = getColumnFontFamilyFromConfig(config)
    const lineHeight = getColumnLineHeightFromFontSize(fontSize)
    singleLineHeight = Math.max(singleLineHeight, lineHeight)

    if (normalizeColumnDisplayMode(config?.display_mode) !== 'single_line') {
      hasWrappedColumns = true
      wrappedColumns.push({ index, fontSize, fontFamily, lineHeight })
    }
  })

  return {
    hasWrappedColumns,
    singleLineHeight: singleLineHeight + TABLE_CELL_VERTICAL_PADDING * 2 + TABLE_CELL_BORDER_WIDTH,
    wrappedColumns,
  }
})

function expandHeaderGroupLabels(row: SheetHeaderGroupCell[], columnCount: number) {
  const labels: string[] = []
  for (const cell of row) {
    const colspan = Math.max(1, cell.colspan ?? 1)
    labels.push(cell.label)
    for (let index = 1; index < colspan && labels.length < columnCount; index += 1) {
      labels.push('')
    }
    if (labels.length >= columnCount) {
      break
    }
  }
  while (labels.length < columnCount) {
    labels.push('')
  }
  return labels
}

const sheetGridRows = computed<SheetRow[]>(() => {
  const headers = columnHeaders.value
  const headerRows = normalizedHeaderGroups.value.map((row) => expandHeaderGroupLabels(row, headers.length))
  headerRows.push([...headers])
  if (shouldShowColumnNoteRow.value) {
    headerRows.push(headers.map((_, index) => getColumnNote(index)))
  }
  return [
    ...headerRows,
    ...rows.value.map((row) => normalizeRow(row, headers)),
  ]
})

const sheetHotGridRows = computed<SheetRow[]>(() => {
  if (rowMarkerColumnCount.value <= 0) {
    return sheetGridRows.value
  }
  return sheetGridRows.value.map((row, rowIndex) => [
    getSheetRowHeaderLabel(rowIndex),
    ...row,
  ])
})

const sheetHotColumnWidths = computed(() => (
  rowMarkerColumnCount.value > 0
    ? [ROW_MARKER_COLUMN_WIDTH, ...columnWidths.value]
    : [...columnWidths.value]
))

const hotHiddenColumnIndexes = computed(() => (
  hiddenColumnIndexes.value.map((index) => toHotColumnIndex(index))
))

const fixedHotColumnsStart = computed(() => (
  fixedColumnsStart.value > 0 ? rowMarkerColumnCount.value + fixedColumnsStart.value : 0
))

function getDocumentColumnNote(header: string, sourceConfigs: Record<string, SheetColumnConfig>) {
  return normalizeColumnNote(sourceConfigs[header]?.note)
}

function shouldIncludeFormulaNoteRow(
  headers: string[],
  sourceConfigs: Record<string, SheetColumnConfig>,
  settings: Required<SheetViewSettings>,
) {
  return (
    settings.column_note_display === 'row'
    && headers.some((header) => getDocumentColumnNote(header, sourceConfigs) !== '')
  )
}

function getFormulaHeaderRowsForDocument(
  headers: string[],
  groups: SheetHeaderGroupCell[][],
  sourceConfigs: Record<string, SheetColumnConfig>,
  settings: Required<SheetViewSettings>,
) {
  const headerRows = groups.map((row) => expandHeaderGroupLabels(row, headers.length))
  headerRows.push([...headers])
  if (shouldIncludeFormulaNoteRow(headers, sourceConfigs, settings)) {
    headerRows.push(headers.map((header) => getDocumentColumnNote(header, sourceConfigs)))
  }
  return headerRows
}

function getCurrentFormulaHeaderRows(headers = columnHeaders.value) {
  return getFormulaHeaderRowsForDocument(
    headers,
    normalizeHeaderGroups(headerGroups.value, headers.length),
    normalizeColumnConfigs(columnConfigs.value, headers),
    sheetViewSettings.value,
  )
}

const sheetMergeCells = computed(() => {
  return getRenderableMergedCells()
})

const sheetHotMergeCells = computed(() => (
  rowMarkerColumnCount.value > 0
    ? sheetMergeCells.value.map((cell) => ({ ...cell, col: cell.col + rowMarkerColumnCount.value }))
    : sheetMergeCells.value
))

function getDocumentGridRowIndex(gridRowIndex: number) {
  if (gridRowIndex < sheetHeaderRowCount.value) {
    return gridRowIndex
  }
  return sheetHeaderRowCount.value + getDocumentRowIndex(getDataRowIndex(gridRowIndex))
}

function getCurrentGridRowIndexFromDocumentRow(documentGridRow: number) {
  if (documentGridRow < sheetHeaderRowCount.value) {
    return documentGridRow
  }
  const dataRow = documentGridRow - sheetHeaderRowCount.value
  const pageLocalRow = dataRow - (effectivePaginationEnabled.value ? pageRowOffset.value : 0)
  return pageLocalRow >= 0 && pageLocalRow < rows.value.length
    ? sheetHeaderRowCount.value + pageLocalRow
    : -1
}

function getRenderableMergedCells() {
  const currentRows = sheetGridRows.value
  const normalized = normalizeMergedCells(
    [
      ...getHeaderGroupMergeCells(normalizedHeaderGroups.value),
      ...mergedCells.value,
    ],
    Math.max(sheetHeaderRowCount.value + totalRowCount.value, currentRows.length),
    columnHeaders.value.length,
  )

  const rendered: SheetMergedCell[] = []
  const seen = new Set<string>()
  normalized.forEach((cell) => {
    const isHeaderMerge = cell.row < sheetHeaderRowCount.value
    if (isHeaderMerge) {
      if (cell.row + cell.rowspan > sheetHeaderRowCount.value) {
        return
      }
      const key = `${cell.row}:${cell.col}`
      if (!seen.has(key)) {
        seen.add(key)
        rendered.push(cell)
      }
      return
    }

    const dataStart = cell.row - sheetHeaderRowCount.value
    const pageStart = effectivePaginationEnabled.value ? pageRowOffset.value : 0
    const pageEnd = pageStart + rows.value.length
    if (dataStart < pageStart || dataStart + cell.rowspan > pageEnd) {
      return
    }
    const row = sheetHeaderRowCount.value + dataStart - pageStart
    const key = `${row}:${cell.col}`
    if (!seen.has(key)) {
      seen.add(key)
      rendered.push({ ...cell, row })
    }
  })
  return rendered
}

function findMergedCellAtDocumentCell(documentRow: number, columnIndex: number) {
  return normalizeMergedCells(
    [
      ...getHeaderGroupMergeCells(normalizedHeaderGroups.value),
      ...mergedCells.value,
    ],
    Math.max(sheetHeaderRowCount.value + totalRowCount.value, sheetGridRows.value.length),
    columnHeaders.value.length,
  ).find((cell) => (
    documentRow >= cell.row
    && documentRow < cell.row + cell.rowspan
    && columnIndex >= cell.col
    && columnIndex < cell.col + cell.colspan
  )) ?? null
}

function findMergedCellAtGridCell(gridRowIndex: number, columnIndex: number) {
  const documentRow = getDocumentGridRowIndex(gridRowIndex)
  return findMergedCellAtDocumentCell(documentRow, columnIndex)
}

function getMergeAnchorForGridCell(gridRowIndex: number, columnIndex: number) {
  const merge = findMergedCellAtGridCell(gridRowIndex, columnIndex)
  if (!merge) {
    return { row: gridRowIndex, column: columnIndex, documentRow: getDocumentGridRowIndex(gridRowIndex) }
  }
  const row = getCurrentGridRowIndexFromDocumentRow(merge.row)
  return {
    row: row >= 0 ? row : gridRowIndex,
    column: merge.col,
    documentRow: merge.row,
  }
}

function getMergeAnchorForDocumentCell(documentRow: number, columnIndex: number) {
  const merge = findMergedCellAtDocumentCell(documentRow, columnIndex)
  if (!merge) {
    return {
      row: getCurrentGridRowIndexFromDocumentRow(documentRow),
      column: columnIndex,
      documentRow,
    }
  }
  return {
    row: getCurrentGridRowIndexFromDocumentRow(merge.row),
    column: merge.col,
    documentRow: merge.row,
  }
}

function normalizeSheetCellToMergeAnchor(cell: SelectedSheetCell): SelectedSheetCell {
  const anchor = getMergeAnchorForDocumentCell(cell.documentRow, cell.column)
  return {
    row: anchor.row >= 0 ? anchor.row : cell.row,
    column: anchor.column,
    documentRow: anchor.documentRow,
  }
}

function normalizeSheetCellsToMergeAnchors(cells: SelectedSheetCell[]) {
  const normalized: SelectedSheetCell[] = []
  const seen = new Set<string>()
  cells.forEach((cell) => {
    const anchor = normalizeSheetCellToMergeAnchor(cell)
    const key = createCellMetaKey(anchor.documentRow, anchor.column)
    if (seen.has(key)) {
      return
    }
    seen.add(key)
    normalized.push(anchor)
  })
  return normalized
}

function getGridCellRawValue(gridRowIndex: number, columnIndex: number) {
  return normalizeCellValue(sheetGridRows.value[gridRowIndex]?.[columnIndex] ?? '')
}

function getLiveGridCellRawValue(gridRowIndex: number, columnIndex: number) {
  const hot = getHotInstance()
  if (!hot || gridRowIndex < 0 || columnIndex < 0) {
    return ''
  }

  return normalizeCellValue(hot.getSourceDataAtCell?.(gridRowIndex, toHotColumnIndex(columnIndex)) ?? '')
}

function getGridCellRenderSourceValue(gridRowIndex: number, columnIndex: number) {
  const rawValue = getGridCellRawValue(gridRowIndex, columnIndex)
  if (rawValue || gridRowIndex >= columnHeaderLevel.value) {
    return rawValue
  }

  const merge = findMergedCellAtGridCell(gridRowIndex, columnIndex)
  if (!merge || merge.row >= columnHeaderLevel.value) {
    return rawValue
  }
  return inferBlankHeaderMergeLabel(sheetGridRows.value, columnHeaders.value, merge, columnHeaderLevel.value)
}

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
  return Math.max(sheetViewportHeight.value, 80)
})

let suppressPersistence = false
let saveTimer: ReturnType<typeof setTimeout> | null = null
let changeSerial = 0
let lastQueuedSerial = 0
let saveInFlight = false
let sheetLayoutObserver: ResizeObserver | null = null
let editingHeaderInputEl: HTMLInputElement | null = null
let formulaEngineImportPromise: Promise<FormulaEngineClass | null> | null = null
let sheetFormulaPluginRegistered = false
let columnMarkerSelectionAnchor: number | null = null
let userMatchRunPollTimer: ReturnType<typeof setTimeout> | null = null
let lastNotifiedUserMatchRunId = ''
let lastNotifiedUserMatchRunStatus = ''
let pendingColumnInsertionTemplate: ColumnInsertionTemplate | null = null
let pendingRowInsertionTemplate: RowInsertionTemplate | null = null

const contextMenu = {
  items: {
    row_detail: {
      name: '单独显式此条',
      hidden: () => !canOpenSelectedRowDetailDialog(),
      callback: () => {
        openSelectedRowDetailDialog()
      },
    },
    hsep_row_detail: {
      name: '---------',
      hidden: () => !(canOpenSelectedRowDetailDialog() && canEditData.value),
    },
    row_above: {
      name: '上方插入行',
      hidden: () => !shouldShowRowActions() || !canEditData.value,
      callback: () => {
        insertRowFromSelection('above')
      },
    },
    row_below: {
      name: '下方插入行',
      hidden: () => !shouldShowRowActions() || !canEditData.value,
      callback: () => {
        insertRowFromSelection('below')
      },
    },
    hsep1: {
      name: '---------',
      hidden: () => !(
        shouldShowRowActions()
        && canEditData.value
        && shouldShowColumnActions()
        && canEditConfig.value
      ),
    },
    insert_col_left: {
      name: '左方插入列',
      hidden: () => !shouldShowColumnActions() || !canEditConfig.value,
      callback: () => {
        insertColumnFromSelection('left')
      },
    },
    insert_col_right: {
      name: '右方插入列',
      hidden: () => !shouldShowColumnActions() || !canEditConfig.value,
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
    hsep_freeze_pane: {
      name: '---------',
      hidden: () => !shouldShowFreezePaneContextMenuGroup(),
    },
    freeze_pane_here: {
      name: '在此处冻结窗口',
      hidden: () => !canFreezePaneAtSelection(),
      callback: () => {
        freezePanesAtSelectedColumn()
      },
    },
    unfreeze_pane: {
      name: '取消冻结窗口',
      hidden: () => !canUnfreezePanesFromSelection(),
      callback: () => {
        setFrozenColumnCount(0)
      },
    },
    column_settings: {
      name: '设置',
      hidden: () => !hasColumnSettingsContextSelection() || !canEditConfig.value,
      callback: () => {
        openSelectedColumnSettings()
      },
    },
    hide_column: {
      name: '隐藏字段',
      hidden: () => !shouldShowHideColumnAction() || !canEditConfig.value,
      callback: () => {
        hideSelectedColumns()
      },
    },
    show_column: {
      name: '显示字段',
      hidden: () => !shouldShowShowColumnAction() || !canEditConfig.value,
      callback: () => {
        showHiddenColumnsFromSelection()
      },
    },
    remove_col: {
      name: '删除选中列',
      hidden: () => !shouldShowRemoveColumnAction() || !canEditConfig.value,
      callback: () => {
        removeSelectedColumns()
      },
    },
    remove_row: {
      name: '删除选中行',
      hidden: () => !shouldShowRemoveRowAction() || !canEditData.value,
      callback: () => {
        removeSelectedRows()
      },
    },
    hsep_attendance_completion: {
      name: '---------',
      hidden: () => !canSetAttendanceCompletedFromSelection() || !canRunSheetActions.value,
    },
    set_attendance_completed: {
      name: '设置完结',
      hidden: () => !canSetAttendanceCompletedFromSelection() || !canRunSheetActions.value,
      callback: () => {
        void handleSetAttendanceCompletedFromSelection()
      },
    },
    hsep_attendance_course_template: {
      name: '---------',
      hidden: () => !canGenerateAttendanceCourseTemplateFromSelection() || !canRunSheetActions.value,
    },
    generate_attendance_course_template: {
      name: '生成新课模板',
      hidden: () => !canGenerateAttendanceCourseTemplateFromSelection() || !canRunSheetActions.value,
      callback: () => {
        void handleGenerateAttendanceCourseTemplateFromSelection()
      },
    },
    hsep_attendance_course_script: {
      name: '---------',
      hidden: () => !canRunSheetActions.value || (
        !canGenerateAttendanceCourseScriptFromSelection()
        && !canOrganizeAttendanceCourseScriptsFromColumn()
      ),
    },
    generate_attendance_course_script: {
      name: '生成py脚本',
      hidden: () => !canGenerateAttendanceCourseScriptFromSelection() || !canRunSheetActions.value,
      callback: () => {
        void handleGenerateAttendanceCourseScriptFromSelection()
      },
    },
    organize_attendance_course_scripts: {
      name: '整理py脚本',
      hidden: () => !canOrganizeAttendanceCourseScriptsFromColumn() || !canRunSheetActions.value,
      callback: () => {
        void handleOrganizeAttendanceCourseScriptsFromColumn()
      },
    },
    hsep_attendance_link_counts: {
      name: '---------',
      hidden: () => !canRunSheetActions.value || (
        !canUpdateAttendanceLinkCountsFromSelection('lesson_links')
        && !canUpdateAttendanceLinkCountsFromSelection('clockin_links')
      ),
    },
    update_attendance_lesson_link_counts: {
      name: '更新数据',
      hidden: () => !canUpdateAttendanceLinkCountsFromSelection('lesson_links') || !canRunSheetActions.value,
      callback: () => {
        void handleUpdateAttendanceLinkCountsFromSelection('lesson_links')
      },
    },
    update_attendance_clockin_link_counts: {
      name: '更新数据',
      hidden: () => !canUpdateAttendanceLinkCountsFromSelection('clockin_links') || !canRunSheetActions.value,
      callback: () => {
        void handleUpdateAttendanceLinkCountsFromSelection('clockin_links')
      },
    },
    hsep_style: {
      name: '---------',
      hidden: () => !shouldShowStyleContextMenuGroup(),
    },
    set_cell_style: {
      name: () => (getSelectedSheetCells().length > 1 ? '设置选区格式' : '设置单元格格式'),
      hidden: () => !hasSheetCellSelection() || !canEditConfig.value,
      callback: () => {
        openSelectedCellStyleDialog()
      },
    },
    copy_cell_format: {
      name: '复制格式',
      hidden: () => !hasSingleSheetCellSelection(),
      callback: () => {
        copySelectedCellFormat()
      },
    },
    paste_cell_format: {
      name: () => (getSelectedSheetCells().length > 1 ? '粘贴格式到选区' : '粘贴格式'),
      hidden: () => !hasSheetCellSelection() || !canEditConfig.value || !hasCopiedCellFormat(),
      callback: () => {
        pasteCellFormatToSelectedCells()
      },
    },
    merge_cells: {
      name: '合并单元格',
      hidden: () => !hasSheetCellSelection() || !canEditConfig.value || !canMergeSelectedCells(),
      callback: () => {
        mergeSelectedCells()
      },
    },
    unmerge_cells: {
      name: '取消合并',
      hidden: () => !hasSelectedMergedCell() || !canEditConfig.value,
      callback: () => {
        unmergeSelectedCells()
      },
    },
    hsep_link: {
      name: '---------',
      hidden: () => !shouldShowLinkContextMenuGroup(),
    },
    set_cell_link: {
      name: '设置超链接',
      hidden: () => !hasSingleLinkTargetSelection() || !canEditConfig.value,
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
  if (!cell) {
    return false
  }
  return cell.dataRow >= 0 ? canEditDataColumn(cell.column) : canEditConfig.value
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

function getSheetCellActionAtDocumentCell(documentRow: number, columnIndex: number) {
  return getCellActionAt(documentRow, columnIndex)
}

function canOpenExcelImportDialog() {
  return props.sheetId != null && canEditData.value && canRunSheetActions.value
}

function canRunRegistrationMatchAction() {
  return props.sheetId != null && canEditData.value && canRunSheetActions.value
}

function getSheetActionErrorMessage(error: unknown, fallback: string) {
  const maybeError = error as { response?: { data?: { detail?: unknown } }, message?: string }
  const detail = maybeError?.response?.data?.detail
  return typeof detail === 'string' && detail.trim()
    ? detail
    : maybeError?.message || fallback
}

function getExcelImportErrorMessage(error: unknown) {
  return getSheetActionErrorMessage(error, '导入 Excel 失败')
}

function clearUserMatchRunPollTimer() {
  if (userMatchRunPollTimer) {
    clearTimeout(userMatchRunPollTimer)
    userMatchRunPollTimer = null
  }
}

function scheduleUserMatchRunPolling() {
  clearUserMatchRunPollTimer()
  const runId = userMatchRunStatus.value?.run_id
  if (!runId || !activeUserMatchRun.value || props.sheetId == null) {
    return
  }
  userMatchRunPollTimer = setTimeout(() => {
    void refreshUserMatchRunStatus(runId)
  }, REGISTRATION_MATCH_RUN_POLL_MS)
}

function notifyUserMatchRunTerminalStatus(run: NoteSheetRegistrationMatchRunResponse) {
  if (!run.run_id || isRegistrationMatchRunActive(run) || run.status === 'idle') {
    return
  }
  if (lastNotifiedUserMatchRunId === run.run_id && lastNotifiedUserMatchRunStatus === run.status) {
    return
  }
  lastNotifiedUserMatchRunId = run.run_id
  lastNotifiedUserMatchRunStatus = run.status

  if (run.status === 'completed') {
    const errorSuffix = run.error_count ? `，${run.error_count} 行异常` : ''
    ElMessage.success(`${run.message || '用户匹配已完成'}${errorSuffix}`)
  } else if (run.status === 'failed') {
    ElMessage.error(run.error_message || run.message || '用户匹配失败')
  } else if (run.status === 'cancelled') {
    ElMessage.warning(run.message || '用户匹配已取消')
  }
}

async function refreshUserMatchRunStatus(runId?: string, options: { silent?: boolean } = {}) {
  if (props.sheetId == null) {
    return null
  }
  try {
    const status = runId
      ? await fetchNoteSheetRegistrationMatchRun(props.sheetId, runId, { workbookId: props.workbookId })
      : await fetchNoteSheetActiveRegistrationMatchRun(
        props.sheetId,
        SHEET_CELL_ACTION_REGISTRATION_USER_MATCH,
        { workbookId: props.workbookId },
      )

    userMatchRunStatus.value = status.status === 'idle' ? null : status
    if (status.status !== 'idle') {
      userMatchUseBrowserFallback.value = status.use_browser_fallback
    }
    if (status.sheet) {
      applyUserMatchRunSheetDetail(status.sheet)
    }
    if (isRegistrationMatchRunActive(status)) {
      scheduleUserMatchRunPolling()
    } else {
      clearUserMatchRunPollTimer()
      if (!options.silent) {
        notifyUserMatchRunTerminalStatus(status)
      }
    }
    return status
  } catch (error) {
    clearUserMatchRunPollTimer()
    console.warn('Failed to refresh registration user match run', error)
    if (!options.silent) {
      ElMessage.error(getSheetActionErrorMessage(error, '刷新用户匹配状态失败'))
    }
    return null
  }
}

function openExcelImportDialog(actionCell: { documentRow: number; column: number }) {
  if (!canOpenExcelImportDialog()) {
    warnReadOnlyAction()
    return
  }
  excelImportActionCell.value = actionCell
  excelImportFile.value = null
  excelImportInstruction.value = ''
  if (excelImportFileInputRef.value) {
    excelImportFileInputRef.value.value = ''
  }
  excelImportDialogVisible.value = true
}

function closeExcelImportDialog(force = false) {
  if (excelImportRunning.value && !force) {
    return
  }
  excelImportDialogVisible.value = false
  excelImportActionCell.value = null
  excelImportFile.value = null
  excelImportInstruction.value = ''
  if (excelImportFileInputRef.value) {
    excelImportFileInputRef.value.value = ''
  }
}

function openUserMatchDialog() {
  if (!canRunRegistrationMatchAction()) {
    warnReadOnlyAction()
    return
  }
  if (!activeUserMatchRun.value) {
    userMatchUseBrowserFallback.value = false
  }
  userMatchDialogVisible.value = true
  void refreshUserMatchRunStatus(undefined, { silent: true })
}

function closeUserMatchDialog() {
  userMatchDialogVisible.value = false
  if (!activeUserMatchRun.value) {
    userMatchUseBrowserFallback.value = false
  }
}

function handleExcelImportFileChange(event: Event) {
  const input = event.target as HTMLInputElement | null
  excelImportFile.value = input?.files?.[0] ?? null
}

async function applyExcelImportReset() {
  if (props.sheetId == null || !excelImportFile.value) {
    ElMessage.warning('请选择 Excel 文件')
    return
  }
  if (!canOpenExcelImportDialog()) {
    warnReadOnlyAction()
    return
  }

  excelImportRunning.value = true
  try {
    commitPendingSheetGridEdit()
    await flushRemoteSave()
    const result = await importNoteSheetFromExcelReset(
      props.sheetId,
      {
        file: excelImportFile.value,
        instruction: excelImportInstruction.value,
        actionCell: excelImportActionCell.value ?? undefined,
      },
      { workbookId: props.workbookId },
    )
    applyRemoteSheetDetail(result.sheet)
    const extraColumnSuffix = result.extra_columns.length
      ? `，追加 ${result.extra_columns.length} 列`
      : ''
    const warningSuffix = result.warnings.length ? `，${result.warnings[0]}` : ''
    ElMessage.success(`已导入 ${result.imported_count} 行${extraColumnSuffix}${warningSuffix}`)
    closeExcelImportDialog(true)
  } catch (error) {
    console.warn('Failed to import note sheet from Excel', error)
    ElMessage.error(getExcelImportErrorMessage(error))
  } finally {
    excelImportRunning.value = false
  }
}

async function applyRegistrationMatchAction(
  type: SheetCellActionType,
  options: { useBrowserFallback?: boolean } = {},
) {
  if (
    type !== SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH
    && type !== SHEET_CELL_ACTION_REGISTRATION_USER_MATCH
  ) {
    return
  }
  if (props.sheetId == null) {
    return
  }
  if (type === SHEET_CELL_ACTION_REGISTRATION_USER_MATCH) {
    await startUserMatchRun(false, options.useBrowserFallback)
    return
  }
  if (!canRunRegistrationMatchAction()) {
    warnReadOnlyAction()
    return
  }
  if (sheetCellActionRunning.value) {
    return
  }

  sheetCellActionRunning.value = type
  try {
    commitPendingSheetGridEdit()
    await flushRemoteSave()
    const result = await updateNoteSheetRegistrationOrderMatch(props.sheetId, { workbookId: props.workbookId })
    applyRemoteSheetDetail(result.sheet)
    const errorSuffix = result.error_count ? `，${result.error_count} 行异常` : ''
    ElMessage.success(`${result.message || SHEET_CELL_ACTION_LABELS[type]}${errorSuffix}`)
  } catch (error) {
    console.warn('Failed to run note sheet action', error)
    ElMessage.error(getSheetActionErrorMessage(error, '执行动作失败'))
  } finally {
    sheetCellActionRunning.value = null
  }
}

async function startUserMatchRun(forceRestart = false, useBrowserFallback = userMatchUseBrowserFallback.value) {
  if (props.sheetId == null) {
    return
  }
  if (!canRunRegistrationMatchAction()) {
    warnReadOnlyAction()
    return
  }
  if (userMatchStartPending.value) {
    return
  }

  userMatchStartPending.value = true
  try {
    commitPendingSheetGridEdit()
    await flushRemoteSave()
    const status = await startNoteSheetRegistrationMatchRun(
      props.sheetId,
      {
        action: SHEET_CELL_ACTION_REGISTRATION_USER_MATCH,
        useBrowserFallback,
        forceRestart,
      },
      { workbookId: props.workbookId },
    )
    userMatchRunStatus.value = status
    userMatchUseBrowserFallback.value = status.use_browser_fallback
    if (status.sheet) {
      applyUserMatchRunSheetDetail(status.sheet)
    }
    closeUserMatchDialog()
    ElMessage.success(status.already_running ? '已有用户匹配任务正在运行' : '已在后台开始用户匹配')
    if (isRegistrationMatchRunActive(status)) {
      scheduleUserMatchRunPolling()
    } else {
      notifyUserMatchRunTerminalStatus(status)
    }
  } catch (error) {
    console.warn('Failed to start registration user match run', error)
    ElMessage.error(getSheetActionErrorMessage(error, '启动用户匹配失败'))
  } finally {
    userMatchStartPending.value = false
  }
}

function runSheetCellAction(action: SheetCellAction, actionCell: { documentRow: number; column: number }) {
  if (action.type === SHEET_CELL_ACTION_EXCEL_IMPORT_RESET) {
    openExcelImportDialog(actionCell)
    return
  }
  if (action.type === SHEET_CELL_ACTION_REGISTRATION_USER_MATCH) {
    openUserMatchDialog()
    return
  }
  void applyRegistrationMatchAction(action.type)
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

function closeColumnFilterPopover() {
  columnFilterPopover.value.visible = false
  columnFilterPopover.value.columnIndex = null
  columnFilterPopover.value.header = ''
  columnFilterPopover.value.draftQuery = ''
  columnFilterPopover.value.draftExcludedValues = []
}

function bindColumnFilterPopoverPosition(anchorRect: DOMRect) {
  const margin = 12
  columnFilterPopover.value.x = anchorRect.left
  columnFilterPopover.value.y = anchorRect.bottom + 8

  void nextTick(() => {
    const popoverEl = columnFilterPopoverRef.value
    if (!popoverEl) {
      return
    }
    const rect = popoverEl.getBoundingClientRect()
    const maxX = Math.max(margin, window.innerWidth - rect.width - margin)
    const maxY = Math.max(margin, window.innerHeight - rect.height - margin)
    columnFilterPopover.value.x = Math.min(Math.max(anchorRect.left, margin), maxX)
    columnFilterPopover.value.y = Math.min(Math.max(anchorRect.bottom + 8, margin), maxY)
  })
}

function openColumnFilterPopover(columnIndex: number, anchorRect: DOMRect) {
  const header = columnHeaders.value[columnIndex]
  if (!header || !isColumnFilterEnabled(columnIndex)) {
    closeColumnFilterPopover()
    return
  }

  columnFilterPopover.value.visible = true
  columnFilterPopover.value.columnIndex = columnIndex
  columnFilterPopover.value.header = header
  const state = getColumnFilterState(header)
  columnFilterPopover.value.draftQuery = state.query
  columnFilterPopover.value.draftExcludedValues = isColumnFilterOptionMode(columnIndex)
    ? [...state.excludedValues]
    : []
  bindColumnFilterPopoverPosition(anchorRect)
}

function applyColumnFilterPopover() {
  const header = columnFilterPopover.value.header
  if (!header) {
    closeColumnFilterPopover()
    return
  }

  const columnIndex = columnFilterPopover.value.columnIndex
  const query = normalizeColumnFilterQuery(columnFilterPopover.value.draftQuery)
  const excludedValues = columnIndex != null && isColumnFilterOptionMode(columnIndex)
    ? columnFilterPopover.value.draftExcludedValues
    : []
  setColumnFilterState(header, { query, excludedValues })
  closeColumnFilterPopover()
}

function clearColumnFilterPopover() {
  const header = columnFilterPopover.value.header
  if (header) {
    const nextFilters = { ...columnFilters.value }
    delete nextFilters[header]
    columnFilters.value = nextFilters
  }
  closeColumnFilterPopover()
}

function setColumnFilterPopoverOptionSelected(value: string, selected: boolean) {
  const normalizedValue = normalizeColumnFilterOptionValue(value)
  const excludedSet = new Set(columnFilterPopover.value.draftExcludedValues)
  if (selected) {
    excludedSet.delete(normalizedValue)
  } else {
    excludedSet.add(normalizedValue)
  }
  columnFilterPopover.value.draftExcludedValues = [...excludedSet]
}

function handleColumnFilterOptionChange(value: string, event: Event) {
  const target = event.target as HTMLInputElement | null
  setColumnFilterPopoverOptionSelected(value, target?.checked === true)
}

function selectAllColumnFilterOptions() {
  columnFilterPopover.value.draftExcludedValues = []
}

function invertColumnFilterOptions() {
  columnFilterPopover.value.draftExcludedValues = columnFilterPopoverOptions.value
    .filter((option) => option.selected)
    .map((option) => option.value)
}

function selectColumnFilterDuplicateOptions() {
  columnFilterPopover.value.draftExcludedValues = columnFilterPopoverOptions.value
    .filter((option) => option.count <= 1)
    .map((option) => option.value)
}

function selectColumnFilterUniqueOptions() {
  columnFilterPopover.value.draftExcludedValues = columnFilterPopoverOptions.value
    .filter((option) => option.count !== 1)
    .map((option) => option.value)
}

function handleGlobalMouseDown(event: MouseEvent) {
  const target = event.target as Node | null
  if (target && isFormulaReferenceEditableMouseTarget(target)) {
    clearFormulaReferenceReplacementSpan()
  }

  if (columnFilterPopover.value.visible) {
    if (target && columnFilterPopoverRef.value?.contains(target)) {
      return
    }
    closeColumnFilterPopover()
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
  clearSheetHeaderSelection()
  hasContextMenuFallbackSelection.value = false
  sheetContentReady.value = false
  closeColumnNotePopover()
  columnHeaders.value = [...DEFAULT_SHEET_COLUMNS]
  headerGroups.value = []
  mergedCells.value = []
  columnConfigs.value = {}
  cellMeta.value = {}
  attendanceCourseScriptStatuses.value = {}
  remoteAccessCapabilities.value = null
  sheetViewSettings.value = createDefaultSheetViewSettings()
  columnWidths.value = DEFAULT_SHEET_COLUMNS.map((header) => getAdaptiveColumnWidth(header))
  rows.value = [createEmptyRow(DEFAULT_SHEET_COLUMNS.length)]
  sheetTitle.value = '未命名表格'
  sheetVersion.value = 0
  sheetSettingsDialogVisible.value = false
  sheetSettingsDraft.value = createDefaultSheetViewSettings()
  sheetSettingsColumnFilterDraft.value = {}
  columnFilters.value = {}
  closeColumnFilterPopover()
  closeExcelImportDialog()
  clearUserMatchRunPollTimer()
  userMatchDialogVisible.value = false
  userMatchUseBrowserFallback.value = false
  userMatchStartPending.value = false
  userMatchRunStatus.value = null
  sheetCellActionRunning.value = null
  columnSettingsDialogVisible.value = false
  columnSettingsColumnIndex.value = null
  columnSettingsSelectionBounds.value = null
  cellLinkDialogVisible.value = false
  cellLinkDialogTarget.value = null
  cellLinkDraftUrl.value = ''
  cellStyleDialogVisible.value = false
  cellStyleDialogCells.value = []
  activeCellStyleColorField.value = null
  cellStyleDraft.value = {
    background_color: '',
    text_color: '',
    font_family: '',
  }
  cellStyleDraftTouched.value = {
    background_color: false,
    text_color: false,
    font_family: false,
  }
  columnSettingsDraft.value = {
    value_type: 'text',
    text_rule: 'none',
    value_mode: 'free',
    filter_enabled: false,
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
    font_family: 'default',
    font_size: DEFAULT_COLUMN_FONT_SIZE,
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

function expandHeaderGroupsToSingleCellRows(groups: SheetHeaderGroupCell[][], columnCount: number) {
  return groups.map((row) => expandHeaderGroupLabels(row, columnCount).map((label) => ({ label, colspan: 1 })))
}

function getHeaderGroupMergeCells(groups: SheetHeaderGroupCell[][]) {
  const cells: SheetMergedCell[] = []
  groups.forEach((row, rowIndex) => {
    let columnIndex = 0
    row.forEach((cell) => {
      const colspan = Math.max(1, cell.colspan ?? 1)
      if (colspan > 1) {
        cells.push({
          row: rowIndex,
          col: columnIndex,
          rowspan: 1,
          colspan,
        })
      }
      columnIndex += colspan
    })
  })
  return cells
}

function createSingleCellHeaderGroupsFromGridRows(
  gridRows: SheetRow[],
  headerGroupRowCount: number,
  headers: string[],
) {
  return gridRows
    .slice(0, Math.max(0, headerGroupRowCount))
    .map((row) => normalizeRow(row, headers).map((label) => ({ label, colspan: 1 })))
}

function getNormalizedHeaderFieldLabels(gridRows: SheetRow[], headers: string[], fieldRowIndex: number) {
  const sourceRow = fieldRowIndex >= 0 ? gridRows[fieldRowIndex] : null
  const labels = normalizeRow(sourceRow ?? headers, headers)
  return labels.map((label, index) => normalizeCellValue(label || headers[index] || createFallbackHeader(index)))
}

function inferAttendanceHeaderGroupLabel(fieldLabels: string[]) {
  const labelSet = new Set(fieldLabels.map((label) => normalizeCellValue(label)).filter(Boolean))
  const hasAny = (...labels: string[]) => labels.some((label) => labelSet.has(label))
  const hasAll = (...labels: string[]) => labels.every((label) => labelSet.has(label))

  if (
    hasAll('分组', '学号', '用户ID')
    && hasAny('禅客', '商户订单号', '昵称', '姓名')
  ) {
    return '用户信息'
  }
  if (
    hasAll('完成视频数', '视频应返款', '打卡应返款', '总应返款')
    && hasAny('已返款', '订单金额')
  ) {
    return '退款总计（每天晚上上21点更新数据）'
  }
  if (hasAll('当前应返款', '返款配置')) {
    return '退款操作'
  }
  return ''
}

function inferBlankHeaderMergeLabel(
  gridRows: SheetRow[],
  headers: string[],
  merge: SheetMergedCell,
  fieldRowIndex: number,
) {
  const row = normalizeRow(gridRows[merge.row] ?? [], headers)
  for (let column = merge.col; column < merge.col + merge.colspan; column += 1) {
    const value = normalizeCellValue(row[column] ?? '')
    if (value) {
      return value
    }
  }

  const fieldLabels = getNormalizedHeaderFieldLabels(gridRows, headers, fieldRowIndex)
  return inferAttendanceHeaderGroupLabel(fieldLabels.slice(merge.col, merge.col + merge.colspan))
}

function repairBlankHeaderMergeAnchors(
  gridRows: SheetRow[],
  mergedCells: SheetMergedCell[],
  headers: string[],
  fieldRowIndex: number,
) {
  if (!gridRows.length || fieldRowIndex <= 0) {
    return gridRows
  }

  let changed = false
  const nextRows = gridRows.map((row) => normalizeRow(row, headers))
  mergedCells.forEach((merge) => {
    if (merge.row < 0 || merge.row >= fieldRowIndex || merge.rowspan !== 1 || merge.col < 0 || merge.col >= headers.length) {
      return
    }
    if (normalizeCellValue(nextRows[merge.row]?.[merge.col] ?? '')) {
      return
    }
    const inferredLabel = inferBlankHeaderMergeLabel(nextRows, headers, merge, fieldRowIndex)
    if (!inferredLabel) {
      return
    }
    nextRows[merge.row][merge.col] = inferredLabel
    changed = true
  })

  return changed ? nextRows : gridRows
}

function repairCoveredMergedCellValuesInGridRows(
  gridRows: SheetRow[],
  mergedCells: SheetMergedCell[],
  headers: string[],
) {
  if (!gridRows.length || !mergedCells.length || !headers.length) {
    return gridRows
  }

  let changed = false
  const nextRows = gridRows.map((row) => normalizeRow(row, headers))
  mergedCells.forEach((merge) => {
    if (
      merge.row < 0
      || merge.col < 0
      || merge.row >= nextRows.length
      || merge.col >= headers.length
      || (merge.rowspan <= 1 && merge.colspan <= 1)
    ) {
      return
    }

    const anchorValue = normalizeCellValue(nextRows[merge.row]?.[merge.col] ?? '')
    let coveredValue = ''
    for (let row = merge.row; row < merge.row + merge.rowspan && row < nextRows.length; row += 1) {
      for (let column = merge.col; column < merge.col + merge.colspan && column < headers.length; column += 1) {
        if (row === merge.row && column === merge.col) {
          continue
        }
        const value = normalizeCellValue(nextRows[row]?.[column] ?? '')
        if (value && !coveredValue) {
          coveredValue = value
        }
        if (value) {
          nextRows[row][column] = ''
          changed = true
        }
      }
    }

    if (!anchorValue && coveredValue) {
      nextRows[merge.row][merge.col] = coveredValue
      changed = true
    }
  })

  return changed ? nextRows : gridRows
}

function repairCoveredMergedCellValuesInDataRows(
  sourceRows: SheetRow[],
  mergedCells: SheetMergedCell[],
  headers: string[],
  dataStartRow: number,
) {
  if (!sourceRows.length || !mergedCells.length || !headers.length) {
    return sourceRows
  }

  const headerPlaceholders = Array.from(
    { length: Math.max(0, dataStartRow) },
    () => createEmptyRow(headers.length),
  )
  const gridRows = [
    ...headerPlaceholders,
    ...sourceRows.map((row) => normalizeRow(row, headers)),
  ]
  const repairedGridRows = repairCoveredMergedCellValuesInGridRows(gridRows, mergedCells, headers)
  if (repairedGridRows === gridRows) {
    return sourceRows
  }
  return trimTrailingBlankRows(repairedGridRows.slice(dataStartRow).map((row) => normalizeRow(row, headers)))
}

function normalizeMergedCells(source: unknown, rowCount: number, columnCount: number) {
  if (!Array.isArray(source) || rowCount <= 0 || columnCount <= 0) {
    return []
  }

  const cells: SheetMergedCell[] = []
  const occupied = new Set<string>()
  for (const item of source) {
    if (!item || typeof item !== 'object') {
      continue
    }
    const record = item as Record<string, unknown>
    const row = normalizeNonNegativeInt(record.row, -1)
    const col = normalizeNonNegativeInt(record.col, -1)
    const rowspan = normalizePositivePageNumber(record.rowspan, 1)
    const colspan = normalizePositivePageNumber(record.colspan, 1)
    if (
      row < 0
      || col < 0
      || row >= rowCount
      || col >= columnCount
      || (rowspan <= 1 && colspan <= 1)
    ) {
      continue
    }

    const boundedRowspan = Math.min(rowspan, rowCount - row)
    const boundedColspan = Math.min(colspan, columnCount - col)
    let overlaps = false
    for (let rowOffset = 0; rowOffset < boundedRowspan && !overlaps; rowOffset += 1) {
      for (let columnOffset = 0; columnOffset < boundedColspan; columnOffset += 1) {
        if (occupied.has(`${row + rowOffset}:${col + columnOffset}`)) {
          overlaps = true
          break
        }
      }
    }
    if (overlaps) {
      continue
    }
    for (let rowOffset = 0; rowOffset < boundedRowspan; rowOffset += 1) {
      for (let columnOffset = 0; columnOffset < boundedColspan; columnOffset += 1) {
        occupied.add(`${row + rowOffset}:${col + columnOffset}`)
      }
    }
    cells.push({ row, col, rowspan: boundedRowspan, colspan: boundedColspan })
  }
  return cells
}

function getMergedCellsSourceRowCount(source: unknown) {
  if (!Array.isArray(source)) {
    return 0
  }
  return source.reduce((maxRow, item) => {
    if (!item || typeof item !== 'object') {
      return maxRow
    }
    const record = item as Record<string, unknown>
    const row = normalizeNonNegativeInt(record.row, -1)
    const rowspan = normalizePositivePageNumber(record.rowspan, 1)
    return row >= 0 ? Math.max(maxRow, row + rowspan) : maxRow
  }, 0)
}

function shiftCellMetaRowKeys(source: unknown, rowDelta: number, columnCount: number) {
  const normalized = normalizeCellMetaMap(source, columnCount)
  if (rowDelta === 0) {
    return normalized
  }
  const shifted: SheetCellMetaMap = {}
  for (const [key, meta] of Object.entries(normalized)) {
    const position = parseCellMetaKey(key)
    if (!position) {
      continue
    }
    shifted[createCellMetaKey(Math.max(0, position.row + rowDelta), position.column)] = meta
  }
  return normalizeCellMetaMap(shifted, columnCount)
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

function normalizeCellActionType(value: unknown): SheetCellActionType | null {
  const normalized = normalizeCellValue(value).trim()
  return normalized in SHEET_CELL_ACTION_LABELS
    ? (normalized as SheetCellActionType)
    : null
}

function normalizeCellAction(source: unknown): SheetCellAction | null {
  if (typeof source === 'string') {
    const type = normalizeCellActionType(source)
    return type ? { type } : null
  }
  if (!source || typeof source !== 'object') {
    return null
  }

  const record = source as Record<string, unknown>
  const type = normalizeCellActionType(record.type ?? record.name)
  if (!type) {
    return null
  }

  const label = normalizeCellValue(record.label).trim()
  return label ? { type, label } : { type }
}

function normalizeCellFontFamily(value: unknown): CellFontFamily | '' {
  if (value === 'default' || value === 'monospace') {
    return value
  }
  return ''
}

function normalizeCellStyle(source: unknown): SheetCellStyle | null {
  if (!source || typeof source !== 'object') {
    return null
  }

  const record = source as Record<string, unknown>
  const backgroundColor = normalizeCssColor(record.background_color)
  const textColor = normalizeCssColor(record.text_color)
  const fontFamily = normalizeCellFontFamily(record.font_family)
  if (!backgroundColor && !textColor && !fontFamily) {
    return null
  }

  const style: SheetCellStyle = {}
  if (backgroundColor) {
    style.background_color = backgroundColor
  }
  if (textColor) {
    style.text_color = textColor
  }
  if (fontFamily) {
    style.font_family = fontFamily
  }
  return style
}

function normalizeCellMetaEntry(source: unknown): SheetCellMeta | null {
  if (!source || typeof source !== 'object') {
    return null
  }

  const record = source as Record<string, unknown>
  const link = normalizeCellLink(record.link)
  const action = normalizeCellAction(record.action)
  const style = normalizeCellStyle(record.style)
  if (!link && !action && !style) {
    return null
  }

  const meta: SheetCellMeta = {}
  if (link) {
    meta.link = link
  }
  if (action) {
    meta.action = action
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

function normalizeLegacySheetCellActionType(value: unknown): SheetCellActionType | null {
  const compactValue = normalizeCellValue(value).replace(/\s+/g, '').toLowerCase()
  const legacyLabels: Array<[SheetCellActionType, string]> = [
    [SHEET_CELL_ACTION_EXCEL_IMPORT_RESET, EXCEL_IMPORT_ACTION_LABEL],
    [SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH, SHEET_CELL_ACTION_LABELS[SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH]],
    [SHEET_CELL_ACTION_REGISTRATION_USER_MATCH, SHEET_CELL_ACTION_LABELS[SHEET_CELL_ACTION_REGISTRATION_USER_MATCH]],
  ]
  const matched = legacyLabels.find(([, label]) => compactValue === label.replace(/\s+/g, '').toLowerCase())
  return matched?.[0] ?? null
}

function addLegacySheetCellActions(
  sourceMeta: SheetCellMetaMap,
  normalizedGridRows: SheetRow[],
  columnCount: number,
) {
  if (columnCount <= 0) {
    return sourceMeta
  }

  let changed = false
  const nextMeta: SheetCellMetaMap = { ...sourceMeta }
  normalizedGridRows.forEach((row, rowIndex) => {
    row.forEach((value, columnIndex) => {
      const actionType = normalizeLegacySheetCellActionType(value)
      if (!actionType) {
        return
      }
      const key = createCellMetaKey(rowIndex, columnIndex)
      const currentMeta = nextMeta[key]
      if (currentMeta?.action) {
        return
      }
      nextMeta[key] = {
        ...(currentMeta ?? {}),
        action: {
          type: actionType,
          label: SHEET_CELL_ACTION_LABELS[actionType],
        },
      }
      changed = true
    })
  })

  return changed ? normalizeCellMetaMap(nextMeta, columnCount) : sourceMeta
}

function splitColumnNoteLeadingLink(source: unknown) {
  const note = normalizeColumnNote(source)
  const match = note.match(/^链接[:：]\s*(https?:\/\/[^\s]+)\s*(?:\r?\n)?/i)
  if (!match) {
    return { note, link: null as SheetCellLink | null }
  }

  return {
    note: note.slice(match[0].length).trim(),
    link: normalizeCellLink({ url: match[1] }),
  }
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
  if (value === 'multi_text' || value === 'number' || value === 'percent' || value === 'date') {
    return value
  }
  return 'text'
}

function normalizeColumnBaseType(value: unknown): ColumnBaseType {
  if (value === 'number' || value === 'time') {
    return value
  }
  return 'text'
}

function normalizeColumnTextRule(value: unknown, valueType: ColumnValueType = 'text'): ColumnTextRule {
  if (valueType !== 'text') {
    return 'none'
  }
  if (value === 'phone' || value === 'id_card') {
    return value
  }
  return 'none'
}

function normalizeColumnTextRuleFromRecord(record: Record<string, unknown>, valueType: ColumnValueType) {
  if (record.value_type === 'phone') {
    return normalizeColumnTextRule('phone', valueType)
  }
  return normalizeColumnTextRule(record.text_rule, valueType)
}

function normalizeColumnValueMode(value: unknown): ColumnValueMode {
  if (value === 'fixed_options' || value === 'fixed') {
    return 'fixed_options'
  }
  return 'free'
}

function getColumnBaseType(config: Pick<ColumnSettingsDraft, 'value_type'>): ColumnBaseType {
  const valueType = normalizeColumnValueType(config.value_type)
  if (valueType === 'number' || valueType === 'percent') {
    return 'number'
  }
  if (valueType === 'date') {
    return 'time'
  }
  return 'text'
}

function getColumnSubType(config: Pick<ColumnSettingsDraft, 'value_type' | 'text_rule'>): ColumnSubType {
  const valueType = normalizeColumnValueType(config.value_type)
  const textRule = normalizeColumnTextRule(config.text_rule, valueType)
  if (valueType === 'multi_text') {
    return 'multi_text'
  }
  if (valueType === 'number') {
    return 'number'
  }
  if (valueType === 'percent') {
    return 'percent'
  }
  if (valueType === 'date') {
    return 'date'
  }
  if (textRule === 'phone') {
    return 'phone'
  }
  if (textRule === 'id_card') {
    return 'id_card'
  }
  return 'plain_text'
}

function getColumnSubTypeBaseType(value: ColumnSubType): ColumnBaseType {
  if (value === 'number' || value === 'percent') {
    return 'number'
  }
  if (value === 'date') {
    return 'time'
  }
  return 'text'
}

function normalizeColumnSubType(value: unknown, baseType: ColumnBaseType): ColumnSubType {
  const options = COLUMN_SUB_TYPE_OPTIONS[baseType]
  return options.some((option) => option.value === value)
    ? value as ColumnSubType
    : options[0].value
}

function getDefaultColumnSubType(baseType: ColumnBaseType): ColumnSubType {
  return COLUMN_SUB_TYPE_OPTIONS[baseType][0].value
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

function normalizeColumnFontFamily(value: unknown): ColumnFontFamily {
  return value === 'monospace' ? 'monospace' : 'default'
}

function normalizeColumnFontSize(value: unknown) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return DEFAULT_COLUMN_FONT_SIZE
  }
  return Math.min(Math.round(numeric), MAX_COLUMN_FONT_SIZE)
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
    row_marker_numbering: 'global',
    row_marker_origin: 'sheet',
    show_column_markers: true,
    column_marker_style: 'letters',
    column_note_display: 'hover',
    frozen_column_count: 0,
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
  return value === 'page' ? 'page' : 'global'
}

function normalizeRowMarkerOrigin(value: unknown): RowMarkerOrigin {
  if (value === 'sheet' || value === 'sheet_zero') {
    return value
  }
  return 'data'
}

function normalizeColumnNoteDisplayMode(value: unknown): ColumnNoteDisplayMode {
  return value === 'row' ? 'row' : 'hover'
}

function normalizeFormulaReferenceOrigin(value: unknown): FormulaReferenceOrigin {
  if (value === 'sheet' || value === 'sheet_v2') {
    return value
  }
  return 'data'
}

function normalizeSheetPageSize(value: unknown) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return DEFAULT_PAGE_SIZE
  }
  return Math.min(Math.max(Math.round(numeric), 1), 1000)
}

function normalizeFrozenColumnCount(value: unknown, columnCount = Number.MAX_SAFE_INTEGER) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return 0
  }
  const maxColumnCount = Number.isFinite(columnCount)
    ? Math.max(0, Math.floor(columnCount))
    : Number.MAX_SAFE_INTEGER
  return Math.min(Math.max(Math.floor(numeric), 0), maxColumnCount)
}

function normalizeSheetViewSettings(source: unknown, columnCount = Number.MAX_SAFE_INTEGER): Required<SheetViewSettings> {
  const defaults = createDefaultSheetViewSettings()
  if (!source || typeof source !== 'object') {
    return defaults
  }

  const record = source as Record<string, unknown>
  return {
    show_row_numbers: record.show_row_numbers !== false,
    row_marker_numbering: normalizeRowMarkerNumbering(record.row_marker_numbering),
    row_marker_origin: normalizeRowMarkerOrigin(record.row_marker_origin),
    show_column_markers: record.show_column_markers !== false,
    column_marker_style: normalizeColumnMarkerStyle(record.column_marker_style),
    column_note_display: normalizeColumnNoteDisplayMode(record.column_note_display),
    frozen_column_count: normalizeFrozenColumnCount(record.frozen_column_count, columnCount),
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
    const textRule = normalizeColumnTextRuleFromRecord(configRecord, valueType)
    const valueMode = normalizeColumnValueMode(configRecord.value_mode)
    const filterEnabled = configRecord.filter_enabled === true
    const displayFormat = normalizeColumnDisplayFormat(configRecord.display_format, valueType)
    const allowEmpty = configRecord.allow_empty !== false
    const displayMode = normalizeColumnDisplayMode(configRecord.display_mode)
    const align = normalizeColumnTextAlign(configRecord.align)
    const trimWhitespace = configRecord.trim_whitespace !== false
    const duplicateValueHighlight = configRecord.duplicate_value_highlight === true
    const hashColorMode = normalizeColumnHashColorMode(configRecord.hash_color_mode)
    const hashColorTone = normalizeColumnHashColorTone(configRecord.hash_color_tone)
    const widthMode = normalizeColumnWidthMode(configRecord.width_mode)
    const fontFamily = normalizeColumnFontFamily(configRecord.font_family)
    const fontSize = normalizeColumnFontSize(configRecord.font_size)
    const hidden = isColumnHiddenConfigValue(configRecord.hidden)
    const restoreIndex = normalizeNonNegativeInt(configRecord.restore_index, -1)
    const headerBackgroundColor = normalizeCssColor(configRecord.header_background_color)
    const headerTextColor = normalizeCssColor(configRecord.header_text_color)
    const noteWithLink = splitColumnNoteLeadingLink(configRecord.note)
    const note = noteWithLink.note
    const headerLink = normalizeCellLink(configRecord.header_link) ?? noteWithLink.link

    if (
      valueType !== 'text'
      || textRule !== 'none'
      || valueMode !== 'free'
      || filterEnabled
      || !isDefaultColumnDisplayFormat(valueType, displayFormat)
      || allowEmpty === false
      || displayMode !== DEFAULT_COLUMN_DISPLAY_MODE
      || align !== DEFAULT_COLUMN_TEXT_ALIGN
      || trimWhitespace === false
      || duplicateValueHighlight
      || hashColorMode !== 'none'
      || widthMode !== 'adaptive'
      || fontFamily !== 'default'
      || fontSize !== DEFAULT_COLUMN_FONT_SIZE
      || hidden
      || restoreIndex >= 0
      || headerBackgroundColor
      || headerTextColor
      || note
      || headerLink
    ) {
      normalized[header] = {}
      if (valueType !== 'text') {
        normalized[header].value_type = valueType
      }
      if (valueType === 'text' && textRule !== 'none') {
        normalized[header].text_rule = textRule
      }
      if (valueMode !== 'free') {
        normalized[header].value_mode = valueMode
      }
      if (filterEnabled) {
        normalized[header].filter_enabled = true
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
      if (fontFamily !== 'default') {
        normalized[header].font_family = fontFamily
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
      if (headerLink) {
        normalized[header].header_link = headerLink
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
  const textRule = normalizeColumnTextRuleFromRecord(record, valueType)
  const hashColorMode = normalizeColumnHashColorMode(record.hash_color_mode)
  return {
    value_type: valueType,
    text_rule: textRule,
    value_mode: normalizeColumnValueMode(record.value_mode),
    filter_enabled: record.filter_enabled === true,
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
    font_family: normalizeColumnFontFamily(record.font_family),
    font_size: normalizeColumnFontSize(record.font_size),
  }
}

function createDefaultColumnSettingsDraft(): ColumnSettingsDraft {
  return {
    value_type: 'text',
    text_rule: 'none',
    value_mode: 'free',
    filter_enabled: false,
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
    font_family: 'default',
    font_size: DEFAULT_COLUMN_FONT_SIZE,
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
  const gridRows = [[...DEFAULT_SHEET_COLUMNS]]
  return {
    schema_version: 1,
    columns: [...DEFAULT_SHEET_COLUMNS],
    rows: [],
    grid_rows: gridRows,
    data_start_row: 1,
    field_row_index: 0,
    merged_cells: [],
    formula_reference_origin: 'sheet_v2',
    header_groups: [],
    cell_meta: {},
    column_configs: {},
    column_widths: DEFAULT_SHEET_COLUMNS.map((header) => getAdaptiveColumnWidth(header)),
    view_settings: createDefaultSheetViewSettings(),
  }
}

function normalizeRowsFormulaReferencesForOrigin(
  sourceRows: SheetRow[],
  origin: FormulaReferenceOrigin,
  headerRowCount: number,
) {
  if (headerRowCount <= 0) {
    return sourceRows
  }
  if (origin === 'data') {
    return remapRowsFormulaReferencesByRowDelta(sourceRows, headerRowCount)
  }
  // The old "sheet" marker was produced by a short-lived migration that over-shifted A1 row refs.
  if (origin === 'sheet') {
    return remapRowsFormulaReferencesByRowDelta(sourceRows, -headerRowCount)
  }
  return sourceRows
}

function normalizeSheetDocument(source: unknown, formulaOptions: FormulaDisplayBuildOptions = {}): SheetDocument {
  if (!source || typeof source !== 'object') {
    return createDefaultDocument()
  }

  const record = source as Record<string, unknown>
  const headers = normalizeHeaders(record.columns)
  const sourceRows = Array.isArray(record.rows) ? record.rows : []
  const sourceWidths = Array.isArray(record.column_widths) ? record.column_widths : []
  const normalizedColumnConfigs = normalizeColumnConfigs(record.column_configs, headers)
  const sourceHeaderGroups = normalizeHeaderGroups(record.header_groups, headers.length)
  const normalizedSettings = normalizeSheetViewSettings(record.view_settings, headers.length)
  const formulaHeaderRows = getFormulaHeaderRowsForDocument(
    headers,
    sourceHeaderGroups,
    normalizedColumnConfigs,
    normalizedSettings,
  )
  const dataStartRow = normalizeNonNegativeInt(record.data_start_row, formulaHeaderRows.length)
  const fieldRowIndex = normalizeNonNegativeInt(record.field_row_index, Math.max(0, formulaHeaderRows.length - 1))
  const hasUnifiedGridRows = Array.isArray(record.grid_rows)
  const sourceGridRows = hasUnifiedGridRows
    ? (record.grid_rows as unknown[]).map((row) => normalizeRow(row, headers))
    : [
      ...formulaHeaderRows.map((row) => normalizeRow(row, headers)),
      ...sourceRows.map((row) => normalizeRow(row, headers)),
    ]
  const sourceFormulaOrigin = normalizeFormulaReferenceOrigin(record.formula_reference_origin)
  const sourceNormalizedRows = trimTrailingBlankRows(
    (hasUnifiedGridRows ? sourceGridRows.slice(dataStartRow) : sourceRows.map((row) => normalizeRow(row, headers))),
  )
  let normalizedRows = normalizeRowsFormulaReferencesForOrigin(
    sourceNormalizedRows,
    sourceFormulaOrigin,
    formulaHeaderRows.length,
  )
  const initialNormalizedGridRows = [
    ...sourceGridRows.slice(0, dataStartRow).map((row) => normalizeRow(row, headers)),
    ...normalizedRows,
  ]
  const normalizedMergedCells = normalizeMergedCells(
    [
      ...(!hasUnifiedGridRows ? getHeaderGroupMergeCells(sourceHeaderGroups) : []),
      ...(Array.isArray(record.merged_cells) ? record.merged_cells : []),
    ],
    Math.max(initialNormalizedGridRows.length, getMergedCellsSourceRowCount(record.merged_cells)),
    headers.length,
  )
  let normalizedGridRows = repairBlankHeaderMergeAnchors(
    initialNormalizedGridRows,
    normalizedMergedCells,
    headers,
    fieldRowIndex,
  )
  normalizedGridRows = repairCoveredMergedCellValuesInGridRows(
    normalizedGridRows,
    normalizedMergedCells,
    headers,
  )
  normalizedRows = trimTrailingBlankRows(
    normalizedGridRows.slice(dataStartRow).map((row) => normalizeRow(row, headers)),
  )
  const normalizedHeaderGroups = hasUnifiedGridRows
    ? createSingleCellHeaderGroupsFromGridRows(normalizedGridRows, fieldRowIndex, headers)
    : expandHeaderGroupsToSingleCellRows(sourceHeaderGroups, headers.length)
  const sourceCellMeta = hasUnifiedGridRows
    ? normalizeCellMetaMap(record.cell_meta, headers.length)
    : shiftCellMetaRowKeys(record.cell_meta, dataStartRow, headers.length)
  const normalizedCellMeta = addLegacySheetCellActions(
    sourceCellMeta,
    normalizedGridRows,
    headers.length,
  )
  const formulaDisplayForWidths = buildFormulaDisplayStateForRows(
    headers,
    normalizedRows,
    normalizedColumnConfigs,
    {
      ...formulaOptions,
      headerRows: formulaHeaderRows,
      headerRowCount: formulaHeaderRows.length,
    },
  )
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
    grid_rows: normalizedGridRows,
    data_start_row: dataStartRow,
    field_row_index: fieldRowIndex,
    merged_cells: normalizedMergedCells,
    formula_reference_origin: 'sheet_v2',
    header_groups: normalizedHeaderGroups,
    cell_meta: normalizedCellMeta,
    column_configs: normalizedColumnConfigs,
    column_widths: normalizedWidths,
    view_settings: normalizedSettings,
  }
}

function buildCurrentDocument(): SheetDocument {
  const headers = normalizeHeaders(columnHeaders.value)
  const normalizedRows = trimTrailingBlankRows(rows.value.map((row) => normalizeRow(row, headers)))
  return {
    schema_version: 1,
    columns: headers,
    rows: normalizedRows,
    grid_rows: sheetGridRows.value.map((row) => normalizeRow(row, headers)),
    data_start_row: sheetHeaderRowCount.value,
    field_row_index: columnHeaderLevel.value,
    merged_cells: normalizeMergedCells(mergedCells.value, sheetHeaderRowCount.value + totalRowCount.value, headers.length),
    formula_reference_origin: 'sheet_v2',
    header_groups: normalizeHeaderGroups(headerGroups.value, headers.length),
    cell_meta: normalizeCellMetaMap(cellMeta.value, headers.length),
    column_configs: normalizeColumnConfigs(columnConfigs.value, headers),
    column_widths: headers.map((_, index) => columnWidths.value[index] ?? getAutoColumnWidth(
      index,
      headers,
      normalizedRows,
      normalizeColumnConfigs(columnConfigs.value, headers),
    )),
    view_settings: normalizeSheetViewSettings(sheetViewSettings.value, headers.length),
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
    { source: 'DATEDIF', target: 'DATEDIF_COMPAT' },
    { source: 'TEXTJOIN', target: 'TEXTJOIN_COMPAT' },
  ]
  for (const alias of aliases) {
    if (value.slice(startIndex, startIndex + alias.source.length).toUpperCase() !== alias.source) {
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

function isFormulaIdentifierChar(char: string | undefined) {
  return !!char && /[A-Za-z0-9_.]/.test(char)
}

function readFormulaBooleanLiteral(value: string, startIndex: number) {
  for (const source of ['TRUE', 'FALSE']) {
    if (value.slice(startIndex, startIndex + source.length).toUpperCase() !== source) {
      continue
    }
    if (isFormulaIdentifierChar(value[startIndex - 1]) || isFormulaIdentifierChar(value[startIndex + source.length])) {
      continue
    }

    const nextIndex = findNextNonWhitespaceIndex(value, startIndex + source.length)
    if (nextIndex >= 0 && (value[nextIndex] === '(' || value[nextIndex] === '（')) {
      continue
    }

    return {
      endIndex: startIndex + source.length - 1,
      value: `${source}()`,
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

    const booleanLiteral = readFormulaBooleanLiteral(value, index)
    if (booleanLiteral) {
      normalized += booleanLiteral.value
      index = booleanLiteral.endIndex
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

function getCurrentFormulaDisplayRowOffset() {
  return effectivePaginationEnabled.value ? pageRowOffset.value : 0
}

function normalizeFormulaExpressionForPagedEngine(
  value: string,
  rowOffset: number,
  dataRowCount: number,
  headerRowCount: number,
) {
  const normalizedFormula = normalizeFormulaExpressionForEngine(value)
  if (rowOffset <= 0) {
    return normalizedFormula
  }

  return remapFormulaCellReferences(normalizedFormula, (sourceRowIndex) => {
    if (sourceRowIndex < headerRowCount) {
      return sourceRowIndex
    }
    const localDataRowIndex = sourceRowIndex - headerRowCount - rowOffset
    return localDataRowIndex >= 0 && localDataRowIndex < dataRowCount
      ? headerRowCount + localDataRowIndex
      : null
  })
}

function normalizeFormulaEngineCellValue(cellValue: string): FormulaEngineCellValue {
  if (isFormulaExpression(cellValue)) {
    return cellValue
  }
  return cellValue === '' ? null : cellValue
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

function formulaDatePartsToUtcDate(parts: FormulaDateParts) {
  return new Date(Date.UTC(parts.year, parts.month - 1, parts.day))
}

function formulaDateSerialToUtcDate(value: number) {
  const parts = formulaDateSerialToParts(Math.floor(value))
  return parts ? formulaDatePartsToUtcDate(parts) : null
}

function formulaUtcDateToSerial(value: Date) {
  return Math.floor(value.getTime() / MS_PER_DAY) + EXCEL_DATE_UNIX_EPOCH_SERIAL
}

function parseSeparatedFormulaDate(value: unknown) {
  const text = normalizeCellValue(value).trim()
  const match = text.match(/^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$/)
  if (!match) {
    return null
  }
  const parts = {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
  }
  return isValidDateParts(parts) ? parts : null
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

function normalizeFormulaDateSerial(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.floor(value)
  }

  const text = normalizeCellValue(value).trim()
  if (!text) {
    return null
  }
  const numericValue = Number(text)
  if (Number.isFinite(numericValue)) {
    return Math.floor(numericValue)
  }

  const parts = parseSeparatedFormulaDate(text)
    ?? parseCompactFormulaDate(text, 'yyyymmdd')
  return parts ? formulaDatePartsToSerial(parts) : null
}

function getFormulaFullYearsBetween(start: Date, end: Date) {
  let years = end.getUTCFullYear() - start.getUTCFullYear()
  const endMonth = end.getUTCMonth()
  const startMonth = start.getUTCMonth()
  if (endMonth < startMonth || (endMonth === startMonth && end.getUTCDate() < start.getUTCDate())) {
    years -= 1
  }
  return years
}

function getFormulaFullMonthsBetween(start: Date, end: Date) {
  let months = (end.getUTCFullYear() - start.getUTCFullYear()) * 12 + end.getUTCMonth() - start.getUTCMonth()
  if (end.getUTCDate() < start.getUTCDate()) {
    months -= 1
  }
  return months
}

function daysInFormulaUtcMonth(year: number, monthIndex: number) {
  return new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate()
}

function calculateFormulaDateDif(startSerial: number, endSerial: number, unitValue: unknown) {
  const unit = normalizeCellValue(unitValue).trim().toUpperCase()
  const start = formulaDateSerialToUtcDate(startSerial)
  const end = formulaDateSerialToUtcDate(endSerial)
  if (!start || !end || endSerial < startSerial) {
    return null
  }

  const days = Math.floor(endSerial) - Math.floor(startSerial)
  if (unit === 'D') {
    return days
  }
  if (unit === 'Y') {
    return getFormulaFullYearsBetween(start, end)
  }
  if (unit === 'M') {
    return getFormulaFullMonthsBetween(start, end)
  }
  if (unit === 'YM') {
    return ((getFormulaFullMonthsBetween(start, end) % 12) + 12) % 12
  }
  if (unit === 'MD') {
    if (end.getUTCDate() >= start.getUTCDate()) {
      return end.getUTCDate() - start.getUTCDate()
    }
    const previousEndMonthDayCount = daysInFormulaUtcMonth(end.getUTCFullYear(), end.getUTCMonth() - 1)
    return previousEndMonthDayCount - start.getUTCDate() + end.getUTCDate()
  }
  if (unit === 'YD') {
    const adjustedStart = new Date(Date.UTC(end.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()))
    if (adjustedStart > end) {
      adjustedStart.setUTCFullYear(adjustedStart.getUTCFullYear() - 1)
    }
    return Math.floor((end.getTime() - adjustedStart.getTime()) / MS_PER_DAY)
  }

  return null
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
    && !!HyperFormula.getFunctionPlugin?.('DATEDIF_COMPAT')
    && !!HyperFormula.getFunctionPlugin?.('TEXTJOIN_COMPAT')
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
  if (
    !FunctionPlugin
    || !FunctionArgumentType?.STRING
    || !FunctionArgumentType?.NUMBER
    || !FunctionArgumentType?.ANY
    || !CellError
    || !ErrorType?.VALUE
  ) {
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
      DATEDIF_COMPAT: {
        method: 'dateDifCompat',
        parameters: [
          { argumentType: FunctionArgumentType.ANY },
          { argumentType: FunctionArgumentType.ANY },
          { argumentType: FunctionArgumentType.STRING },
        ],
      },
      TEXTJOIN_COMPAT: {
        method: 'textJoinCompat',
        parameters: [
          { argumentType: FunctionArgumentType.STRING },
          { argumentType: FunctionArgumentType.ANY },
          { argumentType: FunctionArgumentType.ANY },
        ],
        repeatLastArgs: 1,
        expandRanges: true,
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

    dateDifCompat(ast: { args: unknown[] }, state: unknown) {
      return this.runFunction(
        ast.args,
        state,
        this.metadata('DATEDIF_COMPAT'),
        (startValue: unknown, endValue: unknown, unit: string) => {
          const startSerial = normalizeFormulaDateSerial(startValue)
          const endSerial = normalizeFormulaDateSerial(endValue)
          if (startSerial == null || endSerial == null) {
            return new CellError(ErrorType.VALUE, 'Invalid date value')
          }

          const result = calculateFormulaDateDif(startSerial, endSerial, unit)
          return result == null
            ? new CellError(ErrorType.VALUE, 'Invalid DATEDIF arguments')
            : result
        },
      )
    }

    textJoinCompat(ast: { args: unknown[] }, state: unknown) {
      return this.runFunction(
        ast.args,
        state,
        this.metadata('TEXTJOIN_COMPAT'),
        (delimiter: string, ignoreEmptyValue: unknown, ...items: unknown[]) => {
          const ignoreEmpty = Boolean(ignoreEmptyValue)
          const values = items
            .flat(Number.POSITIVE_INFINITY)
            .map((item) => normalizeCellValue(item))
            .filter((item) => !ignoreEmpty || item !== '')
          return values.join(delimiter)
        },
      )
    }
  }

  HyperFormula.registerFunctionPlugin(SheetFormulaPlugin, {
    enGB: {
      RE_SUB: 'RE_SUB',
      DATE_PARSE: 'DATE_PARSE',
      DATEDIF_COMPAT: 'DATEDIF_COMPAT',
      TEXTJOIN_COMPAT: 'TEXTJOIN_COMPAT',
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
    dataStartRow: 0,
  }
}

function buildFormulaDisplayStateForRows(
  headers: string[],
  sourceRows: SheetRow[],
  sourceConfigs: Record<string, SheetColumnConfig> = columnConfigs.value,
  options: FormulaDisplayBuildOptions = {},
): FormulaDisplayState {
  const normalizedDataRows = sourceRows.map((row) => normalizeRow(row, headers))
  const normalizedHeaderRows = (options.headerRows ?? getCurrentFormulaHeaderRows(headers))
    .map((row) => normalizeRow(row, headers))
  const headerRowCount = normalizeNonNegativeInt(options.headerRowCount, normalizedHeaderRows.length)
  const normalizedRows = [
    ...normalizedHeaderRows.slice(0, headerRowCount),
    ...normalizedDataRows,
  ]
  const rowOffset = normalizeNonNegativeInt(options.rowOffset, getCurrentFormulaDisplayRowOffset())
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
      dataStartRow: headerRowCount,
    }
  }

  const FormulaEngine = formulaEngineClass.value
  if (!FormulaEngine) {
    void ensureFormulaEngineLoaded()
    return {
      cells: [],
      errorKeys: new Set(),
      dataStartRow: headerRowCount,
    }
  }

  const engineRows = normalizedRows.map((row) => row.map((cellValue) => (
    isFormulaExpression(cellValue)
      ? normalizeFormulaExpressionForPagedEngine(cellValue, rowOffset, normalizedDataRows.length, headerRowCount)
      : normalizeFormulaEngineCellValue(cellValue)
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

  return { cells, errorKeys, dataStartRow: headerRowCount }
}

function buildFormulaDisplayState(): FormulaDisplayState {
  const headers = normalizeHeaders(columnHeaders.value)
  const headerRows = getCurrentFormulaHeaderRows(headers)
  return buildFormulaDisplayStateForRows(
    headers,
    rows.value,
    columnConfigs.value,
    {
      headerRows,
      headerRowCount: headerRows.length,
    },
  )
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
  const gridRowIndex = (displayState?.dataStartRow ?? sheetHeaderRowCount.value) + rowIndex
  return displayState?.cells[gridRowIndex]?.[columnIndex]?.text ?? ''
}

function getFormulaCellModel(rowIndex: number, columnIndex: number) {
  if (rowIndex < 0 || columnIndex < 0) {
    return null
  }
  const row = rows.value[rowIndex]
  if (!row || !isFormulaExpression(row[columnIndex])) {
    return null
  }
  return getFormulaCellModelAtGridRow(formulaDisplayState.value.dataStartRow + rowIndex, columnIndex)
}

function getFormulaCellModelAtGridRow(gridRowIndex: number, columnIndex: number) {
  if (gridRowIndex < 0 || columnIndex < 0) {
    return null
  }
  return formulaDisplayState.value.cells[gridRowIndex]?.[columnIndex] ?? null
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

function normalizeColumnFilterQuery(value: unknown) {
  return normalizeCellValue(value).trim()
}

function normalizeColumnFilterSearchText(value: unknown) {
  return normalizeCellValue(value).trim().toLowerCase()
}

function normalizeColumnFilterOptionValue(value: unknown) {
  return normalizeCellValue(value).trim()
}

function getColumnFilterOptionLabel(value: string) {
  return value || '(空白)'
}

function normalizeColumnFilterExcludedValues(value: unknown) {
  if (!Array.isArray(value)) {
    return []
  }

  const seen = new Set<string>()
  const values: string[] = []
  value.forEach((item) => {
    const normalized = normalizeColumnFilterOptionValue(item)
    if (seen.has(normalized)) {
      return
    }
    seen.add(normalized)
    values.push(normalized)
  })
  return values
}

function normalizeColumnFilterState(value: unknown): ColumnFilterState {
  if (typeof value === 'string') {
    return {
      query: normalizeColumnFilterQuery(value),
      excludedValues: [],
    }
  }
  if (!value || typeof value !== 'object') {
    return {
      query: '',
      excludedValues: [],
    }
  }

  const record = value as Record<string, unknown>
  return {
    query: normalizeColumnFilterQuery(record.query),
    excludedValues: normalizeColumnFilterExcludedValues(record.excludedValues ?? record.excluded_values),
  }
}

function isColumnFilterStateEmpty(state: ColumnFilterState) {
  return !state.query && state.excludedValues.length === 0
}

function getColumnFilterState(header: string) {
  return normalizeColumnFilterState(columnFilters.value[header])
}

function setColumnFilterState(header: string, state: ColumnFilterState) {
  const normalized = normalizeColumnFilterState(state)
  const nextFilters = { ...columnFilters.value }
  if (isColumnFilterStateEmpty(normalized)) {
    delete nextFilters[header]
  } else {
    nextFilters[header] = normalized
  }
  columnFilters.value = nextFilters
}

function isColumnFilterEnabled(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  return !!header && normalizeColumnConfig(columnConfigs.value[header]).filter_enabled
}

function isColumnFilterOptionMode(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  return !!header && normalizeColumnConfig(columnConfigs.value[header]).value_mode === 'fixed_options'
}

function getColumnFilterOptions(columnIndex: number): ColumnFilterOptionStat[] {
  const counts = new Map<string, number>()
  rows.value.forEach((row, rowIndex) => {
    const rawValue = row?.[columnIndex] ?? ''
    const displayText = getCellDisplayText(rowIndex, columnIndex, rawValue)
    const value = normalizeColumnFilterOptionValue(displayText)
    counts.set(value, (counts.get(value) ?? 0) + 1)
  })

  return [...counts.entries()]
    .map(([value, count]) => ({
      value,
      label: getColumnFilterOptionLabel(value),
      count,
    }))
    .sort((left, right) => (
      right.count - left.count
      || HASH_COLOR_SEED_COLLATOR.compare(left.label, right.label)
    ))
}

function isColumnFilterActive(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  if (!header || !isColumnFilterEnabled(columnIndex)) {
    return false
  }
  const state = getColumnFilterState(header)
  return state.query !== '' || (isColumnFilterOptionMode(columnIndex) && state.excludedValues.length > 0)
}

function getActiveColumnFilterEntries() {
  return columnHeaders.value
    .map((header, columnIndex) => {
      const state = getColumnFilterState(header)
      const excludedValues = isColumnFilterOptionMode(columnIndex)
        ? state.excludedValues
        : []
      return {
        header,
        columnIndex,
        query: state.query,
        excludedValues: new Set(excludedValues),
      }
    })
    .filter((item) => (
      isColumnFilterEnabled(item.columnIndex)
      && (item.query !== '' || item.excludedValues.size > 0)
    ))
}

function doesRowMatchColumnFilters(row: SheetRow, rowIndex: number) {
  for (const filter of activeColumnFilterEntries.value) {
    const rawValue = row[filter.columnIndex] ?? ''
    const displayText = getCellDisplayText(rowIndex, filter.columnIndex, rawValue)
    if (
      filter.query
      && !normalizeColumnFilterSearchText(displayText).includes(normalizeColumnFilterSearchText(filter.query))
    ) {
      return false
    }
    if (filter.excludedValues.has(normalizeColumnFilterOptionValue(displayText))) {
      return false
    }
  }
  return true
}

function pruneColumnFilters() {
  const nextFilters: Record<string, ColumnFilterState> = {}
  columnHeaders.value.forEach((header, columnIndex) => {
    if (!isColumnFilterEnabled(columnIndex)) {
      return
    }
    const state = getColumnFilterState(header)
    const normalized: ColumnFilterState = {
      query: state.query,
      excludedValues: isColumnFilterOptionMode(columnIndex) ? state.excludedValues : [],
    }
    if (!isColumnFilterStateEmpty(normalized)) {
      nextFilters[header] = normalized
    }
  })
  if (JSON.stringify(nextFilters) !== JSON.stringify(columnFilters.value)) {
    columnFilters.value = nextFilters
  }
  const popoverColumn = columnFilterPopover.value.columnIndex
  if (popoverColumn != null && !isColumnFilterEnabled(popoverColumn)) {
    closeColumnFilterPopover()
  }
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

function remapRowsFormulaReferencesByRowDelta(sourceRows: SheetRow[], rowDelta: number) {
  if (rowDelta === 0) {
    return sourceRows
  }

  let changed = false
  const nextRows = sourceRows.map((row) => row.map((cellValue) => {
    if (!isFormulaExpression(cellValue)) {
      return cellValue
    }
    const nextValue = remapFormulaCellReferences(cellValue, (rowIndex) => {
      const nextRowIndex = rowIndex + rowDelta
      return nextRowIndex >= 0 ? nextRowIndex : null
    })
    if (nextValue !== cellValue) {
      changed = true
    }
    return nextValue
  }))
  return changed ? nextRows : sourceRows
}

function remapRowsFormulaReferencesForHeaderRowCountChange(
  sourceRows: SheetRow[],
  previousHeaderRowCount: number,
  nextHeaderRowCount: number,
) {
  const delta = nextHeaderRowCount - previousHeaderRowCount
  if (delta === 0) {
    return sourceRows
  }

  let changed = false
  const nextRows = sourceRows.map((row) => row.map((cellValue) => {
    if (!isFormulaExpression(cellValue)) {
      return cellValue
    }

    const nextValue = remapFormulaCellReferences(cellValue, (rowIndex) => {
      if (delta > 0) {
        return rowIndex >= previousHeaderRowCount ? rowIndex + delta : rowIndex
      }
      if (rowIndex >= nextHeaderRowCount && rowIndex < previousHeaderRowCount) {
        return null
      }
      return rowIndex >= previousHeaderRowCount ? rowIndex + delta : rowIndex
    })
    if (nextValue !== cellValue) {
      changed = true
    }
    return nextValue
  }))
  return changed ? nextRows : sourceRows
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

function shiftAutofillRange(range: SheetAutofillRange, rowOffset: number, columnOffset = 0): SheetAutofillRange {
  return {
    getTopStartCorner: () => {
      const corner = range.getTopStartCorner()
      return { row: corner.row + rowOffset, col: corner.col + columnOffset }
    },
    getBottomEndCorner: () => {
      const corner = range.getBottomEndCorner()
      return { row: corner.row + rowOffset, col: corner.col + columnOffset }
    },
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

function getCellActionDisplayLabel(action: SheetCellAction, rawText: string) {
  return rawText.trim() || action.label?.trim() || SHEET_CELL_ACTION_LABELS[action.type]
}

function getCellActionTitle(action: SheetCellAction) {
  if (action.type === SHEET_CELL_ACTION_EXCEL_IMPORT_RESET) {
    return '导入 Excel：上传文件并重置此按钮后的数据行'
  }
  if (action.type === SHEET_CELL_ACTION_REGISTRATION_ORDER_MATCH) {
    return '更新订单匹配：按微信支付订单号补全订单日期、商户订单号、订单金额和已返款'
  }
  if (action.type === SHEET_CELL_ACTION_REGISTRATION_USER_MATCH) {
    return '更新用户匹配：默认查本地用户库，可选择 codepc_mi15 小鹅通兜底'
  }
  return ''
}

function renderCellActionButton(TD: HTMLTableCellElement, label: string) {
  TD.textContent = ''
  const button = document.createElement('button')
  button.type = 'button'
  button.tabIndex = -1
  button.className = 'sheet-cell-action-button-inner'
  button.textContent = label
  TD.appendChild(button)
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
    const pageState = normalizeDraftPageState(payload.pageState)
    return {
      version: 1,
      updatedAt: normalizeTimestampMs(payload.updatedAt),
      sheetVersion: payload.sheetVersion == null ? null : Number(payload.sheetVersion),
      title: String(payload.title ?? '').trim() || '未命名表格',
      document: normalizeSheetDocument(payload.document, {
        rowOffset: pageState?.paginationEnabled ? pageState.rowOffset : 0,
      }),
      pageState,
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

function syncContextMenuSelectionFromEvent(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof HTMLElement)) {
    return
  }

  const cell = target.closest('td, th') as HTMLElement | null
  if (!cell || !sheetFrameRef.value?.contains(cell)) {
    return
  }

  const rowMarkerValue = cell.dataset.sheetRowMarker
  if (rowMarkerValue != null) {
    const row = normalizeNonNegativeInt(rowMarkerValue, -1)
    if (row >= 0) {
      selectRowFromMarker(row)
    }
    return
  }

  const headerLevel = normalizeNonNegativeInt(cell.dataset.sheetHeaderLevel, -1)
  const headerColumn = normalizeNonNegativeInt(cell.dataset.sheetHeaderColumn, -1)
  if (headerLevel < 0 || headerColumn < 0 || headerColumn >= columnHeaders.value.length) {
    return
  }

  if (headerLevel === columnHeaderLevel.value) {
    selectColumnMarker(headerColumn)
    return
  }

  selectedSheetHeaderCell.value = { column: headerColumn, headerLevel }
  clearColumnMarkerSelection()
}

function openSheetContextMenuAt(event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()
  syncContextMenuSelectionFromEvent(event)

  const hot = getHotInstance()
  const plugin = getContextMenuPlugin()
  if (!hot || !plugin?.open || !hasSelection()) {
    refreshContextMenuFallbackSelectionState()
    return
  }

  hot.listen()
  plugin.open({
    left: event.clientX,
    top: event.clientY,
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

function isSelectedByRowMarkerSelection() {
  if (rowMarkerColumnCount.value <= 0) {
    return false
  }
  const selection = getHotInstance()?.getSelectedLast()
  if (!selection) {
    return false
  }
  const startRow = Math.min(selection[0], selection[2])
  const endRow = Math.max(selection[0], selection[2])
  const startColumn = Math.min(selection[1], selection[3])
  const endColumn = Math.max(selection[1], selection[3])
  return (
    endRow >= sheetHeaderRowCount.value
    && startRow >= 0
    && startColumn <= 0
    && endColumn >= getHotColumnCount() - 1
  )
}

function clearScheduledRowMarkerSelection() {
  if (rowMarkerSelectionTimer != null && typeof window !== 'undefined') {
    window.clearTimeout(rowMarkerSelectionTimer)
  }
  rowMarkerSelectionTimer = null
}

function selectRowFromMarker(gridRow: number) {
  const hot = getHotInstance()
  if (!hot || gridRow < sheetHeaderRowCount.value) {
    return false
  }

  clearScheduledRowMarkerSelection()
  const run = () => {
    rowMarkerSelectionTimer = null
    const latestHot = getHotInstance()
    if (!latestHot) {
      return
    }

    latestHot.selectCell(gridRow, 0, gridRow, Math.max(0, getHotColumnCount() - 1), true, false)
    latestHot.listen()
    refreshContextMenuFallbackSelectionState()
  }

  if (typeof window === 'undefined') {
    run()
  } else {
    run()
    rowMarkerSelectionTimer = window.setTimeout(run, 0)
  }
  return true
}

function shouldShowRowActions() {
  return hasSelection() && (isSelectedByRowHeader() || isSelectedByCorner() || isSelectedByRowMarkerSelection())
}

function shouldShowColumnActions() {
  return hasSelection() && (isSelectedByColumnHeader() || isSelectedByCorner())
}

function isWholeRowSelection() {
  return isSelectedByRowHeader() || isSelectedByRowMarkerSelection()
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

function getColumnFontSizeFromConfig(config: SheetColumnConfig | undefined) {
  return normalizeColumnFontSize(config?.font_size)
}

function getColumnFontFamilyFromConfig(config: SheetColumnConfig | undefined) {
  return normalizeColumnFontFamily(config?.font_family)
}

function getContentFontFamilyCss(fontFamily: ColumnFontFamily) {
  return fontFamily === 'monospace' ? MONOSPACE_FONT_FAMILY : TABLE_FONT_FAMILY
}

function getColumnFontFamilyStyle(config: SheetColumnConfig | undefined) {
  const fontFamily = getColumnFontFamilyFromConfig(config)
  return fontFamily === 'default' ? '' : getContentFontFamilyCss(fontFamily)
}

function getCellFontFamilyStyle(fontFamily: CellFontFamily) {
  return getContentFontFamilyCss(fontFamily)
}

function getColumnCellFontFromSize(fontSize: number, fontFamily: ColumnFontFamily = 'default') {
  if (fontSize === DEFAULT_COLUMN_FONT_SIZE && fontFamily === 'default') {
    return TABLE_FONT
  }
  const resolvedFontSize = fontSize === DEFAULT_COLUMN_FONT_SIZE ? 14 : fontSize
  return `400 ${resolvedFontSize}px ${getContentFontFamilyCss(fontFamily)}`
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
  const columnConfig = sourceConfigs[header]
  const cellFont = getColumnCellFontFromSize(
    getColumnFontSizeFromConfig(columnConfig),
    getColumnFontFamilyFromConfig(columnConfig),
  )
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
  if (isSheetHeaderGridRow(rowIndex)) {
    if (rowIndex < normalizedHeaderGroups.value.length) {
      return 32
    }
    if (rowIndex === columnHeaderLevel.value) {
      return 36
    }
    return 92
  }

  const dataRowIndex = getDataRowIndex(rowIndex)
  const layoutState = rowHeightLayoutState.value
  if (!layoutState.hasWrappedColumns) {
    return layoutState.singleLineHeight
  }

  const row = rows.value[dataRowIndex] ?? []
  let maxContentHeight = layoutState.singleLineHeight - TABLE_CELL_VERTICAL_PADDING * 2 - TABLE_CELL_BORDER_WIDTH

  for (const columnLayout of layoutState.wrappedColumns) {
    const cellText = getCellTextForLayout(dataRowIndex, columnLayout.index, row[columnLayout.index] ?? '')
    if (!cellText) {
      maxContentHeight = Math.max(maxContentHeight, columnLayout.lineHeight)
      continue
    }

    const availableWidth = Math.max(
      getEffectiveColumnWidth(columnLayout.index) - TABLE_CELL_HORIZONTAL_PADDING * 2,
      12,
    )
    const cellFont = getColumnCellFontFromSize(columnLayout.fontSize, columnLayout.fontFamily)
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
}

function handleWindowResize() {
  closeColumnNotePopover()
  void updateSheetViewportHeight()
}

function captureSheetScrollPosition() {
  const gridElement = sheetFrameRef.value?.querySelector('.ht_master .wtHolder') as HTMLElement | null
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
    const gridElement = sheetFrameRef.value?.querySelector('.ht_master .wtHolder') as HTMLElement | null
    const pageElement = getMainScrollContainer()
    if (gridElement) {
      gridElement.scrollLeft = position.gridLeft
      gridElement.scrollTop = position.gridTop
    }
    if (pageElement) {
      pageElement.scrollLeft = position.pageLeft
      pageElement.scrollTop = position.pageTop
    }
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
  rows.value = sourceRows
    .slice(sheetHeaderRowCount.value)
    .map((row) => normalizeRow(Array.isArray(row) ? row.slice(rowMarkerColumnCount.value) : row, columnHeaders.value))
  return rows.value
}

function refreshGridStructure() {
  const hot = getHotInstance()
  if (!hot) {
    return
  }

  hot.updateSettings({
    data: sheetHotGridRows.value,
    colHeaders: sheetColumnHeaders.value,
    colWidths: [...sheetHotColumnWidths.value],
    rowHeaders: false,
    fixedRowsTop: sheetHeaderRowCount.value,
    fixedColumnsStart: fixedHotColumnsStart.value,
    hiddenColumns: {
      columns: [...hotHiddenColumnIndexes.value],
      indicators: false,
    },
    hiddenRows: {
      rows: [...sheetFilterHiddenRows.value],
      indicators: false,
    },
  })
  hot.updateSettings({
    mergeCells: sheetHotMergeCells.value,
  })
  hot.render()
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
  clearSheetHeaderSelection()
  clearFormulaBarSelection()
  const anchor = extendSelection
    ? (columnMarkerSelectionAnchor ?? selectedColumnMarkerBounds.value?.start ?? columnIndex)
    : columnIndex
  const startColumn = Math.min(anchor, columnIndex)
  const endColumn = Math.max(anchor, columnIndex)
  const selected = startColumn === endColumn
    ? hot.selectColumns(toHotColumnIndex(startColumn))
    : hot.selectColumns(toHotColumnIndex(startColumn), toHotColumnIndex(endColumn))
  if (selected) {
    columnMarkerSelectionAnchor = anchor
  }
  hot.listen()
  syncColumnMarkerSelection()
  refreshContextMenuFallbackSelectionState()
  return selected
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
      .map((index) => getDataRowIndex(index))
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

function normalizeCurrentMergedCells(
  source = mergedCells.value,
  rowCount = getMergedCellsDocumentRowCount(),
  columnCount = columnHeaders.value.length,
) {
  return normalizeMergedCells(source, rowCount, columnCount)
}

function insertMergedCellRows(startRow: number, amount: number) {
  if (amount <= 0) {
    return
  }
  mergedCells.value = normalizeCurrentMergedCells(mergedCells.value.map((cell) => {
    if (cell.row < startRow && startRow < cell.row + cell.rowspan) {
      return { ...cell, rowspan: cell.rowspan + amount }
    }
    return cell.row >= startRow ? { ...cell, row: cell.row + amount } : cell
  }), getMergedCellsDocumentRowCount() + amount)
}

function removeMergedCellRows(startRow: number, amount: number) {
  if (amount <= 0) {
    return
  }
  const endRow = startRow + amount
  mergedCells.value = normalizeCurrentMergedCells(mergedCells.value.map((cell) => {
    const cellEnd = cell.row + cell.rowspan
    if (cellEnd <= startRow) {
      return cell
    }
    if (cell.row >= endRow) {
      return { ...cell, row: cell.row - amount }
    }
    const overlap = Math.min(cellEnd, endRow) - Math.max(cell.row, startRow)
    const nextRowspan = cell.rowspan - Math.max(0, overlap)
    const nextRow = cell.row >= startRow ? startRow : cell.row
    return { ...cell, row: nextRow, rowspan: nextRowspan }
  }).filter((cell) => cell.rowspan > 1 || cell.colspan > 1))
}

function insertMergedCellColumns(startColumn: number, amount: number) {
  if (amount <= 0) {
    return
  }
  mergedCells.value = normalizeCurrentMergedCells(mergedCells.value.map((cell) => {
    if (cell.col < startColumn && startColumn < cell.col + cell.colspan) {
      return { ...cell, colspan: cell.colspan + amount }
    }
    return cell.col >= startColumn ? { ...cell, col: cell.col + amount } : cell
  }), getMergedCellsDocumentRowCount(), columnHeaders.value.length + amount)
}

function removeMergedCellColumns(startColumn: number, amount: number) {
  if (amount <= 0) {
    return
  }
  const endColumn = startColumn + amount
  mergedCells.value = normalizeCurrentMergedCells(mergedCells.value.map((cell) => {
    const cellEnd = cell.col + cell.colspan
    if (cellEnd <= startColumn) {
      return cell
    }
    if (cell.col >= endColumn) {
      return { ...cell, col: cell.col - amount }
    }
    const overlap = Math.min(cellEnd, endColumn) - Math.max(cell.col, startColumn)
    const nextColspan = cell.colspan - Math.max(0, overlap)
    const nextCol = cell.col >= startColumn ? startColumn : cell.col
    return { ...cell, col: nextCol, colspan: nextColspan }
  }).filter((cell) => cell.rowspan > 1 || cell.colspan > 1))
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

  const headerRowCount = sheetHeaderRowCount.value
  const pageStart = getDocumentRowIndex(0)
  const pageEnd = pageStart + sourceRows.length
  const formulaRowIndexMapper = rowIndexMap
    ? (rowIndex: number) => {
        if (rowIndex < headerRowCount) {
          return rowIndex
        }
        const documentDataRowIndex = rowIndex - headerRowCount
        if (documentDataRowIndex < pageStart || documentDataRowIndex >= pageEnd) {
          return rowIndex
        }
        const localDataRowIndex = documentDataRowIndex - pageStart
        return headerRowCount + pageStart + (rowIndexMap.get(localDataRowIndex) ?? localDataRowIndex)
      }
    : undefined

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
        formulaRowIndexMapper,
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
  const headerRowCount = sheetHeaderRowCount.value
  const formulaRowIndexMapper = rowIndexMapper
    ? (rowIndex: number) => {
        if (rowIndex < headerRowCount) {
          return rowIndex
        }
        const documentDataRowIndex = rowIndex - headerRowCount
        const nextDocumentDataRowIndex = rowIndexMapper(documentDataRowIndex)
        return nextDocumentDataRowIndex == null ? null : headerRowCount + nextDocumentDataRowIndex
      }
    : undefined
  const nextRows = rows.value.map((row, rowIndex) => (
    normalizeRow(row, headers).map((cellValue, columnIndex) => {
      if (!isFormulaExpression(cellValue)) {
        return cellValue
      }

      const nextValue = remapFormulaCellReferences(cellValue, formulaRowIndexMapper, columnIndexMapper)
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
  getHotInstance()?.updateSettings({ data: sheetHotGridRows.value })
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
  const pageStart = sheetHeaderRowCount.value + getDocumentRowIndex(0)
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

function canMoveMergedCellsColumns(movedColumns: number[]) {
  const moved = new Set(movedColumns)
  return normalizeCurrentMergedCells().every((cell) => {
    let touched = 0
    for (let column = cell.col; column < cell.col + cell.colspan; column += 1) {
      if (moved.has(column)) {
        touched += 1
      }
    }
    return touched === 0 || touched === cell.colspan
  })
}

function moveMergedCellsColumns(movedColumns: number[], finalIndex: number) {
  const indexMap = buildMoveIndexMap(columnHeaders.value.length, movedColumns, finalIndex)
  mergedCells.value = normalizeCurrentMergedCells(mergedCells.value.map((cell) => {
    const mappedColumns = Array.from({ length: cell.colspan }, (_, offset) => indexMap.get(cell.col + offset) ?? cell.col + offset)
      .sort((left, right) => left - right)
    return { ...cell, col: mappedColumns[0] ?? cell.col }
  }))
}

function canMoveMergedCellsRows(movedRows: number[]) {
  const pageStart = sheetHeaderRowCount.value + getDocumentRowIndex(0)
  const moved = new Set(movedRows.map((row) => pageStart + row))
  return normalizeCurrentMergedCells().every((cell) => {
    let touched = 0
    for (let row = cell.row; row < cell.row + cell.rowspan; row += 1) {
      if (moved.has(row)) {
        touched += 1
      }
    }
    return touched === 0 || touched === cell.rowspan
  })
}

function moveMergedCellsRows(movedRows: number[], finalIndex: number) {
  const pageStart = sheetHeaderRowCount.value + getDocumentRowIndex(0)
  const indexMap = buildMoveIndexMap(rows.value.length, movedRows, finalIndex)
  mergedCells.value = normalizeCurrentMergedCells(mergedCells.value.map((cell) => {
    if (cell.row < pageStart || cell.row >= pageStart + rows.value.length) {
      return cell
    }
    const mappedRows = Array.from({ length: cell.rowspan }, (_, offset) => {
      const localRow = cell.row + offset - pageStart
      return pageStart + (indexMap.get(localRow) ?? localRow)
    }).sort((left, right) => left - right)
    return { ...cell, row: mappedRows[0] ?? cell.row }
  }))
}

function remapVisiblePageCellMetaRows(rowIndexMap: Map<number, number>) {
  const pageStart = sheetHeaderRowCount.value + getDocumentRowIndex(0)
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

  if (movedColumns.some((column) => isRowMarkerHotColumn(column))) {
    return false
  }

  const movedSheetColumns = movedColumns.map((column) => toSheetColumnIndex(column))
  const sheetFinalIndex = Math.max(0, toSheetColumnIndex(finalIndex))
  const sheetDropIndex = dropIndex == null ? undefined : Math.max(0, toSheetColumnIndex(dropIndex))
  const effectiveMovedColumns = getEffectiveMovedColumnsForDrag(movedSheetColumns)
  const effectiveFinalIndex = countMoveFinalIndex(effectiveMovedColumns, sheetDropIndex, sheetFinalIndex)
  const effectiveMovePossible = (
    movePossible
    && effectiveMovedColumns.length > 0
    && effectiveFinalIndex >= 0
    && effectiveFinalIndex + effectiveMovedColumns.length <= columnHeaders.value.length
    && canMoveMergedCellsColumns(effectiveMovedColumns)
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
  moveMergedCellsColumns(effectiveMovedColumns, effectiveFinalIndex)
  columnHeaders.value = nextHeaders
  columnWidths.value = nextWidths
  rows.value = nextRows
  columnConfigs.value = normalizeColumnConfigs(columnConfigs.value, nextHeaders)
  const movedRangeStart = effectiveFinalIndex
  const movedRangeEnd = effectiveFinalIndex + effectiveMovedColumns.length - 1
  const hot = getHotInstance()
  if (hot) {
    hot.updateSettings({
      data: sheetHotGridRows.value,
      colHeaders: sheetColumnHeaders.value,
      colWidths: [...sheetHotColumnWidths.value],
      fixedRowsTop: sheetHeaderRowCount.value,
      fixedColumnsStart: fixedHotColumnsStart.value,
      mergeCells: sheetHotMergeCells.value,
    })
    hot.render()
    void nextTick(() => {
      hot.selectColumns(toHotColumnIndex(movedRangeStart), toHotColumnIndex(movedRangeEnd))
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
  const dataFinalIndex = Math.max(0, getDataRowIndex(finalIndex))
  const dataDropIndex = dropIndex == null ? undefined : Math.max(0, getDataRowIndex(dropIndex))
  const effectiveFinalIndex = countMoveFinalIndex(effectiveMovedRows, dataDropIndex, dataFinalIndex)
  const effectiveMovePossible = (
    movePossible
    && effectiveMovedRows.length > 0
    && effectiveFinalIndex >= 0
    && effectiveFinalIndex + effectiveMovedRows.length <= rows.value.length
    && canMoveMergedCellsRows(effectiveMovedRows)
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
  moveMergedCellsRows(effectiveMovedRows, effectiveFinalIndex)
  rows.value = nextRows
  const movedRangeStart = effectiveFinalIndex
  const movedRangeEnd = effectiveFinalIndex + effectiveMovedRows.length - 1
  const hot = getHotInstance()
  if (hot) {
    hot.updateSettings({
      data: sheetHotGridRows.value,
    })
    hot.render()
    void nextTick(() => {
      hot.selectRows(getGridRowIndex(movedRangeStart), getGridRowIndex(movedRangeEnd))
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

  if (nextHeader === currentHeader) {
    clearEditingColumnState()
    refreshGridStructure()
    return
  }

  if (!renameColumnHeaderAtIndex(columnIndex, nextHeader, { focusOnInvalid: true })) {
    return
  }

  clearEditingColumnState()
  refreshGridStructure()
}

function renameColumnHeaderAtIndex(
  columnIndex: number,
  nextHeaderSource: string,
  options: { focusOnInvalid?: boolean } = {},
) {
  if (columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return false
  }

  const currentHeader = columnHeaders.value[columnIndex] ?? createFallbackHeader(columnIndex)
  const nextHeader = nextHeaderSource.trim()

  if (!nextHeader) {
    ElMessage.warning('字段名不能为空')
    if (options.focusOnInvalid) {
      focusEditingColumnInput()
    }
    return false
  }

  if (columnHeaders.value.some((header, index) => index !== columnIndex && header === nextHeader)) {
    ElMessage.warning('字段名不能重复')
    if (options.focusOnInvalid) {
      focusEditingColumnInput()
    }
    return false
  }

  if (nextHeader === currentHeader) {
    return false
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
  return true
}

function setColumnNoteAtIndex(columnIndex: number, nextNoteSource: string) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return false
  }

  const nextNote = normalizeColumnNote(nextNoteSource)
  const currentNote = getColumnNote(columnIndex)
  if (nextNote === currentNote) {
    return false
  }

  const nextConfigs = { ...columnConfigs.value }
  const nextConfig: SheetColumnConfig = { ...(nextConfigs[header] ?? {}) }
  if (nextNote) {
    nextConfig.note = nextNote
  } else {
    delete nextConfig.note
  }
  nextConfigs[header] = nextConfig
  const previousFormulaHeaderRowCount = getCurrentFormulaHeaderRows().length
  const normalizedNextConfigs = normalizeColumnConfigs(nextConfigs, columnHeaders.value)
  const nextFormulaHeaderRowCount = getFormulaHeaderRowsForDocument(
    columnHeaders.value,
    normalizedHeaderGroups.value,
    normalizedNextConfigs,
    sheetViewSettings.value,
  ).length
  columnConfigs.value = normalizedNextConfigs
  const nextRows = remapRowsFormulaReferencesForHeaderRowCountChange(
    rows.value,
    previousFormulaHeaderRowCount,
    nextFormulaHeaderRowCount,
  )
  if (nextRows !== rows.value) {
    rows.value = nextRows
  }
  return true
}

function setHeaderGroupLabelAtGridCell(rowIndex: number, columnIndex: number, nextLabelSource: string) {
  if (rowIndex < 0 || rowIndex >= normalizedHeaderGroups.value.length || columnIndex < 0) {
    return false
  }

  const nextGroups = normalizedHeaderGroups.value.map((row) => row.map((cell) => ({ ...cell })))
  const row = nextGroups[rowIndex]
  if (!row) {
    return false
  }

  let startColumn = 0
  for (let cellIndex = 0; cellIndex < row.length; cellIndex += 1) {
    const cell = row[cellIndex]
    const colspan = Math.max(1, cell.colspan ?? 1)
    const endColumn = startColumn + colspan - 1
    if (columnIndex >= startColumn && columnIndex <= endColumn) {
      const nextLabel = normalizeCellValue(nextLabelSource)
      if (nextLabel === cell.label) {
        return false
      }
      row[cellIndex] = { ...cell, label: nextLabel }
      headerGroups.value = normalizeHeaderGroups(nextGroups, columnHeaders.value.length)
      return true
    }
    startColumn += colspan
  }

  return false
}

function applySheetHeaderGridChange(gridRowIndex: number, columnIndex: number, nextValue: unknown) {
  if (columnIndex < 0 || !isSheetHeaderGridRow(gridRowIndex)) {
    return false
  }

  const textValue = normalizeCellValue(nextValue)
  if (gridRowIndex < normalizedHeaderGroups.value.length) {
    return setHeaderGroupLabelAtGridCell(gridRowIndex, columnIndex, textValue)
  }
  if (gridRowIndex === columnHeaderLevel.value) {
    return renameColumnHeaderAtIndex(columnIndex, textValue)
  }
  if (gridRowIndex === columnNoteHeaderLevel.value) {
    return setColumnNoteAtIndex(columnIndex, textValue)
  }
  return false
}

function cancelInlineRenameColumn() {
  clearEditingColumnState()
  refreshGridStructure()
}

function isManualColumnResizerMouseTarget(event: MouseEvent) {
  const target = event.target
  return target instanceof Element && !!target.closest('.manualColumnResizer')
}

function isSheetCellActionButtonMouseTarget(event: MouseEvent) {
  const target = event.target
  return target instanceof Element && !!target.closest('.sheet-cell-action-button-inner')
}

function handleBeforeCellMouseDown(
  event: MouseEvent,
  coords: { row: number; col: number },
  _td: HTMLTableCellElement,
  controller: CellMouseSelectionController,
) {
  if (coords.row < 0) {
    return
  }

  if (coords.col < 0) {
    return
  }

  if (isRowMarkerHotColumn(coords.col)) {
    event.preventDefault()
    event.stopImmediatePropagation()
    if (controller) {
      controller.row = false
      controller.column = false
      controller.cell = false
    }
    clearSheetHeaderSelection()
    clearFormulaBarSelection()
    selectRowFromMarker(coords.row)
    return
  }

  const column = toSheetColumnIndex(coords.col)
  if (column < 0) {
    return
  }

  const anchor = getMergeAnchorForGridCell(coords.row, column)

  if (isSheetHeaderGridRow(coords.row) && isManualColumnResizerMouseTarget(event)) {
    return
  }

  const sheetCellAction = event.button === 0
    ? getSheetCellActionAtDocumentCell(anchor.documentRow, anchor.column)
    : null
  if (sheetCellAction && isSheetCellActionButtonMouseTarget(event)) {
    event.preventDefault()
    event.stopPropagation()
    runSheetCellAction(sheetCellAction, {
      documentRow: anchor.documentRow,
      column: anchor.column,
    })
    return
  }

  if (isSheetHeaderGridRow(coords.row)) {
    const headerLevel = getSheetHeaderGridLevel(anchor.row)
    if (
      event.button === 0
      && insertFormulaReferenceText(getCellReferenceLabelForSheetRow(anchor.documentRow, anchor.column))
    ) {
      markFormulaReferencePointerDown()
      stopFormulaReferenceCellSelection(event, controller)
      return
    }

    if (headerLevel === columnHeaderLevel.value && (event.ctrlKey || event.metaKey)) {
      const headerLink = getCellLinkAt(anchor.documentRow, anchor.column) ?? getColumnHeaderLink(anchor.column)
      if (headerLink) {
        event.preventDefault()
        event.stopPropagation()
        openCellLink(headerLink)
        return
      }
    }
    selectedSheetHeaderCell.value = { column: anchor.column, headerLevel }
    return
  }

  clearSheetHeaderSelection()
  if (event.button !== 0) {
    return
  }

  const dataRow = getDataRowIndex(anchor.row)
  if (dataRow < 0) {
    return
  }
  if (beginFormulaReferenceRange(dataRow, anchor.column)) {
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
  const column = toSheetColumnIndex(coords.col)
  if (column < 0) {
    return
  }
  const dataRow = getDataRowIndex(coords.row)
  if (dataRow < 0) {
    return
  }

  if (event.buttons !== 1) {
    finishFormulaReferenceRange({ restoreFocus: true })
    return
  }

  updateFormulaReferenceRange(dataRow, column)
  stopFormulaReferenceCellSelection(event, controller)
}

function handleBeforeCellMouseUp(event: MouseEvent, coords: { row: number; col: number }) {
  if (!formulaReferenceRangeState) {
    return
  }

  if (coords.row >= 0 && coords.col >= 0) {
    const column = toSheetColumnIndex(coords.col)
    const dataRow = getDataRowIndex(coords.row)
    if (dataRow >= 0 && column >= 0) {
      updateFormulaReferenceRange(dataRow, column)
    }
  }
  finishFormulaReferenceRange({ restoreFocus: true })
  event.preventDefault()
}

function handleHeaderMouseDown(event: MouseEvent, coords: { row: number; col: number }) {
  const column = coords.col >= 0 ? toSheetColumnIndex(coords.col) : -1
  const dataRow = coords.row >= 0 ? getDataRowIndex(coords.row) : -1
  if (dataRow >= 0 && column >= 0 && event.button === 0 && isFormulaReferencePickMode()) {
    markFormulaReferencePointerDown()
    return
  }

  if (dataRow >= 0 && column >= 0 && (event.ctrlKey || event.metaKey)) {
    const link = getCellLinkAt(getDocumentRowIndex(dataRow), column)
    if (link) {
      event.preventDefault()
      event.stopPropagation()
      openCellLink(link)
    }
    return
  }

  clearSheetHeaderSelection()
}

function applyHeaderCellStyle(column: number, th: HTMLTableHeaderCellElement, headerLevel: number) {
  th.style.removeProperty('background-color')
  th.style.removeProperty('color')
  th.style.removeProperty('font-weight')

  if (column < 0 || headerLevel < 0) {
    return
  }

  const style = nestedHeaderStyleRows.value[headerLevel]?.[column]
  if (!style) {
    return
  }

  if (style.background_color) {
    th.style.setProperty('background-color', style.background_color, 'important')
  }
  if (style.text_color) {
    th.style.setProperty('color', style.text_color, 'important')
  }
  if (style.background_color || style.text_color) {
    th.style.setProperty('font-weight', '600')
  }
}

function applyCellMetaStyle(TD: HTMLTableCellElement, style: SheetCellStyle | null) {
  if (style?.background_color) {
    TD.style.setProperty('background-color', style.background_color, 'important')
  }
  if (style?.text_color) {
    TD.style.setProperty('color', style.text_color, 'important')
  }
  if (style?.font_family) {
    TD.style.setProperty('font-family', getCellFontFamilyStyle(style.font_family), 'important')
  }
}

function resetRenderedCellState(TD: HTMLTableCellElement) {
  TD.classList.remove(
    'htDimmed',
    'sheet-cell-has-link',
    'sheet-cell-has-action',
    'sheet-cell-formula',
    'sheet-cell-formula-error',
    'sheet-cell-formula-reference-preview',
    'sheet-freeze-column-boundary',
    'sheet-freeze-row-boundary',
    'sheet-row-marker-cell',
    'sheet-grid-header-cell',
    'sheet-grid-group-header-cell',
    'sheet-grid-field-header-cell',
    'sheet-grid-field-header-cell-filtered',
    'sheet-grid-note-header-cell',
    'sheet-grid-header-cell-selected',
  )
  TD.style.removeProperty('background-color')
  TD.style.removeProperty('color')
  TD.style.removeProperty('font-family')
  TD.style.removeProperty('font-size')
  TD.style.removeProperty('font-weight')
  TD.style.removeProperty('line-height')
  TD.style.removeProperty('text-align')
  if (TD.dataset.hyperlinkUrl) {
    delete TD.dataset.hyperlinkUrl
  }
  if (TD.dataset.sheetRowMarker) {
    delete TD.dataset.sheetRowMarker
  }
  if (TD.title) {
    TD.removeAttribute('title')
  }
  TD.onmousedown = null
}

function renderFieldHeaderCell(TD: HTMLTableCellElement, column: number) {
  const headerTitle = columnHeaders.value[column] ?? createFallbackHeader(column)
  if (editingColumnIndex.value !== column) {
    if (editingHeaderInputEl && editingHeaderInputEl.closest('td') === TD) {
      editingHeaderInputEl = null
    }
    TD.textContent = ''
    const label = document.createElement('span')
    label.className = 'sheet-header-label'

    const titleEl = document.createElement('span')
    titleEl.className = 'sheet-header-title'
    titleEl.textContent = headerTitle
    const note = getColumnNote(column)
    const headerLink = getCellLinkAt(getDocumentGridRowIndex(columnHeaderLevel.value), column) ?? getColumnHeaderLink(column)
    if (headerLink) {
      titleEl.classList.add('has-link')
      titleEl.dataset.hyperlinkUrl = headerLink.url
    }
    if (note && columnNoteDisplayMode.value === 'hover') {
      const title = headerLink ? `${note}\n${headerLink.url}` : note
      titleEl.setAttribute('aria-label', title)
      label.addEventListener('mouseenter', () => {
        openColumnNotePopover(column, label.getBoundingClientRect())
      })
      label.addEventListener('mouseleave', () => {
        closeColumnNotePopover()
      })
    } else if (headerLink) {
      titleEl.title = headerLink.url
      titleEl.setAttribute('aria-label', headerLink.url)
      TD.title = headerLink.url
    }
    label.appendChild(titleEl)
    if (isColumnFilterEnabled(column)) {
      const filterButton = document.createElement('button')
      filterButton.type = 'button'
      filterButton.className = 'sheet-header-filter-button'
      filterButton.classList.toggle('is-active', isColumnFilterActive(column))
      filterButton.title = isColumnFilterActive(column) ? '筛选已生效' : '筛选'
      filterButton.setAttribute('aria-label', `${headerTitle} 筛选`)
      filterButton.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h11L9.2 8.4v3.2l-2.4 1.1V8.4L2.5 3.5Z"/></svg>'
      filterButton.addEventListener('mousedown', (event) => {
        event.stopPropagation()
      })
      filterButton.addEventListener('click', (event) => {
        event.preventDefault()
        event.stopPropagation()
        closeColumnNotePopover()
        openColumnFilterPopover(column, filterButton.getBoundingClientRect())
      })
      label.appendChild(filterButton)
    }
    TD.appendChild(label)
    return
  }

  const currentInput = TD.querySelector('.sheet-header-rename-input') as HTMLInputElement | null
  if (currentInput) {
    currentInput.value = editingColumnTitle.value
    currentInput.style.width = getEditingColumnInputWidth(editingColumnTitle.value)
    editingHeaderInputEl = currentInput
    return
  }

  TD.textContent = ''
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
  TD.appendChild(input)
  editingHeaderInputEl = input
  focusEditingColumnInput()
}

function renderRowMarkerCell(TD: HTMLTableCellElement, row: number) {
  resetRenderedCellState(TD)
  TD.classList.add('sheet-row-marker-cell')
  TD.classList.toggle('sheet-freeze-row-boundary', isFreezeRowBoundary(row))
  TD.dataset.sheetRowMarker = String(row)
  TD.onmousedown = (event) => {
    if (event.button !== 0 && event.button !== 2) {
      return
    }
    clearSheetHeaderSelection()
    clearFormulaBarSelection()
    if (!selectRowFromMarker(row)) {
      return
    }
    if (event.button === 0) {
      event.preventDefault()
      event.stopPropagation()
    }
  }
  setRenderedCellText(TD, getSheetRowHeaderLabel(row))
}

function renderSheetHeaderGridCell(TD: HTMLTableCellElement, row: number, column: number, value: string) {
  resetRenderedCellState(TD)
  TD.classList.add('sheet-grid-header-cell')
  TD.classList.toggle('sheet-grid-header-cell-selected', isSheetHeaderCellSelected(column, row))
  applyFreezePaneBoundaryClasses(TD, row, column)
  TD.dataset.sheetHeaderLevel = String(row)
  TD.dataset.sheetHeaderColumn = String(column)

  const documentRow = getDocumentGridRowIndex(row)
  const formulaCell = isFormulaExpression(value)
    ? getFormulaCellModelAtGridRow(row, column)
    : null
  const formulaText = formulaCell?.text ?? null
  const action = getCellActionAt(documentRow, column)
  if (action) {
    if (row < normalizedHeaderGroups.value.length) {
      TD.classList.add('sheet-grid-group-header-cell')
    } else if (row === columnHeaderLevel.value) {
      TD.classList.add('sheet-grid-field-header-cell')
    } else {
      TD.classList.add('sheet-grid-note-header-cell')
    }
    TD.classList.add('sheet-cell-has-action')
    applyHeaderCellStyle(column, TD as unknown as HTMLTableHeaderCellElement, row)
    applyCellMetaStyle(TD, getCellStyleAt(documentRow, column))
    renderCellActionButton(TD, getCellActionDisplayLabel(action, value))
    const actionTitle = getCellActionTitle(action)
    if (actionTitle) {
      TD.title = actionTitle
    }
    return
  }

  if (row < normalizedHeaderGroups.value.length) {
    TD.classList.add('sheet-grid-group-header-cell')
    applyHeaderCellStyle(column, TD as unknown as HTMLTableHeaderCellElement, row)
    applyCellMetaStyle(TD, getCellStyleAt(documentRow, column))
    const link = getCellLinkAt(documentRow, column)
    if (link) {
      TD.classList.add('sheet-cell-has-link')
      TD.dataset.hyperlinkUrl = link.url
      TD.title = link.url
    }
    setRenderedCellText(TD, formulaText ?? value)
    TD.classList.toggle('sheet-cell-formula', formulaText != null)
    TD.classList.toggle('sheet-cell-formula-error', formulaText != null && (isFormulaErrorValue(formulaCell?.value) || formulaCell.text.startsWith('#')))
    if (formulaText != null) {
      TD.title = value && value !== formulaText ? `${value}\n= ${formulaText}` : formulaText
    }
    return
  }

  if (row === columnHeaderLevel.value) {
    TD.classList.add('sheet-grid-field-header-cell')
    TD.classList.toggle('sheet-grid-field-header-cell-filtered', isColumnFilterActive(column))
    applyHeaderCellStyle(column, TD as unknown as HTMLTableHeaderCellElement, row)
    applyCellMetaStyle(TD, getCellStyleAt(documentRow, column))
    renderFieldHeaderCell(TD, column)
    return
  }

  TD.classList.add('sheet-grid-note-header-cell')
  const note = value
  const renderedNote = formulaText ?? note
  applyCellMetaStyle(TD, getCellStyleAt(documentRow, column))
  const link = getCellLinkAt(documentRow, column)
  if (link) {
    TD.classList.add('sheet-cell-has-link')
    TD.dataset.hyperlinkUrl = link.url
  }
  setRenderedCellText(TD, renderedNote)
  TD.classList.toggle('sheet-cell-formula', formulaText != null)
  TD.classList.toggle('sheet-cell-formula-error', formulaText != null && (isFormulaErrorValue(formulaCell?.value) || formulaCell.text.startsWith('#')))
  if (formulaText != null) {
    TD.title = [
      note && note !== formulaText ? `${note}\n= ${formulaText}` : formulaText,
      link?.url,
    ].filter(Boolean).join('\n')
  } else if (note || link) {
    TD.title = [note, link?.url].filter(Boolean).join('\n')
  }
}

function handleAfterGetColHeader(hotColumn: number, th: HTMLTableHeaderCellElement, headerLevel: number) {
  th.classList.remove(
    'sheet-col-marker',
    'sheet-row-marker-col-header',
    'sheet-column-marker-header-selected',
    'sheet-freeze-column-boundary',
  )
  th.classList.add('sheet-col-marker')
  if (hotColumn < 0) {
    th.dataset.sheetHeaderColumn = ''
    return
  }
  if (isRowMarkerHotColumn(hotColumn)) {
    th.classList.add('sheet-row-marker-col-header')
    th.dataset.sheetHeaderColumn = ''
    return
  }
  const column = toSheetColumnIndex(hotColumn)
  th.dataset.sheetHeaderColumn = String(column)
  th.classList.toggle('sheet-column-marker-header-selected', isColumnMarkerSelected(column))
  th.classList.toggle('sheet-freeze-column-boundary', isFreezeColumnBoundary(column))
  if (column < 0) {
    return
  }
}

function handleAfterGetRowHeader(row: number, th: HTMLTableHeaderCellElement) {
  th.classList.toggle('sheet-freeze-row-boundary', isFreezeRowBoundary(row))
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
    && normalizedLeft.row_marker_origin === normalizedRight.row_marker_origin
    && normalizedLeft.show_column_markers === normalizedRight.show_column_markers
    && normalizedLeft.column_marker_style === normalizedRight.column_marker_style
    && normalizedLeft.column_note_display === normalizedRight.column_note_display
    && normalizedLeft.frozen_column_count === normalizedRight.frozen_column_count
    && normalizedLeft.pagination.enabled === normalizedRight.pagination.enabled
    && normalizedLeft.pagination.page_size === normalizedRight.pagination.page_size
  )
}

function createColumnFilterEnabledDraft(
  headers = columnHeaders.value,
  sourceConfigs: Record<string, SheetColumnConfig> = columnConfigs.value,
) {
  return Object.fromEntries(headers.map((header) => [
    header,
    normalizeColumnConfig(sourceConfigs[header]).filter_enabled,
  ]))
}

function setSheetSettingsAllColumnFilters(enabled: boolean) {
  sheetSettingsColumnFilterDraft.value = Object.fromEntries(
    columnHeaders.value.map((header) => [header, enabled]),
  )
}

function buildColumnConfigsWithFilterDraft(
  sourceConfigs: Record<string, SheetColumnConfig>,
  headers: string[],
  filterDraft: Record<string, boolean>,
) {
  const normalizedConfigs = normalizeColumnConfigs(sourceConfigs, headers)
  const nextConfigs: Record<string, SheetColumnConfig> = { ...normalizedConfigs }
  for (const header of headers) {
    const currentConfig = normalizeColumnConfig(normalizedConfigs[header])
    currentConfig.filter_enabled = filterDraft[header] === true
    const preservedConfig = pickPreservedColumnConfig(normalizedConfigs[header])
    const storedConfig = createStoredColumnConfig(currentConfig, preservedConfig)
    if (storedConfig) {
      nextConfigs[header] = storedConfig
    } else {
      delete nextConfigs[header]
    }
  }
  return normalizeColumnConfigs(nextConfigs, headers)
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
  sheetSettingsColumnFilterDraft.value = createColumnFilterEnabledDraft()
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

  const nextSettings = normalizeSheetViewSettings(sheetSettingsDraft.value, columnHeaders.value.length)
  const nextColumnConfigs = buildColumnConfigsWithFilterDraft(
    columnConfigs.value,
    columnHeaders.value,
    sheetSettingsColumnFilterDraft.value,
  )
  const currentColumnConfigs = normalizeColumnConfigs(columnConfigs.value, columnHeaders.value)
  const columnFiltersChanged = JSON.stringify(nextColumnConfigs) !== JSON.stringify(currentColumnConfigs)
  closeSheetSettings()

  if (areSheetViewSettingsEqual(sheetViewSettings.value, nextSettings) && !columnFiltersChanged) {
    return
  }

  const previousSettings = {
    ...sheetViewSettings.value,
    pagination: { ...sheetViewSettings.value.pagination },
  }
  const previousColumnConfigs = columnConfigs.value
  const previousRows = rows.value

  const paginationChanged = (
    sheetViewSettings.value.pagination.enabled !== nextSettings.pagination.enabled
    || sheetViewSettings.value.pagination.page_size !== nextSettings.pagination.page_size
  )
  const previousFormulaHeaderRowCount = getCurrentFormulaHeaderRows().length
  const nextFormulaHeaderRowCount = getFormulaHeaderRowsForDocument(
    columnHeaders.value,
    normalizedHeaderGroups.value,
    normalizeColumnConfigs(columnConfigs.value, columnHeaders.value),
    nextSettings,
  ).length
  const nextRows = remapRowsFormulaReferencesForHeaderRowCountChange(
    rows.value,
    previousFormulaHeaderRowCount,
    nextFormulaHeaderRowCount,
  )

  if (nextRows !== rows.value) {
    rows.value = nextRows
  }
  sheetViewSettings.value = nextSettings
  if (columnFiltersChanged) {
    columnConfigs.value = nextColumnConfigs
  }
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
      columnConfigs.value = previousColumnConfigs
      rows.value = previousRows
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
  clearSheetHeaderSelection()
  hasContextMenuFallbackSelection.value = false
  closeColumnNotePopover()
  closeColumnFilterPopover()
  columnSettingsDialogVisible.value = false
  columnSettingsColumnIndex.value = null
  columnSettingsSelectionBounds.value = null
  closeCellLinkDialog()
  closeCellStyleDialog()
  const normalizedHeaders = normalizeHeaders(document.columns)
  const initialRows = document.rows.length
    ? document.rows.map((row) => normalizeRow(row, normalizedHeaders))
    : [createEmptyRow(normalizedHeaders.length)]

  columnHeaders.value = normalizedHeaders
  headerGroups.value = normalizeHeaderGroups(document.header_groups, normalizedHeaders.length)
  columnConfigs.value = normalizeColumnConfigs(document.column_configs, normalizedHeaders)
  const normalizedMergedCells = normalizeMergedCells(
    document.merged_cells,
    Math.max(document.grid_rows?.length ?? 0, sheetHeaderRowCount.value + initialRows.length, getMergedCellsSourceRowCount(document.merged_cells)),
    normalizedHeaders.length,
  )
  mergedCells.value = normalizedMergedCells
  const dataStartRow = normalizeNonNegativeInt(document.data_start_row, sheetHeaderRowCount.value)
  const repairedRows = repairCoveredMergedCellValuesInDataRows(
    initialRows,
    normalizedMergedCells,
    normalizedHeaders,
    dataStartRow,
  )
  const normalizedRows = repairedRows.length ? repairedRows : [createEmptyRow(normalizedHeaders.length)]
  cellMeta.value = normalizeCellMetaMap(document.cell_meta, normalizedHeaders.length)
  sheetViewSettings.value = normalizeSheetViewSettings(document.view_settings, normalizedHeaders.length)
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
      data: sheetHotGridRows.value,
      colHeaders: sheetColumnHeaders.value,
      colWidths: [...sheetHotColumnWidths.value],
      rowHeaders: false,
      fixedRowsTop: sheetHeaderRowCount.value,
      fixedColumnsStart: fixedHotColumnsStart.value,
      mergeCells: sheetHotMergeCells.value,
      hiddenColumns: {
        columns: [...hotHiddenColumnIndexes.value],
        indicators: false,
      },
      hiddenRows: {
        rows: [...sheetFilterHiddenRows.value],
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
    workbookItems: detail.workbook_items ?? [],
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

function applyInlineSheetDocument() {
  if (!props.inlineDocument) {
    suppressPersistence = true
    resetWorkspaceState()
    sheetContentReady.value = false
    suppressPersistence = false
    return
  }

  suppressPersistence = true
  try {
    remoteAccessCapabilities.value = INLINE_READONLY_ACCESS_CAPABILITIES
    sheetTitle.value = props.inlineTitle || '未命名表格'
    sheetVersion.value = 0
    loadSheetDocument(normalizeSheetDocument(props.inlineDocument))
    applyPaginationState(null)
    sheetContentReady.value = true
    changeSerial = 0
    lastQueuedSerial = 0
    clearSaveTimer()
    void refreshAttendanceCourseScriptStatuses()
  } finally {
    suppressPersistence = false
  }
}

function findHeaderIndex(headers: string[], target: string) {
  return headers.findIndex((header) => normalizeCellValue(header).trim() === target)
}

function applyUserMatchRunSheetDetail(detail: NoteSheetDetail) {
  const remoteDocument = normalizeSheetDocument(detail.document_json)
  const remoteColumns = normalizeHeaders(remoteDocument.columns)
  const currentUserIdColumn = findHeaderIndex(columnHeaders.value, '用户ID')
  const currentScoreColumn = findHeaderIndex(columnHeaders.value, '匹配得分')
  const remoteUserIdColumn = findHeaderIndex(remoteColumns, '用户ID')
  const remoteScoreColumn = findHeaderIndex(remoteColumns, '匹配得分')

  if (
    currentUserIdColumn < 0
    || currentScoreColumn < 0
    || remoteUserIdColumn < 0
    || remoteScoreColumn < 0
  ) {
    applyRemoteSheetDetail(detail)
    return
  }

  const remoteRows = remoteDocument.rows.map((row) => normalizeRow(row, remoteColumns))
  const currentRows = rows.value.map((row) => normalizeRow(row, columnHeaders.value))
  const currentBaseOffset = effectivePaginationEnabled.value ? pageRowOffset.value : 0
  const remoteBaseOffset = detail.pagination?.row_offset ?? 0
  const remoteIsPaged = !!detail.pagination
  let changed = false

  for (let rowIndex = 0; rowIndex < currentRows.length; rowIndex += 1) {
    const remoteRowIndex = remoteIsPaged
      ? rowIndex + currentBaseOffset - remoteBaseOffset
      : rowIndex + currentBaseOffset
    const remoteRow = remoteRows[remoteRowIndex]
    if (!remoteRow) {
      continue
    }

    const nextUserId = remoteRow[remoteUserIdColumn] ?? ''
    const nextScore = remoteRow[remoteScoreColumn] ?? ''
    if (
      currentRows[rowIndex][currentUserIdColumn] !== nextUserId
      || currentRows[rowIndex][currentScoreColumn] !== nextScore
    ) {
      currentRows[rowIndex][currentUserIdColumn] = nextUserId
      currentRows[rowIndex][currentScoreColumn] = nextScore
      changed = true
    }
  }

  suppressPersistence = true
  try {
    remoteAccessCapabilities.value = detail.access?.capabilities ?? remoteAccessCapabilities.value
    sheetTitle.value = detail.title || sheetTitle.value
    sheetVersion.value = Number(detail.version || sheetVersion.value || 1)
    emitSheetSync(detail)
    if (changed) {
      rows.value = currentRows
      refreshFormulaDisplayState()
      refreshGridStructure()
      void refreshComputedRowHeights()
    }
    sheetContentReady.value = true
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
    hot.updateSettings({ data: sheetHotGridRows.value })
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

  const link = getCellLinkAt(sheetHeaderRowCount.value + cell.documentRow, cell.column)
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
    const localSettings = normalizeSheetViewSettings(localDraft.document.view_settings, localDraft.document.columns.length)
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

    applyPaginationState(remote.pagination)
    let remoteDocument = normalizeSheetDocument(remote.document_json)
    const remoteSettings = normalizeSheetViewSettings(remoteDocument.view_settings, remoteDocument.columns.length)
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
      applyPaginationState(remote.pagination)
      remoteDocument = normalizeSheetDocument(remote.document_json)
    }

    sheetTitle.value = remote.title || '未命名表格'
    sheetVersion.value = Number(remote.version || 1)
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

function normalizeSheetGridChanges(changes: unknown): SheetGridChangeRecord[] {
  if (!Array.isArray(changes)) {
    return []
  }

  return changes
    .map((change) => {
      if (!Array.isArray(change) || change.length < 4) {
        return null
      }
      const rowIndex = Number(change[0])
      const hotColumnIndex = Number(change[1])
      const columnIndex = toSheetColumnIndex(hotColumnIndex)
      if (
        !Number.isInteger(rowIndex)
        || !Number.isInteger(hotColumnIndex)
        || rowIndex < 0
        || columnIndex < 0
      ) {
        return null
      }
      return {
        rowIndex,
        hotColumnIndex,
        columnIndex,
        previousValue: change[2],
        nextValue: change[3],
      } satisfies SheetGridChangeRecord
    })
    .filter((record): record is SheetGridChangeRecord => !!record)
}

function clearCoveredMergedCellValuesInRows(nextRows: SheetRow[], merge: SheetMergedCell) {
  for (let documentRow = merge.row; documentRow < merge.row + merge.rowspan; documentRow += 1) {
    const gridRow = getCurrentGridRowIndexFromDocumentRow(documentRow)
    const dataRow = getDataRowIndex(gridRow)
    if (dataRow < 0 || dataRow >= nextRows.length) {
      continue
    }
    if (!nextRows[dataRow]) {
      nextRows[dataRow] = createEmptyRow(columnHeaders.value.length)
    }
    for (let column = merge.col; column < merge.col + merge.colspan; column += 1) {
      if (documentRow === merge.row && column === merge.col) {
        continue
      }
      if (column >= 0 && column < columnHeaders.value.length) {
        nextRows[dataRow][column] = ''
      }
    }
  }
}

function syncCoveredMergedCellChangesToAnchors(changes: unknown) {
  const records = normalizeSheetGridChanges(changes)
  if (!records.length) {
    return false
  }

  let changed = false
  const nextRows = rows.value.map((row) => normalizeRow(row, columnHeaders.value))
  records.forEach((record) => {
    const merge = findMergedCellAtGridCell(record.rowIndex, record.columnIndex)
    if (!merge || (merge.rowspan <= 1 && merge.colspan <= 1)) {
      return
    }

    const anchor = getMergeAnchorForGridCell(record.rowIndex, record.columnIndex)
    const nextValue = normalizeCellValue(record.nextValue)
    if (isSheetHeaderGridRow(anchor.row)) {
      changed = applySheetHeaderGridChange(anchor.row, anchor.column, nextValue) || changed
      return
    }

    const targetDataRow = getDataRowIndex(anchor.row)
    if (targetDataRow < 0 || anchor.column < 0 || anchor.column >= columnHeaders.value.length) {
      return
    }

    if (!nextRows[targetDataRow]) {
      nextRows[targetDataRow] = createEmptyRow(columnHeaders.value.length)
    }
    nextRows[targetDataRow][anchor.column] = normalizeCellInputValueForColumn(nextValue, anchor.column)
    clearCoveredMergedCellValuesInRows(nextRows, merge)

    const sourceDataRow = getDataRowIndex(record.rowIndex)
    if (
      sourceDataRow >= 0
      && sourceDataRow < nextRows.length
      && record.columnIndex >= 0
      && record.columnIndex < columnHeaders.value.length
      && (sourceDataRow !== targetDataRow || record.columnIndex !== anchor.column)
    ) {
      nextRows[sourceDataRow][record.columnIndex] = ''
    }
    changed = true
  })

  if (changed) {
    rows.value = nextRows
  }
  return changed
}

function handleAfterChange(_changes: unknown, source?: string) {
  if (source === 'loadData' || source === 'external-update') {
    return
  }
  finishFormulaReferenceRange()
  clearFormulaReferencePreviewRange()
  syncRowsFromGrid()
  const mergedAnchorSynced = syncCoveredMergedCellChangesToAnchors(_changes)
  refreshFormulaDisplayState()
  syncFormulaBarDraftFromSelectedCell()
  if (mergedAnchorSynced) {
    refreshGridStructure()
  } else {
    getHotInstance()?.render()
  }
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
  const sheetColumnRange = getSheetColumnRangeFromHotRange(range.startColumn, range.endColumn)
  if (!sheetColumnRange) {
    sheetInternalClipboard = null
    return
  }
  const documentRange = {
    startRow: getDocumentGridRowIndex(range.startRow),
    endRow: getDocumentGridRowIndex(range.endRow),
    startColumn: sheetColumnRange.start,
    endColumn: sheetColumnRange.end,
  }
  const visibleData = data.map((row, rowOffset) => (
    row.map((_, columnOffset) => {
      const gridRow = range.startRow + rowOffset
      const hotColumn = range.startColumn + columnOffset
      if (isRowMarkerHotColumn(hotColumn)) {
        return getSheetRowHeaderLabel(gridRow)
      }
      const column = toSheetColumnIndex(hotColumn)
      if (column < 0) {
        return ''
      }
      const anchor = getMergeAnchorForGridCell(gridRow, column)
      return anchor.row === gridRow && anchor.column === column
        ? getCellDisplayText(getDataRowIndex(anchor.row), anchor.column, getGridCellRawValue(anchor.row, anchor.column))
        : ''
    })
  ))

  if (
    range.startRow < sheetHeaderRowCount.value
    || hotRangeIncludesRowMarker(range.startColumn, range.endColumn)
    || rangePartiallyIntersectsMergedCells(documentRange)
  ) {
    sheetInternalClipboard = null
    visibleData.forEach((row, rowIndex) => {
      row.forEach((cell, columnIndex) => {
        if (Array.isArray(data[rowIndex])) {
          data[rowIndex][columnIndex] = cell
        }
      })
    })
    return
  }

  const rawData = data.map((row, rowOffset) => (
    row.map((_, columnOffset) => {
      const sourceRow = getDataRowIndex(range.startRow + rowOffset)
      const sourceColumn = sheetColumnRange.start + columnOffset
      return getRawCellValue(sourceRow, sourceColumn)
    })
  ))
  const displayData = rawData.map((row, rowOffset) => (
    row.map((rawValue, columnOffset) => {
      const sourceRow = getDataRowIndex(range.startRow + rowOffset)
      const sourceColumn = sheetColumnRange.start + columnOffset
      return getCellDisplayText(sourceRow, sourceColumn, rawValue)
    })
  ))

  sheetInternalClipboard = {
    sheetId: props.sheetId,
    sourceStartRow: getDataRowIndex(range.startRow),
    sourceStartColumn: sheetColumnRange.start,
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
  return Math.max(rows.value.length, (hot?.countSourceRows() ?? sheetHeaderRowCount.value) - sheetHeaderRowCount.value)
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
      const dataRowIndex = getDataRowIndex(rowIndex)
      if (dataRowIndex >= 0) {
        endRow = Math.max(endRow, dataRowIndex)
      }
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

    const hotColumnIndex = Number(change[1])
    const columnIndex = toSheetColumnIndex(hotColumnIndex)
    if (!Number.isInteger(hotColumnIndex) || columnIndex < 0) {
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
  const targetColumnRange = getSheetColumnRangeFromHotRange(targetRange.startColumn, targetRange.endColumn)
  if (!targetColumnRange || hotRangeIncludesRowMarker(targetRange.startColumn, targetRange.endColumn)) {
    warnReadOnlyAction()
    return false
  }
  const targetSheetRange = {
    ...targetRange,
    startColumn: targetColumnRange.start,
    endColumn: targetColumnRange.end,
  }
  if (targetRange.startRow < sheetHeaderRowCount.value) {
    warnReadOnlyAction()
    return false
  }
  const targetDocumentRange = {
    startRow: getDocumentGridRowIndex(targetRange.startRow),
    endRow: getDocumentGridRowIndex(targetRange.endRow),
    startColumn: targetSheetRange.startColumn,
    endColumn: getPasteRequiredEndColumn(data, targetSheetRange),
  }
  if (rangePartiallyIntersectsMergedCells(targetDocumentRange)) {
    ElMessage.warning('粘贴范围不能只覆盖合并单元格的一部分')
    return false
  }
  const targetDataStartRow = getDataRowIndex(targetRange.startRow)
  const targetDataRange = {
    ...targetSheetRange,
    startRow: targetDataStartRow,
    endRow: getDataRowIndex(targetRange.endRow),
  }
  if (!isColumnRangeEditable(targetSheetRange.startColumn, getPasteRequiredEndColumn(data, targetSheetRange))) {
    warnReadOnlyColumnAction()
    return false
  }
  if (!canEditData.value && getPasteRequiredEndRow(data, targetDataRange) >= getGridRowCountForExpansionGuard()) {
    warnReadOnlyAction()
    return false
  }
  if (!ensurePagedRowExpansionAllowed(getPasteRequiredEndRow(data, targetDataRange))) {
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
      const targetRow = targetDataStartRow + rowOffset
      const targetColumn = targetSheetRange.startColumn + columnOffset
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
    !Array.isArray(changes)
    || source === 'loadData'
    || source === 'external-update'
  ) {
    return
  }

  changes.forEach((change) => {
    if (!Array.isArray(change) || change.length < 4) {
      return
    }
    const rowIndex = Number(change[0])
    const hotColumnIndex = Number(change[1])
    const columnIndex = toSheetColumnIndex(hotColumnIndex)
    if (!Number.isInteger(rowIndex) || !Number.isInteger(hotColumnIndex) || rowIndex < 0 || columnIndex < 0) {
      if (Number.isInteger(rowIndex) && Number.isInteger(hotColumnIndex) && isRowMarkerHotColumn(hotColumnIndex)) {
        change[3] = change[2]
      }
      return
    }
    const anchor = getMergeAnchorForGridCell(rowIndex, columnIndex)
    if (anchor.row !== rowIndex || anchor.column !== columnIndex) {
      change[0] = anchor.row
      change[1] = toHotColumnIndex(anchor.column)
    }
  })

  const headerChanges = changes.filter((change) => (
    Array.isArray(change)
    && getDataRowIndex(Number(change[0])) < 0
  ))
  if (headerChanges.length > 0) {
    if (!canEditConfig.value) {
      warnReadOnlyAction()
      return false
    }

    let changed = false
    headerChanges.forEach((change) => {
      const rowIndex = Number(change[0])
      const hotColumnIndex = Number(change[1])
      const columnIndex = toSheetColumnIndex(hotColumnIndex)
      if (!Number.isInteger(rowIndex) || !Number.isInteger(hotColumnIndex) || columnIndex < 0) {
        return
      }
      changed = applySheetHeaderGridChange(rowIndex, columnIndex, change[3]) || changed
    })
    if (changed) {
      clearEditingColumnState()
      refreshGridStructure()
      void refreshComputedRowHeights()
    }
    return false
  }

  if (!canEditData.value && !canEditPartialData.value) {
    return false
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

    const hotColumnIndex = Number(change[1])
    const columnIndex = toSheetColumnIndex(hotColumnIndex)
    if (!Number.isInteger(hotColumnIndex) || columnIndex < 0) {
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
  const sourceBounds = normalizeAutofillRange(sourceRange)
  const sourceColumnRange = getSheetColumnRangeFromHotRange(sourceBounds.startColumn, sourceBounds.endColumn)
  const targetColumnRange = getSheetColumnRangeFromHotRange(targetBounds.startColumn, targetBounds.endColumn)
  if (
    !sourceColumnRange
    || !targetColumnRange
    || hotRangeIncludesRowMarker(sourceBounds.startColumn, sourceBounds.endColumn)
    || hotRangeIncludesRowMarker(targetBounds.startColumn, targetBounds.endColumn)
  ) {
    warnReadOnlyAction()
    return false
  }
  if (sourceBounds.startRow < sheetHeaderRowCount.value || targetBounds.startRow < sheetHeaderRowCount.value) {
    warnReadOnlyAction()
    return false
  }
  const targetDataEndRow = getDataRowIndex(targetBounds.endRow)
  if (!isColumnRangeEditable(targetColumnRange.start, targetColumnRange.end)) {
    warnReadOnlyColumnAction()
    return false
  }
  if (!canEditData.value && targetDataEndRow >= getGridRowCountForExpansionGuard()) {
    warnReadOnlyAction()
    return false
  }
  if (!ensurePagedRowExpansionAllowed(targetDataEndRow, PAGED_AUTO_ROW_INSERT_MESSAGE)) {
    return false
  }
  return buildFormulaAutofillData(
    shiftAutofillRange(sourceRange, -sheetHeaderRowCount.value, -rowMarkerColumnCount.value),
    shiftAutofillRange(targetRange, -sheetHeaderRowCount.value, -rowMarkerColumnCount.value),
    direction,
  ) ?? selectionData
}

function handleBeforeCreateRow(index = 0, amount = 1, source?: string) {
  if (!canEditData.value) {
    warnReadOnlyAction()
    return false
  }
  if (source !== 'auto') {
    return
  }

  const startRow = normalizeNonNegativeInt(getDataRowIndex(index), getGridRowCountForExpansionGuard())
  const rowAmount = Math.max(1, normalizePositivePageNumber(amount, 1))
  const requiredEndRow = Math.max(getGridRowCountForExpansionGuard(), startRow) + rowAmount - 1
  if (!ensurePagedRowExpansionAllowed(requiredEndRow, PAGED_AUTO_ROW_INSERT_MESSAGE)) {
    return false
  }
}

function handleAfterCreateRow(index = 0, amount = 1) {
  const dataIndex = Math.max(0, getDataRowIndex(index))
  const documentDataIndex = getDocumentRowIndex(dataIndex)
  const documentGridRow = sheetHeaderRowCount.value + documentDataIndex
  const insertionTemplate = pendingRowInsertionTemplate
  shiftCellMetaRows(documentGridRow, amount)
  applyRowInsertionTemplateCellStyles(documentGridRow, amount, insertionTemplate)
  insertMergedCellRows(documentGridRow, amount)
  syncRowsFromGrid()
  remapFormulaReferencesInRows((rowIndex) => (rowIndex >= documentDataIndex ? rowIndex + amount : rowIndex))
}

function handleAfterRemoveRow(index = 0, amount = 1) {
  const dataIndex = Math.max(0, getDataRowIndex(index))
  const documentDataIndex = getDocumentRowIndex(dataIndex)
  removeCellMetaRows(sheetHeaderRowCount.value + documentDataIndex, amount)
  removeMergedCellRows(sheetHeaderRowCount.value + documentDataIndex, amount)
  syncRowsFromGrid()
  const endIndex = documentDataIndex + amount
  remapFormulaReferencesInRows((rowIndex) => {
    if (rowIndex >= documentDataIndex && rowIndex < endIndex) {
      return null
    }
    return rowIndex >= endIndex ? rowIndex - amount : rowIndex
  })
}

function handleAfterCreateCol(hotIndex: number, amount: number) {
  const index = Math.max(0, toSheetColumnIndex(hotIndex))
  const insertionTemplate = pendingColumnInsertionTemplate
  const nextHeaders = [...columnHeaders.value]
  nextHeaders.splice(index, 0, ...createCustomColumnNames(amount, nextHeaders))
  columnHeaders.value = nextHeaders
  const nextWidths = [...columnWidths.value]
  nextWidths.splice(
    index,
    0,
    ...Array.from({ length: amount }, (_, offset) => (
      insertionTemplate?.targetIndex === index
        ? insertionTemplate.width
        : getAdaptiveColumnWidth(nextHeaders[index + offset] ?? createFallbackHeader(index + offset))
    )),
  )
  columnWidths.value = nextWidths
  columnConfigs.value = applyColumnInsertionTemplate(nextHeaders, index, amount, insertionTemplate)
  shiftCellMetaColumns(index, amount)
  applyColumnInsertionTemplateCellStyles(index, amount, insertionTemplate)
  insertMergedCellColumns(index, amount)
  refreshGridStructure()
  syncRowsFromGrid()
  remapFormulaReferencesInRows(undefined, (columnIndex) => (
    columnIndex >= index ? columnIndex + amount : columnIndex
  ))
}

function handleBeforeRemoveCol(hotIndex: number, amount: number) {
  if (isRowMarkerHotColumn(hotIndex)) {
    return false
  }
  return columnHeaders.value.length - amount >= 1
}

function handleAfterRemoveCol(hotIndex: number, amount: number) {
  const index = Math.max(0, toSheetColumnIndex(hotIndex))
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
  removeMergedCellColumns(index, amount)
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

  return getSheetColumnRangeFromHotRange(startCol, endCol)
}

function getSelectionRowBounds() {
  const hot = getHotInstance()
  const selection = hot?.getSelectedLast()
  if (!selection) {
    return null
  }

  const startRow = Math.min(selection[0], selection[2])
  const endRow = Math.max(selection[0], selection[2])
  const startDataRow = Math.max(getDataRowIndex(startRow), 0)
  const endDataRow = getDataRowIndex(endRow)
  if (endDataRow < 0) {
    return null
  }

  return {
    start: startDataRow,
    end: endDataRow,
  }
}

function hasSingleColumnSelection() {
  const bounds = getSelectionColumnBounds()
  return !!bounds && bounds.start === bounds.end
}

function hasColumnHeaderSelection() {
  return isSelectedByColumnHeader() && !isSelectedByCorner() && !!getSelectionColumnBounds()
}

function getColumnSettingsContextBounds() {
  if (isSelectedByColumnHeader() && !isSelectedByCorner()) {
    return getSelectionColumnBounds()
  }

  const headerCellColumn = getSelectedColumnHeaderCellIndex()
  return headerCellColumn == null
    ? null
    : { start: headerCellColumn, end: headerCellColumn }
}

function hasColumnSettingsContextSelection() {
  return !!getColumnSettingsContextBounds()
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

function getSingleFreezePaneTargetColumnIndex() {
  const selectedColumn = getSingleSelectedColumnIndex()
  if (selectedColumn != null) {
    return selectedColumn
  }
  return getSelectedColumnHeaderCellIndex()
}

function hasFreezePaneContextSelection() {
  return hasColumnHeaderSelection() || !!selectedSheetHeaderCell.value
}

function canFreezePaneAtSelection() {
  return canEditConfig.value && getSingleFreezePaneTargetColumnIndex() != null
}

function canUnfreezePanesFromSelection() {
  return canEditConfig.value && fixedColumnsStart.value > 0 && hasFreezePaneContextSelection()
}

function shouldShowFreezePaneContextMenuGroup() {
  return canFreezePaneAtSelection() || canUnfreezePanesFromSelection()
}

function setFrozenColumnCount(columnCount: number) {
  if (!ensureCanEditConfig()) {
    return
  }

  const nextCount = normalizeFrozenColumnCount(columnCount, columnHeaders.value.length)
  const currentCount = fixedColumnsStart.value
  if (nextCount === currentCount && sheetViewSettings.value.frozen_column_count === nextCount) {
    return
  }

  sheetViewSettings.value = normalizeSheetViewSettings({
    ...sheetViewSettings.value,
    frozen_column_count: nextCount,
  }, columnHeaders.value.length)
  refreshGridStructure()
  scheduleRemoteSave(0)

  if (nextCount > 0) {
    ElMessage.success(`已冻结到 ${getColumnMarkerLabel(nextCount - 1)} 列`)
  } else {
    ElMessage.success('已取消冻结窗口')
  }
}

function freezePanesAtSelectedColumn() {
  const columnIndex = getSingleFreezePaneTargetColumnIndex()
  if (columnIndex == null) {
    return
  }
  setFrozenColumnCount(columnIndex + 1)
}

function isFreezeColumnBoundary(columnIndex: number) {
  return fixedColumnsStart.value > 0 && columnIndex === fixedColumnsStart.value - 1
}

function isFreezeRowBoundary(rowIndex: number) {
  return sheetHeaderRowCount.value > 0 && rowIndex === sheetHeaderRowCount.value - 1
}

function applyFreezePaneBoundaryClasses(element: HTMLElement, rowIndex: number, columnIndex: number) {
  element.classList.toggle('sheet-freeze-column-boundary', isFreezeColumnBoundary(columnIndex))
  element.classList.toggle('sheet-freeze-row-boundary', isFreezeRowBoundary(rowIndex))
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

function clearSheetHeaderSelection() {
  if (!selectedSheetHeaderCell.value) {
    return
  }
  selectedSheetHeaderCell.value = null
  getHotInstance()?.render()
}

function isSheetHeaderCellSelected(column: number, headerLevel: number) {
  return (
    selectedSheetHeaderCell.value?.column === column
    && selectedSheetHeaderCell.value.headerLevel === headerLevel
  )
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

function getDataRowIndex(gridRowIndex: number) {
  return gridRowIndex - sheetHeaderRowCount.value
}

function getGridRowIndex(dataRowIndex: number) {
  return dataRowIndex + sheetHeaderRowCount.value
}

function getSheetCellDocumentRow(rowIndex: number) {
  return getDocumentGridRowIndex(rowIndex)
}

function isSheetHeaderGridRow(rowIndex: number) {
  return rowIndex >= 0 && rowIndex < sheetHeaderRowCount.value
}

function getSheetHeaderGridLevel(rowIndex: number) {
  if (rowIndex < 0 || rowIndex >= sheetHeaderRowCount.value) {
    return -1
  }
  return rowIndex
}

function getSheetRowHeaderLabel(gridRowIndex: number) {
  const rowIndex = getDataRowIndex(gridRowIndex)
  const numbering = sheetViewSettings.value.row_marker_numbering
  const pageOffset = effectivePaginationEnabled.value && numbering === 'global' ? pageRowOffset.value : 0

  if (sheetViewSettings.value.row_marker_origin === 'sheet_zero') {
    if (gridRowIndex < 0) {
      return ''
    }
    if (rowIndex < 0) {
      return String(gridRowIndex)
    }
    return String(pageOffset + sheetHeaderRowCount.value + rowIndex)
  }

  if (sheetViewSettings.value.row_marker_origin === 'sheet') {
    if (gridRowIndex < 0) {
      return ''
    }
    if (rowIndex < 0) {
      return String(gridRowIndex + 1)
    }
    return String(pageOffset + sheetHeaderRowCount.value + rowIndex + 1)
  }

  if (rowIndex < 0) {
    return ''
  }
  return String(pageOffset + rowIndex + 1)
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

function createFormulaBarCellFromGridCell(gridRowIndex: number, columnIndex: number): FormulaBarCell | null {
  if (
    !Number.isInteger(gridRowIndex)
    || !Number.isInteger(columnIndex)
    || gridRowIndex < 0
    || columnIndex < 0
    || columnIndex >= columnHeaders.value.length
    || gridRowIndex >= sheetGridRows.value.length
  ) {
    return null
  }

  const anchor = getMergeAnchorForGridCell(gridRowIndex, columnIndex)
  if (anchor.row < 0 || anchor.column < 0 || anchor.column >= columnHeaders.value.length) {
    return null
  }
  return {
    gridRow: anchor.row,
    dataRow: getDataRowIndex(anchor.row),
    column: anchor.column,
    documentGridRow: anchor.documentRow,
  }
}

function getFormulaBarCellRawValue(cell: FormulaBarCell) {
  return cell.dataRow >= 0
    ? getRawCellValue(cell.dataRow, cell.column)
    : getGridCellRenderSourceValue(cell.gridRow, cell.column)
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

function getFormulaBarCellEditText(cell: FormulaBarCell) {
  const rawValue = getFormulaBarCellRawValue(cell)
  return cell.dataRow >= 0 ? getCellEditText(cell.column, rawValue) : rawValue
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
  formulaBarDraft.value = getFormulaBarCellEditText(cell)
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
  return !!cell && cell.gridRow === rowIndex && cell.column === columnIndex
}

function isFormulaReferencePickMode() {
  return !!formulaBarCell.value && formulaBarFocused.value && formulaBarDraft.value.trimStart().startsWith('=')
}

function getSheetFormulaRowIndex(dataRowIndex: number) {
  return sheetHeaderRowCount.value + getDocumentRowIndex(dataRowIndex)
}

function getCellReferenceLabelForSheetRow(sheetRowIndex: number, columnIndex: number) {
  return `${getExcelColumnLabel(columnIndex)}${sheetRowIndex + 1}`
}

function getCellReferenceLabel(rowIndex: number, columnIndex: number) {
  return getCellReferenceLabelForSheetRow(getSheetFormulaRowIndex(rowIndex), columnIndex)
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

function commitPendingSheetGridEdit() {
  if (formulaBarFocused.value) {
    commitFormulaBarDraft()
  }

  const editor = getActiveOpenedEditor()
  if (!editor) {
    syncRowsFromGrid()
    return
  }

  const rowIndex = Number(editor.row)
  const columnIndex = toSheetColumnIndex(Number(editor.col))
  const draftValue = getActiveEditorDraft(editor)
  if (typeof editor.finishEditing === 'function') {
    editor.finishEditing(false)
  } else if (Number.isInteger(rowIndex) && Number.isInteger(columnIndex) && rowIndex >= 0 && columnIndex >= 0) {
    const anchor = getMergeAnchorForGridCell(rowIndex, columnIndex)
    if (isSheetHeaderGridRow(anchor.row)) {
      applySheetHeaderGridChange(anchor.row, anchor.column, draftValue)
    } else {
      const dataRow = getDataRowIndex(anchor.row)
      if (dataRow >= 0) {
        const nextRows = rows.value.map((row) => normalizeRow(row, columnHeaders.value))
        if (!nextRows[dataRow]) {
          nextRows[dataRow] = createEmptyRow(columnHeaders.value.length)
        }
        nextRows[dataRow][anchor.column] = normalizeCellInputValueForColumn(draftValue, anchor.column)
        rows.value = nextRows
      }
    }
  }
  syncRowsFromGrid()
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
    const dataRow = getDataRowIndex(target.editor.row)
    const column = toSheetColumnIndex(target.editor.col)
    return dataRow >= 0 && column >= 0 ? { row: dataRow, column } : null
  }

  const cell = formulaBarCell.value
  return cell && cell.dataRow >= 0 ? { row: cell.dataRow, column: cell.column } : null
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
      hot.getCell(getGridRowIndex(rowIndex), columnIndex)?.classList.add('sheet-cell-formula-reference-preview')
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

function insertFormulaReferenceText(reference: string) {
  const target = getFormulaReferenceInsertionTarget()
  if (!target) {
    return false
  }

  const editTarget = getFormulaReferenceEditTarget(target)
  const { selectionStart, selectionEnd } = getFormulaReferenceReplacementBounds(target)
  const nextValue = `${target.currentValue.slice(0, selectionStart)}${reference}${target.currentValue.slice(selectionEnd)}`
  const cursorPosition = selectionStart + reference.length

  setFormulaReferenceTargetValue(editTarget, nextValue)
  setFormulaReferenceReplacementSpan(editTarget, selectionStart, reference)
  focusFormulaReferenceTarget(editTarget, cursorPosition)
  return true
}

function insertFormulaReferenceIntoDraft(rowIndex: number, columnIndex: number) {
  return insertFormulaReferenceText(getCellReferenceLabel(rowIndex, columnIndex))
}

function syncFormulaBarFromInlineEditor(editor: SheetActiveEditor, value: string) {
  if (typeof editor.row !== 'number' || typeof editor.col !== 'number') {
    return
  }
  const cell = createFormulaBarCellFromGridCell(editor.row, toSheetColumnIndex(editor.col))
  if (!cell) {
    return
  }
  formulaBarCell.value = cell
  formulaBarDraft.value = value
}

function syncInlineEditorToCellEditText(editor: SheetActiveEditor) {
  if (typeof editor.row !== 'number' || typeof editor.col !== 'number') {
    return
  }

  const cell = createFormulaBarCellFromGridCell(editor.row, toSheetColumnIndex(editor.col))
  if (!cell) {
    return
  }
  const rawValue = getFormulaBarCellRawValue(cell)
  const editText = getFormulaBarCellEditText(cell)
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
  if (!cell || typeof editor.row !== 'number' || typeof editor.col !== 'number') {
    return false
  }
  const editorCell = createFormulaBarCellFromGridCell(editor.row, toSheetColumnIndex(editor.col))
  return !!editorCell && editorCell.gridRow === cell.gridRow && editorCell.column === cell.column
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

function isUndoShortcut(event: KeyboardEvent) {
  return (event.ctrlKey || event.metaKey)
    && !event.altKey
    && !event.shiftKey
    && event.key.toLowerCase() === 'z'
}

function isRedoShortcut(event: KeyboardEvent) {
  return (event.ctrlKey || event.metaKey)
    && !event.altKey
    && (
      (!event.shiftKey && event.key.toLowerCase() === 'y')
      || (event.shiftKey && event.key.toLowerCase() === 'z')
    )
}

function isTextEditingTarget(target: EventTarget | null) {
  return target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || (target instanceof HTMLElement && target.isContentEditable)
}

function isFormulaBarDraftSyncedWithCell() {
  const cell = formulaBarCell.value
  if (!cell) {
    return false
  }
  return formulaBarDraft.value === getFormulaBarCellEditText(cell)
}

function canRouteFormulaBarUndoRedo(event: KeyboardEvent) {
  return event.target instanceof HTMLElement
    && !!event.target.closest('.sheet-formula-input')
    && isFormulaBarDraftSyncedWithCell()
}

function handleUndoRedoShortcut(event: KeyboardEvent, options: { allowSyncedFormulaBar?: boolean } = {}) {
  const wantsUndo = isUndoShortcut(event)
  const wantsRedo = isRedoShortcut(event)
  if (!wantsUndo && !wantsRedo) {
    return false
  }

  if (getActiveOpenedEditor()) {
    return false
  }

  const routeFormulaBarShortcut = options.allowSyncedFormulaBar && canRouteFormulaBarUndoRedo(event)
  if (isTextEditingTarget(event.target) && !routeFormulaBarShortcut) {
    return false
  }

  const undoRedoPlugin = getUndoRedoPlugin()
  if (wantsUndo) {
    if (!undoRedoPlugin?.isUndoAvailable?.() || !undoRedoPlugin.undo) {
      return false
    }
    event.preventDefault()
    undoRedoPlugin.undo()
    return true
  }

  if (!undoRedoPlugin?.isRedoAvailable?.() || !undoRedoPlugin.redo) {
    return false
  }
  event.preventDefault()
  undoRedoPlugin.redo()
  return true
}

function handleBeforeKeyDown(event: KeyboardEvent) {
  if (handleUndoRedoShortcut(event)) {
    return false
  }
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
  if (handleUndoRedoShortcut(event, { allowSyncedFormulaBar: true })) {
    return
  }
  if (handleFormulaReferenceArrowKey(event)) {
    return
  }
  if (isFormulaReferenceEditingKey(event)) {
    clearFormulaReferenceReplacementSpan()
  }
}

function insertFormulaReferenceIntoActiveEditor(rowIndex: number, columnIndex: number) {
  return insertFormulaReferenceText(getCellReferenceLabel(rowIndex, columnIndex))
}

function commitFormulaBarDraft() {
  const cell = formulaBarCell.value
  if (!cell) {
    return
  }

  if (cell.dataRow < 0) {
    if (!canEditConfig.value) {
      syncFormulaBarDraftFromSelectedCell(true)
      clearFormulaReferenceReplacementSpan()
      clearFormulaReferencePreviewRange()
      return
    }

    const nextValue = normalizeCellValue(formulaBarDraft.value)
    if (nextValue === getFormulaBarCellRawValue(cell)) {
      formulaBarDraft.value = nextValue
      clearFormulaReferenceReplacementSpan()
      clearFormulaReferencePreviewRange()
      return
    }

    if (applySheetHeaderGridChange(cell.gridRow, cell.column, nextValue)) {
      clearEditingColumnState()
      refreshGridStructure()
      refreshFormulaDisplayState()
      void refreshComputedRowHeights()
    }
    syncFormulaBarDraftFromSelectedCell(true)
    clearFormulaReferenceReplacementSpan()
    clearFormulaReferencePreviewRange()
    getHotInstance()?.render()
    return
  }

  if (!canEditDataColumn(cell.column)) {
    syncFormulaBarDraftFromSelectedCell(true)
    clearFormulaReferenceReplacementSpan()
    clearFormulaReferencePreviewRange()
    return
  }

  const nextValue = normalizeCellInputValueForColumn(formulaBarDraft.value, cell.column)
  if (nextValue === getRawCellValue(cell.dataRow, cell.column)) {
    formulaBarDraft.value = getCellEditText(cell.column, nextValue)
    clearFormulaReferenceReplacementSpan()
    clearFormulaReferencePreviewRange()
    return
  }

  const hot = getHotInstance()
  if (hot) {
    hot.setDataAtCell(cell.gridRow, toHotColumnIndex(cell.column), nextValue, 'formula-bar')
  } else {
    const nextRows = rows.value.map((row) => normalizeRow(row, columnHeaders.value))
    if (!nextRows[cell.dataRow]) {
      nextRows[cell.dataRow] = createEmptyRow(columnHeaders.value.length)
    }
    nextRows[cell.dataRow][cell.column] = nextValue
    rows.value = nextRows
  }

  syncRowsFromGrid()
  syncFormulaBarDraftFromSelectedCell(true)
  clearFormulaReferenceReplacementSpan()
  clearFormulaReferencePreviewRange()
  getHotInstance()?.render()
  void refreshComputedRowHeights()
}

function setFormulaBarGridCell(gridRowIndex: number, columnIndex: number) {
  const nextCell = createFormulaBarCellFromGridCell(gridRowIndex, columnIndex)
  if (!nextCell) {
    clearFormulaBarSelection()
    return
  }

  if (formulaBarFocused.value && !isSameFormulaBarCell(nextCell.gridRow, nextCell.column)) {
    commitFormulaBarDraft()
  }

  formulaBarCell.value = nextCell
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

function handleAfterSelection(row: number, hotColumn: number) {
  refreshContextMenuFallbackSelectionState()
  const column = hotColumn >= 0 ? toSheetColumnIndex(hotColumn) : hotColumn
  if (hotColumn >= 0 && column < 0) {
    clearSheetHeaderSelection()
    clearFormulaBarSelection()
    syncColumnMarkerSelection()
    return
  }
  if (isSheetHeaderGridRow(row) && column >= 0) {
    const anchor = getMergeAnchorForGridCell(row, column)
    const headerLevel = getSheetHeaderGridLevel(anchor.row)
    if (!isSheetHeaderCellSelected(anchor.column, headerLevel)) {
      selectedSheetHeaderCell.value = { column: anchor.column, headerLevel }
      getHotInstance()?.render()
    }
    setFormulaBarGridCell(anchor.row, anchor.column)
    syncColumnMarkerSelection()
    return
  }
  clearSheetHeaderSelection()
  if (formulaReferenceRangeState) {
    syncColumnMarkerSelection()
    return
  }

  const dataRow = row >= 0 ? getDataRowIndex(row) : -1
  if (formulaReferencePointerDown && dataRow >= 0 && column >= 0 && isFormulaReferencePickMode()) {
    clearFormulaReferencePointerDownReset()
    formulaReferencePointerDown = false
    const anchor = getMergeAnchorForGridCell(row, column)
    insertFormulaReferenceIntoDraft(getDataRowIndex(anchor.row), anchor.column)
    syncColumnMarkerSelection()
    return
  }

  clearFormulaReferencePointerDownReset()
  formulaReferencePointerDown = false
  clearFormulaReferencePreviewRange()
  if (row >= 0 && column >= 0) {
    const anchor = getMergeAnchorForGridCell(row, column)
    setFormulaBarGridCell(anchor.row, anchor.column)
  } else {
    clearFormulaBarSelection()
  }
  syncColumnMarkerSelection()
}

function handleAfterDeselect() {
  clearFormulaBarSelection()
  clearColumnMarkerSelection()
  clearSheetHeaderSelection()
  hasContextMenuFallbackSelection.value = false
}

function getSingleSelectedDataCell() {
  const selection = getHotInstance()?.getSelectedLast()
  if (!selection) {
    return null
  }

  const startRow = Math.min(selection[0], selection[2])
  const endRow = Math.max(selection[0], selection[2])
  const columnRange = getSheetColumnRangeFromHotRange(selection[1], selection[3])
  if (!columnRange) {
    return null
  }
  const startColumn = columnRange.start
  const endColumn = columnRange.end
  const anchor = getMergeAnchorForGridCell(startRow, startColumn)
  const dataRow = getDataRowIndex(anchor.row)
  if (
    dataRow < 0
    || startColumn < 0
    || startRow !== endRow
    || startColumn !== endColumn
  ) {
    return null
  }

  return {
    row: dataRow,
    column: anchor.column,
    documentRow: getDocumentRowIndex(dataRow),
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

    const startRow = Math.max(getDataRowIndex(Math.min(selection[0], selection[2])), 0)
    const endRow = Math.min(getDataRowIndex(Math.max(selection[0], selection[2])), rows.value.length - 1)
    const columnRange = getSheetColumnRangeFromHotRange(selection[1], selection[3])
    if (!columnRange) {
      continue
    }
    const startColumn = columnRange.start
    const endColumn = columnRange.end
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

function getSingleSelectedSheetCell() {
  const cells = getSelectedSheetCells()
  return cells.length === 1 ? cells[0] : null
}

function getSelectedSheetCells(): SelectedSheetCell[] {
  const hot = getHotInstance()
  const selections = hot?.getSelected?.() as number[][] | undefined
  if (!selections?.length || sheetGridRows.value.length <= 0 || columnHeaders.value.length <= 0) {
    return []
  }

  const cells: SelectedSheetCell[] = []
  const seen = new Set<string>()
  for (const selection of selections) {
    if (!Array.isArray(selection) || selection.length < 4) {
      continue
    }

    const startRow = Math.max(Math.min(selection[0], selection[2]), 0)
    const endRow = Math.min(Math.max(selection[0], selection[2]), sheetGridRows.value.length - 1)
    const columnRange = getSheetColumnRangeFromHotRange(selection[1], selection[3])
    if (!columnRange) {
      continue
    }
    const startColumn = columnRange.start
    const endColumn = columnRange.end
    if (startRow > endRow || startColumn > endColumn) {
      continue
    }

    for (let row = startRow; row <= endRow; row += 1) {
      for (let column = startColumn; column <= endColumn; column += 1) {
        const anchor = getMergeAnchorForGridCell(row, column)
        const key = createCellMetaKey(anchor.documentRow, anchor.column)
        if (seen.has(key)) {
          continue
        }
        seen.add(key)
        cells.push({ row: anchor.row, column: anchor.column, documentRow: anchor.documentRow })
      }
    }
  }

  return normalizeSheetCellsToMergeAnchors(cells)
}

function hasSingleDataCellSelection() {
  return !!getSingleSelectedDataCell()
}

function hasDataCellSelection() {
  return getSelectedDataCells().length > 0
}

function hasSheetCellSelection() {
  return getSelectedSheetCells().length > 0
}

function hasSingleSheetCellSelection() {
  return !!getSingleSelectedSheetCell()
}

function getSelectedGridCellBounds() {
  const selection = getHotInstance()?.getSelectedLast()
  if (!selection) {
    return null
  }
  const startRow = Math.min(selection[0], selection[2])
  const endRow = Math.max(selection[0], selection[2])
  const columnRange = getSheetColumnRangeFromHotRange(selection[1], selection[3])
  if (endRow < 0 || !columnRange) {
    return null
  }
  return {
    startRow: Math.max(startRow, 0),
    endRow: Math.min(endRow, sheetGridRows.value.length - 1),
    startColumn: columnRange.start,
    endColumn: columnRange.end,
  }
}

function getSelectedDocumentCellBounds() {
  const bounds = getSelectedGridCellBounds()
  if (!bounds || bounds.startRow > bounds.endRow || bounds.startColumn > bounds.endColumn) {
    return null
  }
  return {
    startRow: getDocumentGridRowIndex(bounds.startRow),
    endRow: getDocumentGridRowIndex(bounds.endRow),
    startColumn: bounds.startColumn,
    endColumn: bounds.endColumn,
  }
}

function doMergedCellsIntersectRange(
  cell: SheetMergedCell,
  range: { startRow: number; endRow: number; startColumn: number; endColumn: number },
) {
  return (
    cell.row <= range.endRow
    && cell.row + cell.rowspan - 1 >= range.startRow
    && cell.col <= range.endColumn
    && cell.col + cell.colspan - 1 >= range.startColumn
  )
}

function selectionCrossesDataStart(range: { startRow: number; endRow: number }) {
  return range.startRow < sheetHeaderRowCount.value && range.endRow >= sheetHeaderRowCount.value
}

function getMergedCellsDocumentRowCount() {
  return Math.max(sheetHeaderRowCount.value + totalRowCount.value, sheetHeaderRowCount.value + rows.value.length, sheetGridRows.value.length)
}

function canMergeSelectedCells() {
  if (isWholeRowSelection()) {
    return false
  }

  const range = getSelectedDocumentCellBounds()
  if (!range) {
    return false
  }
  if (range.startRow === range.endRow && range.startColumn === range.endColumn) {
    return false
  }
  if (selectionCrossesDataStart(range)) {
    return false
  }
  return !normalizeMergedCells(
    mergedCells.value,
    getMergedCellsDocumentRowCount(),
    columnHeaders.value.length,
  ).some((cell) => doMergedCellsIntersectRange(cell, range))
}

function rangeFullyContainsMergedCell(
  range: { startRow: number; endRow: number; startColumn: number; endColumn: number },
  cell: SheetMergedCell,
) {
  return (
    range.startRow <= cell.row
    && range.endRow >= cell.row + cell.rowspan - 1
    && range.startColumn <= cell.col
    && range.endColumn >= cell.col + cell.colspan - 1
  )
}

function rangePartiallyIntersectsMergedCells(range: { startRow: number; endRow: number; startColumn: number; endColumn: number }) {
  return normalizeCurrentMergedCells().some((cell) => (
    doMergedCellsIntersectRange(cell, range)
    && !rangeFullyContainsMergedCell(range, cell)
  ))
}

function mergeSelectedCells() {
  if (!ensureCanEditConfig() || !canMergeSelectedCells()) {
    return
  }
  commitPendingSheetGridEdit()
  const range = getSelectedDocumentCellBounds()
  if (!range) {
    return
  }
  const nextCell: SheetMergedCell = {
    row: range.startRow,
    col: range.startColumn,
    rowspan: range.endRow - range.startRow + 1,
    colspan: range.endColumn - range.startColumn + 1,
  }
  const anchorSynced = syncMergedCellAnchorFromVisibleGrid(nextCell)
  mergedCells.value = normalizeMergedCells(
    [...mergedCells.value, nextCell],
    getMergedCellsDocumentRowCount(),
    columnHeaders.value.length,
  )
  refreshGridStructure()
  if (anchorSynced) {
    refreshFormulaDisplayState()
  }
  scheduleRemoteSave(0)
}

function getSelectedMergedCells() {
  const range = getSelectedDocumentCellBounds()
  if (!range) {
    return []
  }
  return normalizeMergedCells(
    mergedCells.value,
    getMergedCellsDocumentRowCount(),
    columnHeaders.value.length,
  ).filter((cell) => doMergedCellsIntersectRange(cell, range))
}

function hasSelectedMergedCell() {
  return getSelectedMergedCells().length > 0
}

function setSheetCellRawValueAtDocumentCell(documentRow: number, columnIndex: number, value: string) {
  const gridRow = getCurrentGridRowIndexFromDocumentRow(documentRow)
  if (gridRow < 0 || columnIndex < 0 || columnIndex >= columnHeaders.value.length) {
    return false
  }

  if (gridRow < sheetHeaderRowCount.value) {
    return applySheetHeaderGridChange(gridRow, columnIndex, value)
  }

  const dataRow = getDataRowIndex(gridRow)
  if (dataRow < 0 || dataRow >= rows.value.length) {
    return false
  }

  const nextRows = rows.value.map((row) => normalizeRow(row, columnHeaders.value))
  if (!nextRows[dataRow]) {
    nextRows[dataRow] = createEmptyRow(columnHeaders.value.length)
  }
  nextRows[dataRow][columnIndex] = normalizeCellInputValueForColumn(value, columnIndex)
  rows.value = nextRows
  return true
}

function syncMergedCellAnchorFromVisibleGrid(cell: SheetMergedCell) {
  const gridRow = getCurrentGridRowIndexFromDocumentRow(cell.row)
  if (gridRow < 0 || cell.col < 0 || cell.col >= columnHeaders.value.length) {
    return false
  }

  const visibleValue = (
    getLiveGridCellRawValue(gridRow, cell.col)
    || getGridCellRawValue(gridRow, cell.col)
    || getGridCellRenderSourceValue(gridRow, cell.col)
    || inferBlankHeaderMergeLabel(sheetGridRows.value, columnHeaders.value, cell, columnHeaderLevel.value)
  )
  if (!visibleValue) {
    return false
  }

  return setSheetCellRawValueAtDocumentCell(cell.row, cell.col, visibleValue)
}

function unmergeSelectedCells() {
  if (!ensureCanEditConfig()) {
    return
  }
  commitPendingSheetGridEdit()
  const selected = getSelectedMergedCells()
  if (!selected.length) {
    return
  }
  let anchorSynced = false
  selected.forEach((cell) => {
    anchorSynced = syncMergedCellAnchorFromVisibleGrid(cell) || anchorSynced
  })
  const selectedKeys = new Set(selected.map((cell) => `${cell.row}:${cell.col}`))
  mergedCells.value = normalizeMergedCells(
    mergedCells.value.filter((cell) => !selectedKeys.has(`${cell.row}:${cell.col}`)),
    getMergedCellsDocumentRowCount(),
    columnHeaders.value.length,
  )
  refreshGridStructure()
  scheduleRemoteSave(0)
  if (anchorSynced) {
    refreshFormulaDisplayState()
  }
}

function getCellMetaAt(documentRow: number, columnIndex: number) {
  return cellMeta.value[createCellMetaKey(documentRow, columnIndex)] ?? null
}

function getCellLinkAt(documentRow: number, columnIndex: number) {
  return getCellMetaAt(documentRow, columnIndex)?.link ?? null
}

function getCellActionAt(documentRow: number, columnIndex: number) {
  return getCellMetaAt(documentRow, columnIndex)?.action ?? null
}

function getCellStyleAt(documentRow: number, columnIndex: number) {
  return getCellMetaAt(documentRow, columnIndex)?.style ?? null
}

function updateCellMetaEntry(
  documentRow: number,
  columnIndex: number,
  updater: (entry: SheetCellMeta) => SheetCellMeta,
) {
  const anchor = getMergeAnchorForDocumentCell(documentRow, columnIndex)
  const targetRow = anchor.documentRow
  const targetColumn = anchor.column
  const nextMeta = { ...cellMeta.value }
  const key = createCellMetaKey(targetRow, targetColumn)
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

function setColumnHeaderLink(columnIndex: number, url: string) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return
  }

  const normalizedLink = normalizeCellLink({ url })
  const nextConfigs = { ...columnConfigs.value }
  const nextConfig: SheetColumnConfig = { ...(nextConfigs[header] ?? {}) }
  if (normalizedLink) {
    nextConfig.header_link = normalizedLink
  } else {
    delete nextConfig.header_link
  }
  nextConfigs[header] = nextConfig
  columnConfigs.value = normalizeColumnConfigs(nextConfigs, columnHeaders.value)
  refreshGridStructure()
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
  cells: SelectedSheetCell[],
  updater: (entry: SheetCellMeta, cell: SelectedSheetCell) => SheetCellMeta,
) {
  const anchorCells = normalizeSheetCellsToMergeAnchors(cells)
  if (!anchorCells.length) {
    return
  }

  const nextMeta = { ...cellMeta.value }
  anchorCells.forEach((cell) => {
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

function getCommonSelectedCellStyle(cells: SelectedSheetCell[], field: SheetCellStyleField) {
  if (!cells.length) {
    return ''
  }

  const firstValue = getCellStyleAt(cells[0].documentRow, cells[0].column)?.[field] ?? ''
  const hasMixedValue = cells.some((cell) => (
    (getCellStyleAt(cell.documentRow, cell.column)?.[field] ?? '') !== firstValue
  ))
  return hasMixedValue ? '' : firstValue
}

function cloneCellStyle(style: SheetCellStyle | null | undefined) {
  return normalizeCellStyle(style ? { ...style } : null)
}

function getBaseCellStyleForCopy(cell: SelectedSheetCell) {
  const headerStyle = cell.row >= 0 && cell.row < nestedHeaderStyleRows.value.length
    ? nestedHeaderStyleRows.value[cell.row]?.[cell.column] ?? null
    : null
  const explicitStyle = getCellStyleAt(cell.documentRow, cell.column)

  if (!headerStyle) {
    const header = columnHeaders.value[cell.column]
    const columnFontFamily = header
      ? getColumnFontFamilyFromConfig(columnConfigs.value[header])
      : 'default'
    const inheritedStyle: SheetCellStyle | null = columnFontFamily === 'default'
      ? null
      : { font_family: columnFontFamily }
    return normalizeCellStyle({
      ...(inheritedStyle ?? {}),
      ...(explicitStyle ?? {}),
    })
  }
  return normalizeCellStyle({
    ...headerStyle,
    ...(explicitStyle ?? {}),
  })
}

function hasCopiedCellFormat() {
  return copiedCellFormat.value !== null
}

function copySelectedCellFormat() {
  const cell = getSingleSelectedSheetCell()
  if (!cell) {
    return
  }

  copiedCellFormat.value = {
    style: getBaseCellStyleForCopy(cell),
  }
  ElMessage.success('已复制格式')
}

function pasteCellFormatToSelectedCells() {
  if (!ensureCanEditConfig()) {
    return
  }

  const format = copiedCellFormat.value
  if (!format) {
    ElMessage.warning('请先复制格式')
    return
  }

  const cells = getSelectedSheetCells()
  if (!cells.length) {
    return
  }

  const copiedStyle = cloneCellStyle(format.style)
  updateCellMetaEntries(cells, (entry) => {
    const nextEntry = { ...entry }
    if (copiedStyle) {
      nextEntry.style = { ...copiedStyle }
    } else {
      delete nextEntry.style
    }
    return nextEntry
  })
  scheduleRemoteSave(0)
  ElMessage.success(cells.length > 1 ? '已粘贴格式到选区' : '已粘贴格式')
}

function getCellStyleDraftModelValue(field: SheetCellColorField) {
  return cellStyleDraft.value[field] || (
    field === 'text_color' ? DEFAULT_CELL_TEXT_COLOR : DEFAULT_CELL_BACKGROUND_COLOR
  )
}

function getCellStyleDraftSwatchStyle(field: SheetCellColorField) {
  const color = normalizeCssColor(cellStyleDraft.value[field])
  return color
    ? { backgroundColor: color }
    : { backgroundImage: 'linear-gradient(135deg, transparent 0 46%, #dcdfe6 46% 54%, transparent 54% 100%)' }
}

function setCellStyleDraftColor(field: SheetCellColorField, value: string) {
  cellStyleDraftTouched.value[field] = true
  cellStyleDraft.value = {
    ...cellStyleDraft.value,
    [field]: normalizeCssColor(value),
  }
}

function clearCellStyleDraftColor(field: SheetCellColorField) {
  setCellStyleDraftColor(field, '')
}

function handleCellStyleColorPopoverVisibleChange(field: SheetCellColorField, visible: boolean) {
  activeCellStyleColorField.value = visible ? field : null
}

function setCellStyleDraftFontFamily(value: unknown) {
  cellStyleDraftTouched.value.font_family = true
  cellStyleDraft.value = {
    ...cellStyleDraft.value,
    font_family: normalizeCellFontFamily(value),
  }
}

function applyCellStyleToSelectedCells(cells: SelectedSheetCell[]) {
  const touched = cellStyleDraftTouched.value
  if (!touched.background_color && !touched.text_color && !touched.font_family) {
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
    if (touched.font_family) {
      const fontFamily = normalizeCellFontFamily(cellStyleDraft.value.font_family)
      if (fontFamily) {
        nextStyle.font_family = fontFamily
      } else {
        delete nextStyle.font_family
      }
    }

    if (nextStyle.background_color || nextStyle.text_color || nextStyle.font_family) {
      nextEntry.style = nextStyle
    } else {
      delete nextEntry.style
    }
    return nextEntry
  })
}

function openSelectedCellStyleDialog() {
  if (!ensureCanEditConfig()) {
    return
  }
  const cells = getSelectedSheetCells()
  if (!cells.length) {
    return
  }

  cellStyleDialogCells.value = cells
  activeCellStyleColorField.value = null
  cellStyleDraft.value = {
    background_color: getCommonSelectedCellStyle(cells, 'background_color'),
    text_color: getCommonSelectedCellStyle(cells, 'text_color'),
    font_family: normalizeCellFontFamily(getCommonSelectedCellStyle(cells, 'font_family')),
  }
  cellStyleDraftTouched.value = {
    background_color: false,
    text_color: false,
    font_family: false,
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
    font_family: '',
  }
  cellStyleDraftTouched.value = {
    background_color: false,
    text_color: false,
    font_family: false,
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

function getSelectedColumnHeaderCellIndex() {
  const headerCell = selectedSheetHeaderCell.value
  if (!headerCell || headerCell.headerLevel !== columnHeaderLevel.value) {
    return null
  }
  if (headerCell.column < 0 || headerCell.column >= columnHeaders.value.length) {
    return null
  }
  return headerCell.column
}

function hasSingleLinkTargetSelection() {
  return !!getSingleSelectedSheetCell()
}

function getSelectedLinkTargetLink() {
  const cell = getSingleSelectedSheetCell()
  return cell ? getCellLinkAt(cell.documentRow, cell.column) : null
}

function hasSelectedCellLink() {
  return !!getSelectedLinkTargetLink()
}

function shouldShowStyleContextMenuGroup() {
  return (
    hasSingleSheetCellSelection()
    || (hasSheetCellSelection() && canEditConfig.value)
    || (hasSheetCellSelection() && canEditConfig.value && hasCopiedCellFormat())
    || (hasSheetCellSelection() && canEditConfig.value && canMergeSelectedCells())
    || (hasSelectedMergedCell() && canEditConfig.value)
  )
}

function shouldShowLinkContextMenuGroup() {
  return (
    hasSelectedCellLink()
    || (hasSingleLinkTargetSelection() && canEditConfig.value)
  )
}

function openSelectedCellLink() {
  const cell = getSingleSelectedSheetCell()
  if (!cell) {
    return
  }
  openCellLink(getCellLinkAt(cell.documentRow, cell.column))
}

function openSelectedCellLinkDialog() {
  if (!ensureCanEditConfig()) {
    return
  }
  const cell = getSingleSelectedSheetCell()
  if (!cell) {
    return
  }

  cellLinkDialogTarget.value = {
    kind: 'cell',
    row: cell.documentRow,
    column: cell.column,
  }
  cellLinkDraftUrl.value = getCellLinkAt(cell.documentRow, cell.column)?.url ?? ''
  cellLinkDialogVisible.value = true
}

function closeCellLinkDialog() {
  cellLinkDialogVisible.value = false
  cellLinkDialogTarget.value = null
  cellLinkDraftUrl.value = ''
}

function applyCellLinkDialog() {
  if (!ensureCanEditConfig()) {
    closeCellLinkDialog()
    return
  }
  const target = cellLinkDialogTarget.value
  if (!target) {
    closeCellLinkDialog()
    return
  }

  const rawUrl = normalizeCellValue(cellLinkDraftUrl.value).trim()
  const normalizedUrl = normalizeHyperlinkUrl(rawUrl)
  if (!normalizedUrl) {
    if (rawUrl) {
      ElMessage.warning('请输入有效的链接地址')
      return
    }

    if (target.kind === 'cell') {
      setCellLink(target.row, target.column, '')
    } else {
      setColumnHeaderLink(target.column, '')
    }
    closeCellLinkDialog()
    return
  }

  if (target.kind === 'cell') {
    setCellLink(target.row, target.column, normalizedUrl)
  } else {
    setColumnHeaderLink(target.column, normalizedUrl)
  }
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

function isColumnValueValidByConfig(value: unknown, config: ColumnSettingsDraft) {
  const text = normalizeCellValue(value)
  if (!text) {
    return config.allow_empty
  }

  switch (config.value_type) {
    case 'number':
      return (typeof value === 'number' && Number.isFinite(value))
        || /^[-+]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(text)
    case 'percent':
      return parsePercentDisplayNumber(value) != null
    case 'date':
      return !!parseDateDisplayValue(value)
    default:
      if (config.text_rule === 'phone') {
        return /^\d{11}$/.test(text)
      }
      if (config.text_rule === 'id_card') {
        return /^\d{17}[\dXx]$/.test(text)
      }
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
  getHotInstance()?.updateSettings({ data: sheetHotGridRows.value })
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

function getCurrentColumnSettingsSelectionDrafts() {
  const indexes = getColumnSettingsIndexesFromBounds(columnSettingsSelectionBounds.value)
  return indexes.map((index) => getColumnSettingsDraftForColumn(index))
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

function getColumnSettingsDraftBaseType() {
  return getColumnBaseType(columnSettingsDraft.value)
}

function getColumnSettingsDraftSubType() {
  return getColumnSubType(columnSettingsDraft.value)
}

function isColumnSettingsBaseTypeMixed() {
  if (!isColumnSettingsMultiSelection.value || columnSettingsTouched.value.value_type) {
    return false
  }
  const drafts = getCurrentColumnSettingsSelectionDrafts()
  if (drafts.length <= 1) {
    return false
  }
  const firstBaseType = getColumnBaseType(drafts[0])
  return drafts.some((draft) => getColumnBaseType(draft) !== firstBaseType)
}

function isColumnSettingsSubTypeMixed() {
  if (
    !isColumnSettingsMultiSelection.value
    || columnSettingsTouched.value.value_type
    || columnSettingsTouched.value.text_rule
  ) {
    return false
  }
  const drafts = getCurrentColumnSettingsSelectionDrafts()
  if (drafts.length <= 1) {
    return false
  }
  const firstSubType = getColumnSubType(drafts[0])
  return drafts.some((draft) => getColumnSubType(draft) !== firstSubType)
}

function getColumnSettingsBaseTypeSelectModel() {
  return isColumnSettingsBaseTypeMixed() ? '' : getColumnSettingsDraftBaseType()
}

function getColumnSettingsSubTypeSelectModel() {
  return isColumnSettingsSubTypeMixed() ? '' : getColumnSettingsDraftSubType()
}

function getColumnSettingsSubTypeOptions() {
  return COLUMN_SUB_TYPE_OPTIONS[getColumnSettingsDraftBaseType()]
}

function getColumnSettingTextModel(key: 'display_format') {
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

function getColumnSettingFilterEnabledModel() {
  return isColumnSettingMixed('filter_enabled')
    ? ''
    : (columnSettingsDraft.value.filter_enabled ? 'enabled' : 'disabled')
}

function setColumnSettingsValueType(value: unknown) {
  columnSettingsDraft.value.value_type = normalizeColumnValueType(value)
  handleColumnSettingsValueTypeChange(columnSettingsDraft.value.value_type)
}

function setColumnSettingsBaseType(value: unknown) {
  const baseType = normalizeColumnBaseType(value)
  applyColumnSettingsSubType(getDefaultColumnSubType(baseType), baseType)
}

function setColumnSettingsSubType(value: unknown) {
  const currentBaseType = getColumnSettingsDraftBaseType()
  const nextSubType = normalizeColumnSubType(value, currentBaseType)
  applyColumnSettingsSubType(nextSubType, currentBaseType)
}

function applyColumnSettingsSubType(nextSubType: ColumnSubType, baseType: ColumnBaseType) {
  if (getColumnSubTypeBaseType(nextSubType) !== baseType) {
    return
  }

  if (nextSubType === 'multi_text') {
    columnSettingsDraft.value.value_type = 'multi_text'
    columnSettingsDraft.value.text_rule = 'none'
  } else if (nextSubType === 'phone' || nextSubType === 'id_card') {
    columnSettingsDraft.value.value_type = 'text'
    columnSettingsDraft.value.text_rule = nextSubType === 'phone' ? 'phone' : 'id_card'
  } else if (nextSubType === 'number' || nextSubType === 'percent' || nextSubType === 'date') {
    columnSettingsDraft.value.value_type = nextSubType
    columnSettingsDraft.value.text_rule = 'none'
  } else {
    columnSettingsDraft.value.value_type = 'text'
    columnSettingsDraft.value.text_rule = 'none'
  }

  handleColumnSettingsValueTypeChange(columnSettingsDraft.value.value_type)
  markColumnSettingTouched('text_rule')
}

function setColumnSettingsTextRule(value: unknown) {
  columnSettingsDraft.value.text_rule = normalizeColumnTextRule(value, columnSettingsDraft.value.value_type)
  markColumnSettingTouched('text_rule')
}

function setColumnSettingsValueMode(value: unknown) {
  columnSettingsDraft.value.value_mode = normalizeColumnValueMode(value)
  markColumnSettingTouched('value_mode')
}

function setColumnSettingsFilterEnabled(value: unknown) {
  columnSettingsDraft.value.filter_enabled = value === 'enabled' || value === true
  markColumnSettingTouched('filter_enabled')
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

function setColumnSettingsFontFamily(value: unknown) {
  columnSettingsDraft.value.font_family = normalizeColumnFontFamily(value)
  markColumnSettingTouched('font_family')
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
  if (currentRawConfig.header_link) {
    preservedConfig.header_link = currentRawConfig.header_link
  }
  if (currentRawConfig.note) {
    preservedConfig.note = normalizeColumnNote(currentRawConfig.note)
  }
  return preservedConfig
}

function createStoredColumnConfig(
  nextConfig: ColumnSettingsDraft,
  preservedConfig: SheetColumnConfig,
) {
  if (
    nextConfig.value_type === 'text'
    && nextConfig.text_rule === 'none'
    && nextConfig.value_mode === 'free'
    && !nextConfig.filter_enabled
    && isDefaultColumnDisplayFormat(nextConfig.value_type, nextConfig.display_format)
    && nextConfig.allow_empty
    && nextConfig.display_mode === DEFAULT_COLUMN_DISPLAY_MODE
    && nextConfig.align === DEFAULT_COLUMN_TEXT_ALIGN
    && nextConfig.trim_whitespace
    && !nextConfig.duplicate_value_highlight
    && nextConfig.hash_color_mode === 'none'
    && nextConfig.width_mode === 'adaptive'
    && nextConfig.font_family === 'default'
    && nextConfig.font_size === DEFAULT_COLUMN_FONT_SIZE
    && Object.keys(preservedConfig).length === 0
  ) {
    return null
  }

  const storedConfig: SheetColumnConfig = { ...preservedConfig }
  if (nextConfig.value_type !== 'text') {
    storedConfig.value_type = nextConfig.value_type
  }
  if (nextConfig.value_type === 'text' && nextConfig.text_rule !== 'none') {
    storedConfig.text_rule = nextConfig.text_rule
  }
  if (nextConfig.value_mode !== 'free') {
    storedConfig.value_mode = nextConfig.value_mode
  }
  if (nextConfig.filter_enabled) {
    storedConfig.filter_enabled = true
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
  if (nextConfig.font_family !== 'default') {
    storedConfig.font_family = nextConfig.font_family
  }
  if (nextConfig.font_size !== DEFAULT_COLUMN_FONT_SIZE) {
    storedConfig.font_size = nextConfig.font_size
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

function getColumnHeaderLink(columnIndex: number) {
  const header = columnHeaders.value[columnIndex]
  if (!header) {
    return null
  }
  return normalizeCellLink(columnConfigs.value[header]?.header_link)
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

  const bounds = getColumnSettingsContextBounds()
  if (!bounds) {
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
  if (value !== 'text') {
    columnSettingsDraft.value.text_rule = 'none'
    markColumnSettingTouched('text_rule')
  }
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
  const previousFormulaHeaderRowCount = getCurrentFormulaHeaderRows().length
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
        || touched.font_family
        || touched.font_size
        || touched.display_format
        || touched.value_type
        || touched.align
      )
      && (
        nextConfig.width_mode !== currentConfig.width_mode
        || nextConfig.font_family !== currentConfig.font_family
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

  const nextRows = configChanged
    ? remapRowsFormulaReferencesForHeaderRowCountChange(
      rows.value,
      previousFormulaHeaderRowCount,
      getFormulaHeaderRowsForDocument(
        columnHeaders.value,
        normalizedHeaderGroups.value,
        normalizeColumnConfigs(nextNormalizedConfigs, columnHeaders.value),
        sheetViewSettings.value,
      ).length,
    )
    : rows.value

  if (configChanged) {
    columnConfigs.value = nextNormalizedConfigs
  }
  if (nextRows !== rows.value) {
    rows.value = nextRows
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

function getSingleSelectedRowDetailIndex() {
  if ((!isSelectedByRowHeader() && !isSelectedByRowMarkerSelection()) || isSelectedByCorner()) {
    return null
  }
  const bounds = getSelectionRowBounds()
  if (!bounds || bounds.start !== bounds.end) {
    return null
  }
  return bounds.start >= 0 && bounds.start < rows.value.length ? bounds.start : null
}

function canOpenSelectedRowDetailDialog() {
  return getSingleSelectedRowDetailIndex() != null
}

function buildRowDetail(rowIndex: number): SheetRowDetail | null {
  if (rowIndex < 0 || rowIndex >= rows.value.length) {
    return null
  }

  const row = normalizeRow(rows.value[rowIndex], columnHeaders.value)
  const gridRow = getGridRowIndex(rowIndex)
  const documentGridRow = getDocumentGridRowIndex(gridRow)
  const rowLabel = getSheetRowHeaderLabel(gridRow)
  const items = columnHeaders.value
    .map((header, columnIndex): SheetRowDetailItem | null => {
      if (columnConfigs.value[header]?.hidden === true) {
        return null
      }

      const rawValue = row[columnIndex] ?? ''
      const link = getCellLinkAt(documentGridRow, columnIndex)
      const action = getCellActionAt(documentGridRow, columnIndex)
      const displayValue = action
        ? getCellActionDisplayLabel(action, normalizeCellValue(rawValue))
        : getCellDisplayText(rowIndex, columnIndex, rawValue)
      const linkDisplayValue = link ? (displayValue || link.title || link.url) : displayValue
      const value = normalizeCellValue(linkDisplayValue)
      return {
        columnIndex,
        marker: getColumnMarkerLabel(columnIndex),
        label: header || createFallbackHeader(columnIndex),
        value,
        empty: !value.trim(),
        link,
      }
    })
    .filter((item): item is SheetRowDetailItem => !!item)

  return { rowIndex, rowLabel, items }
}

function openSelectedRowDetailDialog() {
  const rowIndex = getSingleSelectedRowDetailIndex()
  if (rowIndex == null) {
    return
  }
  const detail = buildRowDetail(rowIndex)
  if (!detail) {
    return
  }
  rowDetail.value = detail
  rowDetailDialogVisible.value = true
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

function cloneColumnConfigForInsertedColumn(source: SheetColumnConfig | undefined) {
  if (!source) {
    return null
  }

  const config = { ...source }
  delete config.hidden
  delete config.restore_index
  delete config.note
  delete config.header_link
  return Object.keys(config).length ? { ...config } : null
}

function createColumnInsertionTemplate(templateColumnIndex: number, targetIndex: number): ColumnInsertionTemplate | null {
  const header = columnHeaders.value[templateColumnIndex]
  if (!header) {
    return null
  }

  const normalizedConfigs = normalizeColumnConfigs(columnConfigs.value, columnHeaders.value)
  const config = cloneColumnConfigForInsertedColumn(normalizedConfigs[header])
  const width = normalizeColumnWidthValue(columnWidths.value[templateColumnIndex] ?? getEffectiveColumnWidth(templateColumnIndex))
  const cellStyles = Object.entries(cellMeta.value)
    .map(([key, meta]) => {
      const position = parseCellMetaKey(key)
      if (!position || position.column !== templateColumnIndex) {
        return null
      }

      const style = normalizeCellStyle(meta.style)
      return style ? { row: position.row, style } : null
    })
    .filter((item): item is ColumnInsertionTemplate['cellStyles'][number] => !!item)

  return { targetIndex, width, config, cellStyles }
}

function applyColumnInsertionTemplate(
  headers: string[],
  index: number,
  amount: number,
  template: ColumnInsertionTemplate | null,
) {
  const normalizedConfigs = normalizeColumnConfigs(columnConfigs.value, headers)
  if (!template || template.targetIndex !== index) {
    return normalizedConfigs
  }

  for (let offset = 0; offset < amount; offset += 1) {
    const header = headers[index + offset]
    if (header && template.config) {
      normalizedConfigs[header] = { ...template.config }
    }
  }
  return normalizeColumnConfigs(normalizedConfigs, headers)
}

function applyColumnInsertionTemplateCellStyles(index: number, amount: number, template: ColumnInsertionTemplate | null) {
  if (!template || template.targetIndex !== index || !template.cellStyles.length) {
    return
  }

  const nextMeta = { ...cellMeta.value }
  for (const cell of template.cellStyles) {
    for (let offset = 0; offset < amount; offset += 1) {
      const key = createCellMetaKey(cell.row, index + offset)
      const entry = normalizeCellMetaEntry({ ...(nextMeta[key] ?? {}), style: { ...cell.style } })
      if (entry) {
        nextMeta[key] = entry
      } else {
        delete nextMeta[key]
      }
    }
  }
  cellMeta.value = normalizeCellMetaMap(nextMeta, columnHeaders.value.length)
}

function getDocumentGridRowIndexForDataRow(dataRowIndex: number) {
  return sheetHeaderRowCount.value + getDocumentRowIndex(dataRowIndex)
}

function createRowInsertionTemplate(templateDataIndex: number, targetDataIndex: number): RowInsertionTemplate | null {
  if (templateDataIndex < 0 || templateDataIndex >= rows.value.length) {
    return null
  }

  const sourceDocumentRow = getDocumentGridRowIndexForDataRow(templateDataIndex)
  const targetDocumentRow = getDocumentGridRowIndexForDataRow(targetDataIndex)
  const cellStyles = Object.entries(cellMeta.value)
    .map(([key, meta]) => {
      const position = parseCellMetaKey(key)
      if (!position || position.row !== sourceDocumentRow || position.column >= columnHeaders.value.length) {
        return null
      }

      const style = normalizeCellStyle(meta.style)
      return style ? { column: position.column, style } : null
    })
    .filter((item): item is RowInsertionTemplate['cellStyles'][number] => !!item)

  return { targetDocumentRow, cellStyles }
}

function applyRowInsertionTemplateCellStyles(startDocumentRow: number, amount: number, template: RowInsertionTemplate | null) {
  if (!template || template.targetDocumentRow !== startDocumentRow || !template.cellStyles.length) {
    return
  }

  const nextMeta = { ...cellMeta.value }
  for (let offset = 0; offset < amount; offset += 1) {
    for (const cell of template.cellStyles) {
      const key = createCellMetaKey(startDocumentRow + offset, cell.column)
      const entry = normalizeCellMetaEntry({ ...(nextMeta[key] ?? {}), style: { ...cell.style } })
      if (entry) {
        nextMeta[key] = entry
      } else {
        delete nextMeta[key]
      }
    }
  }
  cellMeta.value = normalizeCellMetaMap(nextMeta, columnHeaders.value.length)
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
  const templateColumnIndex = bounds
    ? (side === 'left' ? bounds.start : bounds.end)
    : columnHeaders.value.length - 1

  pendingColumnInsertionTemplate = createColumnInsertionTemplate(templateColumnIndex, targetIndex)
  try {
    hot.alter('insert_col_start', toHotColumnIndex(targetIndex), 1)
  } finally {
    pendingColumnInsertionTemplate = null
  }
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
  const targetDataIndex = bounds
    ? (side === 'above' ? bounds.start : bounds.end + 1)
    : rows.value.length
  const templateDataIndex = bounds
    ? (side === 'above' ? bounds.start : bounds.end)
    : rows.value.length - 1

  pendingRowInsertionTemplate = createRowInsertionTemplate(templateDataIndex, targetDataIndex)
  try {
    hot.alter('insert_row_above', getGridRowIndex(targetDataIndex), 1)
  } finally {
    pendingRowInsertionTemplate = null
  }
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

  hot.alter('remove_col', toHotColumnIndex(bounds.start), bounds.end - bounds.start + 1)
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

  hot.alter('remove_row', getGridRowIndex(bounds.start), bounds.end - bounds.start + 1)
}

function resolveCellMeta(row: number, col: number) {
  if (isRowMarkerHotColumn(col)) {
    return {
      wordWrap: false,
      textEllipsis: false,
      readOnly: true,
      className: 'sheet-row-marker-cell',
    }
  }

  const column = toSheetColumnIndex(col)
  if (column < 0) {
    return {
      wordWrap: true,
      textEllipsis: false,
      readOnly: true,
    }
  }

  const action = row >= 0
    ? getCellActionAt(getDocumentGridRowIndex(row), column)
    : null
  if (isSheetHeaderGridRow(row)) {
    return {
      wordWrap: true,
      textEllipsis: false,
      readOnly: !!action || !canEditConfig.value,
      className: row === columnNoteHeaderLevel.value ? 'sheet-grid-note-header-cell' : 'sheet-grid-header-cell',
    }
  }

  if (getColumnDisplayMode(column) === 'single_line') {
    return {
      wordWrap: false,
      textEllipsis: true,
      readOnly: !!action || !canEditDataColumn(column),
    }
  }

  return {
    wordWrap: true,
    textEllipsis: false,
    readOnly: !!action || !canEditDataColumn(column),
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
  hotColumn: number,
  _prop: string | number,
  value: string,
) {
  if (row >= 0 && isRowMarkerHotColumn(hotColumn)) {
    renderRowMarkerCell(TD, row)
    return
  }

  const column = toSheetColumnIndex(hotColumn)
  if (column < 0) {
    resetRenderedCellState(TD)
    return
  }

  if (row >= 0 && column >= 0 && isSheetHeaderGridRow(row)) {
    const anchor = getMergeAnchorForGridCell(row, column)
    renderSheetHeaderGridCell(TD, anchor.row, anchor.column, getGridCellRenderSourceValue(anchor.row, anchor.column))
    return
  }

  const anchor = row >= 0 && column >= 0
    ? getMergeAnchorForGridCell(row, column)
    : { row, column, documentRow: -1 }
  const renderColumn = anchor.column
  const dataRow = anchor.row >= 0 ? getDataRowIndex(anchor.row) : -1
  resetRenderedCellState(TD)
  applyFreezePaneBoundaryClasses(TD, anchor.row, renderColumn)
  const documentRow = dataRow >= 0 ? anchor.documentRow : -1
  const rawText = dataRow >= 0 && renderColumn >= 0
    ? getGridCellRawValue(anchor.row, renderColumn)
    : normalizeCellValue(value)
  const header = renderColumn >= 0 ? columnHeaders.value[renderColumn] ?? '' : ''
  const columnConfig = header ? columnConfigs.value[header] : undefined
  const link = documentRow >= 0 && renderColumn >= 0
    ? getCellLinkAt(documentRow, renderColumn)
    : null
  const action = documentRow >= 0 && renderColumn >= 0
    ? getCellActionAt(documentRow, renderColumn)
    : null
  const linkDisplayText = rawText.trim() ? '' : (link?.title || link?.url || '')
  const formulaCell = dataRow >= 0 && renderColumn >= 0 && isFormulaExpression(rawText)
    ? getFormulaCellModel(dataRow, renderColumn)
    : null
  const formulaText = formulaCell?.text ?? null
  let renderedText = rawText
  if (action) {
    renderedText = getCellActionDisplayLabel(action, rawText)
  } else if (formulaCell) {
    renderedText = formulaCell.text
  } else if (linkDisplayText) {
    renderedText = linkDisplayText
  } else if (dataRow >= 0 && renderColumn >= 0) {
    renderedText = formatCellDisplayValueCached(rawText, columnConfig)
  }

  if (action) {
    renderCellActionButton(TD, renderedText)
  } else if (formulaText != null || renderedText !== rawText) {
    setRenderedCellText(TD, renderedText)
  }

  const hasFormula = formulaText != null
  const hasFormulaError = hasFormula && (isFormulaErrorValue(formulaCell?.value) || formulaCell.text.startsWith('#'))
  const isFormulaReferencePreview = dataRow >= 0 && renderColumn >= 0 && isCellInFormulaReferencePreview(dataRow, renderColumn)

  const cellStyle = documentRow >= 0 && renderColumn >= 0
    ? getCellStyleAt(documentRow, renderColumn)
    : null
  let backgroundColor = ''
  let textColor = ''
  let fontFamilyStyle = ''
  let fontSizeStyle = ''
  let lineHeightStyle = ''
  let textAlignStyle = ''

  if (renderColumn >= 0) {
    backgroundColor = getPluginRowBackgroundColor(dataRow)
    fontFamilyStyle = getColumnFontFamilyStyle(columnConfig)
    textAlignStyle = resolveColumnTextAlign(columnConfig)
    const fontSize = getColumnFontSizeFromConfig(columnConfig)
    if (fontSize !== DEFAULT_COLUMN_FONT_SIZE) {
      fontSizeStyle = `${fontSize}px`
      lineHeightStyle = `${getColumnLineHeightFromFontSize(fontSize)}px`
    }

    const hashColorStyle = getCellHashColorStyle(renderColumn, columnConfig, renderedText)
    if (hashColorStyle?.backgroundColor) {
      backgroundColor = hashColorStyle.backgroundColor
    }
    if (hashColorStyle?.color) {
      textColor = hashColorStyle.color
    }

    const accentStyle = getCellAccentStyle(dataRow, renderColumn)
    if (accentStyle) {
      backgroundColor = accentStyle.backgroundColor
    }

    if (cellStyle?.background_color) {
      backgroundColor = cellStyle.background_color
    }
    if (cellStyle?.text_color) {
      textColor = cellStyle.text_color
    }
    if (cellStyle?.font_family) {
      fontFamilyStyle = getCellFontFamilyStyle(cellStyle.font_family)
    }
  }

  let title = ''

  const hasLink = !!link
  TD.classList.toggle('sheet-cell-has-link', hasLink)
  TD.classList.toggle('sheet-cell-has-action', !!action)
  TD.classList.toggle('sheet-cell-formula', hasFormula)
  TD.classList.toggle('sheet-cell-formula-error', hasFormulaError)
  TD.classList.toggle('sheet-cell-formula-reference-preview', isFormulaReferencePreview)
  TD.style.backgroundColor = backgroundColor
  TD.style.color = textColor
  TD.style.fontFamily = fontFamilyStyle
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
  if (action) {
    const actionTitle = getCellActionTitle(action)
    if (actionTitle) {
      title = title ? `${title}\n${actionTitle}` : actionTitle
    }
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

function handleAfterColumnResize(newSize: number, hotColumn: number, isDoubleClick = false) {
  if (!canEditConfig.value) {
    return
  }
  const column = toSheetColumnIndex(hotColumn)
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
  [rows, columnHeaders, headerGroups, columnConfigs, sheetViewSettings],
  () => {
    refreshFormulaDisplayState()
  },
  { deep: true, flush: 'post' },
)

watch(
  () => {
    const cell = formulaBarCell.value
    return cell ? getFormulaBarCellRawValue(cell) : ''
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
    clearUserMatchRunPollTimer()
    userMatchRunStatus.value = null
    userMatchStartPending.value = false
    clearSaveTimer()
    if (!nextSheetId) {
      if (hasInlineDocument.value) {
        applyInlineSheetDocument()
      } else {
        suppressPersistence = true
        resetWorkspaceState()
        currentPage.value = 1
        pageCount.value = 1
        totalRowCount.value = 0
        pageRowOffset.value = 0
        pageLoadedRowCount.value = 0
        suppressPersistence = false
      }
      void updateSheetViewportHeight()
      return
    }
    if (nextSheetId === previousSheetId) {
      return
    }
    columnFilters.value = {}
    closeColumnFilterPopover()
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
  [() => props.inlineDocument, () => props.inlineTitle],
  () => {
    if (props.sheetId != null) {
      return
    }
    applyInlineSheetDocument()
    void updateSheetViewportHeight()
  },
  { deep: true },
)

watch(
  () => props.workbookId,
  (nextWorkbookId, previousWorkbookId) => {
    if (nextWorkbookId === previousWorkbookId || props.sheetId == null) {
      return
    }
    clearUserMatchRunPollTimer()
    userMatchRunStatus.value = null
    userMatchStartPending.value = false
    clearSaveTimer()
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

watch(
  [columnHeaders, columnConfigs],
  () => {
    pruneColumnFilters()
  },
  { deep: true },
)

watch(
  sheetFilterHiddenRows,
  (hiddenRows) => {
    const hot = getHotInstance()
    if (!hot) {
      return
    }
    hot.updateSettings({
      hiddenRows: {
        rows: [...hiddenRows],
        indicators: false,
      },
    })
    hot.render()
    void refreshComputedRowHeights()
    void updateSheetViewportHeight()
  },
  { deep: true },
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
  } else if (hasInlineDocument.value) {
    applyInlineSheetDocument()
    void updateSheetViewportHeight()
  } else {
    void updateSheetViewportHeight()
  }
})

onBeforeUnmount(() => {
  clearSaveTimer()
  clearUserMatchRunPollTimer()
  finishFormulaReferenceRange()
  clearScheduledFormulaBarDraftSync()
  clearScheduledInlineEditorFormulaBarSync()
  clearScheduledRowMarkerSelection()
  clearFormulaReferencePointerDownReset()
  window.removeEventListener('resize', handleWindowResize)
  window.removeEventListener('mousedown', handleGlobalMouseDown)
  document.removeEventListener('input', handleInlineEditorInput, true)
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

    <div
      v-if="sheetId == null || shouldRenderSheetContent"
      ref="sheetFrameRef"
      class="sheet-frame"
      :class="{
        'is-empty': !shouldRenderSheetContent,
        'has-frozen-columns': fixedColumnsStart > 0,
        'has-frozen-rows': sheetHeaderRowCount > 0,
      }"
      @contextmenu.capture="openSheetContextMenuAt"
    >
      <HotTable
        v-if="shouldRenderSheetContent"
        ref="hotTableRef"
        :data="sheetHotGridRows"
        :language="'zh-CN'"
        :col-headers="sheetColumnHeaders"
        :col-widths="sheetHotColumnWidths"
        :row-headers="false"
        :fixed-rows-top="sheetHeaderRowCount"
        :fixed-columns-start="fixedHotColumnsStart"
        :merge-cells="sheetHotMergeCells"
        :hidden-columns="{ columns: hotHiddenColumnIndexes, indicators: false }"
        :hidden-rows="{ rows: sheetFilterHiddenRows, indicators: false }"
        :manual-column-resize="canEditConfig"
        :manual-column-move="canEditConfig"
        :manual-row-resize="true"
        :manual-row-move="canEditData"
        :copy-paste="true"
        :undo="true"
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
        :after-get-row-header="handleAfterGetRowHeader"
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
      v-model="rowDetailDialogVisible"
      :title="rowDetailDialogTitle"
      width="560px"
      destroy-on-close
      append-to-body
    >
      <div v-if="rowDetail" class="sheet-row-detail-list">
        <div
          v-for="item in rowDetail.items"
          :key="item.columnIndex"
          class="sheet-row-detail-item"
          :class="{ 'is-empty': item.empty }"
        >
          <div class="sheet-row-detail-key">
            <span class="sheet-row-detail-marker">{{ item.marker }}</span>
            <span class="sheet-row-detail-label">{{ item.label }}</span>
          </div>
          <div class="sheet-row-detail-value">
            <a
              v-if="item.link"
              :href="item.link.url"
              target="_blank"
              rel="noreferrer"
            >{{ item.value || item.link.url }}</a>
            <span v-else>{{ item.value || '--' }}</span>
          </div>
        </div>
      </div>
    </el-dialog>

    <el-dialog
      v-model="excelImportDialogVisible"
      title="导入 Excel"
      width="460px"
      destroy-on-close
      append-to-body
      :close-on-click-modal="!excelImportRunning"
      :close-on-press-escape="!excelImportRunning"
      :show-close="!excelImportRunning"
    >
      <div class="sheet-settings-form">
        <div class="sheet-settings-field">
          <div class="sheet-settings-label">Excel 文件</div>
          <input
            ref="excelImportFileInputRef"
            class="sheet-excel-file-input"
            type="file"
            accept=".xlsx,.xlsm,.xltx,.xltm"
            :disabled="excelImportRunning"
            @change="handleExcelImportFileChange"
          >
          <div v-if="excelImportFile" class="sheet-excel-file-name">{{ excelImportFile.name }}</div>
        </div>
        <div class="sheet-settings-field">
          <div class="sheet-settings-label">补充说明</div>
          <el-input
            v-model="excelImportInstruction"
            type="textarea"
            :rows="4"
            :disabled="excelImportRunning"
            placeholder="例如：只导入报名学员；日志批阅师资不要导入"
          />
        </div>
        <div class="sheet-excel-import-hint">
          会保留当前导入按钮所在的操作行，并重置其后的数据行。
        </div>
      </div>
      <template #footer>
        <div class="sheet-settings-footer">
          <el-button :disabled="excelImportRunning" @click="() => closeExcelImportDialog()">取消</el-button>
          <el-button
            type="primary"
            :loading="excelImportRunning"
            :disabled="!excelImportFile"
            @click="applyExcelImportReset"
          >
            导入并重置
          </el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="userMatchDialogVisible"
      title="更新用户匹配"
      width="420px"
      destroy-on-close
      append-to-body
    >
      <div class="sheet-settings-form">
        <div class="sheet-settings-field">
          <el-checkbox
            v-model="userMatchUseBrowserFallback"
            :disabled="userMatchStartPending || activeUserMatchRun"
          >
            未命中时回查小鹅通
          </el-checkbox>
          <div class="sheet-excel-import-hint">
            默认只查本地用户库。勾选后会调用 codepc_mi15 打开小鹅通用户列表逐行兜底，耗时更长。
          </div>
        </div>
        <div v-if="userMatchRunSummary" class="sheet-action-run-status">
          {{ userMatchRunSummary }}
        </div>
      </div>
      <template #footer>
        <div class="sheet-settings-footer">
          <el-button :disabled="userMatchStartPending" @click="() => closeUserMatchDialog()">
            {{ activeUserMatchRun ? '关闭' : '取消' }}
          </el-button>
          <el-button
            v-if="activeUserMatchRun"
            type="danger"
            plain
            :loading="userMatchStartPending"
            @click="() => startUserMatchRun(true)"
          >
            强制重启
          </el-button>
          <el-button
            v-else
            type="primary"
            :loading="userMatchStartPending"
            @click="() => startUserMatchRun(false)"
          >
            开始匹配
          </el-button>
        </div>
      </template>
    </el-dialog>

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
            <el-option label="数字（全局 101 / 102 / 103）" value="global_numbers" />
            <el-option label="数字（每页 1 / 2 / 3）" value="page_numbers" />
          </el-select>
        </div>
        <div v-if="sheetSettingsDraft.show_row_numbers" class="sheet-settings-inline-field">
          <div class="sheet-settings-label">行号起点</div>
          <el-select v-model="sheetSettingsDraft.row_marker_origin" class="sheet-settings-inline-select">
            <el-option label="数据首行=1" value="data" />
            <el-option label="表头首行=0" value="sheet_zero" />
            <el-option label="表头首行=1" value="sheet" />
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
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">字段备注</div>
          <el-select v-model="sheetSettingsDraft.column_note_display" class="sheet-settings-inline-select">
            <el-option label="悬停展示" value="hover" />
            <el-option label="备注行展示" value="row" />
          </el-select>
        </div>
        <div class="sheet-settings-inline-field">
          <div class="sheet-settings-label">字段筛选</div>
          <div class="sheet-settings-filter-inline">
            <span class="sheet-settings-filter-summary">{{ sheetSettingsColumnFilterSummary }}</span>
            <el-button size="small" @click="() => setSheetSettingsAllColumnFilters(true)">全部开启</el-button>
            <el-button size="small" @click="() => setSheetSettingsAllColumnFilters(false)">全部关闭</el-button>
          </div>
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
        <div class="sheet-settings-section">
          <div class="sheet-settings-section-title">基础规则</div>
          <div class="sheet-settings-inline-field">
            <div class="sheet-settings-label-with-state">
              <span class="sheet-settings-label">主类型</span>
              <span v-if="isColumnSettingsBaseTypeMixed()" class="sheet-settings-mixed-badge">多个值</span>
            </div>
            <el-select
              :model-value="getColumnSettingsBaseTypeSelectModel()"
              class="sheet-settings-inline-select"
              placeholder="多个值，选择后批量覆盖"
              @change="setColumnSettingsBaseType"
            >
              <el-option
                v-for="option in COLUMN_BASE_TYPE_OPTIONS"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </div>
          <div v-if="!isColumnSettingsBaseTypeMixed()" class="sheet-settings-inline-field">
            <div class="sheet-settings-label-with-state">
              <span class="sheet-settings-label">子类型</span>
              <span v-if="isColumnSettingsSubTypeMixed()" class="sheet-settings-mixed-badge">多个值</span>
            </div>
            <el-select
              :model-value="getColumnSettingsSubTypeSelectModel()"
              class="sheet-settings-inline-select"
              placeholder="多个值，选择后批量覆盖"
              @change="setColumnSettingsSubType"
            >
              <el-option
                v-for="option in getColumnSettingsSubTypeOptions()"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </div>
          <div class="sheet-settings-inline-field">
            <div class="sheet-settings-label-with-state">
              <span class="sheet-settings-label">值模式</span>
              <span v-if="isColumnSettingMixed('value_mode')" class="sheet-settings-mixed-badge">多个值</span>
            </div>
            <el-select
              :model-value="getColumnSettingSelectModel('value_mode')"
              class="sheet-settings-inline-select"
              placeholder="多个值，选择后批量覆盖"
              @change="setColumnSettingsValueMode"
            >
              <el-option
                v-for="option in COLUMN_VALUE_MODE_OPTIONS"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
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
        </div>
        <div class="sheet-settings-section">
          <div class="sheet-settings-section-title">字段功能</div>
          <div class="sheet-settings-inline-field">
            <div class="sheet-settings-label-with-state">
              <span class="sheet-settings-label">筛选</span>
              <span v-if="isColumnSettingMixed('filter_enabled')" class="sheet-settings-mixed-badge">多个值</span>
            </div>
            <el-select
              :model-value="getColumnSettingFilterEnabledModel()"
              class="sheet-settings-inline-select"
              placeholder="多个值，选择后批量覆盖"
              @change="setColumnSettingsFilterEnabled"
            >
              <el-option
                v-for="option in COLUMN_FILTER_ENABLED_OPTIONS"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </div>
        </div>
        <div class="sheet-settings-section">
          <div class="sheet-settings-section-title">内容格式</div>
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
              <span class="sheet-settings-label">内容字体</span>
              <span v-if="isColumnSettingMixed('font_family')" class="sheet-settings-mixed-badge">多个值</span>
            </div>
            <el-select
              :model-value="getColumnSettingSelectModel('font_family')"
              class="sheet-settings-inline-select"
              placeholder="多个值，选择后批量覆盖"
              @change="setColumnSettingsFontFamily"
            >
              <el-option
                v-for="option in COLUMN_FONT_FAMILY_OPTIONS"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
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
        </div>
        <div class="sheet-settings-section">
          <div class="sheet-settings-section-title">列布局</div>
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
        </div>
        <div class="sheet-settings-section">
          <div class="sheet-settings-section-title">数据处理</div>
          <el-checkbox
            :model-value="getColumnSettingCheckboxModel('allow_empty')"
            :indeterminate="isColumnSettingMixed('allow_empty')"
            @change="value => setColumnSettingsBooleanValue('allow_empty', value)"
          >
            允许空值
            <span v-if="isColumnSettingMixed('allow_empty')" class="sheet-settings-mixed-badge">多个值</span>
          </el-checkbox>
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
          <div class="sheet-settings-label">字体</div>
          <el-select
            :model-value="cellStyleDraft.font_family"
            class="sheet-settings-inline-select"
            @change="setCellStyleDraftFontFamily"
          >
            <el-option
              v-for="option in CELL_FONT_FAMILY_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </div>
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
          <el-button @click="clearCellStyleDialog">清除格式</el-button>
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
        v-if="columnFilterPopover.visible"
        ref="columnFilterPopoverRef"
        class="sheet-column-filter-popover"
        :style="columnFilterPopoverStyle"
        @mousedown.stop
      >
        <div class="sheet-column-filter-popover-title">{{ columnFilterPopover.header }}</div>
        <el-input
          v-model="columnFilterPopover.draftQuery"
          size="small"
          clearable
          placeholder="包含关键字"
          @keyup.enter="applyColumnFilterPopover"
        />
        <template v-if="columnFilterPopoverIsOptionMode">
          <div class="sheet-column-filter-option-toolbar">
            <button type="button" @click="selectAllColumnFilterOptions">
              全选({{ columnFilterPopoverOptionRowCount }})
            </button>
            <span>|</span>
            <button type="button" @click="invertColumnFilterOptions">反选</button>
            <span>|</span>
            <button type="button" @click="selectColumnFilterDuplicateOptions">重复项</button>
            <span>|</span>
            <button type="button" @click="selectColumnFilterUniqueOptions">唯一项</button>
          </div>
          <div v-if="columnFilterPopoverOptions.length" class="sheet-column-filter-option-list">
            <label
              v-for="option in columnFilterPopoverOptions"
              :key="`option:${option.value}`"
              class="sheet-column-filter-option-row"
            >
              <input
                type="checkbox"
                :checked="option.selected"
                @change="event => handleColumnFilterOptionChange(option.value, event)"
              >
              <span class="sheet-column-filter-option-label" :title="option.label">{{ option.label }}</span>
              <span class="sheet-column-filter-option-count">({{ option.count }})</span>
            </label>
          </div>
          <div v-else class="sheet-column-filter-option-empty">暂无选项</div>
        </template>
        <div class="sheet-column-filter-popover-actions">
          <el-button size="small" @click="clearColumnFilterPopover">清除</el-button>
          <el-button type="primary" size="small" @click="applyColumnFilterPopover">筛选</el-button>
        </div>
      </div>
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

.sheet-settings-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sheet-settings-section + .sheet-settings-section {
  padding-top: 14px;
  border-top: 1px solid #f0e6d8;
}

.sheet-settings-section-title {
  color: #9a7b4f;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.2;
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

.sheet-excel-file-input {
  width: 100%;
  font-size: 13px;
  color: #374151;
}

.sheet-excel-file-name {
  font-size: 12px;
  color: #6b7280;
  word-break: break-all;
}

.sheet-excel-import-hint {
  font-size: 12px;
  line-height: 1.6;
  color: #8b7355;
}

.sheet-action-run-status {
  padding: 8px 10px;
  border: 1px solid #d7e6fb;
  border-radius: 6px;
  background: #f4f8ff;
  color: #24558f;
  font-size: 12px;
  line-height: 1.5;
}

.sheet-row-detail-list {
  max-height: min(68vh, 680px);
  overflow: auto;
  border-top: 1px solid #ebe2d4;
}

.sheet-row-detail-item {
  display: grid;
  grid-template-columns: minmax(128px, 190px) minmax(0, 1fr);
  min-height: 38px;
  border-bottom: 1px solid #ebe2d4;
}

.sheet-row-detail-key {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  padding: 9px 10px;
  background: #faf7f1;
  color: #5f4b32;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
}

.sheet-row-detail-marker {
  flex: 0 0 auto;
  color: #9a7b4f;
  font-size: 12px;
  font-weight: 700;
}

.sheet-row-detail-label {
  min-width: 0;
  word-break: break-word;
}

.sheet-row-detail-value {
  min-width: 0;
  padding: 9px 12px;
  color: #1f2937;
  font-size: 13px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

.sheet-row-detail-value a {
  color: #1d4ed8;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.sheet-row-detail-item.is-empty .sheet-row-detail-value {
  color: #9ca3af;
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

.sheet-settings-filter-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  justify-content: flex-end;
  flex-wrap: wrap;
}

.sheet-settings-filter-summary {
  margin-right: auto;
  color: #8b7355;
  font-size: 12px;
  line-height: 1.2;
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

.sheet-frame :deep(.handsontable tbody th) {
  vertical-align: middle;
}

.sheet-frame :deep(.handsontable td.sheet-row-marker-cell) {
  position: sticky;
  left: 0;
  z-index: 4;
  padding: 0;
  color: var(--ht-header-row-foreground-color);
  background: var(--ht-header-row-background-color, #f7f7f7) !important;
  font-weight: 400;
  text-align: center;
  vertical-align: middle;
  white-space: nowrap;
}

.sheet-frame :deep(.handsontable th.sheet-row-marker-col-header) {
  position: sticky;
  left: 0;
  z-index: 7;
  background: var(--ht-header-background-color, #f7f7f7) !important;
}

.sheet-frame :deep(.handsontable .ht_clone_top td.sheet-row-marker-cell) {
  z-index: 8;
}

.sheet-frame :deep(.handsontable tbody th .relative) {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 100%;
}

.sheet-frame :deep(.handsontable td.sheet-cell-has-link) {
  color: #1d4ed8;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.sheet-frame :deep(.handsontable td.sheet-cell-has-action) {
  text-align: center !important;
  vertical-align: middle;
}

.sheet-frame :deep(.handsontable td.sheet-grid-note-header-cell.sheet-cell-has-action) {
  background: #f2f2f2 !important;
}

.sheet-frame :deep(.handsontable td.sheet-cell-has-action .sheet-cell-action-button-inner) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  max-width: 100%;
  min-height: 24px;
  padding: 0 12px;
  border: 1px solid #b8c7e8;
  border-radius: 6px;
  background: #eef5ff;
  color: #1f4f99;
  font: 600 12px/22px Inter, "Segoe UI", sans-serif;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}

.sheet-frame :deep(.handsontable td.sheet-cell-has-action:hover .sheet-cell-action-button-inner) {
  border-color: #7ea4e8;
  background: #e3efff;
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

.sheet-frame.has-frozen-columns :deep(.handsontable .ht_clone_inline_start::after),
.sheet-frame.has-frozen-columns :deep(.handsontable .ht_clone_left::after),
.sheet-frame.has-frozen-columns :deep(.handsontable .ht_clone_top_inline_start_corner::after),
.sheet-frame.has-frozen-columns :deep(.handsontable .ht_clone_top_left_corner::after) {
  content: "";
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 5;
  width: 2px;
  background: #8f8f8f;
  pointer-events: none;
}

.sheet-frame.has-frozen-rows :deep(.handsontable .ht_clone_top::after) {
  content: "";
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 5;
  height: 2px;
  background: #7f9b70;
  pointer-events: none;
}

.sheet-frame.has-frozen-rows :deep(.handsontable .ht_clone_top_inline_start_corner::before),
.sheet-frame.has-frozen-rows :deep(.handsontable .ht_clone_top_left_corner::before) {
  content: "";
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 6;
  height: 2px;
  background: #7f9b70;
  pointer-events: none;
}

.sheet-frame :deep(.handsontable td.sheet-freeze-column-boundary),
.sheet-frame :deep(.handsontable th.sheet-freeze-column-boundary) {
  box-shadow: inset -2px 0 0 #8f8f8f;
}

.sheet-frame :deep(.handsontable td.sheet-freeze-row-boundary),
.sheet-frame :deep(.handsontable tbody th.sheet-freeze-row-boundary) {
  box-shadow: inset 0 -2px 0 #7f9b70;
}

.sheet-frame :deep(.handsontable td.sheet-freeze-column-boundary.sheet-freeze-row-boundary) {
  box-shadow:
    inset -2px 0 0 #8f8f8f,
    inset 0 -2px 0 #7f9b70;
}

.sheet-frame :deep(th.sheet-col-marker) {
  background: #f7f7f7 !important;
  color: #8c7a62;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.sheet-frame :deep(th.sheet-col-marker .colHeader) {
  opacity: 0.9;
}

.sheet-frame :deep(.handsontable th.sheet-column-marker-header-selected) {
  background: #e8f2ff !important;
  color: #1d4ed8;
}

.sheet-frame :deep(.handsontable td.sheet-grid-header-cell) {
  font-weight: 700;
  text-align: center;
  vertical-align: middle;
}

.sheet-frame :deep(.handsontable td.sheet-grid-group-header-cell) {
  color: #0f172a;
}

.sheet-frame :deep(.handsontable td.sheet-grid-field-header-cell) {
  background: #e7eefb;
  color: #0f172a;
}

.sheet-frame :deep(.handsontable td.sheet-grid-field-header-cell-filtered) {
  background: #dbeafe;
  color: #0f2f6b;
}

.sheet-frame :deep(.handsontable td.sheet-grid-note-header-cell) {
  background: #f2f2f2;
  color: #5f6368;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.35;
  white-space: pre-wrap;
  word-break: break-word;
}

.sheet-frame :deep(.handsontable td.sheet-grid-header-cell-selected) {
  position: relative;
  z-index: 2;
  outline: 2px solid #409eff;
  outline-offset: -2px;
}

.sheet-frame :deep(.sheet-header-label) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  min-width: 0;
}

.sheet-frame :deep(.sheet-header-title) {
  display: block;
  flex: 0 0 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sheet-frame :deep(.sheet-header-title.has-link) {
  color: #1d4ed8;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.sheet-frame :deep(.sheet-header-filter-button) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}

.sheet-frame :deep(.sheet-header-filter-button:hover),
.sheet-frame :deep(.sheet-header-filter-button.is-active) {
  border-color: #93c5fd;
  background: #eff6ff;
  color: #1d4ed8;
}

.sheet-frame :deep(.sheet-header-filter-button svg) {
  width: 12px;
  height: 12px;
  fill: currentColor;
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

.sheet-column-filter-popover {
  position: fixed;
  z-index: 2450;
  width: min(300px, calc(100vw - 24px));
  padding: 10px;
  border: 1px solid #d8e4f7;
  border-radius: 8px;
  background: #fffdfa;
  box-shadow: 0 10px 28px rgba(30, 64, 175, 0.14);
}

.sheet-column-filter-popover-title {
  margin-bottom: 8px;
  color: #374151;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.3;
}

.sheet-column-filter-option-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  margin-top: 9px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.4;
}

.sheet-column-filter-option-toolbar button {
  padding: 0;
  border: 0;
  background: transparent;
  color: #334155;
  font: inherit;
  line-height: inherit;
  cursor: pointer;
}

.sheet-column-filter-option-toolbar button:hover {
  color: #1d4ed8;
}

.sheet-column-filter-option-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 180px;
  margin-top: 7px;
  padding: 2px 0;
  overflow: auto;
}

.sheet-column-filter-option-row {
  display: flex;
  align-items: center;
  min-height: 24px;
  gap: 6px;
  color: #334155;
  font-size: 13px;
  line-height: 1.35;
  cursor: pointer;
}

.sheet-column-filter-option-row input {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  margin: 0;
}

.sheet-column-filter-option-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sheet-column-filter-option-count {
  flex: 0 0 auto;
  color: #64748b;
}

.sheet-column-filter-option-empty {
  margin-top: 8px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.4;
}

.sheet-column-filter-popover-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
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

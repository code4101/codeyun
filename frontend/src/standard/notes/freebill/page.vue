<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { FolderOpened, QuestionFilled, Refresh, Upload } from '@element-plus/icons-vue'
import { BarChart, type BarSeriesOption } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  type GridComponentOption,
  type TooltipComponentOption,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import type { ComposeOption, ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

import {
  applyFreebillRecordOverrides,
  clearFreebillRecordOverrides,
  cloneFreebillProgramChannel,
  createFreebillDateRangeRule,
  createFreebillIncludeAllProgram,
  fetchFreebillCategoryBranchRecords,
  fetchFreebillDashboardByProgram,
  fetchFreebillFilterOptions,
  fetchFreebillSheetWorkbook,
  fetchFreebillStatus,
  importFreebillFiles,
  normalizeFreebillProgramChannel,
  refreshFreebillSheetWorkbook,
  upsertFreebillDateRangeRule,
  type FreebillCategoryStat,
  type FreebillDashboard,
  type FreebillFilterOptions,
  type FreebillImportSource,
  type FreebillProgramChannel,
  type FreebillRecord,
  type FreebillSheetWorkbookSheet,
  type FreebillSheetWorkbook,
  type FreebillStatus,
  type FreebillTrendGranularity,
} from '@/api/freebill'
import FreebillProgramBar from './FreebillProgramBar.vue'
import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

type TrendChartOption = ComposeOption<BarSeriesOption | GridComponentOption | TooltipComponentOption>

const emptySummary = {
  total_income: 0,
  total_expense: 0,
  total_ignore: 0,
  total_other: 0,
  total_count: 0,
  balance: 0,
}

type DateRange = {
  start: Date
  end: Date
}

const FREEBILL_SHEET_TABS = [
  { key: 'records', label: '账单明细', emptyText: '暂无账单明细' },
  { key: 'monthly', label: '月度汇总', emptyText: '暂无月度汇总' },
  { key: 'categories', label: '分类汇总', emptyText: '暂无分类汇总' },
  { key: 'raw-files', label: '原始文件', emptyText: '暂无原始文件' },
] as const
type FreebillSheetTabKey = (typeof FREEBILL_SHEET_TABS)[number]['key']
type NoteSheetWorkspaceInstance = InstanceType<typeof NoteSheetWorkspace>
type CategoryBranchDetailState = {
  loading: boolean
  loaded: boolean
  total: number
  items: FreebillRecord[]
  error: string
}
const SHEET_TAB_CONTEXT_MENU_WIDTH = 140
const SHEET_TAB_CONTEXT_MENU_HEIGHT = 90
const RECORD_SHEET_FILTER_FIELDS = [
  { value: '__all', label: '全部记录', mode: 'all' },
  { value: '交易时间', label: '交易时间', field: '交易时间', mode: 'date' },
  { value: '来源', label: '来源', field: '来源', mode: 'enum', enumKey: 'sources' },
  { value: '收支', label: '收支', field: '收支', mode: 'enum', enumKey: 'directions' },
  { value: '分类', label: '分类', field: '分类', mode: 'enum', enumKey: 'categories' },
  { value: '__full_text', label: '全文搜索', mode: 'full_text' },
  { value: '交易对方', label: '交易对方', field: '交易对方', mode: 'text' },
  { value: '商品', label: '商品', field: '商品', mode: 'text' },
  { value: '金额', label: '金额', field: '金额', mode: 'number' },
  { value: '状态', label: '状态', field: '状态', mode: 'text' },
] as const
const RECORD_SHEET_BACKEND_FIELD_MAP: Record<string, string> = {
  create_time: '交易时间',
  source: '来源',
  direction: '收支',
  type: '分类',
  counterparty: '交易对方',
  product_name: '商品',
  amount: '金额',
  status: '状态',
}

const DAY_MS = 24 * 60 * 60 * 1000
const MIN_TREND_ZOOM_DAYS = 1
const FREEBILL_FILTER_STATE_STORAGE_KEY = 'codeyun.freebill.filterState.v1'
const CATEGORY_DETAIL_POPOVER_WIDTH = 760
const CATEGORY_DETAIL_POPOVER_MAX_HEIGHT = 360
const CATEGORY_DETAIL_POINTER_OFFSET_X = 12
const CATEGORY_DETAIL_POINTER_OFFSET_Y = 14
const CATEGORY_DIRECTION_COLORS: Record<string, string> = {
  支出: '#d78377',
  收入: '#86b96f',
  不计收支: '#94a3b8',
  '(空白)': '#7aa2c7',
}
const CATEGORY_DIRECTION_COLOR_PALETTE = [
  '#7aa2c7',
  '#c9985a',
  '#9a86c8',
  '#5da8a3',
  '#c77b93',
  '#8b9f5e',
]

const loading = ref(false)
const sheetWorkbookLoading = ref(false)
const importingSource = ref<FreebillImportSource | ''>('')
const status = ref<FreebillStatus | null>(null)
const dashboard = ref<FreebillDashboard | null>(null)
const sheetWorkbook = ref<FreebillSheetWorkbook | null>(null)
const sheetReloadToken = ref(0)
const restoredFilterState = readFreebillFilterState()
const backendProgram = ref<FreebillProgramChannel>(restoredFilterState?.backendProgram ?? createFreebillIncludeAllProgram())
const frontendProgram = ref<FreebillProgramChannel>(restoredFilterState?.frontendProgram ?? createDefaultPassThroughProgram())
const sheetViewProgram = ref<FreebillProgramChannel>(restoredFilterState?.sheetViewProgram ?? createDefaultPassThroughProgram())
const filterOptions = ref<FreebillFilterOptions>({
  sources: [],
  directions: [],
  categories: [],
})
const trendGranularity = ref<FreebillTrendGranularity>(restoredFilterState?.trendGranularity ?? 'month')
const alipayFileInput = ref<HTMLInputElement | null>(null)
const wechatFileInput = ref<HTMLInputElement | null>(null)
const trendChartRef = ref<HTMLDivElement | null>(null)
const expandedCategoryKeys = ref<Set<string>>(new Set())
const recordOverrideLoadingTradeNos = ref<Set<string>>(new Set())
const sheetWorkspaceRefs = new Map<FreebillSheetTabKey, NoteSheetWorkspaceInstance>()
const autoExpandedCategoryKeys = new Set<string>()
const sheetTabContextMenu = ref({
  visible: false,
  key: null as FreebillSheetTabKey | null,
  left: 0,
  top: 0,
})
const categoryDetailPopover = reactive({
  visible: false,
  key: '',
  direction: '',
  category: null as string | null,
  counterparty: null as string | null,
  left: 0,
  top: 0,
})
let trendZoomTimer: ReturnType<typeof window.setTimeout> | undefined
let categoryDetailHideTimer: ReturnType<typeof window.setTimeout> | undefined
let trendChart: ECharts | null = null
let trendResizeObserver: ResizeObserver | undefined
let lastAppliedDefaultFrontendRangeKey = restoredFilterState?.lastAppliedDefaultFrontendRangeKey ?? ''
let persistedBackendProgram = cloneFreebillProgramChannel(backendProgram.value)
let suppressNextFrontendProgramQuery = false
const categoryBranchDetailCache = reactive<Record<string, CategoryBranchDetailState>>({})

const summary = computed(() => dashboard.value?.summary ?? emptySummary)
const activeCategoryDetailState = computed(() => {
  if (!categoryDetailPopover.visible || !categoryDetailPopover.direction) return null
  return getCategoryDetailState(
    categoryDetailPopover.direction,
    categoryDetailPopover.category,
    categoryDetailPopover.counterparty,
  )
})
const categoryDetailPopoverStyle = computed(() => {
  if (typeof window === 'undefined') {
    return { left: '0px', top: '0px' }
  }
  const margin = 10
  const width = Math.min(CATEGORY_DETAIL_POPOVER_WIDTH, window.innerWidth - margin * 2)
  const height = Math.min(CATEGORY_DETAIL_POPOVER_MAX_HEIGHT, window.innerHeight - margin * 2)
  let left = categoryDetailPopover.left + CATEGORY_DETAIL_POINTER_OFFSET_X
  let top = categoryDetailPopover.top + CATEGORY_DETAIL_POINTER_OFFSET_Y

  if (left + width > window.innerWidth - margin) {
    left = Math.max(margin, window.innerWidth - margin - width)
  }
  if (top + height > window.innerHeight - margin) {
    top = Math.max(margin, categoryDetailPopover.top - height - CATEGORY_DETAIL_POINTER_OFFSET_Y)
  }
  return {
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    maxHeight: `${height}px`,
  }
})
const lastImportedText = computed(() => {
  const value = status.value?.last_imported_at
  return value ? new Date(value * 1000).toLocaleString() : ''
})
const summaryItems = computed(() => [
  { key: 'income', label: '收入', value: formatMoney(summary.value.total_income), tone: 'income' },
  { key: 'expense', label: '支出', value: formatMoney(summary.value.total_expense), tone: 'expense' },
  { key: 'balance', label: '结余', value: formatMoney(summary.value.balance), tone: summary.value.balance < 0 ? 'expense' : 'balance' },
  { key: 'count', label: '记录', value: formatNumber(summary.value.total_count), tone: 'muted' },
])
const trendGranularityOptions: Array<{ label: string; value: FreebillTrendGranularity }> = [
  { label: '日', value: 'day' },
  { label: '周', value: 'week' },
  { label: '月', value: 'month' },
  { label: '年', value: 'year' },
]
const trendUnitLabels: Record<FreebillTrendGranularity, string> = {
  day: '天',
  week: '周',
  month: '月',
  year: '年',
}
const trendUnitLabel = computed(() => {
  return trendUnitLabels[trendGranularity.value] ?? '期'
})
const trendItems = computed(() => {
  const items = dashboard.value?.monthly_trend ?? []
  return items.map((item, index) => {
    const fullPeriod = formatTrendPeriod(item.month)
    const previousFullPeriod = index > 0 ? formatTrendPeriod(items[index - 1]?.month) : ''
    const axisAnchor = isTrendAxisAnchor(fullPeriod, previousFullPeriod, index)
    return {
      ...item,
      fullPeriod,
      axisAnchor,
      axisLabel: buildTrendAxisLabel(fullPeriod, previousFullPeriod, index),
    }
  })
})
const trendChartStyle = computed(() => ({
  width: `${Math.max(360, 66 + trendItems.value.length * 44)}px`,
}))
const categoryTreeItems = computed(() => {
  const tree = dashboard.value?.category_tree ?? []
  if (tree.length) return tree
  return buildLegacyCategoryTree()
})
const maxCategoryValue = computed(() => Math.max(0, ...flattenCategoryValues(categoryTreeItems.value)))
const workbookId = computed(() => sheetWorkbook.value?.workbook.id ?? null)
const sheetTabs = computed(() => FREEBILL_SHEET_TABS.map((tab) => ({
  ...tab,
  sheet: getSheetItem(tab.key),
})))
const activeSheetKey = ref<(typeof FREEBILL_SHEET_TABS)[number]['key']>('records')
const recordsBaseRowFilterPrograms = computed(() => [
  mapProgramFields(backendProgram.value, RECORD_SHEET_BACKEND_FIELD_MAP),
  mapProgramFields(frontendProgram.value, RECORD_SHEET_BACKEND_FIELD_MAP),
])
const sheetTabContextMenuTab = computed(() => (
  sheetTabs.value.find((tab) => tab.key === sheetTabContextMenu.value.key) ?? null
))

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '读取失败'
}

async function loadStatus() {
  status.value = await fetchFreebillStatus()
}

async function loadDashboard() {
  clearCategoryDetailCache()
  dashboard.value = await fetchFreebillDashboardByProgram({
    program: backendProgram.value,
    programs: [backendProgram.value, frontendProgram.value],
    trend_granularity: trendGranularity.value,
  })
}

async function loadFilterOptions() {
  filterOptions.value = await fetchFreebillFilterOptions()
}

async function loadSheetWorkbook() {
  const payload = await fetchFreebillSheetWorkbook()
  if (isCompleteSheetWorkbook(payload)) {
    applySheetWorkbook(payload)
    return
  }

  applySheetWorkbook(await refreshFreebillSheetWorkbook())
}

async function refreshSheetWorkbook() {
  sheetWorkbookLoading.value = true
  try {
    applySheetWorkbook(await refreshFreebillSheetWorkbook())
    ElMessage.success('星云表格已刷新')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    sheetWorkbookLoading.value = false
  }
}

function openWorkbookFile() {
  if (!workbookId.value) return
  const activeSheet = getSheetItem(activeSheetKey.value)
  const query = activeSheet ? `?sheet=${activeSheet.sheet_id}` : ''
  window.open(`/workbook/${workbookId.value}${query}`, '_blank', 'noopener')
}

function openWorkbookSheet(sheet: FreebillSheetWorkbookSheet | null | undefined) {
  if (!workbookId.value || !sheet) return
  window.open(`/workbook/${workbookId.value}?sheet=${sheet.sheet_id}`, '_blank', 'noopener')
}

async function refreshAll() {
  loading.value = true
  try {
    await loadStatus()
    suppressNextFrontendProgramQuery = ensureFrontendDefaultProgram()
    await Promise.all([
      loadDashboard(),
      loadFilterOptions(),
      loadSheetWorkbook(),
    ])
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function executeQuery() {
  try {
    await loadDashboard()
    return true
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
    return false
  }
}

async function applyBackendProgram() {
  syncTrendGranularityToProgramRange()
  if (await executeQuery()) {
    persistedBackendProgram = cloneFreebillProgramChannel(backendProgram.value)
    persistFreebillFilterState()
  }
}

async function changeTrendGranularity() {
  try {
    await loadDashboard()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

function resetSheetViewProgram() {
  sheetViewProgram.value = createDefaultPassThroughProgram()
}

function resetFrontendProgram() {
  ensureFrontendDefaultProgram(true)
  syncTrendGranularityToProgramRange()
}

function applySheetWorkbook(payload: FreebillSheetWorkbook | null | undefined) {
  sheetWorkbook.value = payload ?? null
  if (payload) {
    sheetReloadToken.value += 1
  }
}

function isCompleteSheetWorkbook(payload: FreebillSheetWorkbook | null | undefined) {
  if (!payload?.workbook?.id) return false
  const sheetKeys = new Set(payload.sheets.map((sheet) => sheet.key))
  return FREEBILL_SHEET_TABS.every((tab) => sheetKeys.has(tab.key))
}

function getSheetItem(key: string): FreebillSheetWorkbookSheet | null {
  return sheetWorkbook.value?.sheets.find((sheet) => sheet.key === key) ?? null
}

function getSheetWorkspaceKey(key: string) {
  const sheet = getSheetItem(key)
  return `${workbookId.value ?? 'none'}:${sheet?.sheet_id ?? 'none'}:${sheet?.updated_at ?? 0}:${sheetReloadToken.value}`
}

function mapProgramFields(program: FreebillProgramChannel, fieldMap: Record<string, string>) {
  const draft = cloneFreebillProgramChannel(program)
  draft.rules.forEach((rule) => {
    if (rule.matcher.kind !== 'field' || !rule.matcher.field) return
    rule.matcher.field = fieldMap[rule.matcher.field] ?? rule.matcher.field
  })
  return draft
}

function ensureFrontendDefaultProgram(force = false) {
  const range = getLatestDataYearRange()
  if (!range) {
    if (force) {
      frontendProgram.value = createDefaultPassThroughProgram()
      lastAppliedDefaultFrontendRangeKey = ''
      return true
    }
    return false
  }

  const nextProgram = createFrontendDateRangeProgram(range)
  const nextRangeKey = getProgramDateRangeKey(nextProgram, 'create_time')
  const currentRangeKey = getProgramDateRangeKey(frontendProgram.value, 'create_time')
  const shouldApply = force
    || isIncludeAllProgram(frontendProgram.value)
    || !frontendProgram.value.rules.length
    || (
      isOnlyDateRangeProgram(frontendProgram.value, 'create_time')
      && (!lastAppliedDefaultFrontendRangeKey || currentRangeKey === lastAppliedDefaultFrontendRangeKey)
    )

  if (!shouldApply) return false
  frontendProgram.value = nextProgram
  lastAppliedDefaultFrontendRangeKey = nextRangeKey
  syncTrendGranularityToProgramRange()
  return true
}

function createDefaultPassThroughProgram(): FreebillProgramChannel {
  return {
    default: true,
    rules: [],
  }
}

type FreebillFilterState = {
  backendProgram: FreebillProgramChannel
  frontendProgram: FreebillProgramChannel
  sheetViewProgram: FreebillProgramChannel
  trendGranularity: FreebillTrendGranularity
  lastAppliedDefaultFrontendRangeKey: string
}

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function normalizeTrendGranularityValue(value: unknown): FreebillTrendGranularity {
  return value === 'day' || value === 'week' || value === 'year' ? value : 'month'
}

function normalizeStoredProgram(value: unknown, fallback: FreebillProgramChannel) {
  return isRecord(value)
    ? normalizeFreebillProgramChannel(value)
    : cloneFreebillProgramChannel(fallback)
}

function readFreebillFilterState(): FreebillFilterState | null {
  if (!canUseLocalStorage()) return null

  try {
    const raw = window.localStorage.getItem(FREEBILL_FILTER_STATE_STORAGE_KEY)
    if (!raw) return null
    const payload = JSON.parse(raw) as Record<string, unknown>
    return {
      backendProgram: normalizeStoredProgram(payload.backendProgram, createFreebillIncludeAllProgram()),
      frontendProgram: normalizeStoredProgram(payload.frontendProgram, createDefaultPassThroughProgram()),
      sheetViewProgram: normalizeStoredProgram(payload.sheetViewProgram, createDefaultPassThroughProgram()),
      trendGranularity: normalizeTrendGranularityValue(payload.trendGranularity),
      lastAppliedDefaultFrontendRangeKey: typeof payload.lastAppliedDefaultFrontendRangeKey === 'string'
        ? payload.lastAppliedDefaultFrontendRangeKey
        : '',
    }
  } catch (error) {
    console.warn('Failed to restore freebill filter state', error)
    window.localStorage.removeItem(FREEBILL_FILTER_STATE_STORAGE_KEY)
    return null
  }
}

function persistFreebillFilterState() {
  if (!canUseLocalStorage()) return

  window.localStorage.setItem(FREEBILL_FILTER_STATE_STORAGE_KEY, JSON.stringify({
    version: 1,
    updatedAt: Date.now(),
    backendProgram: cloneFreebillProgramChannel(persistedBackendProgram),
    frontendProgram: cloneFreebillProgramChannel(frontendProgram.value),
    sheetViewProgram: cloneFreebillProgramChannel(sheetViewProgram.value),
    trendGranularity: normalizeTrendGranularityValue(trendGranularity.value),
    lastAppliedDefaultFrontendRangeKey,
  }))
}

function createFrontendDateRangeProgram(range: DateRange): FreebillProgramChannel {
  const normalized = normalizeDateRange(range)
  return {
    default: true,
    rules: [
      createFreebillDateRangeRule(
        'create_time',
        formatLocalDate(normalized.start),
        formatLocalDate(normalized.end),
      ),
    ],
  }
}

function isIncludeAllProgram(program: FreebillProgramChannel) {
  return program.default === false
    && program.rules.length === 1
    && program.rules[0]?.action === 'include'
    && program.rules[0]?.matcher.kind === 'all'
}

function isOnlyDateRangeProgram(program: FreebillProgramChannel, field: string) {
  const rule = program.rules[0]
  return program.default === true
    && program.rules.length === 1
    && rule?.action === 'filter'
    && rule.matcher.kind === 'field'
    && rule.matcher.field === field
    && rule.matcher.op === 'between'
}

function getProgramDateRangeKey(program: FreebillProgramChannel, field: string) {
  const range = getProgramDateRange(program, field)
  if (!range) return ''
  return `${formatLocalDate(range.start)}:${formatLocalDate(range.end)}`
}

function setSheetWorkspaceRef(key: FreebillSheetTabKey, instance: unknown) {
  if (instance) {
    sheetWorkspaceRefs.set(key, instance as NoteSheetWorkspaceInstance)
  } else {
    sheetWorkspaceRefs.delete(key)
  }
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

function openSheetTabContextMenu(
  event: MouseEvent,
  tab: { key: FreebillSheetTabKey; sheet: FreebillSheetWorkbookSheet | null },
) {
  if (!tab.sheet) return
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()

  activeSheetKey.value = tab.key
  positionSheetTabContextMenu(event)
  sheetTabContextMenu.value.key = tab.key
  sheetTabContextMenu.value.visible = true
}

async function waitForSheetWorkspaceRef(key: FreebillSheetTabKey) {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await nextTick()
    const workspace = sheetWorkspaceRefs.get(key)
    if (workspace) {
      return workspace
    }
    await new Promise((resolve) => window.requestAnimationFrame(resolve))
  }
  return sheetWorkspaceRefs.get(key) ?? null
}

async function configureSheetFromTabContextMenu() {
  const key = sheetTabContextMenu.value.key
  closeSheetTabContextMenu()
  if (!key) return

  activeSheetKey.value = key
  const workspace = await waitForSheetWorkspaceRef(key)
  if (!workspace) {
    ElMessage.warning('工作表还在加载')
    return
  }
  workspace.openSheetSettings?.()
}

function openWorkbookFromTabContextMenu() {
  const tab = sheetTabContextMenuTab.value
  closeSheetTabContextMenu()
  openWorkbookSheet(tab?.sheet)
}

function handleGlobalMouseDown(event: MouseEvent) {
  if (!sheetTabContextMenu.value.visible) return
  const target = event.target
  if (target instanceof HTMLElement && target.closest('.freebill-sheet-tab-context-menu')) {
    return
  }
  closeSheetTabContextMenu()
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeSheetTabContextMenu()
  }
}

function handleTrendWheel(event: WheelEvent) {
  if (!event.ctrlKey) return
  event.preventDefault()

  const bounds = getDataDateBounds()
  const currentRange = getActiveTrendDateRange()
  if (!bounds || !currentRange) return

  const target = event.currentTarget as HTMLElement | null
  const rect = target?.getBoundingClientRect()
  const rawRatio = rect && rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0.5
  const anchorRatio = clampNumber(rawRatio, 0.02, 0.98)
  const zoomFactor = event.deltaY < 0 ? 0.72 : 1.38
  const nextRange = zoomDateRange(currentRange, anchorRatio, zoomFactor, bounds)
  const nextStart = formatLocalDate(nextRange.start)
  const nextEnd = formatLocalDate(nextRange.end)
  const currentProgramRange = getProgramDateRange(frontendProgram.value, 'create_time')
  if (
    currentProgramRange
    && nextStart === formatLocalDate(currentProgramRange.start)
    && nextEnd === formatLocalDate(currentProgramRange.end)
  ) return

  frontendProgram.value = ensureDateRangeRuleFirst(
    upsertFreebillDateRangeRule(frontendProgram.value, 'create_time', nextStart, nextEnd),
    'create_time',
  )
  trendGranularity.value = pickTrendGranularity(nextRange)
}

function ensureDateRangeRuleFirst(program: FreebillProgramChannel, field: string) {
  const draft = cloneFreebillProgramChannel(program)
  const index = draft.rules.findIndex((rule) => (
    rule.matcher.kind === 'field'
    && rule.matcher.field === field
    && rule.matcher.op === 'between'
  ))
  if (index > 0) {
    const [rule] = draft.rules.splice(index, 1)
    if (rule) draft.rules.unshift(rule)
  }
  return draft
}

function scheduleTrendZoomQuery() {
  if (trendZoomTimer !== undefined) {
    window.clearTimeout(trendZoomTimer)
  }
  trendZoomTimer = window.setTimeout(() => {
    trendZoomTimer = undefined
    void executeQuery()
  }, 160)
}

function openFilePicker(source: FreebillImportSource) {
  if (source === 'alipay') {
    alipayFileInput.value?.click()
  } else {
    wechatFileInput.value?.click()
  }
}

async function handleFileInput(source: FreebillImportSource, event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (!files.length) return

  importingSource.value = source
  try {
    const result = await importFreebillFiles(source, files)
    const sourceLabel = source === 'alipay' ? '支付宝 CSV' : '微信 Excel'
    const message = `${sourceLabel}导入完成：新增 ${result.inserted} 条，跳过 ${result.skipped} 条`
    if (result.error_count) {
      const firstError = result.results.find((item) => item.status === 'error')?.error
      ElMessage.warning(firstError ? `${message}，失败 ${result.error_count} 个：${firstError}` : `${message}，失败 ${result.error_count} 个`)
    } else {
      ElMessage.success(message)
    }
    await refreshAll()
    if (sheetWorkbook.value) {
      await refreshSheetWorkbook()
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    importingSource.value = ''
    input.value = ''
  }
}

function formatNumber(value: number | null | undefined) {
  return Number(value || 0).toLocaleString()
}

function formatMoney(value: number | null | undefined) {
  const numberValue = Number(value || 0)
  const normalized = Object.is(numberValue, -0) ? 0 : numberValue
  return formatSignificantUnitNumber(normalized)
}

function formatCompactMoney(value: number | null | undefined) {
  const numberValue = Number(value || 0)
  return formatSignificantUnitNumber(numberValue)
}

function formatSignificantUnitNumber(value: number, significantDigits = 4) {
  if (!Number.isFinite(value)) return '0'
  const normalized = Object.is(value, -0) ? 0 : value
  const sign = normalized < 0 ? '-' : ''
  const absValue = Math.abs(normalized)
  if (absValue >= 100000000) {
    return `${sign}${formatSignificantDigits(absValue / 100000000, significantDigits)}亿`
  }
  if (absValue >= 10000) {
    return `${sign}${formatSignificantDigits(absValue / 10000, significantDigits)}万`
  }
  return `${sign}${formatSignificantDigits(absValue, significantDigits, false)}`
}

function formatSignificantDigits(value: number, significantDigits: number, useGrouping = true) {
  if (!Number.isFinite(value) || value === 0) return '0'
  const decimalDigits = Math.max(0, significantDigits - Math.floor(Math.log10(Math.abs(value))) - 1)
  return value.toLocaleString(undefined, {
    useGrouping,
    minimumFractionDigits: 0,
    maximumFractionDigits: decimalDigits,
  })
}

function formatCategoryBarLabel(value: number | null | undefined) {
  return formatCompactMoney(value)
}

function buildLegacyCategoryTree(): FreebillCategoryStat[] {
  const tree: FreebillCategoryStat[] = []
  if (dashboard.value?.expense_categories.length) {
    tree.push({
      name: '支出',
      value: dashboard.value.summary.total_expense,
      count: dashboard.value.expense_categories.reduce((sum, item) => sum + Number(item.count || 0), 0),
      children: dashboard.value.expense_categories,
    })
  }
  if (dashboard.value?.income_categories.length) {
    tree.push({
      name: '收入',
      value: dashboard.value.summary.total_income,
      count: dashboard.value.income_categories.reduce((sum, item) => sum + Number(item.count || 0), 0),
      children: dashboard.value.income_categories,
    })
  }
  return tree
}

function flattenCategoryValues(items: FreebillCategoryStat[]): number[] {
  const values: number[] = []
  items.forEach((item) => {
    values.push(Number(item.value || 0))
    values.push(...flattenCategoryValues(getCategoryChildren(item)))
  })
  return values
}

function getCategoryKey(...parts: string[]) {
  return parts.map((part) => encodeURIComponent(part)).join('/')
}

function getCategoryChildren(item: FreebillCategoryStat) {
  return Array.isArray(item.children) ? item.children : []
}

function hasCategoryChildren(item: FreebillCategoryStat) {
  return getCategoryChildren(item).length > 0
}

function isCategoryExpanded(...parts: string[]) {
  return expandedCategoryKeys.value.has(getCategoryKey(...parts))
}

function toggleCategoryExpanded(...parts: string[]) {
  const key = getCategoryKey(...parts)
  const next = new Set(expandedCategoryKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedCategoryKeys.value = next
}

function getCategoryDetailKey(direction: string, category?: string | null, counterparty?: string | null) {
  return getCategoryKey('detail', direction, category || '', counterparty || '')
}

function createCategoryDetailState(): CategoryBranchDetailState {
  return {
    loading: false,
    loaded: false,
    total: 0,
    items: [],
    error: '',
  }
}

function getCategoryDetailState(direction: string, category?: string | null, counterparty?: string | null) {
  const key = getCategoryDetailKey(direction, category, counterparty)
  if (!categoryBranchDetailCache[key]) {
    categoryBranchDetailCache[key] = createCategoryDetailState()
  }
  return categoryBranchDetailCache[key]
}

function clearCategoryDetailCache() {
  hideCategoryDetailPopover()
  Object.keys(categoryBranchDetailCache).forEach((key) => {
    delete categoryBranchDetailCache[key]
  })
}

function updateCategoryDetailPopoverPosition(event: MouseEvent) {
  categoryDetailPopover.left = event.clientX
  categoryDetailPopover.top = event.clientY
}

function keepCategoryDetailPopoverVisible() {
  if (categoryDetailHideTimer !== undefined) {
    window.clearTimeout(categoryDetailHideTimer)
    categoryDetailHideTimer = undefined
  }
}

function showCategoryDetailPopover(
  event: MouseEvent,
  direction: string,
  category?: string | null,
  counterparty?: string | null,
) {
  keepCategoryDetailPopoverVisible()
  updateCategoryDetailPopoverPosition(event)
  categoryDetailPopover.key = getCategoryDetailKey(direction, category, counterparty)
  categoryDetailPopover.direction = direction
  categoryDetailPopover.category = category ?? null
  categoryDetailPopover.counterparty = counterparty ?? null
  categoryDetailPopover.visible = true
  void loadCategoryBranchRecords(direction, category, counterparty)
}

function moveCategoryDetailPopover(event: MouseEvent) {
  updateCategoryDetailPopoverPosition(event)
}

function scheduleCategoryDetailPopoverHide() {
  keepCategoryDetailPopoverVisible()
  categoryDetailHideTimer = window.setTimeout(() => {
    categoryDetailPopover.visible = false
    categoryDetailHideTimer = undefined
  }, 160)
}

function hideCategoryDetailPopover() {
  keepCategoryDetailPopoverVisible()
  categoryDetailPopover.visible = false
  categoryDetailPopover.key = ''
  categoryDetailPopover.direction = ''
  categoryDetailPopover.category = null
  categoryDetailPopover.counterparty = null
}

async function loadCategoryBranchRecords(direction: string, category?: string | null, counterparty?: string | null) {
  const state = getCategoryDetailState(direction, category, counterparty)
  if (state.loading || state.loaded) return
  state.loading = true
  state.error = ''
  try {
    const result = await fetchFreebillCategoryBranchRecords({
      program: backendProgram.value,
      programs: [backendProgram.value, frontendProgram.value],
      direction,
      category,
      counterparty,
      limit: 10,
    })
    state.items = result.items
    state.total = result.total
    state.loaded = true
  } catch (error) {
    state.error = getErrorMessage(error)
  } finally {
    state.loading = false
  }
}

function formatCategoryDetailTime(value: string | null | undefined) {
  const text = (value || '').trim()
  if (!text) return '-'
  return text.slice(0, 16).replaceAll('-', '/')
}

function formatCategoryDetailText(value: string | number | null | undefined) {
  const text = String(value ?? '').trim()
  return text || '-'
}

function getRecordTradeNo(record: FreebillRecord) {
  return String(record.trade_no || '').trim()
}

function isFlowOverrideRecord(record: FreebillRecord) {
  return record.direction === '不计收支' && record.type === '流水'
}

function isRecordOverrideLoading(record: FreebillRecord) {
  const tradeNo = getRecordTradeNo(record)
  return Boolean(tradeNo && recordOverrideLoadingTradeNos.value.has(tradeNo))
}

function setRecordOverrideLoading(record: FreebillRecord, loadingState: boolean) {
  const tradeNo = getRecordTradeNo(record)
  if (!tradeNo) return
  const next = new Set(recordOverrideLoadingTradeNos.value)
  if (loadingState) {
    next.add(tradeNo)
  } else {
    next.delete(tradeNo)
  }
  recordOverrideLoadingTradeNos.value = next
}

async function reloadAfterRecordOverride() {
  clearCategoryDetailCache()
  await Promise.all([
    loadStatus(),
    loadDashboard(),
    refreshFreebillSheetWorkbook().then(applySheetWorkbook),
  ])
}

async function markRecordAsFlow(record: FreebillRecord) {
  const tradeNo = getRecordTradeNo(record)
  if (!tradeNo) {
    ElMessage.warning('这条记录没有交易单号，不能保存人工标记')
    return
  }
  setRecordOverrideLoading(record, true)
  try {
    const result = await applyFreebillRecordOverrides({
      trade_nos: [tradeNo],
      direction: '不计收支',
      category: '流水',
      note: '人工确认不计收支流水',
    })
    if (!result.matched) {
      ElMessage.warning('没有找到对应账单记录')
      return
    }
    await reloadAfterRecordOverride()
    ElMessage.success('已标记为不计收支 / 流水')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    setRecordOverrideLoading(record, false)
  }
}

async function clearRecordFlowOverride(record: FreebillRecord) {
  const tradeNo = getRecordTradeNo(record)
  if (!tradeNo) {
    ElMessage.warning('这条记录没有交易单号，不能还原')
    return
  }
  setRecordOverrideLoading(record, true)
  try {
    const result = await clearFreebillRecordOverrides({ trade_nos: [tradeNo] })
    if (!result.cleared) {
      ElMessage.warning('这条记录没有人工标记')
      return
    }
    await reloadAfterRecordOverride()
    ElMessage.success('已还原人工标记')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    setRecordOverrideLoading(record, false)
  }
}

function getCategoryColor(direction: string) {
  const normalized = (direction || '(空白)').trim() || '(空白)'
  const fixedColor = CATEGORY_DIRECTION_COLORS[normalized]
  if (fixedColor) return fixedColor
  let hash = 0
  Array.from(normalized).forEach((char) => {
    hash = (hash * 31 + char.codePointAt(0)!) % CATEGORY_DIRECTION_COLOR_PALETTE.length
  })
  return CATEGORY_DIRECTION_COLOR_PALETTE[hash]
}

function getCategoryBarStyle(direction: string, value: number | null | undefined, maxValue: number) {
  return {
    width: barWidth(value, maxValue),
    backgroundColor: getCategoryColor(direction),
  }
}

function formatTrendPeriod(value: string | null | undefined) {
  const text = (value || '').trim()
  if (!text) return '-'
  if (trendGranularity.value === 'year') return text.slice(0, 4)
  if (trendGranularity.value === 'month') return text.slice(0, 7)
  return text.slice(0, 10).replaceAll('-', '/')
}

function buildTrendAxisLabel(current: string, previous: string, index: number) {
  if (current === '-') return current
  if (trendGranularity.value === 'year') return current
  if (isTrendAxisAnchor(current, previous, index)) return current
  if (trendGranularity.value === 'month') {
    return current.slice(5, 7)
  }
  if (trendGranularity.value === 'day' && current.slice(0, 7) === previous.slice(0, 7)) {
    return current.slice(8, 10)
  }
  return current.slice(5, 10)
}

function isTrendAxisAnchor(current: string, previous: string, index: number) {
  if (current === '-') return false
  if (index === 0 || !previous || previous === '-') return true
  if (trendGranularity.value === 'month') {
    return current.slice(0, 4) !== previous.slice(0, 4)
  }
  if (trendGranularity.value === 'day' || trendGranularity.value === 'week') {
    return current.slice(0, 7) !== previous.slice(0, 7)
  }
  return false
}

function shouldShowTrendAxisLabel(
  index: number,
  items: Array<{ axisAnchor: boolean }>,
  labelStep: number,
) {
  if (items.length <= 18) return true
  if (items[index]?.axisAnchor) return true
  if (items[index - 1]?.axisAnchor || items[index + 1]?.axisAnchor) return false
  return index % labelStep === 0
}

async function updateTrendChart() {
  await nextTick()
  const el = trendChartRef.value
  if (!el || !trendItems.value.length) {
    disposeTrendChart()
    return
  }
  if (!trendChart) {
    trendChart = echarts.init(el)
    trendResizeObserver = new ResizeObserver(() => {
      trendChart?.resize()
    })
    trendResizeObserver.observe(el)
  }
  trendChart.setOption(buildTrendChartOption(), true)
  trendChart.resize()
}

function disposeTrendChart() {
  trendResizeObserver?.disconnect()
  trendResizeObserver = undefined
  trendChart?.dispose()
  trendChart = null
}

function buildTrendChartOption(): TrendChartOption {
  const items = trendItems.value
  const labelStep = Math.max(1, Math.ceil(items.length / 18))
  return {
    animationDuration: 180,
    color: ['#d78377', '#86b96f'],
    grid: {
      top: 12,
      right: 12,
      bottom: 26,
      left: 54,
      containLabel: false,
    },
    tooltip: {
      trigger: 'item',
      borderColor: '#dfe5ee',
      confine: true,
      formatter: buildTrendTooltip,
      padding: [7, 9],
      textStyle: {
        color: '#1f2937',
        fontSize: 12,
      },
    },
    xAxis: {
      type: 'category',
      data: items.map((item) => item.axisLabel),
      axisLine: {
        onZero: true,
        lineStyle: {
          color: '#cbd5e1',
        },
      },
      axisTick: {
        show: false,
      },
      axisLabel: {
        color: '#334155',
        fontSize: 11,
        interval: (index: number) => shouldShowTrendAxisLabel(index, items, labelStep),
      },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#64748b',
        fontSize: 11,
        formatter: (value: number) => (value === 0 ? '0' : formatCompactMoney(Math.abs(value))),
      },
      axisLine: {
        show: false,
      },
      axisTick: {
        show: false,
      },
      splitLine: {
        lineStyle: {
          color: '#edf1f6',
        },
      },
    },
    series: [
      {
        name: '支出',
        type: 'bar',
        barWidth: 18,
        barGap: '-100%',
        data: items.map((item) => Number(item.expense || 0)),
        itemStyle: {
          borderRadius: [3, 3, 0, 0],
          color: '#d78377',
        },
        emphasis: {
          focus: 'series',
        },
      },
      {
        name: '收入',
        type: 'bar',
        barWidth: 18,
        data: items.map((item) => -Number(item.income || 0)),
        itemStyle: {
          borderRadius: [0, 0, 3, 3],
          color: '#86b96f',
        },
        emphasis: {
          focus: 'series',
        },
      },
    ],
  }
}

function buildTrendTooltip(params: unknown) {
  const points = Array.isArray(params) ? params : [params]
  const firstPoint = points[0] as { dataIndex?: number } | undefined
  const item = trendItems.value[Number(firstPoint?.dataIndex ?? 0)]
  if (!item) return ''
  return [
    `<div style="font-weight:650;margin-bottom:5px;">${escapeHtml(item.fullPeriod)}</div>`,
    buildTrendTooltipLine('支出', '#d78377', item.expense, item.expense_count),
    buildTrendTooltipLine('收入', '#86b96f', item.income, item.income_count),
  ].join('')
}

function buildTrendTooltipLine(label: string, color: string, value: number | null | undefined, count: number | null | undefined) {
  return `<div><span style="display:inline-block;width:7px;height:7px;background:${color};margin-right:6px;"></span>${label} ${formatMoney(value)} · ${formatNumber(Number(count || 0))} 条</div>`
}

function escapeHtml(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function syncTrendGranularityToProgramRange() {
  const range = getProgramDateRange(frontendProgram.value, 'create_time')
    ?? getProgramDateRange(backendProgram.value, 'create_time')
  if (!range) return
  trendGranularity.value = pickTrendGranularity(range)
}

function pickTrendGranularity(range: DateRange): FreebillTrendGranularity {
  const days = getDateSpanDays(range)
  if (days <= 62) return 'day'
  if (days <= 183) return 'week'
  if (days <= 6 * 366) return 'month'
  return 'year'
}

function getActiveTrendDateRange(): DateRange | null {
  const bounds = getDataDateBounds()
  const chartRange = getTrendDateRange()
  const frontendRange = getProgramDateRange(frontendProgram.value, 'create_time')
  const backendRange = getProgramDateRange(backendProgram.value, 'create_time')
  const programRange = frontendRange ?? backendRange
  const start = programRange?.start ?? chartRange?.start ?? bounds?.start
  const end = programRange?.end ?? chartRange?.end ?? bounds?.end
  if (!start || !end) return null
  const range = normalizeDateRange({ start, end })
  return bounds ? clampDateRange(range, bounds) : range
}

function getProgramDateRange(program: FreebillProgramChannel, field: string): DateRange | null {
  for (let index = program.rules.length - 1; index >= 0; index -= 1) {
    const rule = program.rules[index]
    if (rule?.matcher.kind !== 'field' || rule.matcher.field !== field) continue
    if (rule.matcher.op === 'year') {
      const year = Number(rule.matcher.value)
      if (Number.isInteger(year) && year >= 1 && year <= 9999) {
        return normalizeDateRange({
          start: new Date(year, 0, 1),
          end: new Date(year, 11, 31),
        })
      }
    }
    if (rule.matcher.op === 'between') {
      const values = Array.isArray(rule.matcher.values) ? rule.matcher.values : []
      const start = parseLocalDate(String(values[0] || ''))
      const end = parseLocalDate(String(values[1] || ''))
      if (start && end) return normalizeDateRange({ start, end })
    }
  }
  return null
}

function getTrendDateRange(): DateRange | null {
  const rows = dashboard.value?.monthly_trend ?? []
  if (!rows.length) return null
  const start = parseTrendPeriodStart(rows[0]?.month)
  const end = parseTrendPeriodEnd(rows[rows.length - 1]?.month)
  if (!start || !end) return null
  return normalizeDateRange({ start, end })
}

function getDataDateBounds(): DateRange | null {
  const start = parseLocalDate(status.value?.min_date)
  const end = parseLocalDate(status.value?.max_date)
  if (!start || !end) return null
  return normalizeDateRange({ start, end })
}

function getLatestDataYearRange(): DateRange | null {
  const bounds = getDataDateBounds()
  if (!bounds) return null
  const year = bounds.end.getFullYear()
  return clampDateRange({
    start: new Date(year, 0, 1),
    end: new Date(year, 11, 31),
  }, bounds)
}

function parseTrendPeriodStart(value: string | null | undefined) {
  const text = (value || '').trim()
  if (!text) return null
  if (trendGranularity.value === 'year') {
    const year = Number(text.slice(0, 4))
    return Number.isFinite(year) ? new Date(year, 0, 1) : null
  }
  if (trendGranularity.value === 'month') {
    const match = text.match(/^(\d{4})-(\d{2})/)
    if (!match) return null
    return new Date(Number(match[1]), Number(match[2]) - 1, 1)
  }
  return parseLocalDate(text.slice(0, 10))
}

function parseTrendPeriodEnd(value: string | null | undefined) {
  const start = parseTrendPeriodStart(value)
  if (!start) return null
  if (trendGranularity.value === 'year') return new Date(start.getFullYear(), 11, 31)
  if (trendGranularity.value === 'month') return new Date(start.getFullYear(), start.getMonth() + 1, 0)
  if (trendGranularity.value === 'week') return addDays(start, 6)
  return start
}

function zoomDateRange(range: DateRange, anchorRatio: number, factor: number, bounds: DateRange): DateRange {
  const boundsStart = dateToDayIndex(bounds.start)
  const boundsEnd = dateToDayIndex(bounds.end)
  const boundsSpan = Math.max(MIN_TREND_ZOOM_DAYS, boundsEnd - boundsStart + 1)
  const startIndex = dateToDayIndex(range.start)
  const endIndex = dateToDayIndex(range.end)
  const span = Math.max(MIN_TREND_ZOOM_DAYS, endIndex - startIndex + 1)
  const nextSpan = clampNumber(Math.round(span * factor), MIN_TREND_ZOOM_DAYS, boundsSpan)
  const anchorIndex = startIndex + (span - 1) * anchorRatio
  let nextStart = Math.round(anchorIndex - (nextSpan - 1) * anchorRatio)
  let nextEnd = nextStart + nextSpan - 1

  if (nextSpan >= boundsSpan) {
    nextStart = boundsStart
    nextEnd = boundsEnd
  } else if (nextStart < boundsStart) {
    nextStart = boundsStart
    nextEnd = nextStart + nextSpan - 1
  } else if (nextEnd > boundsEnd) {
    nextEnd = boundsEnd
    nextStart = nextEnd - nextSpan + 1
  }

  return {
    start: dayIndexToDate(nextStart),
    end: dayIndexToDate(nextEnd),
  }
}

function clampDateRange(range: DateRange, bounds: DateRange): DateRange {
  const boundsStart = dateToDayIndex(bounds.start)
  const boundsEnd = dateToDayIndex(bounds.end)
  const startIndex = clampNumber(dateToDayIndex(range.start), boundsStart, boundsEnd)
  const endIndex = clampNumber(dateToDayIndex(range.end), boundsStart, boundsEnd)
  return normalizeDateRange({
    start: dayIndexToDate(startIndex),
    end: dayIndexToDate(endIndex),
  })
}

function normalizeDateRange(range: DateRange): DateRange {
  const start = startOfLocalDay(range.start)
  const end = startOfLocalDay(range.end)
  return dateToDayIndex(start) <= dateToDayIndex(end)
    ? { start, end }
    : { start: end, end: start }
}

function getDateSpanDays(range: DateRange) {
  const normalized = normalizeDateRange(range)
  return dateToDayIndex(normalized.end) - dateToDayIndex(normalized.start) + 1
}

function parseLocalDate(value: string | null | undefined) {
  const match = (value || '').trim().match(/^(\d{4})[-/](\d{2})[-/](\d{2})/)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const date = new Date(year, month - 1, day)
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null
  return date
}

function formatLocalDate(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function addDays(date: Date, days: number) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days)
}

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function dateToDayIndex(date: Date) {
  return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY_MS)
}

function dayIndexToDate(dayIndex: number) {
  const date = new Date(dayIndex * DAY_MS)
  return new Date(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())
}

function clampNumber(value: number, min: number, max: number) {
  if (max < min) return min
  return Math.min(max, Math.max(min, value))
}

function barWidth(value: number | null | undefined, maxValue: number) {
  const numberValue = Number(value || 0)
  if (!maxValue || numberValue <= 0) return '0%'
  return `${Math.max(4, Math.round((numberValue / maxValue) * 100))}%`
}

watch(trendItems, () => {
  void updateTrendChart()
})

watch(categoryTreeItems, (items) => {
  const next = new Set(expandedCategoryKeys.value)
  let changed = false
  items.forEach((item) => {
    const key = getCategoryKey(item.name)
    if (autoExpandedCategoryKeys.has(key)) return
    autoExpandedCategoryKeys.add(key)
    next.add(key)
    changed = true
  })
  if (changed) {
    expandedCategoryKeys.value = next
  }
}, { immediate: true })

watch(frontendProgram, () => {
  if (suppressNextFrontendProgramQuery) {
    suppressNextFrontendProgramQuery = false
    return
  }
  syncTrendGranularityToProgramRange()
  scheduleTrendZoomQuery()
}, { deep: true })

watch([
  frontendProgram,
  sheetViewProgram,
  trendGranularity,
], () => {
  persistFreebillFilterState()
}, { deep: true })

onMounted(() => {
  document.addEventListener('mousedown', handleGlobalMouseDown)
  document.addEventListener('keydown', handleGlobalKeydown)
  void refreshAll()
})

onUnmounted(() => {
  document.removeEventListener('mousedown', handleGlobalMouseDown)
  document.removeEventListener('keydown', handleGlobalKeydown)
  if (trendZoomTimer !== undefined) {
    window.clearTimeout(trendZoomTimer)
  }
  if (categoryDetailHideTimer !== undefined) {
    window.clearTimeout(categoryDetailHideTimer)
  }
  disposeTrendChart()
})
</script>

<template>
  <div class="freebill-page" v-loading="loading">
    <header class="page-toolbar">
      <div class="title-line">
        <h1>Freebill</h1>
        <el-tooltip placement="bottom-start">
          <template #content>
            <div class="tooltip-content">
              导入支付宝 CSV 和微信支付 Excel 后，本页把账单标准化写入本地 SQLite，只在本机做汇总和明细查询。
            </div>
          </template>
          <el-icon class="help-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-tag v-if="status?.total_records" size="small" effect="plain" type="success">
          {{ formatNumber(status.total_records) }} 条
        </el-tag>
        <el-tag v-else size="small" effect="plain" type="info">未导入</el-tag>
      </div>
      <div class="toolbar-actions">
        <input
          ref="alipayFileInput"
          class="hidden-file-input"
          type="file"
          accept=".csv,text/csv"
          multiple
          @change="handleFileInput('alipay', $event)"
        >
        <input
          ref="wechatFileInput"
          class="hidden-file-input"
          type="file"
          accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          multiple
          @change="handleFileInput('wechat', $event)"
        >
        <el-button
          :icon="Upload"
          :loading="importingSource === 'alipay'"
          size="small"
          plain
          @click="openFilePicker('alipay')"
        >
          支付宝 CSV
        </el-button>
        <el-button
          :icon="Upload"
          :loading="importingSource === 'wechat'"
          size="small"
          plain
          @click="openFilePicker('wechat')"
        >
          微信 Excel
        </el-button>
        <el-button
          :icon="FolderOpened"
          :disabled="!workbookId"
          size="small"
          @click="openWorkbookFile"
        >
          打开星云表格
        </el-button>
        <el-button
          :icon="Refresh"
          :loading="sheetWorkbookLoading"
          size="small"
          plain
          @click="refreshSheetWorkbook"
        >
          刷新表格文件
        </el-button>
        <el-button :icon="Refresh" :loading="loading" size="small" text @click="refreshAll">
          刷新
        </el-button>
      </div>
    </header>

    <FreebillProgramBar
      v-model="backendProgram"
      class="backend-program"
      title="后端筛选"
      help-text="第一层筛选，决定从后端账单库里取哪些候选账单；点击执行后刷新下方统计。"
      :filter-options="filterOptions"
      :show-reset="false"
      :loading="loading"
      @apply="applyBackendProgram"
    />

    <FreebillProgramBar
      v-model="frontendProgram"
      class="frontend-program"
      title="前端筛选"
      help-text="第二层筛选，基于后端筛选结果继续收窄；摘要、趋势、分类和账单明细都按它统计。图表 Ctrl+滚轮缩放会自动写入这里的交易时间范围。"
      :filter-options="filterOptions"
      :loading="loading"
      :show-apply="false"
      :show-reset="false"
    >
      <template #title-actions>
        <el-button
          class="program-title-action"
          size="small"
          text
          @click="resetFrontendProgram"
        >
          最近年份
        </el-button>
      </template>
    </FreebillProgramBar>

    <section class="summary-strip">
      <div
        v-for="item in summaryItems"
        :key="item.key"
        class="summary-item"
        :class="item.tone"
      >
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </div>
      <div v-if="lastImportedText" class="summary-item muted">
        <span>最近导入</span>
        <strong>{{ lastImportedText }}</strong>
      </div>
    </section>

    <main class="analytics-layout">
      <section class="trend-panel">
        <div class="panel-title trend-title">
          <div class="panel-heading">
            <h2>收支趋势</h2>
            <span>{{ trendItems.length }} {{ trendUnitLabel }}</span>
          </div>
          <el-radio-group
            v-model="trendGranularity"
            class="trend-granularity"
            size="small"
            @change="changeTrendGranularity"
          >
            <el-radio-button
              v-for="option in trendGranularityOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </el-radio-button>
          </el-radio-group>
        </div>
        <div
          v-if="trendItems.length"
          class="trend-chart-scroll"
        >
          <div
            ref="trendChartRef"
            class="trend-chart"
            :style="trendChartStyle"
            title="Ctrl+滚轮缩放时间范围"
            @wheel="handleTrendWheel"
          ></div>
        </div>
        <el-empty v-else description="暂无趋势数据" />
      </section>

      <section class="category-panel">
        <div class="panel-title">
          <h2>分类</h2>
          <span>收支 / 分类 / 交易对方</span>
        </div>
        <div v-if="categoryTreeItems.length" class="category-list category-tree-list">
          <template v-for="direction in categoryTreeItems" :key="`direction-${direction.name}`">
            <div class="category-row category-tree-row category-level-0">
              <button
                v-if="hasCategoryChildren(direction)"
                type="button"
                class="category-toggle"
                :title="isCategoryExpanded(direction.name) ? '收起' : '展开'"
                @click="toggleCategoryExpanded(direction.name)"
              >
                {{ isCategoryExpanded(direction.name) ? '-' : '+' }}
              </button>
              <span v-else class="category-toggle-placeholder"></span>
              <div
                class="category-track"
                @mouseenter="showCategoryDetailPopover($event, direction.name)"
                @mousemove="moveCategoryDetailPopover"
                @mouseleave="scheduleCategoryDetailPopoverHide"
              >
                <i
                  class="category-bar"
                  :style="getCategoryBarStyle(direction.name, direction.value, maxCategoryValue)"
                />
                <span class="category-name-label">
                  <span
                    class="category-direction-swatch"
                    :style="{ backgroundColor: getCategoryColor(direction.name) }"
                  ></span>
                  <span class="category-name-text">{{ direction.name }}</span>
                  <span class="category-value-text">{{ formatCategoryBarLabel(direction.value) }}</span>
                </span>
              </div>
            </div>
            <template v-for="category in getCategoryChildren(direction)" :key="`category-${direction.name}-${category.name}`">
              <div
                v-show="isCategoryExpanded(direction.name)"
                class="category-row category-tree-row category-level-1"
              >
                <button
                  v-if="hasCategoryChildren(category)"
                  type="button"
                  class="category-toggle"
                  :title="isCategoryExpanded(direction.name, category.name) ? '收起' : '展开'"
                  @click="toggleCategoryExpanded(direction.name, category.name)"
                >
                  {{ isCategoryExpanded(direction.name, category.name) ? '-' : '+' }}
                </button>
                <span v-else class="category-toggle-placeholder"></span>
                <div
                  class="category-track"
                  @mouseenter="showCategoryDetailPopover($event, direction.name, category.name)"
                  @mousemove="moveCategoryDetailPopover"
                  @mouseleave="scheduleCategoryDetailPopoverHide"
                >
                  <i
                    class="category-bar"
                    :style="getCategoryBarStyle(direction.name, category.value, maxCategoryValue)"
                  />
                  <span class="category-name-label">
                    <span class="category-name-text">{{ category.name }}</span>
                    <span class="category-value-text">{{ formatCategoryBarLabel(category.value) }}</span>
                  </span>
                </div>
              </div>
              <div
                v-for="counterparty in getCategoryChildren(category)"
                v-show="isCategoryExpanded(direction.name) && isCategoryExpanded(direction.name, category.name)"
                :key="`counterparty-${direction.name}-${category.name}-${counterparty.name}`"
                class="category-row category-tree-row category-level-2"
              >
                <span class="category-toggle-placeholder"></span>
                <div
                  class="category-track"
                  @mouseenter="showCategoryDetailPopover($event, direction.name, category.name, counterparty.name)"
                  @mousemove="moveCategoryDetailPopover"
                  @mouseleave="scheduleCategoryDetailPopoverHide"
                >
                  <i
                    class="category-bar"
                    :style="getCategoryBarStyle(direction.name, counterparty.value, maxCategoryValue)"
                  />
                  <span class="category-name-label">
                    <span class="category-name-text">{{ counterparty.name }}</span>
                    <span class="category-value-text">{{ formatCategoryBarLabel(counterparty.value) }}</span>
                  </span>
                </div>
              </div>
            </template>
          </template>
        </div>
        <div v-else class="empty-inline">暂无分类</div>
      </section>
    </main>

    <div
      v-if="categoryDetailPopover.visible"
      class="category-detail-floating"
      :style="categoryDetailPopoverStyle"
      @mouseenter="keepCategoryDetailPopoverVisible"
      @mouseleave="scheduleCategoryDetailPopoverHide"
    >
      <div class="category-detail-popover-content">
        <div class="category-detail-caption">金额 Top 10</div>
        <div v-if="!activeCategoryDetailState || activeCategoryDetailState.loading" class="category-detail-status">
          加载中...
        </div>
        <div v-else-if="activeCategoryDetailState.error" class="category-detail-status is-error">
          {{ activeCategoryDetailState.error }}
        </div>
        <div v-else-if="!activeCategoryDetailState.loaded" class="category-detail-status">
          加载中...
        </div>
        <div v-else-if="!activeCategoryDetailState.items.length" class="category-detail-status">
          暂无明细
        </div>
        <table v-else class="category-detail-table">
          <thead>
            <tr>
              <th>交易时间</th>
              <th>来源</th>
              <th>金额</th>
              <th>商品</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="record in activeCategoryDetailState.items" :key="record.id">
              <td>{{ formatCategoryDetailTime(record.create_time) }}</td>
              <td>{{ formatCategoryDetailText(record.source) }}</td>
              <td class="amount-cell">{{ formatMoney(record.amount) }}</td>
              <td>{{ formatCategoryDetailText(record.product_name) }}</td>
              <td>{{ formatCategoryDetailText(record.remark) }}</td>
              <td class="action-cell">
                <el-button
                  v-if="isFlowOverrideRecord(record)"
                  link
                  size="small"
                  :loading="isRecordOverrideLoading(record)"
                  @click.stop="clearRecordFlowOverride(record)"
                >
                  还原
                </el-button>
                <el-button
                  v-else
                  link
                  size="small"
                  :loading="isRecordOverrideLoading(record)"
                  @click.stop="markRecordAsFlow(record)"
                >
                  流水
                </el-button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <section class="sheet-panel" v-loading="sheetWorkbookLoading">
      <div class="panel-title sheet-title">
        <div class="panel-heading">
          <h2>星云表格</h2>
          <span>{{ sheetWorkbook?.workbook.title || 'Freebill 账单' }}</span>
        </div>
        <span>{{ status?.db_path }}</span>
      </div>
      <el-tabs v-model="activeSheetKey" class="freebill-sheet-tabs">
        <el-tab-pane
          v-for="tab in sheetTabs"
          :key="tab.key"
          :name="tab.key"
        >
          <template #label>
            <span
              class="freebill-sheet-tab-label"
              @contextmenu.capture="event => openSheetTabContextMenu(event, tab)"
            >
              {{ tab.label }}
            </span>
          </template>
          <FreebillProgramBar
            v-if="tab.key === 'records'"
            v-model="sheetViewProgram"
            class="sheet-view-program"
            title="表格筛选"
            help-text="账单明细表自用筛选。它基于上方前端筛选结果继续收窄，只影响表格查看，不影响上方统计。"
            apply-text="即时生效"
            reset-text="清空"
            :show-apply="false"
            :show-reset="sheetViewProgram.rules.length > 0"
            :filter-options="filterOptions"
            :field-options="RECORD_SHEET_FILTER_FIELDS"
            @reset="resetSheetViewProgram"
          />
          <NoteSheetWorkspace
            v-if="workbookId && tab.sheet"
            :ref="instance => setSheetWorkspaceRef(tab.key, instance)"
            class="freebill-sheet-workspace"
            :key="getSheetWorkspaceKey(tab.key)"
            :workbook-id="workbookId"
            :sheet-id="tab.sheet.sheet_id"
            :show-title-input="false"
            :empty-text="tab.emptyText"
            :base-row-filter-programs="tab.key === 'records' ? recordsBaseRowFilterPrograms : null"
            :row-filter-program="tab.key === 'records' ? sheetViewProgram : null"
            default-height-mode="content"
          />
          <el-empty
            v-else
            :description="sheetWorkbookLoading ? '正在刷新星云表格' : tab.emptyText"
          />
        </el-tab-pane>
      </el-tabs>
      <div
        v-if="sheetTabContextMenu.visible"
        class="freebill-sheet-tab-context-menu"
        :style="{ left: `${sheetTabContextMenu.left}px`, top: `${sheetTabContextMenu.top}px` }"
        @contextmenu.prevent.stop
        @mousedown.stop
      >
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          @click="configureSheetFromTabContextMenu"
        >
          配置工作表
        </button>
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          @click="openWorkbookFromTabContextMenu"
        >
          打开完整工作簿
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.freebill-page {
  min-height: 100%;
  padding: 18px 20px 14px;
  background: #f6f8fb;
  color: #1f2937;
}

.page-toolbar,
.title-line,
.toolbar-actions,
.summary-strip,
.panel-title,
.panel-heading,
.category-row {
  display: flex;
  align-items: center;
}

.page-toolbar {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.title-line {
  gap: 8px;
  min-width: 0;
}

h1,
h2 {
  margin: 0;
  line-height: 1.2;
}

h1 {
  font-size: 22px;
  font-weight: 650;
  letter-spacing: 0;
}

h2 {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0;
}

.help-icon {
  color: #64748b;
  cursor: help;
}

.tooltip-content {
  max-width: 320px;
  line-height: 1.6;
}

.toolbar-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.hidden-file-input {
  display: none;
}

.program-title-action {
  height: 24px;
  padding: 0 4px;
}

.backend-program,
.frontend-program {
  margin-bottom: 12px;
}

.summary-strip {
  flex-wrap: wrap;
  gap: 1px;
  margin-bottom: 12px;
  border: 1px solid #dfe5ee;
  background: #dfe5ee;
}

.summary-item {
  min-width: 128px;
  padding: 8px 12px;
  background: #fff;
}

.summary-item span,
.panel-title span,
.category-row span,
.empty-inline {
  color: #64748b;
  font-size: 12px;
}

.summary-item strong {
  display: block;
  margin-top: 3px;
  overflow: hidden;
  color: #111827;
  font-size: 14px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summary-item.income strong {
  color: #15803d;
}

.summary-item.expense strong {
  color: #b91c1c;
}

.summary-item.balance strong {
  color: #1d4ed8;
}

.summary-item.muted strong {
  color: #475569;
}

.analytics-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}

.trend-panel,
.category-panel,
.sheet-panel {
  min-width: 0;
  border: 1px solid #dfe5ee;
  background: #fff;
}

.panel-title {
  justify-content: space-between;
  gap: 12px;
  padding: 11px 12px;
  border-bottom: 1px solid #edf1f6;
}

.panel-heading {
  min-width: 0;
  gap: 8px;
}

.trend-title {
  flex-wrap: wrap;
}

.trend-granularity {
  flex-shrink: 0;
}

.trend-granularity :deep(.el-radio-button__inner) {
  padding: 5px 10px;
}

.category-list {
  display: grid;
  gap: 2px;
  background: transparent;
}

.category-tree-list {
  padding: 8px 0;
}

.category-row {
  min-width: 0;
  padding: 1px 12px;
  background: transparent;
}

.category-tree-row {
  align-items: center;
  gap: 6px;
}

.category-level-1 {
  padding-left: 32px;
}

.category-level-2 {
  padding-left: 52px;
}

.category-toggle,
.category-toggle-placeholder {
  flex: 0 0 18px;
  width: 18px;
  height: 18px;
}

.category-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #cbd5e1;
  border-radius: 3px;
  background: #f8fafc;
  color: #334155;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
}

.category-toggle:hover {
  border-color: #93c5fd;
  color: #1d4ed8;
}

.trend-chart-scroll {
  overflow-x: auto;
  overscroll-behavior: contain;
  padding: 12px;
}

.trend-chart {
  height: 236px;
  min-width: 360px;
}

.category-track {
  position: relative;
  overflow: hidden;
  border-radius: 2px;
  background: #edf1f6;
}

.category-bar {
  position: absolute;
  inset: 0 auto 0 0;
  display: block;
  height: 100%;
}

.category-level-0 .category-track {
  height: 22px;
}

.category-track {
  flex: 1;
  min-width: 0;
  height: 20px;
}

.category-row .category-name-label {
  position: absolute;
  inset: 3px 5px;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  pointer-events: none;
}

.category-direction-swatch {
  flex: 0 0 9px;
  width: 9px;
  height: 9px;
  border-radius: 2px;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.12);
}

.category-row .category-name-text {
  min-width: 0;
  overflow: hidden;
  padding: 0 2px;
  flex: 0 1 auto;
  color: #0f172a;
  font-size: 12px;
  font-weight: 700;
  line-height: 16px;
  text-overflow: ellipsis;
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.55);
  white-space: nowrap;
}

.category-row .category-value-text {
  flex: 0 0 auto;
  padding: 0 2px;
  color: #111827;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  line-height: 14px;
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.58);
  white-space: nowrap;
}

.category-detail-floating {
  position: fixed;
  z-index: 3200;
  overflow: auto;
  padding: 10px;
  border: 1px solid #dbe3ee;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.16);
}

.category-detail-popover-content {
  min-width: 0;
}

.category-detail-caption {
  margin-bottom: 6px;
  color: #64748b;
  font-size: 12px;
  line-height: 1;
}

.category-detail-status {
  padding: 10px 0;
  color: #64748b;
  font-size: 12px;
}

.category-detail-status.is-error {
  color: #b91c1c;
}

.category-detail-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
  font-size: 12px;
}

.category-detail-table th,
.category-detail-table td {
  max-width: 240px;
  overflow: hidden;
  padding: 5px 8px;
  border-bottom: 1px solid #eef2f7;
  text-align: left;
  text-overflow: ellipsis;
  vertical-align: top;
  white-space: nowrap;
}

.category-detail-table th {
  background: #f8fafc;
  color: #475569;
  font-weight: 650;
}

.category-detail-table .amount-cell {
  color: #991b1b;
  font-variant-numeric: tabular-nums;
  font-weight: 650;
}

.category-detail-table .action-cell {
  width: 48px;
  max-width: 48px;
  text-align: center;
}

.category-detail-table .action-cell :deep(.el-button) {
  padding: 0;
}

.empty-inline {
  padding: 18px 12px;
}

.sheet-title > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.freebill-sheet-tabs {
  min-width: 0;
}

.freebill-sheet-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 12px;
}

.freebill-sheet-tabs :deep(.el-tabs__content) {
  min-height: 0;
  overflow: visible;
}

.freebill-sheet-tabs :deep(.el-tab-pane) {
  min-height: 0;
}

.freebill-sheet-tab-label {
  display: inline-flex;
  align-items: center;
  height: 100%;
  margin: 0 -20px;
  padding: 0 20px;
}

.freebill-sheet-tab-context-menu {
  position: fixed;
  z-index: 3000;
  box-sizing: border-box;
  min-width: 136px;
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
  padding: 7px 16px;
  color: #1f2937;
  font-size: 14px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
}

.sheet-tab-context-menu-item:hover {
  background: #f5f7fa;
}

.sheet-view-program {
  margin: 10px 12px;
}

@media (max-width: 980px) {
  .page-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar-actions {
    justify-content: flex-start;
  }

  .analytics-layout {
    grid-template-columns: 1fr;
  }

}
</style>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
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
  createFreebillIncludeAllProgram,
  fetchFreebillDashboardByProgram,
  fetchFreebillFilterOptions,
  fetchFreebillSheetWorkbook,
  fetchFreebillStatus,
  importFreebillFiles,
  refreshFreebillSheetWorkbook,
  upsertFreebillDateRangeRule,
  type FreebillDashboard,
  type FreebillFilterOptions,
  type FreebillImportSource,
  type FreebillProgramChannel,
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

const DAY_MS = 24 * 60 * 60 * 1000
const MIN_TREND_ZOOM_DAYS = 1

const loading = ref(false)
const sheetWorkbookLoading = ref(false)
const importingSource = ref<FreebillImportSource | ''>('')
const status = ref<FreebillStatus | null>(null)
const dashboard = ref<FreebillDashboard | null>(null)
const sheetWorkbook = ref<FreebillSheetWorkbook | null>(null)
const sheetReloadToken = ref(0)
const dataProgram = ref<FreebillProgramChannel>(createFreebillIncludeAllProgram())
const sheetViewProgram = ref<FreebillProgramChannel>(createFreebillIncludeAllProgram())
const filterOptions = ref<FreebillFilterOptions>({
  sources: [],
  directions: [],
  categories: [],
})
const trendGranularity = ref<FreebillTrendGranularity>('month')
const alipayFileInput = ref<HTMLInputElement | null>(null)
const wechatFileInput = ref<HTMLInputElement | null>(null)
const trendChartRef = ref<HTMLDivElement | null>(null)
const sheetWorkspaceRefs = new Map<FreebillSheetTabKey, NoteSheetWorkspaceInstance>()
const sheetTabContextMenu = ref({
  visible: false,
  key: null as FreebillSheetTabKey | null,
  left: 0,
  top: 0,
})
let trendZoomTimer: ReturnType<typeof window.setTimeout> | undefined
let trendChart: ECharts | null = null
let trendResizeObserver: ResizeObserver | undefined

const summary = computed(() => dashboard.value?.summary ?? emptySummary)
const dateRangeText = computed(() => {
  if (!status.value?.min_date || !status.value.max_date) return '暂无账单'
  return `${status.value.min_date} 至 ${status.value.max_date}`
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
  { key: 'raw', label: '原始文件', value: formatNumber(status.value?.raw_file_count ?? 0), tone: 'muted' },
  { key: 'range', label: '跨度', value: dateRangeText.value, tone: 'muted' },
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
    return {
      ...item,
      fullPeriod,
      axisLabel: buildTrendAxisLabel(fullPeriod, previousFullPeriod, index),
    }
  })
})
const trendChartStyle = computed(() => ({
  width: `${Math.max(360, 66 + trendItems.value.length * 44)}px`,
}))
const maxExpenseCategory = computed(() => Math.max(
  0,
  ...(dashboard.value?.expense_categories ?? []).map((item) => Number(item.value || 0)),
))
const maxIncomeCategory = computed(() => Math.max(
  0,
  ...(dashboard.value?.income_categories ?? []).map((item) => Number(item.value || 0)),
))
const workbookId = computed(() => sheetWorkbook.value?.workbook.id ?? null)
const sheetTabs = computed(() => FREEBILL_SHEET_TABS.map((tab) => ({
  ...tab,
  sheet: getSheetItem(tab.key),
})))
const activeSheetKey = ref<(typeof FREEBILL_SHEET_TABS)[number]['key']>('records')
const activeSheetRowFilterProgram = computed(() => (
  activeSheetKey.value === 'records' ? sheetViewProgram.value : null
))
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
  dashboard.value = await fetchFreebillDashboardByProgram({
    program: dataProgram.value,
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
    await Promise.all([
      loadStatus(),
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
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

async function applyDataProgram() {
  syncTrendGranularityToProgramRange()
  await executeQuery()
}

async function changeTrendGranularity() {
  try {
    await loadDashboard()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

function resetSheetViewProgram() {
  sheetViewProgram.value = createFreebillIncludeAllProgram()
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
  const currentProgramRange = getProgramDateRange(dataProgram.value, 'create_time')
  if (
    currentProgramRange
    && nextStart === formatLocalDate(currentProgramRange.start)
    && nextEnd === formatLocalDate(currentProgramRange.end)
  ) return

  dataProgram.value = upsertFreebillDateRangeRule(dataProgram.value, 'create_time', nextStart, nextEnd)
  trendGranularity.value = pickTrendGranularity(nextRange)
  scheduleTrendZoomQuery()
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
  return `¥${normalized.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatCompactMoney(value: number | null | undefined) {
  const numberValue = Number(value || 0)
  const sign = numberValue < 0 ? '-' : ''
  const absValue = Math.abs(numberValue)
  if (absValue >= 10000) return `${sign}${(absValue / 10000).toFixed(1)}万`
  return `${sign}${absValue.toFixed(absValue >= 100 ? 0 : 2)}`
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
  if (trendGranularity.value === 'month') {
    return index === 0 || current.slice(0, 4) !== previous.slice(0, 4)
      ? current
      : current.slice(5, 7)
  }
  if (index === 0 || current.slice(0, 4) !== previous.slice(0, 4)) return current
  if (trendGranularity.value === 'day' && current.slice(0, 7) === previous.slice(0, 7)) {
    return current.slice(8, 10)
  }
  return current.slice(5, 10)
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
      trigger: 'axis',
      axisPointer: {
        type: 'shadow',
      },
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
        interval: (index: number) => items.length <= 18 || index % labelStep === 0,
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
    `<div><span style="display:inline-block;width:7px;height:7px;background:#d78377;margin-right:6px;"></span>支出 ${formatMoney(item.expense)}</div>`,
    `<div><span style="display:inline-block;width:7px;height:7px;background:#86b96f;margin-right:6px;"></span>收入 ${formatMoney(item.income)}</div>`,
    `<div style="color:#64748b;margin-top:4px;">${formatNumber(Number(item.count || 0))} 条</div>`,
  ].join('')
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
  const range = getProgramDateRange(dataProgram.value, 'create_time')
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
  const programRange = getProgramDateRange(dataProgram.value, 'create_time')
  const start = programRange?.start ?? chartRange?.start ?? bounds?.start
  const end = programRange?.end ?? chartRange?.end ?? bounds?.end
  if (!start || !end) return null
  const range = normalizeDateRange({ start, end })
  return bounds ? clampDateRange(range, bounds) : range
}

function getProgramDateRange(program: FreebillProgramChannel, field: string): DateRange | null {
  for (let index = program.rules.length - 1; index >= 0; index -= 1) {
    const rule = program.rules[index]
    if (rule?.matcher.kind !== 'field' || rule.matcher.field !== field || rule.matcher.op !== 'between') continue
    const values = Array.isArray(rule.matcher.values) ? rule.matcher.values : []
    const start = parseLocalDate(String(values[0] || ''))
    const end = parseLocalDate(String(values[1] || ''))
    if (start && end) return normalizeDateRange({ start, end })
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
      v-model="dataProgram"
      class="backend-program"
      title="后端筛选"
      help-text="决定统计图和汇总从后端加载哪些账单；点击执行后生效。规则按顺序执行，后面的包含、排除、筛选可以覆盖前面的结果。"
      :filter-options="filterOptions"
      :show-reset="false"
      :loading="loading"
      @apply="applyDataProgram"
    />

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
          <span>Top 10</span>
        </div>
        <div class="category-columns">
          <div class="category-column">
            <div class="category-heading">支出</div>
            <div v-if="dashboard?.expense_categories.length" class="category-list">
              <div v-for="item in dashboard.expense_categories" :key="`expense-${item.name}`" class="category-row">
                <span class="category-name">{{ item.name }}</span>
                <div class="category-track">
                  <i class="category-bar expense" :style="{ width: barWidth(item.value, maxExpenseCategory) }" />
                </div>
                <span>{{ formatCompactMoney(item.value) }}</span>
              </div>
            </div>
            <div v-else class="empty-inline">暂无支出</div>
          </div>
          <div class="category-column">
            <div class="category-heading">收入</div>
            <div v-if="dashboard?.income_categories.length" class="category-list">
              <div v-for="item in dashboard.income_categories" :key="`income-${item.name}`" class="category-row">
                <span class="category-name">{{ item.name }}</span>
                <div class="category-track">
                  <i class="category-bar income" :style="{ width: barWidth(item.value, maxIncomeCategory) }" />
                </div>
                <span>{{ formatCompactMoney(item.value) }}</span>
              </div>
            </div>
            <div v-else class="empty-inline">暂无收入</div>
          </div>
        </div>
      </section>
    </main>

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
            title="前端筛选"
            help-text="只隐藏当前账单明细工作表里的行，不影响上方统计图和汇总。"
            apply-text="即时生效"
            reset-text="清空"
            :show-apply="false"
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
            :row-filter-program="tab.key === 'records' ? activeSheetRowFilterProgram : null"
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

.backend-program {
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
  gap: 1px;
  background: #edf1f6;
}

.category-row {
  min-width: 0;
  gap: 10px;
  padding: 8px 12px;
  background: #fff;
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
  overflow: hidden;
  border-radius: 2px;
  background: #edf1f6;
}

.category-bar {
  display: block;
  height: 100%;
}

.category-bar.income {
  background: #86b96f;
}

.category-bar.expense {
  background: #d78377;
}

.category-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  min-height: 220px;
}

.category-column + .category-column {
  border-left: 1px solid #edf1f6;
}

.category-heading {
  padding: 8px 12px;
  border-bottom: 1px solid #edf1f6;
  color: #334155;
  font-size: 13px;
  font-weight: 650;
}

.category-name {
  width: 84px;
  overflow: hidden;
  color: #334155;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.category-track {
  flex: 1;
  height: 8px;
}

.category-row > span:last-child {
  width: 58px;
  text-align: right;
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

  .analytics-layout,
  .category-columns {
    grid-template-columns: 1fr;
  }

  .category-column + .category-column {
    border-top: 1px solid #edf1f6;
    border-left: 0;
  }

}
</style>

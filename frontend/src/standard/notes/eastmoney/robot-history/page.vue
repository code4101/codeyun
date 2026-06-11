<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { BarChart, CustomChart, LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { ElMessage } from 'element-plus'
import {
  exportEastmoneyQlibDataset,
  fetchEastmoneyQlibAnalysis,
  fetchEastmoneyQlibHkPoolScreen,
  fetchEastmoneyQlibHkPoolOneLotScoreBacktest,
  fetchEastmoneyQlibOneLotScoreBacktest,
  fetchEastmoneyAkshareHistory,
  fetchEastmoneyAkshareIntraday,
  type EastmoneyAkshareHistoryItem,
  type EastmoneyAkshareIntradayItem,
  type EastmoneyQlibBacktestResult,
  type EastmoneyQlibPoolBacktestItem,
  type EastmoneyQlibPoolBacktestResult,
  type EastmoneyQlibAnalysis,
  type EastmoneyQlibScreenItem,
  type EastmoneyQlibScreenResult,
} from '@/api/eastmoney'
import StandardPagination from '@/components/StandardPagination.vue'
import { formatChineseCompactNumber } from '@/standard/fanxiu/numberFormat'

echarts.use([DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, BarChart, CustomChart, LineChart, CanvasRenderer])

const PREFERENCE_STORAGE_KEY = 'codeyun.eastmoney.robotHistory.preferences'
const BACKTEST_START_DATE = '2025-01-01'
const BACKTEST_END_DATE = '2025-12-31'

type MainPeriodValue = 'intraday' | 'five_day' | 'daily' | 'weekly' | 'monthly'
type MorePeriodValue = 'minute_1' | 'minute_5' | 'minute_15' | 'minute_30' | 'minute_60' | 'minute_120' | 'quarterly' | 'yearly'
type PeriodValue = MainPeriodValue | MorePeriodValue
type WatchTargetKey = 'robot_ph' | 'kingsoft_cloud' | 'xiaomi'
type WatchTarget = {
  key: WatchTargetKey
  label: string
  market: 'SZ' | 'HK'
  symbol: string
  name: string
  startDate: string
}
type RobotHistoryPreferences = {
  targetKey?: WatchTargetKey
  period?: PeriodValue
  adjust?: string
  startDate?: string
  endDate?: string
}

const watchTargets: WatchTarget[] = [
  { key: 'robot_ph', label: 'SZ.159278 机器人PH', market: 'SZ', symbol: '159278', name: '机器人PH', startDate: '1990-01-01' },
  { key: 'kingsoft_cloud', label: 'HK.03896 金山云', market: 'HK', symbol: '03896', name: '金山云', startDate: '1990-01-01' },
  { key: 'xiaomi', label: 'HK.01810 小米集团', market: 'HK', symbol: '01810', name: '小米集团', startDate: '1990-01-01' },
]
const legacyDefaultStartDates = new Set(['2025-08-12', '2020-05-08', '2018-07-09'])

const periodValues = new Set<PeriodValue>([
  'intraday',
  'five_day',
  'daily',
  'weekly',
  'monthly',
  'minute_1',
  'minute_5',
  'minute_15',
  'minute_30',
  'minute_60',
  'minute_120',
  'quarterly',
  'yearly',
])
const adjustValues = new Set(['', 'qfq', 'hfq'])
const targetKeys = new Set<WatchTargetKey>(watchTargets.map((target) => target.key))
const morePeriodValues = new Set<MorePeriodValue>([
  'minute_1',
  'minute_5',
  'minute_15',
  'minute_30',
  'minute_60',
  'minute_120',
  'quarterly',
  'yearly',
])

function readPreferences(): RobotHistoryPreferences {
  try {
    const raw = window.localStorage.getItem(PREFERENCE_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as RobotHistoryPreferences
    return parsed && typeof parsed === 'object' ? parsed : {}
  }
  catch {
    return {}
  }
}

function writePreferences() {
  if (previewTarget.value) return
  const preferences: RobotHistoryPreferences = {
    targetKey: targetKey.value,
    period: period.value,
    adjust: adjust.value,
    startDate: startDate.value,
    endDate: endDate.value,
  }
  window.localStorage.setItem(PREFERENCE_STORAGE_KEY, JSON.stringify(preferences))
}

const savedPreferences = readPreferences()

const targetKey = ref<WatchTargetKey>(
  targetKeys.has(savedPreferences.targetKey as WatchTargetKey) ? savedPreferences.targetKey as WatchTargetKey : 'robot_ph',
)
const previewTarget = ref<WatchTarget | null>(null)
const selectedWatchTarget = computed(() => watchTargets.find((target) => target.key === targetKey.value) ?? watchTargets[0])
const activeTarget = computed(() => previewTarget.value ?? selectedWatchTarget.value)
const activeTargetSource = computed(() => previewTarget.value ? '股票池临时查看' : '自选')
const endDate = ref(savedPreferences.endDate || '')
const period = ref<PeriodValue>(periodValues.has(savedPreferences.period as PeriodValue) ? savedPreferences.period as PeriodValue : 'intraday')
const morePeriod = ref<MorePeriodValue | ''>(
  morePeriodValues.has(period.value as MorePeriodValue) ? period.value as MorePeriodValue : '',
)
const startDate = ref(resolveInitialStartDate(savedPreferences.startDate, period.value, activeTarget.value.startDate))
const adjust = ref(adjustValues.has(savedPreferences.adjust ?? '') ? savedPreferences.adjust ?? '' : '')
const loading = ref(false)
const qlibExporting = ref(false)
const qlibAnalysisLoading = ref(false)
const qlibAnalysis = ref<EastmoneyQlibAnalysis | null>(null)
const hkPoolLoading = ref(false)
const hkPoolRefreshing = ref(false)
const hkPoolScreen = ref<EastmoneyQlibScreenResult | null>(null)
const backtestLoading = ref(false)
const backtestResult = ref<EastmoneyQlibBacktestResult | null>(null)
const poolBacktestLoading = ref(false)
const poolBacktestRefreshing = ref(false)
const poolBacktestResult = ref<EastmoneyQlibPoolBacktestResult | null>(null)
const hkPoolPage = ref(1)
const hkPoolPageSize = ref(20)
const loadedAt = ref('')
const rows = ref<EastmoneyAkshareHistoryItem[]>([])
const intradayRows = ref<EastmoneyAkshareIntradayItem[]>([])
const chartRef = ref<HTMLDivElement | null>(null)
const backtestChartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let backtestChart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let loadSequence = 0
let qlibLoadSequence = 0
let intradayRefreshTimer: number | null = null

const periodOptions: { label: string, value: MainPeriodValue }[] = [
  { label: '分时', value: 'intraday' },
  { label: '五日', value: 'five_day' },
  { label: '日K', value: 'daily' },
  { label: '周K', value: 'weekly' },
  { label: '月K', value: 'monthly' },
]

const morePeriodOptions: { label: string, value: MorePeriodValue }[] = [
  { label: '1分钟', value: 'minute_1' },
  { label: '5分钟', value: 'minute_5' },
  { label: '15分钟', value: 'minute_15' },
  { label: '30分钟', value: 'minute_30' },
  { label: '60分钟', value: 'minute_60' },
  { label: '120分钟', value: 'minute_120' },
  { label: '季K', value: 'quarterly' },
  { label: '年K', value: 'yearly' },
]

const adjustOptions = [
  { label: '不复权', value: '' },
  { label: '前复权', value: 'qfq' },
  { label: '后复权', value: 'hfq' },
]

const isIntraday = computed(() => period.value === 'intraday' || period.value === 'five_day' || period.value.startsWith('minute_'))
const selectedPeriodLabel = computed(() => {
  return [...periodOptions, ...morePeriodOptions].find((option) => option.value === period.value)?.label ?? '分时'
})
const latestRow = computed(() => rows.value.at(-1) ?? null)
const latestIntradayRow = computed(() => intradayRows.value.at(-1) ?? null)
const firstRow = computed(() => rows.value[0] ?? null)
const hkPoolTotal = computed(() => hkPoolScreen.value?.items.length ?? 0)
const hkPoolPageCount = computed(() => Math.max(1, Math.ceil(hkPoolTotal.value / Math.max(hkPoolPageSize.value, 1))))
const hkPoolRows = computed(() => {
  const items = hkPoolScreen.value?.items ?? []
  const start = (hkPoolPage.value - 1) * hkPoolPageSize.value
  return items.slice(start, start + hkPoolPageSize.value)
})
const closeChange = computed(() => {
  if (!firstRow.value?.close || !latestRow.value?.close) return null
  return latestRow.value.close - firstRow.value.close
})
const closeChangeRate = computed(() => {
  if (!firstRow.value?.close || closeChange.value == null) return null
  return closeChange.value / firstRow.value.close * 100
})
const intradayChange = computed(() => {
  if (!intradayRows.value[0]?.close || !latestIntradayRow.value?.close) return null
  return latestIntradayRow.value.close - intradayRows.value[0].close
})
const intradayChangeRate = computed(() => {
  if (!intradayRows.value[0]?.close || intradayChange.value == null) return null
  return intradayChange.value / intradayRows.value[0].close * 100
})
const totalAmount = computed(() => rows.value.reduce((total, row) => total + (row.amount ?? 0), 0))
const intradayTotalAmount = computed(() => intradayRows.value.reduce((total, row) => total + (row.amount ?? 0), 0))
const maxVolumeRow = computed(() => rows.value.reduce<EastmoneyAkshareHistoryItem | null>((best, row) => {
  if (!best) return row
  return (row.volume ?? 0) > (best.volume ?? 0) ? row : best
}, null))
const maxIntradayVolumeRow = computed(() => intradayRows.value.reduce<EastmoneyAkshareIntradayItem | null>((best, row) => {
  if (!best) return row
  return (row.volume ?? 0) > (best.volume ?? 0) ? row : best
}, null))
const qlibScoreRules = computed(() => qlibAnalysis.value?.scoring_rules?.length
  ? qlibAnalysis.value.scoring_rules
  : hkPoolScreen.value?.scoring_rules ?? [])
const backtestOpenLots = computed(() => {
  const result = backtestResult.value
  if (!result?.lot_size) return 0
  return Math.floor(result.open_position_shares / result.lot_size)
})
const backtestMaxScore = computed(() => {
  const scores = (backtestResult.value?.points ?? [])
    .map((point) => point.score)
    .filter((score): score is number => score != null && Number.isFinite(score))
  return scores.length ? Math.max(...scores) : null
})
const backtestTriggerCount = computed(() => {
  const result = backtestResult.value
  if (!result) return 0
  return result.points.filter((point) => (point.score ?? -1) >= result.score_threshold).length
})
const poolBacktestWinners = computed(() => {
  return (poolBacktestResult.value?.items ?? [])
    .filter((item) => item.total_profit > 0)
    .slice(0, 12)
})
const poolBacktestLosers = computed(() => {
  return [...(poolBacktestResult.value?.items ?? [])]
    .filter((item) => item.total_profit < 0)
    .sort((left, right) => left.total_profit - right.total_profit)
    .slice(0, 12)
})
const poolBacktestReturnPercent = computed(() => {
  const result = poolBacktestResult.value
  if (!result?.max_capital_used) return null
  return result.total_profit / result.max_capital_used * 100
})

function formatNumber(value: number | null | undefined, digits = 3) {
  if (value == null || !Number.isFinite(value)) return '-'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function formatPercent(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-'
  return `${value.toFixed(2)}%`
}

function formatAmount(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-'
  return formatChineseCompactNumber(value)
}

function formatVolume(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-'
  return formatChineseCompactNumber(value)
}

function formatRatio(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-'
  return value.toFixed(2)
}

function formatCurrency(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-'
  if (value < 0) return `-${formatChineseCompactNumber(Math.abs(value))}`
  return formatChineseCompactNumber(value)
}

function qlibSignalClass(signal: string | undefined) {
  if (signal === '偏积极') return 'positive'
  if (signal === '偏谨慎') return 'negative'
  return ''
}

function averagePriceFromAmountVolume(amount: number | null | undefined, volume: number | null | undefined) {
  if (amount == null || volume == null || !Number.isFinite(amount) || !Number.isFinite(volume) || volume === 0) {
    return null
  }
  return amount / (volume * 100)
}

type ChartTooltipItem = {
  axisValueLabel?: string
  marker?: string
  seriesName?: string
  value?: unknown
}

function formatChartTooltip(params: unknown) {
  const items = (Array.isArray(params) ? params : [params])
    .filter((item): item is ChartTooltipItem => Boolean(item) && typeof item === 'object')
  const title = items[0]?.axisValueLabel ? `${items[0].axisValueLabel}<br>` : ''
  const lines = items.map((item) => {
    if (item.seriesName === 'K线' && Array.isArray(item.value)) {
      const [, open, close, low, high, volume] = item.value.map((value) => Number(value))
      return `${item.marker ?? ''}${item.seriesName}<span style="float:right;margin-left:16px;font-weight:600">开 ${formatNumber(open)} 收 ${formatNumber(close)} 低 ${formatNumber(low)} 高 ${formatNumber(high)} 量 ${formatVolume(volume)}</span>`
    }
    const numericValue = typeof item.value === 'number' ? item.value : Number(item.value)
    const value = Number.isFinite(numericValue)
      ? item.seriesName === '成交量'
        ? formatVolume(numericValue)
        : formatNumber(numericValue)
      : String(item.value ?? '')
    return `${item.marker ?? ''}${item.seriesName ?? ''}<span style="float:right;margin-left:16px;font-weight:600">${value}</span>`
  })
  return `${title}${lines.join('<br>')}`
}

function formatDateForApi(value: string) {
  return value ? value.slice(0, 10) : ''
}

function formatDateValue(date: Date) {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

function shiftDate(years: number) {
  const date = new Date()
  date.setFullYear(date.getFullYear() - years)
  return formatDateValue(date)
}

function defaultStartDateForPeriod(value: PeriodValue, fallbackStartDate: string) {
  if (value === 'daily') return shiftDate(1)
  if (value === 'weekly') return shiftDate(3)
  if (value === 'monthly') return shiftDate(10)
  if (value === 'quarterly') return shiftDate(20)
  if (value === 'yearly') return fallbackStartDate
  return fallbackStartDate
}

function resolveInitialStartDate(savedStartDate: string | undefined, value: PeriodValue, fallbackStartDate: string) {
  if (savedStartDate && !legacyDefaultStartDates.has(savedStartDate) && savedStartDate !== fallbackStartDate) {
    return savedStartDate
  }
  return defaultStartDateForPeriod(value, fallbackStartDate)
}

function resetLocalHistoryRange() {
  startDate.value = defaultStartDateForPeriod(period.value, activeTarget.value.startDate)
  endDate.value = ''
}

function loadLocalHistoryRange() {
  resetLocalHistoryRange()
  void loadHistory()
}

function loadFullHistory() {
  startDate.value = activeTarget.value.startDate
  endDate.value = ''
  void loadHistory()
}

function formatKlineDateLabel(value: string) {
  if (period.value === 'monthly') return value.slice(0, 7)
  if (period.value === 'yearly') return value.slice(0, 4)
  return value
}

function historyPeriodForApi() {
  if (period.value === 'quarterly' || period.value === 'yearly') return period.value
  return period.value === 'weekly' || period.value === 'monthly' ? period.value : 'daily'
}

function intradayParamsForApi() {
  if (period.value === 'five_day') return { period: '1', day_count: 5 }
  if (period.value === 'minute_5') return { period: '5', day_count: 1 }
  if (period.value === 'minute_15') return { period: '15', day_count: 1 }
  if (period.value === 'minute_30') return { period: '30', day_count: 1 }
  if (period.value === 'minute_60') return { period: '60', day_count: 1 }
  if (period.value === 'minute_120') return { period: '120', day_count: 1 }
  return { period: '1', day_count: 1 }
}

function selectMorePeriod(value: MorePeriodValue | '') {
  if (!value) return
  period.value = value
}

async function loadHistory() {
  if (isIntraday.value) {
    await loadIntraday()
    return
  }
  const sequence = loadSequence + 1
  loadSequence = sequence
  loading.value = true
  rows.value = []
  intradayRows.value = []
  loadedAt.value = ''
  try {
    const result = await fetchEastmoneyAkshareHistory({
      market: activeTarget.value.market,
      symbol: activeTarget.value.symbol,
      name: activeTarget.value.name,
      period: historyPeriodForApi(),
      start_date: formatDateForApi(startDate.value),
      end_date: formatDateForApi(endDate.value),
      adjust: adjust.value,
      refresh: false,
    })
    if (sequence !== loadSequence) return
    rows.value = result.items
    loadedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
    await nextTick()
    renderChart()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取 AKShare 行情失败：${message}`)
  } finally {
    if (sequence === loadSequence) {
      loading.value = false
    }
  }
}

async function loadIntraday() {
  const sequence = loadSequence + 1
  loadSequence = sequence
  loading.value = true
  rows.value = []
  try {
    const intradayParams = intradayParamsForApi()
    const result = await fetchEastmoneyAkshareIntraday({
      market: activeTarget.value.market,
      symbol: activeTarget.value.symbol,
      name: activeTarget.value.name,
      period: intradayParams.period,
      day_count: intradayParams.day_count,
    })
    if (sequence !== loadSequence) return
    intradayRows.value = result.items
    loadedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
    await nextTick()
    renderChart()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取 AKShare 分时行情失败：${message}`)
  } finally {
    if (sequence === loadSequence) {
      loading.value = false
    }
  }
}

async function exportQlibDataset() {
  qlibExporting.value = true
  try {
    const result = await exportEastmoneyQlibDataset({ refresh: true })
    const failedCount = result.items.length - result.exported_count
    const suffix = failedCount > 0 ? `，${failedCount} 个标的失败` : ''
    ElMessage.success(`Qlib CSV 已导出 ${result.exported_count} 个标的${suffix}`)
    await loadQlibAnalysis(true)
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`导出 Qlib 数据失败：${message}`)
  }
  finally {
    qlibExporting.value = false
  }
}

async function loadQlibAnalysis(refresh = false) {
  const sequence = qlibLoadSequence + 1
  qlibLoadSequence = sequence
  qlibAnalysisLoading.value = true
  try {
    const result = await fetchEastmoneyQlibAnalysis({
      market: activeTarget.value.market,
      symbol: activeTarget.value.symbol,
      name: activeTarget.value.name,
      start_date: activeTarget.value.startDate,
      refresh,
    })
    if (sequence !== qlibLoadSequence) return
    qlibAnalysis.value = result
  }
  catch (error) {
    if (sequence !== qlibLoadSequence) return
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取 Qlib 分析失败：${message}`)
  }
  finally {
    if (sequence === qlibLoadSequence) {
      qlibAnalysisLoading.value = false
    }
  }
}

async function loadHkPoolScreen(refresh = false) {
  if (refresh) {
    hkPoolRefreshing.value = true
  }
  else {
    hkPoolLoading.value = true
  }
  try {
    hkPoolScreen.value = await fetchEastmoneyQlibHkPoolScreen({
      refresh,
      start_date: '1990-01-01',
    })
    if (hkPoolPage.value > hkPoolPageCount.value) {
      hkPoolPage.value = hkPoolPageCount.value
    }
    if (refresh) {
      ElMessage.success(`股票池评分完成：${hkPoolScreen.value.analyzed_count}/${hkPoolScreen.value.target_count}`)
    }
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取港股股票池评分失败：${message}`)
  }
  finally {
    hkPoolLoading.value = false
    hkPoolRefreshing.value = false
  }
}

async function loadXiaomiBacktest() {
  backtestLoading.value = true
  try {
    backtestResult.value = await fetchEastmoneyQlibOneLotScoreBacktest({
      market: 'HK',
      symbol: '01810',
      name: '小米集团',
      start_date: BACKTEST_START_DATE,
      end_date: BACKTEST_END_DATE,
      lot_size: 200,
      score_threshold: 84,
      take_profit_percent: 5,
      cost_rate: 0.01,
      force_liquidate_end: true,
      refresh: false,
    })
    await nextTick()
    renderBacktestChart()
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取小米策略回测失败：${message}`)
  }
  finally {
    backtestLoading.value = false
  }
}

async function loadPoolBacktest(refresh = false) {
  if (refresh) {
    poolBacktestRefreshing.value = true
  }
  else {
    poolBacktestLoading.value = true
  }
  try {
    poolBacktestResult.value = await fetchEastmoneyQlibHkPoolOneLotScoreBacktest({
      refresh,
      detail_limit: 5000,
      start_date: BACKTEST_START_DATE,
      end_date: BACKTEST_END_DATE,
      score_threshold: 84,
      take_profit_percent: 5,
      cost_rate: 0.01,
      force_liquidate_end: true,
    })
    if (refresh) {
      ElMessage.success(`港股池回测完成：${poolBacktestResult.value.tested_count}/${poolBacktestResult.value.target_count}`)
    }
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取港股池回测失败：${message}`)
  }
  finally {
    poolBacktestLoading.value = false
    poolBacktestRefreshing.value = false
  }
}

function handleHkPoolPageSizeChange() {
  hkPoolPage.value = 1
}

function qlibAnalysisFromPoolRow(row: EastmoneyQlibScreenItem): EastmoneyQlibAnalysis {
  return {
    market: row.market,
    symbol: row.symbol,
    name: row.name,
    qlib_symbol: row.qlib_symbol,
    row_count: row.row_count,
    source: row.source,
    start_date: row.start_date,
    end_date: row.end_date,
    latest_close: row.latest_close,
    latest_change_percent: row.latest_change_percent,
    return_5: row.return_5,
    return_20: row.return_20,
    return_60: row.return_60,
    ma_5: null,
    ma_20: null,
    ma_60: null,
    ma_20_distance: row.ma_20_distance,
    volatility_20: row.volatility_20,
    max_drawdown: row.max_drawdown,
    volume_ratio_5_20: row.volume_ratio_5_20,
    score: row.score,
    signal: row.signal,
    model_status: '港股股票池缓存因子摘要',
    scoring_rules: hkPoolScreen.value?.scoring_rules ?? [],
    error: row.error,
  }
}

function previewPoolTarget(row: EastmoneyQlibScreenItem) {
  previewTarget.value = {
    key: `pool_${row.market}_${row.symbol}` as WatchTargetKey,
    label: `${row.market}.${row.symbol} ${row.name}`,
    market: row.market === 'HK' ? 'HK' : 'SZ',
    symbol: row.symbol,
    name: row.name,
    startDate: '1990-01-01',
  }
  resetLocalHistoryRange()
  qlibLoadSequence += 1
  qlibAnalysis.value = qlibAnalysisFromPoolRow(row)
  qlibAnalysisLoading.value = false
  startIntradayRefreshTimer()
  void loadHistory()
}

function previewPoolBacktestItem(row: EastmoneyQlibPoolBacktestItem) {
  previewPoolTarget({
    pool: 'hk_pool',
    market: row.market,
    symbol: row.symbol,
    name: row.name,
    qlib_symbol: `${row.market}${row.symbol}`.toLowerCase(),
    score: null,
    signal: '',
    row_count: 0,
    source: 'backtest',
    start_date: row.start_date,
    end_date: row.end_date,
    latest_close: null,
    latest_change_percent: null,
    return_5: null,
    return_20: null,
    return_60: null,
    ma_20_distance: null,
    volatility_20: null,
    max_drawdown: null,
    volume_ratio_5_20: null,
    error: row.error,
  })
}

function returnToWatchTarget() {
  if (!previewTarget.value) return
  previewTarget.value = null
  resetLocalHistoryRange()
  startIntradayRefreshTimer()
  void loadHistory()
  void loadQlibAnalysis(false)
}

function ensureChart() {
  if (!chartRef.value) return null
  if (!chart) {
    chart = echarts.init(chartRef.value)
  }
  return chart
}

function ensureBacktestChart() {
  if (!backtestChartRef.value) return null
  if (!backtestChart) {
    backtestChart = echarts.init(backtestChartRef.value)
  }
  return backtestChart
}

function defaultKlineZoomRange(total: number) {
  if (total <= 0) return { start: 0, end: 100 }
  const visibleCountByPeriod: Partial<Record<PeriodValue, number>> = {
    daily: 180,
    weekly: 156,
    monthly: 120,
    quarterly: 80,
    yearly: 40,
  }
  const visibleCount = visibleCountByPeriod[period.value] ?? total
  if (total <= visibleCount) return { start: 0, end: 100 }
  return {
    start: Math.max(0, (total - visibleCount) / total * 100),
    end: 100,
  }
}

function renderChart() {
  const instance = ensureChart()
  if (!instance) return
  if (isIntraday.value) {
    renderIntradayChart(instance)
    return
  }
  const dates = rows.value.map((row) => formatKlineDateLabel(row.date))
  const maxVolume = Math.max(...rows.value.map((row) => row.volume ?? 0), 1)
  const zoomRange = defaultKlineZoomRange(rows.value.length)
  const averagePriceData = rows.value.map((row) => averagePriceFromAmountVolume(row.amount, row.volume))
  const klineData = rows.value.map((row, index) => [
    index,
    row.open,
    row.close,
    row.low,
    row.high,
    row.volume ?? 0,
  ])
  instance.setOption({
    animation: false,
    color: ['#d92d20', '#d97706'],
    tooltip: {
      trigger: 'axis',
      formatter: formatChartTooltip,
    },
    legend: {
      top: 0,
      right: 4,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: '#52637a', fontSize: 12 },
    },
    grid: [
      { left: 58, right: 28, top: 36, bottom: 78 },
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0],
        start: zoomRange.start,
        end: zoomRange.end,
        filterMode: 'filter',
      },
      {
        type: 'slider',
        xAxisIndex: [0],
        start: zoomRange.start,
        end: zoomRange.end,
        filterMode: 'filter',
        height: 24,
        bottom: 20,
        brushSelect: false,
        showDetail: true,
        showDataShadow: false,
        borderColor: '#dce5f0',
        fillerColor: 'rgba(31, 111, 235, 0.12)',
        handleStyle: {
          color: '#ffffff',
          borderColor: '#8aa4c8',
        },
        textStyle: { color: '#607086', fontSize: 11 },
      },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        boundaryGap: true,
        axisTick: { alignWithLabel: true },
        axisLabel: { color: '#607086' },
      },
    ],
    yAxis: [
      { type: 'value', scale: true, axisLabel: { color: '#607086' }, splitLine: { lineStyle: { color: '#edf1f7' } } },
    ],
    series: [
      {
        name: 'K线',
        type: 'custom',
        data: klineData,
        encode: { x: 0, y: [1, 2, 3, 4] },
        renderItem: (params: any, api: any) => {
          const xValue = api.value(0)
          const open = api.value(1)
          const close = api.value(2)
          const low = api.value(3)
          const high = api.value(4)
          const volume = api.value(5)
          const x = api.coord([xValue, close])[0]
          const openY = api.coord([xValue, open])[1]
          const closeY = api.coord([xValue, close])[1]
          const lowY = api.coord([xValue, low])[1]
          const highY = api.coord([xValue, high])[1]
          const categoryWidth = api.size([1, 0])[0]
          const normalized = Math.max(0.12, Math.min(1, volume / maxVolume))
          const width = Math.max(4, categoryWidth * (0.18 + normalized * 0.62))
          const color = close >= open ? '#d92d20' : '#079455'
          const bodyTop = Math.min(openY, closeY)
          const bodyHeight = Math.max(2, Math.abs(closeY - openY))
          const clipRect = params.coordSys
          return {
            type: 'group',
            children: [
              {
                type: 'line',
                shape: { x1: x, y1: highY, x2: x, y2: lowY },
                style: { stroke: color, lineWidth: 1 },
              },
              {
                type: 'rect',
                shape: echarts.graphic.clipRectByRect(
                  { x: x - width / 2, y: bodyTop, width, height: bodyHeight },
                  { x: clipRect.x, y: clipRect.y, width: clipRect.width, height: clipRect.height },
                ),
                style: { fill: color, stroke: color },
              },
            ],
          }
        },
      },
      {
        name: '均价',
        type: 'line',
        data: averagePriceData,
        symbol: 'none',
        smooth: true,
        lineStyle: { color: '#d97706', width: 1.5 },
      },
    ],
  }, true)
}

function renderIntradayChart(instance: echarts.ECharts) {
  const showDateInIntradayAxis = period.value === 'five_day'
  const times = intradayRows.value.map((row) => {
    const date = row.time.slice(5, 10)
    const time = row.time.slice(11, 16)
    return showDateInIntradayAxis ? `${date} ${time}` : time
  })
  const showIntradayTimeLabel = (_index: number, value: string) => {
    const time = value.slice(-5)
    return time === '09:30'
      || time === '10:30'
      || time === '11:30'
      || time === '13:30'
      || time === '14:30'
      || time === '15:00'
      || time === '16:00'
  }
  instance.setOption({
    animation: false,
    color: ['#1f6feb', '#d97706', '#6b7280'],
    tooltip: {
      trigger: 'axis',
      formatter: formatChartTooltip,
    },
    legend: {
      top: 0,
      right: 4,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: '#52637a', fontSize: 12 },
    },
    grid: [
      { left: 58, right: 28, top: 36, height: 220 },
      { left: 58, right: 28, top: 290, height: 78 },
    ],
    xAxis: [
      {
        type: 'category',
        data: times,
        boundaryGap: false,
        axisLabel: { show: false },
        axisTick: { show: false },
      },
      {
        type: 'category',
        data: times,
        gridIndex: 1,
        axisLabel: {
          color: '#607086',
          interval: showIntradayTimeLabel,
          hideOverlap: true,
          margin: 10,
        },
      },
    ],
    yAxis: [
      {
        type: 'value',
        scale: true,
        splitNumber: 4,
        axisLabel: { color: '#607086', margin: 10 },
        splitLine: { lineStyle: { color: '#edf1f7' } },
      },
      {
        type: 'value',
        gridIndex: 1,
        splitNumber: 3,
        axisLabel: {
          color: '#607086',
          margin: 10,
          formatter: (value: number) => formatChineseCompactNumber(value),
        },
        splitLine: { lineStyle: { color: '#edf1f7' } },
      },
    ],
    series: [
      {
        name: '最新',
        type: 'line',
        data: intradayRows.value.map((row) => row.close),
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2 },
      },
      {
        name: '均价',
        type: 'line',
        data: intradayRows.value.map((row) => row.average_price),
        symbol: 'none',
        lineStyle: { width: 1 },
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: intradayRows.value.map((row) => row.volume),
        barMaxWidth: 12,
      },
    ],
  }, true)
}

function renderBacktestChart() {
  const instance = ensureBacktestChart()
  const result = backtestResult.value
  if (!instance || !result) return
  const dates = result.points.map((point) => point.date)
  instance.setOption({
    animation: false,
    color: ['#1f6feb', '#079455', '#d97706'],
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const items = (Array.isArray(params) ? params : [params]) as ChartTooltipItem[]
        const index = Number((items[0] as any)?.dataIndex ?? 0)
        const point = result.points[index]
        const title = point?.date ? `${point.date}<br>` : ''
        const action = point?.action ? `<br>动作<span style="float:right;margin-left:16px;font-weight:600">${point.action}</span>` : ''
        const lines = items.map((item) => {
          const numericValue = typeof item.value === 'number' ? item.value : Number(item.value)
          return `${item.marker ?? ''}${item.seriesName ?? ''}<span style="float:right;margin-left:16px;font-weight:600">${formatCurrency(numericValue)}</span>`
        })
        return `${title}${lines.join('<br>')}${action}`
      },
    },
    legend: {
      top: 0,
      right: 4,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: '#52637a', fontSize: 12 },
    },
    grid: { left: 64, right: 28, top: 34, bottom: 54 },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0, start: 0, end: 100, filterMode: 'filter' },
      {
        type: 'slider',
        xAxisIndex: 0,
        start: 0,
        end: 100,
        filterMode: 'filter',
        height: 18,
        bottom: 16,
        brushSelect: false,
        showDataShadow: false,
        borderColor: '#dce5f0',
        fillerColor: 'rgba(31, 111, 235, 0.12)',
        textStyle: { color: '#607086', fontSize: 11 },
      },
    ],
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#607086', hideOverlap: true },
      axisTick: { alignWithLabel: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#607086', formatter: (value: number) => formatChineseCompactNumber(value) },
      splitLine: { lineStyle: { color: '#edf1f7' } },
    },
    series: [
      {
        name: '净收益',
        type: 'line',
        data: result.points.map((point) => point.equity),
        symbol: 'none',
        lineStyle: { width: 2 },
      },
      {
        name: '持仓市值',
        type: 'line',
        data: result.points.map((point) => point.position_value),
        symbol: 'none',
        lineStyle: { width: 1.5 },
      },
      {
        name: '现金流',
        type: 'line',
        data: result.points.map((point) => point.cash),
        symbol: 'none',
        lineStyle: { width: 1, type: 'dashed' },
      },
    ],
  }, true)
}

function startIntradayRefreshTimer() {
  stopIntradayRefreshTimer()
  if (!isIntraday.value) return
  intradayRefreshTimer = window.setInterval(() => {
    void loadIntraday()
  }, 60000)
}

function stopIntradayRefreshTimer() {
  if (intradayRefreshTimer != null) {
    window.clearInterval(intradayRefreshTimer)
    intradayRefreshTimer = null
  }
}

onMounted(() => {
  void loadHistory()
  void loadQlibAnalysis(false)
  void loadHkPoolScreen(false)
  void loadXiaomiBacktest()
  void loadPoolBacktest(false)
  startIntradayRefreshTimer()
  if (chartRef.value || backtestChartRef.value) {
    resizeObserver = new ResizeObserver(() => {
      chart?.resize()
      backtestChart?.resize()
    })
  }
  if (chartRef.value && resizeObserver) {
    resizeObserver.observe(chartRef.value)
  }
  if (backtestChartRef.value && resizeObserver) {
    resizeObserver.observe(backtestChartRef.value)
  }
})

onBeforeUnmount(() => {
  stopIntradayRefreshTimer()
  resizeObserver?.disconnect()
  chart?.dispose()
  backtestChart?.dispose()
  chart = null
  backtestChart = null
})

watch(rows, () => {
  void nextTick(renderChart)
})

watch(backtestResult, () => {
  void nextTick(renderBacktestChart)
})

watch(period, () => {
  morePeriod.value = morePeriodValues.has(period.value as MorePeriodValue) ? period.value as MorePeriodValue : ''
  resetLocalHistoryRange()
  startIntradayRefreshTimer()
  void loadHistory()
})

watch(adjust, () => {
  startIntradayRefreshTimer()
  void loadHistory()
})

watch(targetKey, (_value, oldValue) => {
  previewTarget.value = null
  resetLocalHistoryRange()
  if (oldValue != null) {
    startIntradayRefreshTimer()
    void loadHistory()
    void loadQlibAnalysis(false)
  }
})

watch([targetKey, period, adjust, startDate, endDate], () => {
  writePreferences()
})
</script>

<template>
  <div class="robot-history-page">
    <header class="robot-history-toolbar">
      <div class="robot-history-title">
        <h1>{{ activeTarget.name }}</h1>
        <div class="active-target-meta">
          <span>{{ activeTargetSource }} · {{ activeTarget.market }}.{{ activeTarget.symbol }}</span>
          <el-button v-if="previewTarget" link type="primary" @click="returnToWatchTarget">
            返回自选
          </el-button>
        </div>
      </div>
      <div class="robot-history-controls">
        <div class="watch-target-control">
          <span>自选</span>
          <el-select v-model="targetKey" class="target-select" aria-label="观察标的">
            <el-option
              v-for="target in watchTargets"
              :key="target.key"
              :label="target.label"
              :value="target.key"
            />
          </el-select>
        </div>
        <el-segmented v-model="period" :options="periodOptions" />
        <el-select
          v-model="morePeriod"
          class="more-period-select"
          placeholder="更多"
          aria-label="更多周期"
          @change="selectMorePeriod"
        >
          <el-option
            v-for="option in morePeriodOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-select v-if="!isIntraday" v-model="adjust" class="adjust-select" aria-label="复权类型">
          <el-option
            v-for="option in adjustOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-date-picker v-if="!isIntraday" v-model="startDate" type="date" value-format="YYYY-MM-DD" />
        <el-date-picker v-if="!isIntraday" v-model="endDate" type="date" value-format="YYYY-MM-DD" placeholder="今天" />
        <el-button v-if="!isIntraday" @click="loadLocalHistoryRange">最近区间</el-button>
        <el-button v-if="!isIntraday" @click="loadFullHistory">完整历史</el-button>
        <el-button :loading="qlibExporting" @click="exportQlibDataset">Qlib分析</el-button>
        <el-button type="primary" :loading="loading" @click="loadHistory">查询</el-button>
      </div>
    </header>

    <section class="robot-history-metrics">
      <div>
        <span>{{ isIntraday ? '最新价' : '最新收盘' }}</span>
        <strong>{{ formatNumber(isIntraday ? latestIntradayRow?.close : latestRow?.close) }}</strong>
      </div>
      <div>
        <span>{{ isIntraday ? `${selectedPeriodLabel}涨跌` : '区间涨跌' }}</span>
        <strong :class="{ positive: ((isIntraday ? intradayChange : closeChange) ?? 0) > 0, negative: ((isIntraday ? intradayChange : closeChange) ?? 0) < 0 }">
          {{ formatNumber(isIntraday ? intradayChange : closeChange) }} / {{ formatPercent(isIntraday ? intradayChangeRate : closeChangeRate) }}
        </strong>
      </div>
      <div>
        <span>{{ isIntraday ? `${selectedPeriodLabel}成交额` : '区间成交额' }}</span>
        <strong>{{ formatAmount(isIntraday ? intradayTotalAmount : totalAmount) }}</strong>
      </div>
      <div>
        <span>最大成交量</span>
        <strong>
          {{ isIntraday ? (maxIntradayVolumeRow?.time?.slice(11, 16) || '-') : (maxVolumeRow?.date || '-') }}
          · {{ formatVolume(isIntraday ? maxIntradayVolumeRow?.volume : maxVolumeRow?.volume) }}
        </strong>
      </div>
      <div>
        <span>{{ isIntraday ? '分钟条数' : '数据条数' }}</span>
        <strong>{{ isIntraday ? intradayRows.length : rows.length }}</strong>
      </div>
      <div>
        <span>加载时间</span>
        <strong>{{ loadedAt || '-' }}</strong>
      </div>
    </section>

    <section class="robot-history-chart-section" v-loading="loading">
      <div ref="chartRef" class="robot-history-chart" />
    </section>

    <section class="qlib-analysis-section" v-loading="qlibAnalysisLoading">
      <div class="qlib-analysis-head">
        <div>
          <span>Qlib 因子信号</span>
          <strong :class="qlibSignalClass(qlibAnalysis?.signal)">
            {{ qlibAnalysis?.signal || '-' }}
          </strong>
        </div>
        <div>
          <span class="qlib-score-label">
            综合分
            <el-popover placement="top" trigger="click" width="360">
              <template #reference>
                <button class="score-help-button" type="button" aria-label="查看综合分规则">?</button>
              </template>
              <div class="score-rule-popover">
                <strong>当前综合分规则</strong>
                <ul>
                  <li v-for="rule in qlibScoreRules" :key="rule">{{ rule }}</li>
                </ul>
              </div>
            </el-popover>
          </span>
          <strong>{{ qlibAnalysis?.score ?? '-' }}</strong>
        </div>
        <div>
          <span>样本</span>
          <strong>{{ qlibAnalysis?.row_count ?? 0 }} 条</strong>
        </div>
        <div>
          <span>区间</span>
          <strong>{{ qlibAnalysis?.start_date || '-' }} ~ {{ qlibAnalysis?.end_date || '-' }}</strong>
        </div>
      </div>
      <div class="qlib-factor-grid">
        <div>
          <span>5日动量</span>
          <strong :class="{ positive: (qlibAnalysis?.return_5 ?? 0) > 0, negative: (qlibAnalysis?.return_5 ?? 0) < 0 }">
            {{ formatPercent(qlibAnalysis?.return_5) }}
          </strong>
        </div>
        <div>
          <span>20日动量</span>
          <strong :class="{ positive: (qlibAnalysis?.return_20 ?? 0) > 0, negative: (qlibAnalysis?.return_20 ?? 0) < 0 }">
            {{ formatPercent(qlibAnalysis?.return_20) }}
          </strong>
        </div>
        <div>
          <span>60日动量</span>
          <strong :class="{ positive: (qlibAnalysis?.return_60 ?? 0) > 0, negative: (qlibAnalysis?.return_60 ?? 0) < 0 }">
            {{ formatPercent(qlibAnalysis?.return_60) }}
          </strong>
        </div>
        <div>
          <span>20日均线偏离</span>
          <strong>{{ formatPercent(qlibAnalysis?.ma_20_distance) }}</strong>
        </div>
        <div>
          <span>20日波动</span>
          <strong>{{ formatPercent(qlibAnalysis?.volatility_20) }}</strong>
        </div>
        <div>
          <span>最大回撤</span>
          <strong class="negative">{{ formatPercent(qlibAnalysis?.max_drawdown) }}</strong>
        </div>
        <div>
          <span>量能比</span>
          <strong>{{ formatRatio(qlibAnalysis?.volume_ratio_5_20) }}</strong>
        </div>
        <div>
          <span>数据源</span>
          <strong>{{ qlibAnalysis?.source || '-' }}</strong>
        </div>
        <div class="qlib-factor-error">
          <span>状态</span>
          <strong :class="{ negative: Boolean(qlibAnalysis?.error) }">{{ qlibAnalysis?.error || qlibAnalysis?.qlib_symbol || '-' }}</strong>
        </div>
      </div>
    </section>

    <section class="backtest-section" v-loading="backtestLoading">
      <div class="backtest-header">
        <div>
          <h2>小米一手评分策略回测</h2>
          <span>HK.01810 · {{ BACKTEST_START_DATE }} ~ {{ BACKTEST_END_DATE }} · 年末强制平仓</span>
        </div>
        <el-popover placement="top" trigger="click" width="420">
          <template #reference>
            <button class="score-help-button" type="button" aria-label="查看回测规则">?</button>
          </template>
          <div class="score-rule-popover">
            <strong>当前回测规则</strong>
            <ul>
              <li v-for="rule in backtestResult?.rules ?? []" :key="rule">{{ rule }}</li>
            </ul>
          </div>
        </el-popover>
      </div>
      <div class="backtest-metrics">
        <div>
          <span>净收益</span>
          <strong :class="{ positive: (backtestResult?.total_profit ?? 0) > 0, negative: (backtestResult?.total_profit ?? 0) < 0 }">
            {{ formatCurrency(backtestResult?.total_profit) }}
          </strong>
        </div>
        <div>
          <span>最终资金</span>
          <strong>{{ formatCurrency((backtestResult?.max_capital_used ?? 0) + (backtestResult?.total_profit ?? 0)) }}</strong>
        </div>
        <div>
          <span>占用收益率</span>
          <strong :class="{ positive: (backtestResult?.total_return_percent ?? 0) > 0, negative: (backtestResult?.total_return_percent ?? 0) < 0 }">
            {{ formatPercent(backtestResult?.total_return_percent) }}
          </strong>
        </div>
        <div>
          <span>最大资金占用</span>
          <strong>{{ formatCurrency(backtestResult?.max_capital_used) }}</strong>
        </div>
        <div>
          <span>总买入成本</span>
          <strong>{{ formatCurrency(backtestResult?.total_invested) }}</strong>
        </div>
        <div>
          <span>手续费消耗</span>
          <strong>{{ formatCurrency(backtestResult?.total_fee) }}</strong>
        </div>
        <div>
          <span>最高分</span>
          <strong>{{ backtestMaxScore ?? '-' }}</strong>
        </div>
        <div>
          <span>触发次数</span>
          <strong>{{ backtestTriggerCount }}</strong>
        </div>
        <div>
          <span>交易</span>
          <strong>{{ backtestResult?.closed_trade_count ?? 0 }}/{{ backtestResult?.trade_count ?? 0 }}</strong>
        </div>
        <div>
          <span>期末持仓</span>
          <strong>{{ backtestOpenLots }} 手</strong>
        </div>
      </div>
      <div ref="backtestChartRef" class="backtest-chart" />
      <el-table
        :data="backtestResult?.trades ?? []"
        table-layout="auto"
        :fit="false"
        stripe
      >
        <el-table-column label="触发日" min-width="100">
          <template #default="{ row }">{{ row.trigger_date }}</template>
        </el-table-column>
        <el-table-column label="买入日" min-width="100">
          <template #default="{ row }">{{ row.buy_date }}</template>
        </el-table-column>
        <el-table-column label="买入价" min-width="86" align="right">
          <template #default="{ row }">{{ formatNumber(row.buy_price) }}</template>
        </el-table-column>
        <el-table-column label="卖出日" min-width="100">
          <template #default="{ row }">{{ row.sell_date || '持有中' }}</template>
        </el-table-column>
        <el-table-column label="卖出价" min-width="86" align="right">
          <template #default="{ row }">{{ formatNumber(row.sell_price) }}</template>
        </el-table-column>
        <el-table-column label="股数" min-width="72" align="right">
          <template #default="{ row }">{{ row.shares }}</template>
        </el-table-column>
        <el-table-column label="盈亏" min-width="92" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.realized_profit ?? 0) > 0, negative: (row.realized_profit ?? 0) < 0 }">
              {{ formatCurrency(row.realized_profit) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="收益率" min-width="84" align="right">
          <template #default="{ row }">{{ formatPercent(row.realized_return_percent) }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="76">
          <template #default="{ row }">{{ row.status === 'closed' ? '已卖出' : '持有中' }}</template>
        </el-table-column>
      </el-table>
    </section>

    <section class="pool-backtest-section" v-loading="poolBacktestLoading || poolBacktestRefreshing">
      <div class="backtest-header">
        <div>
          <h2>港股池一手评分策略回测</h2>
          <span>
            {{ poolBacktestResult?.tested_count ?? 0 }}/{{ poolBacktestResult?.target_count ?? 0 }}
            · 跳过 {{ poolBacktestResult?.skipped_count ?? 0 }}
            · {{ BACKTEST_START_DATE }} ~ {{ BACKTEST_END_DATE }}
          </span>
        </div>
        <el-button :loading="poolBacktestRefreshing" @click="loadPoolBacktest(true)">重算港股池</el-button>
      </div>
      <div class="backtest-metrics">
        <div>
          <span>组合净收益</span>
          <strong :class="{ positive: (poolBacktestResult?.total_profit ?? 0) > 0, negative: (poolBacktestResult?.total_profit ?? 0) < 0 }">
            {{ formatCurrency(poolBacktestResult?.total_profit) }}
          </strong>
        </div>
        <div>
          <span>最终资金</span>
          <strong>{{ formatCurrency((poolBacktestResult?.max_capital_used ?? 0) + (poolBacktestResult?.total_profit ?? 0)) }}</strong>
        </div>
        <div>
          <span>最大资金占用</span>
          <strong>{{ formatCurrency(poolBacktestResult?.max_capital_used) }}</strong>
        </div>
        <div>
          <span>总买入成本</span>
          <strong>{{ formatCurrency(poolBacktestResult?.total_invested) }}</strong>
        </div>
        <div>
          <span>手续费消耗</span>
          <strong>{{ formatCurrency(poolBacktestResult?.total_fee) }}</strong>
        </div>
        <div>
          <span>交易</span>
          <strong>{{ poolBacktestResult?.closed_trade_count ?? 0 }}/{{ poolBacktestResult?.trade_count ?? 0 }}</strong>
        </div>
        <div>
          <span>期末持仓</span>
          <strong>{{ poolBacktestResult?.open_position_count ?? 0 }}</strong>
        </div>
        <div>
          <span>数据源</span>
          <strong>{{ poolBacktestResult?.source || '-' }}</strong>
        </div>
      </div>
      <el-table
        class="benchmark-table"
        :data="poolBacktestResult?.benchmarks ?? []"
        table-layout="auto"
        :fit="false"
        stripe
      >
        <el-table-column label="基准" min-width="132">
          <template #default="{ row }">{{ row.name }}</template>
        </el-table-column>
        <el-table-column label="区间" min-width="180">
          <template #default="{ row }">{{ row.start_date || '-' }} ~ {{ row.end_date || '-' }}</template>
        </el-table-column>
        <el-table-column label="策略占用收益率" min-width="126" align="right">
          <template #default>{{ formatPercent(poolBacktestReturnPercent) }}</template>
        </el-table-column>
        <el-table-column label="指数收益" min-width="96" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.return_percent ?? 0) > 0, negative: (row.return_percent ?? 0) < 0 }">
              {{ formatPercent(row.return_percent) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="超额收益" min-width="96" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.excess_return_percent ?? 0) > 0, negative: (row.excess_return_percent ?? 0) < 0 }">
              {{ formatPercent(row.excess_return_percent) }}
            </span>
          </template>
        </el-table-column>
      </el-table>
      <div class="pool-backtest-lists">
        <div>
          <h3>主要盈利</h3>
          <el-table :data="poolBacktestWinners" table-layout="auto" :fit="false" stripe>
            <el-table-column label="代码" min-width="92">
              <template #default="{ row }">
                <el-button link type="primary" @click.stop="previewPoolBacktestItem(row)">
                  {{ row.market }}.{{ row.symbol }}
                </el-button>
              </template>
            </el-table-column>
            <el-table-column label="名称" min-width="116">
              <template #default="{ row }">{{ row.name }}</template>
            </el-table-column>
            <el-table-column label="净收益" min-width="92" align="right">
              <template #default="{ row }"><span class="positive">{{ formatCurrency(row.total_profit) }}</span></template>
            </el-table-column>
            <el-table-column label="交易" min-width="72" align="right">
              <template #default="{ row }">{{ row.closed_trade_count }}/{{ row.trade_count }}</template>
            </el-table-column>
            <el-table-column label="占用" min-width="92" align="right">
              <template #default="{ row }">{{ formatCurrency(row.max_capital_used) }}</template>
            </el-table-column>
          </el-table>
        </div>
        <div>
          <h3>主要亏损</h3>
          <el-table :data="poolBacktestLosers" table-layout="auto" :fit="false" stripe>
            <el-table-column label="代码" min-width="92">
              <template #default="{ row }">
                <el-button link type="primary" @click.stop="previewPoolBacktestItem(row)">
                  {{ row.market }}.{{ row.symbol }}
                </el-button>
              </template>
            </el-table-column>
            <el-table-column label="名称" min-width="116">
              <template #default="{ row }">{{ row.name }}</template>
            </el-table-column>
            <el-table-column label="净收益" min-width="92" align="right">
              <template #default="{ row }"><span :class="{ positive: row.total_profit > 0, negative: row.total_profit < 0 }">{{ formatCurrency(row.total_profit) }}</span></template>
            </el-table-column>
            <el-table-column label="交易" min-width="72" align="right">
              <template #default="{ row }">{{ row.closed_trade_count }}/{{ row.trade_count }}</template>
            </el-table-column>
            <el-table-column label="占用" min-width="92" align="right">
              <template #default="{ row }">{{ formatCurrency(row.max_capital_used) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>

    <section class="hk-pool-section" v-loading="hkPoolLoading || hkPoolRefreshing">
      <div class="hk-pool-header">
        <div>
          <h2>港股股票池评分榜</h2>
          <span>
            {{ hkPoolScreen?.analyzed_count ?? 0 }}/{{ hkPoolScreen?.target_count ?? 0 }}
            · {{ hkPoolScreen?.source || '-' }}
          </span>
        </div>
        <el-button :loading="hkPoolRefreshing" @click="loadHkPoolScreen(false)">刷新榜单</el-button>
      </div>
      <el-table
        :data="hkPoolRows"
        table-layout="auto"
        :fit="false"
        stripe
      >
        <el-table-column label="代码" min-width="92">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="previewPoolTarget(row)">
              {{ row.market }}.{{ row.symbol }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="名称" min-width="128">
          <template #default="{ row }">{{ row.name }}</template>
        </el-table-column>
        <el-table-column min-width="82" align="right">
          <template #header>
            <span class="qlib-score-label is-table-header">
              综合分
              <el-popover placement="top" trigger="click" width="360">
                <template #reference>
                  <button class="score-help-button" type="button" aria-label="查看综合分规则">?</button>
                </template>
                <div class="score-rule-popover">
                  <strong>当前综合分规则</strong>
                  <ul>
                    <li v-for="rule in qlibScoreRules" :key="rule">{{ rule }}</li>
                  </ul>
                </div>
              </el-popover>
            </span>
          </template>
          <template #default="{ row }">
            <strong :class="qlibSignalClass(row.signal)">{{ row.score ?? '-' }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="信号" min-width="88">
          <template #default="{ row }">
            <span :class="qlibSignalClass(row.signal)">{{ row.signal }}</span>
          </template>
        </el-table-column>
        <el-table-column label="5日动量" min-width="92" align="right">
          <template #default="{ row }">{{ formatPercent(row.return_5) }}</template>
        </el-table-column>
        <el-table-column label="20日动量" min-width="96" align="right">
          <template #default="{ row }">{{ formatPercent(row.return_20) }}</template>
        </el-table-column>
        <el-table-column label="均线偏离" min-width="96" align="right">
          <template #default="{ row }">{{ formatPercent(row.ma_20_distance) }}</template>
        </el-table-column>
        <el-table-column label="量能比" min-width="84" align="right">
          <template #default="{ row }">{{ formatRatio(row.volume_ratio_5_20) }}</template>
        </el-table-column>
        <el-table-column label="样本" min-width="76" align="right">
          <template #default="{ row }">{{ row.row_count }}</template>
        </el-table-column>
        <el-table-column label="区间" min-width="184">
          <template #default="{ row }">{{ row.start_date || '-' }} ~ {{ row.end_date || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="220">
          <template #default="{ row }">{{ row.error || row.source }}</template>
        </el-table-column>
      </el-table>
      <div class="hk-pool-pagination">
        <span>第 {{ hkPoolPage }} 页 · {{ hkPoolTotal }} 条</span>
        <StandardPagination
          v-model:page="hkPoolPage"
          v-model:page-size="hkPoolPageSize"
          :page-size-options="[20, 50, 100]"
          :total="hkPoolTotal"
          @page-size-change="handleHkPoolPageSizeChange"
        />
      </div>
    </section>

  </div>
</template>

<style scoped>
.robot-history-page {
  padding: 16px 18px 24px;
  color: #172033;
}

.robot-history-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
  border-bottom: 1px solid #e6ebf2;
  padding-bottom: 12px;
}

.robot-history-title {
  min-width: 230px;
}

.robot-history-title h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0;
}

.active-target-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  color: #607086;
  font-size: 12px;
  line-height: 20px;
}

.robot-history-controls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.watch-target-control {
  display: flex;
  align-items: center;
  gap: 6px;
}

.watch-target-control span {
  color: #607086;
  font-size: 12px;
  white-space: nowrap;
}

.adjust-select {
  width: 96px;
}

.more-period-select {
  width: 104px;
}

.target-select {
  width: 178px;
}

.robot-history-metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(120px, max-content));
  gap: 10px 22px;
  margin-bottom: 14px;
  max-width: 100%;
  overflow-x: auto;
}

.robot-history-metrics div {
  display: grid;
  gap: 3px;
  min-width: 120px;
}

.robot-history-metrics span {
  color: #607086;
  font-size: 12px;
}

.robot-history-metrics strong {
  font-size: 16px;
  font-weight: 700;
  white-space: nowrap;
}

.robot-history-chart-section {
  border: 1px solid #dce5f0;
  border-radius: 6px;
  margin-bottom: 14px;
  padding: 12px 10px 8px;
}

.robot-history-chart {
  height: 390px;
  width: 100%;
}

.qlib-analysis-section {
  border-bottom: 1px solid #e6ebf2;
  margin-bottom: 14px;
  padding: 2px 0 14px;
}

.backtest-section {
  border-bottom: 1px solid #e6ebf2;
  margin-bottom: 14px;
  padding: 2px 0 14px;
}

.backtest-header {
  align-items: flex-start;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 10px;
}

.backtest-header h2 {
  font-size: 16px;
  margin: 0 0 3px;
}

.backtest-header span {
  color: #607086;
  font-size: 12px;
}

.backtest-metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(112px, max-content));
  gap: 10px 22px;
  margin-bottom: 10px;
  max-width: 100%;
  overflow-x: auto;
}

.backtest-metrics div {
  display: grid;
  gap: 3px;
  min-width: 112px;
}

.backtest-metrics span {
  color: #607086;
  font-size: 12px;
}

.backtest-metrics strong {
  font-size: 15px;
  font-weight: 700;
  white-space: nowrap;
}

.backtest-chart {
  border: 1px solid #dce5f0;
  border-radius: 6px;
  box-sizing: border-box;
  height: 260px;
  margin-bottom: 10px;
  padding: 8px 8px 0;
  width: 100%;
}

.pool-backtest-section {
  border-bottom: 1px solid #e6ebf2;
  margin-bottom: 14px;
  padding: 2px 0 14px;
}

.pool-backtest-lists {
  display: grid;
  grid-template-columns: repeat(2, minmax(360px, max-content));
  gap: 18px;
  max-width: 100%;
  overflow-x: auto;
}

.benchmark-table {
  margin-bottom: 12px;
}

.pool-backtest-lists h3 {
  font-size: 14px;
  margin: 0 0 8px;
}

.hk-pool-section {
  border-bottom: 1px solid #e6ebf2;
  margin-bottom: 14px;
  padding: 2px 0 14px;
}

.hk-pool-header {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 10px;
}

.hk-pool-header h2 {
  font-size: 16px;
  margin: 0 0 3px;
}

.hk-pool-header span {
  color: #607086;
  font-size: 12px;
}

.hk-pool-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-top: 1px solid #e6ebf2;
  margin-top: 10px;
  padding-top: 10px;
}

.hk-pool-pagination > span {
  color: #607086;
  font-size: 12px;
  white-space: nowrap;
}

.qlib-analysis-head,
.qlib-factor-grid {
  display: grid;
  gap: 10px 22px;
  max-width: 100%;
  overflow-x: auto;
}

.qlib-analysis-head {
  grid-template-columns: repeat(4, minmax(120px, max-content));
  margin-bottom: 10px;
}

.qlib-factor-grid {
  grid-template-columns: repeat(9, minmax(96px, max-content));
}

.qlib-analysis-head div,
.qlib-factor-grid div {
  display: grid;
  gap: 3px;
  min-width: 96px;
}

.qlib-analysis-head span,
.qlib-factor-grid span {
  color: #607086;
  font-size: 12px;
}

.qlib-analysis-head strong,
.qlib-factor-grid strong {
  font-size: 15px;
  font-weight: 700;
  white-space: nowrap;
}

.qlib-score-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.qlib-score-label.is-table-header {
  justify-content: flex-end;
  width: 100%;
}

.score-help-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  padding: 0;
  background: #fff;
  color: #607086;
  cursor: pointer;
  font-size: 11px;
  line-height: 1;
}

.score-help-button:hover {
  border-color: #409eff;
  color: #1677ff;
}

.score-rule-popover {
  display: grid;
  gap: 8px;
  color: #172033;
}

.score-rule-popover strong {
  font-size: 13px;
}

.score-rule-popover ul {
  display: grid;
  gap: 5px;
  margin: 0;
  padding-left: 18px;
}

.score-rule-popover li {
  line-height: 1.45;
}

.qlib-factor-error {
  min-width: 220px;
}

.positive {
  color: #b42318;
}

.negative {
  color: #067647;
}

@media (max-width: 900px) {
  .robot-history-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .robot-history-controls {
    justify-content: flex-start;
  }

  .robot-history-metrics {
    grid-template-columns: repeat(2, minmax(120px, 1fr));
  }

  .qlib-analysis-head,
  .qlib-factor-grid {
    grid-template-columns: repeat(2, minmax(120px, 1fr));
  }
}
</style>

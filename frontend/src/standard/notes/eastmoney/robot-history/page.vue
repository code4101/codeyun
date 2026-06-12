<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { DataZoomComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { BarChart, CustomChart, LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { ElMessage } from 'element-plus'
import {
  exportEastmoneyQlibDataset,
  fetchEastmoneyQlibAnalysis,
  fetchEastmoneyQlibHkPoolScreen,
  fetchEastmoneyQlibHkPoolOneLotScoreBacktest,
  fetchEastmoneyQlibHkPoolRotationStrategySearch,
  fetchEastmoneyQlibHkPoolStrategySearch,
  fetchEastmoneyHkConnectMomentumReview,
  fetchEastmoneyAkshareHistory,
  fetchEastmoneyAkshareIntraday,
  type EastmoneyAkshareHistoryItem,
  type EastmoneyAkshareIntradayItem,
  type EastmoneyHkConnectMomentumCandidate,
  type EastmoneyHkConnectMomentumReviewResult,
  type EastmoneyQlibPoolBacktestItem,
  type EastmoneyQlibPoolBacktestResult,
  type EastmoneyQlibAnalysis,
  type EastmoneyQlibScreenItem,
  type EastmoneyQlibScreenResult,
  type EastmoneyQlibRotationStrategySearchResult,
  type EastmoneyQlibStrategySearchResult,
} from '@/api/eastmoney'
import StandardPagination from '@/components/StandardPagination.vue'
import { formatChineseCompactNumber } from '@/standard/fanxiu/numberFormat'

echarts.use([DataZoomComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent, BarChart, CustomChart, LineChart, CanvasRenderer])

const PREFERENCE_STORAGE_KEY = 'codeyun.eastmoney.robotHistory.preferences'

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
type StrategyDeskKey = 'hk_connect' | 'cross_asset_etf' | 'convertible_bond'
type StrategyDeskStatus = 'buy' | 'hold_cash' | 'watch'
type StrategyDeskItem = {
  key: StrategyDeskKey
  name: string
  status: StrategyDeskStatus
  statusText: string
  summary: string
  evidence: string
  risk: string
}
type StrategyDeskOrder = {
  code: string
  name: string
  close: number
  amount: string
  quantity: string
  skipAbove: number
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
const hkConnectReviewLoading = ref(false)
const hkConnectReviewRefreshing = ref(false)
const hkConnectReview = ref<EastmoneyHkConnectMomentumReviewResult | null>(null)
const poolBacktestLoading = ref(false)
const poolBacktestRefreshing = ref(false)
const poolBacktestResult = ref<EastmoneyQlibPoolBacktestResult | null>(null)
const poolBacktestYear = ref('2025')
const poolBacktestScoreThreshold = ref(84)
const poolBacktestTakeProfitPercent = ref(5)
const poolBacktestCostPercent = ref(1)
const poolBacktestLimit = ref(0)
const strategySearchLoading = ref(false)
const strategySearchRefreshing = ref(false)
const strategySearchResult = ref<EastmoneyQlibStrategySearchResult | null>(null)
const strategySearchYears = ref('2023,2024,2025')
const strategySearchLimit = ref(20)
const strategySearchScoreThresholds = ref('70,76,80,84,88,90')
const strategySearchScoreProfiles = ref('balanced,trend_momentum,short_reversal,low_volatility,volume_breakout')
const strategySearchTakeProfitPercents = ref('5,8,10,15')
const strategySearchStopLossPercents = ref('0,8')
const strategySearchMaxHoldingDays = ref('0,60')
const strategySearchCostPercent = ref(1)
const strategySearchMinAnnualReturnPercent = ref(5)
const strategySearchRequireBeatBenchmark = ref(true)
const rotationSearchLoading = ref(false)
const rotationSearchRefreshing = ref(false)
const rotationSearchResult = ref<EastmoneyQlibRotationStrategySearchResult | null>(null)
const rotationSearchYears = ref('2024,2025')
const rotationSearchLimit = ref(100)
const rotationSearchScoreProfiles = ref('balanced')
const rotationSearchRankMetrics = ref('score,volume_breakout_rank,value_score_rank')
const rotationSearchMarketFilters = ref('none,hsi_ma60')
const rotationSearchScoreThresholds = ref('0')
const rotationSearchMinAmounts = ref('10000000')
const rotationSearchTopNValues = ref('5')
const rotationSearchRebalances = ref('quarterly')
const rotationSearchCostPercent = ref(1)
const rotationSearchMinAnnualReturnPercent = ref(5)
const rotationSearchRequireBeatBenchmark = ref(true)
const hkPoolPage = ref(1)
const hkPoolPageSize = ref(20)
const selectedStrategyKey = ref<StrategyDeskKey>('cross_asset_etf')
const researchPanels = ref<string[]>([])
const loadedAt = ref('')
const rows = ref<EastmoneyAkshareHistoryItem[]>([])
const intradayRows = ref<EastmoneyAkshareIntradayItem[]>([])
const chartNotice = ref('')
const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let loadSequence = 0
let qlibLoadSequence = 0
let intradayRefreshTimer: number | null = null
let poolBacktestProgressTimer: number | null = null
let hkConnectReviewProgressTimer: number | null = null
let strategySearchProgressTimer: number | null = null
let rotationSearchProgressTimer: number | null = null

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
const poolBacktestYearOptions = ['2025', '2024', '2023', '2022', '2021', '2020']
const poolBacktestLimitOptions = [
  { label: '全量', value: 0 },
  { label: '前100', value: 100 },
  { label: '前300', value: 300 },
  { label: '前500', value: 500 },
  { label: '前1000', value: 1000 },
]
const strategySearchLimitOptions = [
  { label: '前20', value: 20 },
  { label: '前100', value: 100 },
  { label: '前300', value: 300 },
  { label: '前500', value: 500 },
  { label: '全量', value: 0 },
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
const usingDailyFallback = computed(() => isIntraday.value && intradayRows.value.length === 0 && rows.value.length > 0)
const metricLatestClose = computed(() => {
  if (usingDailyFallback.value) return latestRow.value?.close
  return isIntraday.value ? latestIntradayRow.value?.close : latestRow.value?.close
})
const metricChange = computed(() => {
  if (usingDailyFallback.value) return closeChange.value
  return isIntraday.value ? intradayChange.value : closeChange.value
})
const metricChangeRate = computed(() => {
  if (usingDailyFallback.value) return closeChangeRate.value
  return isIntraday.value ? intradayChangeRate.value : closeChangeRate.value
})
const metricTotalAmount = computed(() => {
  if (usingDailyFallback.value) return totalAmount.value
  return isIntraday.value ? intradayTotalAmount.value : totalAmount.value
})
const metricMaxVolumeLabel = computed(() => {
  if (usingDailyFallback.value || !isIntraday.value) {
    return `${maxVolumeRow.value?.date || '-'} · ${formatVolume(maxVolumeRow.value?.volume)}`
  }
  return `${maxIntradayVolumeRow.value?.time?.slice(11, 16) || '-'} · ${formatVolume(maxIntradayVolumeRow.value?.volume)}`
})
const metricRowCount = computed(() => usingDailyFallback.value || !isIntraday.value ? rows.value.length : intradayRows.value.length)
const metricModeLabel = computed(() => usingDailyFallback.value ? '日K兜底' : selectedPeriodLabel.value)
const qlibScoreRules = computed(() => qlibAnalysis.value?.scoring_rules?.length
  ? qlibAnalysis.value.scoring_rules
  : hkPoolScreen.value?.scoring_rules ?? [])
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
const poolBacktestStartDate = computed(() => `${poolBacktestYear.value}-01-01`)
const poolBacktestEndDate = computed(() => `${poolBacktestYear.value}-12-31`)
const poolBacktestScaleLabel = computed(() => poolBacktestLimit.value ? `前${poolBacktestLimit.value}` : '全量')
const poolBacktestActionText = computed(() => poolBacktestLimit.value ? '快速试算' : '全量重算')
const poolBacktestRunning = computed(() => (poolBacktestResult.value?.source || '').startsWith('running:'))
const strategySearchRows = computed(() => strategySearchResult.value?.items ?? [])
const strategySearchScaleLabel = computed(() => strategySearchLimit.value ? `前${strategySearchLimit.value}` : '全量')
const strategySearchRunning = computed(() => strategySearchResult.value?.status === 'running' || (strategySearchResult.value?.source || '').startsWith('running:'))
const strategySearchQualifiedCount = computed(() => strategySearchResult.value?.qualified_count ?? strategySearchRows.value.filter((row) => row.is_qualified).length)
const rotationSearchRows = computed(() => rotationSearchResult.value?.items ?? [])
const rotationSearchScaleLabel = computed(() => rotationSearchLimit.value ? `前${rotationSearchLimit.value}` : '全量')
const rotationSearchRunning = computed(() => rotationSearchResult.value?.status === 'running' || (rotationSearchResult.value?.source || '').startsWith('running:'))
const rotationSearchQualifiedCount = computed(() => rotationSearchResult.value?.qualified_count ?? rotationSearchRows.value.filter((row) => row.is_qualified).length)
const hkConnectReviewRunning = computed(() => hkConnectReview.value?.status === 'running' || (hkConnectReview.value?.source || '').startsWith('running:'))
const hkConnectActionText = computed(() => {
  if (!hkConnectReview.value) return '-'
  if (hkConnectReview.value.action === 'buy') return '开仓'
  if (hkConnectReview.value.action === 'hold_cash') return '空仓'
  return '等待'
})
const hkConnectActionClass = computed(() => {
  if (hkConnectReview.value?.action === 'buy') return 'positive'
  if (hkConnectReview.value?.action === 'hold_cash') return 'negative'
  return ''
})
const hkConnectCandidateRows = computed(() => hkConnectReview.value?.candidates ?? [])
const hkConnectSelectedRows = computed(() => hkConnectReview.value?.selected ?? [])
const crossAssetOrders: StrategyDeskOrder[] = [
  { code: '513520', name: '日经ETF华夏', close: 2.292, amount: '34838.40 元', quantity: '15200 份', skipAbove: 2.338 },
  { code: '515220', name: '煤炭ETF国泰', close: 1.318, amount: '34927.00 元', quantity: '26500 份', skipAbove: 1.344 },
]
const convertibleBondWatchRows = [
  { code: '111023', name: '利柏转债', metric: '双低 110.23', note: 'AA / 成交 3.08 亿' },
  { code: '113042', name: '上银转债', metric: '双低 124.01', note: 'AAA / 成交 4.38 亿' },
  { code: '113644', name: '艾迪转债', metric: '双低 123.03', note: 'AA- / 成交 3.69 亿' },
]
const strategyDeskItems = computed<StrategyDeskItem[]>(() => {
  const hkSummary = hkConnectReview.value?.summary || '恒生指数过滤未确认，等待盘后复盘。'
  return [
    {
      key: 'hk_connect',
      name: '港股通主策略',
      status: hkConnectReview.value?.action === 'buy' ? 'buy' : 'hold_cash',
      statusText: hkConnectReview.value?.action === 'buy' ? '开仓' : '空仓',
      summary: hkSummary,
      evidence: hkConnectReview.value
        ? `恒生 ${formatNumber(hkConnectReview.value.hsi_close, 2)} / MA60 ${formatNumber(hkConnectReview.value.hsi_ma60, 2)}`
        : '等待缓存',
      risk: '只在恒生站上 60 日线后执行，避免弱势市场追动量。',
    },
    {
      key: 'cross_asset_etf',
      name: '跨资产 ETF 轮动',
      status: 'watch',
      statusText: '小仓验证',
      summary: '固定资产池周频 20 日动量给出下周信号，建议只用观察仓验证。',
      evidence: '2021-2026YTD 年度为正；最大回撤约 -13.20%。',
      risk: '2026 年 3-4 月曾连续亏损，不适合满额执行。',
    },
    {
      key: 'convertible_bond',
      name: '可转债观察池',
      status: 'watch',
      statusText: '观察',
      summary: '当前有双低候选，但价格型周频回测没有通过年度稳定性。',
      evidence: '低价 Top10 在 2022、2023、2026YTD 均不稳定。',
      risk: '缺历史溢价率、剩余规模和强赎状态，不直接给买入清单。',
    },
  ]
})
const selectedStrategy = computed<StrategyDeskItem>(() => strategyDeskItems.value.find((item) => item.key === selectedStrategyKey.value) ?? strategyDeskItems.value[0]!)
const deskFinalAction = computed(() => {
  if (selectedStrategy.value?.key === 'cross_asset_etf') return '下周可小仓验证'
  if (hkConnectReview.value?.action === 'buy') return '港股通可开仓'
  return '主策略空仓'
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
  chartNotice.value = ''
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
  chartNotice.value = ''
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
    if (result.items.length === 0) {
      const fallback = await fetchEastmoneyAkshareHistory({
        market: activeTarget.value.market,
        symbol: activeTarget.value.symbol,
        name: activeTarget.value.name,
        period: 'daily',
        start_date: activeTarget.value.startDate,
        adjust: 'qfq',
        refresh: false,
      })
      if (sequence !== loadSequence) return
      rows.value = fallback.items
      chartNotice.value = fallback.items.length > 0
        ? `${selectedPeriodLabel.value}暂无数据，已显示日K缓存`
        : `${selectedPeriodLabel.value}和日K缓存均暂无数据`
    }
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

async function loadHkConnectReview(refresh = false) {
  if (refresh) {
    hkConnectReviewRefreshing.value = true
  }
  else {
    hkConnectReviewLoading.value = true
  }
  try {
    hkConnectReview.value = await fetchEastmoneyHkConnectMomentumReview({
      refresh,
      background: refresh,
    })
    if (refresh) {
      startHkConnectReviewProgressTimer()
      ElMessage.success('港股通策略复盘已在后台开始')
    }
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取港股通策略复盘失败：${message}`)
  }
  finally {
    hkConnectReviewLoading.value = false
    if (!refresh || hkConnectReviewProgressTimer == null) {
      hkConnectReviewRefreshing.value = false
    }
  }
}

async function loadHkConnectReviewProgress() {
  try {
    const result = await fetchEastmoneyHkConnectMomentumReview({
      progress: true,
    })
    hkConnectReview.value = result
    if (result.status !== 'running' && !result.source.startsWith('running:')) {
      stopHkConnectReviewProgressTimer()
      hkConnectReviewRefreshing.value = false
      if (result.error) {
        ElMessage.error(`港股通策略复盘失败：${result.error}`)
      }
      else {
        ElMessage.success('港股通策略复盘完成')
      }
    }
  }
  catch (error) {
    stopHkConnectReviewProgressTimer()
    hkConnectReviewRefreshing.value = false
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取港股通策略复盘进度失败：${message}`)
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
    const limit = poolBacktestLimit.value || undefined
    poolBacktestResult.value = await fetchEastmoneyQlibHkPoolOneLotScoreBacktest({
      refresh,
      background: refresh,
      limit,
      detail_limit: limit ? Math.max(limit, 100) : 5000,
      start_date: poolBacktestStartDate.value,
      end_date: poolBacktestEndDate.value,
      score_threshold: poolBacktestScoreThreshold.value,
      take_profit_percent: poolBacktestTakeProfitPercent.value,
      cost_rate: poolBacktestCostPercent.value / 100,
      force_liquidate_end: true,
    })
    if (refresh) {
      startPoolBacktestProgressTimer()
      ElMessage.success('港股池回测已在后台开始')
    }
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取港股池回测失败：${message}`)
  }
  finally {
    poolBacktestLoading.value = false
    if (!refresh || poolBacktestProgressTimer == null) {
      poolBacktestRefreshing.value = false
    }
  }
}

async function loadPoolBacktestProgress() {
  try {
    const limit = poolBacktestLimit.value || undefined
    const result = await fetchEastmoneyQlibHkPoolOneLotScoreBacktest({
      progress: true,
      limit,
      detail_limit: limit ? Math.max(limit, 100) : 5000,
      start_date: poolBacktestStartDate.value,
      end_date: poolBacktestEndDate.value,
      score_threshold: poolBacktestScoreThreshold.value,
      take_profit_percent: poolBacktestTakeProfitPercent.value,
      cost_rate: poolBacktestCostPercent.value / 100,
      force_liquidate_end: true,
    })
    poolBacktestResult.value = result
    if (!result.source.startsWith('running:')) {
      stopPoolBacktestProgressTimer()
      poolBacktestRefreshing.value = false
      ElMessage.success(`港股池回测完成：${result.tested_count}/${result.target_count}`)
    }
  }
  catch (error) {
    stopPoolBacktestProgressTimer()
    poolBacktestRefreshing.value = false
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取港股池回测进度失败：${message}`)
  }
}

function strategySearchParams(extra: { background?: boolean, progress?: boolean } = {}) {
  return {
    years: strategySearchYears.value,
    limit: strategySearchLimit.value || undefined,
    score_thresholds: strategySearchScoreThresholds.value,
    score_profiles: strategySearchScoreProfiles.value,
    take_profit_percents: strategySearchTakeProfitPercents.value,
    stop_loss_percents: strategySearchStopLossPercents.value,
    max_holding_days: strategySearchMaxHoldingDays.value,
    cost_rate: strategySearchCostPercent.value / 100,
    min_annual_return_percent: strategySearchMinAnnualReturnPercent.value,
    require_beat_benchmark: strategySearchRequireBeatBenchmark.value,
    ...extra,
  }
}

async function loadStrategySearch(refresh = false) {
  if (refresh) {
    strategySearchRefreshing.value = true
  }
  else {
    strategySearchLoading.value = true
  }
  try {
    strategySearchResult.value = await fetchEastmoneyQlibHkPoolStrategySearch(strategySearchParams({
      background: refresh,
    }))
    if (refresh) {
      startStrategySearchProgressTimer()
      ElMessage.success('候选策略搜索已在后台开始')
    }
    else {
      ElMessage.success(`候选策略完成：${strategySearchResult.value.items.length} 组`)
    }
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取候选策略失败：${message}`)
  }
  finally {
    strategySearchLoading.value = false
    if (!refresh || strategySearchProgressTimer == null) {
      strategySearchRefreshing.value = false
    }
  }
}

async function loadStrategySearchProgress() {
  try {
    const result = await fetchEastmoneyQlibHkPoolStrategySearch(strategySearchParams({
      progress: true,
    }))
    strategySearchResult.value = result
    if (result.status !== 'running' && !result.source.startsWith('running:')) {
      stopStrategySearchProgressTimer()
      strategySearchRefreshing.value = false
      if (result.error) {
        ElMessage.error(`候选策略搜索失败：${result.error}`)
      }
      else {
        ElMessage.success(`候选策略完成：${result.items.length} 组`)
      }
    }
  }
  catch (error) {
    stopStrategySearchProgressTimer()
    strategySearchRefreshing.value = false
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取候选策略进度失败：${message}`)
  }
}

function rotationSearchParams(extra: { background?: boolean, progress?: boolean } = {}) {
  return {
    years: rotationSearchYears.value,
    limit: rotationSearchLimit.value || undefined,
    score_profiles: rotationSearchScoreProfiles.value,
    rank_metrics: rotationSearchRankMetrics.value,
    market_filters: rotationSearchMarketFilters.value,
    score_thresholds: rotationSearchScoreThresholds.value,
    min_amounts: rotationSearchMinAmounts.value,
    top_n_values: rotationSearchTopNValues.value,
    rebalances: rotationSearchRebalances.value,
    cost_rate: rotationSearchCostPercent.value / 100,
    min_annual_return_percent: rotationSearchMinAnnualReturnPercent.value,
    require_beat_benchmark: rotationSearchRequireBeatBenchmark.value,
    ...extra,
  }
}

async function loadRotationSearch(refresh = false) {
  if (refresh) {
    rotationSearchRefreshing.value = true
  }
  else {
    rotationSearchLoading.value = true
  }
  try {
    rotationSearchResult.value = await fetchEastmoneyQlibHkPoolRotationStrategySearch(rotationSearchParams({
      background: refresh,
    }))
    if (refresh) {
      startRotationSearchProgressTimer()
      ElMessage.success('轮动策略搜索已在后台开始')
    }
    else {
      ElMessage.success(`轮动策略完成：${rotationSearchResult.value.items.length} 组`)
    }
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取轮动策略失败：${message}`)
  }
  finally {
    rotationSearchLoading.value = false
    if (!refresh || rotationSearchProgressTimer == null) {
      rotationSearchRefreshing.value = false
    }
  }
}

async function loadRotationSearchProgress() {
  try {
    const result = await fetchEastmoneyQlibHkPoolRotationStrategySearch(rotationSearchParams({
      progress: true,
    }))
    rotationSearchResult.value = result
    if (result.status !== 'running' && !result.source.startsWith('running:')) {
      stopRotationSearchProgressTimer()
      rotationSearchRefreshing.value = false
      if (result.error) {
        ElMessage.error(`轮动策略搜索失败：${result.error}`)
      }
      else {
        ElMessage.success(`轮动策略完成：${result.items.length} 组`)
      }
    }
  }
  catch (error) {
    stopRotationSearchProgressTimer()
    rotationSearchRefreshing.value = false
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取轮动策略进度失败：${message}`)
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
  if (isIntraday.value && intradayRows.value.length > 0) {
    renderIntradayChart(instance)
    return
  }
  if (rows.value.length === 0) {
    renderEmptyChart(instance, chartNotice.value || '暂无行情数据')
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

function renderEmptyChart(instance: echarts.ECharts, message: string) {
  instance.setOption({
    animation: false,
    title: {
      text: message,
      left: 'center',
      top: 'middle',
      textStyle: {
        color: '#607086',
        fontSize: 14,
        fontWeight: 500,
      },
    },
    xAxis: { show: false },
    yAxis: { show: false },
    series: [],
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

function startPoolBacktestProgressTimer() {
  stopPoolBacktestProgressTimer()
  poolBacktestRefreshing.value = true
  void loadPoolBacktestProgress()
  poolBacktestProgressTimer = window.setInterval(() => {
    void loadPoolBacktestProgress()
  }, 2000)
}

function stopPoolBacktestProgressTimer() {
  if (poolBacktestProgressTimer != null) {
    window.clearInterval(poolBacktestProgressTimer)
    poolBacktestProgressTimer = null
  }
}

function startHkConnectReviewProgressTimer() {
  stopHkConnectReviewProgressTimer()
  hkConnectReviewRefreshing.value = true
  void loadHkConnectReviewProgress()
  hkConnectReviewProgressTimer = window.setInterval(() => {
    void loadHkConnectReviewProgress()
  }, 2000)
}

function stopHkConnectReviewProgressTimer() {
  if (hkConnectReviewProgressTimer != null) {
    window.clearInterval(hkConnectReviewProgressTimer)
    hkConnectReviewProgressTimer = null
  }
}

function startStrategySearchProgressTimer() {
  stopStrategySearchProgressTimer()
  strategySearchRefreshing.value = true
  void loadStrategySearchProgress()
  strategySearchProgressTimer = window.setInterval(() => {
    void loadStrategySearchProgress()
  }, 2000)
}

function stopStrategySearchProgressTimer() {
  if (strategySearchProgressTimer != null) {
    window.clearInterval(strategySearchProgressTimer)
    strategySearchProgressTimer = null
  }
}

function startRotationSearchProgressTimer() {
  stopRotationSearchProgressTimer()
  rotationSearchRefreshing.value = true
  void loadRotationSearchProgress()
  rotationSearchProgressTimer = window.setInterval(() => {
    void loadRotationSearchProgress()
  }, 2000)
}

function stopRotationSearchProgressTimer() {
  if (rotationSearchProgressTimer != null) {
    window.clearInterval(rotationSearchProgressTimer)
    rotationSearchProgressTimer = null
  }
}

onMounted(() => {
  void loadHistory()
  void loadQlibAnalysis(false)
  void loadHkPoolScreen(false)
  void loadHkConnectReview(false)
  void loadPoolBacktest(false)
  startIntradayRefreshTimer()
  if (chartRef.value) {
    resizeObserver = new ResizeObserver(() => {
      chart?.resize()
    })
  }
  if (chartRef.value && resizeObserver) {
    resizeObserver.observe(chartRef.value)
  }
})

onBeforeUnmount(() => {
  stopIntradayRefreshTimer()
  stopPoolBacktestProgressTimer()
  stopHkConnectReviewProgressTimer()
  stopStrategySearchProgressTimer()
  stopRotationSearchProgressTimer()
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})

watch(rows, () => {
  void nextTick(renderChart)
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

    <section class="strategy-desk">
      <div class="strategy-desk-head">
        <div>
          <span>今日结论</span>
          <strong>{{ deskFinalAction }}</strong>
        </div>
        <p>{{ selectedStrategy.summary }}</p>
      </div>
      <div class="strategy-desk-body">
        <nav class="strategy-list" aria-label="策略列表">
          <button
            v-for="strategy in strategyDeskItems"
            :key="strategy.key"
            class="strategy-list-item"
            :class="{ active: selectedStrategyKey === strategy.key }"
            type="button"
            @click="selectedStrategyKey = strategy.key"
          >
            <span>
              <strong>{{ strategy.name }}</strong>
              <small>{{ strategy.evidence }}</small>
            </span>
            <em :class="`strategy-status is-${strategy.status}`">{{ strategy.statusText }}</em>
          </button>
        </nav>
        <div class="strategy-detail">
          <div class="strategy-detail-title">
            <div>
              <span>当前策略</span>
              <h2>{{ selectedStrategy.name }}</h2>
            </div>
            <em :class="`strategy-status is-${selectedStrategy.status}`">{{ selectedStrategy.statusText }}</em>
          </div>

          <template v-if="selectedStrategy.key === 'cross_asset_etf'">
            <div class="strategy-signal-table">
              <table>
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>收盘</th>
                    <th>计划</th>
                    <th>占用</th>
                    <th>跳过价</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="order in crossAssetOrders" :key="order.code">
                    <td>{{ order.code }}</td>
                    <td>{{ order.name }}</td>
                    <td>{{ formatNumber(order.close, 3) }}</td>
                    <td>{{ order.quantity }}</td>
                    <td>{{ order.amount }}</td>
                    <td>{{ formatNumber(order.skipAbove, 3) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="strategy-facts">
              <div><span>年度</span><strong>2021-2026YTD 全正</strong></div>
              <div><span>累计</span><strong class="positive">89.95%</strong></div>
              <div><span>最大回撤</span><strong class="negative">-13.20%</strong></div>
            </div>
          </template>

          <template v-else-if="selectedStrategy.key === 'hk_connect'">
            <div class="strategy-facts">
              <div><span>明日动作</span><strong :class="hkConnectActionClass">{{ hkConnectActionText }}</strong></div>
              <div><span>恒生过滤</span><strong>{{ hkConnectReview?.hsi_filter_passed ? '通过' : '未通过' }}</strong></div>
              <div><span>候选</span><strong>{{ hkConnectReview?.usable_count ?? 0 }}/{{ hkConnectReview?.pool_count ?? 0 }}</strong></div>
            </div>
            <p class="strategy-note">{{ hkConnectReview?.summary || selectedStrategy.summary }}</p>
          </template>

          <template v-else>
            <div class="strategy-signal-table">
              <table>
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>指标</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in convertibleBondWatchRows" :key="row.code">
                    <td>{{ row.code }}</td>
                    <td>{{ row.name }}</td>
                    <td>{{ row.metric }}</td>
                    <td>{{ row.note }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>

          <p class="strategy-risk">{{ selectedStrategy.risk }}</p>
        </div>
      </div>
    </section>

    <section class="robot-history-metrics">
      <div>
        <span>{{ usingDailyFallback || !isIntraday ? '最新收盘' : '最新价' }}</span>
        <strong>{{ formatNumber(metricLatestClose) }}</strong>
      </div>
      <div>
        <span>{{ usingDailyFallback ? '日K区间涨跌' : (isIntraday ? `${selectedPeriodLabel}涨跌` : '区间涨跌') }}</span>
        <strong :class="{ positive: (metricChange ?? 0) > 0, negative: (metricChange ?? 0) < 0 }">
          {{ formatNumber(metricChange) }} / {{ formatPercent(metricChangeRate) }}
        </strong>
      </div>
      <div>
        <span>{{ usingDailyFallback ? '日K区间成交额' : (isIntraday ? `${selectedPeriodLabel}成交额` : '区间成交额') }}</span>
        <strong>{{ formatAmount(metricTotalAmount) }}</strong>
      </div>
      <div>
        <span>最大成交量</span>
        <strong>{{ metricMaxVolumeLabel }}</strong>
      </div>
      <div>
        <span>{{ usingDailyFallback ? '日K条数' : (isIntraday ? '分钟条数' : '数据条数') }}</span>
        <strong>{{ metricRowCount }}</strong>
      </div>
      <div>
        <span>加载时间</span>
        <strong>{{ loadedAt || '-' }}</strong>
      </div>
    </section>

    <section class="robot-history-chart-section" v-loading="loading">
      <div v-if="chartNotice" class="chart-notice">{{ chartNotice }}</div>
      <div ref="chartRef" class="robot-history-chart" />
    </section>

    <el-collapse v-model="researchPanels" class="research-collapse">
      <el-collapse-item title="复盘与高级研究" name="advanced">
    <section class="hk-connect-review-section" v-loading="hkConnectReviewLoading">
      <div class="backtest-header">
        <div>
          <h2>港股通策略复盘</h2>
          <span>
            {{ hkConnectReview?.signal_date || '-' }}
            · {{ hkConnectReview?.strategy_name || '恒生60日线大市值成交额动量' }}
            · 可用 {{ hkConnectReview?.usable_count ?? 0 }}/{{ hkConnectReview?.pool_count ?? 0 }}
            <template v-if="hkConnectReviewRunning"> · 后台计算中</template>
          </span>
        </div>
        <div class="pool-backtest-actions">
          <el-button size="small" :loading="hkConnectReviewLoading" @click="loadHkConnectReview(false)">读缓存</el-button>
          <el-button size="small" type="primary" :loading="hkConnectReviewRefreshing" @click="loadHkConnectReview(true)">盘后重算</el-button>
        </div>
      </div>
      <div class="hk-connect-review-summary">
        <div>
          <span>明日动作</span>
          <strong :class="hkConnectActionClass">{{ hkConnectActionText }}</strong>
        </div>
        <div>
          <span>恒生过滤</span>
          <strong :class="{ positive: hkConnectReview?.hsi_filter_passed, negative: hkConnectReview && !hkConnectReview.hsi_filter_passed }">
            {{ hkConnectReview?.hsi_filter_passed ? '通过' : '未通过' }}
          </strong>
        </div>
        <div>
          <span>恒生 / MA60</span>
          <strong>{{ formatNumber(hkConnectReview?.hsi_close, 2) }} / {{ formatNumber(hkConnectReview?.hsi_ma60, 2) }}</strong>
        </div>
        <div>
          <span>单票预算</span>
          <strong>{{ formatCurrency(hkConnectReview?.single_position_budget) }}</strong>
        </div>
        <div>
          <span>生成时间</span>
          <strong>{{ hkConnectReview?.generated_at?.replace('T', ' ') || '-' }}</strong>
        </div>
      </div>
      <p class="hk-connect-review-note" :class="{ negative: hkConnectReview?.action === 'hold_cash', positive: hkConnectReview?.action === 'buy' }">
        {{ hkConnectReview?.summary || '暂无复盘结果' }}
      </p>
      <div v-if="hkConnectSelectedRows.length" class="hk-connect-selected">
        <h3>建议开仓</h3>
        <el-table :data="hkConnectSelectedRows" table-layout="auto" :fit="false" stripe>
          <el-table-column label="代码" min-width="78">
            <template #default="{ row }">{{ row.symbol }}</template>
          </el-table-column>
          <el-table-column label="名称" min-width="118">
            <template #default="{ row }">{{ row.name }}</template>
          </el-table-column>
          <el-table-column label="手数" min-width="70" align="right">
            <template #default="{ row }">{{ row.budget_lots }}</template>
          </el-table-column>
          <el-table-column label="预计占用" min-width="92" align="right">
            <template #default="{ row }">{{ formatCurrency(row.estimated_cash) }}</template>
          </el-table-column>
          <el-table-column label="10日涨幅" min-width="92" align="right">
            <template #default="{ row }">{{ formatPercent(row.return_10_percent) }}</template>
          </el-table-column>
        </el-table>
      </div>
      <div class="hk-connect-candidates">
        <h3>候选排名</h3>
        <el-table :data="hkConnectCandidateRows" table-layout="auto" :fit="false" stripe>
          <el-table-column label="#" min-width="48" align="right">
            <template #default="{ row }">{{ row.rank }}</template>
          </el-table-column>
          <el-table-column label="代码" min-width="78">
            <template #default="{ row }">{{ row.symbol }}</template>
          </el-table-column>
          <el-table-column label="名称" min-width="120">
            <template #default="{ row }">{{ row.name }}</template>
          </el-table-column>
          <el-table-column label="信号分" min-width="82" align="right">
            <template #default="{ row }">{{ formatNumber(row.signal_score, 2) }}</template>
          </el-table-column>
          <el-table-column label="10日" min-width="82" align="right">
            <template #default="{ row }">{{ formatPercent(row.return_10_percent) }}</template>
          </el-table-column>
          <el-table-column label="成交额" min-width="92" align="right">
            <template #default="{ row }">{{ formatAmount(row.amount) }}</template>
          </el-table-column>
          <el-table-column label="一手" min-width="82" align="right">
            <template #default="{ row }">{{ formatCurrency(row.lot_value) }}</template>
          </el-table-column>
          <el-table-column label="可买" min-width="64" align="right">
            <template #default="{ row }">
              <span :class="{ negative: row.budget_lots <= 0, positive: row.selected }">{{ row.budget_lots }} 手</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
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

    <section class="pool-backtest-section">
      <div class="backtest-header">
        <div>
          <h2>港股池一手评分策略回测</h2>
          <span>
            {{ poolBacktestResult?.tested_count ?? 0 }}/{{ poolBacktestResult?.target_count ?? 0 }}
            · 跳过 {{ poolBacktestResult?.skipped_count ?? 0 }}
            · {{ poolBacktestStartDate }} ~ {{ poolBacktestEndDate }}
            · {{ poolBacktestScaleLabel }}
            <template v-if="poolBacktestRunning"> · 后台计算中</template>
          </span>
        </div>
      </div>
      <div class="pool-backtest-controls">
        <label>
          <span>年份</span>
          <el-select v-model="poolBacktestYear" size="small" style="width: 94px">
            <el-option v-for="year in poolBacktestYearOptions" :key="year" :label="year" :value="year" />
          </el-select>
        </label>
        <label>
          <span>触发分</span>
          <el-input-number
            v-model="poolBacktestScoreThreshold"
            size="small"
            :min="0"
            :max="100"
            :step="1"
            controls-position="right"
            style="width: 104px"
          />
        </label>
        <label>
          <span>止盈%</span>
          <el-input-number
            v-model="poolBacktestTakeProfitPercent"
            size="small"
            :min="0"
            :max="100"
            :step="0.5"
            controls-position="right"
            style="width: 112px"
          />
        </label>
        <label>
          <span>成本%</span>
          <el-input-number
            v-model="poolBacktestCostPercent"
            size="small"
            :min="0"
            :max="10"
            :step="0.1"
            controls-position="right"
            style="width: 112px"
          />
        </label>
        <label>
          <span>规模</span>
          <el-select v-model="poolBacktestLimit" size="small" style="width: 94px">
            <el-option
              v-for="option in poolBacktestLimitOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </label>
        <div class="pool-backtest-actions">
          <el-button size="small" :loading="poolBacktestLoading" @click="loadPoolBacktest(false)">读缓存</el-button>
          <el-button size="small" type="primary" :loading="poolBacktestRefreshing" @click="loadPoolBacktest(true)">
            {{ poolBacktestActionText }}
          </el-button>
        </div>
      </div>
      <div class="backtest-metrics">
        <div>
          <span>组合净收益</span>
          <strong :class="{ positive: (poolBacktestResult?.total_profit ?? 0) > 0, negative: (poolBacktestResult?.total_profit ?? 0) < 0 }">
            {{ formatCurrency(poolBacktestResult?.total_profit) }} / {{ formatPercent(poolBacktestReturnPercent) }}
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
        <el-table-column label="指数收益" min-width="96" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.return_percent ?? 0) > 0, negative: (row.return_percent ?? 0) < 0 }">
              {{ formatPercent(row.return_percent) }}
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

    <section class="strategy-search-section">
      <div class="backtest-header">
        <div>
          <h2>轮动策略年度对比</h2>
          <span>
            {{ rotationSearchResult?.done_count ?? rotationSearchRows.length }}/{{ rotationSearchResult?.candidate_count ?? 0 }} 组
            · 达标 {{ rotationSearchQualifiedCount }}
            · {{ rotationSearchYears }}
            · {{ rotationSearchScaleLabel }}
            · {{ rotationSearchResult?.source || '-' }}
            <template v-if="rotationSearchRunning"> · 后台计算中</template>
          </span>
        </div>
        <div class="strategy-search-controls">
          <label>
            <span>年份</span>
            <el-input v-model="rotationSearchYears" size="small" style="width: 112px" />
          </label>
          <label>
            <span>规模</span>
            <el-select v-model="rotationSearchLimit" size="small" style="width: 94px">
              <el-option
                v-for="option in strategySearchLimitOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </label>
          <label>
            <span>排序</span>
            <el-input v-model="rotationSearchRankMetrics" size="small" style="width: 190px" />
          </label>
          <label>
            <span>过滤</span>
            <el-input v-model="rotationSearchMarketFilters" size="small" style="width: 118px" />
          </label>
          <label>
            <span>分数</span>
            <el-input v-model="rotationSearchScoreThresholds" size="small" style="width: 72px" />
          </label>
          <label>
            <span>成交额</span>
            <el-input v-model="rotationSearchMinAmounts" size="small" style="width: 112px" />
          </label>
          <label>
            <span>TopN</span>
            <el-input v-model="rotationSearchTopNValues" size="small" style="width: 72px" />
          </label>
          <label>
            <span>调仓</span>
            <el-input v-model="rotationSearchRebalances" size="small" style="width: 86px" />
          </label>
          <label>
            <span>成本%</span>
            <el-input-number
              v-model="rotationSearchCostPercent"
              size="small"
              :min="0"
              :max="20"
              :step="0.1"
              controls-position="right"
              style="width: 102px"
            />
          </label>
          <label>
            <span>年收益底线%</span>
            <el-input-number
              v-model="rotationSearchMinAnnualReturnPercent"
              size="small"
              :min="-100"
              :max="1000"
              :step="1"
              controls-position="right"
              style="width: 124px"
            />
          </label>
          <el-checkbox v-model="rotationSearchRequireBeatBenchmark" size="small">每年跑赢恒生</el-checkbox>
          <el-button size="small" :loading="rotationSearchLoading" @click="loadRotationSearch(false)">读缓存</el-button>
          <el-button size="small" type="primary" :loading="rotationSearchRefreshing" @click="loadRotationSearch(true)">后台搜索</el-button>
        </div>
      </div>
      <el-table
        :data="rotationSearchRows"
        table-layout="auto"
        :fit="false"
        stripe
      >
        <el-table-column label="达标" min-width="72">
          <template #default="{ row }">
            <span :class="{ positive: row.is_qualified, negative: !row.is_qualified }">
              {{ row.is_qualified ? '是' : '否' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="判定" min-width="118">
          <template #default="{ row }">
            <span :class="{ positive: row.is_qualified, negative: !row.is_qualified }">
              {{ row.qualification_note || '-' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="策略" min-width="260">
          <template #default="{ row }">{{ row.name }}</template>
        </el-table-column>
        <el-table-column label="盈利年份" min-width="82" align="right">
          <template #default="{ row }">{{ row.profitable_year_count }}/{{ row.tested_year_count }}</template>
        </el-table-column>
        <el-table-column label="跑赢恒生" min-width="82" align="right">
          <template #default="{ row }">{{ row.beat_benchmark_year_count }}/{{ row.tested_year_count }}</template>
        </el-table-column>
        <el-table-column label="平均收益" min-width="92" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.average_return_percent ?? 0) > 0, negative: (row.average_return_percent ?? 0) < 0 }">
              {{ formatPercent(row.average_return_percent) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="最差收益" min-width="92" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.min_return_percent ?? 0) > 0, negative: (row.min_return_percent ?? 0) < 0 }">
              {{ formatPercent(row.min_return_percent) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="最差超额" min-width="92" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.min_excess_return_percent ?? 0) > 0, negative: (row.min_excess_return_percent ?? 0) < 0 }">
              {{ formatPercent(row.min_excess_return_percent) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="总净收益" min-width="100" align="right">
          <template #default="{ row }">
            <span :class="{ positive: row.total_profit > 0, negative: row.total_profit < 0 }">
              {{ formatCurrency(row.total_profit) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="年度" min-width="360">
          <template #default="{ row }">
            <div class="strategy-year-chips">
              <span
                v-for="year in row.years"
                :key="year.year"
              >
                {{ year.year }}:
                <strong :class="{ positive: (year.return_percent ?? 0) > 0, negative: (year.return_percent ?? 0) < 0 }">
                  {{ formatPercent(year.return_percent) }}
                </strong>
                / {{ year.benchmark_name || rotationSearchResult?.benchmark_name || '指数' }}
                <strong :class="{ positive: (year.benchmark_return_percent ?? 0) > 0, negative: (year.benchmark_return_percent ?? 0) < 0 }">
                  {{ formatPercent(year.benchmark_return_percent) }}
                </strong>
              </span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="strategy-search-section">
      <div class="backtest-header">
        <div>
          <h2>候选策略年度对比</h2>
          <span>
            {{ strategySearchResult?.done_count ?? strategySearchRows.length }}/{{ strategySearchResult?.candidate_count ?? 0 }} 组
            · 达标 {{ strategySearchQualifiedCount }}
            · {{ strategySearchYears }}
            · {{ strategySearchScaleLabel }}
            · {{ strategySearchResult?.source || '-' }}
            <template v-if="strategySearchRunning"> · 后台计算中</template>
          </span>
        </div>
        <div class="strategy-search-controls">
          <label>
            <span>年份</span>
            <el-input v-model="strategySearchYears" size="small" style="width: 138px" />
          </label>
          <label>
            <span>规模</span>
            <el-select v-model="strategySearchLimit" size="small" style="width: 94px">
              <el-option
                v-for="option in strategySearchLimitOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </label>
          <label>
            <span>分数</span>
            <el-input v-model="strategySearchScoreThresholds" size="small" style="width: 136px" />
          </label>
          <label>
            <span>模型</span>
            <el-input v-model="strategySearchScoreProfiles" size="small" style="width: 250px" />
          </label>
          <label>
            <span>止盈%</span>
            <el-input v-model="strategySearchTakeProfitPercents" size="small" style="width: 112px" />
          </label>
          <label>
            <span>止损%</span>
            <el-input v-model="strategySearchStopLossPercents" size="small" style="width: 92px" />
          </label>
          <label>
            <span>持有日</span>
            <el-input v-model="strategySearchMaxHoldingDays" size="small" style="width: 92px" />
          </label>
          <label>
            <span>成本%</span>
            <el-input-number
              v-model="strategySearchCostPercent"
              size="small"
              :min="0"
              :max="20"
              :step="0.1"
              controls-position="right"
              style="width: 102px"
            />
          </label>
          <label>
            <span>年收益底线%</span>
            <el-input-number
              v-model="strategySearchMinAnnualReturnPercent"
              size="small"
              :min="-100"
              :max="1000"
              :step="1"
              controls-position="right"
              style="width: 124px"
            />
          </label>
          <el-checkbox v-model="strategySearchRequireBeatBenchmark" size="small">每年跑赢恒生</el-checkbox>
          <el-button size="small" :loading="strategySearchLoading" @click="loadStrategySearch(false)">读缓存</el-button>
          <el-button size="small" type="primary" :loading="strategySearchRefreshing" @click="loadStrategySearch(true)">后台搜索</el-button>
        </div>
      </div>
      <el-table
        :data="strategySearchRows"
        table-layout="auto"
        :fit="false"
        stripe
      >
        <el-table-column label="达标" min-width="72">
          <template #default="{ row }">
            <span :class="{ positive: row.is_qualified, negative: !row.is_qualified }">
              {{ row.is_qualified ? '是' : '否' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="判定" min-width="118">
          <template #default="{ row }">
            <span :class="{ positive: row.is_qualified, negative: !row.is_qualified }">
              {{ row.qualification_note || '-' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="策略" min-width="142">
          <template #default="{ row }">{{ row.name }}</template>
        </el-table-column>
        <el-table-column label="盈利年份" min-width="82" align="right">
          <template #default="{ row }">{{ row.profitable_year_count }}/{{ row.tested_year_count }}</template>
        </el-table-column>
        <el-table-column label="跑赢恒生" min-width="82" align="right">
          <template #default="{ row }">{{ row.beat_benchmark_year_count }}/{{ row.tested_year_count }}</template>
        </el-table-column>
        <el-table-column label="平均收益" min-width="92" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.average_return_percent ?? 0) > 0, negative: (row.average_return_percent ?? 0) < 0 }">
              {{ formatPercent(row.average_return_percent) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="最差收益" min-width="92" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.min_return_percent ?? 0) > 0, negative: (row.min_return_percent ?? 0) < 0 }">
              {{ formatPercent(row.min_return_percent) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="最差超额" min-width="92" align="right">
          <template #default="{ row }">
            <span :class="{ positive: (row.min_excess_return_percent ?? 0) > 0, negative: (row.min_excess_return_percent ?? 0) < 0 }">
              {{ formatPercent(row.min_excess_return_percent) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="总净收益" min-width="100" align="right">
          <template #default="{ row }">
            <span :class="{ positive: row.total_profit > 0, negative: row.total_profit < 0 }">
              {{ formatCurrency(row.total_profit) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="年度" min-width="360">
          <template #default="{ row }">
            <div class="strategy-year-chips">
              <span
                v-for="year in row.years"
                :key="year.year"
              >
                {{ year.year }}:
                <strong :class="{ positive: (year.return_percent ?? 0) > 0, negative: (year.return_percent ?? 0) < 0 }">
                  {{ formatPercent(year.return_percent) }}
                </strong>
                / {{ year.benchmark_name || strategySearchResult?.benchmark_name || '指数' }}
                <strong :class="{ positive: (year.benchmark_return_percent ?? 0) > 0, negative: (year.benchmark_return_percent ?? 0) < 0 }">
                  {{ formatPercent(year.benchmark_return_percent) }}
                </strong>
              </span>
            </div>
          </template>
        </el-table-column>
      </el-table>
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
      </el-collapse-item>
    </el-collapse>

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

.strategy-desk {
  border-bottom: 1px solid #e6ebf2;
  margin-bottom: 14px;
  padding-bottom: 14px;
}

.strategy-desk-head {
  align-items: flex-end;
  display: flex;
  gap: 18px;
  justify-content: space-between;
  margin-bottom: 12px;
}

.strategy-desk-head div {
  display: grid;
  gap: 2px;
}

.strategy-desk-head span,
.strategy-detail-title span {
  color: #607086;
  font-size: 12px;
}

.strategy-desk-head strong {
  font-size: 24px;
  line-height: 1.2;
}

.strategy-desk-head p {
  color: #526173;
  font-size: 13px;
  line-height: 1.6;
  margin: 0;
  max-width: 720px;
}

.strategy-desk-body {
  align-items: start;
  display: grid;
  gap: 18px;
  grid-template-columns: minmax(260px, 330px) minmax(520px, 1fr);
}

.strategy-list {
  display: grid;
  gap: 6px;
}

.strategy-list-item {
  align-items: center;
  background: #fff;
  border: 1px solid #dfe7f0;
  border-radius: 6px;
  color: #172033;
  cursor: pointer;
  display: flex;
  gap: 10px;
  justify-content: space-between;
  padding: 10px 12px;
  text-align: left;
}

.strategy-list-item:hover,
.strategy-list-item.active {
  border-color: #8bb8ee;
  background: #f7fbff;
}

.strategy-list-item span {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.strategy-list-item strong {
  font-size: 14px;
  font-weight: 700;
}

.strategy-list-item small {
  color: #607086;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.strategy-status {
  border-radius: 4px;
  font-size: 12px;
  font-style: normal;
  font-weight: 700;
  padding: 3px 7px;
  white-space: nowrap;
}

.strategy-status.is-buy,
.strategy-status.is-watch {
  background: #fff5e6;
  color: #b54708;
}

.strategy-status.is-hold_cash {
  background: #edf4ff;
  color: #175cd3;
}

.strategy-detail {
  border-left: 1px solid #e6ebf2;
  min-width: 0;
  padding-left: 18px;
}

.strategy-detail-title {
  align-items: start;
  display: flex;
  gap: 14px;
  justify-content: space-between;
  margin-bottom: 10px;
}

.strategy-detail-title h2 {
  font-size: 18px;
  margin: 2px 0 0;
}

.strategy-signal-table {
  max-width: 100%;
  overflow-x: auto;
}

.strategy-signal-table table {
  border-collapse: collapse;
  font-size: 13px;
  width: max-content;
  max-width: 100%;
}

.strategy-signal-table th,
.strategy-signal-table td {
  border-bottom: 1px solid #e6ebf2;
  padding: 8px 18px 8px 0;
  text-align: left;
  white-space: nowrap;
}

.strategy-signal-table th {
  color: #607086;
  font-weight: 600;
}

.strategy-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 24px;
  margin-top: 12px;
}

.strategy-facts div {
  display: grid;
  gap: 2px;
}

.strategy-facts span {
  color: #607086;
  font-size: 12px;
}

.strategy-facts strong {
  font-size: 15px;
}

.strategy-note,
.strategy-risk {
  color: #526173;
  font-size: 13px;
  line-height: 1.6;
  margin: 10px 0 0;
}

.strategy-risk {
  color: #7a4b00;
}

.research-collapse {
  border-top: 0;
  margin-top: 4px;
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

.chart-notice {
  color: #7a4b00;
  font-size: 12px;
  margin: 0 0 8px;
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

.pool-backtest-section {
  border-bottom: 1px solid #e6ebf2;
  margin-bottom: 14px;
  padding: 2px 0 14px;
}

.hk-connect-review-section {
  border-bottom: 1px solid #e6ebf2;
  margin-bottom: 14px;
  padding: 2px 0 14px;
}

.hk-connect-review-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 22px;
  margin: 8px 0;
}

.hk-connect-review-summary div {
  display: grid;
  gap: 2px;
}

.hk-connect-review-summary span {
  color: #607086;
  font-size: 12px;
}

.hk-connect-review-summary strong {
  font-size: 15px;
  font-weight: 700;
}

.hk-connect-review-note {
  margin: 6px 0 12px;
}

.hk-connect-selected {
  margin-bottom: 12px;
}

.hk-connect-selected h3,
.hk-connect-candidates h3 {
  font-size: 14px;
  margin: 0 0 8px;
}

.pool-backtest-controls {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin: 0 0 10px;
}

.pool-backtest-controls label {
  align-items: center;
  display: flex;
  gap: 5px;
}

.pool-backtest-controls span {
  color: #607086;
  font-size: 12px;
  white-space: nowrap;
}

.pool-backtest-actions {
  display: flex;
  gap: 8px;
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

.strategy-search-section {
  border-bottom: 1px solid #e6ebf2;
  margin-bottom: 14px;
  padding: 2px 0 14px;
}

.strategy-search-controls {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.strategy-search-controls label {
  align-items: center;
  display: flex;
  gap: 5px;
}

.strategy-search-controls span {
  color: #607086;
  font-size: 12px;
  white-space: nowrap;
}

.strategy-year-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  max-width: 720px;
}

.strategy-year-chips span {
  white-space: nowrap;
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

  .strategy-desk-head {
    align-items: stretch;
    flex-direction: column;
  }

  .strategy-desk-body {
    grid-template-columns: 1fr;
  }

  .strategy-detail {
    border-left: 0;
    border-top: 1px solid #e6ebf2;
    padding-left: 0;
    padding-top: 14px;
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

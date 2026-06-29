<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { DataZoomComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { BarChart, CustomChart, LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { ElMessage } from 'element-plus'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import {
  exportEastmoneyQlibDataset,
  fetchEastmoneyQlibAnalysis,
  fetchEastmoneyAkshareHistory,
  fetchEastmoneyAkshareIntraday,
  fetchEastmoneyTradeAdvice,
  fetchEastmoneyTradeReport,
  fetchEastmoneyTradeWorkbench,
  type EastmoneyAkshareHistoryItem,
  type EastmoneyAkshareIntradayItem,
  type EastmoneyQlibAnalysis,
  type EastmoneyTradeAdvice,
  type EastmoneyTradeAdviceAction,
  type EastmoneyTradeAdviceBacktest,
  type EastmoneyTradeEventEvidence,
  type EastmoneyTradeCandidateAdvice,
  type EastmoneyTradeReport,
  type EastmoneyTradeWorkbench,
} from '@/api/eastmoney'
import { formatChineseCompactNumber } from '@/standard/fanxiu/numberFormat'

echarts.use([DataZoomComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent, BarChart, CustomChart, LineChart, CanvasRenderer])

const PREFERENCE_STORAGE_KEY = 'codeyun.eastmoney.tradeWorkbench.preferences'
const showLegacyTradeTools = false

type MainPeriodValue = 'intraday' | 'five_day' | 'daily' | 'weekly' | 'monthly'
type MorePeriodValue = 'minute_1' | 'minute_5' | 'minute_15' | 'minute_30' | 'minute_60' | 'minute_120' | 'quarterly' | 'yearly'
type PeriodValue = MainPeriodValue | MorePeriodValue
type MarketCode = 'SH' | 'SZ' | 'HK'
type WatchTargetKey = 'robot_etf' | 'robot_ph' | 'kingsoft_cloud' | 'xiaomi' | 'custom'
type WatchTarget = {
  key: WatchTargetKey
  label: string
  market: MarketCode
  symbol: string
  name: string
  startDate: string
}
type TradeWorkbenchPreferences = {
  targetKey?: WatchTargetKey
  period?: PeriodValue
  adjust?: string
  startDate?: string
  endDate?: string
  holdingQuantity?: number
  holdingCostPrice?: number
  holdingCurrentPrice?: number
  accountTotalAsset?: number
  accountCashAvailable?: number
}
type TradeWorkbenchRowKind = 'holding' | 'candidate'
type TradeWorkbenchActionRow = {
  key: string
  kind: TradeWorkbenchRowKind
  item: EastmoneyTradeAdvice | EastmoneyTradeCandidateAdvice
  market: string
  symbol: string
  name: string
  action: EastmoneyTradeAdviceAction
  actionText: string
  headline: string
  primaryOrder: string
  nextTrigger: string
  riskLine: string
  score: number | null
  rankScore: number | null
  quantityText: string
  costText: string
  accountSummary: string
  operationSummary: string
  operationPriceText: string
  operationQuantityText: string
  operationAmountText: string
  operationGuardrailText: string
  planSteps: EastmoneyTradeAdviceStep[]
  strategyText: string
  strategyBasisText: string
  eventText: string
  backtestText: string
  benchmarkText: string
  evidencePreview: string
}

const watchTargets: WatchTarget[] = [
  { key: 'robot_etf', label: 'SH.562500 机器人ETF华夏', market: 'SH', symbol: '562500', name: '机器人ETF华夏', startDate: '2024-01-01' },
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

function readPreferences(): TradeWorkbenchPreferences {
  try {
    const raw = window.localStorage.getItem(PREFERENCE_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as TradeWorkbenchPreferences
    return parsed && typeof parsed === 'object' ? parsed : {}
  }
  catch {
    return {}
  }
}

function readPreferenceNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined
}

function optionalPositiveNumber(value: number | undefined): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined
}

function formatPlainNumber(value: number) {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value)
}

function writePreferences() {
  if (previewTarget.value) return
  const preferences: TradeWorkbenchPreferences = {
    targetKey: targetKey.value,
    period: period.value,
    adjust: adjust.value,
    startDate: startDate.value,
    endDate: endDate.value,
    holdingQuantity: holdingQuantity.value,
    holdingCostPrice: holdingCostPrice.value,
    holdingCurrentPrice: holdingCurrentPrice.value,
    accountTotalAsset: accountTotalAsset.value,
    accountCashAvailable: accountCashAvailable.value,
  }
  window.localStorage.setItem(PREFERENCE_STORAGE_KEY, JSON.stringify(preferences))
}

const savedPreferences = readPreferences()

const targetKey = ref<WatchTargetKey>(
  targetKeys.has(savedPreferences.targetKey as WatchTargetKey) ? savedPreferences.targetKey as WatchTargetKey : 'robot_etf',
)
const previewTarget = ref<WatchTarget | null>(null)
const selectedWatchTarget = computed(() => watchTargets.find((target) => target.key === targetKey.value) ?? watchTargets[0])
const activeTarget = computed(() => previewTarget.value ?? selectedWatchTarget.value)
const activeTargetSource = computed(() => previewTarget.value ? '临时标的' : '自选')
const endDate = ref(savedPreferences.endDate || '')
const period = ref<PeriodValue>(periodValues.has(savedPreferences.period as PeriodValue) ? savedPreferences.period as PeriodValue : 'intraday')
const morePeriod = ref<MorePeriodValue | ''>(
  morePeriodValues.has(period.value as MorePeriodValue) ? period.value as MorePeriodValue : '',
)
const startDate = ref(resolveInitialStartDate(savedPreferences.startDate, period.value, activeTarget.value.startDate))
const adjust = ref(adjustValues.has(savedPreferences.adjust ?? '') ? savedPreferences.adjust ?? '' : '')
const holdingQuantity = ref<number | undefined>(readPreferenceNumber(savedPreferences.holdingQuantity))
const holdingCostPrice = ref<number | undefined>(readPreferenceNumber(savedPreferences.holdingCostPrice))
const holdingCurrentPrice = ref<number | undefined>(readPreferenceNumber(savedPreferences.holdingCurrentPrice))
const accountTotalAsset = ref<number | undefined>(readPreferenceNumber(savedPreferences.accountTotalAsset))
const accountCashAvailable = ref<number | undefined>(readPreferenceNumber(savedPreferences.accountCashAvailable))
const customMarket = ref<MarketCode>('SH')
const customSymbol = ref('')
const customName = ref('')
const loading = ref(false)
const qlibExporting = ref(false)
const qlibAnalysisLoading = ref(false)
const qlibAnalysis = ref<EastmoneyQlibAnalysis | null>(null)
const tradeAdviceLoading = ref(false)
const tradeAdviceResult = ref<EastmoneyTradeAdvice | null>(null)
const tradeWorkbenchLoading = ref(false)
const tradeWorkbench = ref<EastmoneyTradeWorkbench | null>(null)
const tradeReportLoading = ref(false)
const tradeReport = ref<EastmoneyTradeReport | null>(null)
const selectedTradeActionKey = ref('')
const loadedAt = ref('')
const rows = ref<EastmoneyAkshareHistoryItem[]>([])
const intradayRows = ref<EastmoneyAkshareIntradayItem[]>([])
const chartNotice = ref('')
const chartRef = ref<HTMLDivElement | null>(null)

const emptyTradeAdvice: EastmoneyTradeAdvice = {
  market: '',
  symbol: '',
  name: '',
  action: 'no_data',
  action_text: '等待数据',
  headline: '正在生成当前标的的买卖维护建议。',
  primary_order: '先读取持仓、行情和策略评分。',
  next_trigger: '-',
  risk_line: '-',
  recovery_line: '-',
  operation: {
    intent: 'none',
    side: 'none',
    order_type: 'none',
    price: null,
    price_text: '-',
    trigger_price: null,
    trigger_price_text: '-',
    quantity: 0,
    quantity_text: '-',
    amount: null,
    amount_text: '-',
    cash_budget: null,
    cash_budget_text: '-',
    stop_price: null,
    stop_price_text: '-',
    recovery_price: null,
    recovery_price_text: '-',
    lot_size: 100,
    summary: '先读取持仓、行情和策略评分。',
    guardrail_text: '数据不完整时不生成下单动作。',
  },
  evidence: ['交易建议接口尚未返回。'],
  steps: [],
  event_evidence: [],
  strategy_status: '等待交易建议',
  strategy_score: null,
  strategy_rules: [],
  backtests: [],
  position: {
    quantity: 0,
    cost_price: null,
    current_price: null,
    market_value: null,
    quantity_text: '无持仓',
    cost_price_text: '-',
    current_price_text: '-',
    market_value_text: '-',
  },
  account: {
    total_asset: null,
    cash_available: null,
    position_weight_percent: null,
    max_single_position_percent: null,
    first_lot_budget: null,
    summary: '未读取到账户资金约束。',
  },
  source: 'frontend.empty',
}
let chart: echarts.ECharts | null = null
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
const marketOptions: { label: string, value: MarketCode }[] = [
  { label: '沪', value: 'SH' },
  { label: '深', value: 'SZ' },
  { label: '港', value: 'HK' },
]
const isIntraday = computed(() => period.value === 'intraday' || period.value === 'five_day' || period.value.startsWith('minute_'))
const selectedPeriodLabel = computed(() => {
  return [...periodOptions, ...morePeriodOptions].find((option) => option.value === period.value)?.label ?? '分时'
})
const latestRow = computed(() => rows.value.at(-1) ?? null)
const latestIntradayRow = computed(() => intradayRows.value.at(-1) ?? null)
const firstRow = computed(() => rows.value[0] ?? null)
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
const metricLatestClose = computed(() => {
  return isIntraday.value ? latestIntradayRow.value?.close : latestRow.value?.close
})
const metricChange = computed(() => {
  return isIntraday.value ? intradayChange.value : closeChange.value
})
const metricChangeRate = computed(() => {
  return isIntraday.value ? intradayChangeRate.value : closeChangeRate.value
})
const metricTotalAmount = computed(() => {
  return isIntraday.value ? intradayTotalAmount.value : totalAmount.value
})
const metricMaxVolumeLabel = computed(() => {
  if (!isIntraday.value) {
    return `${maxVolumeRow.value?.date || '-'} · ${formatVolume(maxVolumeRow.value?.volume)}`
  }
  return `${maxIntradayVolumeRow.value?.time?.slice(11, 16) || '-'} · ${formatVolume(maxIntradayVolumeRow.value?.volume)}`
})
const metricRowCount = computed(() => isIntraday.value ? intradayRows.value.length : rows.value.length)
const tradeAdvice = computed(() => tradeAdviceResult.value ?? emptyTradeAdvice)
const tradeCandidatePool = ref<'watchlist' | 'hk_pool'>('watchlist')
const manualTradeParams = computed(() => ({
  quantity: optionalPositiveNumber(holdingQuantity.value),
  cost_price: optionalPositiveNumber(holdingCostPrice.value),
  current_price: optionalPositiveNumber(holdingCurrentPrice.value),
  total_asset: optionalPositiveNumber(accountTotalAsset.value),
  cash_available: optionalPositiveNumber(accountCashAvailable.value),
}))
const manualTradeWorkbenchParams = computed(() => ({
  focus_market: activeTarget.value.market,
  focus_symbol: activeTarget.value.symbol,
  focus_name: activeTarget.value.name,
  focus_quantity: optionalPositiveNumber(holdingQuantity.value),
  focus_cost_price: optionalPositiveNumber(holdingCostPrice.value),
  focus_current_price: optionalPositiveNumber(holdingCurrentPrice.value),
  total_asset: optionalPositiveNumber(accountTotalAsset.value),
  cash_available: optionalPositiveNumber(accountCashAvailable.value),
}))
const holdingInputSummary = computed(() => {
  const parts = []
  if (holdingQuantity.value && holdingQuantity.value > 0) parts.push(`持仓 ${formatPlainNumber(holdingQuantity.value)} 份`)
  if (holdingCostPrice.value && holdingCostPrice.value > 0) parts.push(`成本 ${holdingCostPrice.value}`)
  if (holdingCurrentPrice.value && holdingCurrentPrice.value > 0) parts.push(`现价 ${holdingCurrentPrice.value}`)
  return parts.length ? parts.join('，') : '未填写时使用同步账户快照'
})
const tradeWorkbenchHoldings = computed(() => tradeWorkbench.value?.holdings ?? [])
const tradeWorkbenchCandidates = computed(() => tradeWorkbench.value?.candidates ?? [])
const tradeWorkbenchActionRows = computed<TradeWorkbenchActionRow[]>(() => {
  const holdings = tradeWorkbenchHoldings.value.map((item) => ({
    key: `holding-${item.market}.${item.symbol}`,
    kind: 'holding' as const,
    item,
    market: item.market,
    symbol: item.symbol,
    name: item.name,
    action: item.action,
    actionText: item.action_text,
    headline: item.headline,
    primaryOrder: item.primary_order,
    nextTrigger: item.next_trigger,
    riskLine: item.risk_line,
    score: item.strategy_score,
    rankScore: null,
    quantityText: item.position?.quantity_text ?? '-',
    costText: item.position?.cost_price_text ?? '-',
    accountSummary: item.account?.summary ?? '等待账户资金约束',
    operationSummary: item.operation?.summary || item.primary_order,
    operationPriceText: item.operation?.price_text || '-',
    operationQuantityText: item.operation?.quantity_text || item.position?.quantity_text || '-',
    operationAmountText: item.operation?.amount_text || '-',
    operationGuardrailText: item.operation?.guardrail_text || item.risk_line,
    planSteps: item.steps ?? [],
    strategyText: item.strategy_status,
    strategyBasisText: formatTradeStrategyBasis(item.strategy_status, item.evidence?.[0]),
    eventText: formatTradeEventSummary(item.event_evidence),
    backtestText: formatTradeBacktestSummary(item.backtests?.[0]),
    benchmarkText: formatTradeBacktestBenchmark(item.backtests?.[0]),
    evidencePreview: item.evidence?.[0] ?? '等待策略依据',
  }))
  const candidates = tradeWorkbenchCandidates.value.map((item) => ({
    key: `candidate-${item.market}.${item.symbol}`,
    kind: 'candidate' as const,
    item,
    market: item.market,
    symbol: item.symbol,
    name: item.name,
    action: item.action,
    actionText: item.action_text,
    headline: item.headline,
    primaryOrder: item.primary_order,
    nextTrigger: item.next_trigger,
    riskLine: item.risk_line,
    score: item.strategy_score,
    rankScore: item.rank_score,
    quantityText: '-',
    costText: '-',
    accountSummary: item.account?.summary ?? '等待账户资金约束',
    operationSummary: item.operation?.summary || item.primary_order,
    operationPriceText: item.operation?.price_text || '-',
    operationQuantityText: item.operation?.quantity_text || '-',
    operationAmountText: item.operation?.amount_text || item.operation?.cash_budget_text || '-',
    operationGuardrailText: item.operation?.guardrail_text || item.risk_line,
    planSteps: item.steps ?? [],
    strategyText: item.strategy_score == null ? '等待策略分' : `策略分 ${item.strategy_score}`,
    strategyBasisText: formatTradeStrategyBasis(
      item.strategy_score == null ? '等待策略分' : `策略分 ${item.strategy_score}`,
      item.evidence?.[0],
    ),
    eventText: formatTradeEventSummary(item.event_evidence),
    backtestText: formatTradeBacktestSummary(item.backtests?.[0]),
    benchmarkText: formatTradeBacktestBenchmark(item.backtests?.[0]),
    evidencePreview: item.evidence?.[0] ?? '等待策略依据',
  }))
  return [...holdings, ...candidates].sort((left, right) => {
    return tradeActionPriority(right.action, right.kind) - tradeActionPriority(left.action, left.kind)
      || (right.rankScore ?? 0) - (left.rankScore ?? 0)
      || left.key.localeCompare(right.key)
  })
})
const selectedTradeAction = computed(() => {
  return tradeWorkbenchActionRows.value.find((row) => row.key === selectedTradeActionKey.value)
    ?? tradeWorkbenchActionRows.value[0]
    ?? null
})
const tradeWorkbenchAccountText = computed(() => {
  const account = tradeWorkbench.value?.account
  if (!account) return '等待账户资金约束'
  const maxSinglePosition = formatOptionalPercent(account.max_single_position_percent, '未设')
  const firstLotCash = formatOptionalPercent(account.first_lot_cash_percent, '未设')
  const firstLotAsset = formatOptionalPercent(account.first_lot_asset_percent, '未设')
  return `总资产 ${formatCurrency(account.total_asset)} · 可用现金 ${formatCurrency(account.cash_available)} · 单票上限 ${maxSinglePosition} · 首仓不超过现金 ${firstLotCash} / 资产 ${firstLotAsset}`
})
const tradeReportMarkdown = computed(() => tradeReport.value?.markdown?.trim() || '')
const renderedTradeReportHtml = computed(() => {
  const markdown = tradeReportMarkdown.value || tradeReportPlaceholder.value
  const html = marked.parse(markdown, {
    async: false,
    breaks: true,
    gfm: true,
  })
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  const firstHeading = parsed.body.querySelector('h1')
  if (firstHeading?.textContent?.trim() === '股票操作报告') {
    firstHeading.remove()
  }
  return DOMPurify.sanitize(parsed.body.innerHTML)
})
const tradeReportUpdatedText = computed(() => {
  const timestamp = tradeReport.value?.updated_at
  if (!timestamp) return '暂无报告更新'
  return new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false })
})
const tradeReportPlaceholder = computed(() => [
  '# 股票操作报告',
  '',
  '暂无 AI 撰写的报告。',
  '',
  '有需要时在 Codex 对话里沟通更新，报告会写入本机数据库后在这里展示。',
].join('\n'))
const tradeReportHoldingRows = computed(() => tradeWorkbenchActionRows.value.filter((row) => row.kind === 'holding'))
const compactHoldingFacts = computed(() => tradeReportHoldingRows.value.map((row) => ({
  key: row.key,
  name: row.name || `${row.market}.${row.symbol}`,
  action: row.actionText,
  position: row.quantityText,
  price: row.operationPriceText,
  amount: row.operationAmountText,
  guardrail: row.operationGuardrailText,
})))
const primaryTradeBacktest = computed(() => {
  return tradeAdvice.value.backtests?.[0] ?? selectedTradeAction.value?.item.backtests?.[0] ?? null
})
const primaryTradeBacktestBenchmarkText = computed(() => {
  const item = primaryTradeBacktest.value
  if (!item) return '等待指数对比'
  return `${item.benchmark_name} ${formatSignedPercent(item.benchmark_return_percent)} · 超额 ${formatSignedPercent(item.excess_return_percent)}`
})
function tradeAdviceActionClass(action: EastmoneyTradeAdviceAction) {
  if (action === 'buy') return 'is-buy'
  if (action === 'risk_reduce') return 'is-risk'
  if (action === 'sell_plan') return 'is-sell-plan'
  if (action === 'hold') return 'is-hold'
  return 'is-watch'
}

function tradeActionPriority(action: EastmoneyTradeAdviceAction, kind: TradeWorkbenchRowKind) {
  if (action === 'risk_reduce') return 100
  if (action === 'sell_plan') return 90
  if (action === 'buy') return 80
  if (kind === 'holding' && action === 'hold') return 60
  if (action === 'buy_watch') return 40
  return 10
}

function previewTradeAction(row: TradeWorkbenchActionRow) {
  selectedTradeActionKey.value = row.key
}

function formatTradeBacktestSummary(item: EastmoneyTradeAdviceBacktest | null | undefined) {
  if (!item) return '等待回测'
  return item.summary || `${item.strategy_name} ${formatSignedPercent(item.total_return_percent)}`
}

function formatTradeBacktestBenchmark(item: EastmoneyTradeAdviceBacktest | null | undefined) {
  if (!item) return '等待指数对比'
  return `${item.benchmark_name} ${formatSignedPercent(item.benchmark_return_percent)} · 超额 ${formatSignedPercent(item.excess_return_percent)}`
}

function formatTradeStrategyBasis(strategyText: string | null | undefined, evidenceText: string | null | undefined) {
  const strategy = strategyText?.trim()
  const evidence = evidenceText?.trim()
  if (strategy && evidence && evidence !== strategy) return `${strategy} · ${evidence}`
  return strategy || evidence || '等待策略依据'
}

function formatTradeEventSummary(items: EastmoneyTradeEventEvidence[] | null | undefined) {
  if (!items?.length) return '暂无直接事件'
  const supportCount = items.filter((item) => item.impact === 'support').length
  const riskCount = items.filter((item) => item.impact === 'risk').length
  const latest = [...items].sort((left, right) => right.event_date.localeCompare(left.event_date))[0]
  const counts = [
    supportCount ? `${supportCount} 条支撑` : '',
    riskCount ? `${riskCount} 条风险` : '',
  ].filter(Boolean).join('，')
  return `${counts || '事件待判断'} · ${latest.event_date} ${latest.title}`
}

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

function formatOptionalPercent(value: number | null | undefined, fallback = '-') {
  if (value == null || !Number.isFinite(value)) return fallback
  return `${value.toFixed(0)}%`
}

function formatSignedPercent(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '-'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
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

function normalizeCustomTargetSymbol(market: MarketCode, symbol: string) {
  const trimmed = symbol.trim().toUpperCase()
  const prefixed = trimmed.match(/^(SH|SZ|HK)[\s.:-]*(.+)$/)
  const resolvedMarket = prefixed ? prefixed[1] as MarketCode : market
  const rawSymbol = (prefixed ? prefixed[2] : trimmed).replace(/[^0-9A-Z]/g, '')
  if (!rawSymbol) return null
  if (/^\d+$/.test(rawSymbol)) {
    const width = resolvedMarket === 'HK' ? 5 : 6
    return {
      market: resolvedMarket,
      symbol: rawSymbol.padStart(width, '0'),
    }
  }
  return {
    market: resolvedMarket,
    symbol: rawSymbol,
  }
}

function applyCustomTarget() {
  const normalized = normalizeCustomTargetSymbol(customMarket.value, customSymbol.value)
  if (!normalized) {
    ElMessage.warning('先输入股票或ETF代码')
    return
  }
  customMarket.value = normalized.market
  customSymbol.value = normalized.symbol
  const name = customName.value.trim() || `${normalized.market}.${normalized.symbol}`
  previewTarget.value = {
    key: 'custom',
    label: `${normalized.market}.${normalized.symbol} ${name}`,
    market: normalized.market,
    symbol: normalized.symbol,
    name,
    startDate: '1990-01-01',
  }
  resetLocalHistoryRange()
  startIntradayRefreshTimer()
  void loadHistory()
  void loadTradeAdvice(false)
  void loadTradeWorkbench(false)
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

function isWeekend(date: Date) {
  const day = date.getDay()
  return day === 0 || day === 6
}

function minutesSinceMidnight(date: Date) {
  return date.getHours() * 60 + date.getMinutes()
}

function isLikelyMarketClosedNow(date = new Date()) {
  if (isWeekend(date)) return true
  const minutes = minutesSinceMidnight(date)
  if (activeTarget.value.market === 'HK') {
    return minutes < 9 * 60 + 30 || minutes > 16 * 60 + 15
  }
  return minutes < 9 * 60 + 15 || minutes > 15 * 60 + 15
}

function buildIntradayClosedNotice(tradeDate: string) {
  return tradeDate
    ? `当前休市或未开盘，显示 ${tradeDate} 的${selectedPeriodLabel.value}数据`
    : `当前休市或未开盘，显示最近交易日的${selectedPeriodLabel.value}数据`
}

function buildIntradayFallbackNotice(error: string | undefined, tradeDate = '', refresh = false) {
  if (error) {
    if (error.includes('本地暂无') && !refresh) return `${selectedPeriodLabel.value}${error}`
    if (tradeDate && refresh) {
      if (error.includes('补分时失败')) return `${selectedPeriodLabel.value}${error}`
      return `${selectedPeriodLabel.value}补分时失败：${error}`
    }
    const dateText = tradeDate ? `（目标交易日 ${tradeDate}）` : ''
    return `${selectedPeriodLabel.value}获取失败${dateText}：${error}`
  }
  if (isLikelyMarketClosedNow()) {
    return tradeDate
      ? `${selectedPeriodLabel.value}本地暂无 ${tradeDate} 已持久化数据`
      : `${selectedPeriodLabel.value}本地暂无已持久化数据`
  }
  return `${selectedPeriodLabel.value}暂无数据`
}

function selectMorePeriod(value: MorePeriodValue | '') {
  if (!value) return
  period.value = value
}

async function loadHistory(refresh = false) {
  if (isIntraday.value) {
    await loadIntraday(refresh)
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
    rows.value = result.items ?? []
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

async function loadIntraday(refresh = false) {
  const sequence = loadSequence + 1
  loadSequence = sequence
  loading.value = true
  rows.value = []
  chartNotice.value = ''
  try {
    const intradayParams = intradayParamsForApi()
    const marketClosed = isLikelyMarketClosedNow()
    const result = await fetchEastmoneyAkshareIntraday({
      market: activeTarget.value.market,
      symbol: activeTarget.value.symbol,
      name: activeTarget.value.name,
      period: intradayParams.period,
      day_count: intradayParams.day_count,
      refresh,
    })
    if (sequence !== loadSequence) return
    intradayRows.value = result.items ?? []
    const targetTradeDate = result.target_trade_date || result.trade_date
    const displayTradeDate = result.display_trade_date || result.trade_date
    if (result.items.length > 0 && result.error) {
      chartNotice.value = result.error
    }
    else if (result.items.length > 0 && (marketClosed || result.provider === 'market-data')) {
      chartNotice.value = buildIntradayClosedNotice(displayTradeDate)
    }
    if (result.items.length === 0) {
      chartNotice.value = buildIntradayFallbackNotice(result.error, targetTradeDate, refresh)
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
    void loadTradeAdvice(refresh)
    void loadTradeWorkbench(refresh)
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

async function loadTradeAdvice(refresh = false) {
  tradeAdviceLoading.value = true
  try {
    tradeAdviceResult.value = await fetchEastmoneyTradeAdvice({
      market: activeTarget.value.market,
      symbol: activeTarget.value.symbol,
      name: activeTarget.value.name,
      start_date: activeTarget.value.startDate,
      refresh,
      _ts: Date.now(),
      ...manualTradeParams.value,
    })
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取交易建议失败：${message}`)
  }
  finally {
    tradeAdviceLoading.value = false
  }
}

async function loadTradeWorkbench(refresh = false) {
  tradeWorkbenchLoading.value = true
  try {
    tradeWorkbench.value = await fetchEastmoneyTradeWorkbench({
      candidate_pool: tradeCandidatePool.value,
      holding_limit: 12,
      candidate_limit: 6,
      screen_limit: tradeCandidatePool.value === 'hk_pool' ? 120 : 20,
      refresh,
      _ts: Date.now(),
      ...manualTradeWorkbenchParams.value,
    })
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取交易工作台失败：${message}`)
  }
  finally {
    tradeWorkbenchLoading.value = false
  }
}

async function loadTradeReport() {
  tradeReportLoading.value = true
  try {
    tradeReport.value = await fetchEastmoneyTradeReport()
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    ElMessage.error(`读取股票报告失败：${message}`)
  }
  finally {
    tradeReportLoading.value = false
  }
}

function returnToWatchTarget() {
  if (!previewTarget.value) return
  previewTarget.value = null
  resetLocalHistoryRange()
  startIntradayRefreshTimer()
  void loadHistory()
  void loadTradeAdvice(false)
  void loadTradeWorkbench(false)
}

function applyHoldingInputs() {
  writePreferences()
  void loadTradeAdvice(false)
  void loadTradeWorkbench(false)
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

function handleMarketDetailsToggle(event: Event) {
  const element = event.currentTarget as HTMLDetailsElement | null
  if (!element?.open) return
  void nextTick(() => {
    chart?.resize()
    renderChart()
  })
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
    if (intradayRows.value.length === 0) return
    void loadIntraday(false)
  }, 60000)
}

function stopIntradayRefreshTimer() {
  if (intradayRefreshTimer != null) {
    window.clearInterval(intradayRefreshTimer)
    intradayRefreshTimer = null
  }
}

onMounted(() => {
  void loadTradeReport()
  void loadHistory()
  void loadTradeAdvice(false)
  void loadTradeWorkbench(false)
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
    void loadTradeAdvice(false)
  }
})

watch(tradeCandidatePool, () => {
  void loadTradeWorkbench(false)
})

watch(tradeWorkbenchActionRows, (rows) => {
  if (!rows.length) {
    selectedTradeActionKey.value = ''
    return
  }
  if (!rows.some((row) => row.key === selectedTradeActionKey.value)) {
    selectedTradeActionKey.value = rows[0]!.key
  }
})

watch([targetKey, period, adjust, startDate, endDate, holdingQuantity, holdingCostPrice, holdingCurrentPrice, accountTotalAsset, accountCashAvailable], () => {
  writePreferences()
})

</script>

<template>
  <div class="trade-workbench-page">
    <header class="trade-workbench-toolbar">
      <div class="trade-workbench-title">
        <h1>股票操作建议</h1>
        <div class="active-target-meta">
          <strong>{{ activeTarget.name }}</strong>
          <span>{{ activeTarget.market }}.{{ activeTarget.symbol }} · {{ activeTargetSource }}</span>
          <el-button v-if="previewTarget" link type="primary" @click="returnToWatchTarget">
            返回自选
          </el-button>
        </div>
      </div>
      <div class="trade-workbench-controls">
        <div class="watch-target-control">
          <span>当前标的</span>
          <el-select v-model="targetKey" class="target-select" aria-label="观察标的">
            <el-option
              v-for="target in watchTargets"
              :key="target.key"
              :label="target.label"
              :value="target.key"
            />
          </el-select>
        </div>
        <div class="custom-target-control">
          <span>临时标的</span>
          <el-select v-model="customMarket" class="custom-market-select" aria-label="临时标的市场">
            <el-option
              v-for="option in marketOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-input
            v-model.trim="customSymbol"
            class="custom-symbol-input"
            placeholder="代码"
            clearable
            @keyup.enter="applyCustomTarget"
          />
          <el-input
            v-model.trim="customName"
            class="custom-name-input"
            placeholder="名称可选"
            clearable
            @keyup.enter="applyCustomTarget"
          />
          <el-button @click="applyCustomTarget">
            查看
          </el-button>
        </div>
      </div>
    </header>

    <section class="trade-workbench-chart-section is-primary-chart" v-loading="loading">
      <div class="chart-section-head">
        <div>
          <span>行情图</span>
          <strong>{{ activeTarget.name }} · {{ selectedPeriodLabel }}</strong>
        </div>
        <div class="chart-controls">
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
          <el-button :loading="qlibExporting" @click="exportQlibDataset">更新评分</el-button>
          <el-button v-if="isIntraday" :loading="loading" @click="loadHistory(false)">加载分时</el-button>
          <el-button v-if="isIntraday" type="primary" :loading="loading" @click="loadHistory(true)">补分时</el-button>
          <el-button v-else type="primary" :loading="loading" @click="loadHistory(false)">加载行情</el-button>
        </div>
      </div>
      <div v-if="chartNotice" class="chart-notice">{{ chartNotice }}</div>
      <div ref="chartRef" class="trade-workbench-chart" />
    </section>

    <section class="trade-report-section" v-loading="tradeReportLoading">
      <div class="trade-report-main">
        <div class="trade-report-head">
          <div>
            <span>AI 撰写报告</span>
            <strong>股票操作报告</strong>
            <small>更新：{{ tradeReportUpdatedText }}</small>
          </div>
        </div>
        <article class="trade-report-markdown" v-html="renderedTradeReportHtml"></article>
      </div>
      <aside class="trade-report-side">
        <div>
          <span>账户约束</span>
          <strong>{{ tradeWorkbenchAccountText }}</strong>
        </div>
        <table v-if="compactHoldingFacts.length" class="compact-holding-table">
          <thead>
            <tr>
              <th>持仓</th>
              <th>建议</th>
              <th>数量/价格</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in compactHoldingFacts" :key="row.key">
              <td>{{ row.name }}</td>
              <td>{{ row.action }}</td>
              <td>{{ row.position }} · {{ row.price }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else>等待账户持仓数据。</p>
      </aside>
    </section>

    <details v-if="showLegacyTradeTools" class="legacy-trade-tools">
      <summary>旧版分析工具</summary>

    <section class="trade-input-strip">
      <div class="trade-input-summary">
        <strong>当前计算口径</strong>
        <span>{{ holdingInputSummary }}</span>
      </div>
      <div class="trade-input-grid">
        <label>
          <span>持仓数量</span>
          <el-input-number
            v-model="holdingQuantity"
            :min="0"
            :step="100"
            :controls="false"
            placeholder="如 23000"
          />
        </label>
        <label>
          <span>成本价</span>
          <el-input-number
            v-model="holdingCostPrice"
            :min="0"
            :precision="3"
            :step="0.001"
            :controls="false"
            placeholder="如 1.18"
          />
        </label>
        <label>
          <span>现价覆盖</span>
          <el-input-number
            v-model="holdingCurrentPrice"
            :min="0"
            :precision="3"
            :step="0.001"
            :controls="false"
            placeholder="不填用行情"
          />
        </label>
        <label>
          <span>总资产</span>
          <el-input-number
            v-model="accountTotalAsset"
            :min="0"
            :step="1000"
            :controls="false"
            placeholder="可选"
          />
        </label>
        <label>
          <span>可用现金</span>
          <el-input-number
            v-model="accountCashAvailable"
            :min="0"
            :step="1000"
            :controls="false"
            placeholder="可选"
          />
        </label>
        <el-button type="primary" :loading="tradeAdviceLoading || tradeWorkbenchLoading" @click="applyHoldingInputs">
          重新计算
        </el-button>
      </div>
    </section>

    <section class="trade-workbench-section" v-loading="tradeWorkbenchLoading">
      <div class="trade-workbench-head">
        <div>
          <strong>账户操作清单</strong>
          <span>{{ tradeWorkbench?.summary?.headline || '正在汇总持仓和候选标的。' }}</span>
          <small>{{ tradeWorkbenchAccountText }}</small>
        </div>
        <div class="trade-candidates-actions">
          <el-segmented
            v-model="tradeCandidatePool"
            :options="[
              { label: '自选候选', value: 'watchlist' },
              { label: '可买候选', value: 'hk_pool' },
            ]"
          />
        </div>
      </div>
      <div class="trade-workbench-inspector">
        <div v-if="tradeWorkbenchActionRows.length" class="trade-action-table-wrap">
          <table class="trade-action-table">
            <thead>
              <tr>
                <th>标的</th>
                <th>操作建议</th>
                <th>价格 / 数量 / 金额</th>
                <th>触发 / 风控</th>
                <th>依据</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in tradeWorkbenchActionRows"
                :key="row.key"
                :class="{ 'is-active': selectedTradeAction?.key === row.key }"
                @click="previewTradeAction(row)"
              >
                <td>
                  <button type="button">{{ row.market }}.{{ row.symbol }} {{ row.name }}</button>
                  <small>{{ row.kind === 'holding' ? '持仓维护' : '可买候选' }}</small>
                </td>
                <td>
                  <em :class="['trade-advice-action', tradeAdviceActionClass(row.action)]">{{ row.actionText }}</em>
                  <strong>{{ row.operationSummary }}</strong>
                  <small>{{ row.operationGuardrailText }}</small>
                </td>
                <td>
                  <strong>{{ row.operationPriceText }}</strong>
                  <small>{{ row.operationQuantityText }} · {{ row.operationAmountText }}</small>
                </td>
                <td>
                  <strong>{{ row.nextTrigger }}</strong>
                  <small>{{ row.riskLine }}</small>
                </td>
                <td>
                  <span class="trade-action-position">{{ row.kind === 'holding' ? row.quantityText : row.score ?? '-' }}</span>
                  <small>{{ row.eventText }}</small>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="trade-candidate-empty">暂无账户操作项。</div>

        <aside v-if="selectedTradeAction" class="trade-action-detail">
          <div class="trade-action-detail-title">
            <em :class="['trade-advice-action', tradeAdviceActionClass(selectedTradeAction.action)]">{{ selectedTradeAction.actionText }}</em>
            <strong>{{ selectedTradeAction.market }}.{{ selectedTradeAction.symbol }} {{ selectedTradeAction.name }}</strong>
          </div>
          <p>{{ selectedTradeAction.headline }}</p>
          <div class="trade-candidate-order">{{ selectedTradeAction.operationSummary }}</div>
          <div class="trade-guardrail-note">{{ selectedTradeAction.operationGuardrailText }}</div>
          <div class="trade-account-note">{{ selectedTradeAction.accountSummary }}</div>
          <dl class="trade-action-evidence-strip">
            <dt>策略依据</dt>
            <dd>{{ selectedTradeAction.strategyBasisText }}</dd>
            <dt>新闻政策</dt>
            <dd>{{ formatTradeEventSummary(selectedTradeAction.item.event_evidence) }}</dd>
            <dt>历史回测</dt>
            <dd>{{ selectedTradeAction.backtestText }}</dd>
            <dt>指数对比</dt>
            <dd>{{ selectedTradeAction.benchmarkText }}</dd>
          </dl>
          <dl>
            <dt>{{ selectedTradeAction.kind === 'holding' ? '持仓' : '策略分' }}</dt>
            <dd>{{ selectedTradeAction.kind === 'holding' ? selectedTradeAction.quantityText : selectedTradeAction.score ?? '-' }}</dd>
            <dt>{{ selectedTradeAction.kind === 'holding' ? '成本' : '排序分' }}</dt>
            <dd>{{ selectedTradeAction.kind === 'holding' ? selectedTradeAction.costText : selectedTradeAction.rankScore?.toFixed(1) ?? '-' }}</dd>
            <dt>价格</dt>
            <dd>{{ selectedTradeAction.operationPriceText }}</dd>
            <dt>数量</dt>
            <dd>{{ selectedTradeAction.operationQuantityText }}</dd>
            <dt>金额</dt>
            <dd>{{ selectedTradeAction.operationAmountText }}</dd>
            <dt>触发</dt>
            <dd>{{ selectedTradeAction.nextTrigger }}</dd>
            <dt>风控</dt>
            <dd>{{ selectedTradeAction.riskLine }}</dd>
            <dt>边界</dt>
            <dd>{{ selectedTradeAction.operationGuardrailText }}</dd>
            <dt>依据</dt>
            <dd>{{ selectedTradeAction.evidencePreview }}</dd>
          </dl>
          <table v-if="selectedTradeAction.planSteps.length" class="trade-plan-table is-compact">
            <tbody>
              <tr v-for="step in selectedTradeAction.planSteps" :key="step.label">
                <th>{{ step.label }}</th>
                <td><strong>{{ step.value }}</strong><span>{{ step.note }}</span></td>
              </tr>
            </tbody>
          </table>
          <details class="trade-action-detail-evidence">
            <summary>依据和回测</summary>
            <ul>
              <li v-for="item in selectedTradeAction.item.evidence ?? []" :key="item">{{ item }}</li>
            </ul>
            <div v-if="selectedTradeAction.item.event_evidence?.length" class="trade-event-evidence-list">
              <div v-for="item in selectedTradeAction.item.event_evidence ?? []" :key="item.url || item.title">
                <strong>{{ item.event_date }} · {{ item.title }}</strong>
                <span>{{ item.summary }}</span>
                <a :href="item.url" target="_blank" rel="noreferrer">{{ item.source }} · {{ item.impact }}</a>
              </div>
            </div>
            <div v-if="selectedTradeAction.item.backtests?.length" class="trade-action-backtest-list">
              <div v-for="item in selectedTradeAction.item.backtests ?? []" :key="item.strategy_id">
                <strong>{{ item.strategy_name }}</strong>
                <span>{{ item.summary }}</span>
                <small>{{ formatTradeBacktestBenchmark(item) }} · 交易 {{ item.trade_count }} 次</small>
              </div>
            </div>
          </details>
        </aside>
      </div>
      <details class="trade-policy-evidence">
        <summary>策略和候选池依据</summary>
        <div class="trade-policy-evidence-body">
          <section>
            <strong>{{ tradeWorkbench?.policy?.name || '标准持仓维护策略' }}</strong>
            <ul>
              <li v-for="rule in tradeWorkbench?.policy?.rules || []" :key="rule">{{ rule }}</li>
            </ul>
          </section>
          <section>
            <strong>{{ tradeWorkbench?.candidate_pool_definition?.name || '候选池' }}</strong>
            <p>{{ tradeWorkbench?.candidate_pool_definition?.description || '等待候选池定义。' }}</p>
            <p>{{ tradeWorkbench?.candidate_pool_definition?.source || '-' }}</p>
          </section>
        </div>
      </details>
    </section>

    <details class="trade-advice-panel">
      <summary>当前标的单独测算</summary>
      <section class="trade-advice-section" v-loading="tradeAdviceLoading">
        <div class="trade-advice-main">
        <div class="trade-advice-title">
          <em :class="['trade-advice-action', tradeAdviceActionClass(tradeAdvice.action)]">
            {{ tradeAdvice.action_text }}
          </em>
          <h2>{{ tradeAdvice.headline }}</h2>
        </div>
        <div class="trade-advice-order">{{ tradeAdvice.operation?.summary || tradeAdvice.primary_order }}</div>
        <div class="trade-guardrail-note">{{ tradeAdvice.operation?.guardrail_text || tradeAdvice.risk_line }}</div>
        <div class="trade-advice-lines">
          <div>
            <span>价格/触发</span>
            <strong>{{ tradeAdvice.operation?.price_text || '-' }} / {{ tradeAdvice.operation?.trigger_price_text || '-' }}</strong>
          </div>
          <div>
            <span>数量/金额</span>
            <strong>{{ tradeAdvice.operation?.quantity_text || '-' }} / {{ tradeAdvice.operation?.amount_text || '-' }}</strong>
          </div>
          <div>
            <span>风险线</span>
            <strong>{{ tradeAdvice.operation?.stop_price_text || tradeAdvice.risk_line }}</strong>
          </div>
        </div>
        </div>
        <div class="trade-advice-side">
          <div class="trade-position-facts">
          <div>
            <span>持仓</span>
            <strong>{{ tradeAdvice.position.quantity_text }}</strong>
          </div>
          <div>
            <span>成本</span>
            <strong>{{ tradeAdvice.position.cost_price_text }}</strong>
          </div>
          <div>
            <span>市值</span>
            <strong>{{ tradeAdvice.position.market_value_text }}</strong>
          </div>
          <div>
            <span>策略分</span>
            <strong :class="qlibSignalClass(qlibAnalysis?.signal)">{{ tradeAdvice.strategy_score ?? '-' }}</strong>
          </div>
          </div>
          <table v-if="tradeAdvice.steps?.length" class="trade-plan-table">
          <tbody>
            <tr v-for="step in tradeAdvice.steps ?? []" :key="step.label">
              <th>{{ step.label }}</th>
              <td><strong>{{ step.value }}</strong><span>{{ step.note }}</span></td>
            </tr>
          </tbody>
          </table>
        </div>
        <div class="trade-evidence-strip">
        <div>
          <span>执行策略</span>
          <strong>{{ tradeAdvice.strategy_status }}</strong>
        </div>
        <div>
          <span>新闻政策</span>
          <strong>{{ formatTradeEventSummary(tradeAdvice.event_evidence) }}</strong>
        </div>
        <div>
          <span>历史回测</span>
          <strong>{{ primaryTradeBacktest?.summary || '等待回测结果' }}</strong>
        </div>
        <div>
          <span>指数对比</span>
          <strong>{{ primaryTradeBacktestBenchmarkText }}</strong>
        </div>
        </div>
        <details class="trade-advice-evidence">
        <summary>策略依据和回测</summary>
        <ul>
          <li v-for="item in tradeAdvice.evidence ?? []" :key="item">{{ item }}</li>
        </ul>
        <div v-if="tradeAdvice.event_evidence?.length" class="trade-event-evidence-list">
          <div v-for="item in tradeAdvice.event_evidence ?? []" :key="item.url || item.title">
            <strong>{{ item.event_date }} · {{ item.title }}</strong>
            <span>{{ item.summary }}</span>
            <a :href="item.url" target="_blank" rel="noreferrer">{{ item.source }} · {{ item.impact }}</a>
          </div>
        </div>
        <ul v-if="tradeAdvice.strategy_rules?.length" class="trade-rule-list">
          <li v-for="rule in tradeAdvice.strategy_rules ?? []" :key="rule">{{ rule }}</li>
        </ul>
        <div v-if="tradeAdvice.backtests?.length" class="trade-backtest-list">
          <div v-for="item in tradeAdvice.backtests ?? []" :key="item.strategy_id" class="trade-backtest-card">
            <div>
              <span>{{ item.strategy_name }}</span>
              <strong>{{ item.summary }}</strong>
            </div>
            <dl>
              <dt>策略</dt>
              <dd :class="{ positive: (item.total_return_percent ?? 0) > 0, negative: (item.total_return_percent ?? 0) < 0 }">
                {{ formatSignedPercent(item.total_return_percent) }}
              </dd>
              <dt>{{ item.benchmark_name }}</dt>
              <dd :class="{ positive: (item.benchmark_return_percent ?? 0) > 0, negative: (item.benchmark_return_percent ?? 0) < 0 }">
                {{ formatSignedPercent(item.benchmark_return_percent) }}
              </dd>
              <dt>超额</dt>
              <dd :class="{ positive: (item.excess_return_percent ?? 0) > 0, negative: (item.excess_return_percent ?? 0) < 0 }">
                {{ formatSignedPercent(item.excess_return_percent) }}
              </dd>
              <dt>交易</dt>
              <dd>{{ item.trade_count }} 次</dd>
            </dl>
          </div>
        </div>
        <div class="trade-backtest-strip">
          <span>当前综合分规则来自 Qlib 日线摘要</span>
          <strong>{{ tradeAdvice.strategy_status }}</strong>
        </div>
        </details>
      </section>
    </details>

    <details class="trade-market-details" @toggle="handleMarketDetailsToggle">
      <summary>行情和评分维护</summary>
      <section class="trade-workbench-metrics">
        <div>
          <span>{{ isIntraday ? '最新价' : '最新收盘' }}</span>
          <strong>{{ formatNumber(metricLatestClose) }}</strong>
        </div>
        <div>
          <span>{{ isIntraday ? `${selectedPeriodLabel}涨跌` : '区间涨跌' }}</span>
          <strong :class="{ positive: (metricChange ?? 0) > 0, negative: (metricChange ?? 0) < 0 }">
            {{ formatNumber(metricChange) }} / {{ formatPercent(metricChangeRate) }}
          </strong>
        </div>
        <div>
          <span>{{ isIntraday ? `${selectedPeriodLabel}成交额` : '区间成交额' }}</span>
          <strong>{{ formatAmount(metricTotalAmount) }}</strong>
        </div>
        <div>
          <span>最大成交量</span>
          <strong>{{ metricMaxVolumeLabel }}</strong>
        </div>
        <div>
          <span>{{ isIntraday ? '分钟条数' : '数据条数' }}</span>
          <strong>{{ metricRowCount }}</strong>
        </div>
        <div>
          <span>加载时间</span>
          <strong>{{ loadedAt || '-' }}</strong>
        </div>
      </section>
    </details>

    </details>


  </div>
</template>

<style scoped>
.trade-workbench-page {
  background: #f6f7f9;
  color: #172033;
  min-height: calc(100vh - 52px);
  padding: 14px 18px 28px;
}

.trade-workbench-toolbar {
  background: #fff;
  border: 1px solid #e4e9f0;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
  padding: 12px 14px;
}

.trade-report-section {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 10px;
  margin-bottom: 10px;
}

.trade-report-main,
.trade-report-side {
  background: #fff;
  border: 1px solid #e4e9f0;
  border-radius: 8px;
  min-width: 0;
}

.trade-report-main {
  padding: 14px;
}

.trade-report-side {
  align-content: start;
  display: grid;
  gap: 12px;
  padding: 14px;
}

.trade-report-head {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 12px;
}

.trade-report-head > div:first-child,
.trade-report-side > div {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.trade-report-head span,
.trade-report-side span {
  color: #667085;
  font-size: 12px;
}

.trade-report-head strong {
  color: #172033;
  font-size: 20px;
  line-height: 1.25;
}

.trade-report-head small {
  color: #667085;
  font-size: 12px;
}

.trade-report-markdown {
  background: #fafbfc;
  border: 1px solid #edf1f7;
  border-radius: 6px;
  color: #202b3c;
  font-size: 14px;
  line-height: 1.75;
  margin: 0;
  min-height: 420px;
  overflow: auto;
  padding: 16px 18px;
  word-break: break-word;
}

.trade-report-markdown :deep(h1),
.trade-report-markdown :deep(h2),
.trade-report-markdown :deep(h3) {
  color: #172033;
  letter-spacing: 0;
  line-height: 1.35;
  margin: 18px 0 10px;
}

.trade-report-markdown :deep(h1:first-child),
.trade-report-markdown :deep(h2:first-child),
.trade-report-markdown :deep(h3:first-child) {
  margin-top: 0;
}

.trade-report-markdown :deep(h1) {
  font-size: 22px;
}

.trade-report-markdown :deep(h2) {
  border-top: 1px solid #edf1f7;
  font-size: 17px;
  padding-top: 14px;
}

.trade-report-markdown :deep(h3) {
  font-size: 15px;
}

.trade-report-markdown :deep(p),
.trade-report-markdown :deep(ul),
.trade-report-markdown :deep(ol) {
  margin: 8px 0;
}

.trade-report-markdown :deep(ul),
.trade-report-markdown :deep(ol) {
  padding-left: 22px;
}

.trade-report-markdown :deep(li) {
  margin: 5px 0;
}

.trade-report-markdown :deep(strong) {
  color: #172033;
  font-weight: 700;
}

.trade-report-markdown :deep(code) {
  background: #eef2f7;
  border-radius: 4px;
  color: #172033;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.92em;
  padding: 1px 4px;
}

.trade-report-side strong {
  color: #172033;
  font-size: 13px;
  line-height: 1.55;
}

.trade-report-side p {
  color: #667085;
  font-size: 13px;
  margin: 0;
}

.compact-holding-table {
  border-collapse: collapse;
  font-size: 12px;
  width: 100%;
}

.compact-holding-table th,
.compact-holding-table td {
  border-bottom: 1px solid #edf1f7;
  padding: 8px 6px;
  text-align: left;
  vertical-align: top;
}

.compact-holding-table th {
  color: #667085;
  font-weight: 700;
  white-space: nowrap;
}

.compact-holding-table td {
  color: #283548;
  line-height: 1.45;
}

.legacy-trade-tools {
  background: #fff;
  border: 1px solid #e4e9f0;
  border-radius: 8px;
  padding: 10px 12px 12px;
}

.legacy-trade-tools > summary {
  color: #526173;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
  line-height: 28px;
}

.legacy-trade-tools[open] > summary {
  margin-bottom: 10px;
}

.trade-workbench-title {
  min-width: 230px;
}

.trade-workbench-title h1 {
  margin: 0;
  font-size: 20px;
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

.active-target-meta strong {
  color: #172033;
  font-size: 13px;
  font-weight: 700;
}

.trade-workbench-controls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.trade-input-strip {
  background: #fff;
  border: 1px solid #e4e9f0;
  border-radius: 8px;
  display: grid;
  grid-template-columns: minmax(170px, max-content) minmax(0, 1fr);
  gap: 16px;
  align-items: end;
  margin-bottom: 8px;
  padding: 10px 14px;
}

.trade-input-summary {
  display: grid;
  gap: 4px;
}

.trade-input-summary strong {
  color: #172033;
  font-size: 14px;
}

.trade-input-summary span {
  color: #5f6b7a;
  font-size: 13px;
}

.trade-input-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: end;
  justify-content: flex-end;
}

.trade-input-grid label {
  display: grid;
  gap: 5px;
  color: #6a7482;
  font-size: 12px;
  width: 112px;
}

.trade-input-grid :deep(.el-input-number) {
  width: 100%;
}

.trade-input-grid :deep(.el-input__wrapper) {
  border-radius: 6px;
}

.watch-target-control,
.custom-target-control {
  display: flex;
  align-items: center;
  gap: 6px;
}

.watch-target-control span,
.custom-target-control span {
  color: #607086;
  font-size: 12px;
  white-space: nowrap;
}

.custom-market-select {
  width: 64px;
}

.custom-symbol-input {
  width: 104px;
}

.custom-name-input {
  width: 128px;
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

.trade-market-details {
  margin-bottom: 10px;
}

.trade-market-details summary {
  color: #526173;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 10px;
}

.trade-workbench-metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(120px, max-content));
  gap: 10px 22px;
  margin-bottom: 14px;
  max-width: 100%;
  overflow-x: auto;
}

.trade-workbench-metrics div {
  display: grid;
  gap: 3px;
  min-width: 120px;
}

.trade-workbench-metrics span {
  color: #607086;
  font-size: 12px;
}

.trade-workbench-metrics strong {
  font-size: 16px;
  font-weight: 700;
  white-space: nowrap;
}

.trade-advice-section {
  display: grid;
  gap: 12px 22px;
  grid-template-columns: minmax(420px, 1.1fr) minmax(360px, 0.9fr);
  padding-top: 12px;
}

.trade-advice-main {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.trade-advice-title {
  align-items: center;
  display: flex;
  gap: 10px;
  min-width: 0;
}

.trade-advice-title h2 {
  color: #172033;
  font-size: 20px;
  line-height: 1.25;
  margin: 0;
  min-width: 0;
}

.trade-advice-action {
  border-radius: 4px;
  flex: 0 0 auto;
  font-size: 13px;
  font-style: normal;
  font-weight: 700;
  padding: 4px 8px;
}

.trade-advice-action.is-hold {
  background: #ecfdf3;
  color: #087443;
}

.trade-advice-action.is-buy,
.trade-advice-action.is-sell-plan {
  background: #fff5e6;
  color: #b54708;
}

.trade-advice-action.is-risk {
  background: #fef3f2;
  color: #b42318;
}

.trade-advice-action.is-watch {
  background: #f4f6f8;
  color: #526173;
}

.trade-advice-order {
  color: #172033;
  font-size: 15px;
  font-weight: 700;
}

.trade-guardrail-note {
  color: #7a4b00;
  font-size: 13px;
  line-height: 1.45;
}

.trade-advice-lines,
.trade-position-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 22px;
}

.trade-advice-lines div,
.trade-position-facts div {
  display: grid;
  gap: 2px;
  min-width: 116px;
}

.trade-advice-lines span,
.trade-position-facts span {
  color: #607086;
  font-size: 12px;
}

.trade-advice-lines strong,
.trade-position-facts strong {
  color: #172033;
  font-size: 14px;
  line-height: 1.35;
}

.trade-advice-side {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.trade-evidence-strip {
  border-top: 1px solid #e6ebf2;
  display: grid;
  gap: 10px 22px;
  grid-column: 1 / -1;
  grid-template-columns: minmax(150px, max-content) minmax(220px, 1fr) minmax(260px, 1fr) minmax(220px, max-content);
  padding-top: 10px;
}

.trade-evidence-strip div {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.trade-evidence-strip span {
  color: #607086;
  font-size: 12px;
}

.trade-evidence-strip strong {
  color: #172033;
  font-size: 13px;
  line-height: 1.45;
}

.trade-plan-table {
  border-collapse: collapse;
  font-size: 13px;
  justify-self: start;
  width: max-content;
  max-width: 100%;
}

.trade-plan-table th,
.trade-plan-table td {
  border-bottom: 1px solid #e6ebf2;
  padding: 6px 18px 6px 0;
  text-align: left;
  vertical-align: top;
}

.trade-plan-table th {
  color: #607086;
  font-weight: 600;
  white-space: nowrap;
}

.trade-plan-table td {
  display: grid;
  gap: 2px;
}

.trade-plan-table td strong {
  color: #172033;
  white-space: nowrap;
}

.trade-plan-table td span {
  color: #607086;
  line-height: 1.4;
}

.trade-plan-table.is-compact {
  font-size: 12px;
  width: 100%;
}

.trade-plan-table.is-compact th,
.trade-plan-table.is-compact td {
  padding: 5px 10px 5px 0;
}

.trade-advice-evidence {
  border-top: 1px solid #e6ebf2;
  grid-column: 1 / -1;
  padding-top: 10px;
}

.trade-advice-evidence summary {
  color: #526173;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
}

.trade-advice-evidence ul {
  color: #526173;
  display: grid;
  font-size: 13px;
  gap: 5px;
  line-height: 1.5;
  margin: 8px 0;
  padding-left: 18px;
}

.trade-event-evidence-list {
  display: grid;
  gap: 8px;
  margin: 8px 0;
}

.trade-event-evidence-list div {
  border-top: 1px solid #eef2f6;
  display: grid;
  gap: 3px;
  padding-top: 7px;
}

.trade-event-evidence-list strong {
  color: #172033;
  font-size: 12px;
}

.trade-event-evidence-list span {
  color: #526173;
  font-size: 12px;
  line-height: 1.45;
}

.trade-event-evidence-list a {
  color: #426799;
  font-size: 12px;
  text-decoration: none;
}

.trade-backtest-list {
  display: grid;
  gap: 8px;
  margin: 10px 0;
}

.trade-backtest-card {
  border: 1px solid #e6ebf2;
  border-radius: 6px;
  display: grid;
  gap: 8px;
  padding: 8px 10px;
}

.trade-backtest-card > div {
  display: grid;
  gap: 2px;
}

.trade-backtest-card span {
  color: #607086;
  font-size: 12px;
  font-weight: 700;
}

.trade-backtest-card strong {
  color: #172033;
  font-size: 13px;
  line-height: 1.45;
}

.trade-backtest-card dl {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin: 0;
}

.trade-backtest-card dt {
  color: #607086;
  font-size: 12px;
}

.trade-backtest-card dd {
  color: #172033;
  font-size: 12px;
  font-weight: 700;
  margin: 0;
}

.trade-backtest-strip {
  align-items: baseline;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  font-size: 13px;
  margin-top: 6px;
}

.trade-backtest-strip span {
  color: #607086;
}

.trade-backtest-strip strong {
  color: #172033;
}

.trade-workbench-section {
  background: #fff;
  border: 1px solid #e4e9f0;
  border-radius: 8px;
  display: grid;
  gap: 10px;
  margin-bottom: 8px;
  padding: 12px 14px 14px;
}

.trade-workbench-head {
  align-items: center;
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.trade-workbench-head > div {
  display: grid;
  gap: 2px;
}

.trade-workbench-head strong {
  color: #172033;
  font-size: 16px;
}

.trade-workbench-head span {
  color: #607086;
  font-size: 12px;
}

.trade-workbench-head small {
  color: #7a8797;
  font-size: 12px;
}

.trade-workbench-inspector {
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1fr) minmax(380px, 420px);
  align-items: start;
}

.trade-action-table-wrap {
  border: 1px solid #e4e9f0;
  border-radius: 8px;
  max-width: 100%;
  overflow-x: auto;
}

.trade-action-table {
  border-collapse: collapse;
  font-size: 13px;
  min-width: 720px;
  table-layout: fixed;
  width: 100%;
}

.trade-action-table th:nth-child(1) {
  width: 19%;
}

.trade-action-table th:nth-child(2) {
  width: 28%;
}

.trade-action-table th:nth-child(3) {
  width: 16%;
}

.trade-action-table th:nth-child(4) {
  width: 20%;
}

.trade-action-table th:nth-child(5) {
  width: 17%;
}

.trade-action-table th,
.trade-action-table td {
  border-bottom: 1px solid #e8edf4;
  padding: 9px 11px;
  text-align: left;
  vertical-align: middle;
}

.trade-action-table th {
  background: #f8fafc;
  color: #607086;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.trade-action-table tbody tr {
  cursor: pointer;
}

.trade-action-table tbody tr:hover,
.trade-action-table tbody tr.is-active {
  background: #f4f8ff;
}

.trade-action-table tbody tr.is-active td:first-child {
  box-shadow: inset 3px 0 0 #2f7de1;
}

.trade-action-table td {
  color: #172033;
  line-height: 1.4;
}

.trade-action-table td:first-child {
  display: grid;
  gap: 2px;
  min-width: 142px;
}

.trade-action-table td strong {
  color: #172033;
  display: -webkit-box;
  font-size: 13px;
  line-height: 1.35;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.trade-action-table td em + strong,
.trade-action-table td strong + small {
  margin-top: 4px;
}

.trade-action-table button {
  background: transparent;
  border: 0;
  color: #172033;
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  padding: 0;
  text-align: left;
}

.trade-action-table small {
  color: #7a8797;
  display: -webkit-box;
  font-size: 12px;
  line-height: 1.35;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.trade-action-position {
  color: #172033;
  display: block;
  font-weight: 700;
}

.trade-action-detail {
  background: #fcfdff;
  border: 1px solid #e4e9f0;
  border-radius: 8px;
  display: grid;
  gap: 9px;
  min-width: 0;
  padding: 12px;
}

.trade-action-detail-title {
  align-items: center;
  display: flex;
  gap: 8px;
  min-width: 0;
}

.trade-action-detail-title strong {
  color: #172033;
  font-size: 16px;
  min-width: 0;
}

.trade-action-detail p {
  color: #526173;
  font-size: 13px;
  line-height: 1.45;
  margin: 0;
}

.trade-action-detail > dl {
  display: grid;
  gap: 4px 8px;
  grid-template-columns: repeat(2, auto minmax(0, 1fr));
  margin: 0;
}

.trade-action-detail > dl dt {
  color: #607086;
  font-size: 12px;
}

.trade-action-detail > dl dd {
  color: #172033;
  font-size: 12px;
  font-weight: 700;
  margin: 0;
  min-width: 0;
}

.trade-action-detail-evidence {
  border-top: 1px solid #e6ebf2;
  padding-top: 8px;
}

.trade-action-detail-evidence summary {
  color: #526173;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
}

.trade-action-detail-evidence ul {
  color: #526173;
  display: grid;
  font-size: 13px;
  gap: 5px;
  line-height: 1.45;
  margin: 8px 0;
  padding-left: 18px;
}

.trade-policy-evidence {
  border-top: 1px solid #e6ebf2;
  margin: 0;
  padding-top: 8px;
}

.trade-policy-evidence summary {
  color: #526173;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
}

.trade-policy-evidence-body {
  display: grid;
  gap: 10px;
  grid-template-columns: minmax(0, 1fr) minmax(260px, max-content);
  margin-top: 8px;
}

.trade-policy-evidence-body section {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.trade-policy-evidence-body strong {
  color: #172033;
  font-size: 13px;
}

.trade-policy-evidence-body ul {
  color: #526173;
  display: grid;
  font-size: 13px;
  gap: 4px;
  line-height: 1.45;
  margin: 0;
  padding-left: 18px;
}

.trade-policy-evidence-body p {
  color: #526173;
  font-size: 13px;
  line-height: 1.45;
  margin: 0;
}

.trade-candidates-actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.trade-candidate-order {
  color: #172033;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.45;
}

.trade-account-note {
  color: #7a8797;
  font-size: 12px;
  line-height: 1.45;
}

.trade-action-evidence-strip {
  display: grid;
  gap: 5px 10px;
  grid-template-columns: auto minmax(0, 1fr);
  margin: 0;
  padding: 8px 0;
  border-bottom: 1px solid #edf1f6;
  border-top: 1px solid #edf1f6;
}

.trade-action-evidence-strip dt,
.trade-action-backtest-list small {
  color: #7a8797;
  font-size: 12px;
  white-space: nowrap;
}

.trade-action-evidence-strip dd {
  color: #172033;
  font-size: 12px;
  line-height: 1.35;
  margin: 0;
  min-width: 0;
}

.trade-advice-panel,
.trade-market-details {
  background: #fff;
  border: 1px solid #e4e9f0;
  border-radius: 8px;
  margin-bottom: 8px;
  padding: 9px 14px;
}

.trade-advice-panel summary {
  color: #526173;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
}

.trade-action-backtest-list {
  display: grid;
  gap: 8px;
  margin-top: 8px;
}

.trade-action-backtest-list div {
  border-top: 1px solid #eef2f6;
  display: grid;
  gap: 3px;
  padding-top: 7px;
}

.trade-action-backtest-list strong {
  color: #172033;
  font-size: 12px;
}

.trade-action-backtest-list span {
  color: #526173;
  font-size: 12px;
  line-height: 1.45;
}

.trade-candidate-empty {
  color: #607086;
  font-size: 13px;
}

.trade-workbench-chart-section {
  background: #fff;
  border: 1px solid #dce5f0;
  border-radius: 6px;
  margin-bottom: 14px;
  padding: 12px 10px 8px;
}

.trade-workbench-chart-section.is-primary-chart {
  border-color: #e4e9f0;
  border-radius: 8px;
  margin-bottom: 10px;
}

.chart-section-head {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 8px;
}

.chart-section-head > div:first-child {
  display: grid;
  gap: 2px;
}

.chart-section-head span {
  color: #607086;
  font-size: 12px;
}

.chart-section-head strong {
  color: #172033;
  font-size: 15px;
}

.chart-controls {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.chart-notice {
  color: #7a4b00;
  font-size: 12px;
  line-height: 1.5;
  margin: 0 0 8px;
}

.trade-workbench-chart {
  height: 390px;
  width: 100%;
}

.positive {
  color: #b42318;
}

.negative {
  color: #067647;
}

@media (max-width: 900px) {
  .trade-workbench-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .trade-report-section {
    grid-template-columns: 1fr;
  }

  .trade-report-head {
    align-items: stretch;
    flex-direction: column;
  }

  .trade-workbench-controls {
    justify-content: flex-start;
  }

  .trade-input-strip {
    grid-template-columns: 1fr;
  }

  .trade-input-grid {
    grid-template-columns: repeat(2, minmax(120px, 1fr));
  }

  .trade-input-grid .el-button {
    grid-column: 1 / -1;
  }

  .trade-workbench-metrics {
    grid-template-columns: repeat(2, minmax(120px, 1fr));
  }

  .trade-advice-section {
    grid-template-columns: 1fr;
  }

  .trade-evidence-strip {
    grid-template-columns: 1fr;
  }

  .trade-workbench-inspector {
    grid-template-columns: 1fr;
  }

  .trade-action-detail {
    border-left: 0;
    border-top: 1px solid #e6ebf2;
    padding-left: 0;
    padding-top: 10px;
  }

  .trade-policy-evidence-body {
    grid-template-columns: 1fr;
  }

  .chart-section-head {
    align-items: stretch;
    display: grid;
  }

  .chart-controls {
    justify-content: flex-start;
  }
}

@media (max-width: 1280px) {
  .trade-workbench-inspector {
    grid-template-columns: 1fr;
  }

  .trade-action-detail {
    background: #fff;
  }
}
</style>

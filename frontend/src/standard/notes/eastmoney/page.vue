<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, Link, QuestionFilled, Upload } from '@element-plus/icons-vue'
import { CustomChart, type CustomSeriesOption } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  type DataZoomComponentOption,
  type GridComponentOption,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import type { ComposeOption, ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

import {
  fetchLatestEastmoneyAssetSnapshot,
  fetchEastmoneyTradeRecords,
  fetchLatestEastmoneyMarketQuotes,
  fetchLatestEastmoneyPositions,
  importEastmoneyTradeDetailFromOcr,
  openEastmoneyTradeAccountPage,
  refreshEastmoneySheetWorkbook,
  refreshEastmoneyMarketQuotes,
  syncEastmoneyTradeData,
  type EastmoneyAssetSnapshot,
  type EastmoneyMarketQuote,
  type EastmoneyPositionRecord,
  type EastmoneySheetWorkbook,
  type EastmoneySheetWorkbookSheet,
  type EastmoneyTradeRecord,
} from '@/api/eastmoney'
import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

echarts.use([CustomChart, DataZoomComponent, GridComponent, CanvasRenderer])

type BatchChartOption = ComposeOption<
  CustomSeriesOption | GridComponentOption | DataZoomComponentOption
>

type NoteSheetWorkspaceExpose = InstanceType<typeof NoteSheetWorkspace> & {
  openSheetSettings?: () => void
}

type SheetTabMenuCommand =
  | 'open_workbook'
  | 'open_sheet'
  | 'copy_workbook_link'
  | 'copy_sheet_link'
  | 'configure'
  | 'refresh'

type SheetTabMenuItem = {
  command: SheetTabMenuCommand
  label: string
  divided?: boolean
}

type BatchSecurityOption = {
  key: string
  market: string
  code: string
  name: string
  hasPosition: boolean
  pnlValue: number
}

type BatchLot = {
  id: string
  market: string
  code: string
  name: string
  buyDate: string
  buyTime: string
  price: number
  originalQty: number
  remainingQty: number
  synthetic: boolean
}

type BatchMatch = {
  id: string
  lotId: string
  buyDate: string
  buyTime: string
  sellDate: string
  sellTime: string
  qty: number
  buyPrice: number
  sellPrice: number
  netSellPrice: number
  fee: number
  pnl: number
}

type BatchLotView = BatchLot & {
  x: number
  width: number
  xQty: number
  widthQty: number
  height: number
  remainingWidth: number
  soldWidth: number
}

type BatchMatchView = BatchMatch & {
  x: number
  width: number
  xQty: number
  widthQty: number
  deltaBottom: number
  deltaHeight: number
  feeBottom: number
  feeHeight: number
  positive: boolean
}

type BatchProfileModel = {
  key: string
  market: string
  code: string
  name: string
  lots: BatchLotView[]
  matches: BatchMatchView[]
  totalOriginalQty: number
  totalRemainingQty: number
  totalSoldQty: number
  realizedPnl: number
  chartMaxPrice: number
  currentPrice: number | null
  currentPricePercent: number | null
  currentPriceSource: 'akshare' | 'market' | 'position' | ''
  currentPriceUpdatedAt: string
}

type BatchChartKind = 'held' | 'sold' | 'profit' | 'loss' | 'fee' | 'current'

type BatchChartPayload = {
  kind: BatchChartKind
  lot?: BatchLotView
  match?: BatchMatchView
  segmentQty: number
  yStart: number
  yEnd: number
  currentPrice?: number | null
  currentPriceSource?: BatchProfileModel['currentPriceSource']
  currentPriceUpdatedAt?: string
}

type BatchChartDataItem = {
  id: string
  name: string
  value: [number, number, number, number, number, number]
  itemStyle: {
    color: string
    borderColor?: string
    borderWidth?: number
  }
  payload: BatchChartPayload
}

type BatchTooltipTone = 'default' | 'profit' | 'loss'

type BatchTooltipDetail = {
  title: string
  lines: Array<{
    label: string
    value: string
    tone?: BatchTooltipTone
  }>
}

const EASTMONEY_SHEET_TABS = [
  { key: 'local-history', label: '成交明细', emptyText: '暂无成交数据' },
  { key: 'operation-history', label: '操作明细', emptyText: '暂无操作流水' },
  { key: 'positions', label: '持仓', emptyText: '暂无持仓数据' },
  { key: 'sync-runs', label: '同步记录', emptyText: '暂无同步记录' },
] as const
const DEFAULT_BATCH_SECURITY_CODE = '159278'
const DEFAULT_BATCH_SECURITY_NAME = '机器人PH'
const SHEET_TAB_CONTEXT_MENU_WIDTH = 152
const SHEET_TAB_CONTEXT_MENU_HEIGHT = 210

const syncing = ref(false)
const pageError = ref('')
const activeTab = ref('local-history')
const latestAssetSnapshot = ref<EastmoneyAssetSnapshot | null>(null)
const pasteImportEnabled = ref(false)
const ocrImporting = ref(false)
const tradePageOpening = ref(false)
const sheetWorkbookLoading = ref(false)
const sheetWorkbook = ref<EastmoneySheetWorkbook | null>(null)
const sheetReloadToken = ref(0)
const sheetWorkspaceRefs = ref<Record<string, NoteSheetWorkspaceExpose | null>>({})
const batchChartRef = ref<HTMLDivElement | null>(null)
const batchTradeRecords = ref<EastmoneyTradeRecord[]>([])
const batchPositions = ref<EastmoneyPositionRecord[]>([])
const batchMarketQuotes = ref<EastmoneyMarketQuote[]>([])
const batchProfileLoading = ref(false)
const batchQuoteRefreshing = ref(false)
const batchQuoteError = ref('')
const selectedBatchSecurityKey = ref('')
const activeBatchChartItem = ref<BatchChartDataItem | null>(null)
const activeBatchChartKey = ref('')
let batchChart: ECharts | null = null
let batchChartResizeObserver: ResizeObserver | undefined
let batchQuoteRefreshTimer: number | undefined
const sheetTabMenu = ref({
  visible: false,
  key: '',
  x: 0,
  y: 0,
})

const accountLabel = computed(() => latestAssetSnapshot.value?.account_label || '')
const summaryItems = computed(() => {
  const summary = latestAssetSnapshot.value?.raw_json ?? {}
  return [
    ['证券市值', summary['证券市值']],
    ['资金余额', summary['资金余额']],
    ['持仓盈亏', summary['持仓盈亏']],
  ].filter(([, value]) => value)
    .map(([label, value]) => ({
      label,
      value: formatSmartMoney(value),
      negative: parseMoneyValue(value) < 0,
    }))
})
const lastUpdatedAtText = computed(() => {
  return formatTime(latestAssetSnapshot.value?.captured_at)
})
const workbookId = computed(() => sheetWorkbook.value?.workbook.id ?? null)
const sheetTabs = computed(() => EASTMONEY_SHEET_TABS.map((tab) => ({
  ...tab,
  sheet: getSheetItem(tab.key),
})))
const batchSecurityOptions = computed(() => buildBatchSecurityOptions(batchTradeRecords.value, batchPositions.value))
const selectedBatchProfile = computed(() => buildBatchProfileModel(
  selectedBatchSecurityKey.value,
  batchTradeRecords.value,
  batchPositions.value,
  batchMarketQuotes.value,
))
const activeBatchTooltipDetail = computed(() => (
  activeBatchChartItem.value ? buildBatchTooltipDetail(activeBatchChartItem.value) : null
))
const batchQuoteStatusText = computed(() => {
  const profile = selectedBatchProfile.value
  if (!profile) return ''
  if (isMarketQuoteSource(profile.currentPriceSource) && profile.currentPriceUpdatedAt) {
    const label = getBatchQuoteSourceLabel(profile.currentPriceSource)
    return batchQuoteRefreshing.value
      ? `${label} ${profile.currentPriceUpdatedAt}，刷新中`
      : `${label} ${profile.currentPriceUpdatedAt}`
  }
  if (isMarketQuoteSource(profile.currentPriceSource)) {
    const label = getBatchQuoteSourceLabel(profile.currentPriceSource)
    return batchQuoteRefreshing.value ? `${label}刷新中` : label
  }
  if (profile.currentPriceSource === 'position') {
    const timeText = profile.currentPriceUpdatedAt ? ` ${profile.currentPriceUpdatedAt}` : ''
    if (batchQuoteError.value) return `行情未刷新，当前为快照价${timeText}`
    return batchQuoteRefreshing.value ? `快照价${timeText}，尝试刷新` : `快照价${timeText}`
  }
  if (batchQuoteError.value) return '行情未连接'
  if (batchQuoteRefreshing.value) return '行情刷新中'
  return ''
})
const sheetTabMenuItems: SheetTabMenuItem[] = [
  { command: 'open_workbook', label: '打开完整工作簿' },
  { command: 'open_sheet', label: '单独打开工作表' },
  { command: 'copy_workbook_link', label: '复制工作簿链接' },
  { command: 'copy_sheet_link', label: '复制工作表链接' },
  { command: 'configure', label: '设置表格', divided: true },
  { command: 'refresh', label: '刷新表格文件' },
]

function formatDate(value: Date) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function defaultSyncDateParams() {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - 100)
  return {
    start_date: formatDate(start),
    end_date: formatDate(end),
  }
}

function formatTime(value: number | null | undefined) {
  return value ? new Date(value * 1000).toLocaleString() : ''
}

function parseMoneyValue(value: unknown) {
  const text = String(value ?? '').trim()
  if (!text || text === '-') return Number.NaN
  const multiplier = text.includes('万') ? 10000 : 1
  const normalized = text.replace(/,/g, '').replace(/，/g, '').replace(/[^\d.+-]/g, '')
  if (!normalized || normalized === '-' || normalized === '+') return Number.NaN
  return Number(normalized) * multiplier
}

function formatSmartMoney(value: unknown) {
  const text = String(value ?? '').trim()
  const numberValue = parseMoneyValue(text)
  if (!Number.isFinite(numberValue)) return text
  return formatSmartMoneyNumber(numberValue)
}

function formatSmartMoneyNumber(value: number) {
  if (!Number.isFinite(value)) return ''
  const normalizedValue = Object.is(value, -0) ? 0 : value
  const sign = normalizedValue < 0 ? '-' : ''
  const absValue = Math.abs(normalizedValue)
  if (absValue < 100) return `${sign}${absValue.toFixed(2)}`
  if (absValue < 10000) return `${sign}${absValue.toFixed(0)}`
  return `${sign}${(absValue / 10000).toFixed(2)}万`
}

function parseNumericValue(value: unknown) {
  const text = String(value ?? '').trim()
  if (!text || text === '-' || text === '--') return Number.NaN
  const normalized = text.replace(/,/g, '').replace(/，/g, '').replace(/[^\d.+-]/g, '')
  return normalized ? Number(normalized) : Number.NaN
}

function formatQuantity(value: number) {
  if (!Number.isFinite(value)) return ''
  return formatCompactQuantity(value)
}

function formatCompactQuantity(value: number, forceWan = false) {
  if (!Number.isFinite(value)) return ''
  const roundedValue = Math.round(value)
  const sign = roundedValue < 0 ? '-' : ''
  const absValue = Math.abs(roundedValue)
  if (absValue === 0) return '0'
  if (forceWan || absValue >= 10000) {
    const scaledValue = absValue / 10000
    const fractionDigits = scaledValue < 10 ? 2 : scaledValue < 100 ? 1 : 0
    const text = scaledValue
      .toFixed(fractionDigits)
      .replace(/0+$/, '')
      .replace(/\.$/, '')
    return `${sign}${text}万`
  }
  return `${sign}${absValue}`
}

function formatPriceValue(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '--'
  if (Math.abs(value) >= 100) return value.toFixed(2)
  return value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

function formatPerShareCostValue(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '--'
  const absValue = Math.abs(value)
  if (absValue > 0 && absValue < 0.001) {
    return value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')
  }
  if (absValue > 0 && absValue < 0.01) {
    return value.toFixed(5).replace(/0+$/, '').replace(/\.$/, '')
  }
  return formatPriceValue(value)
}

function formatTradeMinute(value: string) {
  const text = String(value || '').trim()
  if (!text) return ''
  const match = text.match(/^(\d{1,2}):(\d{2})/)
  return match ? `${match[1].padStart(2, '0')}:${match[2]}` : text
}

function formatTradeDateMinute(date: string, time: string) {
  const normalizedDate = String(date || '').trim().replaceAll('-', '/')
  const normalizedTime = formatTradeMinute(time)
  return [normalizedDate, normalizedTime].filter(Boolean).join(' ')
}

function formatSignedPriceDelta(value: number) {
  if (!Number.isFinite(value)) return ''
  const sign = value >= 0 ? '+' : ''
  return `${sign}${formatPriceValue(value)}`
}

function formatPercentValue(value: number) {
  if (!Number.isFinite(value)) return ''
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatSignedMoneyValue(value: number) {
  if (!Number.isFinite(value)) return ''
  return `${value >= 0 ? '+' : ''}${formatSmartMoneyNumber(value)}`
}

function parseTradeDateTime(date: string, time: string) {
  const text = `${String(date || '').trim()}T${formatTradeMinute(time) || '00:00'}:00`
  const value = new Date(text)
  return Number.isNaN(value.getTime()) ? null : value
}

function formatHoldingDuration(buyDate: string, buyTime: string, sellDate: string, sellTime: string) {
  const start = parseTradeDateTime(buyDate, buyTime)
  const end = parseTradeDateTime(sellDate, sellTime)
  if (!start || !end) return ''
  const days = Math.max(0, Math.floor((end.getTime() - start.getTime()) / 86400000))
  return days === 0 ? '当日' : `${days}天`
}

function normalizeSecurityCode(value: string) {
  return String(value || '').trim().toUpperCase().replace(/[^0-9A-Z]/g, '')
}

function normalizeSecurityMarket(market: string, code: string) {
  const normalizedMarket = String(market || '').trim().toUpperCase()
  const normalizedCode = normalizeSecurityCode(code)
  if (normalizedMarket === 'HK' || normalizedMarket === 'HKG') return 'HK'
  if (normalizedMarket === 'SH' || normalizedMarket === 'SSE') return 'SH'
  if (normalizedMarket === 'SZ' || normalizedMarket === 'SZSE') return 'SZ'
  if (normalizedCode.length === 5) return 'HK'
  if (/^[569]/.test(normalizedCode)) return 'SH'
  return 'SZ'
}

function normalizeSecuritySymbol(market: string, code: string) {
  const normalizedCode = normalizeSecurityCode(code)
  if (!normalizedCode) return ''
  const normalizedMarket = normalizeSecurityMarket(market, normalizedCode)
  return normalizedMarket === 'HK' ? normalizedCode.padStart(5, '0') : normalizedCode.padStart(6, '0')
}

function createSecurityKey(market: string, code: string) {
  const normalizedMarket = normalizeSecurityMarket(market, code)
  const symbol = normalizeSecuritySymbol(normalizedMarket, code)
  return symbol ? `${normalizedMarket}:${symbol}` : ''
}

function getTradeQuantity(record: EastmoneyTradeRecord) {
  const numeric = Number(record.quantity_value)
  if (Number.isFinite(numeric) && numeric !== 0) return Math.abs(numeric)
  return Math.abs(parseNumericValue(record.quantity))
}

function getTradePrice(record: EastmoneyTradeRecord) {
  const numeric = Number(record.price_value)
  if (Number.isFinite(numeric) && numeric > 0) return numeric
  return parseNumericValue(record.price)
}

function getTradeFee(record: EastmoneyTradeRecord) {
  const directFee = Number(record.fee_value)
  if (Number.isFinite(directFee) && directFee > 0) return Math.abs(directFee)
  const componentValues = [
    record.commission_value,
    record.stamp_tax_value,
    record.transfer_fee_value,
    record.other_fee_value,
  ]
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value > 0)
  if (componentValues.length) {
    return componentValues.reduce((sum, value) => sum + Math.abs(value), 0)
  }
  return Math.abs(parseNumericValue(record.fee))
}

function isBuyTrade(record: EastmoneyTradeRecord) {
  return record.direction.includes('买')
}

function isSellTrade(record: EastmoneyTradeRecord) {
  return record.direction.includes('卖')
}

function getTradeDateTime(record: EastmoneyTradeRecord) {
  return `${record.trade_date || record.occurrence_date || ''} ${record.trade_time || record.occurrence_time || ''}`.trim()
}

function dedupeBatchPositions(positions: EastmoneyPositionRecord[]) {
  const map = new Map<string, EastmoneyPositionRecord>()
  for (const position of positions) {
    const key = createSecurityKey(position.market, position.security_code)
    if (!key) continue
    const current = map.get(key)
    if (!current) {
      map.set(key, position)
      continue
    }
    const currentCost = parseNumericValue(current.cost_price)
    const nextCost = parseNumericValue(position.cost_price)
    const currentPnl = parseNumericValue(current.pnl)
    const nextPnl = parseNumericValue(position.pnl)
    if (
      (!Number.isFinite(currentCost) && Number.isFinite(nextCost))
      || (!Number.isFinite(currentPnl) && Number.isFinite(nextPnl))
      || (current.source !== 'normal_position' && position.source === 'normal_position')
    ) {
      map.set(key, position)
    }
  }
  return [...map.values()]
}

function buildBatchSecurityOptions(
  records: EastmoneyTradeRecord[],
  positions: EastmoneyPositionRecord[],
): BatchSecurityOption[] {
  const map = new Map<string, BatchSecurityOption>()
  for (const position of dedupeBatchPositions(positions)) {
    const key = createSecurityKey(position.market, position.security_code)
    if (!key) continue
    map.set(key, {
      key,
      market: normalizeSecurityMarket(position.market, position.security_code),
      code: normalizeSecuritySymbol(position.market, position.security_code),
      name: position.security_name || position.security_code,
      hasPosition: true,
      pnlValue: parseNumericValue(position.pnl),
    })
  }
  for (const record of records) {
    const key = createSecurityKey(record.market, record.security_code)
    if (!key) continue
    if (!map.has(key)) {
      map.set(key, {
        key,
        market: normalizeSecurityMarket(record.market, record.security_code),
        code: normalizeSecuritySymbol(record.market, record.security_code),
        name: record.security_name || record.security_code,
        hasPosition: false,
        pnlValue: 0,
      })
    }
  }
  return [...map.values()].sort((left, right) => {
    if (left.hasPosition !== right.hasPosition) return left.hasPosition ? -1 : 1
    const leftPnl = Number.isFinite(left.pnlValue) ? left.pnlValue : 0
    const rightPnl = Number.isFinite(right.pnlValue) ? right.pnlValue : 0
    if (left.hasPosition && leftPnl !== rightPnl) return leftPnl - rightPnl
    return `${left.market}${left.code}`.localeCompare(`${right.market}${right.code}`)
  })
}

function getDefaultBatchSecurityKey(options: BatchSecurityOption[]) {
  const preferred = options.find((option) => {
    return option.code === DEFAULT_BATCH_SECURITY_CODE
      || option.name.includes(DEFAULT_BATCH_SECURITY_NAME)
  })
  return preferred?.key || options[0]?.key || ''
}

function getBatchPositionByKey(key: string, positions: EastmoneyPositionRecord[]) {
  return dedupeBatchPositions(positions).find((position) => createSecurityKey(position.market, position.security_code) === key) ?? null
}

function getBatchQuoteByKey(key: string, quotes: EastmoneyMarketQuote[]) {
  return quotes.find((quote) => createSecurityKey(quote.market, quote.symbol) === key) ?? null
}

function normalizeQuoteUpdateTime(quote: EastmoneyMarketQuote | null) {
  const updateTime = String(quote?.update_time || '').trim()
  if (updateTime) return updateTime.replaceAll('-', '/')
  return quote?.fetched_at ? formatTime(quote.fetched_at) : ''
}

function getBatchCurrentPriceLabel(source: BatchProfileModel['currentPriceSource'] | undefined) {
  return isMarketQuoteSource(source) ? '现价' : '快照价'
}

function isMarketQuoteSource(source: BatchProfileModel['currentPriceSource'] | undefined) {
  return source === 'akshare' || source === 'market'
}

function getBatchQuoteSourceLabel(source: BatchProfileModel['currentPriceSource'] | undefined) {
  if (source === 'akshare') return 'AKShare现价'
  if (source === 'market') return '行情现价'
  return '现价'
}

function buildBatchProfileModel(
  key: string,
  records: EastmoneyTradeRecord[],
  positions: EastmoneyPositionRecord[],
  quotes: EastmoneyMarketQuote[],
): BatchProfileModel | null {
  if (!key) return null
  const option = batchSecurityOptions.value.find((item) => item.key === key)
  const matchedRecords = records
    .filter((record) => createSecurityKey(record.market, record.security_code) === key)
    .slice()
    .sort((left, right) => getTradeDateTime(left).localeCompare(getTradeDateTime(right)) || left.id.localeCompare(right.id))

  const lots: BatchLot[] = []
  const matches: BatchMatch[] = []
  for (const record of matchedRecords) {
    const qty = getTradeQuantity(record)
    const price = getTradePrice(record)
    if (!Number.isFinite(qty) || qty <= 0 || !Number.isFinite(price) || price <= 0) continue
    if (isBuyTrade(record)) {
      lots.push({
        id: `trade:${record.id}`,
        market: normalizeSecurityMarket(record.market, record.security_code),
        code: normalizeSecuritySymbol(record.market, record.security_code),
        name: record.security_name || option?.name || record.security_code,
        buyDate: record.trade_date || record.occurrence_date || '',
        buyTime: record.trade_time || record.occurrence_time || '',
        price,
        originalQty: qty,
        remainingQty: qty,
        synthetic: false,
      })
      continue
    }
    if (!isSellTrade(record)) continue

    let remainingSellQty = qty
    const sellFee = getTradeFee(record)
    const totalSellFee = Number.isFinite(sellFee) && sellFee > 0 ? sellFee : 0
    const sellDate = record.trade_date || record.occurrence_date || ''
    const sellTime = record.trade_time || record.occurrence_time || ''
    while (remainingSellQty > 0.000001) {
      const lot = lots
        .filter((candidate) => candidate.remainingQty > 0.000001)
        .sort((left, right) => {
          const leftTime = `${left.buyDate || ''} ${left.buyTime || ''}`
          const rightTime = `${right.buyDate || ''} ${right.buyTime || ''}`
          return rightTime.localeCompare(leftTime) || right.id.localeCompare(left.id)
        })[0]
      if (!lot) break
      const matchedQty = Math.min(remainingSellQty, lot.remainingQty)
      const matchedFee = totalSellFee * (matchedQty / qty)
      const feePerShare = matchedQty > 0 ? matchedFee / matchedQty : 0
      lot.remainingQty -= matchedQty
      remainingSellQty -= matchedQty
      matches.push({
        id: `${record.id}:${lot.id}:${matches.length}`,
        lotId: lot.id,
        buyDate: lot.buyDate,
        buyTime: lot.buyTime,
        sellDate,
        sellTime,
        qty: matchedQty,
        buyPrice: lot.price,
        sellPrice: price,
        netSellPrice: price - feePerShare,
        fee: matchedFee,
        pnl: (price - lot.price) * matchedQty - matchedFee,
      })
    }
  }

  const position = getBatchPositionByKey(key, positions)
  const quote = getBatchQuoteByKey(key, quotes)
  const positionQty = position ? Math.abs(parseNumericValue(position.quantity)) : Number.NaN
  const positionCost = position ? parseNumericValue(position.cost_price) : Number.NaN
  const quotePrice = Number(quote?.price)
  const positionCurrentPrice = position ? parseNumericValue(position.current_price) : Number.NaN
  const currentPrice = Number.isFinite(quotePrice) && quotePrice > 0 ? quotePrice : positionCurrentPrice
  const currentPriceSource: BatchProfileModel['currentPriceSource'] = Number.isFinite(quotePrice) && quotePrice > 0
    ? normalizeQuotePriceSource(quote?.provider)
    : Number.isFinite(positionCurrentPrice) && positionCurrentPrice > 0 ? 'position' : ''
  const currentRemainingQty = lots.reduce((sum, lot) => sum + Math.max(0, lot.remainingQty), 0)
  if (
    position
    && Number.isFinite(positionQty)
    && positionQty > currentRemainingQty + 0.000001
    && Number.isFinite(positionCost)
    && positionCost > 0
  ) {
    lots.push({
      id: `position:${key}`,
      market: normalizeSecurityMarket(position.market, position.security_code),
      code: normalizeSecuritySymbol(position.market, position.security_code),
      name: position.security_name || option?.name || position.security_code,
      buyDate: position.captured_at ? formatDate(new Date(position.captured_at * 1000)) : '持仓快照',
      buyTime: '',
      price: positionCost,
      originalQty: positionQty - currentRemainingQty,
      remainingQty: positionQty - currentRemainingQty,
      synthetic: true,
    })
  }

  if (!lots.length) return null
  return layoutBatchProfile({
    key,
    market: option?.market || lots[0].market,
    code: option?.code || lots[0].code,
    name: option?.name || lots[0].name,
    lots,
    matches,
    currentPrice: Number.isFinite(currentPrice) && currentPrice > 0 ? currentPrice : null,
    currentPriceSource,
    currentPriceUpdatedAt: isMarketQuoteSource(currentPriceSource)
      ? normalizeQuoteUpdateTime(quote)
      : position?.captured_at ? formatTime(position.captured_at) : '',
  })
}

function normalizeQuotePriceSource(provider: string | undefined): BatchProfileModel['currentPriceSource'] {
  if (provider === 'akshare') return provider
  return 'market'
}

function layoutBatchProfile(input: {
  key: string
  market: string
  code: string
  name: string
  lots: BatchLot[]
  matches: BatchMatch[]
  currentPrice: number | null
  currentPriceSource: 'akshare' | 'position' | ''
  currentPriceUpdatedAt: string
}): BatchProfileModel {
  const sortedLots = input.lots
    .slice()
    .sort((left, right) => {
      const leftTime = `${left.buyDate || ''} ${left.buyTime || ''}`
      const rightTime = `${right.buyDate || ''} ${right.buyTime || ''}`
      return leftTime.localeCompare(rightTime) || left.id.localeCompare(right.id)
    })
  const totalOriginalQty = sortedLots.reduce((sum, lot) => sum + lot.originalQty, 0)
  const totalRemainingQty = sortedLots.reduce((sum, lot) => sum + Math.max(0, lot.remainingQty), 0)
  const maxPrice = Math.max(
    input.currentPrice ?? 0,
    ...sortedLots.map((lot) => lot.price),
    ...input.matches.map((match) => match.sellPrice),
    ...input.matches.map((match) => match.netSellPrice),
    1,
  )
  const chartMaxPrice = maxPrice * 1.12
  let cursor = 0
  let cursorQty = 0
  const lotViews: BatchLotView[] = sortedLots.map((lot) => {
    const width = totalOriginalQty > 0 ? (lot.originalQty / totalOriginalQty) * 100 : 0
    const view = {
      ...lot,
      x: cursor,
      width,
      xQty: cursorQty,
      widthQty: lot.originalQty,
      height: (lot.price / chartMaxPrice) * 100,
      remainingWidth: lot.originalQty > 0 ? (Math.max(0, lot.remainingQty) / lot.originalQty) * 100 : 0,
      soldWidth: lot.originalQty > 0 ? ((lot.originalQty - Math.max(0, lot.remainingQty)) / lot.originalQty) * 100 : 0,
    }
    cursor += width
    cursorQty += lot.originalQty
    return view
  })
  const lotViewMap = new Map(lotViews.map((lot) => [lot.id, lot]))
  const consumedWidthByLot = new Map<string, number>()
  const consumedQtyByLot = new Map<string, number>()
  const matchViews: BatchMatchView[] = []
  for (const match of input.matches) {
    const lot = lotViewMap.get(match.lotId)
    if (!lot || lot.originalQty <= 0) continue
    const matchWidth = (match.qty / lot.originalQty) * lot.width
    const consumedWidth = consumedWidthByLot.get(match.lotId) ?? 0
    const consumedQty = consumedQtyByLot.get(match.lotId) ?? 0
    const x = lot.x + lot.width - consumedWidth - matchWidth
    const xQty = lot.xQty + lot.originalQty - consumedQty - match.qty
    consumedWidthByLot.set(match.lotId, consumedWidth + matchWidth)
    consumedQtyByLot.set(match.lotId, consumedQty + match.qty)
    const deltaLow = Math.min(match.buyPrice, match.netSellPrice)
    const deltaHigh = Math.max(match.buyPrice, match.netSellPrice)
    const feePerShare = match.qty > 0 ? Math.max(0, match.fee / match.qty) : 0
    matchViews.push({
      ...match,
      x,
      width: matchWidth,
      xQty,
      widthQty: match.qty,
      deltaBottom: (deltaLow / chartMaxPrice) * 100,
      deltaHeight: Math.max(1.2, ((deltaHigh - deltaLow) / chartMaxPrice) * 100),
      feeBottom: (Math.max(0, match.sellPrice - feePerShare) / chartMaxPrice) * 100,
      feeHeight: feePerShare > 0 ? Math.max(0.8, (feePerShare / chartMaxPrice) * 100) : 0,
      positive: match.netSellPrice >= match.buyPrice,
    })
  }

  return {
    key: input.key,
    market: input.market,
    code: input.code,
    name: input.name,
    lots: lotViews,
    matches: matchViews,
    totalOriginalQty,
    totalRemainingQty,
    totalSoldQty: input.matches.reduce((sum, match) => sum + match.qty, 0),
    realizedPnl: input.matches.reduce((sum, match) => sum + match.pnl, 0),
    chartMaxPrice,
    currentPrice: input.currentPrice,
    currentPricePercent: input.currentPrice ? (input.currentPrice / chartMaxPrice) * 100 : null,
    currentPriceSource: input.currentPriceSource,
    currentPriceUpdatedAt: input.currentPriceUpdatedAt,
  }
}

function getBatchChartKindCode(kind: BatchChartKind) {
  return {
    held: 1,
    sold: 2,
    profit: 3,
    loss: 4,
    fee: 5,
    current: 6,
  }[kind]
}

function getBatchChartHoverFill(kindCode: number) {
  if (kindCode === getBatchChartKindCode('held')) return 'rgba(37,99,235,0.62)'
  if (kindCode === getBatchChartKindCode('sold')) return 'rgba(100,116,139,0.5)'
  if (kindCode === getBatchChartKindCode('profit')) return 'rgba(239,68,68,0.4)'
  if (kindCode === getBatchChartKindCode('loss')) return 'rgba(34,197,94,0.4)'
  if (kindCode === getBatchChartKindCode('fee')) return 'rgba(234,179,8,0.76)'
  return 'rgba(15,23,42,0.88)'
}

function getBatchChartHoverStroke(kindCode: number) {
  if (kindCode === getBatchChartKindCode('held')) return '#1d4ed8'
  if (kindCode === getBatchChartKindCode('sold')) return '#334155'
  if (kindCode === getBatchChartKindCode('profit')) return '#dc2626'
  if (kindCode === getBatchChartKindCode('loss')) return '#16a34a'
  if (kindCode === getBatchChartKindCode('fee')) return '#ca8a04'
  return '#0f172a'
}

function formatBatchChartKeyNumber(value: number) {
  return Number.isFinite(value) ? value.toFixed(6) : ''
}

function getBatchChartItemKey(
  kind: BatchChartKind,
  xStart: number,
  xEnd: number,
  yStart: number,
  yEnd: number,
  payload: Omit<BatchChartPayload, 'kind' | 'yStart' | 'yEnd'>,
) {
  return [
    kind,
    payload.lot?.id ?? '',
    payload.match?.id ?? '',
    formatBatchChartKeyNumber(payload.segmentQty),
    formatBatchChartKeyNumber(xStart),
    formatBatchChartKeyNumber(xEnd),
    formatBatchChartKeyNumber(yStart),
    formatBatchChartKeyNumber(yEnd),
  ].join('|')
}

function createBatchChartItem(
  name: string,
  kind: BatchChartKind,
  xStart: number,
  xEnd: number,
  yStart: number,
  yEnd: number,
  itemStyle: BatchChartDataItem['itemStyle'],
  payload: Omit<BatchChartPayload, 'kind' | 'yStart' | 'yEnd'>,
): BatchChartDataItem {
  const id = getBatchChartItemKey(kind, xStart, xEnd, yStart, yEnd, payload)
  return {
    id,
    name,
    value: [xStart, xEnd, yStart, yEnd, getBatchChartKindCode(kind), activeBatchChartKey.value === id ? 1 : 0],
    itemStyle,
    payload: {
      ...payload,
      kind,
      yStart,
      yEnd,
    },
  }
}

function buildBatchChartData(profile: BatchProfileModel): BatchChartDataItem[] {
  const items: BatchChartDataItem[] = []
  const lotById = new Map(profile.lots.map((lot) => [lot.id, lot]))
  const matchedQtyByLot = new Map<string, number>()
  for (const match of profile.matches) {
    matchedQtyByLot.set(match.lotId, (matchedQtyByLot.get(match.lotId) ?? 0) + match.qty)
  }
  for (const lot of profile.lots) {
    const remainingQty = Math.max(0, lot.remainingQty)
    const soldQty = Math.max(0, lot.originalQty - remainingQty)
    const matchedSoldQty = matchedQtyByLot.get(lot.id) ?? 0
    if (remainingQty > 0.000001) {
      items.push(createBatchChartItem(
        '持仓',
        'held',
        lot.xQty,
        lot.xQty + remainingQty,
        0,
        lot.price,
        { color: lot.synthetic ? 'rgba(100,116,139,0.34)' : 'rgba(37,99,235,0.38)' },
        {
          lot,
          segmentQty: remainingQty,
          currentPrice: profile.currentPrice,
          currentPriceSource: profile.currentPriceSource,
          currentPriceUpdatedAt: profile.currentPriceUpdatedAt,
        },
      ))
    }
    if (soldQty - matchedSoldQty > 0.000001) {
      const unmatchedSoldQty = soldQty - matchedSoldQty
      items.push(createBatchChartItem(
        '已卖',
        'sold',
        lot.xQty + remainingQty,
        lot.xQty + remainingQty + unmatchedSoldQty,
        0,
        lot.price,
        { color: 'rgba(148,163,184,0.34)' },
        {
          lot,
          segmentQty: unmatchedSoldQty,
          currentPrice: profile.currentPrice,
          currentPriceSource: profile.currentPriceSource,
          currentPriceUpdatedAt: profile.currentPriceUpdatedAt,
        },
      ))
    }
  }
  for (const match of profile.matches.slice().sort((left, right) => left.xQty - right.xQty)) {
    const lot = lotById.get(match.lotId)
    items.push(createBatchChartItem(
      '已卖',
      'sold',
      match.xQty,
      match.xQty + match.widthQty,
      0,
      lot?.price ?? match.buyPrice,
      {
        color: 'rgba(148,163,184,0.34)',
        borderColor: 'rgba(255,255,255,0.82)',
        borderWidth: 1,
      },
      {
        lot,
        match,
        segmentQty: match.qty,
        currentPrice: profile.currentPrice,
        currentPriceSource: profile.currentPriceSource,
        currentPriceUpdatedAt: profile.currentPriceUpdatedAt,
      },
    ))
  }
  for (const match of profile.matches) {
    const kind: BatchChartKind = match.positive ? 'profit' : 'loss'
    const low = Math.min(match.buyPrice, match.netSellPrice)
    const high = Math.max(match.buyPrice, match.netSellPrice)
    items.push(createBatchChartItem(
      match.positive ? '盈利' : '亏损',
      kind,
      match.xQty,
      match.xQty + match.widthQty,
      low,
      high,
      {
        color: match.positive ? 'rgba(239,68,68,0.2)' : 'rgba(34,197,94,0.2)',
        borderColor: match.positive ? 'rgba(220,38,38,0.92)' : 'rgba(22,163,74,0.92)',
        borderWidth: 1,
      },
      { match, segmentQty: match.qty },
    ))
    if (match.fee > 0.000001 && match.sellPrice > match.netSellPrice) {
      items.push(createBatchChartItem(
        '费用',
        'fee',
        match.xQty,
        match.xQty + match.widthQty,
        match.netSellPrice,
        match.sellPrice,
        {
          color: 'rgba(234,179,8,0.5)',
          borderColor: 'rgba(202,138,4,0.86)',
          borderWidth: 1,
        },
        { match, segmentQty: match.qty },
      ))
    }
  }
  if (profile.currentPrice != null) {
    items.push(createBatchChartItem(
      '现价',
      'current',
      0,
      profile.totalOriginalQty,
      profile.currentPrice,
      profile.currentPrice,
      { color: '#111827' },
      {
        segmentQty: 0,
        currentPrice: profile.currentPrice,
        currentPriceSource: profile.currentPriceSource,
        currentPriceUpdatedAt: profile.currentPriceUpdatedAt,
      },
    ))
  }
  return items
}

function renderBatchChartItem(params: any, api: any) {
  const kindCode = Number(api.value(4))
  const xStart = Number(api.value(0))
  const xEnd = Number(api.value(1))
  const yStart = Number(api.value(2))
  const yEnd = Number(api.value(3))
  const selected = Number(api.value(5)) === 1
  const coordSys = params.coordSys as { x: number; y: number; width: number; height: number }
  const start = api.coord([xStart, yStart])
  const end = api.coord([xEnd, yEnd])
  const x = Math.min(start[0], end[0])
  const y = Math.min(start[1], end[1])
  const width = Math.max(1, Math.abs(end[0] - start[0]))
  const height = kindCode === getBatchChartKindCode('current')
    ? 2
    : Math.max(2, Math.abs(start[1] - end[1]))

  if (kindCode === getBatchChartKindCode('current')) {
    const lineY = start[1] - 1
    const dataItem = params.data as BatchChartDataItem | undefined
    const label = getBatchCurrentPriceLabel(dataItem?.payload.currentPriceSource)
    return {
      type: 'group',
      children: [
        {
          type: 'rect',
          shape: {
            x: coordSys.x,
            y: lineY,
            width: coordSys.width,
            height,
          },
          style: {
            fill: '#111827',
          },
          silent: true,
        },
        {
          type: 'text',
          style: {
            x: coordSys.x + coordSys.width - 6,
            y: lineY - 4,
            text: `${label} ${formatPriceValue(yStart)}`,
            align: 'right',
            verticalAlign: 'bottom',
            fill: '#fff',
            backgroundColor: 'rgba(17,24,39,0.86)',
            borderRadius: 4,
            padding: [2, 6],
            fontSize: 11,
          },
          silent: true,
        },
      ],
    }
  }

  const shape = echarts.graphic.clipRectByRect(
    {
      x,
      y: yEnd === yStart ? y - 1 : y,
      width,
      height,
    },
    {
      x: coordSys.x,
      y: coordSys.y,
      width: coordSys.width,
      height: coordSys.height,
    },
  )
  if (!shape) return null
  const rectItem = {
    type: 'rect',
    shape,
    cursor: 'pointer',
    z2: kindCode >= getBatchChartKindCode('profit') ? 6 : 2,
    style: api.style(),
    emphasis: {
      z2: 24,
      style: {
        fill: getBatchChartHoverFill(kindCode),
        stroke: getBatchChartHoverStroke(kindCode),
        lineWidth: 2,
        opacity: 1,
        shadowBlur: 14,
        shadowColor: 'rgba(15,23,42,0.28)',
        shadowOffsetY: 1,
      },
    },
  }
  if (!selected) return rectItem
  return {
    type: 'group',
    z2: 40,
    children: [
      rectItem,
      {
        type: 'rect',
        shape,
        silent: true,
        z2: 42,
        style: {
          fill: 'transparent',
          stroke: getBatchChartHoverStroke(kindCode),
          lineWidth: 2.5,
          shadowBlur: 8,
          shadowColor: 'rgba(15,23,42,0.32)',
          shadowOffsetY: 1,
        },
      },
    ],
  }
}

function getBatchTooltipTitle(kind: BatchChartKind, hasMatch: boolean) {
  return {
    held: '持仓批次',
    sold: hasMatch ? '已卖片段' : '已卖批次',
    profit: '价差增值',
    loss: '价差损失',
    fee: '费用分摊',
    current: '当前价格',
  }[kind]
}

function getBatchTooltipLineTone(value: number): BatchTooltipTone {
  if (!Number.isFinite(value) || value === 0) return 'default'
  return value > 0 ? 'profit' : 'loss'
}

function buildBatchTooltipDetail(item: BatchChartDataItem): BatchTooltipDetail {
  const payload = item.payload
  const match = payload.match
  const lot = payload.lot ?? (
    match ? selectedBatchProfile.value?.lots.find((candidateLot) => candidateLot.id === match.lotId) : undefined
  )
  const lines: BatchTooltipDetail['lines'] = []

  if (payload.kind === 'current') {
    lines.push({ label: getBatchCurrentPriceLabel(payload.currentPriceSource), value: formatPriceValue(payload.currentPrice) })
    if (payload.currentPriceUpdatedAt) {
      lines.push({ label: '更新时间', value: payload.currentPriceUpdatedAt })
    }
    return {
      title: getBatchTooltipTitle(payload.kind, false),
      lines,
    }
  }

  if (match && (payload.kind === 'profit' || payload.kind === 'loss')) {
    const netDelta = match.netSellPrice - match.buyPrice
    const netRatio = match.buyPrice > 0 ? (netDelta / match.buyPrice) * 100 : Number.NaN
    const tone = getBatchTooltipLineTone(netDelta)
    lines.push({ label: '买入价格', value: formatPriceValue(match.buyPrice) })
    lines.push({ label: '扣费净卖价', value: formatPriceValue(match.netSellPrice), tone })
    lines.push({
      label: '每股差额',
      value: `${formatSignedPriceDelta(netDelta)}（${formatPercentValue(netRatio)}）`,
      tone,
    })
    lines.push({ label: '数量', value: formatQuantity(match.qty) })
    lines.push({
      label: '净归因盈亏',
      value: formatSignedMoneyValue(match.pnl),
      tone: getBatchTooltipLineTone(match.pnl),
    })
    return {
      title: getBatchTooltipTitle(payload.kind, true),
      lines,
    }
  }

  if (match && payload.kind === 'fee') {
    const feePerShare = match.qty > 0 ? match.fee / match.qty : Number.NaN
    const sellTime = formatTradeDateMinute(match.sellDate, match.sellTime)
    if (sellTime) {
      lines.push({ label: '卖出时间', value: sellTime })
    }
    lines.push({ label: '成交卖价', value: formatPriceValue(match.sellPrice) })
    lines.push({ label: '扣费净价', value: formatPriceValue(match.netSellPrice) })
    lines.push({ label: '每股费用', value: formatPerShareCostValue(feePerShare) })
    lines.push({ label: '数量', value: formatQuantity(match.qty) })
    lines.push({ label: '分摊费用', value: formatSmartMoneyNumber(match.fee) })
    return {
      title: getBatchTooltipTitle(payload.kind, true),
      lines,
    }
  }

  const buyTime = lot
    ? (lot.synthetic ? '持仓快照' : formatTradeDateMinute(lot.buyDate, lot.buyTime))
    : match ? formatTradeDateMinute(match.buyDate, match.buyTime) : ''
  const buyPrice = lot?.price ?? match?.buyPrice
  if (buyTime) {
    lines.push({ label: '买入时间', value: buyTime })
  }
  if (buyPrice != null) {
    lines.push({ label: '买入价格', value: formatPriceValue(buyPrice) })
  }

  if (match) {
    const sellDelta = match.sellPrice - match.buyPrice
    const sellRatio = match.buyPrice > 0 ? (sellDelta / match.buyPrice) * 100 : Number.NaN
    const sellTime = formatTradeDateMinute(match.sellDate, match.sellTime)
    const sellText = `${formatPriceValue(match.sellPrice)}（${formatSignedPriceDelta(sellDelta)}，${formatPercentValue(sellRatio)}）`
    if (sellTime) {
      lines.push({ label: '卖出时间', value: sellTime })
    }
    lines.push({ label: '卖出价格', value: sellText, tone: getBatchTooltipLineTone(sellDelta) })
  }

  const quantityText = lot
    ? `${formatQuantity(payload.segmentQty)} / ${formatQuantity(lot.originalQty)}`
    : formatQuantity(payload.segmentQty)
  lines.push({ label: '数量', value: quantityText })

  if (match) {
    lines.push({
      label: '归因盈亏',
      value: formatSignedMoneyValue(match.pnl),
      tone: getBatchTooltipLineTone(match.pnl),
    })
    if (match.fee > 0.000001) {
      lines.push({ label: '费用', value: formatSmartMoneyNumber(match.fee) })
    }
  } else if (payload.kind === 'held' && lot && payload.currentPrice != null) {
    const floatPnl = (payload.currentPrice - lot.price) * payload.segmentQty
    const floatRatio = lot.price > 0 ? ((payload.currentPrice - lot.price) / lot.price) * 100 : Number.NaN
    lines.push({ label: getBatchCurrentPriceLabel(payload.currentPriceSource), value: formatPriceValue(payload.currentPrice) })
    lines.push({
      label: '浮动盈亏',
      value: `${formatSignedMoneyValue(floatPnl)}（${formatPercentValue(floatRatio)}）`,
      tone: getBatchTooltipLineTone(floatPnl),
    })
  }

  return {
    title: getBatchTooltipTitle(payload.kind, Boolean(match)),
    lines,
  }
}

function buildBatchChartOption(profile: BatchProfileModel): BatchChartOption {
  const data = buildBatchChartData(profile)
  const showSlider = data.length > 28
  return {
    animationDuration: 160,
    grid: {
      top: 8,
      right: 12,
      bottom: showSlider ? 36 : 24,
      left: 52,
      containLabel: false,
    },
    xAxis: {
      type: 'value',
      min: 0,
      max: Math.max(1, profile.totalOriginalQty),
      axisLabel: {
        color: '#64748b',
        fontSize: 11,
        formatter: (value: number) => formatCompactQuantity(value, profile.totalOriginalQty >= 10000),
      },
      axisLine: {
        lineStyle: {
          color: '#cbd5e1',
        },
      },
      axisTick: {
        show: false,
      },
      splitLine: {
        show: false,
      },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: profile.chartMaxPrice,
      axisLabel: {
        color: '#64748b',
        fontSize: 11,
        formatter: (value: number) => formatPriceValue(value),
      },
      axisLine: {
        show: false,
      },
      axisTick: {
        show: false,
      },
      splitLine: {
        lineStyle: {
          color: 'rgba(148,163,184,0.18)',
          type: 'dashed',
        },
      },
    },
    dataZoom: showSlider
      ? [
          { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
          {
            type: 'slider',
            xAxisIndex: 0,
            height: 14,
            bottom: 4,
            brushSelect: false,
            filterMode: 'none',
          },
        ]
      : [
          { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
        ],
    series: [
      {
        type: 'custom',
        name: '批次剖面',
        renderItem: renderBatchChartItem,
        data,
        encode: {
          x: [0, 1],
          y: [2, 3],
        },
        emphasis: {
          itemStyle: {
            shadowBlur: 14,
            shadowColor: 'rgba(15,23,42,0.28)',
          },
        },
      },
    ],
  }
}

async function updateBatchChart() {
  await nextTick()
  const el = batchChartRef.value
  const profile = selectedBatchProfile.value
  if (!el || !profile) {
    disposeBatchChart()
    return
  }
  if (!batchChart) {
    batchChart = echarts.init(el)
    batchChartResizeObserver = new ResizeObserver(() => {
      batchChart?.resize()
    })
    batchChartResizeObserver.observe(el)
    batchChart.on('click', handleBatchChartClick)
    batchChart.getZr().on('click', handleBatchChartCanvasClick)
  }
  batchChart.setOption(buildBatchChartOption(profile), true)
  batchChart.resize()
}

function getBatchChartDataItem(data: unknown) {
  const item = data as BatchChartDataItem | undefined
  if (!item?.payload || item.payload.kind === 'current') return null
  return item
}

function handleBatchChartClick(params: { data?: unknown }) {
  const item = getBatchChartDataItem(params.data)
  if (!item) return
  activeBatchChartItem.value = item
  activeBatchChartKey.value = item.id
  refreshBatchChartSelection()
}

function handleBatchChartCanvasClick(event: { target?: unknown }) {
  if (!event.target) {
    clearBatchTooltip()
  }
}

function clearBatchTooltip() {
  activeBatchChartItem.value = null
  activeBatchChartKey.value = ''
  refreshBatchChartSelection()
}

function refreshBatchChartSelection() {
  const profile = selectedBatchProfile.value
  if (!batchChart || !profile) return
  batchChart.setOption({
    series: [
      {
        name: '批次剖面',
        data: buildBatchChartData(profile),
      },
    ],
  })
}

function syncActiveBatchChartItem() {
  const profile = selectedBatchProfile.value
  if (!profile || !activeBatchChartKey.value) return
  const item = buildBatchChartData(profile).find((candidate) => candidate.id === activeBatchChartKey.value)
  if (item) {
    activeBatchChartItem.value = item
  } else {
    activeBatchChartItem.value = null
    activeBatchChartKey.value = ''
  }
}

function disposeBatchChart() {
  batchChartResizeObserver?.disconnect()
  batchChartResizeObserver = undefined
  batchChart?.dispose()
  batchChart = null
  clearBatchTooltip()
}

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '读取失败'
}

function applySheetWorkbook(payload: EastmoneySheetWorkbook | undefined) {
  if (!payload) return
  sheetWorkbook.value = payload
  sheetReloadToken.value += 1
}

async function refreshSheetWorkbook(options: { silent?: boolean } = {}) {
  if (!options.silent) {
    sheetWorkbookLoading.value = true
  }
  try {
    applySheetWorkbook(await refreshEastmoneySheetWorkbook())
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    if (!options.silent) {
      sheetWorkbookLoading.value = false
    }
  }
}

function getSheetItem(key: string): EastmoneySheetWorkbookSheet | null {
  return sheetWorkbook.value?.sheets.find((sheet) => sheet.key === key) ?? null
}

function getSheetWorkspaceKey(key: string) {
  const sheet = getSheetItem(key)
  return `${workbookId.value ?? 'none'}:${sheet?.sheet_id ?? 'none'}:${sheet?.updated_at ?? 0}:${sheetReloadToken.value}`
}

function setSheetWorkspaceRef(key: string, instance: unknown) {
  sheetWorkspaceRefs.value[key] = instance as NoteSheetWorkspaceExpose | null
}

function getSheetTabMenuSheet() {
  return getSheetItem(sheetTabMenu.value.key)
}

function getSheetWorkbookUrl(sheet: EastmoneySheetWorkbookSheet | null = getSheetTabMenuSheet()) {
  if (!workbookId.value) return ''
  const query = sheet ? `?sheet=${sheet.sheet_id}` : ''
  return `/workbook/${workbookId.value}${query}`
}

function getStandaloneSheetUrl(sheet: EastmoneySheetWorkbookSheet | null = getSheetTabMenuSheet()) {
  return sheet ? `/sheet/${sheet.sheet_id}` : ''
}

function toAbsoluteUrl(path: string) {
  return new URL(path, window.location.origin).toString()
}

async function copyTextToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }

  const input = document.createElement('textarea')
  input.value = text
  input.setAttribute('readonly', 'readonly')
  input.style.position = 'fixed'
  input.style.left = '-9999px'
  document.body.appendChild(input)
  input.select()
  document.execCommand('copy')
  document.body.removeChild(input)
}

function positionSheetTabMenu(event: MouseEvent) {
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight
  sheetTabMenu.value.x = Math.max(8, Math.min(event.clientX, viewportWidth - SHEET_TAB_CONTEXT_MENU_WIDTH - 8))
  sheetTabMenu.value.y = Math.max(8, Math.min(event.clientY, viewportHeight - SHEET_TAB_CONTEXT_MENU_HEIGHT - 8))
}

function openSheetTabMenu(event: MouseEvent, key: string) {
  if (!getSheetItem(key)) return
  sheetTabMenu.value.key = key
  sheetTabMenu.value.visible = true
  positionSheetTabMenu(event)
}

function closeSheetTabMenu() {
  sheetTabMenu.value.visible = false
}

async function openSheetSettingsFromTabMenu() {
  const key = sheetTabMenu.value.key
  closeSheetTabMenu()
  if (!key || !getSheetItem(key)) return
  if (activeTab.value !== key) {
    activeTab.value = key
  }
  const workspace = await waitForSheetWorkspaceRef(key)
  if (!workspace) {
    ElMessage.warning('工作表还在加载')
    return
  }
  workspace.openSheetSettings?.()
}

async function waitForSheetWorkspaceRef(key: string) {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await nextTick()
    const workspace = sheetWorkspaceRefs.value[key]
    if (workspace) return workspace
    await new Promise((resolve) => window.requestAnimationFrame(resolve))
  }
  return sheetWorkspaceRefs.value[key] ?? null
}

function openWorkbookFromTabMenu() {
  const url = getSheetWorkbookUrl()
  closeSheetTabMenu()
  if (!url) return
  window.open(url, '_blank', 'noopener')
}

function openSheetFromTabMenu() {
  const url = getStandaloneSheetUrl()
  closeSheetTabMenu()
  if (!url) return
  window.open(url, '_blank', 'noopener')
}

async function copyWorkbookLinkFromTabMenu() {
  const url = getSheetWorkbookUrl()
  closeSheetTabMenu()
  if (!url) return
  try {
    await copyTextToClipboard(toAbsoluteUrl(url))
    ElMessage.success('已复制工作簿链接')
  } catch {
    ElMessage.error('复制链接失败')
  }
}

async function copySheetLinkFromTabMenu() {
  const url = getStandaloneSheetUrl()
  closeSheetTabMenu()
  if (!url) return
  try {
    await copyTextToClipboard(toAbsoluteUrl(url))
    ElMessage.success('已复制工作表链接')
  } catch {
    ElMessage.error('复制链接失败')
  }
}

async function refreshSheetWorkbookFromTabMenu() {
  closeSheetTabMenu()
  await refreshSheetWorkbook()
}

function handleSheetTabMenuCommand(command: SheetTabMenuCommand) {
  switch (command) {
    case 'open_workbook':
      openWorkbookFromTabMenu()
      break
    case 'open_sheet':
      openSheetFromTabMenu()
      break
    case 'copy_workbook_link':
      void copyWorkbookLinkFromTabMenu()
      break
    case 'copy_sheet_link':
      void copySheetLinkFromTabMenu()
      break
    case 'configure':
      void openSheetSettingsFromTabMenu()
      break
    case 'refresh':
      void refreshSheetWorkbookFromTabMenu()
      break
  }
}

function handleGlobalPointerDown(event: MouseEvent) {
  if (!sheetTabMenu.value.visible) return
  const target = event.target as HTMLElement | null
  if (target?.closest('.sheet-tab-context-menu')) return
  closeSheetTabMenu()
}

function handleGlobalKeyDown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeSheetTabMenu()
}

async function loadLatestAssetSnapshot() {
  try {
    latestAssetSnapshot.value = (await fetchLatestEastmoneyAssetSnapshot()).item
  } catch {
    latestAssetSnapshot.value = null
  }
}

async function fetchBatchTradeRecords() {
  const limit = 1000
  const firstPage = await fetchEastmoneyTradeRecords({ limit, offset: 0 })
  const items = [...firstPage.items]
  for (let offset = limit; offset < firstPage.total; offset += limit) {
    const page = await fetchEastmoneyTradeRecords({ limit, offset })
    items.push(...page.items)
  }
  return items
}

async function loadBatchMarketQuotes() {
  try {
    const quotePage = await fetchLatestEastmoneyMarketQuotes()
    batchMarketQuotes.value = quotePage.items
    batchQuoteError.value = ''
  } catch (error) {
    console.warn('Failed to load eastmoney market quote cache:', error)
  }
}

async function refreshBatchMarketQuotes(options: { silent?: boolean } = {}) {
  if (batchQuoteRefreshing.value) return
  if (document.hidden) return
  batchQuoteRefreshing.value = true
  try {
    const result = await refreshEastmoneyMarketQuotes()
    batchMarketQuotes.value = result.items
    batchQuoteError.value = result.error || ''
    if (result.error) {
      console.warn(result.error)
      if (!options.silent) ElMessage.warning(result.error)
    }
  } catch (error) {
    batchQuoteError.value = getErrorMessage(error)
    console.warn('Failed to refresh eastmoney market quotes:', error)
    if (!options.silent) ElMessage.warning(batchQuoteError.value)
  } finally {
    batchQuoteRefreshing.value = false
  }
}

function startBatchQuoteRefreshTimer() {
  stopBatchQuoteRefreshTimer()
  batchQuoteRefreshTimer = window.setInterval(() => {
    void refreshBatchMarketQuotes({ silent: true })
  }, 60000)
}

function stopBatchQuoteRefreshTimer() {
  if (batchQuoteRefreshTimer != null) {
    window.clearInterval(batchQuoteRefreshTimer)
    batchQuoteRefreshTimer = undefined
  }
}

function handleVisibilityChange() {
  if (!document.hidden) {
    void refreshBatchMarketQuotes({ silent: true })
  }
}

async function loadBatchProfileData() {
  batchProfileLoading.value = true
  try {
    const [tradeRecords, positionPage] = await Promise.all([
      fetchBatchTradeRecords(),
      fetchLatestEastmoneyPositions(),
    ])
    batchTradeRecords.value = tradeRecords
    batchPositions.value = positionPage.items
    const options = buildBatchSecurityOptions(tradeRecords, positionPage.items)
    if (!options.length) {
      selectedBatchSecurityKey.value = ''
    } else if (!selectedBatchSecurityKey.value || !options.some((option) => option.key === selectedBatchSecurityKey.value)) {
      selectedBatchSecurityKey.value = getDefaultBatchSecurityKey(options)
    }
    void loadBatchMarketQuotes()
  } catch (error) {
    console.warn('Failed to load eastmoney batch profile data:', error)
    batchTradeRecords.value = []
    batchPositions.value = []
  } finally {
    batchProfileLoading.value = false
  }
}

async function openTradeAccountPage() {
  tradePageOpening.value = true
  pageError.value = ''
  try {
    const state = await openEastmoneyTradeAccountPage()
    if (state.login_required) {
      const durationText = state.login_duration_preset ? '在线时间已选 3 小时，' : ''
      if (state.captcha_ocr_filled && state.captcha_ocr_text) {
        ElMessage.info(`${durationText}验证码已尝试填入 ${state.captcha_ocr_text}，请核对后手动登录。`)
      } else {
        ElMessage.info(`${durationText}已打开东方财富登录页，请手动输入验证码并登录。`)
      }
    } else if (state.account_label) {
      ElMessage.success(`资金账户已登录：${state.account_label}`)
    } else {
      ElMessage.info('已打开东方财富登录页。')
    }
  } catch (error) {
    pageError.value = getErrorMessage(error)
    ElMessage.error(pageError.value)
  } finally {
    tradePageOpening.value = false
  }
}

async function syncToDatabase() {
  syncing.value = true
  pageError.value = ''
  try {
    const run = await syncEastmoneyTradeData(defaultSyncDateParams())
    applySheetWorkbook(run.sheet_workbook)
    if (run.status === 'login_required') {
      ElMessage.warning('证券交易系统未登录，请先打开登录页并完成资金账号登录。')
    } else if (run.status === 'success') {
      ElMessage.success(`同步完成：新增 ${run.inserted_count} 条，更新 ${run.updated_count} 条`)
      activeTab.value = 'operation-history'
    }
    await Promise.all([
      run.sheet_workbook ? Promise.resolve() : refreshSheetWorkbook({ silent: true }),
      loadLatestAssetSnapshot(),
      loadBatchProfileData(),
    ])
    void refreshBatchMarketQuotes({ silent: true })
  } catch (error) {
    pageError.value = getErrorMessage(error)
    ElMessage.error(pageError.value)
  } finally {
    syncing.value = false
  }
}

function extractClipboardImage(event: ClipboardEvent): File | null {
  const items = Array.from(event.clipboardData?.items || [])
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      return item.getAsFile()
    }
  }
  return null
}

function toggleTradeDetailPasteImport() {
  pasteImportEnabled.value = !pasteImportEnabled.value
  if (pasteImportEnabled.value) {
    ElMessage.info('已准备导入东方财富手机交易明细，请直接粘贴截图。')
  }
}

async function importTradeDetailImage(image: File) {
  ocrImporting.value = true
  try {
    const result = await importEastmoneyTradeDetailFromOcr(image)
    const record = result.record
    const actionText = result.created ? '新增' : '更新'
    const recordName = record.security_code || record.security_name || '成交明细'
    ElMessage.success(`已${actionText} ${recordName} ${record.trade_date}，可继续粘贴`)
    activeTab.value = 'local-history'
    applySheetWorkbook(result.sheet_workbook)
    await Promise.all([
      result.sheet_workbook ? Promise.resolve() : refreshSheetWorkbook({ silent: true }),
      loadLatestAssetSnapshot(),
      loadBatchProfileData(),
    ])
    void refreshBatchMarketQuotes({ silent: true })
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    ocrImporting.value = false
  }
}

async function handleWindowPaste(event: ClipboardEvent) {
  if (!pasteImportEnabled.value || ocrImporting.value) return
  const image = extractClipboardImage(event)
  if (!image) return
  event.preventDefault()
  event.stopPropagation()
  await importTradeDetailImage(image)
}

watch(selectedBatchSecurityKey, () => {
  clearBatchTooltip()
})

watch(selectedBatchProfile, () => {
  syncActiveBatchChartItem()
  void updateBatchChart()
}, { flush: 'post' })

onMounted(() => {
  window.addEventListener('paste', handleWindowPaste)
  window.addEventListener('mousedown', handleGlobalPointerDown)
  window.addEventListener('keydown', handleGlobalKeyDown)
  document.addEventListener('visibilitychange', handleVisibilityChange)
  void refreshSheetWorkbook()
  void loadLatestAssetSnapshot()
  void loadBatchProfileData()
  void loadBatchMarketQuotes()
  void refreshBatchMarketQuotes({ silent: true })
  startBatchQuoteRefreshTimer()
})

onBeforeUnmount(() => {
  window.removeEventListener('paste', handleWindowPaste)
  window.removeEventListener('mousedown', handleGlobalPointerDown)
  window.removeEventListener('keydown', handleGlobalKeyDown)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  stopBatchQuoteRefreshTimer()
  disposeBatchChart()
})
</script>

<template>
  <div class="eastmoney-page">
    <header class="page-toolbar">
      <div class="title-line">
        <h1>东方财富</h1>
        <el-tooltip placement="bottom-start">
          <template #content>
            <div class="tooltip-content">
              交易同步依赖东方财富资金账户网页登录态；先打开登录页手动登录，再同步到本地库。截图导入是独立的手动入口。
            </div>
          </template>
          <el-icon class="help-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-tag v-if="accountLabel" size="small" effect="plain">{{ accountLabel }}</el-tag>
      </div>
      <div class="toolbar-actions">
        <div class="action-group">
          <el-button
            :icon="Link"
            :loading="tradePageOpening"
            size="small"
            @click="openTradeAccountPage"
          >
            登录页
          </el-button>
          <el-button
            :icon="Download"
            :loading="syncing"
            size="small"
            type="primary"
            @click="syncToDatabase"
          >
            同步到本地库
          </el-button>
        </div>
        <div class="action-group">
          <span class="action-group-label">手动导入</span>
          <el-button
            :icon="Upload"
            :loading="ocrImporting"
            size="small"
            :type="pasteImportEnabled ? 'primary' : 'default'"
            @click="toggleTradeDetailPasteImport"
          >
            {{ ocrImporting ? '识别中...' : pasteImportEnabled ? '关闭截图导入' : '粘贴截图导入' }}
          </el-button>
        </div>
      </div>
    </header>

    <el-alert
      v-if="pageError"
      class="page-alert"
      :title="pageError"
      type="error"
      :closable="false"
      show-icon
    />
    <section v-if="summaryItems.length" class="summary-strip">
      <div v-for="item in summaryItems" :key="item.label" class="summary-item">
        <span>{{ item.label }}</span>
        <strong :class="{ negative: item.negative }">{{ item.value }}</strong>
      </div>
      <div v-if="lastUpdatedAtText" class="summary-item muted">
        <span>资产更新时间</span>
        <strong>{{ lastUpdatedAtText }}</strong>
      </div>
    </section>

    <section class="batch-profile-section" v-loading="batchProfileLoading">
      <div class="batch-profile-header">
        <div class="batch-profile-title">
          <strong>批次剖面</strong>
          <span v-if="selectedBatchProfile">
            {{ selectedBatchProfile.market }}.{{ selectedBatchProfile.code }} {{ selectedBatchProfile.name }}
          </span>
          <span v-if="batchQuoteStatusText" class="batch-quote-status">{{ batchQuoteStatusText }}</span>
        </div>
        <div class="batch-profile-legend" aria-hidden="true">
          <span class="is-held">持仓</span>
          <span class="is-sold">已卖</span>
          <span class="is-profit">盈利</span>
          <span class="is-loss">亏损</span>
          <span class="is-fee">费用</span>
        </div>
        <el-select
          v-model="selectedBatchSecurityKey"
          class="batch-security-select"
          size="small"
          filterable
          placeholder="选择股票"
          :disabled="!batchSecurityOptions.length"
        >
          <el-option
            v-for="option in batchSecurityOptions"
            :key="option.key"
            :label="`${option.market}.${option.code} ${option.name}`"
            :value="option.key"
          />
        </el-select>
      </div>

      <div v-if="selectedBatchProfile" class="batch-profile-body">
        <div class="batch-chart-wrap">
          <div
            ref="batchChartRef"
            class="batch-chart"
            :aria-label="`${selectedBatchProfile.name} 批次剖面`"
          ></div>
        </div>
        <div class="batch-profile-side">
          <div class="batch-pinned-tooltip">
            <template v-if="activeBatchTooltipDetail">
              <div class="batch-pinned-tooltip-title">
              <strong>{{ activeBatchTooltipDetail.title }}</strong>
              <button
                type="button"
                aria-label="清空批次详情"
                @click.stop="clearBatchTooltip"
              >
                ×
              </button>
              </div>
              <div
                v-for="line in activeBatchTooltipDetail.lines"
                :key="line.label"
                class="batch-pinned-tooltip-line"
              >
                <span>{{ line.label }}</span>
                <strong :class="line.tone">{{ line.value }}</strong>
              </div>
            </template>
            <div v-else class="batch-detail-empty">
              <strong>批次明细</strong>
              <span>点击图中柱段查看详情</span>
            </div>
          </div>
          <div class="batch-profile-stats">
            <div>
              <span>原始批次数量</span>
              <strong>{{ formatCompactQuantity(selectedBatchProfile.totalOriginalQty) }}</strong>
            </div>
            <div>
              <span>当前剩余</span>
              <strong>{{ formatCompactQuantity(selectedBatchProfile.totalRemainingQty) }}</strong>
            </div>
            <div>
              <span>已匹配卖出</span>
              <strong>{{ formatCompactQuantity(selectedBatchProfile.totalSoldQty) }}</strong>
            </div>
            <div>
              <span>归因盈亏</span>
              <strong :class="{ negative: selectedBatchProfile.realizedPnl < 0 }">
                {{ formatSmartMoneyNumber(selectedBatchProfile.realizedPnl) || '--' }}
              </strong>
            </div>
          </div>
        </div>
      </div>
      <el-empty
        v-else
        class="batch-profile-empty"
        :description="batchProfileLoading ? '正在加载批次数据' : '暂无可视化批次数据'"
      />
    </section>

    <el-tabs v-model="activeTab" class="data-tabs" v-loading="sheetWorkbookLoading">
      <el-tab-pane
        v-for="tab in sheetTabs"
        :key="tab.key"
        :name="tab.key"
      >
        <template #label>
          <span
            class="sheet-tab-label"
            @contextmenu.prevent.stop="openSheetTabMenu($event, tab.key)"
          >
            {{ tab.label }}
          </span>
        </template>
        <NoteSheetWorkspace
          v-if="workbookId && tab.sheet"
          :ref="(instance) => setSheetWorkspaceRef(tab.key, instance)"
          class="eastmoney-sheet-workspace"
          :key="getSheetWorkspaceKey(tab.key)"
          :workbook-id="workbookId"
          :sheet-id="tab.sheet.sheet_id"
          default-height-mode="content"
          :show-title-input="false"
          :empty-text="tab.emptyText"
        />
        <el-empty
          v-else
          :description="sheetWorkbookLoading ? '正在刷新表格文件' : tab.emptyText"
        />
      </el-tab-pane>
    </el-tabs>

    <teleport to="body">
      <div
        v-if="sheetTabMenu.visible"
        class="sheet-tab-context-menu"
        :style="{ left: `${sheetTabMenu.x}px`, top: `${sheetTabMenu.y}px` }"
        @mousedown.stop
        @contextmenu.prevent
      >
        <template v-for="item in sheetTabMenuItems" :key="item.command">
          <div v-if="item.divided" class="sheet-tab-context-menu-separator"></div>
          <button
            type="button"
            class="sheet-tab-context-menu-item"
            @click="handleSheetTabMenuCommand(item.command)"
          >
            {{ item.label }}
          </button>
        </template>
      </div>
    </teleport>

  </div>
</template>

<style scoped>
.eastmoney-page {
  display: flex;
  height: 100%;
  min-height: 100%;
  min-width: 0;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px 20px;
  color: #1f2937;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 10px;
}

.title-line {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.title-line h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  letter-spacing: 0;
}

.help-icon {
  color: #64748b;
  cursor: help;
}

.tooltip-content {
  max-width: 280px;
  line-height: 1.5;
}

.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 10px 14px;
}

.action-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.action-group-label {
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.page-alert {
  flex: 0 0 auto;
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  align-items: flex-start;
  border-bottom: 1px solid #eef2f7;
  padding-bottom: 10px;
}

.summary-item {
  display: grid;
  grid-template-columns: auto;
  gap: 2px;
  min-width: 92px;
}

.summary-item span {
  color: #64748b;
  font-size: 12px;
}

.summary-item strong {
  color: #111827;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0;
}

.summary-item strong.negative {
  color: #15803d;
}

.summary-item.muted strong {
  color: #475569;
  font-size: 13px;
  font-weight: 500;
}

.batch-profile-section {
  flex: 0 0 auto;
  min-width: 0;
  border-bottom: 1px solid #eef2f7;
  padding-bottom: 12px;
}

.batch-profile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.batch-profile-title {
  display: flex;
  min-width: 0;
  align-items: baseline;
  gap: 8px;
  flex: 1 1 auto;
}

.batch-profile-title strong {
  color: #111827;
  font-size: 14px;
  font-weight: 650;
}

.batch-profile-title span {
  overflow: hidden;
  color: #64748b;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.batch-profile-title .batch-quote-status {
  flex: 0 0 auto;
  color: #2563eb;
}

.batch-profile-legend {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  color: #64748b;
  font-size: 12px;
}

.batch-profile-legend span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.batch-profile-legend span::before {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  content: '';
}

.batch-profile-legend .is-held::before {
  background: rgba(37, 99, 235, 0.42);
}

.batch-profile-legend .is-sold::before {
  background: rgba(148, 163, 184, 0.38);
}

.batch-profile-legend .is-profit::before {
  background: rgba(239, 68, 68, 0.5);
}

.batch-profile-legend .is-loss::before {
  background: rgba(34, 197, 94, 0.5);
}

.batch-profile-legend .is-fee::before {
  background: rgba(234, 179, 8, 0.66);
}

.batch-security-select {
  width: 240px;
  flex: 0 0 auto;
}

.batch-profile-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 10px;
  align-items: stretch;
}

.batch-price-axis {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 2px 0 20px;
  color: #94a3b8;
  font-size: 11px;
  text-align: right;
}

.batch-chart-wrap {
  position: relative;
  min-width: 0;
}

.batch-chart {
  position: relative;
  min-height: 220px;
  overflow: hidden;
  border: 1px solid #dbe7f5;
  border-radius: 6px;
  background:
    linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.98)),
    repeating-linear-gradient(90deg, transparent 0, transparent 23px, rgba(148, 163, 184, 0.1) 24px);
}

.batch-profile-side {
  display: grid;
  min-width: 0;
  align-content: start;
  gap: 8px;
}

.batch-pinned-tooltip {
  width: 100%;
  height: 176px;
  overflow: auto;
  padding: 9px 10px;
  border: 1px solid #d8e2ef;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.08);
}

.batch-detail-empty {
  display: grid;
  height: 100%;
  align-content: center;
  justify-items: center;
  gap: 4px;
  color: #94a3b8;
  font-size: 12px;
}

.batch-detail-empty strong {
  color: #475569;
  font-size: 12px;
  font-weight: 650;
}

.batch-pinned-tooltip-title,
.batch-pinned-tooltip-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.batch-pinned-tooltip-title {
  margin-bottom: 4px;
}

.batch-pinned-tooltip-title strong {
  color: #0f172a;
  font-size: 12px;
  font-weight: 700;
}

.batch-pinned-tooltip-title span {
  color: #94a3b8;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}

.batch-pinned-tooltip-title button {
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}

.batch-pinned-tooltip-title button:hover {
  background: #f1f5f9;
  color: #0f172a;
}

.batch-pinned-tooltip-line {
  line-height: 1.65;
}

.batch-pinned-tooltip-line span {
  flex: 0 0 auto;
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.batch-pinned-tooltip-line strong {
  min-width: 0;
  color: #111827;
  font-size: 12px;
  font-weight: 650;
  text-align: right;
  word-break: break-word;
}

.batch-pinned-tooltip-line strong.profit {
  color: #b91c1c;
}

.batch-pinned-tooltip-line strong.loss {
  color: #15803d;
}

.batch-grid-line {
  position: absolute;
  left: 0;
  right: 0;
  border-top: 1px dashed rgba(100, 116, 139, 0.18);
  pointer-events: none;
}

.batch-grid-line.is-top {
  top: 8px;
}

.batch-grid-line.is-middle {
  top: 50%;
}

.batch-current-line {
  position: absolute;
  left: 0;
  right: 0;
  z-index: 8;
  border-top: 1px solid #111827;
  pointer-events: none;
}

.batch-current-line span {
  position: absolute;
  right: 6px;
  bottom: 2px;
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(17, 24, 39, 0.84);
  color: #fff;
  font-size: 11px;
  line-height: 1.3;
}

.batch-lot {
  position: absolute;
  bottom: 0;
  z-index: 2;
  min-width: 2px;
  border-right: 1px solid rgba(255, 255, 255, 0.8);
  border-left: 1px solid rgba(15, 23, 42, 0.06);
  background: transparent;
}

.batch-lot.is-synthetic {
  opacity: 0.82;
}

.batch-lot-held,
.batch-lot-sold {
  position: absolute;
  top: 0;
  bottom: 0;
}

.batch-lot-held {
  left: 0;
  background: rgba(37, 99, 235, 0.34);
}

.batch-lot-sold {
  right: 0;
  background: rgba(148, 163, 184, 0.32);
}

.batch-lot.is-synthetic .batch-lot-held {
  background: rgba(100, 116, 139, 0.32);
}

.batch-lot-date,
.batch-lot-price {
  position: absolute;
  left: 4px;
  z-index: 3;
  max-width: calc(100% - 8px);
  overflow: hidden;
  color: #1e3a8a;
  font-size: 10px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.batch-lot-price {
  bottom: 18px;
}

.batch-lot-date {
  bottom: 4px;
}

.batch-lot.is-synthetic .batch-lot-date,
.batch-lot.is-synthetic .batch-lot-price {
  color: #475569;
}

.batch-match {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 6;
  min-width: 2px;
  pointer-events: auto;
}

.batch-match-delta,
.batch-match-fee {
  position: absolute;
  left: 0;
  right: 0;
  min-height: 2px;
}

.batch-match-delta.is-profit {
  border: 1px solid rgba(220, 38, 38, 0.88);
  background: rgba(239, 68, 68, 0.11);
}

.batch-match-delta.is-loss {
  border: 1px solid rgba(22, 163, 74, 0.9);
  background: rgba(34, 197, 94, 0.12);
}

.batch-match-fee {
  z-index: 2;
  border: 1px solid rgba(202, 138, 4, 0.84);
  background: rgba(234, 179, 8, 0.44);
}

.batch-match span {
  position: absolute;
  left: 3px;
  top: -18px;
  max-width: 160px;
  overflow: hidden;
  color: #334155;
  font-size: 10px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.batch-profile-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-content: start;
  gap: 8px 12px;
  padding-top: 2px;
}

.batch-profile-stats div {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.batch-profile-stats span {
  color: #64748b;
  font-size: 12px;
}

.batch-profile-stats strong {
  color: #111827;
  font-size: 14px;
  font-weight: 650;
}

.batch-profile-stats strong.negative {
  color: #15803d;
}

.batch-profile-empty {
  --el-empty-padding: 10px 0;
}

.data-tabs {
  min-width: 0;
}

.data-tabs :deep(.el-tabs__content) {
  min-height: 0;
  overflow: visible;
}

.data-tabs :deep(.el-tab-pane) {
  min-height: 0;
}

.sheet-tab-label {
  display: inline-flex;
  align-items: center;
  height: 100%;
}

:global(.sheet-tab-context-menu) {
  position: fixed;
  z-index: 2500;
  min-width: 152px;
  padding: 4px;
  border: 1px solid #d8e4f7;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.14);
}

:global(.sheet-tab-context-menu-item) {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #1f2937;
  font: inherit;
  font-size: 13px;
  line-height: 1.2;
  text-align: left;
  cursor: pointer;
}

:global(.sheet-tab-context-menu-item:hover) {
  background: #eff6ff;
  color: #1d4ed8;
}

:global(.sheet-tab-context-menu-separator) {
  height: 1px;
  margin: 4px 2px;
  background: #e5e7eb;
}

@media (max-width: 980px) {
  .batch-profile-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .batch-pinned-tooltip {
    height: auto;
    min-height: 96px;
    max-height: none;
  }
}

@media (max-width: 760px) {
  .eastmoney-page {
    padding: 10px;
  }

  .page-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .toolbar-actions {
    justify-content: flex-start;
    width: 100%;
  }

  .action-group {
    flex-wrap: wrap;
  }

}
</style>

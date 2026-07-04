<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Delete, Search, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type Handsontable from 'handsontable/base'
import type { CellProperties, ColumnSettings } from 'handsontable/settings'
import { useRoute, useRouter } from 'vue-router'
import StandardPagination from '@/components/StandardPagination.vue'
import { useUserStore } from '@/store/userStore'
import { formatNoteDateTimeDetailed } from '@/utils/noteDate'

import {
  executeAttendanceOrder,
  fetchAttendanceConfig,
  fetchAttendanceOrderRefundDetails,
  fetchAttendanceOrderRefundHistory,
  type AttendanceConfigResponse,
  type AttendanceOrderRefundDetailItem,
  type AttendanceOrderRefundDetailSummary,
  type AttendanceOrderRow,
  type AttendanceOrderRefundHistoryItem,
} from '@/api/attendance'

type InputOrderRow = {
  订单号: string
  学员名称: string
}

type QueryOrderRow = {
  学员名称: string
  微信支付订单号: string
  商户订单号: string
  订单金额: string | number
  已返款: string | number
  剩余金额: string | number
  退款额度: string | number
  退款原因: string
}

type RefundResultRow = {
  学员名称: string
  微信支付订单号: string
  商户订单号: string
  订单金额: string | number
  已返款: string | number
  剩余金额: string | number
  处理结果: string
}

type OrderSubview = 'refund' | 'detail'
type PersistedDetailDraft = {
  orderId: string
  searched: boolean
  summary: AttendanceOrderRefundDetailSummary | null
  rows: AttendanceOrderRefundDetailItem[]
}

const INPUT_TABLE_COLUMNS = ['订单号', '学员名称'] as const
const QUERY_TABLE_COLUMNS = ['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额', '退款额度', '退款原因'] as const
const REFUND_TABLE_COLUMNS = ['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额', '处理结果'] as const

const INPUT_TABLE_HEADERS = ['订单号（必填，兼容微信支付单号/商户单号）', '学员名称（选填）']
const QUERY_TABLE_HEADERS = ['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额', '退款额度(默认全退)', '退款原因（推荐填）']
const REFUND_TABLE_HEADERS = ['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额', '处理结果']

const QUERY_READONLY_COLUMNS = new Set(['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额'])
const REFUND_READONLY_COLUMNS = new Set(REFUND_TABLE_COLUMNS)
const ORDER_DRAFT_STORAGE_KEY_PREFIX = 'attendance.orders.v2'
const DETAIL_DRAFT_STORAGE_KEY_PREFIX = 'attendance.order-detail.v1'
const HOT_TABLE_HEADER_HEIGHT = 42
const HOT_TABLE_ROW_HEIGHT = 28
const HOT_TABLE_MAX_VISIBLE_ROWS = 12
const INPUT_TABLE_MIN_VISIBLE_ROWS = 6
const QUERY_TABLE_MIN_VISIBLE_ROWS = 4
const REFUND_HISTORY_PAGE_SIZE_OPTIONS = [20, 50, 100, 200]

const loading = ref(false)
const querying = ref(false)
const refunding = ref(false)
const detailQuerying = ref(false)
const refundHistoryLoading = ref(false)
const config = ref<AttendanceConfigResponse | null>(null)
const pageRootRef = ref<HTMLElement | null>(null)
const inputHotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const queryHotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const refundHotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const viewportWidth = ref(resolveInitialViewportWidth())
const inputMeasuredHeight = ref<number | null>(null)
const queryMeasuredHeight = ref<number | null>(null)
const inputRows = ref<InputOrderRow[]>([createEmptyInputRow()])
const queryRows = ref<QueryOrderRow[]>([])
const refundRows = ref<RefundResultRow[]>([])
const detailOrderId = ref('')
const detailSearched = ref(false)
const detailRows = ref<AttendanceOrderRefundDetailItem[]>([])
const detailSummary = ref<AttendanceOrderRefundDetailSummary | null>(null)
const refundHistoryItems = ref<AttendanceOrderRefundHistoryItem[]>([])
const refundHistoryPage = ref(1)
const refundHistoryPageSize = ref(20)
const refundHistoryTotal = ref(0)
const refundHistoryLoaded = ref(false)
const refundHistoryPending = ref(false)
const restoredInputDraftStorageKey = ref<string | null>(null)
const restoredQueryDraftStorageKey = ref<string | null>(null)
const restoredRefundDraftStorageKey = ref<string | null>(null)
const restoredDetailDraftStorageKey = ref<string | null>(null)
const refundErrorMessage = ref('')
const userStore = useUserStore()
const route = useRoute()
const router = useRouter()
const activeSubview = ref<OrderSubview>(resolveOrderSubview(route.query.tab))
let orderResizeObserver: ResizeObserver | null = null
let hotTableComponentLoader: Promise<unknown> | null = null
let hotTableAssetsLoader: Promise<void> | null = null
let refundHistoryLoadPromise: Promise<void> | null = null
let refundHistoryDeferredTimer: number | null = null

const loadHotTableAssets = async () => {
  if (!hotTableAssetsLoader) {
    hotTableAssetsLoader = Promise.all([
      import('handsontable/styles/handsontable.css'),
      import('handsontable/styles/ht-theme-main.css'),
    ]).then(() => undefined)
  }
  return hotTableAssetsLoader
}

const loadHotTableComponent = async () => {
  if (!hotTableComponentLoader) {
    hotTableComponentLoader = (async () => {
      const [, { registerAttendanceOrderHandsontableModules }, handsontableVue3] = await Promise.all([
        loadHotTableAssets(),
        import('@/utils/handsontableOrderSetup'),
        import('@handsontable/vue3'),
      ])
      registerAttendanceOrderHandsontableModules()
      return handsontableVue3.HotTable
    })()
  }
  return hotTableComponentLoader
}

const HotTable = defineAsyncComponent({
  loader: loadHotTableComponent,
  suspensible: false,
})

const orderContentWidth = computed(() => Math.max(280, Math.floor(viewportWidth.value || 0)))
const isCompactViewport = computed(() => orderContentWidth.value <= 640)
const tableRowHeaderWidth = computed(() => (isCompactViewport.value ? 36 : 50))
const tableAutoColumnSize = computed(() => !isCompactViewport.value)
const inputTableStretchMode = computed<'none' | 'all'>(() => (isCompactViewport.value ? 'all' : 'none'))
const dataTableStretchMode = computed<'none'>(() => 'none')
const currentExecutionDevice = computed(() => config.value?.current_execution_device ?? null)
const currentOrderLookupMode = computed(() => config.value?.service.order_lookup_mode || 'browser_only')
const inputDraftStorageKey = computed(() => buildScopedStorageKey('input'))
const queryDraftStorageKey = computed(() => buildScopedStorageKey('query'))
const refundDraftStorageKey = computed(() => buildScopedStorageKey('refund'))
const detailDraftStorageKey = computed(() => buildScopedStorageKey('detail', DETAIL_DRAFT_STORAGE_KEY_PREFIX))
const hasQueryRows = computed(() => queryRows.value.some(isMeaningfulQueryRow))
const hasRefundRows = computed(() => refundRows.value.some(isMeaningfulRefundRow))
const hasOrderPageData = computed(() => (
  inputRows.value.some(isMeaningfulInputRow)
  || hasQueryRows.value
  || hasRefundRows.value
))
const hasDetailQueryData = computed(() => Boolean(detailOrderId.value.trim()) || detailSearched.value)
const inputFallbackTableHeight = computed(() => getAdaptiveTableHeight(inputRows.value.length, {
  minVisibleRows: INPUT_TABLE_MIN_VISIBLE_ROWS,
  maxVisibleRows: Number.POSITIVE_INFINITY,
  extraVisibleRows: 1,
}))
const queryFallbackTableHeight = computed(() => getAdaptiveTableHeight(queryRows.value.length, {
  minVisibleRows: QUERY_TABLE_MIN_VISIBLE_ROWS,
  maxVisibleRows: Number.POSITIVE_INFINITY,
}))
const inputTableHeight = computed(() => inputMeasuredHeight.value ?? inputFallbackTableHeight.value)
const queryTableHeight = computed(() => queryMeasuredHeight.value ?? queryFallbackTableHeight.value)
const refundTableHeight = computed(() => getAdaptiveTableHeight(refundRows.value.length))
const editableTableContentWidth = computed(() => Math.max(260, orderContentWidth.value - tableRowHeaderWidth.value - 2))
const inputColWidths = computed<number[] | undefined>(() => {
  if (!isCompactViewport.value) return undefined

  const orderColumnWidth = Math.max(178, Math.floor(editableTableContentWidth.value * 0.66))
  return [orderColumnWidth, Math.max(82, editableTableContentWidth.value - orderColumnWidth)]
})
const queryColWidths = computed<number[] | undefined>(() => {
  if (!isCompactViewport.value) return undefined

  const moneyWidth = orderContentWidth.value <= 430 ? 66 : 76
  return [
    92,
    172,
    142,
    moneyWidth,
    moneyWidth,
    moneyWidth,
    82,
    136,
  ]
})
const refundColWidths = computed<number[] | undefined>(() => {
  if (!isCompactViewport.value) return undefined

  const moneyWidth = orderContentWidth.value <= 430 ? 66 : 76
  return [
    92,
    172,
    142,
    moneyWidth,
    moneyWidth,
    moneyWidth,
    156,
  ]
})

const inputColumns: ColumnSettings[] = [
  { data: '订单号' },
  { data: '学员名称' },
]

const queryColumns: ColumnSettings[] = [
  { data: '学员名称' },
  { data: '微信支付订单号' },
  { data: '商户订单号' },
  { data: '订单金额' },
  { data: '已返款' },
  { data: '剩余金额' },
  { data: '退款额度' },
  { data: '退款原因' },
]

const refundColumns: ColumnSettings[] = [
  { data: '学员名称' },
  { data: '微信支付订单号' },
  { data: '商户订单号' },
  { data: '订单金额' },
  { data: '已返款' },
  { data: '剩余金额' },
  { data: '处理结果' },
]

function createEmptyInputRow(): InputOrderRow {
  return {
    订单号: '',
    学员名称: '',
  }
}

function createEmptyQueryRow(): QueryOrderRow {
  return {
    学员名称: '',
    微信支付订单号: '',
    商户订单号: '',
    订单金额: '',
    已返款: '',
    剩余金额: '',
    退款额度: '',
    退款原因: '',
  }
}

function createEmptyRefundRow(): RefundResultRow {
  return {
    学员名称: '',
    微信支付订单号: '',
    商户订单号: '',
    订单金额: '',
    已返款: '',
    剩余金额: '',
    处理结果: '',
  }
}

function getAdaptiveTableHeight(
  rowCount: number,
  options?: {
    minVisibleRows?: number
    maxVisibleRows?: number
    extraVisibleRows?: number
  },
): number {
  const minVisibleRows = options?.minVisibleRows ?? 1
  const maxVisibleRows = options?.maxVisibleRows ?? HOT_TABLE_MAX_VISIBLE_ROWS
  const extraVisibleRows = options?.extraVisibleRows ?? 0
  const visibleRows = Math.min(
    Math.max(rowCount + extraVisibleRows, minVisibleRows),
    maxVisibleRows,
  )
  return HOT_TABLE_HEADER_HEIGHT + visibleRows * HOT_TABLE_ROW_HEIGHT
}

function resolveInitialViewportWidth() {
  if (typeof window === 'undefined') return 0
  const visualWidth = window.visualViewport?.width ?? window.innerWidth
  return Math.max(0, Math.floor(visualWidth || 0))
}

function normalizeTextValue(value: unknown): string {
  return value == null ? '' : String(value)
}

function normalizeNumberLikeValue(value: unknown): string | number {
  if (value == null || value === '') return ''
  if (typeof value === 'number') return Number.isFinite(value) ? value : ''

  const text = String(value).trim()
  if (!text) return ''

  const numericValue = Number(text)
  return Number.isFinite(numericValue) ? numericValue : text
}

function toFiniteNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const numericValue = typeof value === 'number' ? value : Number(String(value).trim())
  return Number.isFinite(numericValue) ? numericValue : null
}

function resolveDefaultRefundAmount(refundAmount: unknown, remainingAmount: unknown): string | number {
  const normalizedRefundAmount = normalizeNumberLikeValue(refundAmount)
  const normalizedRemainingAmount = normalizeNumberLikeValue(remainingAmount)
  const remainingNumeric = toFiniteNumber(normalizedRemainingAmount)
  if (remainingNumeric == null) {
    return normalizedRefundAmount
  }

  const refundNumeric = toFiniteNumber(normalizedRefundAmount)
  if (normalizedRefundAmount === '' || refundNumeric === 0) {
    return normalizedRemainingAmount
  }

  return normalizedRefundAmount
}

function computeRemainingAmount(orderAmount: unknown, refundedAmount: unknown): string | number {
  const amount = toFiniteNumber(orderAmount)
  const refunded = toFiniteNumber(refundedAmount)
  if (amount == null || refunded == null) return ''
  return Math.max(amount - refunded, 0)
}

function normalizeOrderId(value: unknown): string {
  return String(value ?? '').replace(/^`+/, '').trim()
}

function isWechatFlowOrder(orderId: string): boolean {
  return /^\d{18,}$/.test(orderId)
}

function normalizeInputRow(row?: InputOrderRow | null): InputOrderRow {
  return {
    订单号: normalizeOrderId(row?.订单号),
    学员名称: normalizeTextValue(row?.学员名称).trim(),
  }
}

function normalizeQueryRow(row?: QueryOrderRow | null): QueryOrderRow {
  const normalized = {
    学员名称: normalizeTextValue(row?.学员名称).trim(),
    微信支付订单号: normalizeOrderId(row?.微信支付订单号),
    商户订单号: normalizeOrderId(row?.商户订单号),
    订单金额: normalizeNumberLikeValue(row?.订单金额),
    已返款: normalizeNumberLikeValue(row?.已返款),
    剩余金额: '',
    退款额度: normalizeNumberLikeValue(row?.退款额度),
    退款原因: normalizeTextValue(row?.退款原因).trim(),
  }

  normalized.剩余金额 = computeRemainingAmount(normalized.订单金额, normalized.已返款)
  normalized.退款额度 = resolveDefaultRefundAmount(normalized.退款额度, normalized.剩余金额)
  return normalized
}

function normalizeRefundRow(row?: RefundResultRow | null): RefundResultRow {
  const normalized = {
    学员名称: normalizeTextValue(row?.学员名称).trim(),
    微信支付订单号: normalizeOrderId(row?.微信支付订单号),
    商户订单号: normalizeOrderId(row?.商户订单号),
    订单金额: normalizeNumberLikeValue(row?.订单金额),
    已返款: normalizeNumberLikeValue(row?.已返款),
    剩余金额: '',
    处理结果: normalizeTextValue(row?.处理结果).trim(),
  }

  normalized.剩余金额 = computeRemainingAmount(normalized.订单金额, normalized.已返款)
  return normalized
}

function hasNonEmptyCell(value: unknown): boolean {
  return String(value ?? '').trim() !== ''
}

function isMeaningfulInputRow(row: InputOrderRow): boolean {
  return INPUT_TABLE_COLUMNS.some((column) => hasNonEmptyCell(row[column]))
}

function isMeaningfulQueryRow(row: QueryOrderRow): boolean {
  return QUERY_TABLE_COLUMNS.some((column) => hasNonEmptyCell(row[column]))
}

function isMeaningfulRefundRow(row: RefundResultRow): boolean {
  return REFUND_TABLE_COLUMNS.some((column) => hasNonEmptyCell(row[column]))
}

function normalizeDetailSummaryValue(value: unknown): AttendanceOrderRefundDetailSummary | null {
  if (!value || typeof value !== 'object') return null
  const source = value as Partial<AttendanceOrderRefundDetailSummary>
  return {
    order_id: normalizeOrderId(source.order_id),
    matched_order_id: normalizeOrderId(source.matched_order_id),
    query_type: source.query_type || 'auto',
    row_count: Math.max(0, Number(source.row_count || 0)),
    refund_amount_total: Number(source.refund_amount_total || 0),
    wechat_order_id: normalizeOrderId(source.wechat_order_id),
    merchant_order_id: normalizeOrderId(source.merchant_order_id),
    refund_statuses: Array.isArray(source.refund_statuses)
      ? source.refund_statuses.filter((item): item is string => typeof item === 'string' && !!item.trim())
      : [],
  }
}

function normalizeDetailRowsValue(value: unknown): AttendanceOrderRefundDetailItem[] {
  if (!Array.isArray(value)) return []
  return value
    .filter((item): item is AttendanceOrderRefundDetailItem => !!item && typeof item === 'object')
    .map((item) => ({
      wechat_order_id: normalizeOrderId(item.wechat_order_id),
      merchant_order_id: normalizeOrderId(item.merchant_order_id),
      refund_id: normalizeOrderId(item.refund_id),
      refund_amount: Number(item.refund_amount || 0),
      refund_status: normalizeTextValue(item.refund_status).trim(),
      applicant: normalizeTextValue(item.applicant).trim(),
      submitted_at: normalizeTextValue(item.submitted_at).trim(),
      completed_at: normalizeTextValue(item.completed_at).trim(),
    }))
}

function resolveOrderSubview(value: unknown): OrderSubview {
  return value === 'detail' ? 'detail' : 'refund'
}

function buildScopedStorageKey(scope: string, prefix = ORDER_DRAFT_STORAGE_KEY_PREFIX): string | null {
  const userId = userStore.user?.id
  if (typeof userId === 'number' && Number.isFinite(userId)) {
    return `${prefix}:${scope}:user:${userId}`
  }

  const username = (userStore.user?.username || '').trim()
  if (username) {
    return `${prefix}:${scope}:username:${username}`
  }

  return null
}

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function clearDraftStorageKey(storageKey: string | null) {
  if (!storageKey || !canUseLocalStorage()) return
  window.localStorage.removeItem(storageKey)
}

function getInputHotInstance() {
  return inputHotTableRef.value?.hotInstance ?? null
}

function getQueryHotInstance() {
  return queryHotTableRef.value?.hotInstance ?? null
}

function getRefundHotInstance() {
  return refundHotTableRef.value?.hotInstance ?? null
}

function measureRenderedTableHeight(hot: Handsontable | null, extraVisibleRows = 0): number | null {
  if (!hot) return null

  const rowCount = hot.countRows()
  let totalHeight = HOT_TABLE_HEADER_HEIGHT + 2
  for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
    totalHeight += hot.getRowHeight(rowIndex) ?? HOT_TABLE_ROW_HEIGHT
  }
  totalHeight += extraVisibleRows * HOT_TABLE_ROW_HEIGHT
  return totalHeight
}

function refreshInputTableHeight() {
  requestAnimationFrame(() => {
    inputMeasuredHeight.value = measureRenderedTableHeight(getInputHotInstance(), 1)
  })
}

function refreshQueryTableHeight() {
  requestAnimationFrame(() => {
    queryMeasuredHeight.value = measureRenderedTableHeight(getQueryHotInstance())
  })
}

function updateOrderViewportWidth() {
  if (typeof window === 'undefined') return

  const elementWidth = pageRootRef.value?.clientWidth ?? 0
  const visualWidth = window.visualViewport?.width ?? window.innerWidth
  viewportWidth.value = Math.floor(elementWidth || visualWidth || 0)
}

function refreshOrderTableLayouts() {
  void nextTick(() => {
    requestAnimationFrame(() => {
      getInputHotInstance()?.render()
      getQueryHotInstance()?.render()
      getRefundHotInstance()?.render()
      refreshInputTableHeight()
      refreshQueryTableHeight()
    })
  })
}

function setupOrderViewportObserver() {
  if (typeof window === 'undefined') return

  updateOrderViewportWidth()
  window.addEventListener('resize', updateOrderViewportWidth, { passive: true })
  window.visualViewport?.addEventListener('resize', updateOrderViewportWidth, { passive: true })

  if (typeof ResizeObserver !== 'undefined' && pageRootRef.value) {
    orderResizeObserver = new ResizeObserver(updateOrderViewportWidth)
    orderResizeObserver.observe(pageRootRef.value)
  }
}

function cleanupOrderViewportObserver() {
  if (typeof window === 'undefined') return

  window.removeEventListener('resize', updateOrderViewportWidth)
  window.visualViewport?.removeEventListener('resize', updateOrderViewportWidth)
  orderResizeObserver?.disconnect()
  orderResizeObserver = null
}

function syncInputRowsFromGrid() {
  const hot = getInputHotInstance()
  if (!hot) return inputRows.value

  const sourceRows = hot.getSourceData() as InputOrderRow[]
  inputRows.value = sourceRows.map(normalizeInputRow)
  return inputRows.value
}

function syncQueryRowsFromGrid() {
  const hot = getQueryHotInstance()
  if (!hot) return queryRows.value

  const sourceRows = hot.getSourceData() as QueryOrderRow[]
  queryRows.value = sourceRows.map(normalizeQueryRow)
  return queryRows.value
}

function syncRefundRowsFromGrid() {
  const hot = getRefundHotInstance()
  if (!hot) return refundRows.value

  const sourceRows = hot.getSourceData() as RefundResultRow[]
  refundRows.value = sourceRows.map(normalizeRefundRow)
  return refundRows.value
}

function loadInputRowsToGrid(nextRows: InputOrderRow[]) {
  const normalizedRows = nextRows.length ? nextRows.map(normalizeInputRow) : [createEmptyInputRow()]
  inputRows.value = normalizedRows

  const hot = getInputHotInstance()
  if (hot) {
    hot.loadData(normalizedRows, 'external-update')
    hot.render()
  }
}

function loadQueryRowsToGrid(nextRows: QueryOrderRow[]) {
  const normalizedRows = nextRows.map(normalizeQueryRow)
  queryRows.value = normalizedRows

  const hot = getQueryHotInstance()
  if (hot) {
    hot.loadData(normalizedRows, 'external-update')
    hot.render()
  }
}

function loadRefundRowsToGrid(nextRows: RefundResultRow[]) {
  const normalizedRows = nextRows.map(normalizeRefundRow)
  refundRows.value = normalizedRows

  const hot = getRefundHotInstance()
  if (hot) {
    hot.loadData(normalizedRows, 'external-update')
    hot.render()
  }
}

function commitPendingInputEdit() {
  const hot = getInputHotInstance()
  if (!hot) return

  const editor = (hot as any).getActiveEditor?.()
  editor?.finishEditing?.(false)
  syncInputRowsFromGrid()
}

function commitPendingQueryEdit() {
  const hot = getQueryHotInstance()
  if (!hot) return

  const editor = (hot as any).getActiveEditor?.()
  editor?.finishEditing?.(false)
  syncQueryRowsFromGrid()
}

function persistInputDraftRows() {
  const storageKey = inputDraftStorageKey.value
  if (!storageKey || !canUseLocalStorage()) return

  const normalizedRows = inputRows.value.map(normalizeInputRow)
  if (!normalizedRows.some(isMeaningfulInputRow)) {
    window.localStorage.removeItem(storageKey)
    return
  }

  try {
    window.localStorage.setItem(storageKey, JSON.stringify(normalizedRows))
  } catch (error) {
    console.warn('Failed to persist attendance order input draft', error)
  }
}

function persistQueryDraftRows() {
  const storageKey = queryDraftStorageKey.value
  if (!storageKey || !canUseLocalStorage()) return

  const normalizedRows = queryRows.value.map(normalizeQueryRow)
  if (!normalizedRows.some(isMeaningfulQueryRow)) {
    window.localStorage.removeItem(storageKey)
    return
  }

  try {
    window.localStorage.setItem(storageKey, JSON.stringify(normalizedRows))
  } catch (error) {
    console.warn('Failed to persist attendance order query draft', error)
  }
}

function persistRefundDraftRows() {
  const storageKey = refundDraftStorageKey.value
  if (!storageKey || !canUseLocalStorage()) return

  const normalizedRows = refundRows.value.map(normalizeRefundRow)
  if (!normalizedRows.some(isMeaningfulRefundRow)) {
    window.localStorage.removeItem(storageKey)
    return
  }

  try {
    window.localStorage.setItem(storageKey, JSON.stringify(normalizedRows))
  } catch (error) {
    console.warn('Failed to persist attendance order refund draft', error)
  }
}

function persistDetailDraftValue() {
  const storageKey = detailDraftStorageKey.value
  if (!storageKey || !canUseLocalStorage()) return

  const payload: PersistedDetailDraft = {
    orderId: normalizeOrderId(detailOrderId.value),
    searched: Boolean(detailSearched.value),
    summary: normalizeDetailSummaryValue(detailSummary.value),
    rows: normalizeDetailRowsValue(detailRows.value),
  }

  const hasMeaningfulPayload = Boolean(payload.orderId)
    || payload.searched
    || Boolean(payload.summary)
    || payload.rows.length > 0
  if (!hasMeaningfulPayload) {
    window.localStorage.removeItem(storageKey)
    return
  }

  try {
    window.localStorage.setItem(storageKey, JSON.stringify(payload))
  } catch (error) {
    console.warn('Failed to persist attendance order detail draft', error)
  }
}

function restoreInputDraftRows(storageKey: string | null) {
  if (!storageKey || !canUseLocalStorage()) return
  if (restoredInputDraftStorageKey.value === storageKey) return

  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) {
      loadInputRowsToGrid([createEmptyInputRow()])
      restoredInputDraftStorageKey.value = storageKey
      return
    }

    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      window.localStorage.removeItem(storageKey)
      loadInputRowsToGrid([createEmptyInputRow()])
      restoredInputDraftStorageKey.value = storageKey
      return
    }

    const normalizedRows = parsed
      .filter((item): item is InputOrderRow => !!item && typeof item === "object")
      .map(normalizeInputRow)
    loadInputRowsToGrid(normalizedRows.length ? normalizedRows : [createEmptyInputRow()])
  } catch (error) {
    console.warn('Failed to restore attendance order input draft', error)
    window.localStorage.removeItem(storageKey)
    loadInputRowsToGrid([createEmptyInputRow()])
  } finally {
    restoredInputDraftStorageKey.value = storageKey
  }
}

function restoreQueryDraftRows(storageKey: string | null) {
  if (!storageKey || !canUseLocalStorage()) return
  if (restoredQueryDraftStorageKey.value === storageKey) return

  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) {
      loadQueryRowsToGrid([])
      restoredQueryDraftStorageKey.value = storageKey
      return
    }

    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      window.localStorage.removeItem(storageKey)
      loadQueryRowsToGrid([])
      restoredQueryDraftStorageKey.value = storageKey
      return
    }

    const normalizedRows = parsed
      .filter((item): item is QueryOrderRow => !!item && typeof item === "object")
      .map(normalizeQueryRow)
    loadQueryRowsToGrid(normalizedRows)
  } catch (error) {
    console.warn('Failed to restore attendance order query draft', error)
    window.localStorage.removeItem(storageKey)
    loadQueryRowsToGrid([])
  } finally {
    restoredQueryDraftStorageKey.value = storageKey
  }
}

function restoreRefundDraftRows(storageKey: string | null) {
  if (!storageKey || !canUseLocalStorage()) return
  if (restoredRefundDraftStorageKey.value === storageKey) return

  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) {
      loadRefundRowsToGrid([])
      restoredRefundDraftStorageKey.value = storageKey
      return
    }

    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      window.localStorage.removeItem(storageKey)
      loadRefundRowsToGrid([])
      restoredRefundDraftStorageKey.value = storageKey
      return
    }

    const normalizedRows = parsed
      .filter((item): item is RefundResultRow => !!item && typeof item === "object")
      .map(normalizeRefundRow)
    loadRefundRowsToGrid(normalizedRows)
  } catch (error) {
    console.warn('Failed to restore attendance order refund draft', error)
    window.localStorage.removeItem(storageKey)
    loadRefundRowsToGrid([])
  } finally {
    restoredRefundDraftStorageKey.value = storageKey
  }
}

function restoreDetailDraftValue(storageKey: string | null) {
  if (!storageKey || !canUseLocalStorage()) return
  if (restoredDetailDraftStorageKey.value === storageKey) return

  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) {
      detailOrderId.value = ''
      detailSearched.value = false
      detailSummary.value = null
      detailRows.value = []
      restoredDetailDraftStorageKey.value = storageKey
      return
    }

    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object') {
        throw new Error('invalid detail draft payload')
      }

      detailOrderId.value = normalizeOrderId((parsed as PersistedDetailDraft).orderId)
      detailSearched.value = Boolean((parsed as PersistedDetailDraft).searched)
      detailSummary.value = normalizeDetailSummaryValue((parsed as PersistedDetailDraft).summary)
      detailRows.value = normalizeDetailRowsValue((parsed as PersistedDetailDraft).rows)
      if (detailSummary.value || detailRows.value.length) {
        detailSearched.value = true
      }
    } catch {
      // Backward compatible with the old plain-string storage format.
      detailOrderId.value = normalizeOrderId(raw)
      detailSearched.value = false
      detailSummary.value = null
      detailRows.value = []
    }
  } catch (error) {
    console.warn('Failed to restore attendance order detail draft', error)
    window.localStorage.removeItem(storageKey)
    detailOrderId.value = ''
    detailSearched.value = false
    detailSummary.value = null
    detailRows.value = []
  } finally {
    restoredDetailDraftStorageKey.value = storageKey
  }
}

function mapInspectPayloadRow(row: InputOrderRow): AttendanceOrderRow {
  const orderId = normalizeOrderId(row.订单号)
  const isWechatId = isWechatFlowOrder(orderId)

  return {
    学员名称: row.学员名称,
    微信支付订单号: isWechatId ? orderId : '',
    商户订单号: isWechatId ? '' : orderId,
    订单金额: '',
    已返款: '',
    退款原因: '',
    退款额度: '',
    执行退款: '',
  }
}

function mapQueryRowFromBackend(row: AttendanceOrderRow): QueryOrderRow {
  return normalizeQueryRow({
    学员名称: normalizeTextValue(row.学员名称),
    微信支付订单号: normalizeTextValue(row.微信支付订单号),
    商户订单号: normalizeTextValue(row.商户订单号),
    订单金额: normalizeNumberLikeValue(row.订单金额),
    已返款: normalizeNumberLikeValue(row.已返款),
    剩余金额: '',
    退款额度: normalizeNumberLikeValue(row.退款额度),
    退款原因: normalizeTextValue(row.退款原因),
  })
}

function mapRefundRowFromBackend(row: AttendanceOrderRow): RefundResultRow {
  const fallbackResult = typeof row.订单金额 === 'string' && toFiniteNumber(row.订单金额) == null
    ? row.订单金额
    : ''

  return normalizeRefundRow({
    学员名称: normalizeTextValue(row.学员名称),
    微信支付订单号: normalizeTextValue(row.微信支付订单号),
    商户订单号: normalizeTextValue(row.商户订单号),
    订单金额: normalizeNumberLikeValue(row.订单金额),
    已返款: normalizeNumberLikeValue(row.已返款),
    剩余金额: '',
    处理结果: normalizeTextValue(row.执行退款 || fallbackResult),
  })
}

function mapRefundPayloadRow(row: QueryOrderRow): AttendanceOrderRow {
  return {
    学员名称: row.学员名称,
    微信支付订单号: normalizeOrderId(row.微信支付订单号),
    商户订单号: normalizeOrderId(row.商户订单号),
    订单金额: row.订单金额,
    已返款: row.已返款,
    退款原因: row.退款原因,
    退款额度: row.退款额度,
    执行退款: '',
  }
}

function validateRefundRows(rows: QueryOrderRow[]): string | null {
  for (const row of rows) {
    const orderAmount = toFiniteNumber(row.订单金额)
    const refundedAmount = toFiniteNumber(row.已返款)
    if (orderAmount == null || refundedAmount == null) {
      return '查询结果里有未成功识别的订单，不能直接执行退款'
    }

    const remainingAmount = Math.max(orderAmount - refundedAmount, 0)
    if (row.退款额度 !== '') {
      const refundAmount = toFiniteNumber(row.退款额度)
      if (refundAmount == null) {
        return '退款额度必须是数字'
      }
    }
  }

  return null
}

function queryCells(_row: number, _column: number, prop: string | number): CellProperties {
  const cellProperties: CellProperties = {}
  if (QUERY_READONLY_COLUMNS.has(String(prop))) {
    cellProperties.readOnly = true
    cellProperties.className = 'htDimmed hot-readonly-cell'
  }
  return cellProperties
}

function refundCells(_row: number, _column: number, prop: string | number): CellProperties {
  const cellProperties: CellProperties = {}
  if (REFUND_READONLY_COLUMNS.has(String(prop))) {
    cellProperties.readOnly = true
    cellProperties.className = 'htDimmed hot-readonly-cell'
  }
  return cellProperties
}

function handleInputAfterChange(_changes: unknown, source?: string) {
  if (source === 'loadData' || source === 'external-update') return
  syncInputRowsFromGrid()
}

function handleInputAfterRender() {
  refreshInputTableHeight()
}

function handleInputAfterCreateRow() {
  syncInputRowsFromGrid()
  refreshInputTableHeight()
}

function handleInputAfterRemoveRow() {
  syncInputRowsFromGrid()
  if (!inputRows.value.length) {
    loadInputRowsToGrid([createEmptyInputRow()])
  }
  refreshInputTableHeight()
}

function handleQueryAfterChange(_changes: unknown, source?: string) {
  if (source === 'loadData' || source === 'external-update') return
  syncQueryRowsFromGrid()
}

function handleQueryAfterRender() {
  refreshQueryTableHeight()
}

function handleRefundAfterChange(_changes: unknown, source?: string) {
  if (source === 'loadData' || source === 'external-update') return
  syncRefundRowsFromGrid()
}

async function loadPageData() {
  loading.value = true
  try {
    const configData = await fetchAttendanceConfig()
    config.value = configData
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '加载订单页失败')
  } finally {
    loading.value = false
  }
  if (activeSubview.value === 'refund') {
    scheduleRefundHistoryLoad()
  }
}

async function ensureRefundHistoryLoaded() {
  if (refundHistoryLoaded.value) return
  if (refundHistoryLoadPromise) {
    await refundHistoryLoadPromise
    return
  }
  refundHistoryLoadPromise = loadRefundHistory(refundHistoryPage.value, refundHistoryPageSize.value)
  try {
    await refundHistoryLoadPromise
  } finally {
    refundHistoryLoadPromise = null
  }
}

function clearRefundHistoryDeferredTimer() {
  if (refundHistoryDeferredTimer !== null) {
    window.clearTimeout(refundHistoryDeferredTimer)
    refundHistoryDeferredTimer = null
  }
  refundHistoryPending.value = false
}

function scheduleRefundHistoryLoad(delayMs = 160) {
  if (refundHistoryLoaded.value || refundHistoryLoading.value || refundHistoryLoadPromise || refundHistoryDeferredTimer !== null) {
    return
  }
  refundHistoryPending.value = true
  refundHistoryDeferredTimer = window.setTimeout(() => {
    refundHistoryDeferredTimer = null
    if (activeSubview.value === 'refund') {
      void ensureRefundHistoryLoaded()
    } else {
      refundHistoryPending.value = false
    }
  }, delayMs)
}

async function loadRefundHistory(page = refundHistoryPage.value, pageSize = refundHistoryPageSize.value) {
  refundHistoryPending.value = false
  refundHistoryLoading.value = true
  try {
    const result = await fetchAttendanceOrderRefundHistory({
      page,
      page_size: pageSize,
    })
    refundHistoryItems.value = result.items || []
    refundHistoryTotal.value = result.total || 0
    refundHistoryPage.value = result.page || page
    refundHistoryPageSize.value = result.page_size || pageSize
    refundHistoryLoaded.value = true
  } catch (error: any) {
    refundHistoryLoaded.value = false
    ElMessage.error(error.response?.data?.detail || '加载退款历史失败')
  } finally {
    refundHistoryLoading.value = false
  }
}

function handleRefundHistoryPageChange(page: number) {
  void loadRefundHistory(page, refundHistoryPageSize.value)
}

function handleRefundHistoryPageSizeChange(pageSize: number) {
  void loadRefundHistory(1, pageSize)
}

function formatRefundHistoryCreatedAt(timestamp: number) {
  return formatNoteDateTimeDetailed(timestamp)
}

function getForegroundStyle(color?: string | null) {
  if (!color) {
    return undefined
  }
  return {
    color,
  }
}

function resolveUiErrorMessage(error: any, fallback: string) {
  return error?.response?.data?.detail || error?.message || fallback
}

function formatMoney(value: number | string | null | undefined) {
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric)) return '-'
  return numeric.toFixed(2)
}

function switchOrderSubview(view: OrderSubview) {
  activeSubview.value = view
}

async function confirmRefundExecution(rowCount: number): Promise<boolean> {
  const confirmationSteps = [
    {
      title: '第一次确认',
      message: `将对 ${rowCount} 条订单发起真实退款。退款会直接作用到真实订单，请先核对订单号、退款额度和退款原因。`,
      confirmButtonText: '继续确认',
    },
    {
      title: '第二次确认',
      message: '最后确认：点击后会立即开始执行退款，当前页面无法撤销。只有在你已经确认无误时才继续。',
      confirmButtonText: '确定执行',
    },
  ] as const

  for (const step of confirmationSteps) {
    try {
      await ElMessageBox.confirm(step.message, step.title, {
        type: 'warning',
        showCancelButton: true,
        confirmButtonText: step.confirmButtonText,
        cancelButtonText: '取消',
        confirmButtonClass: 'refund-safety-confirm-button',
        cancelButtonClass: 'refund-safety-cancel-button',
        customClass: 'refund-safety-dialog',
        closeOnClickModal: false,
        closeOnPressEscape: false,
        showClose: false,
        autofocus: false,
      })
    } catch {
      return false
    }
  }

  return true
}

async function queryOrders() {
  if (!currentExecutionDevice.value?.entry_id) {
    ElMessage.warning('请先去考勤配置里设置全局执行设备')
    return
  }

  commitPendingInputEdit()
  const candidateRows = syncInputRowsFromGrid().map(normalizeInputRow).filter(isMeaningfulInputRow)
  if (!candidateRows.length) {
    ElMessage.warning('请先录入至少一行订单')
    return
  }

  if (candidateRows.some((row) => !normalizeOrderId(row.订单号))) {
    ElMessage.warning('查询输入里有未填写订单号的行')
    return
  }

  querying.value = true
  try {
    const result = await executeAttendanceOrder({
      action: 'inspect',
      rows: candidateRows.map(mapInspectPayloadRow),
      order_lookup_mode: currentOrderLookupMode.value,
    })
    loadQueryRowsToGrid(result.rows.map(mapQueryRowFromBackend))
    loadRefundRowsToGrid([])
    refundHistoryPage.value = 1
    await loadRefundHistory(1, refundHistoryPageSize.value)
    ElMessage.success('订单状态已刷新')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '订单查询失败')
  } finally {
    querying.value = false
  }
}

async function clearOrderPageData() {
  if (!hasOrderPageData.value) return

  try {
    await ElMessageBox.confirm('清空当前查询输入、查询结果和退款结果？退款历史不会删除。', '清空数据', {
      type: 'warning',
      confirmButtonText: '清空',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  commitPendingInputEdit()
  commitPendingQueryEdit()
  clearDraftStorageKey(inputDraftStorageKey.value)
  clearDraftStorageKey(queryDraftStorageKey.value)
  clearDraftStorageKey(refundDraftStorageKey.value)
  loadInputRowsToGrid([createEmptyInputRow()])
  loadQueryRowsToGrid([])
  loadRefundRowsToGrid([])
  ElMessage.success('当前订单页数据已清空')
}

async function queryOrderDetails() {
  if (!currentExecutionDevice.value?.entry_id) {
    ElMessage.warning('请先去考勤配置里设置全局执行设备')
    return
  }

  const orderId = normalizeOrderId(detailOrderId.value)
  if (!orderId) {
    ElMessage.warning('请输入订单号')
    return
  }

  detailQuerying.value = true
  try {
    const result = await fetchAttendanceOrderRefundDetails({
      order_id: orderId,
      execution_device_entry_id: currentExecutionDevice.value.entry_id,
    })
    detailOrderId.value = orderId
    detailSummary.value = result.summary
    detailRows.value = result.rows
    detailSearched.value = true

    if (result.rows.length) {
      ElMessage.success(`已查询到 ${result.rows.length} 笔退款记录`)
    } else {
      ElMessage.info('未查到退款记录')
    }
  } catch (error: any) {
    console.error('Attendance order refund detail query failed', error)
    ElMessage.error(resolveUiErrorMessage(error, '退款详情查询失败'))
  } finally {
    detailQuerying.value = false
  }
}

async function clearOrderDetailData() {
  if (!hasDetailQueryData.value) return

  try {
    await ElMessageBox.confirm('清空当前详情查询输入和查询结果？', '清空数据', {
      type: 'warning',
      confirmButtonText: '清空',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  clearDraftStorageKey(detailDraftStorageKey.value)
  detailOrderId.value = ''
  detailSearched.value = false
  detailSummary.value = null
  detailRows.value = []
  ElMessage.success('详情查询数据已清空')
}

async function refundOrders() {
  if (!currentExecutionDevice.value?.entry_id) {
    ElMessage.warning('请先去考勤配置里设置全局执行设备')
    return
  }

  commitPendingQueryEdit()
  const candidateRows = syncQueryRowsFromGrid().map(normalizeQueryRow).filter(isMeaningfulQueryRow)
  if (!candidateRows.length) {
    ElMessage.warning('请先查询订单，再执行退款')
    return
  }

  const validationError = validateRefundRows(candidateRows)
  if (validationError) {
    ElMessage.warning(validationError)
    return
  }

  const confirmed = await confirmRefundExecution(candidateRows.length)
  if (!confirmed) {
    return
  }

  refunding.value = true
  refundErrorMessage.value = ''
  try {
    const result = await executeAttendanceOrder({
      action: 'refund',
      rows: candidateRows.map(mapRefundPayloadRow),
      order_lookup_mode: currentOrderLookupMode.value,
    })
    loadQueryRowsToGrid(result.rows.map(mapQueryRowFromBackend))
    loadRefundRowsToGrid(result.rows.map(mapRefundRowFromBackend))
    refundHistoryPage.value = 1
    await loadRefundHistory(1, refundHistoryPageSize.value)
    refundErrorMessage.value = ''
    ElMessage.success('退款流程已执行')
  } catch (error: any) {
    const detail = error.response?.data?.detail || '退款任务执行失败'
    refundErrorMessage.value = detail
    ElMessage.error(detail)
  } finally {
    refunding.value = false
  }
}

watch(
  inputRows,
  () => {
    persistInputDraftRows()
  },
  { deep: true }
)

watch(
  queryRows,
  () => {
    persistQueryDraftRows()
  },
  { deep: true }
)

watch(
  refundRows,
  () => {
    persistRefundDraftRows()
  },
  { deep: true }
)

watch([detailOrderId, detailSearched, detailSummary, detailRows], () => {
  persistDetailDraftValue()
}, { deep: true })

watch(
  inputDraftStorageKey,
  (storageKey) => {
    if (!storageKey) return
    restoreInputDraftRows(storageKey)
  },
  { immediate: true }
)

watch(
  queryDraftStorageKey,
  (storageKey) => {
    if (!storageKey) return
    restoreQueryDraftRows(storageKey)
  },
  { immediate: true }
)

watch(
  refundDraftStorageKey,
  (storageKey) => {
    if (!storageKey) return
    restoreRefundDraftRows(storageKey)
  },
  { immediate: true }
)

watch(
  detailDraftStorageKey,
  (storageKey) => {
    if (!storageKey) return
    restoreDetailDraftValue(storageKey)
  },
  { immediate: true }
)

watch(
  () => route.query.tab,
  (tab) => {
    const nextView = resolveOrderSubview(tab)
    if (activeSubview.value !== nextView) {
      activeSubview.value = nextView
    }
  }
)

watch(activeSubview, (view) => {
  const nextTab = view === 'detail' ? 'detail' : undefined
  const currentTab = typeof route.query.tab === 'string' ? route.query.tab : undefined
  if (currentTab === nextTab) return

  const nextQuery = { ...route.query }
  if (nextTab) {
    nextQuery.tab = nextTab
  } else {
    delete nextQuery.tab
  }
  void router.replace({ query: nextQuery })
  if (view === 'refund') {
    scheduleRefundHistoryLoad()
  } else {
    clearRefundHistoryDeferredTimer()
  }
})

watch([orderContentWidth, isCompactViewport], refreshOrderTableLayouts)

onMounted(() => {
  setupOrderViewportObserver()
  if (userStore.isAuthenticated && !userStore.user && !userStore.loading) {
    void userStore.fetchUserProfile()
  }
  void loadPageData()
})

onBeforeUnmount(() => {
  clearRefundHistoryDeferredTimer()
  cleanupOrderViewportObserver()
})
</script>

<template>
  <div ref="pageRootRef" class="attendance-page">
    <header class="page-header">
      <div class="page-switcher" role="tablist" aria-label="订单页子页切换">
        <button
          type="button"
          class="page-switcher__item"
          role="tab"
          :aria-selected="activeSubview === 'refund'"
          :class="{ 'is-active': activeSubview === 'refund' }"
          @click="switchOrderSubview('refund')"
        >
          退款
        </button>
        <button
          type="button"
          class="page-switcher__item"
          role="tab"
          :aria-selected="activeSubview === 'detail'"
          :class="{ 'is-active': activeSubview === 'detail' }"
          @click="switchOrderSubview('detail')"
        >
          详情
        </button>
      </div>
    </header>

    <div v-if="activeSubview === 'refund'" class="orders-layout">
      <section class="panel-card">
        <div class="panel-header">
          <h2>查询输入</h2>
        </div>

        <div class="sheet-frame">
          <HotTable
            ref="inputHotTableRef"
            :data="inputRows"
            :language="'zh-CN'"
            :columns="inputColumns"
            :col-headers="INPUT_TABLE_HEADERS"
            :col-widths="inputColWidths"
            :column-header-height="HOT_TABLE_HEADER_HEIGHT"
            :row-headers="true"
            :row-header-width="tableRowHeaderWidth"
            :manual-column-resize="true"
            :auto-column-size="tableAutoColumnSize"
            :manual-row-resize="true"
            :copy-paste="true"
            :context-menu="true"
            :auto-row-size="true"
            :min-spare-rows="1"
            :render-all-rows="true"
            :stretch-h="inputTableStretchMode"
            :height="inputTableHeight"
            :auto-wrap-row="true"
            :auto-wrap-col="true"
            :selection-mode="'multiple'"
            :outside-click-deselects="false"
            :theme-name="'ht-theme-main'"
            :license-key="'non-commercial-and-evaluation'"
            :after-change="handleInputAfterChange"
            :after-create-row="handleInputAfterCreateRow"
            :after-remove-row="handleInputAfterRemoveRow"
            :after-render="handleInputAfterRender"
          />
        </div>

        <div class="section-action-row">
          <el-button :icon="Delete" :disabled="!hasOrderPageData" @click="clearOrderPageData">
            清空数据
          </el-button>
          <el-button type="primary" :icon="Search" :loading="loading || querying" @click="queryOrders">
            查询已返款
          </el-button>
        </div>
      </section>

      <section v-if="hasQueryRows" class="panel-card">
        <div class="panel-header">
          <h2>查询结果</h2>
        </div>

        <div class="sheet-frame">
          <HotTable
            ref="queryHotTableRef"
            :data="queryRows"
            :language="'zh-CN'"
            :columns="queryColumns"
            :col-headers="QUERY_TABLE_HEADERS"
            :col-widths="queryColWidths"
            :column-header-height="HOT_TABLE_HEADER_HEIGHT"
            :row-headers="true"
            :row-header-width="tableRowHeaderWidth"
            :manual-column-resize="true"
            :auto-column-size="tableAutoColumnSize"
            :manual-row-resize="true"
            :copy-paste="true"
            :context-menu="true"
            :auto-row-size="true"
            :min-spare-rows="0"
            :render-all-rows="true"
            :stretch-h="dataTableStretchMode"
            :height="queryTableHeight"
            :auto-wrap-row="true"
            :auto-wrap-col="true"
            :selection-mode="'multiple'"
            :outside-click-deselects="false"
            :theme-name="'ht-theme-main'"
            :license-key="'non-commercial-and-evaluation'"
            :cells="queryCells"
            :after-change="handleQueryAfterChange"
            :after-render="handleQueryAfterRender"
          />
        </div>

        <div class="section-action-row">
          <el-button type="danger" :icon="VideoPlay" :loading="refunding" @click="refundOrders">执行退款</el-button>
        </div>

        <el-alert
          v-if="refundErrorMessage"
          class="refund-error-alert"
          type="error"
          :title="refundErrorMessage"
          show-icon
          closable
          @close="refundErrorMessage = ''"
        />
      </section>

      <section v-if="hasRefundRows" class="panel-card">
        <div class="panel-header">
          <h2>退款结果</h2>
        </div>

        <div class="sheet-frame">
          <HotTable
            ref="refundHotTableRef"
            :data="refundRows"
            :language="'zh-CN'"
            :columns="refundColumns"
            :col-headers="REFUND_TABLE_HEADERS"
            :col-widths="refundColWidths"
            :column-header-height="HOT_TABLE_HEADER_HEIGHT"
            :row-headers="true"
            :row-header-width="tableRowHeaderWidth"
            :manual-column-resize="true"
            :auto-column-size="tableAutoColumnSize"
            :manual-row-resize="true"
            :copy-paste="true"
            :context-menu="true"
            :min-spare-rows="0"
            :stretch-h="dataTableStretchMode"
            :height="refundTableHeight"
            :auto-wrap-row="true"
            :auto-wrap-col="true"
            :selection-mode="'multiple'"
            :outside-click-deselects="false"
            :theme-name="'ht-theme-main'"
            :license-key="'non-commercial-and-evaluation'"
            :cells="refundCells"
            :after-change="handleRefundAfterChange"
          />
        </div>
      </section>

      <section class="panel-card">
        <div class="panel-header">
          <h2>退款历史</h2>
          <span v-if="refundHistoryLoading || refundHistoryPending" class="panel-header__status" aria-live="polite">
            {{ refundHistoryLoaded ? '刷新中...' : '加载中...' }}
          </span>
        </div>

        <div v-if="refundHistoryLoaded || refundHistoryLoading" class="history-table-shell">
          <el-table
            v-if="!isCompactViewport"
            :data="refundHistoryItems"
            row-key="id"
            table-layout="fixed"
            empty-text="暂无退款历史"
            class="history-table desktop-history-table"
          >
            <el-table-column label="操作时间" min-width="150">
              <template #default="{ row }">
                <div :style="getForegroundStyle(row.foreground_colors?.created_day)">
                  {{ formatRefundHistoryCreatedAt(row.created_at) }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="操作人" min-width="120">
              <template #default="{ row }">
                <div :style="getForegroundStyle(row.foreground_colors?.operator)">{{ row.operator_name || '-' }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="student_name" label="学员名称" min-width="120" />
            <el-table-column prop="wechat_order_id" label="微信支付订单号" min-width="180" />
            <el-table-column prop="merchant_order_id" label="商户订单号" min-width="180" />
            <el-table-column prop="order_amount" label="订单金额" min-width="96" />
            <el-table-column prop="refunded_amount" label="已返款" min-width="96" />
            <el-table-column prop="remaining_amount" label="剩余金额" min-width="96" />
            <el-table-column prop="refund_amount" label="退款额度" min-width="96" />
            <el-table-column prop="refund_reason" label="退款原因" min-width="180" show-overflow-tooltip />
            <el-table-column prop="result_text" label="处理结果" min-width="180" show-overflow-tooltip />
          </el-table>

          <div v-else class="mobile-history-list" aria-live="polite">
            <article v-for="row in refundHistoryItems" :key="row.id" class="mobile-history-item">
              <div class="mobile-history-item__head">
                <div class="mobile-history-item__title">
                  <strong>{{ row.student_name || '-' }}</strong>
                  <span :style="getForegroundStyle(row.foreground_colors?.created_day)">
                    {{ formatRefundHistoryCreatedAt(row.created_at) }}
                  </span>
                </div>
                <strong class="mobile-history-item__amount">{{ formatMoney(row.refund_amount) }}</strong>
              </div>

              <dl class="mobile-history-item__details">
                <div>
                  <dt>操作人</dt>
                  <dd :style="getForegroundStyle(row.foreground_colors?.operator)">{{ row.operator_name || '-' }}</dd>
                </div>
                <div>
                  <dt>订单号</dt>
                  <dd>{{ row.wechat_order_id || row.merchant_order_id || '-' }}</dd>
                </div>
                <div>
                  <dt>退款原因</dt>
                  <dd>{{ row.refund_reason || '-' }}</dd>
                </div>
                <div>
                  <dt>处理结果</dt>
                  <dd>{{ row.result_text || '-' }}</dd>
                </div>
              </dl>
            </article>
            <div v-if="!refundHistoryItems.length" class="mobile-history-empty">暂无退款历史</div>
          </div>
        </div>
        <div v-else-if="refundHistoryPending" class="history-table-shell history-table-shell--pending" aria-hidden="true"></div>

        <div v-if="refundHistoryLoaded && refundHistoryTotal > 0" class="history-pagination-row">
          <StandardPagination
            :page="refundHistoryPage"
            :page-size="refundHistoryPageSize"
            :total="refundHistoryTotal"
            :page-size-options="REFUND_HISTORY_PAGE_SIZE_OPTIONS"
            @page-change="handleRefundHistoryPageChange"
            @page-size-change="handleRefundHistoryPageSizeChange"
          />
        </div>
      </section>
    </div>

    <div v-else class="detail-layout">
      <section class="panel-card">
        <div class="panel-header panel-header--tight">
          <h2>退款详情</h2>
        </div>

        <div class="detail-search-row">
          <label class="detail-search-field">
            <span class="detail-search-label">订单号</span>
            <el-input
              v-model="detailOrderId"
              clearable
              placeholder="支持微信支付单号、商户单号、退款单号"
              @keyup.enter="queryOrderDetails"
            />
          </label>

          <div class="detail-action-group">
            <el-button :icon="Delete" :disabled="!hasDetailQueryData" @click="clearOrderDetailData">
              清空数据
            </el-button>
            <el-button type="primary" :icon="Search" :loading="detailQuerying" @click="queryOrderDetails">
              查询详情
            </el-button>
          </div>
        </div>
      </section>

      <section v-if="detailSummary" class="panel-card">
        <div class="panel-header">
          <h2>查询摘要</h2>
        </div>

        <div class="detail-summary-grid">
          <article class="summary-metric">
            <span class="summary-metric__label">退款笔数</span>
            <strong class="summary-metric__value">{{ detailSummary.row_count }}</strong>
          </article>
          <article class="summary-metric">
            <span class="summary-metric__label">退款总额</span>
            <strong class="summary-metric__value">{{ formatMoney(detailSummary.refund_amount_total) }}</strong>
          </article>
        </div>
      </section>

      <section v-if="detailSearched" class="panel-card">
        <div class="panel-header">
          <h2>退款明细</h2>
        </div>

        <div class="history-table-shell">
          <el-table
            :data="detailRows"
            row-key="refund_id"
            table-layout="auto"
            :empty-text="detailRows.length ? '暂无退款记录' : '未查到退款记录'"
            class="history-table desktop-history-table"
          >
            <el-table-column prop="completed_at" label="退款完成时间" min-width="170" />
            <el-table-column prop="refund_id" label="退款单号" min-width="190" />
            <el-table-column label="退款金额" min-width="96">
              <template #default="{ row }">
                {{ formatMoney(row.refund_amount) }}
              </template>
            </el-table-column>
            <el-table-column prop="refund_status" label="退款状态" min-width="110" />
            <el-table-column prop="applicant" label="申请人" min-width="120" />
          </el-table>

          <div class="mobile-history-list" aria-live="polite">
            <article v-for="row in detailRows" :key="row.refund_id" class="mobile-history-item">
              <div class="mobile-history-item__head">
                <div class="mobile-history-item__title">
                  <strong>{{ formatMoney(row.refund_amount) }}</strong>
                  <span>{{ row.completed_at || '-' }}</span>
                </div>
                <strong class="mobile-history-item__amount">{{ row.refund_status || '-' }}</strong>
              </div>

              <dl class="mobile-history-item__details">
                <div>
                  <dt>退款单号</dt>
                  <dd>{{ row.refund_id || '-' }}</dd>
                </div>
                <div>
                  <dt>申请人</dt>
                  <dd>{{ row.applicant || '-' }}</dd>
                </div>
              </dl>
            </article>
            <div v-if="!detailRows.length" class="mobile-history-empty">未查到退款记录</div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.attendance-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}

.page-switcher {
  display: inline-flex;
  align-items: center;
  gap: 24px;
  border-bottom: 1px solid rgba(133, 100, 59, 0.16);
}

.page-switcher__item {
  position: relative;
  border: 0;
  background: transparent;
  color: #7a5d38;
  font-size: 15px;
  font-weight: 600;
  padding: 4px 0 10px;
  border-radius: 0;
  cursor: pointer;
  transition: color 0.18s ease;
}

.page-switcher__item::after {
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  border-radius: 999px;
  background: #cb8f34;
  content: '';
  transform: scaleX(0);
  transform-origin: center;
  transition: transform 0.18s ease;
}

.page-switcher__item.is-active {
  color: #322719;
}

.page-switcher__item.is-active::after {
  transform: scaleX(1);
}

.page-switcher__item:hover {
  color: #4f3c25;
}

.page-switcher__item:focus-visible {
  outline: 2px solid rgba(203, 143, 52, 0.28);
  outline-offset: 4px;
}

.orders-layout {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
}

.detail-layout {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
}

.panel-card {
  padding: 24px;
  border-radius: 22px;
  background: #fffaf2;
  border: 1px solid rgba(122, 93, 56, 0.14);
  box-shadow: 0 12px 28px rgba(68, 48, 26, 0.08);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.panel-header h2 {
  margin: 0;
  font-size: 22px;
  color: #322719;
}

.panel-header__status {
  color: #9d7b49;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.panel-header--tight {
  margin-bottom: 0;
}

.section-action-row {
  display: flex;
  justify-content: flex-start;
  margin-top: 14px;
}

.detail-search-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  margin-top: 18px;
  align-items: end;
}

.detail-search-field {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-search-label {
  color: #7a5d38;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.detail-action-group {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.detail-summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.summary-metric {
  padding: 16px 18px;
  border-radius: 18px;
  border: 1px solid rgba(133, 100, 59, 0.14);
  background: linear-gradient(145deg, rgba(255, 253, 248, 0.98), rgba(247, 238, 220, 0.92));
}

.summary-metric__label {
  display: block;
  font-size: 12px;
  color: #8b704a;
  margin-bottom: 10px;
}

.summary-metric__value {
  display: block;
  font-size: 22px;
  color: #322719;
  line-height: 1.35;
}

.sheet-frame {
  width: 100%;
  min-width: 0;
  overflow-x: auto;
  overflow-y: visible;
  border-radius: 18px;
  border: 1px solid rgba(133, 100, 59, 0.14);
  background: rgba(255, 253, 248, 0.95);
  -webkit-overflow-scrolling: touch;
}

.sheet-frame :deep(.handsontable) {
  font-size: 14px;
}

.sheet-frame :deep(.htCore td),
.sheet-frame :deep(.htCore th) {
  vertical-align: middle;
}

.sheet-frame :deep(.htCore thead th) {
  height: 42px;
  padding: 0 4px;
  white-space: normal;
}

.sheet-frame :deep(.htCore thead th .relative) {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 42px;
}

.sheet-frame :deep(.htCore thead th .colHeader) {
  display: block;
  max-width: 100%;
  color: #4f3c25;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.25;
  overflow: visible;
  text-align: center;
  text-overflow: clip;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.sheet-frame :deep(td.hot-readonly-cell) {
  color: #6a5536;
  background: rgba(219, 194, 146, 0.1);
}

.history-table-shell {
  overflow-x: auto;
  overflow-y: visible;
  border-radius: 18px;
  border: 1px solid rgba(133, 100, 59, 0.14);
  background: rgba(255, 253, 248, 0.95);
  -webkit-overflow-scrolling: touch;
}

.history-table-shell--pending {
  min-height: 88px;
}

.mobile-history-list {
  display: none;
}

.history-pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.history-table-shell :deep(.el-table) {
  --el-table-border-color: rgba(133, 100, 59, 0.14);
  --el-table-header-bg-color: rgba(250, 245, 236, 0.92);
  --el-table-row-hover-bg-color: rgba(243, 232, 208, 0.28);
  border-radius: 18px;
}

@media (max-width: 960px) {
  .detail-search-row {
    grid-template-columns: 1fr;
  }

  .detail-action-group {
    justify-content: flex-start;
  }

  .detail-summary-grid {
    grid-template-columns: 1fr;
  }

  .history-pagination-row {
    justify-content: flex-start;
    overflow: auto;
  }
}

@media (max-width: 640px) {
  .attendance-page {
    gap: 14px;
    padding: 0 max(8px, env(safe-area-inset-right)) 16px max(8px, env(safe-area-inset-left));
  }

  .page-header {
    gap: 10px;
  }

  .page-switcher {
    gap: 20px;
  }

  .page-switcher__item {
    padding-bottom: 9px;
  }

  .orders-layout,
  .detail-layout {
    gap: 12px;
  }

  .panel-card {
    padding: 12px;
    border-radius: 16px;
    box-shadow: 0 8px 18px rgba(68, 48, 26, 0.06);
  }

  .panel-header {
    margin-bottom: 10px;
  }

  .panel-header h2 {
    font-size: 20px;
  }

  .panel-header__status {
    font-size: 12px;
  }

  .section-action-row,
  .detail-action-group {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin-top: 10px;
  }

  .section-action-row :deep(.el-button),
  .detail-action-group :deep(.el-button) {
    width: 100%;
    min-height: 38px;
    margin: 0;
    padding: 8px 10px;
  }

  .detail-search-row {
    gap: 10px;
    margin-top: 12px;
  }

  .summary-metric {
    padding: 12px 14px;
    border-radius: 14px;
  }

  .summary-metric__value {
    font-size: 18px;
    word-break: break-all;
  }

  .sheet-frame {
    border-radius: 12px;
  }

  .sheet-frame :deep(.handsontable) {
    font-size: 13px;
  }

  .sheet-frame :deep(.htCore td),
  .sheet-frame :deep(.htCore tbody th) {
    height: 26px;
    padding: 0 4px;
    line-height: 1.25;
  }

  .sheet-frame :deep(.htCore thead th),
  .sheet-frame :deep(.htCore thead th .relative) {
    height: 42px;
  }

  .sheet-frame :deep(.htCore td) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .sheet-frame :deep(.htCore th) {
    white-space: normal;
  }

  .history-table-shell {
    overflow: visible;
    border-radius: 12px;
  }

  .desktop-history-table {
    display: none;
  }

  .mobile-history-list {
    display: flex;
    flex-direction: column;
  }

  .mobile-history-item {
    padding: 12px;
    border-bottom: 1px solid rgba(133, 100, 59, 0.12);
  }

  .mobile-history-item:last-child {
    border-bottom: 0;
  }

  .mobile-history-item__head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: flex-start;
    margin-bottom: 10px;
  }

  .mobile-history-item__title {
    display: flex;
    min-width: 0;
    flex-direction: column;
    gap: 4px;
  }

  .mobile-history-item__title strong {
    color: #322719;
    line-height: 1.4;
  }

  .mobile-history-item__title span {
    color: #8b704a;
    font-size: 12px;
  }

  .mobile-history-item__amount {
    flex: 0 0 auto;
    color: #6b4d24;
    font-size: 16px;
    line-height: 1.4;
    text-align: right;
  }

  .mobile-history-item__details {
    display: grid;
    grid-template-columns: 1fr;
    gap: 8px;
    margin: 0;
  }

  .mobile-history-item__details div {
    display: grid;
    grid-template-columns: 68px minmax(0, 1fr);
    gap: 10px;
    align-items: start;
  }

  .mobile-history-item__details dt {
    color: #8b704a;
    font-size: 12px;
  }

  .mobile-history-item__details dd {
    margin: 0;
    min-width: 0;
    color: #3f321f;
    line-height: 1.45;
    word-break: break-all;
  }

  .mobile-history-empty {
    padding: 28px 12px;
    color: #9b8a72;
    text-align: center;
  }

  .history-pagination-row {
    margin-top: 10px;
  }
}

:global(.refund-safety-dialog) {
  border-radius: 18px;
  border: 1px solid rgba(133, 100, 59, 0.16);
}

:global(.refund-safety-dialog .el-message-box__title) {
  color: #322719;
}

:global(.refund-safety-dialog .el-message-box__message) {
  color: #5f4a30;
  line-height: 1.7;
}

:global(.refund-safety-confirm-button.el-button) {
  background: #fffaf2;
  border-color: rgba(133, 100, 59, 0.24);
  color: #6a5536;
  box-shadow: none;
}

:global(.refund-safety-confirm-button.el-button:hover) {
  background: #f7efe0;
  border-color: rgba(133, 100, 59, 0.34);
  color: #4f3c25;
}

:global(.refund-safety-confirm-button.el-button:focus-visible) {
  outline: 2px solid rgba(133, 100, 59, 0.28);
  outline-offset: 2px;
}

:global(.refund-safety-cancel-button.el-button) {
  background: #cb8f34;
  border-color: #cb8f34;
  color: #fffaf2;
  box-shadow: 0 10px 18px rgba(203, 143, 52, 0.22);
}

:global(.refund-safety-cancel-button.el-button:hover) {
  background: #b97e26;
  border-color: #b97e26;
  color: #fffdf8;
}

:global(.refund-safety-cancel-button.el-button:focus-visible) {
  outline: 2px solid rgba(203, 143, 52, 0.35);
  outline-offset: 2px;
}
</style>

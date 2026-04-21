<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Delete, Search, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { HotTable } from '@handsontable/vue3'
import { registerAllModules } from 'handsontable/registry'
import type Handsontable from 'handsontable/base'
import type { CellProperties, ColumnSettings } from 'handsontable/settings'
import { registerLanguageDictionary, zhCN } from 'handsontable/i18n'
import { useUserStore } from '@/store/userStore'
import { formatNoteDateTimeDetailed } from '@/utils/noteDate'

import 'handsontable/styles/handsontable.css'
import 'handsontable/styles/ht-theme-main.css'

import {
  executeAttendanceOrder,
  fetchAttendanceConfig,
  fetchAttendanceOrderRefundHistory,
  type AttendanceConfigResponse,
  type AttendanceOrderRow,
  type AttendanceOrderRefundHistoryItem,
} from '@/api/attendance'

registerAllModules()
registerLanguageDictionary(zhCN)

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

const INPUT_TABLE_COLUMNS = ['订单号', '学员名称'] as const
const QUERY_TABLE_COLUMNS = ['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额', '退款额度', '退款原因'] as const
const REFUND_TABLE_COLUMNS = ['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额', '处理结果'] as const

const INPUT_TABLE_HEADERS = ['订单号（必填，兼容微信支付单号/商户单号）', '学员名称（选填）']
const QUERY_TABLE_HEADERS = ['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额', '退款额度(默认全退)', '退款原因（推荐填）']
const REFUND_TABLE_HEADERS = ['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额', '处理结果']

const QUERY_READONLY_COLUMNS = new Set(['学员名称', '微信支付订单号', '商户订单号', '订单金额', '已返款', '剩余金额'])
const REFUND_READONLY_COLUMNS = new Set(REFUND_TABLE_COLUMNS)
const ORDER_DRAFT_STORAGE_KEY_PREFIX = 'attendance.orders.v2'
const HOT_TABLE_HEADER_HEIGHT = 34
const HOT_TABLE_ROW_HEIGHT = 28
const HOT_TABLE_MAX_VISIBLE_ROWS = 12
const INPUT_TABLE_MIN_VISIBLE_ROWS = 6
const QUERY_TABLE_MIN_VISIBLE_ROWS = 4
const REFUND_HISTORY_PAGE_SIZE_OPTIONS = [10, 20, 50]

const loading = ref(false)
const querying = ref(false)
const refunding = ref(false)
const refundHistoryLoading = ref(false)
const config = ref<AttendanceConfigResponse | null>(null)
const inputHotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const queryHotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const refundHotTableRef = ref<{ hotInstance: Handsontable } | null>(null)
const inputMeasuredHeight = ref<number | null>(null)
const queryMeasuredHeight = ref<number | null>(null)
const inputRows = ref<InputOrderRow[]>([createEmptyInputRow()])
const queryRows = ref<QueryOrderRow[]>([])
const refundRows = ref<RefundResultRow[]>([])
const refundHistoryItems = ref<AttendanceOrderRefundHistoryItem[]>([])
const refundHistoryPage = ref(1)
const refundHistoryPageSize = ref(20)
const refundHistoryTotal = ref(0)
const restoredInputDraftStorageKey = ref<string | null>(null)
const restoredQueryDraftStorageKey = ref<string | null>(null)
const restoredRefundDraftStorageKey = ref<string | null>(null)
const userStore = useUserStore()

const currentExecutionDevice = computed(() => config.value?.current_execution_device ?? null)
const currentOrderLookupMode = computed(() => config.value?.service.order_lookup_mode || 'browser_only')
const inputDraftStorageKey = computed(() => buildScopedStorageKey('input'))
const queryDraftStorageKey = computed(() => buildScopedStorageKey('query'))
const refundDraftStorageKey = computed(() => buildScopedStorageKey('refund'))
const hasQueryRows = computed(() => queryRows.value.some(isMeaningfulQueryRow))
const hasRefundRows = computed(() => refundRows.value.some(isMeaningfulRefundRow))
const hasOrderPageData = computed(() => (
  inputRows.value.some(isMeaningfulInputRow)
  || hasQueryRows.value
  || hasRefundRows.value
))
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

function buildScopedStorageKey(scope: string): string | null {
  const userId = userStore.user?.id
  if (typeof userId === 'number' && Number.isFinite(userId)) {
    return `${ORDER_DRAFT_STORAGE_KEY_PREFIX}:${scope}:user:${userId}`
  }

  const username = (userStore.user?.username || '').trim()
  if (username) {
    return `${ORDER_DRAFT_STORAGE_KEY_PREFIX}:${scope}:username:${username}`
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

  await loadRefundHistory(refundHistoryPage.value, refundHistoryPageSize.value)
}

async function loadRefundHistory(page = refundHistoryPage.value, pageSize = refundHistoryPageSize.value) {
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
  } catch (error: any) {
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
    ElMessage.success('退款流程已执行')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '退款任务执行失败')
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

onMounted(() => {
  if (userStore.isAuthenticated && !userStore.user && !userStore.loading) {
    void userStore.fetchUserProfile()
  }
  void loadPageData()
})
</script>

<template>
  <div class="attendance-page">
    <header class="page-header">
      <h1>订单</h1>
    </header>

    <div class="orders-layout">
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
            :row-headers="true"
            :manual-column-resize="true"
            :auto-column-size="true"
            :manual-row-resize="true"
            :copy-paste="true"
            :context-menu="true"
            :auto-row-size="true"
            :min-spare-rows="1"
            :render-all-rows="true"
            :stretch-h="'none'"
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
            :row-headers="true"
            :manual-column-resize="true"
            :auto-column-size="true"
            :manual-row-resize="true"
            :copy-paste="true"
            :context-menu="true"
            :auto-row-size="true"
            :min-spare-rows="0"
            :render-all-rows="true"
            :stretch-h="'none'"
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
            :row-headers="true"
            :manual-column-resize="true"
            :auto-column-size="true"
            :manual-row-resize="true"
            :copy-paste="true"
            :context-menu="true"
            :min-spare-rows="0"
            :stretch-h="'none'"
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
        </div>

        <div v-loading="refundHistoryLoading" class="history-table-shell">
          <el-table
            :data="refundHistoryItems"
            row-key="id"
            table-layout="auto"
            empty-text="暂无退款历史"
            class="history-table"
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
        </div>

        <div v-if="refundHistoryTotal > 0" class="history-pagination-row">
          <el-pagination
            background
            layout="total, sizes, prev, pager, next"
            :current-page="refundHistoryPage"
            :page-size="refundHistoryPageSize"
            :page-sizes="REFUND_HISTORY_PAGE_SIZE_OPTIONS"
            :total="refundHistoryTotal"
            @current-change="handleRefundHistoryPageChange"
            @size-change="handleRefundHistoryPageSizeChange"
          />
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
}

.page-header h1 {
  margin: 0;
  font-size: 30px;
  line-height: 1.1;
  color: #322719;
}

.orders-layout {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.panel-card {
  padding: 24px;
  border-radius: 22px;
  background: #fffaf2;
  border: 1px solid rgba(122, 93, 56, 0.14);
  box-shadow: 0 12px 28px rgba(68, 48, 26, 0.08);
}

.panel-header {
  margin-bottom: 18px;
}

.panel-header h2 {
  margin: 0;
  font-size: 22px;
  color: #322719;
}

.section-action-row {
  display: flex;
  justify-content: flex-start;
  margin-top: 14px;
}

.sheet-frame {
  overflow-x: auto;
  overflow-y: visible;
  border-radius: 18px;
  border: 1px solid rgba(133, 100, 59, 0.14);
  background: rgba(255, 253, 248, 0.95);
}

.sheet-frame :deep(.handsontable) {
  font-size: 14px;
}

.sheet-frame :deep(.htCore td),
.sheet-frame :deep(.htCore th) {
  vertical-align: middle;
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
  .history-pagination-row {
    justify-content: flex-start;
    overflow: auto;
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

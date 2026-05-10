<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, QuestionFilled, Search, Setting, Upload } from '@element-plus/icons-vue'

import {
  fetchEastmoneyTradeSnapshot,
  fetchEastmoneySyncRuns,
  fetchEastmoneyTradeRecords,
  fetchLatestEastmoneyAssetSnapshot,
  importEastmoneyTradeDetailFromOcr,
  syncEastmoneyTradeData,
  type EastmoneyAssetSnapshot,
  type EastmoneySyncRun,
  type EastmoneyTable,
  type EastmoneyTradeRecord,
  type EastmoneyTradeSnapshot,
} from '@/api/eastmoney'

const TRADE_RECORD_COLUMN_STORAGE_KEY = 'codeyun:eastmoney:trade-record-columns:v2'
const MONEY_COLUMN_KEYWORDS = ['金额', '市值', '资金', '余额', '费用', '佣金', '印花税', '规费', '过户费', '盈亏']
const NON_MONEY_COLUMN_KEYWORDS = ['价格', '价', '比例', '%', '数量', '代码', '名称', '日期', '时间', '编号', '方向', '币种']
const SIGNED_TRADE_MONEY_KEYS = ['occurrence_amount', 'amount']
const TRADE_FEE_KEYS = ['fee', 'commission', 'stamp_tax', 'transfer_fee', 'other_fee']
const TRADE_RECORD_COLUMNS = [
  { key: 'trade_date', prop: 'trade_date', label: '日期', minWidth: 74, maxWidth: 104 },
  { key: 'trade_time', prop: 'trade_time', label: '时间', minWidth: 68, maxWidth: 94 },
  { key: 'security_code', prop: 'security_code', label: '代码', minWidth: 64, maxWidth: 92 },
  { key: 'security_name', prop: 'security_name', label: '名称', minWidth: 64, maxWidth: 150, showOverflowTooltip: true },
  { key: 'direction', prop: 'direction', label: '方向', minWidth: 58, maxWidth: 82 },
  { key: 'quantity', prop: 'quantity', label: '数量', minWidth: 64, maxWidth: 96, align: 'right' },
  { key: 'price', prop: 'price', label: '价格', minWidth: 58, maxWidth: 88, align: 'right' },
  { key: 'occurrence_amount', prop: 'occurrence_amount', label: '发生金额', minWidth: 82, maxWidth: 122, align: 'right' },
  { key: 'amount', prop: 'amount', label: '成交金额', minWidth: 82, maxWidth: 122, align: 'right' },
  { key: 'fee', prop: 'fee', label: '费用', minWidth: 58, maxWidth: 84, align: 'right' },
  { key: 'commission', prop: 'commission', label: '佣金', minWidth: 58, maxWidth: 84, align: 'right', defaultVisible: false },
  { key: 'stamp_tax', prop: 'stamp_tax', label: '印花税', minWidth: 64, maxWidth: 92, align: 'right', defaultVisible: false },
  { key: 'transfer_fee', prop: 'transfer_fee', label: '过户费', minWidth: 64, maxWidth: 92, align: 'right', defaultVisible: false },
  { key: 'other_fee', prop: 'other_fee', label: '其他费用', minWidth: 76, maxWidth: 104, align: 'right', defaultVisible: false },
  { key: 'currency', prop: 'currency', label: '币种', minWidth: 58, maxWidth: 82 },
  { key: 'source', prop: 'source', label: '来源', minWidth: 72, maxWidth: 106 },
  { key: 'deal_id', prop: 'deal_id', label: '成交编号', minWidth: 112, maxWidth: 190, showOverflowTooltip: true },
  { key: 'occurrence_date', prop: 'occurrence_date', label: '发生日期', minWidth: 74, maxWidth: 104, defaultVisible: false },
  { key: 'occurrence_time', prop: 'occurrence_time', label: '发生时间', minWidth: 68, maxWidth: 94, defaultVisible: false },
  { key: 'shareholder_account', prop: 'shareholder_account', label: '股东账号', minWidth: 96, maxWidth: 136, showOverflowTooltip: true, defaultVisible: false },
  { key: 'share_balance', prop: 'share_balance', label: '股份余额', minWidth: 82, maxWidth: 118, align: 'right', defaultVisible: false },
  { key: 'fund_balance', prop: 'fund_balance', label: '资金余额', minWidth: 82, maxWidth: 122, align: 'right', defaultVisible: false },
  { key: 'extended_name', prop: 'extended_name', label: '扩位简称', minWidth: 96, maxWidth: 160, showOverflowTooltip: true, defaultVisible: false },
  { key: 'last_seen_at', prop: 'last_seen_at', label: '入库时间', minWidth: 118, maxWidth: 170 },
] as const
const DEFAULT_TRADE_RECORD_COLUMN_KEYS = TRADE_RECORD_COLUMNS
  .filter((column) => column.defaultVisible !== false)
  .map((column) => column.key)

const loading = ref(false)
const syncing = ref(false)
const localLoading = ref(false)
const pageError = ref('')
const snapshot = ref<EastmoneyTradeSnapshot | null>(null)
const activeTab = ref('local-history')
const tradeRecordColumnDialogVisible = ref(false)
const visibleTradeRecordColumnKeys = ref<string[]>(loadVisibleTradeRecordColumnKeys())
const localRecords = ref<EastmoneyTradeRecord[]>([])
const localTotal = ref(0)
const localPage = ref(1)
const localPageSize = 50
const syncRuns = ref<EastmoneySyncRun[]>([])
const latestAssetSnapshot = ref<EastmoneyAssetSnapshot | null>(null)
const sourceFilter = ref('')
const securityCodeFilter = ref('')
const pasteImportEnabled = ref(false)
const ocrImporting = ref(false)

const accountLabel = computed(() => latestAssetSnapshot.value?.account_label || snapshot.value?.account_label || '')
const summaryItems = computed(() => {
  const summary = latestAssetSnapshot.value?.raw_json ?? snapshot.value?.summary ?? {}
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
const positionTables = computed(() => [
  snapshot.value?.positions,
  snapshot.value?.hk_positions,
  snapshot.value?.sgt_positions,
].filter(Boolean) as EastmoneyTable[])
const visibleTradeRecordColumns = computed(() => {
  const visibleKeys = new Set(visibleTradeRecordColumnKeys.value)
  return TRADE_RECORD_COLUMNS.filter((column) => visibleKeys.has(column.key))
})
const tradeRecordColumnWidths = computed<Record<string, number>>(() => {
  const widths: Record<string, number> = {}
  for (const column of TRADE_RECORD_COLUMNS) {
    const maxTextWidth = Math.max(
      getVisualTextWidth(column.label),
      ...localRecords.value.map((row) => getVisualTextWidth(getTradeRecordCellText(row, column.key))),
    )
    const horizontalPadding = column.align === 'right' ? 30 : 26
    const measuredWidth = Math.ceil(maxTextWidth * 7 + horizontalPadding)
    widths[column.key] = Math.min(Math.max(measuredWidth, column.minWidth), column.maxWidth)
  }
  return widths
})

watch(
  visibleTradeRecordColumnKeys,
  (keys) => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(TRADE_RECORD_COLUMN_STORAGE_KEY, JSON.stringify(keys))
  },
  { deep: true },
)

function loadVisibleTradeRecordColumnKeys() {
  if (typeof window === 'undefined') return [...DEFAULT_TRADE_RECORD_COLUMN_KEYS]
  try {
    const parsed = JSON.parse(window.localStorage.getItem(TRADE_RECORD_COLUMN_STORAGE_KEY) || '[]')
    if (!Array.isArray(parsed)) return [...DEFAULT_TRADE_RECORD_COLUMN_KEYS]
    const validKeys = new Set(TRADE_RECORD_COLUMNS.map((column) => column.key))
    const visibleKeys = DEFAULT_TRADE_RECORD_COLUMN_KEYS.filter((key) => parsed.includes(key) && validKeys.has(key))
    visibleKeys.push(...parsed.filter((key) => validKeys.has(key) && !visibleKeys.includes(key)))
    return visibleKeys.length ? visibleKeys : [...DEFAULT_TRADE_RECORD_COLUMN_KEYS]
  } catch {
    return [...DEFAULT_TRADE_RECORD_COLUMN_KEYS]
  }
}

function resetTradeRecordColumns() {
  visibleTradeRecordColumnKeys.value = [...DEFAULT_TRADE_RECORD_COLUMN_KEYS]
}

function getTradeRecordCellText(row: EastmoneyTradeRecord, key: string) {
  if (key === 'source') return sourceLabel(row.source)
  if (key === 'last_seen_at') return formatTime(row.last_seen_at)
  if (key === 'quantity') {
    return formatSignedTradeText(row.quantity, isSellTrade(row) ? -1 : 1)
  }
  if (key === 'occurrence_amount') {
    return formatSignedSmartMoney(row.occurrence_amount || row.amount, isBuyTrade(row) ? -1 : 1)
  }
  if (SIGNED_TRADE_MONEY_KEYS.includes(key)) {
    return formatSignedSmartMoney(row[key as 'occurrence_amount' | 'amount'], isBuyTrade(row) ? -1 : 1)
  }
  if (TRADE_FEE_KEYS.includes(key)) {
    return formatSignedSmartMoney(row[key as 'fee' | 'commission' | 'stamp_tax' | 'transfer_fee' | 'other_fee'], -1)
  }
  if (key === 'fund_balance') {
    return formatSmartMoney(row.fund_balance)
  }
  return String(row[key as keyof EastmoneyTradeRecord] ?? '')
}

function getPositionCellText(row: Record<string, string>, column: string) {
  const value = row[column] ?? ''
  return isEastmoneyMoneyColumn(column) ? formatSmartMoney(value) : value
}

function getTradeRecordColumnWidth(column: (typeof TRADE_RECORD_COLUMNS)[number]) {
  return tradeRecordColumnWidths.value[column.key] ?? column.minWidth
}

function getVisualTextWidth(value: unknown) {
  return Array.from(String(value ?? '')).reduce((width, char) => {
    return width + (/[\u3000-\u9fff\uff00-\uffef]/.test(char) ? 2 : 1)
  }, 0)
}

function isNegativeTradeRecordCell(row: EastmoneyTradeRecord, key: string) {
  if (![...SIGNED_TRADE_MONEY_KEYS, ...TRADE_FEE_KEYS, 'quantity', 'fund_balance'].includes(key)) return false
  return parseMoneyValue(getTradeRecordCellText(row, key)) < 0
}

function isNegativePositionCell(row: Record<string, string>, column: string) {
  return isEastmoneyMoneyColumn(column) && parseMoneyValue(row[column]) < 0
}

function isEastmoneyMoneyColumn(column: string) {
  return MONEY_COLUMN_KEYWORDS.some((keyword) => column.includes(keyword))
    && !NON_MONEY_COLUMN_KEYWORDS.some((keyword) => column.includes(keyword))
}

function isBuyTrade(row: EastmoneyTradeRecord) {
  return row.direction.includes('买')
}

function isSellTrade(row: EastmoneyTradeRecord) {
  return row.direction.includes('卖')
}

function formatSignedTradeText(value: unknown, sign: 1 | -1) {
  const text = String(value ?? '').trim()
  const numberValue = parseMoneyValue(text)
  if (!Number.isFinite(numberValue) || numberValue === 0) return text
  const unsignedText = text.replace(/^[+-]/, '')
  return sign < 0 ? `-${unsignedText}` : unsignedText
}

function formatSignedSmartMoney(value: unknown, sign: 1 | -1) {
  const numberValue = parseMoneyValue(value)
  if (!Number.isFinite(numberValue)) return String(value ?? '').trim()
  return formatSmartMoneyNumber(Math.abs(numberValue) * sign)
}

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

function sourceLabel(value: string) {
  if (value === 'normal_history_deal') return '普通交易'
  if (value === 'hk_history_deal') return '港股通'
  if (value === 'mobile_trade_detail') return '手机明细'
  return value || '-'
}

function statusLabel(value: string) {
  if (value === 'success') return '成功'
  if (value === 'running') return '运行中'
  if (value === 'login_required') return '需登录'
  if (value === 'failed') return '失败'
  return value || '-'
}

function statusType(value: string): 'success' | 'warning' | 'danger' | 'info' {
  if (value === 'success') return 'success'
  if (value === 'login_required') return 'warning'
  if (value === 'failed') return 'danger'
  return 'info'
}

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '读取失败'
}

async function loadSnapshot() {
  loading.value = true
  pageError.value = ''
  try {
    snapshot.value = await fetchEastmoneyTradeSnapshot()
  } catch (error) {
    pageError.value = getErrorMessage(error)
    ElMessage.error(pageError.value)
  } finally {
    loading.value = false
  }
}

async function loadLocalRecords() {
  localLoading.value = true
  try {
    const payload = await fetchEastmoneyTradeRecords({
      source: sourceFilter.value || undefined,
      security_code: securityCodeFilter.value.trim() || undefined,
      limit: localPageSize,
      offset: (localPage.value - 1) * localPageSize,
    })
    localRecords.value = payload.items
    localTotal.value = payload.total
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    localLoading.value = false
  }
}

function reloadLocalRecordsFromFirstPage() {
  localPage.value = 1
  void loadLocalRecords()
}

function handleLocalPageChange(page: number) {
  localPage.value = page
  void loadLocalRecords()
}

async function loadSyncRuns() {
  try {
    syncRuns.value = (await fetchEastmoneySyncRuns({ limit: 20 })).items
  } catch {
    syncRuns.value = []
  }
}

async function loadLatestAssetSnapshot() {
  try {
    latestAssetSnapshot.value = (await fetchLatestEastmoneyAssetSnapshot()).item
  } catch {
    latestAssetSnapshot.value = null
  }
}

async function syncToDatabase() {
  syncing.value = true
  pageError.value = ''
  try {
    const run = await syncEastmoneyTradeData(defaultSyncDateParams())
    if (run.status === 'login_required') {
      ElMessage.warning('证券交易系统未登录，请先完成资金账号登录后再同步。')
    } else if (run.status === 'success') {
      ElMessage.success(`同步完成：新增 ${run.inserted_count} 条，更新 ${run.updated_count} 条`)
      activeTab.value = 'local-history'
    }
    await Promise.all([loadLocalRecords(), loadSyncRuns(), loadLatestAssetSnapshot()])
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
    await Promise.all([loadLocalRecords(), loadSyncRuns()])
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

function rowClassName({ row }: { row: Record<string, string> }) {
  const value = row['持仓盈亏'] || row['参考盈亏(￥)'] || row['盈亏比例(%)'] || row['持仓盈亏比例'] || ''
  if (value.startsWith('-')) return 'loss-row'
  if (value && value !== '-' && !value.startsWith('0')) return 'gain-row'
  return ''
}

onMounted(() => {
  window.addEventListener('paste', handleWindowPaste)
  void loadSnapshot()
  void loadLocalRecords()
  void loadSyncRuns()
  void loadLatestAssetSnapshot()
})

onBeforeUnmount(() => {
  window.removeEventListener('paste', handleWindowPaste)
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
              更新到库会按东方财富限制同步最近100天成交明细，并保存当次资产和持仓快照。
            </div>
          </template>
          <el-icon class="help-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-tag v-if="accountLabel" size="small" effect="plain">{{ accountLabel }}</el-tag>
        <el-tag v-if="snapshot?.login_required" size="small" type="warning" effect="plain">需要交易登录</el-tag>
      </div>
      <div class="toolbar-actions">
        <el-button
          :icon="Upload"
          :loading="ocrImporting"
          size="small"
          :type="pasteImportEnabled ? 'primary' : 'default'"
          @click="toggleTradeDetailPasteImport"
        >
          {{ ocrImporting ? '识别中...' : pasteImportEnabled ? '关闭截图导入' : '粘贴截图导入' }}
        </el-button>
        <el-button
          :icon="Download"
          :loading="syncing"
          size="small"
          type="primary"
          @click="syncToDatabase"
        >
          更新到库
        </el-button>
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
    <el-alert
      v-else-if="snapshot?.login_required"
      class="page-alert"
      title="证券交易系统未登录，请先在东方财富浏览器窗口完成资金账号登录。"
      type="warning"
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

    <el-tabs v-model="activeTab" class="data-tabs">
      <el-tab-pane label="成交明细" name="local-history">
        <section class="table-section">
          <div class="table-title local-record-title">
            <div>
              <h2>成交明细</h2>
              <span>{{ localTotal }} 条</span>
            </div>
            <div class="inline-filters">
              <el-button :icon="Setting" size="small" @click="tradeRecordColumnDialogVisible = true">
                字段
              </el-button>
              <el-select
                v-model="sourceFilter"
                class="source-filter"
                clearable
                placeholder="来源"
                size="small"
                @change="reloadLocalRecordsFromFirstPage"
              >
                <el-option label="普通交易" value="normal_history_deal" />
                <el-option label="港股通" value="hk_history_deal" />
                <el-option label="手机明细" value="mobile_trade_detail" />
              </el-select>
              <el-input
                v-model="securityCodeFilter"
                class="code-filter"
                clearable
                placeholder="代码"
                size="small"
                @keyup.enter="reloadLocalRecordsFromFirstPage"
                @clear="reloadLocalRecordsFromFirstPage"
              />
              <el-button :icon="Search" :loading="localLoading" size="small" @click="reloadLocalRecordsFromFirstPage">
                查询
              </el-button>
            </div>
          </div>
          <el-table
            v-loading="localLoading"
            :data="localRecords"
            size="small"
            border
            table-layout="auto"
            :fit="false"
            empty-text="暂无本地成交数据"
          >
            <el-table-column
              v-for="column in visibleTradeRecordColumns"
              :key="column.key"
              :prop="column.prop"
              :label="column.label"
              :width="getTradeRecordColumnWidth(column)"
              :align="column.align"
              :show-overflow-tooltip="Boolean(column.showOverflowTooltip)"
            >
              <template #default="{ row }">
                <span :class="{ 'negative-value': isNegativeTradeRecordCell(row, column.key) }">
                  {{ getTradeRecordCellText(row, column.key) }}
                </span>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="localTotal > localPageSize"
            class="local-pagination"
            background
            layout="prev, pager, next, total"
            :current-page="localPage"
            :page-size="localPageSize"
            :total="localTotal"
            @current-change="handleLocalPageChange"
          />
        </section>
      </el-tab-pane>

      <el-tab-pane label="持仓" name="positions">
        <section v-for="table in positionTables" :key="table.title" class="table-section">
          <div class="table-title">
            <h2>{{ table.title }}</h2>
            <span>{{ table.rows.length }} 条</span>
          </div>
          <el-table
            :data="table.rows"
            size="small"
            border
            table-layout="auto"
            :fit="false"
            :row-class-name="rowClassName"
            empty-text="暂无数据"
          >
            <el-table-column
              v-for="column in table.columns"
              :key="column"
              :prop="column"
              :label="column"
              min-width="92"
              :align="isEastmoneyMoneyColumn(column) ? 'right' : undefined"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                <span :class="{ 'negative-value': isNegativePositionCell(row, column) }">
                  {{ getPositionCellText(row, column) }}
                </span>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </el-tab-pane>

      <el-tab-pane label="同步记录" name="sync-runs">
        <section class="table-section">
          <div class="table-title">
            <h2>最近同步</h2>
            <span>{{ syncRuns.length }} 次</span>
          </div>
          <el-table
            :data="syncRuns"
            size="small"
            border
            table-layout="auto"
            :fit="false"
            empty-text="暂无同步记录"
          >
            <el-table-column label="状态" min-width="78">
              <template #default="{ row }">
                <el-tag size="small" :type="statusType(row.status)" effect="plain">
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="日期范围" min-width="180">
              <template #default="{ row }">
                {{ row.start_date }} 至 {{ row.end_date }}
              </template>
            </el-table-column>
            <el-table-column prop="account_label" label="账户" min-width="150" show-overflow-tooltip />
            <el-table-column prop="inserted_count" label="新增" min-width="70" align="right" />
            <el-table-column prop="updated_count" label="更新" min-width="70" align="right" />
            <el-table-column prop="position_count" label="持仓快照" min-width="92" align="right" />
            <el-table-column label="开始时间" min-width="154">
              <template #default="{ row }">
                {{ formatTime(row.started_at) }}
              </template>
            </el-table-column>
            <el-table-column label="错误" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="run-error">{{ row.error_message || '' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="tradeRecordColumnDialogVisible" title="成交字段" width="360px">
      <div class="column-visibility-list">
        <el-checkbox-group v-model="visibleTradeRecordColumnKeys">
          <el-checkbox
            v-for="column in TRADE_RECORD_COLUMNS"
            :key="column.key"
            :value="column.key"
          >
            {{ column.label }}
          </el-checkbox>
        </el-checkbox-group>
      </div>
      <template #footer>
        <el-button size="small" @click="resetTradeRecordColumns">重置</el-button>
        <el-button size="small" type="primary" @click="tradeRecordColumnDialogVisible = false">
          完成
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.eastmoney-page {
  display: flex;
  min-height: 100%;
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
  gap: 8px;
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

.data-tabs {
  min-width: 0;
}

.table-section {
  margin-bottom: 16px;
}

.table-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  justify-content: space-between;
  margin-bottom: 8px;
}

.table-title > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.table-title h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0;
}

.table-title span {
  color: #64748b;
  font-size: 12px;
}

.local-record-title {
  align-items: center;
  gap: 12px;
}

.inline-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

.source-filter {
  width: 116px;
}

.code-filter {
  width: 112px;
}

.local-pagination {
  justify-content: flex-end;
  margin-top: 10px;
}

.run-error {
  color: #b91c1c;
}

.negative-value {
  color: #15803d;
  font-weight: 500;
}

.column-visibility-list {
  max-height: 420px;
  overflow: auto;
}

:deep(.column-visibility-list .el-checkbox-group) {
  display: grid;
  gap: 2px;
}

:deep(.column-visibility-list .el-checkbox) {
  height: 28px;
  margin-right: 0;
}

:deep(.loss-row) {
  --el-table-tr-bg-color: #f3fbf5;
}

:deep(.gain-row) {
  --el-table-tr-bg-color: #fff7f7;
}

:deep(.el-table) {
  max-width: 100%;
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

  .local-record-title {
    align-items: flex-start;
    flex-direction: column;
  }

  .inline-filters {
    justify-content: flex-start;
  }
}
</style>

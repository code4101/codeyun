<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Filter } from '@element-plus/icons-vue'

import {
  fetchEastmoneyTradeRecords,
  type EastmoneyTradeRecord,
} from '@/api/eastmoney'
import StandardPagination from '@/components/StandardPagination.vue'
import { formatCompactSignificant } from '@/utils/numberFormat'

type HistoryView = 'detail' | 'summary'
type SummarySortKey = 'security' | 'market' | 'buyAmount' | 'sellAmount' | 'totalFee' | 'feeRate'
type SummarySortDirection = 'ascending' | 'descending'

interface SecuritySummaryRow {
  key: string
  market: string
  securityCode: string
  securityName: string
  currency: string
  buyAmount: number
  sellAmount: number
  buyAmountInCny: number
  sellAmountInCny: number
  totalFee: number
  turnoverInCny: number
  missingSettlementRate: boolean
  latestTradeTime: string
}

const rows = ref<EastmoneyTradeRecord[]>([])
const allTradeRows = ref<EastmoneyTradeRecord[]>([])
const securityOptions = ref<Array<{ security_code: string; security_name: string }>>([])
const selectedSecurityCode = ref('')
const securitySearch = ref('')
const securityPopoverVisible = ref(false)
const activeView = ref<HistoryView>('detail')
const summarySortKey = ref<SummarySortKey>('buyAmount')
const summarySortDirection = ref<SummarySortDirection>('descending')
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const loading = ref(false)
const summaryLoading = ref(false)
let requestSequence = 0

const filteredSecurityOptions = computed(() => {
  const keyword = securitySearch.value.trim().toLocaleLowerCase()
  if (!keyword) return securityOptions.value
  return securityOptions.value.filter((option) => (
    option.security_code.toLocaleLowerCase().includes(keyword)
    || option.security_name.toLocaleLowerCase().includes(keyword)
  ))
})

const summaryRows = computed<SecuritySummaryRow[]>(() => {
  const groups = new Map<string, SecuritySummaryRow>()
  for (const row of allTradeRows.value) {
    if (!row.security_code) continue
    const key = `${row.market}:${row.security_code}`
    let summary = groups.get(key)
    if (!summary) {
      summary = {
        key,
        market: row.market,
        securityCode: row.security_code,
        securityName: row.security_name,
        currency: amountCurrency(row),
        buyAmount: 0,
        sellAmount: 0,
        buyAmountInCny: 0,
        sellAmountInCny: 0,
        totalFee: 0,
        turnoverInCny: 0,
        missingSettlementRate: false,
        latestTradeTime: [row.trade_date, row.trade_time].filter(Boolean).join(' '),
      }
      groups.set(key, summary)
    }

    const amount = row.amount_value
    if (amount != null && Number.isFinite(amount) && amount > 0) {
      if (row.direction.includes('买')) summary.buyAmount += amount
      if (row.direction.includes('卖')) summary.sellAmount += amount

      let amountInCny = amount
      if (row.market === 'HK') {
        const settlementRate = Number(row.raw_json?.['结算汇率'])
        if (Number.isFinite(settlementRate) && settlementRate > 0) {
          amountInCny *= settlementRate
        } else {
          summary.missingSettlementRate = true
          amountInCny = 0
        }
      }
      summary.turnoverInCny += amountInCny
      if (row.direction.includes('买')) summary.buyAmountInCny += amountInCny
      if (row.direction.includes('卖')) summary.sellAmountInCny += amountInCny
    }
    if (row.fee_value != null && Number.isFinite(row.fee_value)) {
      summary.totalFee += row.fee_value
    }
  }
  const direction = summarySortDirection.value === 'ascending' ? 1 : -1
  return [...groups.values()].sort((left, right) => {
    let result = 0
    switch (summarySortKey.value) {
      case 'security':
        result = (
          left.securityName.localeCompare(right.securityName, 'zh-CN')
          || left.securityCode.localeCompare(right.securityCode)
        )
        break
      case 'market':
        result = marketText(left.market).localeCompare(marketText(right.market), 'zh-CN')
        break
      case 'buyAmount':
        result = left.buyAmountInCny - right.buyAmountInCny
        break
      case 'sellAmount':
        result = left.sellAmountInCny - right.sellAmountInCny
        break
      case 'totalFee':
        result = left.totalFee - right.totalFee
        break
      case 'feeRate': {
        const leftRate = left.turnoverInCny > 0 && !left.missingSettlementRate
          ? left.totalFee / left.turnoverInCny
          : Number.NEGATIVE_INFINITY
        const rightRate = right.turnoverInCny > 0 && !right.missingSettlementRate
          ? right.totalFee / right.turnoverInCny
          : Number.NEGATIVE_INFINITY
        result = leftRate - rightRate
        break
      }
    }
    return (
      result * direction
      || right.latestTradeTime.localeCompare(left.latestTradeTime)
      || left.key.localeCompare(right.key)
    )
  })
})

function displayText(value: string | null | undefined): string {
  const text = String(value ?? '').trim()
  return text || '—'
}

function displayNumber(value: number | null, fallback = ''): string {
  if (value == null || !Number.isFinite(value)) return displayText(fallback)
  const sign = value < 0 ? '-' : ''
  const numeric = Math.abs(value)
  if (numeric >= 1e8) return `${sign}${formatCompactSignificant(numeric / 1e8, 4)}亿`
  if (numeric >= 1e4) return `${sign}${formatCompactSignificant(numeric / 1e4, 4)}万`
  return `${sign}${formatCompactSignificant(numeric, 4)}`
}

function displayTime(row: EastmoneyTradeRecord): string {
  return [row.trade_date, row.trade_time].filter(Boolean).join(' ') || '—'
}

function marketText(market: string): string {
  return ({ HK: '港股', SH: '沪市', SZ: '深市' } as Record<string, string>)[market] || displayText(market)
}

function marketCurrencyHint(row: EastmoneyTradeRecord): string {
  if (row.market === 'HK') return '成交额为港币，费用为人民币'
  return displayText(row.currency)
}

function amountCurrency(row: EastmoneyTradeRecord): string {
  if (row.market === 'HK') return '港币'
  if (row.currency && row.currency !== '-') return row.currency
  return '人民币'
}

function displayAmount(row: EastmoneyTradeRecord): string {
  return `${displayNumber(row.amount_value, row.amount)} ${amountCurrency(row)}`
}

function displayFeeRate(row: EastmoneyTradeRecord): string {
  const fee = row.fee_value
  const amount = row.amount_value
  if (fee == null || amount == null || amount <= 0) return '—'

  let amountInCny = amount
  if (row.market === 'HK') {
    const settlementRate = Number(row.raw_json?.['结算汇率'])
    if (!Number.isFinite(settlementRate) || settlementRate <= 0) return '—'
    amountInCny *= settlementRate
  }
  return `${formatCompactSignificant((fee / amountInCny) * 100, 4)}%`
}

function displaySummaryAmount(value: number, currency: string): string {
  return `${displayNumber(value)} ${currency}`
}

function displaySummaryFeeRate(row: SecuritySummaryRow): string {
  if (row.missingSettlementRate || row.turnoverInCny <= 0) return '—'
  return `${formatCompactSignificant((row.totalFee / row.turnoverInCny) * 200, 4)}%`
}

function changeSummarySort(key: SummarySortKey) {
  if (summarySortKey.value === key) {
    summarySortDirection.value = summarySortDirection.value === 'ascending'
      ? 'descending'
      : 'ascending'
    return
  }
  summarySortKey.value = key
  summarySortDirection.value = key === 'security' || key === 'market'
    ? 'ascending'
    : 'descending'
}

function summarySortIndicator(key: SummarySortKey): string {
  if (summarySortKey.value !== key) return ''
  return summarySortDirection.value === 'ascending' ? '↑' : '↓'
}

function summaryAriaSort(key: SummarySortKey): SummarySortDirection | 'none' {
  return summarySortKey.value === key ? summarySortDirection.value : 'none'
}

function regulationFee(row: EastmoneyTradeRecord): string {
  const rawValue = row.raw_json?.['交易规费'] ?? ''
  const numeric = Number(rawValue)
  return rawValue !== '' && Number.isFinite(numeric)
    ? displayNumber(numeric)
    : displayText(rawValue)
}

function directionClass(direction: string): string {
  if (direction.includes('买')) return 'is-buy'
  if (direction.includes('卖')) return 'is-sell'
  return ''
}

function selectSecurity(securityCode: string) {
  selectedSecurityCode.value = securityCode
  securityPopoverVisible.value = false
  securitySearch.value = ''
}

async function loadRows() {
  const sequence = ++requestSequence
  loading.value = true
  try {
    const result = await fetchEastmoneyTradeRecords({
      security_code: selectedSecurityCode.value || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (sequence !== requestSequence) return
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    if (sequence === requestSequence) {
      ElMessage.error(error instanceof Error ? error.message : '历史成交加载失败')
    }
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

async function loadAllRows() {
  summaryLoading.value = true
  try {
    const items: EastmoneyTradeRecord[] = []
    let offset = 0
    let expectedTotal = Number.POSITIVE_INFINITY
    while (offset < expectedTotal) {
      const result = await fetchEastmoneyTradeRecords({ limit: 1000, offset })
      items.push(...result.items)
      expectedTotal = result.total
      if (!result.items.length) break
      offset += result.items.length
    }
    allTradeRows.value = items

    const options = new Map<string, string>()
    for (const row of items) {
      if (row.security_code) options.set(row.security_code, row.security_name)
    }
    securityOptions.value = [...options]
      .map(([security_code, security_name]) => ({ security_code, security_name }))
      .sort((left, right) => (
        left.security_name.localeCompare(right.security_name, 'zh-CN')
        || left.security_code.localeCompare(right.security_code)
      ))
  } catch {
    ElMessage.error('证券汇总加载失败')
  } finally {
    summaryLoading.value = false
  }
}

watch(page, () => void loadRows())
watch(pageSize, () => {
  if (page.value === 1) void loadRows()
  else page.value = 1
})
watch(selectedSecurityCode, () => {
  if (page.value === 1) void loadRows()
  else page.value = 1
})
onMounted(() => {
  void loadRows()
  void loadAllRows()
})
</script>

<template>
  <main class="history-page">
    <header class="page-head">
      <h1>历史数据</h1>
    </header>

    <nav class="view-switch" aria-label="历史数据视图">
      <button
        type="button"
        :class="{ active: activeView === 'detail' }"
        @click="activeView = 'detail'"
      >
        成交明细
      </button>
      <button
        type="button"
        :class="{ active: activeView === 'summary' }"
        @click="activeView = 'summary'"
      >
        证券汇总
      </button>
    </nav>

    <section
      class="table-shell"
      v-loading="activeView === 'detail' ? loading : summaryLoading"
    >
      <div v-if="activeView === 'detail'" class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>成交时间 ↓</th>
              <th>市场</th>
              <th>
                <el-popover
                  v-model:visible="securityPopoverVisible"
                  placement="bottom-start"
                  :width="250"
                  trigger="click"
                  popper-style="padding: 8px"
                >
                  <template #reference>
                    <button
                      class="column-filter"
                      :class="{ active: selectedSecurityCode }"
                      type="button"
                      title="筛选证券"
                    >
                      证券
                      <el-icon><Filter /></el-icon>
                    </button>
                  </template>
                  <el-input
                    v-model="securitySearch"
                    clearable
                    placeholder="搜索名称或代码"
                    size="small"
                  />
                  <div class="security-filter-list">
                    <button
                      type="button"
                      :class="{ selected: !selectedSecurityCode }"
                      @click="selectSecurity('')"
                    >
                      全部证券
                    </button>
                    <button
                      v-for="option in filteredSecurityOptions"
                      :key="option.security_code"
                      type="button"
                      :class="{ selected: selectedSecurityCode === option.security_code }"
                      @click="selectSecurity(option.security_code)"
                    >
                      <span>{{ option.security_name }}</span>
                      <small>{{ option.security_code }}</small>
                    </button>
                    <div v-if="!filteredSecurityOptions.length" class="filter-empty">
                      没有匹配的证券
                    </div>
                  </div>
                </el-popover>
              </th>
              <th>方向</th>
              <th class="number">数量</th>
              <th class="number">成交价</th>
              <th class="number">成交金额（原币）</th>
              <th class="number">总费用</th>
              <th
                class="number"
                title="总费用 ÷ 人民币成交金额；港股成交额先按结算汇率折算"
              >
                手续费率
              </th>
              <th class="number">佣金</th>
              <th class="number">规费</th>
              <th class="number">印花税</th>
              <th class="number">过户费</th>
              <th>成交编号</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id">
              <td class="time">{{ displayTime(row) }}</td>
              <td>
                <span class="market" :title="marketCurrencyHint(row)">{{ marketText(row.market) }}</span>
              </td>
              <td>
                <div class="security">
                  <strong>{{ displayText(row.security_name) }}</strong>
                  <span>{{ displayText(row.security_code) }}</span>
                </div>
              </td>
              <td>
                <span class="direction" :class="directionClass(row.direction)">
                  {{ displayText(row.direction) }}
                </span>
              </td>
              <td class="number">{{ displayNumber(row.quantity_value, row.quantity) }}</td>
              <td class="number">{{ displayNumber(row.price_value, row.price) }}</td>
              <td class="number">{{ displayAmount(row) }}</td>
              <td class="number fee">{{ displayNumber(row.fee_value, row.fee) }}</td>
              <td class="number fee-rate">{{ displayFeeRate(row) }}</td>
              <td class="number">{{ displayNumber(row.commission_value, row.commission) }}</td>
              <td class="number">{{ regulationFee(row) }}</td>
              <td class="number">{{ displayNumber(row.stamp_tax_value, row.stamp_tax) }}</td>
              <td class="number">{{ displayNumber(row.transfer_fee_value, row.transfer_fee) }}</td>
              <td class="deal-id">{{ displayText(row.deal_id) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!loading && !rows.length" class="empty">暂无历史成交数据</div>
      </div>

      <div v-else class="table-scroll">
        <table>
          <thead>
            <tr>
              <th :aria-sort="summaryAriaSort('security')">
                <button class="sort-button" type="button" @click="changeSummarySort('security')">
                  证券 <span>{{ summarySortIndicator('security') }}</span>
                </button>
              </th>
              <th :aria-sort="summaryAriaSort('market')">
                <button class="sort-button" type="button" @click="changeSummarySort('market')">
                  市场 <span>{{ summarySortIndicator('market') }}</span>
                </button>
              </th>
              <th
                class="number"
                :aria-sort="summaryAriaSort('buyAmount')"
                title="点击排序；不同币种按人民币等值比较"
              >
                <button class="sort-button number" type="button" @click="changeSummarySort('buyAmount')">
                  买入总额（原币） <span>{{ summarySortIndicator('buyAmount') }}</span>
                </button>
              </th>
              <th
                class="number"
                :aria-sort="summaryAriaSort('sellAmount')"
                title="点击排序；不同币种按人民币等值比较"
              >
                <button class="sort-button number" type="button" @click="changeSummarySort('sellAmount')">
                  卖出总额（原币） <span>{{ summarySortIndicator('sellAmount') }}</span>
                </button>
              </th>
              <th class="number" :aria-sort="summaryAriaSort('totalFee')">
                <button class="sort-button number" type="button" @click="changeSummarySort('totalFee')">
                  总手续费（人民币） <span>{{ summarySortIndicator('totalFee') }}</span>
                </button>
              </th>
              <th
                class="number"
                :aria-sort="summaryAriaSort('feeRate')"
                title="全部手续费 ÷ 买卖平均成交额；即平均单边费率 × 2，港股逐笔按结算汇率折算"
              >
                <button class="sort-button number" type="button" @click="changeSummarySort('feeRate')">
                  来回手续费率 <span>{{ summarySortIndicator('feeRate') }}</span>
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in summaryRows" :key="row.key">
              <td>
                <div class="security">
                  <strong>{{ displayText(row.securityName) }}</strong>
                  <span>{{ row.securityCode }}</span>
                </div>
              </td>
              <td><span class="market">{{ marketText(row.market) }}</span></td>
              <td class="number">{{ displaySummaryAmount(row.buyAmount, row.currency) }}</td>
              <td class="number">{{ displaySummaryAmount(row.sellAmount, row.currency) }}</td>
              <td class="number fee">{{ displayNumber(row.totalFee) }}</td>
              <td class="number fee-rate">{{ displaySummaryFeeRate(row) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!summaryLoading && !summaryRows.length" class="empty">暂无证券汇总数据</div>
      </div>

      <footer v-if="activeView === 'detail'" class="table-footer">
        <span>共 {{ total }} 笔</span>
        <StandardPagination
          :page="page"
          :page-size="pageSize"
          :total="total"
          :page-size-options="[20, 50, 100, 200]"
          align="right"
          :disabled="loading"
          @update:page="value => page = value"
          @update:page-size="value => pageSize = value"
        />
      </footer>
      <footer v-else class="table-footer">
        <span>共 {{ summaryRows.length }} 个证券</span>
      </footer>
    </section>
  </main>
</template>

<style scoped>
.history-page {
  box-sizing: border-box;
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  gap: 14px;
  padding: 20px 22px 24px;
  color: #202428;
  background: #f6f7f8;
}

.page-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
}

.page-head h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
}

.view-switch {
  display: inline-flex;
  align-self: flex-start;
  padding: 2px;
  border: 1px solid #dfe2e5;
  border-radius: 6px;
  background: #fff;
}

.view-switch button {
  padding: 5px 12px;
  border: 0;
  border-radius: 4px;
  color: #687078;
  background: transparent;
  font-size: 13px;
  cursor: pointer;
}

.view-switch button.active {
  color: #202428;
  background: #eef1f3;
  font-weight: 600;
}

.table-shell {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #dfe2e5;
  border-radius: 8px;
  background: #fff;
}

.table-scroll {
  min-height: 0;
  flex: 1;
  overflow: auto;
}

table {
  width: max-content;
  min-width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: auto;
  font-size: 13px;
}

th,
td {
  padding: 10px 13px;
  border-bottom: 1px solid #eceef0;
  text-align: left;
  white-space: nowrap;
}

th {
  position: sticky;
  z-index: 1;
  top: 0;
  color: #656b72;
  background: #f8f9fa;
  font-size: 12px;
  font-weight: 600;
}

.sort-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  color: inherit;
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.sort-button.number {
  justify-content: flex-end;
  text-align: right;
}

.sort-button span {
  min-width: 9px;
  color: #2563a8;
}

.sort-button:hover {
  color: #202428;
}

.column-filter {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0;
  border: 0;
  color: inherit;
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.column-filter .el-icon {
  color: #949aa0;
  font-size: 12px;
}

.column-filter.active,
.column-filter.active .el-icon {
  color: #2563a8;
}

.security-filter-list {
  max-height: 280px;
  margin-top: 7px;
  overflow: auto;
}

.security-filter-list button {
  display: flex;
  width: 100%;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 9px;
  border: 0;
  border-radius: 4px;
  color: #343a40;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.security-filter-list button:hover {
  background: #f3f5f7;
}

.security-filter-list button.selected {
  color: #2563a8;
  background: #edf4fc;
}

.security-filter-list small {
  color: #8a9096;
  font-size: 12px;
}

.filter-empty {
  padding: 18px 8px;
  color: #969ca2;
  text-align: center;
  font-size: 12px;
}

tbody tr:hover {
  background: #fafbfc;
}

.number {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.time,
.deal-id {
  color: #596068;
  font-variant-numeric: tabular-nums;
}

.security {
  display: flex;
  align-items: baseline;
  gap: 7px;
}

.security strong {
  font-weight: 600;
}

.security span {
  color: #8a9096;
  font-size: 12px;
}

.market,
.direction {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 7px;
  border-radius: 4px;
  color: #555d65;
  background: #f0f2f4;
  font-size: 12px;
}

.direction.is-buy {
  color: #a43b32;
  background: #fff0ee;
}

.direction.is-sell {
  color: #19704a;
  background: #eaf7f0;
}

.fee {
  color: #8a4b1f;
  font-weight: 600;
}

.fee-rate {
  color: #596068;
  font-weight: 600;
}

.empty {
  padding: 46px 20px;
  color: #8b9197;
  text-align: center;
}

.table-footer {
  display: flex;
  min-height: 52px;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 0 14px;
  border-top: 1px solid #e5e7e9;
  color: #7a8188;
  background: #fff;
  font-size: 13px;
}

@media (max-width: 760px) {
  .history-page {
    padding: 14px;
  }
}
</style>

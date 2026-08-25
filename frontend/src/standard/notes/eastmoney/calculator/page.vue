<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  fetchEastmoneyAkshareHistory,
  fetchEastmoneyCalculatorWorkspace,
  fetchEastmoneyTradeRecords,
  fetchLatestEastmoneyMarketQuotes,
  syncEastmoneyCalculatorMarketQuotes,
  saveEastmoneyCalculatorWorkspace,
  type EastmoneyCalculatorItem,
  type EastmoneyCalculatorTarget,
  type EastmoneyCalculatorTrade,
  type EastmoneyTradeRecord,
} from '@/api/eastmoney'
import { formatCompactSignificant } from '@/utils/numberFormat'

interface ParsedPrice {
  decimals: number
  scaledValue: bigint
}

const STORAGE_KEY = 'notes.eastmoney.priceCalculators.v1'
const QUOTE_REFRESH_RETRY_MS = 10_000
const multipliers = [80, 85, 90, 95, 100, 105, 110, 115, 120] as const
const calculators = ref<EastmoneyCalculatorItem[]>([])
const targets = ref<EastmoneyCalculatorTarget[]>([])
const historyByTarget = ref<Record<string, EastmoneyCalculatorTrade[]>>({})
const currentPrices = ref<Record<string, { price: number; updateTime: string; fetchedAt: number }>>({})
const historicalRoundTripRates = ref<Record<string, number>>({})
const historicalTradesByTarget = ref<Record<string, EastmoneyTradeRecord[]>>({})
const loading = ref(true)
const ready = ref(false)
const saveError = ref(false)
const newDialogVisible = ref(false)
const selectedTargetKey = ref('')
let saveTimer: ReturnType<typeof setTimeout> | null = null
let saveInFlight = false
let saveQueued = false
let quoteRefreshTimer: ReturnType<typeof setTimeout> | null = null
let quoteRefreshInFlight = false
let quoteRefreshStarted = false
let quoteRefreshDelayMs = QUOTE_REFRESH_RETRY_MS

function createId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

interface LegacyCalculator {
  id?: string
  label?: string
  basePrice?: string
  trades?: Array<Partial<EastmoneyCalculatorTrade>>
}

function loadLegacyCalculators(): LegacyCalculator[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (!saved) return []

    const parsed: unknown = JSON.parse(saved)
    return Array.isArray(parsed) ? parsed as LegacyCalculator[] : []
  } catch {
    return []
  }
}

function targetKey(target: Pick<EastmoneyCalculatorTarget, 'market' | 'symbol'>): string {
  return `${target.market}.${target.symbol}`
}

function legacyTarget(label: string): EastmoneyCalculatorTarget | null {
  const text = label.trim()
  if (text.includes('小米')) return targets.value.find((item) => targetKey(item) === 'HK.01810') ?? null
  if (text.includes('金山云')) return targets.value.find((item) => targetKey(item) === 'HK.03896') ?? null
  if (text.includes('机器人')) return targets.value.find((item) => targetKey(item) === 'SZ.159278') ?? null
  return null
}

function normalizeLegacyTrades(rows: LegacyCalculator['trades']): EastmoneyCalculatorTrade[] {
  if (!Array.isArray(rows)) return []
  return rows.flatMap((row): EastmoneyCalculatorTrade[] => {
    if (
      !row
      || typeof row.time !== 'string'
      || typeof row.price !== 'string'
      || typeof row.quantity !== 'string'
    ) return []
    return [{
      id: typeof row.id === 'string' && row.id ? row.id : createId(),
      time: row.time,
      price: row.price,
      quantity: row.quantity,
      source_record_id: typeof row.source_record_id === 'string' ? row.source_record_id : '',
    }]
  })
}

function mergeTrades(
  imported: EastmoneyCalculatorTrade[],
  manual: EastmoneyCalculatorTrade[],
): EastmoneyCalculatorTrade[] {
  const result = imported.map((trade) => ({ ...trade }))
  const importedSourceIds = new Set(imported.map((trade) => trade.source_record_id).filter(Boolean))
  const importedValues = new Set(imported.map((trade) => `${trade.time}|${trade.price}|${trade.quantity}`))
  const manualIds = new Set<string>()
  for (const trade of manual) {
    const valueKey = `${trade.time}|${trade.price}|${trade.quantity}`
    if (
      (trade.source_record_id && importedSourceIds.has(trade.source_record_id))
      || importedValues.has(valueKey)
      || manualIds.has(trade.id)
    ) continue
    manualIds.add(trade.id)
    result.push({ ...trade })
  }
  return result.sort((left, right) => right.time.localeCompare(left.time))
}

async function loadWorkspace(): Promise<void> {
  loading.value = true
  try {
    const workspace = await fetchEastmoneyCalculatorWorkspace()
    targets.value = workspace.targets
    historyByTarget.value = workspace.history_by_target
    if (workspace.items.length) {
      calculators.value = workspace.items
    } else {
      const migrated = loadLegacyCalculators().flatMap((legacy): EastmoneyCalculatorItem[] => {
        const target = legacyTarget(typeof legacy.label === 'string' ? legacy.label : '')
        if (!target) return []
        const key = targetKey(target)
        return [{
          id: typeof legacy.id === 'string' && legacy.id ? legacy.id : createId(),
          ...target,
          base_price: typeof legacy.basePrice === 'string' ? legacy.basePrice : '',
          trades: mergeTrades(historyByTarget.value[key] ?? [], normalizeLegacyTrades(legacy.trades)),
        }]
      })
      calculators.value = migrated
      if (migrated.length) {
        const saved = await saveEastmoneyCalculatorWorkspace(migrated)
        calculators.value = saved.items
        localStorage.removeItem(STORAGE_KEY)
      }
    }
    ready.value = true
    void loadHistoricalRoundTripRates()
    void loadCurrentPrices().finally(startQuoteRefresh)
  } catch (error) {
    console.error('Failed to load calculator workspace:', error)
    ElMessage.error('加载计算器数据失败')
  } finally {
    loading.value = false
  }
}

async function loadHistoricalRoundTripRates(): Promise<void> {
  try {
    const rows: EastmoneyTradeRecord[] = []
    let offset = 0
    let total = Number.POSITIVE_INFINITY
    while (offset < total) {
      const result = await fetchEastmoneyTradeRecords({ limit: 1000, offset })
      rows.push(...result.items)
      total = result.total
      if (!result.items.length) break
      offset += result.items.length
    }

    const groups = new Map<string, {
      fee: number
      turnoverInCny: number
      missingSettlementRate: boolean
    }>()
    const tradesByTarget = new Map<string, EastmoneyTradeRecord[]>()
    for (const row of rows) {
      if (!row.security_code) continue
      const key = `${row.market}.${row.security_code}`
      const targetTrades = tradesByTarget.get(key) ?? []
      targetTrades.push(row)
      tradesByTarget.set(key, targetTrades)
      const group = groups.get(key) ?? {
        fee: 0,
        turnoverInCny: 0,
        missingSettlementRate: false,
      }
      const amount = row.amount_value
      if (amount != null && Number.isFinite(amount) && amount > 0) {
        let amountInCny = amount
        if (row.market === 'HK') {
          const settlementRate = Number(row.raw_json?.['结算汇率'])
          if (!Number.isFinite(settlementRate) || settlementRate <= 0) {
            group.missingSettlementRate = true
            amountInCny = 0
          } else {
            amountInCny *= settlementRate
          }
        }
        group.turnoverInCny += amountInCny
      }
      if (row.fee_value != null && Number.isFinite(row.fee_value)) {
        group.fee += row.fee_value
      }
      groups.set(key, group)
    }

    historicalTradesByTarget.value = Object.fromEntries(
      [...tradesByTarget].map(([key, trades]) => [
        key,
        trades.sort((left, right) => {
          const leftTime = `${left.trade_date} ${left.trade_time}`
          const rightTime = `${right.trade_date} ${right.trade_time}`
          return rightTime.localeCompare(leftTime)
        }),
      ]),
    )
    historicalRoundTripRates.value = Object.fromEntries(
      [...groups].flatMap(([key, group]) => {
        if (group.missingSettlementRate || group.turnoverInCny <= 0) return []
        return [[key, (group.fee / group.turnoverInCny) * 2]]
      }),
    )
  } catch (error) {
    console.warn('Failed to load historical round-trip fee rates:', error)
  }
}

async function loadCurrentPrices(): Promise<void> {
  await refreshCurrentPrices()
  const quotePage = await fetchLatestEastmoneyMarketQuotes().catch(() => ({ items: [] }))
  for (const quote of quotePage.items) {
    const key = `${quote.market}.${quote.symbol}`
    if (quote.price === null || currentPrices.value[key]) continue
    currentPrices.value[key] = {
      price: quote.price,
      updateTime: quote.update_time,
      fetchedAt: quote.fetched_at,
    }
  }
  await Promise.all(
    calculators.value
      .filter((calculator) => !currentPrices.value[targetKey(calculator)])
      .map((calculator) => loadCurrentPriceForTarget(calculator)),
  )
}

async function refreshCurrentPrices(): Promise<void> {
  if (quoteRefreshInFlight || document.hidden) return
  quoteRefreshInFlight = true
  let nextRefreshMs = QUOTE_REFRESH_RETRY_MS
  try {
    const result = await syncEastmoneyCalculatorMarketQuotes()
    for (const quote of result.items) {
      if (quote.price === null) continue
      currentPrices.value[`${quote.market}.${quote.symbol}`] = {
        price: quote.price,
        updateTime: quote.update_time,
        fetchedAt: quote.fetched_at,
      }
    }
    const fetchedTimes = result.items
      .map((quote) => quote.fetched_at)
      .filter((value) => Number.isFinite(value) && value > 0)
    if (result.target_count === 0) {
      nextRefreshMs = result.ttl_seconds * 1000
    } else if (fetchedTimes.length && result.error_count === 0) {
      const oldestFetchedAt = Math.min(...fetchedTimes) * 1000
      nextRefreshMs = Math.max(1_000, oldestFetchedAt + result.ttl_seconds * 1000 - Date.now())
    }
  } catch (error) {
    console.warn('Failed to refresh calculator prices:', error)
  } finally {
    quoteRefreshInFlight = false
    quoteRefreshDelayMs = nextRefreshMs
    if (quoteRefreshStarted && !document.hidden) scheduleQuoteRefresh(nextRefreshMs)
  }
}

function handleVisibilityChange(): void {
  if (!document.hidden) void refreshCurrentPrices()
}

function startQuoteRefresh(): void {
  if (quoteRefreshStarted) return
  quoteRefreshStarted = true
  document.addEventListener('visibilitychange', handleVisibilityChange)
  scheduleQuoteRefresh(quoteRefreshDelayMs)
}

function scheduleQuoteRefresh(delayMs: number): void {
  if (quoteRefreshTimer) clearTimeout(quoteRefreshTimer)
  quoteRefreshTimer = setTimeout(() => {
    quoteRefreshTimer = null
    void refreshCurrentPrices()
  }, delayMs)
}

async function loadCurrentPriceForTarget(target: EastmoneyCalculatorTarget): Promise<void> {
  const key = targetKey(target)
  const startDate = new Date()
  startDate.setDate(startDate.getDate() - 45)
  try {
    const history = await fetchEastmoneyAkshareHistory({
      ...target,
      period: 'daily',
      start_date: startDate.toISOString().slice(0, 10),
      refresh: false,
    })
    const latest = history.items[history.items.length - 1]
    if (latest?.close === null || latest?.close === undefined) return
    const existing = currentPrices.value[key]
    if (existing && existing.updateTime.slice(0, 10) >= latest.date) return
    currentPrices.value[key] = {
      price: latest.close,
      updateTime: latest.date,
      fetchedAt: 0,
    }
  } catch (error) {
    console.warn(`Failed to load current price for ${key}:`, error)
  }
}

async function persistWorkspace(): Promise<void> {
  if (!ready.value) return
  if (saveInFlight) {
    saveQueued = true
    return
  }
  saveInFlight = true
  saveError.value = false
  try {
    const snapshot = JSON.parse(JSON.stringify(calculators.value)) as EastmoneyCalculatorItem[]
    await saveEastmoneyCalculatorWorkspace(snapshot)
  } catch (error) {
    console.error('Failed to save calculator workspace:', error)
    saveError.value = true
  } finally {
    saveInFlight = false
    if (saveQueued) {
      saveQueued = false
      void persistWorkspace()
    }
  }
}

function scheduleSave(): void {
  if (!ready.value) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveTimer = null
    void persistWorkspace()
  }, 500)
}

watch(calculators, scheduleSave, { deep: true })

onMounted(() => void loadWorkspace())

onBeforeUnmount(() => {
  if (saveTimer) clearTimeout(saveTimer)
  if (quoteRefreshTimer) clearTimeout(quoteRefreshTimer)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})

const usedTargetKeys = computed(() => new Set(calculators.value.map(targetKey)))
const availableTargets = computed(() => targets.value.filter((target) => !usedTargetKeys.value.has(targetKey(target))))

function parsePrice(value: string): ParsedPrice | null {
  const match = value.trim().match(/^(\d+)(?:\.(\d*))?$/)
  if (!match) return null

  const decimals = match[2]?.length ?? 0
  return {
    decimals,
    scaledValue: BigInt(`${match[1]}${match[2] ?? ''}`),
  }
}

function formatScaledPrice(basePrice: string, multiplier: number): string {
  const parsed = parsePrice(basePrice)
  if (!parsed) return '—'

  const multiplied = parsed.scaledValue * BigInt(multiplier)
  const rounded = (multiplied + 50n) / 100n
  if (parsed.decimals === 0) return rounded.toString()

  const scale = 10n ** BigInt(parsed.decimals)
  const integerPart = rounded / scale
  const decimalPart = (rounded % scale).toString().padStart(parsed.decimals, '0')
  return `${integerPart}.${decimalPart}`
}

function priceTick(calculator: EastmoneyCalculatorItem): { decimals: number; value: number } {
  const isExchangeFund = (
    (calculator.market === 'SZ' && /^(15|16)/.test(calculator.symbol))
    || (calculator.market === 'SH' && /^(50|51|52|56|58)/.test(calculator.symbol))
  )
  const decimals = isExchangeFund ? 3 : 2
  return { decimals, value: 10 ** -decimals }
}

function currentPriceMarker(calculator: EastmoneyCalculatorItem) {
  const current = currentPrices.value[targetKey(calculator)]
  const base = Number(calculator.base_price)
  if (!current || !Number.isFinite(base) || base <= 0) return null
  const ratio = current.price / base
  const position = Math.min(1, Math.max(0, (ratio - 0.8) / 0.4))
  const tick = priceTick(calculator)
  const roundTripRate = historicalRoundTripRates.value[targetKey(calculator)]
  const rawFeeGap = roundTripRate ? current.price * roundTripRate : 0
  const roundedFeeGap = rawFeeGap > 0
    ? Math.ceil(rawFeeGap / tick.value - 1e-12) * tick.value
    : 0
  return {
    left: `${5.5 + position * 89}%`,
    price: current.price.toFixed(tick.decimals),
    title: [
      `行情时间：${current.updateTime || '未知'}`,
      roundTripRate
        ? `来回手续费价差：${current.price} × ${formatCompactSignificant(roundTripRate * 100, 4)}%`
        : '',
    ].filter(Boolean).join('；'),
    boundary: ratio < 0.8 ? '<80%' : ratio > 1.2 ? '>120%' : '',
    feeGap: roundedFeeGap > 0 ? roundedFeeGap.toFixed(tick.decimals) : '',
  }
}

function openNewDialog(): void {
  selectedTargetKey.value = availableTargets.value[0] ? targetKey(availableTargets.value[0]) : ''
  newDialogVisible.value = true
}

function addCalculator(): void {
  const target = targets.value.find((item) => targetKey(item) === selectedTargetKey.value)
  if (!target || usedTargetKeys.value.has(targetKey(target))) return
  const importedTrades = historyByTarget.value[targetKey(target)] ?? []
  calculators.value.push({
    id: createId(),
    ...target,
    base_price: importedTrades[0]?.price ?? '',
    trades: importedTrades.map((trade) => ({ ...trade })),
  })
  void loadCurrentPriceForTarget(target)
  newDialogVisible.value = false
}

function historicalTrades(calculator: EastmoneyCalculatorItem): EastmoneyTradeRecord[] {
  return (historicalTradesByTarget.value[targetKey(calculator)] ?? []).slice(0, 10)
}

function historicalTradeTime(trade: EastmoneyTradeRecord): string {
  return [trade.trade_date, trade.trade_time].filter(Boolean).join(' ') || '—'
}

function historicalTradeQuantity(trade: EastmoneyTradeRecord): string {
  const quantity = trade.quantity.trim().replace(/^[+-]/, '')
  if (!quantity) return '—'
  return trade.direction.includes('卖') ? `-${quantity}` : quantity
}

async function removeCalculator(calculator: EastmoneyCalculatorItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除“${calculator.name}（${targetKey(calculator)}）”吗？`,
      '删除计算器',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }

  calculators.value = calculators.value.filter((row) => row.id !== calculator.id)
}
</script>

<template>
  <main v-loading="loading" class="stock-calculator-page">
    <header class="page-header">
      <div>
        <h1>计算器</h1>
        <p>输入基数价格，查看上下 5%、10%、15%、20% 的价格点位。</p>
      </div>
      <div class="page-actions">
        <span v-if="saveError" class="save-error">自动保存失败，编辑后将重试</span>
        <el-button type="primary" plain :disabled="loading || !availableTargets.length" @click="openNewDialog">
          + 新建
        </el-button>
      </div>
    </header>

    <div class="calculator-list">
      <section
        v-for="calculator in calculators"
        :key="calculator.id"
        class="calculator-row"
      >
        <div class="calculator-label">
          <strong>{{ calculator.name }}</strong>
          <span>{{ targetKey(calculator) }}</span>
          <span v-if="!parsePrice(calculator.base_price)" class="input-error">价格格式不正确</span>
        </div>

        <div class="axis-scroll">
          <div class="price-axis" :class="{ 'has-error': !parsePrice(calculator.base_price) }">
            <div
              v-for="multiplier in multipliers"
              :key="multiplier"
              class="axis-point"
              :class="{
                'is-base': multiplier === 100,
                'is-lower': multiplier < 100,
                'is-higher': multiplier > 100,
              }"
            >
              <div class="point-value">
                <input
                  v-if="multiplier === 100"
                  v-model="calculator.base_price"
                  :aria-label="`${calculator.name}的基数价格`"
                  inputmode="decimal"
                  placeholder="26.200"
                  spellcheck="false"
                >
                <strong v-else>{{ formatScaledPrice(calculator.base_price, multiplier) }}</strong>
              </div>
              <span class="point-marker" />
              <span class="point-label">{{ multiplier }}%</span>
            </div>
            <div
              v-if="currentPriceMarker(calculator)"
              class="current-price-marker"
              :class="{
                'is-left-boundary': currentPriceMarker(calculator)!.boundary === '<80%',
                'is-right-boundary': currentPriceMarker(calculator)!.boundary === '>120%',
              }"
              :style="{ left: currentPriceMarker(calculator)!.left }"
              :title="currentPriceMarker(calculator)!.title"
            >
              <span class="current-price-pointer" />
              <span class="current-price-label">
                现价{{ currentPriceMarker(calculator)!.price }}
                <small v-if="currentPriceMarker(calculator)!.feeGap" class="fee-gap">
                  手续费{{ currentPriceMarker(calculator)!.feeGap }}
                </small>
              </span>
            </div>
          </div>
        </div>

        <div class="trade-history">
          <div class="trade-history-head">
            <span>最近交易</span>
          </div>
          <div class="trade-table-scroll">
            <table class="trade-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>价格</th>
                  <th title="正数为买入，负数为卖出">交易数量（+买入 / −卖出）</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!historicalTrades(calculator).length">
                  <td class="empty-trades" colspan="3">暂无历史成交</td>
                </tr>
                <tr v-for="trade in historicalTrades(calculator)" v-else :key="trade.id">
                  <td class="trade-time">{{ historicalTradeTime(trade) }}</td>
                  <td class="trade-price">{{ trade.price || '—' }}</td>
                  <td
                    class="trade-quantity"
                    :class="{
                      'is-buy': trade.direction.includes('买'),
                      'is-sell': trade.direction.includes('卖'),
                    }"
                  >
                    {{ historicalTradeQuantity(trade) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <button
          class="remove-button"
          type="button"
          :aria-label="`删除${calculator.name}`"
          :title="`删除${calculator.name}`"
          @click="removeCalculator(calculator)"
        >
          −
        </button>
      </section>
    </div>

    <el-dialog v-model="newDialogVisible" title="选择股票" width="420px">
      <el-select v-model="selectedTargetKey" class="target-select" placeholder="请选择股票" filterable>
        <el-option
          v-for="target in availableTargets"
          :key="targetKey(target)"
          :label="`${targetKey(target)} ${target.name}`"
          :value="targetKey(target)"
        />
      </el-select>
      <template #footer>
        <el-button @click="newDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!selectedTargetKey" @click="addCalculator">新建</el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.stock-calculator-page {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  overflow: auto;
  padding: 24px 28px;
}

.page-header {
  align-items: flex-start;
  display: flex;
  gap: 24px;
  justify-content: space-between;
}

.page-header h1 {
  color: #172033;
  font-size: 22px;
  margin: 0;
}

.page-header p {
  color: #607086;
  font-size: 14px;
  margin: 8px 0 0;
}

.page-actions {
  align-items: center;
  display: flex;
  gap: 10px;
}

.save-error {
  color: #d92d20;
  font-size: 12px;
}

.calculator-list {
  margin-top: 36px;
  min-width: 0;
}

.calculator-row {
  align-items: center;
  border-bottom: 1px solid #edf0f5;
  display: grid;
  gap: 14px;
  grid-template-columns: 136px minmax(0, 1120px) 28px;
  max-width: 1312px;
  padding: 28px 0;
}

.calculator-row:first-child {
  padding-top: 12px;
}

.calculator-label {
  align-self: center;
  display: grid;
  gap: 4px;
  padding: 0 8px;
}

.calculator-label strong {
  color: #172033;
  font-size: 15px;
  font-weight: 600;
}

.calculator-label > span:not(.input-error) {
  color: #667085;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.axis-scroll {
  min-width: 0;
  overflow-x: auto;
  padding: 12px 6px 14px;
}

.trade-history {
  grid-column: 2;
  min-width: 0;
  padding: 2px 6px 0;
}

.trade-history-head {
  align-items: center;
  display: flex;
  justify-content: space-between;
  max-width: 590px;
}

.trade-history-head span {
  color: #475467;
  font-size: 13px;
  font-weight: 600;
}

.trade-table-scroll {
  max-width: 100%;
  overflow-x: auto;
}

.trade-table {
  border-collapse: collapse;
  color: #344054;
  font-size: 13px;
  margin-top: 4px;
  table-layout: auto;
  width: max-content;
}

.trade-table th {
  background: #f8fafc;
  color: #667085;
  font-weight: 500;
  height: 30px;
  padding: 0 8px;
  position: sticky;
  text-align: left;
  top: 0;
  white-space: nowrap;
  z-index: 1;
}

.trade-table td {
  border-bottom: 1px solid #edf0f5;
  font-variant-numeric: tabular-nums;
  height: 30px;
  padding: 0 8px;
  white-space: nowrap;
}

.trade-time {
  min-width: 174px;
}

.trade-price {
  min-width: 72px;
}

.trade-quantity {
  min-width: 184px;
}

.trade-quantity.is-buy {
  color: #b42318;
}

.trade-quantity.is-sell {
  color: #067647;
}

.empty-trades {
  color: #98a2b3;
  height: 34px;
  padding-left: 8px !important;
}

.trade-remove-button:hover {
  background: #fef3f2;
}

.price-axis {
  display: grid;
  grid-template-columns: repeat(9, minmax(88px, 1fr));
  min-width: 792px;
  padding-bottom: 36px;
  position: relative;
}

.price-axis::before {
  background: #cfd7e3;
  content: '';
  height: 2px;
  left: 5.5%;
  position: absolute;
  right: 5.5%;
  top: 47px;
}

.axis-point {
  align-items: center;
  display: flex;
  flex-direction: column;
  min-width: 0;
  position: relative;
}

.point-value {
  align-items: center;
  display: flex;
  height: 32px;
  justify-content: center;
  margin-bottom: 8px;
}

.point-value strong {
  color: #344054;
  font-size: 15px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.is-lower .point-value strong {
  color: #067647;
}

.is-higher .point-value strong {
  color: #b42318;
}

.point-value input {
  background: #fff;
  border: 1px solid #409eff;
  border-radius: 4px;
  box-shadow: 0 0 0 2px rgb(64 158 255 / 10%);
  box-sizing: border-box;
  color: #172033;
  font: 600 16px/1 system-ui, sans-serif;
  font-variant-numeric: tabular-nums;
  height: 34px;
  outline: none;
  text-align: center;
  width: 98px;
}

.has-error .point-value input {
  border-color: #d92d20;
  box-shadow: 0 0 0 2px rgb(217 45 32 / 10%);
}

.point-marker {
  background: #fff;
  border: 2px solid #98a2b3;
  border-radius: 50%;
  box-sizing: border-box;
  height: 14px;
  position: relative;
  width: 14px;
}

.is-base .point-marker {
  background: #409eff;
  border-color: #409eff;
  height: 16px;
  margin-top: -1px;
  width: 16px;
}

.point-label {
  color: #667085;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  margin-top: 9px;
}

.is-base .point-label {
  color: #172033;
  font-weight: 600;
}

.current-price-marker {
  align-items: center;
  display: flex;
  flex-direction: column;
  position: absolute;
  top: 78px;
  transform: translateX(-50%);
  z-index: 2;
}

.current-price-marker.is-left-boundary {
  align-items: flex-start;
  transform: translateX(-5px);
}

.current-price-marker.is-right-boundary {
  align-items: flex-end;
  transform: translateX(calc(-100% + 5px));
}

.current-price-pointer {
  border-bottom: 7px solid #d97706;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  height: 0;
  width: 0;
}

.current-price-label {
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 3px;
  color: #9a3412;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  line-height: 22px;
  margin-top: 2px;
  padding: 0 6px;
  white-space: nowrap;
}

.current-price-label small {
  font-size: 10px;
  margin-left: 2px;
}

.current-price-label .fee-gap {
  font-size: inherit;
  margin-left: 6px;
}

.input-error {
  color: #d92d20;
  font-size: 12px;
  padding-left: 8px;
}

.target-select {
  width: 100%;
}

.remove-button {
  align-items: center;
  align-self: center;
  background: transparent;
  border: 0;
  border-radius: 4px;
  color: #d92d20;
  cursor: pointer;
  display: flex;
  font-size: 22px;
  height: 28px;
  justify-content: center;
  padding: 0;
  width: 28px;
}

.remove-button:hover {
  background: #fef3f2;
}

@media (max-width: 900px) {
  .stock-calculator-page {
    padding: 20px 16px;
  }

  .calculator-row {
    gap: 8px;
    grid-template-columns: minmax(112px, 1fr) 28px;
  }

  .calculator-label {
    grid-column: 1;
  }

  .axis-scroll {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  .trade-history {
    grid-column: 1 / -1;
    grid-row: 3;
  }

  .remove-button {
    grid-column: 2;
    grid-row: 1;
  }
}
</style>

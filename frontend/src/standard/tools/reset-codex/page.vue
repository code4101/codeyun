<template>
  <div class="page">
    <div class="head">
      <h1>本机 AI 模型与额度</h1>
      <span v-if="switching" class="busy">切换中…</span>
    </div>

    <div class="picker">
      <button class="apply" type="button" :disabled="switching || !canApply" @click="applySwitch">
        {{ applyLabel }}
      </button>
      <select v-model="selectedProvider" :disabled="switching">
        <option v-for="provider in providers" :key="provider.id" :value="provider.id">
          {{ provider.label }}
        </option>
      </select>
      <a
        v-if="selectedProvider === 'deepseek'"
        class="help"
        :href="DOC_URL"
        target="_blank"
        rel="noopener noreferrer"
        title="查看 DeepSeek 官方文档"
      >?</a>
    </div>

    <div class="observed-row">
      <button class="refresh" type="button" :disabled="loadingQuota" @click="refreshAll">
        {{ loadingQuota ? '采集中…' : '刷新' }}
      </button>
      <span class="observed">{{ latestObservedLabel }} · 每个整点自动采集，失败后 5 分钟重试</span>
    </div>

    <p v-if="selectedProvider === 'opencode' && status && !status.opencode_proxy_running" class="hint">
      OpenCode 代理未运行，请先在「集群 / 运行」里启动 opencode-proxy 服务。
    </p>

    <section ref="codexSection" class="quota">
      <div class="quota-head">
        <a class="quota-title" :href="CODEX_USAGE_URL" target="_blank" rel="noopener noreferrer">Codex 账号额度</a>
        <span
          v-if="quotaGroups.length && quotaError"
          class="quota-badge"
          :title="`最近一次余额读取失败：${quotaError}`"
        >!</span>
        <a
          class="status-link"
          href="https://status.openai.com/"
          target="_blank"
          rel="noopener noreferrer"
          title="查看 OpenAI 官方服务状态"
        >OpenAI 状态 ↗</a>
      </div>

      <div v-if="quotaGroups.length" class="quota-rows">
        <div v-for="group in quotaGroups" :key="group.id" class="quota-row">
          <span class="quota-name">{{ group.name }}</span>
          <span class="quota-values">
            <span
              v-for="window in group.windows"
              :key="window.label"
              class="quota-value"
              :class="percentClass(window.remaining_percent)"
              :title="window.reset_at ? `重置 ${formatClock(window.reset_at)}` : ''"
            >
              <span v-if="window.label" class="quota-window">{{ window.label }}</span>
              <span class="quota-percent">剩余 {{ window.remaining_percent }}%</span>
            </span>
          </span>
        </div>
      </div>
      <p v-else class="quota-empty">{{ quotaError || '点击「刷新」读取实时额度' }}</p>

      <QuotaLineChart v-if="chartData" :data="chartData" />
    </section>

    <section ref="opencodeSection" class="quota">
      <div class="quota-head">
        <a class="quota-title" :href="OPENCODE_GO_URL" target="_blank" rel="noopener noreferrer">OpenCode Go</a>
        <span
          v-if="opencode.windows.length && opencode.error"
          class="quota-badge"
          :title="`最近一次余额读取失败：${opencode.error}`"
        >!</span>
      </div>
      <div v-if="opencode.windows.length" class="quota-rows">
        <div class="quota-row">
          <span class="quota-name">套餐余额</span>
          <span class="quota-values">
            <span
              v-for="window in opencode.windows"
              :key="window.label"
              class="quota-value"
              :class="percentClass(window.remaining_percent)"
              :title="window.reset_at ? `重置 ${formatClock(window.reset_at)}` : ''"
            >
              <span class="quota-window">{{ window.label }}</span>
              <span class="quota-percent">剩余 {{ window.remaining_percent }}%</span>
            </span>
          </span>
        </div>
      </div>
      <p v-else class="quota-empty">{{ opencode.error || '未找到 OpenCode Go 凭证' }}</p>

      <QuotaLineChart v-if="opencodeChartData" :data="opencodeChartData" />
    </section>

    <section ref="deepseekSection" class="quota">
      <div class="quota-head">
        <a class="quota-title" :href="DEEPSEEK_PLATFORM_URL" target="_blank" rel="noopener noreferrer">DeepSeek 余额</a>
        <span
          v-if="deepseek.balances.length && deepseek.error"
          class="quota-badge"
          :title="`最近一次余额读取失败：${deepseek.error}`"
        >!</span>
      </div>
      <div v-if="deepseek.balances.length" class="quota-rows">
        <div v-for="item in deepseek.balances" :key="item.currency" class="quota-row">
          <span class="quota-name">总余额{{ item.currency ? `（${item.currency}）` : '' }}</span>
          <span class="quota-values">
            <span class="quota-value">¥{{ item.total_balance }}</span>
          </span>
        </div>
      </div>
      <p v-else class="quota-empty">{{ deepseek.error || '未找到可用的 DeepSeek API Key' }}</p>

      <QuotaLineChart v-if="deepseekChartData" :data="deepseekChartData" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import QuotaLineChart from './QuotaLineChart.vue'
import type { LineChartData, LineChartPace } from './chartTypes'

import {
  fetchCodexQuota,
  fetchCodexSetupStatus,
  fetchDeepSeekBalance,
  fetchOpenCodeUsage,
  refreshCodexQuota,
  refreshDeepSeekBalance,
  refreshOpenCodeUsage,
  switchCodexSetup,
  type BalanceWindow,
  type CodexQuotaWindowHistory,
  type DeepSeekBalanceResponse,
  type CodexProviderInfo,
  type CodexQuotaGroup,
  type CodexQuotaResponse,
  type CodexSetupStatus,
  type OpenCodeUsageResponse,
} from '@/api/codexSetup'

const DOC_URL = 'https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/codex'
const CODEX_USAGE_URL = 'https://chatgpt.com/codex/cloud/settings/analytics#usage'
const DEEPSEEK_PLATFORM_URL = 'https://platform.deepseek.com/usage'
const OPENCODE_GO_URL = 'https://opencode.ai/zh/go'
const FALLBACK_PROVIDERS: CodexProviderInfo[] = [
  { id: 'openai', label: 'OpenAI', models: [] },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    models: [
      { id: 'deepseek-flash', label: 'DeepSeek Flash' },
      { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
    ],
  },
]

const status = ref<CodexSetupStatus | null>(null)
const switching = ref(false)
const selectedProvider = ref('openai')
const quotaGroups = ref<CodexQuotaGroup[]>([])
const generalWindow = ref<CodexQuotaResponse['general_window']>(null)
const observedAt = ref('')
const quotaError = ref('')
const loadingQuota = ref(false)
const opencode = ref<OpenCodeUsageResponse>({ available: false, windows: [], monthly_window: null, observed_at: '', error: '' })
const deepseek = ref<DeepSeekBalanceResponse>({ available: false, is_available: false, balances: [], observed_at: '', error: '' })

const providers = computed<CodexProviderInfo[]>(() => (
  status.value?.providers?.length ? status.value.providers : FALLBACK_PROVIDERS
))

// Allow re-applying the current provider too: re-running it rewrites the config
// and regenerates the catalog, which is handy when debugging.  Only block until
// the status is loaded or while a switch is already in flight.
const canApply = computed(() => Boolean(status.value))

const isReapply = computed(() => (
  Boolean(status.value) && selectedProvider.value === status.value?.provider
))

const applyLabel = computed(() => (isReapply.value ? '重新应用当前供应商' : '切换 Codex 模型供应商'))

const latestObservedLabel = computed(() => {
  let best = ''
  let bestTime = Number.NEGATIVE_INFINITY
  for (const value of [observedAt.value, opencode.value.observed_at, deepseek.value.observed_at]) {
    const time = new Date(value).getTime()
    if (Number.isFinite(time) && time > bestTime) {
      bestTime = time
      best = value
    }
  }
  return best ? `最近采集 ${formatClock(best)}` : '尚未采集'
})

function percentWindowToChart(window: CodexQuotaWindowHistory | null): LineChartData | null {
  if (!window || !window.window_start || !window.window_end) {
    return null
  }
  return {
    points: (window.points ?? []).map((point) => ({ at: point.at, value: point.remaining_percent })),
    windowStart: window.window_start,
    windowEnd: window.window_end,
    resetAt: window.reset_at || undefined,
    unit: '%',
    max: 100,
    paces: buildPaces(window),
  }
}

function formatPeriod(minutes: number) {
  if (minutes % 1440 === 0) {
    return `${minutes / 1440} 天`
  }
  if (minutes % 60 === 0) {
    return `${minutes / 60} 小时`
  }
  return `${minutes} 分钟`
}

// Each reset cycle has its own start and reset, and the timer only starts on
// first use after a reset, so the previous cycle's end is never the next cycle's
// start. Build one even-burn reference line per cycle from the backend periods.
function buildPaces(window: CodexQuotaWindowHistory): LineChartPace[] {
  const periodMinutes = Number(window.period_minutes || 0)
  if (periodMinutes <= 0) {
    return []
  }
  const label = `理论匀速消耗：${formatPeriod(periodMinutes)} 100% → 0%`
  const periods = window.periods ?? []
  if (periods.length) {
    return periods
      .filter((item) => item.start_at && item.reset_at)
      .map((item) => ({
        startAt: item.start_at,
        endAt: item.reset_at,
        startValue: 100,
        endValue: 0,
        label,
      }))
  }
  // Older snapshots predate `periods`; fall back to a single cycle.
  if (!window.reset_at) {
    return []
  }
  const end = new Date(window.reset_at).getTime()
  if (!Number.isFinite(end)) {
    return []
  }
  return [{
    startAt: new Date(end - periodMinutes * 60_000).toISOString(),
    endAt: window.reset_at,
    startValue: 100,
    endValue: 0,
    label,
  }]
}

function balanceWindowToChart(window: BalanceWindow | null): LineChartData | null {
  if (!window || !window.window_start || !window.window_end) {
    return null
  }
  return {
    points: (window.points ?? []).map((point) => ({ at: point.at, value: point.value })),
    windowStart: window.window_start,
    windowEnd: window.window_end,
    unit: '¥',
    max: null,
    paces: [],
  }
}

const chartData = computed(() => percentWindowToChart(generalWindow.value))
const opencodeChartData = computed(() => percentWindowToChart(opencode.value.monthly_window))
const deepseekChartData = computed(() => balanceWindowToChart(deepseek.value.total_window))

function percentClass(remaining: number) {
  if (remaining < 10) {
    return 'quota-low'
  }
  if (remaining < 30) {
    return 'quota-warn'
  }
  return ''
}

function formatClock(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const pad = (input: number) => String(input).padStart(2, '0')
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function getErrorMessage(error: unknown) {
  if (typeof error === 'object' && error && 'response' in error) {
    const maybeError = error as {
      response?: { data?: { detail?: string; message?: string } }
      message?: string
    }
    return maybeError.response?.data?.detail
      || maybeError.response?.data?.message
      || maybeError.message
      || '请求失败'
  }
  if (error instanceof Error) {
    return error.message
  }
  return '请求失败'
}

function syncSelection() {
  if (status.value) {
    selectedProvider.value = status.value.provider
  }
}

const codexSection = ref<HTMLElement | null>(null)
const opencodeSection = ref<HTMLElement | null>(null)
const deepseekSection = ref<HTMLElement | null>(null)

// Keep the picker and the quota charts in sync: the sections follow the picker
// order, and choosing a provider scrolls its section into view.
const SECTION_BY_PROVIDER: Record<string, () => HTMLElement | null> = {
  openai: () => codexSection.value,
  opencode: () => opencodeSection.value,
  deepseek: () => deepseekSection.value,
}

watch(selectedProvider, async (value) => {
  if (!value) {
    return
  }
  await nextTick()
  SECTION_BY_PROVIDER[value]?.()?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
})

function applyQuota(quota: CodexQuotaResponse) {
  quotaGroups.value = quota.groups ?? []
  generalWindow.value = quota.general_window ?? null
  observedAt.value = quota.observed_at || ''
  quotaError.value = quota.error || (quota.groups?.length ? '' : '点击「刷新」读取实时额度')
}

async function loadStatus() {
  try {
    status.value = await fetchCodexSetupStatus()
    syncSelection()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

async function loadQuota() {
  try {
    applyQuota(await fetchCodexQuota())
  } catch (error) {
    quotaError.value = getErrorMessage(error)
  }
}

async function loadOpencode() {
  try {
    opencode.value = await fetchOpenCodeUsage()
  } catch (error) {
    opencode.value = { available: false, windows: [], monthly_window: null, observed_at: '', error: getErrorMessage(error) }
  }
}

async function loadDeepseek() {
  try {
    deepseek.value = await fetchDeepSeekBalance()
  } catch (error) {
    deepseek.value = { available: false, is_available: false, balances: [], observed_at: '', error: getErrorMessage(error) }
  }
}

async function refreshAll() {
  if (loadingQuota.value) {
    return
  }
  loadingQuota.value = true
  try {
    const quota = await refreshCodexQuota()
    applyQuota(quota)
    if (quota.error) {
      ElMessage.error(quota.error)
    } else {
      ElMessage.success('额度已更新')
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loadingQuota.value = false
  }
  try {
    opencode.value = await refreshOpenCodeUsage()
  } catch (error) {
    opencode.value = { available: false, windows: [], monthly_window: null, observed_at: '', error: getErrorMessage(error) }
  }
  try {
    deepseek.value = await refreshDeepSeekBalance()
  } catch (error) {
    deepseek.value = { available: false, is_available: false, balances: [], observed_at: '', error: getErrorMessage(error) }
  }
}

async function applySwitch() {
  if (switching.value || !canApply.value) {
    return
  }
  const providerLabel = providers.value.find((item) => item.id === selectedProvider.value)?.label
    ?? selectedProvider.value
  try {
    await ElMessageBox.confirm(
      isReapply.value
        ? `重新应用「${providerLabel}」？会重新写入本机配置并重启 Codex。`
        : `切换到「${providerLabel}」？会自动关闭并重新打开 Codex，模型在 Codex 内切换。`,
      isReapply.value ? '重新应用' : '切换 Codex',
      { confirmButtonText: isReapply.value ? '重新应用' : '切换', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  switching.value = true
  try {
    const result = await switchCodexSetup(selectedProvider.value)
    status.value = result.status
    syncSelection()
    const notice = result.notice?.replace(/^已还原为 OpenAI 默认配置：/, '')
    ElMessage({
      message: notice ? `${result.message}；${notice}` : result.message,
      type: notice ? 'warning' : 'success',
      duration: notice ? 10000 : 3000,
      showClose: Boolean(notice),
    })
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    switching.value = false
  }
}

onMounted(async () => {
  await Promise.allSettled([loadStatus(), loadQuota(), loadOpencode(), loadDeepseek()])
})
</script>

<style scoped>
.page {
  min-height: 100%;
  padding: 32px 28px 48px;
  background: #fff;
  color: #1f2937;
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
}

h1 {
  margin: 0;
  color: #111827;
  font-size: 20px;
  font-weight: 600;
}

.help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  color: #64748b;
  font-size: 12px;
  line-height: 1;
  text-decoration: none;
}

.help:hover {
  border-color: #94a3b8;
  color: #1e293b;
}

.busy {
  color: #2563eb;
  font-size: 13px;
}

.status-link {
  margin-left: auto;
  padding: 3px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  color: #475569;
  font-size: 12px;
  text-decoration: none;
}

.status-link:hover {
  border-color: #cbd5e1;
  color: #1e293b;
}

.observed-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.observed {
  color: #a8b3c2;
  font-size: 11px;
}

.refresh {
  padding: 6px 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  color: #475569;
  font-size: 13px;
  cursor: pointer;
}

.refresh:hover:not(:disabled) {
  border-color: #cbd5e1;
  color: #1e293b;
}

.refresh:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.picker {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 20px;
  flex-wrap: wrap;
}

.picker select {
  flex: none;
  width: auto;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  font-size: 14px;
}

.picker select:disabled {
  background: #f8fafc;
  color: #94a3b8;
}

.picker-note {
  flex: 1;
  color: #94a3b8;
  font-size: 13px;
}

.apply {
  flex: none;
  padding: 8px 16px;
  border: 1px solid #22c55e;
  border-radius: 8px;
  background: #22c55e;
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.apply:hover:not(:disabled) {
  background: #16a34a;
}

.apply:disabled {
  border-color: #e2e8f0;
  background: #f1f5f9;
  color: #94a3b8;
  cursor: not-allowed;
}

.hint {
  max-width: 420px;
  margin: 12px 0 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fef3c7;
  color: #92400e;
  font-size: 13px;
  line-height: 1.6;
}

.quota {
  max-width: 420px;
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px solid #f1f5f9;
}

.quota-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.quota-title {
  color: #334155;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
}

.quota-title:hover {
  text-decoration: underline;
}

.quota-observed {
  color: #94a3b8;
  font-size: 12px;
}

.quota-refresh {
  margin-left: auto;
  padding: 2px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #475569;
  font-size: 12px;
  cursor: pointer;
}

.quota-refresh:hover:not(:disabled) {
  border-color: #cbd5e1;
  color: #1e293b;
}

.quota-refresh:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.quota-rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 10px;
}

.quota-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
}

.quota-name {
  color: #64748b;
}

.quota-values {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.quota-value {
  color: #334155;
  white-space: nowrap;
}

.quota-window {
  margin-right: 4px;
  color: #94a3b8;
}

.quota-warn .quota-percent {
  color: #b45309;
}

.quota-low .quota-percent {
  color: #dc2626;
}

.quota-empty {
  margin: 10px 0 0;
  color: #94a3b8;
  font-size: 13px;
  line-height: 1.6;
}

.quota-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #f59e0b;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  cursor: help;
}

.chart {
  width: 100%;
  height: auto;
  margin-top: 12px;
}

.chart-grid {
  stroke: #eef2f7;
  stroke-width: 1;
}

.chart-reset {
  stroke: #cbd5e1;
  stroke-width: 1;
  stroke-dasharray: 3 3;
}

.chart-line {
  fill: none;
  stroke: #22c55e;
  stroke-width: 2;
}

.chart-dot {
  fill: #22c55e;
}

.chart-axis {
  fill: #94a3b8;
  font-size: 9px;
}

.chart-reset-label {
  fill: #94a3b8;
}
</style>

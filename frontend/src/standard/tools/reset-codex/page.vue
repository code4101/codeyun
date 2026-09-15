<template>
  <div class="page">
    <div class="head">
      <h1>本机 AI 模型与额度</h1>
      <a
        class="help"
        :href="DOC_URL"
        target="_blank"
        rel="noopener noreferrer"
        title="查看 DeepSeek 官方文档"
      >?</a>
      <span v-if="switching" class="busy">切换中…</span>
    </div>

    <div class="picker">
      <select v-model="selectedProvider" :disabled="switching" @change="onProviderChange">
        <option v-for="provider in providers" :key="provider.id" :value="provider.id">
          {{ provider.label }}
        </option>
      </select>
      <select v-if="currentModels.length" v-model="selectedModel" :disabled="switching">
        <option v-for="model in currentModels" :key="model.id" :value="model.id">
          {{ model.label }}
        </option>
      </select>
      <span v-else class="picker-note">官方默认</span>
      <button class="apply" type="button" :disabled="switching || !canApply" @click="applySwitch">
        切换
      </button>
    </div>

    <section class="quota">
      <div class="quota-head">
        <span class="quota-title">Codex 账号额度</span>
        <span class="quota-observed">{{ observedLabel }}</span>
        <button class="quota-refresh" type="button" :disabled="loadingQuota" @click="refreshAll">
          {{ loadingQuota ? '采集中…' : '刷新' }}
        </button>
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

      <svg
        v-if="chart"
        class="chart"
        :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
        role="img"
        aria-label="通用余额变化"
      >
        <line
          v-for="line in chart.grid"
          :key="line.value"
          :x1="CHART_PAD_LEFT"
          :x2="CHART_W - CHART_PAD_RIGHT"
          :y1="line.y"
          :y2="line.y"
          class="chart-grid"
        />
        <text
          v-for="line in chart.grid"
          :key="`label-${line.value}`"
          :x="CHART_PAD_LEFT - 4"
          :y="line.y + 3"
          class="chart-axis"
          text-anchor="end"
        >{{ line.value }}%</text>

        <line
          v-if="chart.resetX !== null"
          :x1="chart.resetX"
          :x2="chart.resetX"
          :y1="CHART_PAD_TOP"
          :y2="CHART_H - CHART_PAD_BOTTOM"
          class="chart-reset"
        />
        <text
          v-if="chart.resetX !== null"
          :x="chart.resetX"
          :y="CHART_PAD_TOP - 1"
          class="chart-axis chart-reset-label"
          text-anchor="middle"
        >重置 {{ chart.resetLabel }}</text>

        <polyline v-if="chart.points.length > 1" :points="chart.polyline" class="chart-line" />
        <circle
          v-for="(point, index) in chart.points"
          :key="index"
          :cx="point.x"
          :cy="point.y"
          r="2.5"
          class="chart-dot"
        />

        <text :x="CHART_PAD_LEFT" :y="CHART_H - 5" class="chart-axis" text-anchor="start">
          {{ chart.startLabel }}
        </text>
        <text :x="CHART_W - CHART_PAD_RIGHT" :y="CHART_H - 5" class="chart-axis" text-anchor="end">
          {{ chart.endLabel }}
        </text>
      </svg>
    </section>

    <section class="quota">
      <div class="quota-head">
        <span class="quota-title">opencode Go</span>
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
      <p v-else class="quota-empty">{{ opencode.error || '未找到 opencode-go 凭证' }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  fetchCodexQuota,
  fetchCodexSetupStatus,
  fetchOpenCodeUsage,
  refreshCodexQuota,
  switchCodexSetup,
  type CodexProviderInfo,
  type CodexQuotaGroup,
  type CodexQuotaResponse,
  type CodexSetupStatus,
  type OpenCodeUsageResponse,
} from '@/api/codexSetup'

const DOC_URL = 'https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/codex'
const CHART_W = 400
const CHART_H = 118
const CHART_PAD_LEFT = 26
const CHART_PAD_RIGHT = 8
const CHART_PAD_TOP = 14
const CHART_PAD_BOTTOM = 20

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
const selectedModel = ref('')
const quotaGroups = ref<CodexQuotaGroup[]>([])
const generalWindow = ref<CodexQuotaResponse['general_window']>(null)
const observedAt = ref('')
const quotaError = ref('')
const loadingQuota = ref(false)
const opencode = ref<OpenCodeUsageResponse>({ available: false, windows: [], error: '' })

const providers = computed<CodexProviderInfo[]>(() => (
  status.value?.providers?.length ? status.value.providers : FALLBACK_PROVIDERS
))

const currentModels = computed(() => (
  providers.value.find((item) => item.id === selectedProvider.value)?.models ?? []
))

const canApply = computed(() => {
  const active = status.value
  if (!active) {
    return false
  }
  if (selectedProvider.value !== active.provider) {
    return true
  }
  return selectedProvider.value !== 'openai' && selectedModel.value !== active.model
})

const observedLabel = computed(() => (
  observedAt.value ? `最近采集 ${formatClock(observedAt.value)}` : '尚未采集'
))

const chart = computed(() => {
  const data = generalWindow.value
  if (!data || !data.window_start || !data.window_end) {
    return null
  }
  const start = new Date(data.window_start).getTime()
  const end = new Date(data.window_end).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return null
  }
  const plotW = CHART_W - CHART_PAD_LEFT - CHART_PAD_RIGHT
  const plotH = CHART_H - CHART_PAD_TOP - CHART_PAD_BOTTOM
  const x = (time: number) => CHART_PAD_LEFT + ((time - start) / (end - start)) * plotW
  const y = (percent: number) => CHART_PAD_TOP + (1 - percent / 100) * plotH

  const points = (data.points ?? [])
    .map((point) => ({ time: new Date(point.at).getTime(), percent: point.remaining_percent }))
    .filter((point) => Number.isFinite(point.time))
    .sort((a, b) => a.time - b.time)
    .map((point) => ({ x: x(point.time), y: y(point.percent) }))

  const resetTime = data.reset_at ? new Date(data.reset_at).getTime() : NaN
  return {
    points,
    polyline: points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' '),
    grid: [100, 50, 0].map((value) => ({ value, y: y(value) })),
    resetX: Number.isFinite(resetTime) ? x(resetTime) : null,
    resetLabel: Number.isFinite(resetTime) ? formatClock(data.reset_at) : '',
    startLabel: formatDay(start),
    endLabel: formatDay(end),
  }
})

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

function formatDay(value: number) {
  const date = new Date(value)
  return `${date.getMonth() + 1}/${date.getDate()}`
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
  const active = status.value
  if (!active) {
    return
  }
  selectedProvider.value = active.provider
  const models = providers.value.find((item) => item.id === active.provider)?.models ?? []
  selectedModel.value = models.some((item) => item.id === active.model)
    ? active.model
    : (models[0]?.id ?? '')
}

function onProviderChange() {
  const models = currentModels.value
  selectedModel.value = models[0]?.id ?? ''
}

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
    opencode.value = { available: false, windows: [], error: getErrorMessage(error) }
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
  void loadOpencode()
}

async function applySwitch() {
  if (switching.value || !canApply.value) {
    return
  }
  const providerLabel = providers.value.find((item) => item.id === selectedProvider.value)?.label
    ?? selectedProvider.value
  const target = selectedModel.value
    ? `${providerLabel} · ${currentModels.value.find((item) => item.id === selectedModel.value)?.label ?? selectedModel.value}`
    : providerLabel
  try {
    await ElMessageBox.confirm(
      `切换到「${target}」？会自动关闭并重新打开 Codex。`,
      '切换 Codex',
      { confirmButtonText: '切换', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  switching.value = true
  try {
    const result = await switchCodexSetup(selectedProvider.value, selectedModel.value || undefined)
    status.value = result.status
    syncSelection()
    ElMessage.success(result.message)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    switching.value = false
  }
}

onMounted(() => {
  void loadStatus()
  void loadQuota()
  void loadOpencode()
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

.picker {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 420px;
  margin-top: 20px;
}

.picker select {
  flex: 1;
  min-width: 0;
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

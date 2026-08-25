<template>
  <div class="temperature-page">
    <header class="page-header">
      <div>
        <h1>设备温度</h1>
        <p>CodeYun 本机采集 · CPU、GPU 与硬盘当前温度</p>
      </div>
      <div class="live-state" :class="snapshotStatusClass">
        <span class="state-dot" aria-hidden="true" />
        <span>{{ snapshotStatusText }}</span>
        <time v-if="snapshot?.observed_at">{{ formatObservedAt(snapshot.observed_at) }}</time>
        <el-button
          v-if="needsElevation"
          text
          type="primary"
          :loading="requestingElevation"
          @click="enableFullCollection"
        >
          读取完整温度
        </el-button>
      </div>
    </header>

    <section class="temperature-table" aria-live="polite">
      <div class="table-header">
        <span>设备</span>
        <span>传感器</span>
        <span>当前</span>
      </div>

      <div v-if="initialLoading" class="empty-state">正在读取本机温度…</div>
      <div v-else-if="!devices.length" class="empty-state">
        {{ snapshot?.message || '暂时没有可用的温度数据' }}
      </div>

      <article v-for="device in devices" :key="device.id" class="device-row">
        <div class="device-main">
          <span class="device-kind">{{ kindLabel(device.kind) }}</span>
          <strong>{{ device.name }}</strong>
          <span v-if="device.drive_letters.length" class="drive-letters">
            {{ device.drive_letters.join(' / ') }}
          </span>
        </div>

        <div v-if="device.sensors.length" class="sensor-list">
          <span v-for="sensor in device.sensors" :key="sensor.id" class="sensor-value">
            <span>{{ sensor.name }}</span>
            <strong>{{ formatTemperature(sensor.value) }}</strong>
          </span>
        </div>
        <div v-else class="sensor-unavailable">暂未读取到温度</div>

        <div class="device-temperature" :class="temperatureLevel(device)">
          <strong>{{ device.temperature == null ? '—' : formatTemperature(device.temperature) }}</strong>
          <span>{{ temperatureLevelText(device) }}</span>
        </div>
      </article>
    </section>

    <p v-if="statusNote" class="status-note" :class="snapshotStatusClass">
      {{ statusNote }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  getHardwareTemperatures,
  requestFullHardwareTemperatures,
  type TemperatureDevice,
  type TemperatureDeviceKind,
  type TemperatureSnapshot,
} from '@/api/hardwareTemperatures'

const snapshot = ref<TemperatureSnapshot | null>(null)
const initialLoading = ref(true)
const requestingElevation = ref(false)
let pollTimer: number | null = null

const devices = computed(() => snapshot.value?.devices ?? [])
const needsElevation = computed(() => snapshot.value?.status === 'partial' && !snapshot.value.elevated)

const snapshotStatusClass = computed(() => {
  const status = snapshot.value?.status
  if (status === 'ok') return 'is-ok'
  if (status === 'partial') return 'is-partial'
  return 'is-error'
})

const snapshotStatusText = computed(() => {
  const status = snapshot.value?.status
  if (status === 'ok') return '实时'
  if (status === 'partial') return '部分可用'
  if (status === 'stale') return '已中断'
  return '不可用'
})

const statusNote = computed(() => {
  if (!snapshot.value || snapshot.value.status === 'ok') return ''
  return snapshot.value.message
})

function kindLabel(kind: TemperatureDeviceKind) {
  return {
    cpu: 'CPU',
    gpu: 'GPU',
    storage: '硬盘',
    motherboard: '主板',
    other: '设备',
  }[kind]
}

function formatTemperature(value: number) {
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}°C`
}

function formatObservedAt(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function temperatureLevel(device: TemperatureDevice) {
  if (device.temperature == null) return 'is-unavailable'
  const warm = device.kind === 'storage' ? 65 : 80
  const hot = device.kind === 'storage' ? 70 : 90
  if (device.temperature >= hot) return 'is-hot'
  if (device.temperature >= warm) return 'is-warm'
  return 'is-normal'
}

function temperatureLevelText(device: TemperatureDevice) {
  const level = temperatureLevel(device)
  if (level === 'is-hot') return '高温'
  if (level === 'is-warm') return '偏热'
  if (level === 'is-normal') return '正常'
  return '未读取'
}

async function loadTemperatures() {
  try {
    snapshot.value = await getHardwareTemperatures()
  } catch {
    if (snapshot.value) {
      snapshot.value = {
        ...snapshot.value,
        status: 'stale',
        message: '温度接口暂时无法连接，保留最后一次读数',
      }
    }
  } finally {
    initialLoading.value = false
  }
}

async function enableFullCollection() {
  requestingElevation.value = true
  try {
    await requestFullHardwareTemperatures()
  } finally {
    window.setTimeout(() => {
      requestingElevation.value = false
      void loadTemperatures()
    }, 1200)
  }
}

onMounted(() => {
  void loadTemperatures()
  pollTimer = window.setInterval(() => void loadTemperatures(), 2000)
})

onBeforeUnmount(() => {
  if (pollTimer != null) window.clearInterval(pollTimer)
})
</script>

<style scoped>
.temperature-page {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  overflow: auto;
  padding: 24px 28px 36px;
  color: var(--el-text-color-primary);
}

.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  max-width: 1120px;
  margin-bottom: 22px;
}

.page-header h1 {
  margin: 0;
  font-size: 24px;
  line-height: 1.25;
  font-weight: 650;
}

.page-header p {
  margin: 5px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.live-state {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 24px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  white-space: nowrap;
}

.live-state time {
  margin-left: 5px;
  color: var(--el-text-color-placeholder);
  font-variant-numeric: tabular-nums;
}

.state-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-color-info);
}

.live-state.is-ok .state-dot { background: var(--el-color-success); }
.live-state.is-partial .state-dot { background: var(--el-color-warning); }
.live-state.is-error .state-dot { background: var(--el-color-danger); }

.temperature-table {
  width: min(1120px, 100%);
  border-top: 1px solid var(--el-border-color);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.table-header,
.device-row {
  display: grid;
  grid-template-columns: minmax(260px, 0.9fr) minmax(320px, 1.4fr) 112px;
  align-items: center;
  column-gap: 28px;
}

.table-header {
  min-height: 38px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.table-header span:last-child { text-align: right; }

.device-row {
  min-height: 84px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.device-main {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  align-items: center;
  gap: 5px 10px;
  min-width: 0;
}

.device-kind {
  color: var(--el-text-color-placeholder);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.05em;
}

.device-main strong {
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drive-letters {
  grid-column: 2;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.sensor-list {
  display: flex;
  flex-wrap: wrap;
  gap: 7px 18px;
}

.sensor-value {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.sensor-value strong {
  color: var(--el-text-color-primary);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.sensor-unavailable {
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}

.device-temperature {
  justify-self: end;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.device-temperature strong {
  font-size: 22px;
  line-height: 1;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}

.device-temperature span {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.device-temperature.is-normal strong { color: var(--el-color-success); }
.device-temperature.is-warm strong { color: var(--el-color-warning); }
.device-temperature.is-hot strong { color: var(--el-color-danger); }
.device-temperature.is-unavailable strong { color: var(--el-text-color-placeholder); }

.empty-state {
  padding: 48px 0;
  border-top: 1px solid var(--el-border-color-lighter);
  color: var(--el-text-color-secondary);
  font-size: 14px;
}

.status-note {
  width: min(1120px, 100%);
  margin: 12px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.status-note.is-error { color: var(--el-color-danger); }

@media (max-width: 820px) {
  .temperature-page { padding: 18px 16px 28px; }
  .page-header { align-items: flex-start; }
  .table-header { display: none; }
  .device-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px 18px;
    padding: 16px 0;
  }
  .sensor-list,
  .sensor-unavailable { grid-column: 1 / -1; grid-row: 2; }
  .device-temperature { grid-column: 2; grid-row: 1; }
}
</style>

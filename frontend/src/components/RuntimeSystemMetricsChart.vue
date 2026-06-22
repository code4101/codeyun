<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { LineChart, type LineSeriesOption } from 'echarts/charts';
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
  type GridComponentOption,
  type LegendComponentOption,
  type TooltipComponentOption,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import type { ComposeOption, ECharts } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import {
  fetchRuntimeSystemMetrics,
  type RuntimeSystemMetricsResponse,
} from '@/api/runtime';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

type ChartOption = ComposeOption<
  GridComponentOption | LegendComponentOption | TooltipComponentOption | LineSeriesOption
>;

const props = defineProps<{
  entryId: string;
}>();

const chartRef = ref<HTMLElement | null>(null);
const metrics = ref<RuntimeSystemMetricsResponse | null>(null);
const loading = ref(false);
const errorMessage = ref('');

let chart: ECharts | null = null;
let refreshTimer: number | null = null;
let resizeObserver: ResizeObserver | null = null;

const samples = computed(() => metrics.value?.samples || []);
const latest = computed(() => metrics.value?.latest || samples.value[samples.value.length - 1] || null);

const pad2 = (value: number) => String(value).padStart(2, '0');

const formatPercent = (value: number | null | undefined) => (
  typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)}%` : '-'
);

const formatBytes = (value: number | null | undefined) => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const digits = unitIndex <= 1 ? 0 : 1;
  return `${size.toFixed(digits)} ${units[unitIndex]}`;
};

const formatTime = (timestampSeconds: number | null | undefined) => {
  if (typeof timestampSeconds !== 'number' || !Number.isFinite(timestampSeconds)) return '';
  const date = new Date(timestampSeconds * 1000);
  if (!Number.isFinite(date.getTime())) return '';
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
};

const latestTimeLabel = computed(() => {
  const label = formatTime(latest.value?.sampled_at);
  return label ? `采样 ${label}` : '暂无采样';
});

const memoryUsageLabel = computed(() => {
  const item = latest.value;
  if (!item) return '-';
  return `${formatBytes(item.memory_used)} / ${formatBytes(item.memory_total)}`;
});

const displayErrorMessage = computed(() => {
  if (!errorMessage.value) return '';
  return samples.value.length ? '资源监控暂时失联，先保留最近采样' : '资源监控暂不可用';
});

const buildChartOption = (): ChartOption => {
  const cpuData = samples.value.map(sample => [sample.sampled_at * 1000, sample.cpu_percent]);
  const memoryData = samples.value.map(sample => [sample.sampled_at * 1000, sample.memory_percent]);
  const sampleByTime = new Map(samples.value.map(sample => [sample.sampled_at * 1000, sample]));

  return {
    animation: false,
    color: ['#2563eb', '#16a34a'],
    grid: {
      left: 38,
      right: 16,
      top: 30,
      bottom: 28,
    },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 16,
      itemHeight: 8,
      textStyle: {
        color: '#606266',
        fontSize: 12,
      },
      data: ['CPU', '内存'],
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      formatter(params: unknown) {
        const rows = Array.isArray(params) ? params : [params];
        const first = rows[0] as { value?: unknown } | undefined;
        const firstValue = Array.isArray(first?.value) ? first.value[0] : null;
        const timestamp = typeof firstValue === 'number' ? firstValue : Number(firstValue);
        const sample = sampleByTime.get(timestamp);
        const date = Number.isFinite(timestamp) ? new Date(timestamp) : null;
        const title = date && Number.isFinite(date.getTime())
          ? `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`
          : '';
        const valueRows = rows.map(row => {
          const item = row as { marker?: string; seriesName?: string; value?: unknown };
          const value = Array.isArray(item.value) ? Number(item.value[1]) : Number(item.value);
          return `${item.marker || ''}${item.seriesName || ''}: ${formatPercent(value)}`;
        });
        if (sample) {
          valueRows.push(`内存用量: ${formatBytes(sample.memory_used)} / ${formatBytes(sample.memory_total)}`);
        }
        return [title, ...valueRows].filter(Boolean).join('<br/>');
      },
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#dcdfe6' } },
      axisLabel: {
        color: '#909399',
        fontSize: 11,
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: {
        color: '#909399',
        fontSize: 11,
        formatter: '{value}%',
      },
      splitLine: {
        lineStyle: { color: '#eef2f7' },
      },
    },
    series: [
      {
        name: 'CPU',
        type: 'line',
        showSymbol: cpuData.length <= 1,
        symbolSize: 5,
        smooth: true,
        lineStyle: { width: 2 },
        data: cpuData,
      },
      {
        name: '内存',
        type: 'line',
        showSymbol: memoryData.length <= 1,
        symbolSize: 5,
        smooth: true,
        lineStyle: { width: 2 },
        data: memoryData,
      },
    ],
  };
};

const ensureChart = async () => {
  await nextTick();
  if (!chartRef.value) return;
  if (!chart) {
    chart = echarts.init(chartRef.value);
  }
  chart.setOption(buildChartOption(), true);
};

const loadMetrics = async (silent = false) => {
  if (!props.entryId) {
    metrics.value = null;
    return;
  }
  if (!silent) loading.value = true;
  try {
    const payload = await fetchRuntimeSystemMetrics(props.entryId, { hours: 24, limit: 1600 });
    metrics.value = payload;
    errorMessage.value = '';
    await ensureChart();
  } catch (err: any) {
    errorMessage.value = err.response?.data?.detail || '资源监控读取失败';
  } finally {
    if (!silent) loading.value = false;
  }
};

const stopPolling = () => {
  if (refreshTimer !== null) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
};

const startPolling = () => {
  stopPolling();
  refreshTimer = window.setInterval(() => {
    void loadMetrics(true);
  }, 60_000);
};

watch(
  () => props.entryId,
  async () => {
    metrics.value = null;
    await loadMetrics(false);
    startPolling();
  }
);

onMounted(async () => {
  await loadMetrics(false);
  startPolling();
  if (chartRef.value) {
    resizeObserver = new ResizeObserver(() => chart?.resize());
    resizeObserver.observe(chartRef.value);
  }
});

onUnmounted(() => {
  stopPolling();
  resizeObserver?.disconnect();
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <section class="system-monitor">
    <div class="system-monitor-header">
      <div class="runtime-section-title">
        <span>资源监控</span>
      </div>
      <div class="system-monitor-current">
        <span>CPU <strong>{{ formatPercent(latest?.cpu_percent) }}</strong></span>
        <span>内存 <strong>{{ formatPercent(latest?.memory_percent) }}</strong></span>
        <span class="system-monitor-memory">{{ memoryUsageLabel }}</span>
        <span class="system-monitor-time">{{ latestTimeLabel }}</span>
      </div>
    </div>

    <div class="system-monitor-body" v-loading="loading">
      <div ref="chartRef" class="system-monitor-chart" />
      <div v-if="!loading && !samples.length" class="system-monitor-empty">暂无采样</div>
      <div v-if="displayErrorMessage" class="system-monitor-error" :title="errorMessage">{{ displayErrorMessage }}</div>
    </div>
  </section>
</template>

<style scoped>
.system-monitor {
  margin-top: 16px;
}

.system-monitor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
}

.runtime-section-title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #303133;
  font-weight: 600;
}

.system-monitor-current {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 0;
  color: #606266;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.system-monitor-current strong {
  color: #1f2937;
  font-weight: 700;
}

.system-monitor-memory,
.system-monitor-time {
  color: #909399;
}

.system-monitor-body {
  position: relative;
  min-height: 190px;
  border-top: 1px solid #ebeef5;
  border-bottom: 1px solid #ebeef5;
  background: #fff;
}

.system-monitor-chart {
  width: 100%;
  height: 190px;
}

.system-monitor-empty,
.system-monitor-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  color: #909399;
  font-size: 13px;
}

.system-monitor-error {
  color: #f56c6c;
  background: rgba(255, 255, 255, 0.74);
}

@media (max-width: 720px) {
  .system-monitor-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }

  .system-monitor-current {
    justify-content: flex-start;
  }
}
</style>

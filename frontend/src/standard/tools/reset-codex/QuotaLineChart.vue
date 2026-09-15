<template>
  <div ref="containerRef" class="quota-chart" :style="{ height: `${height}px` }"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import type { ECharts } from 'echarts/core'
import { LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type { LineChartData } from './chartTypes'

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer])

const props = withDefaults(defineProps<{ data: LineChartData | null; height?: number }>(), {
  height: 140,
})

const containerRef = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

function formatClock(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const pad = (input: number) => String(input).padStart(2, '0')
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatValue(value: number, unit: string) {
  return unit === '%' ? `${value}%` : `${unit}${value}`
}

function buildOption(data: LineChartData | null) {
  if (!data || !data.windowStart || !data.windowEnd || !data.points.length) {
    return null
  }
  const start = new Date(data.windowStart).getTime()
  const end = new Date(data.windowEnd).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return null
  }
  const unit = data.unit ?? '%'

  const points = data.points
    .map((point) => ({ time: new Date(point.at).getTime(), value: point.value }))
    .filter((point) => Number.isFinite(point.time))
    .sort((a, b) => a.time - b.time)

  // Line uses every point; dots are thinned to the earliest point of each day.
  const seenDays = new Set<string>()
  const dotData: [number, number][] = []
  for (const point of points) {
    const date = new Date(point.time)
    const key = `${date.getFullYear()}/${date.getMonth()}/${date.getDate()}`
    if (!seenDays.has(key)) {
      seenDays.add(key)
      dotData.push([point.time, point.value])
    }
  }

  const yMax = typeof data.max === 'number' ? data.max : undefined
  const resetTime = data.resetAt ? new Date(data.resetAt).getTime() : NaN
  const markLine = Number.isFinite(resetTime)
    ? {
        symbol: 'none',
        silent: true,
        lineStyle: { color: '#cbd5e1', type: 'dashed', width: 1 },
        label: {
          formatter: `重置 ${formatClock(data.resetAt as string)}`,
          color: '#94a3b8',
          fontSize: 9,
          align: 'right',
        },
        data: [{ xAxis: resetTime }],
      }
    : undefined

  return {
    animation: false,
    grid: { left: 40, right: 8, top: 24, bottom: 22 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      valueFormatter: (value: unknown) => formatValue(Number(value), unit),
    },
    xAxis: {
      type: 'time',
      min: start,
      max: end,
      axisLabel: {
        color: '#94a3b8',
        fontSize: 9,
        showMinLabel: true,
        showMaxLabel: true,
        hideOverlap: true,
        formatter: (value: number) => {
          const date = new Date(value)
          return `${date.getMonth() + 1}/${date.getDate()}`
        },
      },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0,
      ...(yMax !== undefined ? { max: yMax } : {}),
      axisLabel: {
        color: '#94a3b8',
        fontSize: 9,
        formatter: (value: number) => formatValue(value, unit),
      },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#eef2f7' } },
    },
    series: [
      {
        type: 'line',
        showSymbol: false,
        smooth: false,
        lineStyle: { color: '#22c55e', width: 2 },
        areaStyle: { color: 'rgba(34,197,94,0.08)' },
        data: points.map((point) => [point.time, point.value]),
        markLine,
      },
      {
        type: 'scatter',
        symbolSize: 5,
        itemStyle: { color: '#22c55e' },
        data: dotData,
        silent: true,
      },
    ],
  }
}

function render() {
  if (!chart) {
    return
  }
  chart.setOption(buildOption(props.data) ?? { series: [] }, true)
}

onMounted(() => {
  if (!containerRef.value) {
    return
  }
  chart = echarts.init(containerRef.value)
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(containerRef.value)
})

watch(() => props.data, render, { deep: true })

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.quota-chart {
  width: 100%;
  margin-top: 8px;
}
</style>

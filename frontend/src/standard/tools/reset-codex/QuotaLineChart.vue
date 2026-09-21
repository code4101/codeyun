<template>
  <div>
    <div ref="containerRef" class="quota-chart" :style="{ height: `${height}px` }"></div>
    <p v-if="paceLabel" class="pace-note">
      <span class="pace-swatch"></span>
      <span>虚线为{{ paceLabel }}</span>
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import type { ECharts } from 'echarts/core'
import { LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type { LineChartData } from './chartTypes'
import { buildPaceSegments } from './chartPaces'

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer])

const props = withDefaults(defineProps<{ data: LineChartData | null; height?: number }>(), {
  height: 140,
})

const containerRef = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

const paceLabel = computed(() => props.data?.paces?.[0]?.label ?? '')

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

const DAY_MS = 24 * 60 * 60 * 1000
// Two Codex periods rounded out to local midnight span a full 15 days.
const WEEKDAY_MAX_SPAN_MS = 16 * DAY_MS

function atLocalDay(base: number, offsetDays: number) {
  const date = new Date(base)
  date.setHours(0, 0, 0, 0)
  date.setDate(date.getDate() + offsetDays)
  return date.getTime()
}

// Every Monday gets a tick, and the days between are filled at a steady step that
// is kept clear of the Mondays, so the axis stays readable without dropping one.
function buildWeekdayTicks(start: number, end: number) {
  const totalDays = Math.max(0, Math.round((end - start) / DAY_MS))
  const step = totalDays > 20 ? 3 : 2
  const mondays = new Set<number>()
  for (let offset = 0; offset <= totalDays; offset += 1) {
    const day = atLocalDay(start, offset)
    if (new Date(day).getDay() === 1) {
      mondays.add(day)
    }
  }
  const ticks = new Set<number>(mondays)
  for (let offset = 0; offset <= totalDays; offset += step) {
    const day = atLocalDay(start, offset)
    const touchesMonday = [...mondays].some((monday) => Math.abs(monday - day) <= DAY_MS)
    if (!touchesMonday) {
      ticks.add(day)
    }
  }
  ticks.add(end)
  return [...ticks].sort((a, b) => a - b)
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
  // Only annotate Mondays on short windows; long spans use a coarser axis
  // interval where the weekday would just add noise. The two-period Codex view
  // rounds to a full 15 days once the axis snaps to local midnight, so the
  // cutoff has to clear that.
  const span = end - start
  const showWeekday = span <= WEEKDAY_MAX_SPAN_MS
  const weekdayTicks = showWeekday ? buildWeekdayTicks(start, end) : []

  const points = data.points
    .map((point) => ({ time: new Date(point.at).getTime(), value: point.value }))
    .filter((point) => Number.isFinite(point.time))
    .sort((a, b) => a.time - b.time)

  // Line uses every point; dots are thinned to the earliest point of each day.
  const seenDays = new Set<string>()
  const dotData: [number, number][] = []
  for (const point of points) {
    if (point.value === null) {
      continue
    }
    const date = new Date(point.time)
    const key = `${date.getFullYear()}/${date.getMonth()}/${date.getDate()}`
    if (!seenDays.has(key)) {
      seenDays.add(key)
      dotData.push([point.time, point.value])
    }
  }

  const yMax = typeof data.max === 'number' ? data.max : undefined
  const resetTime = data.resetAt ? new Date(data.resetAt).getTime() : NaN

  // Even-burn reference: one dashed line per reset cycle, drawn from that cycle's
  // own start (100%) to its reset (0%). Cycles differ in start and reset, so the
  // previous cycle's end is never reused as a start. Windows without a reset
  // period (DeepSeek balance) carry no paces.
  // An early reset clips the old line at the new start, keeping its old slope.
  const paceSeries: Record<string, unknown>[] = []
  for (const segment of buildPaceSegments(data.paces ?? [])) {
    paceSeries.push({
      type: 'line',
      silent: true,
      showSymbol: false,
      smooth: false,
      lineStyle: { color: '#94a3b8', width: 1, type: 'dashed' },
      itemStyle: { color: '#94a3b8' },
      tooltip: { show: false },
      data: segment,
    })
  }

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
      axisPointer: {
        type: 'line',
        ...(showWeekday
          ? { label: { formatter: (params: { value: number }) => formatClock(Number(params.value)) } }
          : {}),
      },
      valueFormatter: (value: unknown) => formatValue(Number(value), unit),
      // A value axis has no date notion, so spell out both the axis bubble and
      // the tooltip header from the raw timestamp.
      ...(showWeekday
        ? {
            formatter: (params: unknown) => {
              const list = Array.isArray(params) ? params : [params]
              const raw = (list[0] as { value?: unknown } | undefined)?.value
              const moment = Array.isArray(raw) ? raw[0] : raw
              const rows = list
                .filter((item) => (item as { seriesType?: string }).seriesType === 'line')
                .map((item) => {
                  const entry = item as { marker?: string; value?: unknown }
                  const value = Array.isArray(entry.value) ? entry.value[1] : entry.value
                  return value === null || value === undefined
                    ? ''
                    : `${entry.marker ?? ''}${formatValue(Number(value), unit)}`
                })
                .filter((row) => row !== '')
              return [formatClock(Number(moment)), ...rows].join('<br/>')
            },
          }
        : {}),
    },
    xAxis: {
      // A time axis can only scale through ECharts' nice intervals and would skip
      // some Mondays, so short windows use a linear value axis carrying the raw
      // timestamps with a hand-built tick set instead.
      type: showWeekday ? 'value' : 'time',
      min: start,
      max: end,
      ...(showWeekday ? { axisTick: { customValues: weekdayTicks } } : {}),
      axisLabel: {
        color: '#94a3b8',
        fontSize: 9,
        // A value axis renders the custom grid but drops the values that coincide
        // with the extent, so the range edges are kept through these two flags.
        showMinLabel: true,
        showMaxLabel: true,
        hideOverlap: !showWeekday,
        ...(showWeekday ? { customValues: weekdayTicks } : {}),
        rich: {
          week: { color: '#475569', fontSize: 9, fontWeight: 'bold' },
        },
        formatter: (value: number) => {
          const date = new Date(value)
          const label = `${date.getMonth() + 1}/${date.getDate()}`
          return showWeekday && date.getDay() === 1 ? `{week|${label}周一}` : label
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
      ...paceSeries,
      {
        type: 'line',
        showSymbol: false,
        smooth: false,
        itemStyle: { color: '#22c55e' },
        lineStyle: { color: '#22c55e', width: 2 },
        data: points.map((point) => [point.time, point.value]),
        markLine,
      },
      {
        type: 'scatter',
        symbolSize: 5,
        itemStyle: { color: '#22c55e' },
        data: dotData,
        silent: true,
        // The dots mirror the line's daily points; keep them out of the axis
        // tooltip so it does not list the same value twice.
        tooltip: { show: false },
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

/* Sits under the plot area, aligned with the y-axis labels' left edge. */
.pace-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 2px 0 0 40px;
  color: #94a3b8;
  font-size: 10px;
  line-height: 1.4;
}

.pace-swatch {
  flex: none;
  width: 16px;
  border-top: 1px dashed #94a3b8;
}
</style>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { LineChart, ScatterChart, type LineSeriesOption, type ScatterSeriesOption } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
  type GridComponentOption,
  type LegendComponentOption,
  type TooltipComponentOption,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import type { ComposeOption, ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { formatChineseCompactNumber } from '@/utils/numberFormat'
import { niceAxisScale, type XYPoint } from '@/utils/relationshipAnalysis'

echarts.use([ScatterChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

type ChartOption = ComposeOption<
  GridComponentOption | LegendComponentOption | TooltipComponentOption | ScatterSeriesOption | LineSeriesOption
>

export interface RelationshipChartSeries {
  label: string
  color?: string
  observed: XYPoint[]
  keyPoints?: XYPoint[]
  currentPoint?: XYPoint
  projected?: XYPoint[]
  showProjectedLabels?: boolean
}

const props = defineProps<{
  xLabel: string
  yLabel: string
  series: RelationshipChartSeries[]
  xDataMax?: number
}>()

const chartRef = ref<HTMLElement | null>(null)
let chart: ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const xScale = computed(() => {
  const pointMax = Math.max(
    0,
    ...props.series.flatMap(item => [
      ...item.observed,
      ...(item.currentPoint ? [item.currentPoint] : []),
      ...(item.projected || []),
    ].map(point => point.x)),
  )
  return niceAxisScale(Math.max(pointMax, props.xDataMax || 0))
})

function sortedPoints(points: XYPoint[]): XYPoint[] {
  return points
    .filter(point => Number.isFinite(point.x) && Number.isFinite(point.y))
    .sort((left, right) => left.x - right.x)
}

function buildOption(): ChartOption {
  const chartSeries: Array<ScatterSeriesOption | LineSeriesOption> = []
  const showProjectedLabels = props.series.some(item => item.showProjectedLabels)
  props.series.forEach(definition => {
    const observedPoints = sortedPoints(definition.observed)
    const keyPoints = sortedPoints(definition.keyPoints || [])
    const currentPoint = definition.currentPoint
    const projectedPoints = sortedPoints(definition.projected || [])
    const observed = observedPoints.map(point => [point.x, point.y])
    const projected = projectedPoints.map((point, index) => ({
      name: point.label || '',
      value: [point.x, point.y],
      label: definition.showProjectedLabels && index === projectedPoints.length - 1
        ? { position: 'left', distance: 10 }
        : undefined,
    }))
    if (observed.length) {
      chartSeries.push({
        name: definition.label,
        type: 'line',
        symbol: 'none',
        silent: true,
        data: observed,
        lineStyle: { width: 2, color: definition.color },
        emphasis: { disabled: true },
        tooltip: { show: false },
      })
    }
    if (keyPoints.length) {
      chartSeries.push({
        name: definition.label,
        type: 'scatter',
        symbolSize: 8,
        data: keyPoints.map(point => ({
          name: point.label || '',
          value: [point.x, point.y],
          label: point.label ? {
            show: true,
            formatter: point.label,
            position: 'top',
          } : { show: false },
        })),
        itemStyle: {
          color: definition.color,
          borderColor: '#fff',
          borderWidth: 1,
        },
        z: 3,
      })
    }
    if (currentPoint && Number.isFinite(currentPoint.x) && Number.isFinite(currentPoint.y)) {
      chartSeries.push({
        name: `${definition.label}（当前）`,
        type: 'scatter',
        symbol: 'diamond',
        symbolSize: 11,
        data: [{
          name: currentPoint.label || '当前',
          value: [currentPoint.x, currentPoint.y],
          label: {
            show: true,
            formatter: currentPoint.label || '当前',
            position: 'left',
            distance: 7,
          },
        }],
        itemStyle: {
          color: definition.color,
          borderColor: '#fff',
          borderWidth: 1,
        },
        z: 4,
      })
    }
    if (projected.length) {
      const lastObserved = observed[observed.length - 1]
      const projectedData = lastObserved
        ? [{
            value: lastObserved,
            symbol: 'none',
            symbolSize: 0,
            itemStyle: { opacity: 0 },
            label: { show: false },
            tooltip: { show: false },
          }, ...projected]
        : projected
      chartSeries.push({
        name: `${definition.label}（预测）`,
        type: 'line',
        symbolSize: 8,
        data: projectedData,
        lineStyle: { type: 'dashed', width: 2, color: definition.color },
        itemStyle: {
          color: definition.color,
          borderColor: '#fff',
          borderWidth: 1,
        },
        label: definition.showProjectedLabels ? {
          show: true,
          position: 'top',
          distance: 8,
          color: '#606266',
          fontSize: 11,
          formatter: (params: any) => {
            const value = params?.value
            if (!Array.isArray(value)) return ''
            const name = params?.name ? `${params.name} ` : ''
            return `${name}\n(${formatChineseCompactNumber(value[0])}, ${formatChineseCompactNumber(value[1])})`
          },
        } : undefined,
      })
    }
  })
  return {
    animation: false,
    grid: {
      left: 62,
      right: showProjectedLabels ? 58 : 18,
      top: props.series.length > 1 || showProjectedLabels ? 42 : 12,
      bottom: 42,
    },
    legend: { show: props.series.length > 1, top: 0, left: 0 },
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const value = params?.value
        if (!Array.isArray(value)) return params?.seriesName || ''
        return [
          params?.seriesName || '',
          `${props.xLabel}：${formatChineseCompactNumber(value[0])}`,
          `${props.yLabel}：${formatChineseCompactNumber(value[1])}`,
        ].join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      min: 0,
      max: xScale.value.max,
      interval: xScale.value.interval,
      name: props.xLabel,
      nameLocation: 'middle',
      nameGap: 28,
      axisLabel: { formatter: value => formatChineseCompactNumber(value) },
    },
    yAxis: {
      type: 'value',
      axisLabel: { formatter: value => formatChineseCompactNumber(value) },
    },
    series: chartSeries,
  }
}

function hasData(): boolean {
  return props.series.some(item => item.observed.length || item.projected?.length)
}

async function renderChart() {
  await nextTick()
  if (!chartRef.value || !hasData()) return
  chart ||= echarts.init(chartRef.value)
  chart.setOption(buildOption(), true)
}

watch(() => [props.series, props.xLabel, props.yLabel, props.xDataMax], renderChart, { deep: true })

onMounted(() => {
  void renderChart()
  if (chartRef.value && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(chartRef.value)
  }
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div v-if="hasData()" ref="chartRef" class="relationship-chart"></div>
  <div v-else class="empty-chart">更新后开始积累关系样本</div>
</template>

<style scoped>
.relationship-chart {
  width: min(100%, 620px);
  height: 270px;
}

.empty-chart {
  padding: 28px 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>

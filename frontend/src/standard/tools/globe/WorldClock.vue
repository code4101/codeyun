<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

interface ClockLocation {
  id: string
  name: string
  region: string
  timeZone: string
}

// 横轴每格代表相对北京的真实时差；伦敦与美国的夏令时交给 IANA 时区数据计算。
const locations: ClockLocation[] = [
  // 贝克岛为无人岛，作为 UTC−12 地理端点展示；Etc/GMT 的符号与 UTC 偏移相反。
  { id: 'baker', name: '贝克岛', region: '美国无人岛 · UTC−12 地理端点', timeZone: 'Etc/GMT+12' },
  { id: 'los-angeles', name: '洛杉矶', region: '美国西部', timeZone: 'America/Los_Angeles' },
  { id: 'new-york', name: '纽约', region: '美国东部', timeZone: 'America/New_York' },
  { id: 'utc', name: '零时区', region: '本初子午线 · UTC', timeZone: 'UTC' },
  { id: 'london', name: '伦敦', region: '英国', timeZone: 'Europe/London' },
  { id: 'beijing', name: '北京', region: '中国', timeZone: 'Asia/Shanghai' },
  { id: 'kiritimati', name: '基里蒂马蒂', region: '基里巴斯 · UTC+14', timeZone: 'Pacific/Kiritimati' },
]

const BOARD_CENTER = 760
const PIXELS_PER_HOUR = 34
const MIN_OFFSET_HOURS = -20 // UTC−12 相对北京 UTC+8 的最左端。
const MAX_OFFSET_HOURS = 6 // UTC+14 相对北京 UTC+8 的最右端。
const AXIS_START = BOARD_CENTER + MIN_OFFSET_HOURS * PIXELS_PER_HOUR
const AXIS_END = BOARD_CENTER + MAX_OFFSET_HOURS * PIXELS_PER_HOUR
const chartViewBox = `${AXIS_START - 48} 0 ${AXIS_END - AXIS_START + 96} 180`
const ticks = [-20, -16, -12, -8, -4, 0, 4, 6]
const AXIS_Y = 92
const now = ref(new Date())
let timer: ReturnType<typeof setTimeout> | undefined

function scheduleNextMinute() {
  timer = setTimeout(() => {
    now.value = new Date()
    scheduleNextMinute()
  }, 60_000 - Date.now() % 60_000)
}

function utcOffsetMinutes(timeZone: string, date: Date): number {
  const name = new Intl.DateTimeFormat('en-US', {
    timeZone,
    timeZoneName: 'longOffset',
  }).formatToParts(date).find(part => part.type === 'timeZoneName')?.value ?? 'GMT+00:00'
  const match = /^GMT([+-])(\d{2}):(\d{2})$/.exec(name)
  if (!match) return 0
  const minutes = Number(match[2]) * 60 + Number(match[3])
  return match[1] === '-' ? -minutes : minutes
}

const beijingLocal = computed(() => new Date(now.value.getTime() + utcOffsetMinutes('Asia/Shanghai', now.value) * 60_000))
const minuteOfDay = computed(() => beijingLocal.value.getUTCHours() * 60 + beijingLocal.value.getUTCMinutes())

// 数轴上的日期分段由北京时间和横轴时差共同决定；跨午夜处就是日期边界。
const dateBands = computed(() => {
  const min = MIN_OFFSET_HOURS * 60
  const max = MAX_OFFSET_HOURS * 60
  const edges = [min, max]
  for (let day = -1; day <= 2; day++) {
    const boundary = day * 1440 - minuteOfDay.value
    if (boundary > min && boundary < max) edges.push(boundary)
  }
  edges.sort((a, b) => a - b)

  return edges.slice(0, -1).map((start, index) => {
    const end = edges[index + 1]
    const midpoint = (start + end) / 2
    const dayOffset = Math.floor((minuteOfDay.value + midpoint) / 1440)
    const localDate = new Date(beijingLocal.value.getTime() + midpoint * 60_000)
    const dayName = dayOffset < 0 ? '昨日' : dayOffset > 0 ? '明日' : '今日'
    return {
      x: BOARD_CENTER + start / 60 * PIXELS_PER_HOUR,
      width: (end - start) / 60 * PIXELS_PER_HOUR,
      label: `${dayName} · ${localDate.getUTCMonth() + 1}月${localDate.getUTCDate()}日`,
    }
  })
})

const clocks = computed(() => {
  const date = now.value
  const beijingOffset = utcOffsetMinutes('Asia/Shanghai', date)
  const points = locations.map(location => {
    const parts = new Intl.DateTimeFormat('zh-CN', {
      timeZone: location.timeZone,
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(date)
    const part = (type: string) => parts.find(item => item.type === type)?.value ?? ''
    const offset = utcOffsetMinutes(location.timeZone, date)
    const x = BOARD_CENTER + (offset - beijingOffset) / 60 * PIXELS_PER_HOUR
    const sign = offset >= 0 ? '+' : '−'
    const hours = Math.floor(Math.abs(offset) / 60)
    const minutes = Math.abs(offset) % 60
    return {
      ...location,
      x,
      utcLabel: `UTC${sign}${hours}${minutes ? `:${String(minutes).padStart(2, '0')}` : ''}`,
      time: `${part('hour')}:${part('minute')}`,
    }
  })
  const sameTime = points.find(point => point.id === 'utc')?.x === points.find(point => point.id === 'london')?.x
  // 冬季两地同点、同时间，合并显示；夏季用紧凑标签保持各自垂直对齐。
  return points.filter(point => !(sameTime && point.id === 'london')).map(point => ({
    ...point,
    name: sameTime && point.id === 'utc' ? '零时区 / 伦敦' : point.name,
    region: sameTime && point.id === 'utc' ? 'UTC 基准 / 英国当地时间' : point.region,
    compact: !sameTime && (point.id === 'utc' || point.id === 'london'),
  }))
})

onMounted(() => {
  now.value = new Date()
  scheduleNextMinute()
})
onUnmounted(() => { if (timer) clearTimeout(timer) })
</script>

<template>
  <section class="world-clock" aria-labelledby="world-clock-heading">
    <header class="page-header">
      <div>
        <h2 id="world-clock-heading">时区时间</h2>
        <p class="subtitle">以此刻的北京时间为中心，看世界各地的当地时间。</p>
      </div>
      <div class="live-indicator"><span class="live-dot" /> 实时更新</div>
    </header>

    <section class="timeline-section" aria-label="各地时间与北京时差">
      <div class="timeline-viewport" tabindex="0" aria-label="时区时间图，窄屏可横向滚动">
        <!-- 坐标、刻度、连接线与标签均由 SVG 自身定位，样式热更新失败时仍能呈现横轴。 -->
        <svg class="timeline-chart" width="100%" :viewBox="chartViewBox" role="img" aria-label="以北京时间为基准，上方显示地点和 UTC 偏移，紧邻轴线下方显示当地时间，再下方显示日期" style="display: block; font-family: system-ui, sans-serif">
          <rect x="0" y="0" width="1320" height="180" fill="#ffffff" />
          <text :x="AXIS_START" y="15" fill="#8495aa" font-size="11">← 比北京早</text>
          <text :x="AXIS_END" y="15" fill="#8495aa" font-size="11" text-anchor="end">比北京晚 →</text>

          <g v-for="clock in clocks" :key="`guide-${clock.id}`">
            <line :x1="clock.x" :x2="clock.x" y1="62" y2="105" :stroke="clock.id === 'beijing' ? '#3f7fc5' : '#b4c4d7'" stroke-width="1.2" />
          </g>
          <line :x1="AXIS_START" :x2="AXIS_END" :y1="AXIS_Y" :y2="AXIS_Y" stroke="#5a83b7" stroke-width="3" stroke-linecap="round" />
          <g v-for="tick in ticks" :key="`tick-label-${tick}`">
            <line :x1="BOARD_CENTER + tick * PIXELS_PER_HOUR" :x2="BOARD_CENTER + tick * PIXELS_PER_HOUR" :y1="AXIS_Y - 6" :y2="AXIS_Y + 6" stroke="#5a83b7" stroke-width="2" />
          </g>
          <g v-for="(band, index) in dateBands" :key="band.label">
            <rect :x="band.x" y="146" :width="band.width" height="24" :fill="index % 2 ? '#eaf2fb' : '#f3f7fc'" />
            <line v-if="index > 0" :x1="band.x" :x2="band.x" y1="146" y2="170" stroke="#cb9658" stroke-width="1.5" />
            <text :x="band.x + band.width / 2" y="163" text-anchor="middle" fill="#4f6887" font-size="11" font-weight="600">{{ band.label }}</text>
          </g>

          <g v-for="clock in clocks" :key="clock.id">
            <title>{{ clock.name }}（{{ clock.region }}）：{{ clock.time }}</title>
            <circle :cx="clock.x" :cy="AXIS_Y" :r="clock.id === 'beijing' ? 6 : 4" :fill="clock.id === 'beijing' ? '#247bd2' : '#496e98'" stroke="#ffffff" stroke-width="1.5" />
            <text :x="clock.x" y="40" text-anchor="middle" :fill="clock.id === 'beijing' ? '#2365b5' : '#263e5a'" :font-size="clock.compact ? 11 : 12" font-weight="600">{{ clock.name }}</text>
            <text :x="clock.x" y="56" text-anchor="middle" fill="#8695a8" :font-size="clock.compact ? 9 : 10">{{ clock.utcLabel }}</text>
            <text :x="clock.x" y="122" text-anchor="middle" :fill="clock.id === 'beijing' ? '#2365b5' : '#344b67'" :font-size="clock.compact ? 11 : 13" font-weight="600" font-variant-numeric="tabular-nums">{{ clock.time }}</text>
          </g>
        </svg>
      </div>
      <div class="axis-footer">
        <span>横轴按实际时差定位</span>
        <span>伦敦和美国城市的夏令时自动调整</span>
      </div>
    </section>
    <p class="footnote">贝克岛为无人岛，用于标示 UTC−12 端点；有常住人口的最晚时区为 UTC−11（如美属萨摩亚）。时间以当前设备时钟为准。</p>
  </section>
</template>

<style scoped>
.world-clock { min-width: 0; margin-bottom: 24px; color: #17243b; }
.page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 14px; }
h2 { margin: 0; font-size: 18px; line-height: 1.25; }
.subtitle { margin: 10px 0 0; color: #61728a; font-size: 14px; }
.live-indicator { display: flex; align-items: center; gap: 8px; color: #41745c; font-size: 12px; white-space: nowrap; }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: #48b47d; box-shadow: 0 0 0 4px #dff5e8; }
.timeline-section { box-sizing: border-box; width: 100%; min-width: 0; overflow: hidden; border: 1px solid #e0e6ee; border-radius: 18px; background: #fff; box-shadow: 0 8px 28px rgb(28 48 78 / 5%); }
.axis-footer { display: flex; justify-content: space-between; gap: 16px; padding: 13px 26px 18px; border-top: 1px solid #eff2f6; color: #71829a; font-size: 12px; }
.timeline-viewport { width: 100%; min-width: 0; overflow-x: auto; overflow-y: hidden; scrollbar-color: #bac8d9 #f3f6fa; }
/* 在普通窗口内按比例铺满；低于可读宽度时才允许图内滚动，避免整页溢出。 */
.timeline-chart { width: 100%; min-width: 760px; max-width: 1120px; height: auto; margin-inline: auto; }
.footnote { margin: 16px 0 0; color: #78889b; font-size: 12px; line-height: 1.7; }
@media (max-width: 650px) {
  .page-header { align-items: flex-start; }
  .live-indicator { margin-top: 7px; }
  .axis-footer { display: block; padding: 12px 16px 16px; }
  .axis-footer span { display: block; }
}
</style>

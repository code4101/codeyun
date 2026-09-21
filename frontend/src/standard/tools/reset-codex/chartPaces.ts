import type { LineChartPace } from './chartTypes'

/** Clip each reference at the next cycle's start, preserving its original slope. */
export function buildPaceSegments(paces: LineChartPace[]): [number, number][][] {
  const cycles = paces.map((pace) => ({
    start: Date.parse(pace.startAt),
    end: Date.parse(pace.endAt),
    from: pace.startValue,
    to: pace.endValue,
  })).filter(({ start, end }) => Number.isFinite(start) && Number.isFinite(end) && end > start)
    .sort((a, b) => a.start - b.start)

  return cycles.flatMap((cycle, index) => {
    const end = Math.min(cycle.end, cycles[index + 1]?.start ?? cycle.end)
    if (end <= cycle.start) return []
    const value = cycle.from + (cycle.to - cycle.from) * (end - cycle.start) / (cycle.end - cycle.start)
    return [[[cycle.start, cycle.from], [end, value]] as [number, number][]]
  })
}

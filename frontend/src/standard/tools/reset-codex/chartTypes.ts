export interface LineChartPoint {
  at: string
  /** None marks a reset break; the line is not drawn across it. */
  value: number | null
}

/**
 * Even-burn reference line for one reset cycle: the ideal straight drop from
 * `startValue` at that cycle's own start to `endValue` at its reset. Cycles have
 * separate start/reset pairs, so the previous cycle's end is not a start.
 * These endpoints retain the original slope; rendering clips at the next start.
 */
export interface LineChartPace {
  startAt: string
  endAt: string
  startValue: number
  endValue: number
  label: string
}

export interface LineChartData {
  points: LineChartPoint[]
  windowStart: string
  windowEnd: string
  resetAt?: string
  unit?: string
  max?: number | null
  paces?: LineChartPace[]
}

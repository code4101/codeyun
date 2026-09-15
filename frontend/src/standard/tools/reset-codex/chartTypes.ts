export interface LineChartPoint {
  at: string
  value: number
}

export interface LineChartData {
  points: LineChartPoint[]
  windowStart: string
  windowEnd: string
  resetAt?: string
  unit?: string
  max?: number | null
}

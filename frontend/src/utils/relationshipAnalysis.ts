export interface XYPoint {
  x: number
  y: number
  label?: string
}

export interface RelationshipPoint {
  id: string
  captured_at: string
  x: number
  values: Record<string, number>
}

export interface OriginFit {
  slope: number
  rSquared: number | null
  sampleCount: number
}

export interface NiceAxisScale {
  interval: number
  max: number
}

export interface RelationshipKeyPointOptions {
  yThresholds?: number[]
  relativeSlopeChange?: number
}

/** Round a positive data maximum up to a stable, human-friendly equal-step axis. */
export function niceAxisScale(maxValue: number, preferredSplitCount = 6): NiceAxisScale {
  if (!Number.isFinite(maxValue) || maxValue <= 0) return { interval: 1, max: 1 }
  const rawInterval = maxValue / Math.max(2, preferredSplitCount)
  const magnitude = 10 ** Math.floor(Math.log10(rawInterval))
  const normalized = rawInterval / magnitude
  const factor = [1, 2, 2.5, 5, 10].find(candidate => candidate >= normalized) || 10
  const interval = factor * magnitude
  return {
    interval,
    max: Math.ceil(maxValue / interval) * interval,
  }
}

/** Keep the exact polyline dense while exposing only meaningful interactive points. */
export function selectRelationshipKeyPoints(
  points: XYPoint[],
  options: RelationshipKeyPointOptions = {},
): XYPoint[] {
  const sorted = points
    .filter(point => Number.isFinite(point.x) && point.x > 0 && Number.isFinite(point.y))
    .sort((left, right) => left.x - right.x)
  if (sorted.length <= 2) return sorted

  const selected = new Set<number>([0, sorted.length - 1])
  for (const threshold of options.yThresholds || []) {
    const index = sorted.findIndex(point => point.y >= threshold)
    if (index >= 0) selected.add(index)
  }

  const changeThreshold = Math.max(0, options.relativeSlopeChange ?? 0.2)
  for (let index = 1; index < sorted.length - 1; index += 1) {
    const previous = sorted[index - 1]
    const current = sorted[index]
    const next = sorted[index + 1]
    const leftDx = current.x - previous.x
    const rightDx = next.x - current.x
    if (leftDx <= 0 || rightDx <= 0) continue
    const leftSlope = (current.y - previous.y) / leftDx
    const rightSlope = (next.y - current.y) / rightDx
    const scale = Math.max(Math.abs(leftSlope), Math.abs(rightSlope), Number.EPSILON)
    if (Math.abs(rightSlope - leftSlope) / scale >= changeThreshold) selected.add(index)
  }

  return [...selected]
    .sort((left, right) => left - right)
    .map(index => sorted[index])
}

function validPoints(points: XYPoint[]): XYPoint[] {
  return points.filter(point =>
    Number.isFinite(point.x) && point.x > 0 && Number.isFinite(point.y) && point.y >= 0,
  )
}

/** Fit y = slope * x. Keeping the known zero origin makes one sample useful. */
export function fitThroughOrigin(points: XYPoint[]): OriginFit | null {
  const valid = validPoints(points)
  const denominator = valid.reduce((total, point) => total + point.x * point.x, 0)
  if (!valid.length || denominator <= 0) return null
  const slope = valid.reduce((total, point) => total + point.x * point.y, 0) / denominator
  if (!Number.isFinite(slope) || slope <= 0) return null

  const residual = valid.reduce((total, point) => {
    const error = point.y - slope * point.x
    return total + error * error
  }, 0)
  const total = valid.reduce((sum, point) => sum + point.y * point.y, 0)
  return {
    slope,
    rSquared: valid.length > 1 && total > 0 ? 1 - residual / total : null,
    sampleCount: valid.length,
  }
}

export function estimateXForY(points: XYPoint[], targetY: number): number | null {
  if (!Number.isFinite(targetY) || targetY < 0) return null
  const fit = fitThroughOrigin(points)
  return fit ? targetY / fit.slope : null
}

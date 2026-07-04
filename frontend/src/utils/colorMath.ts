export interface RgbColor {
  r: number
  g: number
  b: number
}

export interface WeightedColorMixEntry {
  color: string | RgbColor
  weight: number
}

const clampChannel = (value: number): number => {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(255, Math.round(value)))
}

const normalizeHex = (hex: string): string => {
  const match = hex.match(/([0-9a-fA-F]{6})/)
  if (!match) {
    throw new Error(`Invalid hex color: ${hex}`)
  }
  return `#${match[1].toUpperCase()}`
}

export function createRgbColor(r = 0, g = 0, b = 0): RgbColor {
  return {
    r: clampChannel(r),
    g: clampChannel(g),
    b: clampChannel(b),
  }
}

export function fromHex(hex: string): RgbColor {
  const normalized = normalizeHex(hex)
  return {
    r: Number.parseInt(normalized.slice(1, 3), 16),
    g: Number.parseInt(normalized.slice(3, 5), 16),
    b: Number.parseInt(normalized.slice(5, 7), 16),
  }
}

export function toHex(color: RgbColor, lower = false): string {
  const hex = `#${clampChannel(color.r).toString(16).padStart(2, '0')}${clampChannel(color.g).toString(16).padStart(2, '0')}${clampChannel(color.b).toString(16).padStart(2, '0')}`
  return lower ? hex.toLowerCase() : hex.toUpperCase()
}

export function mixWeightedColors(
  entries: WeightedColorMixEntry[],
  options: { fillColor?: string | RgbColor; fillToWeight?: number } = {},
): RgbColor | null {
  const { fillColor = '#FFFFFF', fillToWeight = 100 } = options
  const normalizedEntries = entries
    .map((entry) => {
      const color = typeof entry.color === 'string' ? fromHex(entry.color) : entry.color
      return {
        color,
        weight: Number.isFinite(entry.weight) ? entry.weight : 0,
      }
    })
    .filter(entry => entry.weight > 0)

  const normalizedFillColor = typeof fillColor === 'string' ? fromHex(fillColor) : fillColor

  let totalWeight = 0
  let sumR = 0
  let sumG = 0
  let sumB = 0

  for (const entry of normalizedEntries) {
    totalWeight += entry.weight
    sumR += entry.color.r * entry.weight
    sumG += entry.color.g * entry.weight
    sumB += entry.color.b * entry.weight
  }

  const fillWeight = Math.max(fillToWeight - totalWeight, 0)
  const denominator = totalWeight + fillWeight
  if (denominator <= 0) return null

  return createRgbColor(
    (sumR + normalizedFillColor.r * fillWeight) / denominator,
    (sumG + normalizedFillColor.g * fillWeight) / denominator,
    (sumB + normalizedFillColor.b * fillWeight) / denominator,
  )
}

export function getReadableTextColor(color: RgbColor): '#111827' | '#FFFFFF' {
  const brightness = (color.r * 299 + color.g * 587 + color.b * 114) / 1000
  return brightness > 150 ? '#111827' : '#FFFFFF'
}

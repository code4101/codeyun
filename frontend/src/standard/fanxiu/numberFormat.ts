import { formatChineseCompactNumber, formatCompactSignificant } from '@/utils/numberFormat'

export { formatChineseCompactNumber, formatCompactSignificant }

function normalizeNonNegativeInt(value: unknown) {
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric > 0 ? Math.floor(numeric) : 0
}

function formatFanxiuGameDecimal(value: number, fractionDigits = 1) {
  const factor = 10 ** fractionDigits
  const truncated = Math.floor(value * factor) / factor
  return truncated
    .toFixed(fractionDigits)
    .replace(/\.0+$/, '')
    .replace(/(\.\d*?)0+$/, '$1')
}

export function formatFanxiuGameNumber(value: unknown) {
  const numeric = normalizeNonNegativeInt(value)
  if (!numeric) {
    return '0'
  }
  const units = [
    ['秭秭', 1e48],
    ['垓秭', 1e44],
    ['垓垓', 1e40],
    ['京垓', 1e36],
    ['京京', 1e32],
    ['兆京', 1e28],
    ['亿京', 1e24],
    ['万京', 1e20],
    ['京', 1e16],
    ['兆', 1e12],
  ] as const
  for (const [unit, divisor] of units) {
    if (numeric >= divisor) {
      return `${formatFanxiuGameDecimal(numeric / divisor)}${unit}`
    }
  }
  return formatChineseCompactNumber(numeric)
}

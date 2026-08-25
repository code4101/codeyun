export function formatCompactSignificant(value: number, significantDigits = 4) {
  const numeric = Math.abs(value)
  if (!Number.isFinite(numeric) || numeric === 0) return '0'

  const integerDigits = Math.floor(Math.log10(numeric)) + 1
  const fractionDigits = Math.max(0, significantDigits - integerDigits)
  return numeric
    .toFixed(fractionDigits)
    .replace(/\.0+$/, '')
    .replace(/(\.\d*?)0+$/, '$1')
}

/** CodeYun 默认数值展示：使用万/亿单位，最多保留 4 位有效数字。 */
export function formatChineseCompactNumber(value: unknown, significantDigits = 4) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric === 0) return '0'

  const sign = numeric < 0 ? '-' : ''
  const absolute = Math.abs(numeric)
  const units = [
    ['亿', 1e8],
    ['万', 1e4],
  ] as const
  for (const [unit, divisor] of units) {
    if (absolute >= divisor) {
      return `${sign}${formatCompactSignificant(absolute / divisor, significantDigits)}${unit}`
    }
  }
  return `${sign}${formatCompactSignificant(absolute, significantDigits)}`
}

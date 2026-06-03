function normalizeNonNegativeInt(value: unknown) {
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric > 0 ? Math.floor(numeric) : 0
}

export function formatCompactSignificant(value: number, significantDigits = 4) {
  const numeric = Math.abs(value)
  if (!Number.isFinite(numeric) || numeric === 0) {
    return '0'
  }

  const integerDigits = Math.floor(Math.log10(numeric)) + 1
  const fractionDigits = Math.max(0, significantDigits - integerDigits)
  return numeric
    .toFixed(fractionDigits)
    .replace(/\.0+$/, '')
    .replace(/(\.\d*?)0+$/, '$1')
}

export function formatChineseCompactNumber(value: unknown) {
  const numeric = normalizeNonNegativeInt(value)
  if (!numeric) {
    return '0'
  }
  const units = [
    ['载', 1e44],
    ['正', 1e40],
    ['涧', 1e36],
    ['沟', 1e32],
    ['穰', 1e28],
    ['秭', 1e24],
    ['垓', 1e20],
    ['京', 1e16],
    ['兆', 1e12],
    ['亿', 1e8],
    ['万', 1e4],
  ] as const
  for (const [unit, divisor] of units) {
    if (numeric >= divisor) {
      return `${formatCompactSignificant(numeric / divisor)}${unit}`
    }
  }
  return formatCompactSignificant(numeric)
}

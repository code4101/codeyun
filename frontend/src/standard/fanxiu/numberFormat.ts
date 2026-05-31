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
  if (numeric >= 100000000) {
    return `${formatCompactSignificant(numeric / 100000000)}亿`
  }
  if (numeric >= 10000) {
    return `${formatCompactSignificant(numeric / 10000)}万`
  }
  return formatCompactSignificant(numeric)
}

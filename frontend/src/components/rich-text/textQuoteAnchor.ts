/** Reattach only a unique quote/context. Duplicate text must never guess. */
export function matchingQuoteOffset(source: string, quote: string, prefix: string, suffix: string): number {
  if (!quote) return -1
  const positions: number[] = []
  const contextual: number[] = []
  let offset = 0
  while (offset <= source.length - quote.length) {
    const index = source.indexOf(quote, offset)
    if (index < 0) break
    positions.push(index)
    if ((!prefix || source.slice(0, index).endsWith(prefix)) &&
        (!suffix || source.slice(index + quote.length).startsWith(suffix))) contextual.push(index)
    offset = index + 1
  }
  if (contextual.length === 1) return contextual[0]!
  return positions.length === 1 ? positions[0]! : -1
}

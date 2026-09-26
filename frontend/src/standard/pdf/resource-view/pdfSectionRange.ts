type Entry = { id: string; page: number | null; level: number }

/** Include descendants and stop before the next peer/ancestor, even when a group has no page target. */
export function pdfSectionRange(entries: Entry[], id: string, total: number) {
  const index = entries.findIndex(entry => entry.id === id)
  if (index < 0 || total < 1) return null
  const node = entries[index]!
  let endIndex = index + 1
  while (endIndex < entries.length && entries[endIndex]!.level > node.level) endIndex++
  const start = entries.slice(index, endIndex).find(entry => entry.page !== null)?.page
  if (start == null || start < 1 || start > total) return null
  const next = entries.slice(endIndex).find(entry => entry.page !== null && entry.page >= start)?.page
  // PDF headings can share a page; that page belongs to both sections.
  return { start, end: Math.min(total, next == null ? total : Math.max(start, next - 1)) }
}

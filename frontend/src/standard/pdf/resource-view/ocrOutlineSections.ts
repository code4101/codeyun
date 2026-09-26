type Entry = { id: string; title: string; page: number | null; level: number }

/** Each heading owns text until the following heading, including a shared boundary page for OCR trimming. */
export function ocrOutlineSections(entries: Entry[], ids: string[], scopeId: string, total: number, page: number) {
  const selected = new Set([...ids, scopeId].filter(Boolean))
  const usable = entries.filter(entry => entry.page !== null && entry.page >= 1 && entry.page <= total)
  if (!selected.size) {
    let nearest: Entry | undefined
    for (const entry of usable) if (entry.page! <= page) nearest = entry
    if (nearest) selected.add(nearest.id)
  }
  const sections = usable.flatMap((entry, index) => {
    if (!selected.has(entry.id)) return []
    const next = usable[index + 1]
    return [{ id: entry.id, title: entry.title, level: entry.level, start: entry.page!, end: Math.max(entry.page!, next?.page ?? total), nextTitle: next?.title, nextStart: next?.page ?? undefined, hasTitle: true }]
  })
  return sections.length ? sections : [{ id: 'body', title: '正文', level: 0, start: 1, end: usable[0]?.page ?? total, nextTitle: usable[0]?.title, nextStart: usable[0]?.page ?? undefined, hasTitle: false }]
}

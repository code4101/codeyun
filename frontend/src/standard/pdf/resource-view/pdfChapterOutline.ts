import { readerTreeRows, splitReaderTree } from '../library/readerTree.ts'

type Entry = { id: string; title: string; page: number | null; level: number }

/** Both panes share one resolved boundary, including automatic mode. */
export function pdfChapterOutline(entries: Entry[], page: number, outlineLevel = 0) {
  const parents: Entry[] = []
  const rows = entries.map(entry => {
    while (parents.length && parents[parents.length - 1]!.level >= entry.level) parents.pop()
    const row = { ...entry, parentId: parents.at(-1)?.id, rootId: parents[0]?.id ?? entry.id }
    parents.push(entry)
    return row
  })
  let active: typeof rows[number] | undefined
  for (const row of rows) {
    if (row.page !== null && row.page <= page && (!active || row.page >= active.page!)) active = row
  }
  const depth = readerTreeRows(rows, new Set()).reduce((maximum, row) => Math.max(maximum, row.depth + 1), 1)
  // Prefer chapters and sections on the left; shallow books keep at least their root directory.
  const level = outlineLevel || Math.min(3, Math.max(2, depth))
  const split = splitReaderTree(rows, active?.id ?? '', level)
  return { scopeId: split.tocActiveId, items: split.outline, toc: split.toc, activeId: active?.id ?? '', level }
}

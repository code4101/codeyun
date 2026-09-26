/** A directory target is opaque: the provider decides whether it means a chapter or page. */
export interface ReaderTreeItem { id: string; title: string; parentId?: string | null; number?: string }

/** Split one tree without changing its source. Level is one-based; level 1 puts the whole tree on the right. */
export function splitReaderTree(items: ReaderTreeItem[], activeId: string, level: number) {
  const rows = readerTreeRows(items, new Set())
  const byId = new Map(rows.map(row => [row.id, row]))
  const ancestors = (id: string) => {
    const result: string[] = []
    const seen = new Set([id])
    let parent = byId.get(id)?.parentId
    while (parent && byId.has(parent) && !seen.has(parent)) {
      result.push(parent); seen.add(parent); parent = byId.get(parent)?.parentId
    }
    return result
  }
  const path = [activeId, ...ancestors(activeId)]
  const boundary = path.map(id => byId.get(id)).find(row => row && row.depth < level - 1)
  const toc = rows.filter(row => row.depth < level - 1)
  const outline = rows.filter(row => row.depth >= level - 1 && (level === 1 || Boolean(boundary && ancestors(row.id).includes(boundary.id))))
  const outlineIds = new Set(outline.map(row => row.id))
  return {
    toc,
    tocActiveId: boundary?.id ?? '',
    outline: outline.map(({ number: _number, ...row }) => ({ ...row, parentId: row.parentId && outlineIds.has(row.parentId) ? row.parentId : null })),
    outlineActiveId: activeId,
  }
}

/** Attach the loaded document's headings beneath its catalog node using the same tree contract. */
export function appendReaderHeadings(items: ReaderTreeItem[], chapter: string, headings: { id: string; title: string; level: number }[]) {
  const stack: { id: string; level: number }[] = []
  const children = headings.map(heading => {
    while (stack.length && stack[stack.length - 1]!.level >= heading.level) stack.pop()
    const id = `heading:${heading.id}`
    const item = { id, title: heading.title, parentId: stack.at(-1)?.id ?? chapter }
    stack.push({ id, level: heading.level })
    return item
  })
  const chapterIndex = items.findIndex(item => item.id === chapter)
  if (chapterIndex < 0) return items
  let end = chapterIndex + 1
  while (end < items.length && readerTreeAncestors(items, items[end]!.id).includes(chapter)) end++
  return [...items.slice(0, end), ...children, ...items.slice(end)]
}

export function readerTreeRows(items: ReaderTreeItem[], collapsed: ReadonlySet<string>) {
  const byId = new Map(items.map(item => [item.id, item]))
  const parents = new Set(items.map(item => item.parentId).filter(Boolean))
  return items.flatMap(item => {
    let parent = item.parentId
    let depth = 0
    const seen = new Set([item.id])
    while (parent && byId.has(parent) && !seen.has(parent)) {
      if (collapsed.has(parent)) return []
      seen.add(parent)
      depth++
      parent = byId.get(parent)?.parentId
    }
    return [{ ...item, depth, hasChildren: parents.has(item.id) }]
  })
}

export function readerTreeAncestors(items: ReaderTreeItem[], id: string): string[] {
  const byId = new Map(items.map(item => [item.id, item]))
  const ancestors: string[] = []
  const seen = new Set([id])
  let parent = byId.get(id)?.parentId
  while (parent && byId.has(parent) && !seen.has(parent)) {
    seen.add(parent); ancestors.push(parent); parent = byId.get(parent)?.parentId
  }
  return ancestors
}

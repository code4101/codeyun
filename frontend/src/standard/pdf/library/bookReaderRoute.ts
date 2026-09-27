import type { LocationQueryRaw, Router } from 'vue-router'
import type { ReaderTab } from './readerWorkspaceState'

/** 对外地址只使用全局编号；资源类型由后端解析。 */
export function readerLocation(tab?: ReaderTab, query: LocationQueryRaw = {}) {
  const id = tab?.publicId ?? (tab?.kind === 'pdf' ? Number(tab.id) : undefined)
  return { name: 'ReaderWorkspace', query: id ? {
    ...query, id: String(id),
    ...(tab?.bookshelfId ? { bookshelf: tab.bookshelfId } : {}),
    ...(tab?.readingMode === 'paginated' ? { mode: tab.readingMode } : {}),
    ...(tab?.pageSize && tab.pageSize !== 1600 ? { pageSize: String(tab.pageSize) } : {}),
  } : {} }
}

export function readerTarget(query: Record<string, unknown>): number | undefined {
  if (typeof query.id !== 'string' || !/^[1-9]\d*$/.test(query.id)) return
  const id = Number(query.id)
  return Number.isSafeInteger(id) ? id : undefined
}

export function bookReaderHref(router: Router, publicId?: number, query: LocationQueryRaw = {}): string {
  if (!publicId) return ''
  return router.resolve(readerLocation({ kind: 'ebook', id: '', publicId }, query)).href
}

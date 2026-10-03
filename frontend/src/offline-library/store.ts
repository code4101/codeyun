/** Explicit whole-book downloads. Packages and their catalogue commit together;
 * failed replacements keep the previous complete copy. No TTL/LRU eviction. */
export type OfflineBookKind = 'pdf' | 'ebook' | 'skill'
export interface OfflineSection { id: string; title: string; html: string; text: string; revision?: string }
export interface OfflineToc { id: string; title: string; level: number; section: number; anchor?: string }
export interface OfflineAnnotation { chapter_id: string; quote_text: string; comment_text: string; color: string }
export interface OfflineBookSummary {
  key: string; owner: string; kind: OfflineBookKind; id: string; title: string
  revision: string; downloadedAt: number; bytes: number; sectionCount: number
}
export interface OfflineBook extends OfflineBookSummary {
  schema: 1; sections: OfflineSection[]; toc: OfflineToc[]; annotations: OfflineAnnotation[]
  pdf?: Blob; notes?: Record<string, string>; initialSection: number; initialOffset: number
  bookshelfId?: string
}
export interface OfflinePosition { key: string; section: number; offset: number; updatedAt: number }
export const OFFLINE_LIBRARY_URL = '/offline-library/'

/** Stable session subject; expiration requires a network login, not removal of
 * an already downloaded book. Logout/account switching hide other users' books. */
export function offlineOwner(): string {
  const token = localStorage.getItem('token')
  if (!token) return 'anonymous'
  try {
    const encoded = token.split('.')[1]!.replace(/-/g, '+').replace(/_/g, '/')
    const claims = JSON.parse(atob(encoded))
    if (claims.scope === 'user-session' && /^\d+$/.test(claims.sub)) return `user:${claims.sub}`
  } catch { /* An invalid session cannot select private downloads. */ }
  return 'anonymous'
}
export function offlineBookKey(kind: OfflineBookKind, id: string, owner = offlineOwner()) {
  return `${owner}:${kind}:${id}`
}
let database: Promise<IDBDatabase> | undefined
function db(): Promise<IDBDatabase> {
  return database ??= new Promise((resolve, reject) => {
    let settled = false
    const request = indexedDB.open('codeyun-offline-library', 1)
    const timer = setTimeout(() => { settled = true; database = undefined; reject(new Error('本地存储被占用，请关闭其他旧页面后重试')) }, 10000)
    request.onupgradeneeded = () => {
      for (const name of ['books', 'catalogue', 'positions']) request.result.createObjectStore(name, { keyPath: 'key' })
    }
    request.onerror = () => { clearTimeout(timer); if (settled) return; settled = true; database = undefined; reject(request.error) }
    request.onsuccess = () => {
      clearTimeout(timer)
      const opened = request.result
      if (settled) { opened.close(); return }
      settled = true
      opened.onversionchange = () => { opened.close(); database = undefined }
      resolve(opened)
    }
  })
}
async function write(stores: string[], mutate: (tx: IDBTransaction) => void) {
  const tx = (await db()).transaction(stores, 'readwrite')
  const done = finished(tx)
  try { mutate(tx); await done }
  catch (cause) {
    // A synchronous put() failure does not automatically abort IndexedDB.
    // Explicit abort prevents a replacement package/catalogue mismatch.
    try { tx.abort() } catch { /* already aborted/completed */ }
    await done.catch(() => undefined)
    throw cause
  }
}
function finished(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve()
    tx.onabort = () => reject(tx.error ?? new Error('本地保存失败'))
    tx.onerror = () => { /* abort carries the final error */ }
  })
}
async function read<T>(store: string, key?: string): Promise<T> {
  const tx = (await db()).transaction(store, 'readonly')
  const done = finished(tx)
  const request = key === undefined ? tx.objectStore(store).getAll() : tx.objectStore(store).get(key)
  // Resolve only after transaction completion, including asynchronous failures.
  await done
  return request.result as T
}
export async function listOfflineBooks(): Promise<OfflineBookSummary[]> {
  const items = await read<OfflineBookSummary[]>('catalogue')
  return items.filter(book => book.owner === offlineOwner())
    .sort((a, b) => b.downloadedAt - a.downloadedAt)
}
export async function readOfflineBook(key: string): Promise<OfflineBook | undefined> {
  const book = await read<OfflineBook | undefined>('books', key)
  return book?.schema === 1 && book.owner === offlineOwner() ? book : undefined
}
export async function saveOfflineBook(book: OfflineBook): Promise<void> {
  if (book.owner !== offlineOwner()) throw new Error('账号已切换，请重新下载')
  const { sections, toc, annotations, pdf, notes, initialSection, initialOffset, bookshelfId, schema, ...summary } = book
  await write(['books', 'catalogue'], tx => {
    if (book.owner !== offlineOwner()) throw new Error('账号已切换，请重新下载')
    tx.objectStore('books').put(book)
    tx.objectStore('catalogue').put(summary)
  })
  if (book.owner !== offlineOwner()) throw new Error('账号已切换，离线副本保存在原账号下')
}
export async function deleteOfflineBook(key: string): Promise<void> {
  if (!await readOfflineBook(key)) return
  await write(['books', 'catalogue', 'positions'], tx => {
    if (!key.startsWith(`${offlineOwner()}:`)) throw new Error('账号已切换')
    for (const store of ['books', 'catalogue', 'positions']) tx.objectStore(store).delete(key)
  })
}
export async function readOfflinePosition(key: string) {
  if (!key.startsWith(`${offlineOwner()}:`)) return undefined
  return read<OfflinePosition | undefined>('positions', key)
}
export async function saveOfflinePosition(position: OfflinePosition) {
  if (!position.key.startsWith(`${offlineOwner()}:`)) throw new Error('账号已切换')
  await write(['positions'], tx => {
    if (!position.key.startsWith(`${offlineOwner()}:`)) throw new Error('账号已切换')
    tx.objectStore('positions').put(position)
  })
}
export function formatOfflineBytes(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`
}

import { useUserStore } from '@/store/userStore'
export interface GraphDocument { id: string; title: string; revision: number; bytes: Uint8Array; updatedAt: number; folderId?: string }
export interface GraphFolder { id: string; title: string; parentId: string }
export interface GraphStorage {
  read(id: string): Promise<GraphDocument | undefined>
  write(id: string, title: string, bytes: Uint8Array, expectedRevision: number): Promise<GraphDocument>
}
const encode = (bytes: Uint8Array) => {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
  return btoa(binary)
}
const document = (row: any): GraphDocument => ({ id: String(row.id), title: row.title, revision: row.revision,
  updatedAt: row.updatedAt, folderId: row.parentId ? String(row.parentId) : '',
  bytes: Uint8Array.from(atob(row.content ?? ''), c => c.charCodeAt(0)) })

/** One adapter belongs to one authenticated user. A late autosave must never
 * follow an account switch into somebody else's space. */
export function createGraphLibrary() {
  const user = useUserStore(), ownerId = user.user?.id, ownerName = user.user?.username
  const check = () => {
    let subject = ''
    try { subject = JSON.parse(atob((user.token?.split('.')[1] ?? '').replace(/-/g, '+').replace(/_/g, '/'))).sub } catch { /* Invalid/absent session. */ }
    // Login updates the token before fetching the new profile. Guard both so
    // that brief intermediate state cannot write an old file into a new account.
    if (!ownerId || ownerId !== user.user?.id || subject !== ownerName) throw new Error('用户已切换，请重新打开文件')
  }
  async function request(path = '', method = 'GET', body?: unknown, retry = true): Promise<any> {
    check()
    const response = await fetch(`/api/project-graph/files${path}`, { method,
      headers: { Authorization: `Bearer ${user.token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body) })
    check()
    if (response.status === 401 && retry && await user.refreshAccessToken()) return request(path, method, body, false)
    const result = await response.json()
    if (!response.ok) throw new Error(result.detail ?? '文件操作失败')
    return result
  }
  const storage: GraphStorage = {
    async read(id) { return document(await request(`/${id}`)) },
    async write(id, _title, bytes, expectedRevision) {
      const row = await request(`/${id}/content`, 'PUT', { content: encode(bytes), expectedRevision })
      return { ...document(row), bytes }
    },
  }
  async function create(title: string, bytes: Uint8Array = new Uint8Array(), parentId = '', skipExisting = false) {
    return document(await request('', 'POST', { title, content: encode(bytes), parentId: Number(parentId), skipExisting }))
  }
  async function list() {
    const result = await request()
    return { documents: result.entries.filter((r: any) => r.kind === 'document').map(document) as GraphDocument[],
      folders: result.entries.filter((r: any) => r.kind === 'folder').map((r: any) => ({ id: String(r.id), title: r.title, parentId: r.parentId ? String(r.parentId) : '' })) as GraphFolder[],
      legacyBrowserImport: result.legacyBrowserImport as boolean }
  }
  async function change(action:
    | { type: 'folder'; folder: GraphFolder }
    | { type: 'remove-folder'; id: string }
    | { type: 'document'; id: string; title?: string; folderId?: string; remove?: boolean }) {
    if (action.type === 'folder') {
      const { folder } = action
      await request(folder.id ? `/${folder.id}` : '', folder.id ? 'PATCH' : 'POST', {
        kind: 'folder', title: folder.title, parentId: Number(folder.parentId),
      })
    } else if (action.type === 'remove-folder' || action.remove) await request(`/${action.id}`, 'DELETE')
    else await request(`/${action.id}`, 'PATCH', { title: action.title,
      parentId: action.folderId === undefined ? undefined : Number(action.folderId) })
  }
  /** The old DB has no owner. Only the explicitly designated migration owner
   * may claim it; never assign it to whoever happens to log in first. */
  async function migrateBrowser() {
    if (!(await list()).legacyBrowserImport) return {}
    const doneKey = `codeyun.project-graph.browser-import:${ownerId}`
    try {
      const saved = JSON.parse(localStorage.getItem(doneKey) || 'null')
      if (saved?.ids) return saved.ids as Record<string, string>
    } catch { /* Resume the earlier import marker and recover tab mappings. */ }
    const legacy = await import('./legacyBrowserStorage')
    const oldFolders = await legacy.listGraphFolders(), oldDocs = await legacy.listBrowserGraphDocuments()
    const mapping = new Map<string, string>([['', '']])
    const pending = [...oldFolders]
    while (pending.length) {
      const index = pending.findIndex(f => mapping.has(f.parentId))
      if (index < 0) throw new Error('旧文件夹结构异常，原数据已保留')
      const folder = pending.splice(index, 1)[0]!
      const row = await request('', 'POST', { kind: 'folder', title: folder.title,
        parentId: Number(mapping.get(folder.parentId)), skipExisting: true })
      mapping.set(folder.id, String(row.id))
    }
    const ids: Record<string, string> = {}
    for (const doc of oldDocs) {
      const parent = mapping.get(doc.folderId ?? '')
      if (parent === undefined) throw new Error('旧文件目录缺失，原数据已保留')
      ids[doc.id] = (await create(doc.title, doc.bytes, parent, true)).id
    }
    const tabsKey = `codeyun.project-graph.tabs:${ownerId}`
    if (!localStorage.getItem(tabsKey)) {
      try {
        const oldTabs = JSON.parse(localStorage.getItem('codeyun.project-graph.tabs') || '[]')
        if (Array.isArray(oldTabs)) localStorage.setItem(tabsKey, JSON.stringify([...new Set(oldTabs.map(id => ids[id]).filter(Boolean))]))
      } catch { /* File migration does not depend on UI state. */ }
    }
    localStorage.setItem(doneKey, JSON.stringify({ ids }))
    return ids
  }
  return { ownerId, storage, list, create, change, migrateBrowser }
}

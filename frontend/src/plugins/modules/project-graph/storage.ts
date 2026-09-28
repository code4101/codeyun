import { useUserStore } from '@/store/userStore'
export type GraphRole = 'viewer' | 'editor' | 'manager'
export interface GraphAccess { owner: { id: number; username: string; nickname: string | null }; grants: { userId: number; username: string; nickname: string | null; role: 'viewer' | 'editor' | 'deny' }[] }
export interface GraphDocument { id: string; title: string; revision: number; bytes: Uint8Array; updatedAt: number; folderId?: string; role?: GraphRole; ownerId?: number; shared?: boolean; collaborative?: boolean; journalDate?: string | null }
export interface GraphFolder { id: string; title: string; parentId: string }
export interface GraphStorage {
  read(id: string): Promise<GraphDocument | undefined>
  write(id: string, title: string, bytes: Uint8Array, expectedRevision: number): Promise<GraphDocument>
  collaborationCredentials?(id: string): Promise<{ url: string; token: string }>
}
const encode = (bytes: Uint8Array) => {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
  return btoa(binary)
}
const document = (row: any): GraphDocument => ({ id: String(row.id), title: row.title, revision: row.revision,
  role: row.role, ownerId: row.ownerId, shared: row.shared ?? row.role !== 'manager',
  collaborative: row.collaborative === true,
  journalDate: row.journalDate, updatedAt: row.updatedAt, folderId: row.parentId ? String(row.parentId) : '',
  bytes: Uint8Array.from(atob(row.content ?? ''), c => c.charCodeAt(0)) })

/** One adapter belongs to one authenticated user. A late autosave must never
 * follow an account switch into somebody else's space. */
export function createGraphLibrary() {
  const user = useUserStore(), ownerId = user.user?.id
  const check = () => {
    let subject: unknown, scope: unknown
    try { ({ sub: subject, scope } = JSON.parse(atob((user.token?.split('.')[1] ?? '').replace(/-/g, '+').replace(/_/g, '/')))) } catch { /* Invalid/absent session. */ }
    // Login updates the token before fetching the new profile. Guard both so
    // that brief intermediate state cannot write an old file into a new account.
    // Match create_user_access_token: identity is the immutable numeric ID;
    // usernames can change without changing ownership of this library.
    if (!ownerId || ownerId !== user.user?.id || scope !== 'user-session' || subject !== String(ownerId)) throw new Error('用户已切换，请重新打开文件')
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
    async collaborationCredentials(id) {
      check()
      const claims = JSON.parse(atob(user.token!.split('.')[1]!.replace(/-/g, '+').replace(/_/g, '/')))
      if (claims.exp * 1000 < Date.now() + 15000 && !await user.refreshAccessToken()) throw new Error('登录已过期，请重新登录')
      check()
      const url = new URL(`/api/project-graph/files/${id}/collaboration/socket`, location.href)
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      return { url: url.href, token: user.token! }
    },
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
    | { type: 'document'; id: string; title?: string; folderId?: string; journalDate?: string | null; remove?: boolean }) {
    if (action.type === 'folder') {
      const { folder } = action
      await request(folder.id ? `/${folder.id}` : '', folder.id ? 'PATCH' : 'POST', {
        kind: 'folder', title: folder.title, parentId: Number(folder.parentId),
      })
    } else if (action.type === 'remove-folder' || action.remove) await request(`/${action.id}`, 'DELETE')
    else await request(`/${action.id}`, 'PATCH', { title: action.title, journalDate: action.journalDate,
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
  const getAccess = (id: string): Promise<GraphAccess> => request(`/${id}/access`)
  const setAccess = (id: string, userId: number, role: 'viewer' | 'editor' | 'deny'): Promise<GraphAccess> => request(`/${id}/access`, 'PUT', { userId, role })
  async function setCollaborative(id: string, enabled: boolean) {
    const latest = await storage.read(id)
    return request(`/${id}/collaboration`, enabled ? 'POST' : 'DELETE', enabled ? { expectedRevision: latest!.revision } : undefined)
  }
  const openJournal = async (day: string) => document(await request(`/journals/${day}`, 'POST'))
  return { ownerId, storage, list, create, change, migrateBrowser, openJournal, getAccess, setAccess, setCollaborative }
}

/** Host storage contract. Notes can later provide a server-backed adapter without
 * importing React, Plate, or Project Graph internals into CodeYun. */
export interface GraphDocument {
  id: string
  title: string
  revision: number
  bytes: Uint8Array
  updatedAt: number
  folderId?: string
  deletedAt?: number
}

export interface GraphFolder { id: string; title: string; parentId: string }

export interface GraphStorage {
  read(id: string): Promise<GraphDocument | undefined>
  write(id: string, title: string, bytes: Uint8Array, expectedRevision: number): Promise<GraphDocument>
}

const DATABASE = 'codeyun-project-graph-v1'
function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 2)
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains('documents')) request.result.createObjectStore('documents', { keyPath: 'id' })
      if (!request.result.objectStoreNames.contains('folders')) request.result.createObjectStore('folders', { keyPath: 'id' })
    }
    request.onsuccess = () => { request.result.onversionchange = () => request.result.close(); resolve(request.result) }
    request.onerror = () => reject(request.error)
  })
}

export const browserGraphStorage: GraphStorage = {
  async read(id) {
    const database = await openDatabase()
    try {
      return await new Promise<GraphDocument | undefined>((resolve, reject) => {
        const request = database.transaction('documents').objectStore('documents').get(id)
        request.onsuccess = () => resolve(request.result?.deletedAt ? undefined : request.result)
        request.onerror = () => reject(request.error)
      })
    } finally { database.close() }
  },
  async write(id, title, bytes, expectedRevision) {
    const database = await openDatabase()
    try {
      return await new Promise<GraphDocument>((resolve, reject) => {
        // Read + compare + write in one transaction: two tabs cannot overwrite each other.
        const transaction = database.transaction('documents', 'readwrite')
        const documents = transaction.objectStore('documents')
        const request = documents.get(id)
        let result: GraphDocument
        let conflict = false
        request.onsuccess = () => {
          if (request.result?.deletedAt || (request.result?.revision ?? 0) !== expectedRevision) {
            conflict = true
            transaction.abort()
            return
          }
          // Folder/title metadata belongs to the library; an older editor tab must
          // never overwrite a rename or move during content autosave.
          result = { ...request.result, id, title: request.result?.title ?? title, revision: expectedRevision + 1, bytes, updatedAt: Date.now() }
          documents.put(result)
        }
        transaction.oncomplete = () => resolve(result!)
        transaction.onabort = () => reject(new Error(conflict
          ? '另一页面已更新此文档。请先导出当前内容备份，再刷新载入最新版本。'
          : `保存失败：${transaction.error?.message ?? '存储事务中断'}`))
        transaction.onerror = () => { /* onabort reports the final transaction outcome */ }
      })
    } finally { database.close() }
  },
}

export async function listBrowserGraphDocuments(): Promise<GraphDocument[]> {
  const database = await openDatabase()
  try {
    return await new Promise((resolve, reject) => {
      const request = database.transaction('documents').objectStore('documents').getAll()
      request.onsuccess = () => resolve(request.result.filter((item: GraphDocument) => !item.deletedAt).sort((a: GraphDocument, b: GraphDocument) => b.updatedAt - a.updatedAt))
      request.onerror = () => reject(request.error)
    })
  } finally { database.close() }
}

/** Library operations stay outside GraphStorage: embedded note editors need only
 * read/write, while this standalone host owns folders and file lifecycle. */
export async function listGraphFolders(): Promise<GraphFolder[]> {
  const database = await openDatabase()
  try {
    return await new Promise((resolve, reject) => {
      const request = database.transaction('folders').objectStore('folders').getAll()
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
  } finally { database.close() }
}

export async function changeGraphLibrary(action:
  | { type: 'folder'; folder: GraphFolder }
  | { type: 'remove-folder'; id: string }
  | { type: 'document'; id: string; title?: string; folderId?: string; remove?: boolean }
): Promise<void> {
  const database = await openDatabase()
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = database.transaction(['documents', 'folders'], 'readwrite')
      const docs = tx.objectStore('documents'), folders = tx.objectStore('folders')
      let error = '文件操作失败'
      const fail = (message: string) => { error = message; tx.abort() }
      const inFolder = (id: string, apply: () => void) => {
        if (!id) { apply(); return }
        const request = folders.get(id)
        request.onsuccess = () => request.result ? apply() : fail('目标文件夹不存在')
      }
      if (action.type === 'folder') {
        inFolder(action.folder.parentId, () => folders.put(action.folder))
      } else if (action.type === 'document') {
        const request = docs.get(action.id)
        request.onsuccess = () => {
          const doc: GraphDocument | undefined = request.result
          if (!doc || doc.deletedAt) { fail('文件已被删除或尚未保存'); return }
          inFolder(action.folderId ?? '', () => docs.put({ ...doc,
            ...(action.title !== undefined ? { title: action.title } : {}),
            ...(action.folderId !== undefined ? { folderId: action.folderId } : {}),
            // Retain a tombstone so a stale tab cannot recreate a deleted file.
            ...(action.remove ? { deletedAt: Date.now(), bytes: new Uint8Array() } : {}),
          }))
        }
      } else {
        const allDocs = docs.getAll(), allFolders = folders.getAll()
        allFolders.onsuccess = () => {
          if (allDocs.result.some((doc: GraphDocument) => !doc.deletedAt && doc.folderId === action.id)
            || allFolders.result.some((folder: GraphFolder) => folder.parentId === action.id)) {
            fail('请先移走文件和子文件夹'); return
          }
          folders.delete(action.id)
        }
      }
      tx.oncomplete = () => resolve()
      tx.onabort = () => reject(new Error(tx.error?.message ?? error))
      tx.onerror = () => { /* onabort reports final result */ }
    })
  } finally { database.close() }
}

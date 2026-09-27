/** Read-only input for the explicitly owned legacy browser migration. */
import type { GraphDocument, GraphFolder } from './storage'
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

export async function listBrowserGraphDocuments(): Promise<GraphDocument[]> {
  const database = await openDatabase()
  try {
    return await new Promise((resolve, reject) => {
      const request = database.transaction('documents').objectStore('documents').getAll()
      request.onsuccess = () => resolve(request.result.filter((item: GraphDocument) => !('deletedAt' in item && item.deletedAt)).sort((a: GraphDocument, b: GraphDocument) => b.updatedAt - a.updatedAt))
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


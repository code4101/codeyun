import type { ResourceNode } from '@/components/resource-explorer/resourceTree'
import type { GraphDocument, GraphFolder } from './storage'

export function canManageResource(node: ResourceNode, documents: GraphDocument[], folders: GraphFolder[]) {
  return node.kind === 'directory' ? folders.some(folder => `folder:${folder.id}` === node.id)
    : documents.some(doc => `file:${doc.id}` === node.id && doc.role === 'manager')
}

/** File rows mean their containing directory; prevent no-ops and directory cycles. */
export function resourceDestination(target: ResourceNode | null, documents: GraphDocument[]) {
  return !target ? '' : target.kind === 'directory' ? target.id.slice(7)
    : documents.find(doc => `file:${doc.id}` === target.id)?.folderId ?? ''
}

export function canMoveResource(source: ResourceNode, target: ResourceNode | null, documents: GraphDocument[], folders: GraphFolder[]) {
  if (!canManageResource(source, documents, folders)) return false
  const destination = resourceDestination(target, documents)
  if (destination && !folders.some(folder => folder.id === destination)) return false
  const id = source.id.slice(source.kind === 'directory' ? 7 : 5)
  const parent = source.kind === 'directory' ? folders.find(folder => folder.id === id)?.parentId
    : documents.find(doc => doc.id === id)?.folderId ?? ''
  if (parent === destination) return false
  if (source.kind === 'directory') {
    let cursor = destination
    const seen = new Set<string>()
    while (cursor && !seen.has(cursor)) {
      if (cursor === id) return false
      seen.add(cursor)
      cursor = folders.find(folder => folder.id === cursor)?.parentId ?? ''
    }
  }
  return true
}

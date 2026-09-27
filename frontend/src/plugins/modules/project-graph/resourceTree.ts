import type { ResourceNode } from '@/components/resource-explorer/resourceTree'
import type { GraphDocument, GraphFolder } from './storage'
import { graphFileName } from './fileName'

/** PG 只提供资源数据，目录展开、压缩、键盘导航由通用资源树处理。 */
export function graphResourceTree(folders: GraphFolder[], documents: Pick<GraphDocument, 'id' | 'title' | 'folderId'>[], expanded: ReadonlySet<string>): ResourceNode[] {
  const root: ResourceNode[] = []
  const directories = new Map(folders.map(folder => [folder.id, {
    id: `folder:${folder.id}`, name: folder.title, kind: 'directory' as const,
    children: [] as ResourceNode[], loaded: true, expanded: expanded.has(`folder:${folder.id}`),
  }]))
  for (const folder of folders) (directories.get(folder.parentId)?.children ?? root).push(directories.get(folder.id)!)
  for (const doc of documents) (directories.get(doc.folderId ?? '')?.children ?? root).push({ id: `file:${doc.id}`, name: graphFileName(doc.title), kind: 'file' })
  return root
}

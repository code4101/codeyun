/** PRG gallery v1. Canvas and stored subgraphs are disjoint UUID sets.
 * Organization is independent of task status. All operations return a new
 * document projection, allowing one atomic save/commit and undo for both sides.
 * Assets remain document-wide references, never copied into individual items.
 */
import type { Objects } from '../../../collaboration/objectState.ts'

export const GALLERY_INDEX = '@gallery:index'
export const GALLERY_ITEM = '@gallery:item:'
export interface GalleryGroup { id: string; title: string }
export interface GalleryItem { id: string; groupId: string; title: string; createdAt: number; objects: Objects }
export interface GalleryPreview {
  nodes: { id: string; x: number; y: number; width: number; height: number }[]
  edges: { source: string; target: string }[]
}
export interface GallerySnapshot {
  groups: GalleryGroup[]
  items: { id: string; groupId: string; title: string; createdAt: number; objectCount: number; preview?: GalleryPreview }[]
  selection: { ids: string[]; title: string }
  readOnly: boolean
}
/** Viewport coordinates cross the iframe boundary; the host owns drop targets. */
export interface GalleryCanvasDrag { phase: 'move' | 'end' | 'cancel'; x: number; y: number; ids: string[]; title: string }
export type GalleryCommand =
  | { action: 'create-group'; title: string }
  | { action: 'rename-group'; groupId: string; title: string }
  | { action: 'delete-group'; groupId: string }
  | { action: 'store'; groupId: string; selectedIds: string[] }
  | { action: 'take'; itemId: string }
  | { action: 'move'; itemId: string; groupId: string }
  | { action: 'rename-item'; itemId: string; title: string }

export function graphReferences(value: any): string[] {
  if (!value || typeof value !== 'object') return []
  if (Object.keys(value).length === 1 && typeof value.$graphRef === 'string') return [value.$graphRef]
  return Object.values(value).flatMap(graphReferences)
}
export function defaultGroups(): GalleryGroup[] {
  return [{ id: 'todo', title: '待办' }, { id: 'done', title: '完成' }, { id: 'abandoned', title: '放弃' }]
}

/** A bounded directory preview, without transferring stored bodies or assets.
 * Native collision rectangles retain spatial layout; objects without geometry
 * use a grid. Large subgraphs show their first 32 entities only. */
export function galleryPreview(objects: Objects): GalleryPreview {
  const order = objects['@order']?.value as string[] ?? []
  const raw = order.filter(id => !String(objects[id]?._).includes('Edge')).slice(0, 32).map((id, index) => {
    const shape = objects[id]?.collisionBox?.shapes?.find((value: any) => value.location && value.size)
    const finite = (value: unknown, fallback: number) => typeof value === 'number' && Number.isFinite(value) ? value : fallback
    return { id, x: finite(shape?.location?.x, index % 4 * 100), y: finite(shape?.location?.y, Math.floor(index / 4) * 70),
      width: Math.max(1, finite(shape?.size?.x, 70)), height: Math.max(1, finite(shape?.size?.y, 35)) }
  })
  if (!raw.length) return { nodes: [], edges: [] }
  const left = Math.min(...raw.map(node => node.x)), top = Math.min(...raw.map(node => node.y))
  const width = Math.max(...raw.map(node => node.x + node.width)) - left
  const height = Math.max(...raw.map(node => node.y + node.height)) - top
  const scale = Math.min(80 / width, 46 / height)
  const nodes = raw.map(node => ({ id: node.id, x: (node.x - left) * scale + (96 - width * scale) / 2,
    y: (node.y - top) * scale + (62 - height * scale) / 2, width: node.width * scale, height: node.height * scale }))
  const ids = new Set(nodes.map(node => node.id))
  const edges = order.filter(id => String(objects[id]?._).includes('Edge')).flatMap(id => {
    const refs = graphReferences(objects[id]).filter(ref => ids.has(ref))
    return refs.length >= 2 ? [{ source: refs[0]!, target: refs[1]! }] : []
  }).slice(0, 64)
  return { nodes, edges }
}
export function gallerySnapshot(objects: Objects, selection: string[] = [], readOnly = false): GallerySnapshot {
  const groups = objects[GALLERY_INDEX]?.groups ?? defaultGroups()
  const items = Object.entries(objects).filter(([key]) => key.startsWith(GALLERY_ITEM)).map(([, value]) => {
    const item = value as unknown as GalleryItem
    return { id: item.id, groupId: item.groupId, title: item.title, createdAt: item.createdAt, objectCount: item.objects['@order'].value.length, preview: galleryPreview(item.objects) }
  })
  const title = selection.map(id => objects[id]?.text).find(text => typeof text === 'string' && text.trim()) ?? '选中子图'
  return { groups, items, selection: { ids: selection, title: String(title).slice(0, 120) }, readOnly }
}
function name(value: string) {
  const title = value.trim()
  if (!title || title.length > 120) throw new Error('名称需为 1–120 个字符')
  return title
}

/** Include selected groups' children and internal edges. Never silently cut a
 * crossing edge or pull an entire connected project into the gallery. */
export function selectedSubgraph(objects: Objects, selected: string[]): string[] {
  const order = objects['@order'].value as string[], ids = new Set(selected)
  if (!ids.size || [...ids].some(id => !order.includes(id))) throw new Error('选中对象已变化，请重新选择')
  const visit = (id: string) => {
    for (const ref of graphReferences(objects[id])) {
      if (!order.includes(ref)) throw new Error('子图包含无效引用')
      if (!ids.has(ref)) { ids.add(ref); visit(ref) }
    }
  }
  selected.forEach(visit)
  for (const id of order) {
    const refs = graphReferences(objects[id])
    if (String(objects[id]._).includes('Edge') && refs.length && refs.every(ref => ids.has(ref))) ids.add(id)
  }
  if (order.some(id => !ids.has(id) && graphReferences(objects[id]).some(ref => ids.has(ref))))
    throw new Error('子图仍与画布其他对象有连线或分组关系，请一并选中关联对象，或先调整关系')
  return order.filter(id => ids.has(id))
}

export function changeGallery(objects: Objects, command: GalleryCommand, newId = crypto.randomUUID(), now = Date.now()): Objects {
  const next = { ...objects }
  const groups: GalleryGroup[] = (objects[GALLERY_INDEX]?.groups ?? defaultGroups()).map((group: GalleryGroup) => ({ ...group }))
  const group = 'groupId' in command ? groups.find(item => item.id === command.groupId) : undefined
  if ('groupId' in command && !group) throw new Error('图库分组已变化，请刷新后重试')
  const itemKey = 'itemId' in command ? GALLERY_ITEM + command.itemId : ''
  const item = itemKey ? objects[itemKey] as unknown as GalleryItem | undefined : undefined
  if (itemKey && !item) throw new Error('子图已取回或不存在')
  if (command.action === 'create-group') groups.push({ id: newId, title: name(command.title) })
  if (command.action === 'rename-group') group!.title = name(command.title)
  if (command.action === 'delete-group') {
    if (Object.entries(objects).some(([key, value]) => key.startsWith(GALLERY_ITEM) && value.groupId === command.groupId))
      throw new Error('请先取回或移动分组中的子图')
    groups.splice(groups.findIndex(group => group.id === command.groupId), 1)
  }
  if (command.action === 'store') {
    const ids = selectedSubgraph(objects, command.selectedIds), selected = new Set(ids)
    const snapshot = gallerySnapshot(objects, ids)
    const stored = Object.fromEntries(ids.map(id => [id, objects[id]]))
    stored['@order'] = { value: ids }
    next[GALLERY_ITEM + newId] = { id: newId, groupId: command.groupId, title: snapshot.selection.title,
      createdAt: now, objects: stored }
    for (const id of ids) delete next[id]
    next['@order'] = { value: (objects['@order'].value as string[]).filter(id => !selected.has(id)) }
  }
  if (command.action === 'take') {
    const ids = item!.objects['@order'].value as string[]
    if (ids.some(id => objects[id])) throw new Error('画布已存在同一对象，无法重复取回')
    for (const id of ids) next[id] = item!.objects[id]
    next['@order'] = { value: [...objects['@order'].value, ...ids] }
    delete next[itemKey]
  }
  if (command.action === 'move') next[itemKey] = { ...item!, groupId: command.groupId }
  if (command.action === 'rename-item') next[itemKey] = { ...item!, title: name(command.title) }
  next[GALLERY_INDEX] = { version: 1, groups }
  return next
}

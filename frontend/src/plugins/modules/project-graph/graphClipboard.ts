import { equal, type Objects } from '../../../collaboration/objectState.ts'
import { graphReferences, placeGalleryObjects } from './gallery.ts'

/** Portable canvas selection, distinct from the resource explorer's file clipboard. */
export interface GraphClipboard {
  format: 'codeyun.pg-selection'; version: 1
  objects: Objects
  attachments: Record<string, { type: string; data: string }>
  tags: string[]
  text: string
}

export function selectionObjects(objects: Objects, selected: string[]): Objects {
  const order = objects['@order']?.value as string[] ?? [], ids = new Set<string>()
  const visit = (id: string) => {
    if (ids.has(id)) return
    if (!order.includes(id) || !objects[id]) throw new Error('选中对象包含失效引用')
    ids.add(id)
    graphReferences(objects[id]).forEach(visit)
  }
  selected.forEach(visit)
  for (const id of order) {
    const refs = graphReferences(objects[id])
    if (String(objects[id]._).includes('Edge') && refs.length && refs.every(ref => ids.has(ref))) ids.add(id)
  }
  if (!ids.size) throw new Error('请先选择要复制的图对象')
  const copied = order.filter(id => ids.has(id))
  return structuredClone({ ...Object.fromEntries(copied.map(id => [id, objects[id]])), '@order': { value: copied } })
}

export function attachmentIds(objects: Objects): string[] {
  const ids = new Set<string>()
  const visit = (value: any) => {
    if (!value || typeof value !== 'object') return
    if (typeof value.attachmentId === 'string' && value.attachmentId) ids.add(value.attachmentId)
    Object.values(value).forEach(visit)
  }
  visit(objects)
  return [...ids]
}

export function validateClipboard(value: unknown): GraphClipboard {
  const packet = value as GraphClipboard
  if (packet?.format !== 'codeyun.pg-selection' || packet.version !== 1 || !packet.objects
    || !packet.attachments || typeof packet.attachments !== 'object' || !Array.isArray(packet.tags)
    || packet.tags.some(tag => typeof tag !== 'string') || typeof packet.text !== 'string') throw new Error('不支持的图剪贴板格式')
  const order = packet.objects['@order']?.value
  if (!Array.isArray(order) || !order.length || new Set(order).size !== order.length
    || order.some(id => typeof id !== 'string' || id.startsWith('@') || !Object.hasOwn(packet.objects, id)
      || packet.objects[id]?.uuid !== id || typeof packet.objects[id]?._ !== 'string')
    || Object.keys(packet.objects).some(id => id !== '@order' && !order.includes(id))) throw new Error('图剪贴板对象无效')
  for (const id of order) if (graphReferences(packet.objects[id]).some(ref => !order.includes(ref))) throw new Error('图剪贴板引用不完整')
  for (const id of attachmentIds(packet.objects)) {
    const blob = packet.attachments[id]
    if (!blob || typeof blob.type !== 'string' || typeof blob.data !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(blob.data)) throw new Error('图剪贴板附件不完整')
  }
  return packet
}

/** Remap every object and asset once, including edge identities and group references. */
export function preparePaste(packet: GraphClipboard, position?: { x: number; y: number }, uuid: () => string = () => crypto.randomUUID()) {
  validateClipboard(packet)
  const objectIds = new Map<string, string>((packet.objects['@order'].value as string[]).map(id => [id, uuid()]))
  const assets = new Map(attachmentIds(packet.objects).map(id => [id, uuid()]))
  const rewrite = (value: any): any => {
    if (Array.isArray(value)) return value.map(rewrite)
    if (!value || typeof value !== 'object') return value
    if (Object.keys(value).length === 1 && typeof value.$graphRef === 'string') return { $graphRef: objectIds.get(value.$graphRef)! }
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
      key === 'uuid' && typeof item === 'string' && objectIds.has(item) ? objectIds.get(item)
        : key === 'attachmentId' && typeof item === 'string' && assets.has(item) ? assets.get(item) : rewrite(item)]))
  }
  let objects: Objects = Object.fromEntries([...objectIds].map(([old, id]) => [id, rewrite(packet.objects[old])]))
  // A new identity is a newly created node; source audit dates belong to the original.
  for (const value of Object.values(objects)) { delete value.createdAt; delete value.updatedAt }
  objects['@order'] = { value: [...objectIds.values()] }
  if (position) objects = placeGalleryObjects(objects, position)
  return { objects, assets: Object.fromEntries([...assets].map(([old, id]) => [id, packet.attachments[old]])), tags: packet.tags }
}

export function insertPaste(before: Objects, prepared: ReturnType<typeof preparePaste>, extension: (type: string) => string | null) {
  const ids = prepared.objects['@order'].value as string[]
  if (ids.some(id => before[id])) throw new Error('粘贴对象身份冲突，请重试')
  const next: Objects = { ...before, ...prepared.objects, '@order': { value: [...before['@order'].value, ...ids] } }
  for (const [id, asset] of Object.entries(prepared.assets)) next[`@attachment:${id}.${extension(asset.type) ?? 'bin'}`] = { value: asset.data }
  next['@tags'] = { value: [...new Set([...(before['@tags']?.value ?? []), ...prepared.tags])] }
  return next
}

/** Cut removes the copied snapshot and crossing edges, while retaining enclosing groups. */
export function removeCut(before: Objects, packet: GraphClipboard): Objects {
  const removed = new Set(packet.objects['@order'].value as string[])
  for (const id of removed) if (!equal(before[id], packet.objects[id])) throw new Error('复制期间选中内容发生变化，未剪切，请重新操作')
  for (const id of before['@order'].value as string[]) if (String(before[id]._).includes('Edge') && graphReferences(before[id]).some(ref => removed.has(ref))) removed.add(id)
  const prune = (value: any): any => {
    if (Array.isArray(value)) return value.filter(item => !graphReferences(item).some(ref => removed.has(ref))).map(prune)
    if (!value || typeof value !== 'object') return value
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, prune(item)]))
  }
  const next = { ...before }
  for (const id of before['@order'].value as string[]) {
    if (removed.has(id)) delete next[id]
    else if (graphReferences(before[id]).some(ref => removed.has(ref))) next[id] = prune(before[id])
  }
  next['@order'] = { value: before['@order'].value.filter((id: string) => !removed.has(id)) }
  return next
}

const marker = 'codeyun-pg-selection'
export function clipboardHtml(packet: GraphClipboard): string {
  const bytes = new TextEncoder().encode(JSON.stringify(validateClipboard(packet)))
  let binary = ''
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
  const text = packet.text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  return `<div data-${marker}="${btoa(binary)}"><pre>${text}</pre></div>`
}
export function parseClipboardHtml(html: string): GraphClipboard | null {
  const encoded = html.match(/data-codeyun-pg-selection=["']([A-Za-z0-9+/=]+)["']/)?.[1]
  if (!encoded) return null
  return validateClipboard(JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(encoded), c => c.charCodeAt(0)))))
}

// The host survives iframe replacement. Keep one account-scoped fallback; the OS
// clipboard remains authoritative whenever a read succeeds, even for plain text.
let cached: { scope: string; packet: GraphClipboard } | null = null
export function setGraphClipboard(scope: string, packet: GraphClipboard) { cached = { scope, packet: structuredClone(validateClipboard(packet)) } }
export function getGraphClipboard(scope: string) { return cached?.scope === scope ? structuredClone(cached.packet) : null }

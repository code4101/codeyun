/** 工具的位置、排列顺序与展开集合相互独立；单击独占，修饰键切换集合成员。 */
export const dockSides = ['left', 'right', 'bottom'] as const
export type DockSide = typeof dockSides[number]
export const dockSideLabels: Record<DockSide, string> = { left: '左侧', right: '右侧', bottom: '底部' }
export type DockIcon = 'document' | 'search' | 'info' | 'ocr' | 'outline' | 'settings'
export interface DockTool { id: string; title: string; icon: DockIcon; position: DockSide; open?: boolean }
export interface DockRegionState { visible: boolean; size: number; tools: string[]; active: string[]; weights: Record<string, number> }
export interface DockLayout { version: 2; regions: Record<DockSide, DockRegionState> }
export const defaultDockSizes = { left: 290, right: 240, bottom: 240 }
export function bounded(value: unknown, fallback: number, min: number, max: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : fallback
}
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
export function createDockLayout(tools: readonly DockTool[]): DockLayout {
  const regions = Object.fromEntries(dockSides.map(side => [side, { visible: true, size: defaultDockSizes[side], tools: [] as string[], active: [] as string[], weights: {} }])) as DockLayout['regions']
  const seen = new Set<string>()
  for (const tool of tools) {
    if (seen.has(tool.id)) throw new Error(`Duplicate dock tool: ${tool.id}`)
    seen.add(tool.id)
    const region = regions[tool.position]
    region.tools.push(tool.id)
    if (tool.open) region.active.push(tool.id)
  }
  return { version: 2, regions }
}
/** 旧六分区合并为三个区域，保留位置、顺序、展开状态及尺寸。 */
export function restoreDockLayout(tools: readonly DockTool[], saved: unknown): DockLayout {
  const layout = createDockLayout(tools), data = record(saved)
  if (data.version !== 1 && data.version !== 2) return layout
  const regions = record(data.regions), groups = record(data.groups)
  const known = new Set(tools.map(t => t.id)), seen = new Set<string>()
  for (const side of dockSides) {
    const stored = record(regions[side])
    const oldPositions = side === 'bottom' ? ['bottom-left', 'bottom-right'] : [`${side}-top`, `${side}-bottom`]
    const oldGroups = oldPositions.map(p => record(groups[p]))
    const ids = data.version === 1 ? oldGroups.flatMap(g => Array.isArray(g.tools) ? g.tools : []) : stored.tools
    const active = data.version === 1 ? oldGroups.map(g => g.active) : stored.active
    const region = layout.regions[side]
    region.tools = (Array.isArray(ids) ? ids : []).filter((id): id is string => {
      if (typeof id !== 'string' || !known.has(id) || seen.has(id)) return false
      seen.add(id); return true
    })
    region.active = region.tools.filter(id => Array.isArray(active) && active.includes(id))
    region.visible = stored.visible !== false
    region.size = bounded(stored.size, defaultDockSizes[side], 160, 900)
    const weights = record(stored.weights)
    for (const id of region.tools) region.weights[id] = bounded(weights[id], 1, .01, 100)
    if (data.version === 1 && region.active.length === 2) {
      const ratio = bounded(stored.ratio, .5, .15, .85)
      region.weights[region.active[0]!] = ratio * 2
      region.weights[region.active[1]!] = (1 - ratio) * 2
    }
  }
  for (const tool of tools) if (!seen.has(tool.id)) {
    layout.regions[tool.position].tools.push(tool.id)
    if (tool.open) layout.regions[tool.position].active.push(tool.id)
  }
  return layout
}
export function locateTool(layout: DockLayout, id: string): DockSide | undefined {
  return dockSides.find(side => layout.regions[side].tools.includes(id))
}
export function openDockTool(layout: DockLayout, id: string): void {
  const side = locateTool(layout, id)
  if (!side) return
  const region = layout.regions[side]
  if (!region.active.includes(id)) region.active.push(id)
  region.visible = true
}
export function selectDockTool(layout: DockLayout, id: string, additive = false): void {
  const side = locateTool(layout, id)
  if (!side) return
  const region = layout.regions[side]
  const selected = region.visible && region.active.includes(id)
  if (additive) region.active = selected ? region.active.filter(x => x !== id) : [...new Set([...region.active, id])]
  else region.active = selected && region.active.length === 1 ? [] : [id]
  region.visible = true
}
export function moveDockTool(layout: DockLayout, id: string, to: DockSide, before?: string): void {
  const from = locateTool(layout, id)
  if (!from || !dockSides.includes(to) || before === id) return
  const source = layout.regions[from], target = layout.regions[to]
  source.tools = source.tools.filter(x => x !== id)
  if (from !== to) {
    source.active = source.active.filter(x => x !== id)
    target.weights[id] = source.weights[id] ?? 1
    delete source.weights[id]
  }
  const index = before ? target.tools.indexOf(before) : -1
  target.tools.splice(index < 0 ? target.tools.length : index, 0, id)
  openDockTool(layout, id)
}
export function isDockToolVisible(layout: DockLayout, id: string): boolean {
  const side = locateTool(layout, id)
  return Boolean(side && layout.regions[side].visible && layout.regions[side].active.includes(id))
}

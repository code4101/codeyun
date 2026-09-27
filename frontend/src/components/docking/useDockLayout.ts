import { ref, watch } from 'vue'
import { bounded, createDockLayout, defaultDockSizes, isDockToolVisible, locateTool, moveDockTool, openDockTool, selectDockTool, restoreDockLayout, type DockSide, type DockTool } from './dockLayout'

/** 每个阅读器实例独立工作；同类阅读器下次打开恢复布局，业务阅读进度另行保存。 */
export function useDockLayout(storageKey: string, tools: readonly DockTool[]) {
  let saved: unknown
  try { saved = JSON.parse(localStorage.getItem(storageKey) || 'null') } catch { /* 无存储时照常使用。 */ }
  const state = ref(restoreDockLayout(tools, saved))
  let customized = Boolean(saved && typeof saved === 'object' && 'version' in saved && (saved.version === 1 || saved.version === 2))
  watch(state, value => {
    customized = true
    try { localStorage.setItem(storageKey, JSON.stringify(value)) } catch { /* 本次会话仍可调整。 */ }
  }, { deep: true, flush: 'sync' })
  const tool = (id: string | null) => tools.find(t => t.id === id)
  const visible = (id: string) => isDockToolVisible(state.value, id)
  const regionOpen = (side: DockSide) => state.value.regions[side].visible && state.value.regions[side].active.length > 0
  return {
    tools, state, tool, visible, regionOpen,
    position: (id: string) => locateTool(state.value, id),
    open: (id: string) => openDockTool(state.value, id),
    toggle: (id: string, additive = false) => selectDockTool(state.value, id, additive),
    close(id: string) { const p = locateTool(state.value, id); if (p) state.value.regions[p].active = state.value.regions[p].active.filter(x => x !== id) },
    move: (id: string, to: DockSide, before?: string) => moveDockTool(state.value, id, to, before),
    toggleRegion(side: DockSide) { state.value.regions[side].visible = !state.value.regions[side].visible },
    resize(side: DockSide, size: number) { state.value.regions[side].size = bounded(size, defaultDockSizes[side], 160, 900) },
    split(side: DockSide, first: string, second: string, ratio: number) {
      const region = state.value.regions[side]
      const total = (region.weights[first] ?? 1) + (region.weights[second] ?? 1)
      const value = bounded(ratio, .5, .1, .9)
      region.weights[first] = total * value; region.weights[second] = total * (1 - value)
    },
    reset() { state.value = createDockLayout(tools) },
    /** 旧 PDF 进度只在尚无新布局时迁移一次，切书不得覆盖用户的工作区布局。 */
    importLegacy(id: string, open: boolean) {
      if (customized || !tool(id)) return
      const side = locateTool(state.value, id)!
      state.value.regions[side].active = open ? [id] : []
      customized = true
    },
  }
}
export type DockController = ReturnType<typeof useDockLayout>

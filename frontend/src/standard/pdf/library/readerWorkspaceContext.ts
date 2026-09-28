import { computed, inject, type ComputedRef, type ShallowRef, type InjectionKey } from 'vue'
import { useDockLayout, type DockController } from '@/components/docking/useDockLayout'
import { dockSides, type DockTool, type DockLayout } from '@/components/docking/dockLayout'

export interface ReaderTabContext {
  active: ComputedRef<boolean>
  dock: DockController
  title: (value: string) => void
  canPersist: () => boolean
}
export const readerTabContext: InjectionKey<ReaderTabContext> = Symbol('reader-tab')
export const readerSurfaceContext: InjectionKey<{ pageHref: ComputedRef<string> }> = Symbol('reader-surface')

/** 功能页只替换中央内容，继续使用当前资源的工具实例与停靠布局。 */
export const readerCentralViewContext: InjectionKey<{
  shelfActive: ComputedRef<boolean>
  target: ShallowRef<HTMLElement | null>
  dock: ShallowRef<DockController | null>
}> = Symbol('reader-central-view')

/** 工具能力属于阅读器插件，布局属于工作区；过滤不适用的工具但保留其保存位置。 */
export function useReaderDock(storageKey: string, tools: readonly DockTool[]): DockController {
  const context = inject(readerTabContext, null)
  if (!context) return useDockLayout(storageKey, tools)
  const shared = context.dock
  const supported = new Set(tools.map(tool => tool.id))
  const available = shared.tools.filter(tool => supported.has(tool.id))
  const state = computed(() => ({
    ...shared.state.value,
    regions: Object.fromEntries(dockSides.map(side => {
      const region = shared.state.value.regions[side]
      return [side, { ...region, tools: region.tools.filter(id => supported.has(id)), active: region.active.filter(id => supported.has(id)) }]
    })) as DockLayout['regions'],
  }))
  return {
    ...shared, tools: available, state,
    visible: id => context.active.value && supported.has(id) && shared.visible(id),
    regionOpen: side => state.value.regions[side].visible && state.value.regions[side].active.length > 0,
    // 旧单书 sidebar_open 不得覆盖工作区布局。
    importLegacy() {},
  }
}

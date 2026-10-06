import type { DockLayout } from './dockLayout'
export const BOTTOM_DOCK_RAIL_SIZE = 28

/** 保持左右/底部空间关系，按窗口预算压缩面板，始终给正文留空间。 */
export function dockGeometry(layout: DockLayout, width: number, height: number, contentOnly = false, dragging = false) {
  const sideWidth = (side: 'left' | 'right') => {
    const region = layout.regions[side]
    if (contentOnly || !region.visible || (!region.tools.length && !dragging)) return { rail: 0, panel: 0 }
    return { rail: 38, panel: region.active.length ? region.size : dragging ? 72 : 0 }
  }
  const left = sideWidth('left'), right = sideWidth('right')
  const available = Math.max(0, width - left.rail - right.rail - Math.min(180, width * .35))
  const requested = left.panel + right.panel
  const scale = requested ? Math.min(1, available / requested) : 0
  const bottom = layout.regions.bottom
  const bottomVisible = !contentOnly && bottom.visible && (bottom.tools.length || dragging)
  const bottomRail = bottomVisible ? BOTTOM_DOCK_RAIL_SIZE : 0
  const bottomPanel = bottomVisible && (bottom.active.length || dragging)
    ? Math.min(bottom.active.length ? bottom.size : 48, Math.max(0, height * .45 - bottomRail)) : 0
  return {
    gridTemplateColumns: `${left.rail + left.panel * scale}px minmax(0, 1fr) ${right.rail + right.panel * scale}px`,
    gridTemplateRows: `minmax(0, 1fr) ${bottomRail + bottomPanel}px`,
  }
}

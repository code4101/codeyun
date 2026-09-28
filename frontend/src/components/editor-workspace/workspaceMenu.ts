import { dockSides, dockSideLabels } from '@/components/docking/dockLayout'
import type { DockController } from '@/components/docking/useDockLayout'

/** Commands belong to the active editor; their menu belongs to the workspace. */
export interface WorkspaceMenuItem {
  id: string
  label: string
  disabled?: boolean
  separator?: boolean
  children?: WorkspaceMenuItem[]
}

/** 工作区布局命令统一由“窗口”菜单提供。 */
export function dockWindowMenuItems(dock: DockController): WorkspaceMenuItem[] {
  return [
    { id: 'layout-regions', label: '区域', children: dockSides.map(side => ({
      id: `layout:region:${side}`,
      label: `${dock.state.value.regions[side].visible ? '✓ ' : ''}${dockSideLabels[side]}`,
    })) },
    { id: 'layout:reset', label: '恢复默认布局' },
  ]
}

export function executeDockWindowCommand(dock: DockController, id: string): boolean {
  if (id === 'layout:reset') { dock.reset(); return true }
  const side = dockSides.find(side => id === `layout:region:${side}`)
  if (side) { dock.toggleRegion(side); return true }
  return false
}

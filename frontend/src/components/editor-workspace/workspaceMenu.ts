/**
 * CodeYun 工作台（Workbench）——通用 UI 术语与代码入口。
 *
 * 工作台：图书馆、Project Graph 共用的菜单栏、活动栏、可停靠工具面板、中央标签视图框架。
 * 工作区（Workspace）：某个业务/用户保存的打开资源、活动标签、排列及布局状态。
 * 视图（View）：一个内容或功能展示单元；中央标签可承载正文、PG 画布、书架、设置等，
 * 不要求绑定文件。工具视图（Tool View）放在周边停靠区，辅助导航或处理当前内容。
 *
 * 当前工作台由以下组件组合，尚无单一 Workbench.vue：
 * - 本目录 WorkspaceMenu.vue / EditorTabs.vue：顶层菜单与中央标签。
 * - ../docking/DockWorkspace.vue / useDockLayout.ts：活动栏、工具停靠与分栏布局。
 * - ../resource-explorer/ResourceExplorer.vue：可复用资源目录树。
 * - ../../standard/pdf/library/ReaderWorkspace.vue：图书馆的业务装配入口。
 * - ../../plugins/modules/project-graph/GraphWorkspace.vue：PG 的业务装配入口。
 *
 * 修改“工作台”交互时先定位共享组件，再检查两个业务入口；格式解析、保存与权限归业务层。
 * 现有 Editor/Reader/Workspace 命名兼容保留，不代表另有一套 UI；工作台也不是仓库/目录工作区。
 */
import { dockSides, dockSideLabels } from '@/components/docking/dockLayout'
import type { DockController } from '@/components/docking/useDockLayout'

/** 工作台菜单描述；命令由业务装配层或当前内容视图执行。 */
export interface WorkspaceMenuItem {
  id: string
  label: string
  disabled?: boolean
  disabledReason?: string
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

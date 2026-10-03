import type { DockLayout } from '@/components/docking/dockLayout'

export interface ReaderTab {
  kind: 'pdf' | 'ebook' | 'skill'
  id: string
  publicId?: number
  title?: string
  bookshelfId?: string
  pageSize?: number
  readingMode?: 'scroll' | 'paginated'
}
export const readerTabKey = (tab: ReaderTab) => `${tab.kind}:${tab.id}`
export interface ReaderWorkspaceState { tabs: ReaderTab[]; active: string; layout: Partial<DockLayout> }
export interface WorkspaceCommand {
  action: 'open' | 'close' | 'activate' | 'move' | 'layout' | 'title'
  title?: string
  tab?: ReaderTab; key?: string; before?: string; layout?: DockLayout
}

/** 本地存储不可信：过滤未知阅读器和重复标签，修复失效的选中状态。 */
export function restoreReaderWorkspace(saved: unknown): ReaderWorkspaceState {
  const value = saved && typeof saved === 'object' ? saved as Partial<ReaderWorkspaceState> : {}
  const tabs: ReaderTab[] = []
  for (const tab of Array.isArray(value.tabs) ? value.tabs : []) {
    if (!tab || !['pdf', 'ebook', 'skill'].includes(tab.kind) || typeof tab.id !== 'string' || !tab.id || tabs.some(item => readerTabKey(item) === readerTabKey(tab))) continue
    tabs.push({ kind: tab.kind, id: tab.id,
      ...(typeof tab.title === 'string' ? { title: tab.title } : {}),
      ...(typeof tab.bookshelfId === 'string' ? { bookshelfId: tab.bookshelfId } : {}),
      ...(Number.isSafeInteger(tab.publicId) && tab.publicId! > 0 ? { publicId: tab.publicId } : {}),
      ...(Number.isInteger(tab.pageSize) && tab.pageSize! >= 200 && tab.pageSize! <= 20000 ? { pageSize: tab.pageSize } : {}),
      ...(tab.readingMode === 'scroll' || tab.readingMode === 'paginated' ? { readingMode: tab.readingMode } : {}),
    })
  }
  return { tabs, active: tabs.some(tab => readerTabKey(tab) === value.active) ? value.active! : tabs[0] ? readerTabKey(tabs[0]) : '',
    layout: value.layout && typeof value.layout === 'object' && !Array.isArray(value.layout) ? value.layout : {} }
}

/** 标签操作只修改本地现场，公开资源也可在访客工作区保存。 */
export function applyLocalWorkspaceCommand(state: ReaderWorkspaceState, command: WorkspaceCommand): ReaderWorkspaceState {
  const tabs = [...state.tabs]
  let active = state.active
  const index = tabs.findIndex(tab => readerTabKey(tab) === command.key)
  if (command.action === 'open' && command.tab) {
    const found = tabs.findIndex(tab => readerTabKey(tab) === readerTabKey(command.tab!))
    if (found < 0) tabs.push(command.tab)
    else if (command.tab.title) tabs[found] = { ...tabs[found]!, title: command.tab.title }
    active = readerTabKey(command.tab)
  } else if (command.action === 'close' && index >= 0) {
    tabs.splice(index, 1)
    if (active === command.key) active = tabs.length ? readerTabKey(tabs[Math.min(index, tabs.length - 1)]!) : ''
  } else if (command.action === 'title' && index >= 0) tabs[index] = { ...tabs[index]!, title: command.title }
  else if (command.action === 'activate' && index >= 0) active = command.key!
  else if (command.action === 'move' && index >= 0 && command.key !== command.before) {
    const [tab] = tabs.splice(index, 1)
    const before = tabs.findIndex(item => readerTabKey(item) === command.before)
    tabs.splice(before < 0 ? tabs.length : before, 0, tab!)
  }
  return { tabs, active, layout: command.layout ?? state.layout }
}

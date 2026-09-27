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
export interface ReaderWorkspaceState { tabs: ReaderTab[]; active: string; layout: Partial<DockLayout>; revision: number }
export interface WorkspaceCommand {
  action: 'open' | 'close' | 'activate' | 'move' | 'layout' | 'title'
  title?: string
  tab?: ReaderTab; key?: string; before?: string; layout?: DockLayout
}

/** 未登录的公开 PDF 仍可阅读，仅不保存用户工作区。 */
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
  return { tabs, active, layout: command.layout ?? state.layout, revision: state.revision + 1 }
}

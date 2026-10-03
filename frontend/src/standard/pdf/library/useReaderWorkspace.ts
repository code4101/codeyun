import { computed, markRaw, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import api from '@/api'
import { useUserStore } from '@/store/userStore'
import { useDockLayout } from '@/components/docking/useDockLayout'
import { restoreDockLayout } from '@/components/docking/dockLayout'
import { pdfDockTools } from './readerDockTools'
import { applyLocalWorkspaceCommand, restoreReaderWorkspace, type ReaderTab, type ReaderWorkspaceState, type WorkspaceCommand } from './readerWorkspaceState'

export const workspaceTools = [...pdfDockTools]
type StoredWorkspace = Partial<ReaderWorkspaceState> & {
  version?: number; shelfOpen?: boolean; shelfActive?: boolean; settingsOpen?: boolean; settingsActive?: boolean
}

/** 工作台现场按浏览器、账号保存；服务器只管理内容、阅读进度和资源编号。 */
export const useReaderWorkspace = defineStore('reader-workspace', () => {
  const user = useUserStore()
  const owner = computed(() => user.isAuthenticated ? user.user?.id ?? 'pending' : 'guest')
  const storageKey = () => owner.value === 'pending' ? null : `codeyun.reader-workspace:${owner.value}`
  const state = ref(restoreReaderWorkspace(null))
  const shelfOpen = ref(false)
  const shelfActive = ref(false)
  const settingsOpen = ref(false)
  const settingsActive = ref(false)
  const visible = ref(false)
  const ready = ref(false)
  const dock = markRaw(useDockLayout(null, workspaceTools))
  let restoring = false
  let generation = 0
  // 身份尚未加载时，暂存用户操作，拿到账号后再合并到该账号的本地现场。
  let pending: WorkspaceCommand[] = []

  function persist() {
    const key = storageKey()
    if (restoring || !key) return
    try {
      localStorage.setItem(key, JSON.stringify({ version: 1, ...state.value,
        shelfOpen: shelfOpen.value, shelfActive: shelfActive.value,
        settingsOpen: settingsOpen.value, settingsActive: settingsActive.value }))
    } catch { /* 存储不可用时仍可在当前页面使用。 */ }
  }
  function restore() {
    restoring = true
    let saved: StoredWorkspace | undefined
    try {
      const key = storageKey()
      const parsed: unknown = key ? JSON.parse(localStorage.getItem(key) || 'null') : null
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) saved = parsed as StoredWorkspace
    } catch { /* 忽略损坏的本地数据。 */ }
    state.value = restoreReaderWorkspace(saved?.version === 1 ? saved : null)
    shelfOpen.value = saved?.version === 1 && saved?.shelfOpen === true
    settingsOpen.value = saved?.version === 1 && saved?.settingsOpen === true
    shelfActive.value = shelfOpen.value && saved?.shelfActive === true
    settingsActive.value = !shelfActive.value && settingsOpen.value && saved?.settingsActive === true
    dock.state.value = restoreDockLayout(workspaceTools, state.value.layout)
    ready.value = true
    restoring = false
  }
  function openShelf() { shelfOpen.value = true; shelfActive.value = true; settingsActive.value = false; visible.value = true }
  function openSettings() { settingsOpen.value = true; settingsActive.value = true; shelfActive.value = false; visible.value = true }
  function closeSettings() { settingsOpen.value = false; settingsActive.value = false }
  function activateTab(key: string) {
    if (key === 'view:bookshelf') { openShelf(); return Promise.resolve() }
    if (key === 'view:settings') { openSettings(); return Promise.resolve() }
    settingsActive.value = false
    return command({ action: 'activate', key })
  }
  function closeTab(key: string) {
    if (key === 'view:bookshelf') { shelfOpen.value = false; shelfActive.value = false; return Promise.resolve() }
    if (key === 'view:settings') { closeSettings(); return Promise.resolve() }
    return command({ action: 'close', key })
  }
  function command(operation: WorkspaceCommand) {
    if (!ready.value) restore()
    if (operation.action === 'open' || operation.action === 'activate') { shelfActive.value = false; settingsActive.value = false }
    state.value = applyLocalWorkspaceCommand(state.value, operation)
    if (!state.value.tabs.length && shelfOpen.value) shelfActive.value = true
    if (owner.value === 'pending') pending.push(operation)
    persist()
    return Promise.resolve()
  }
  async function identify(tab: ReaderTab) {
    const current = state.value.tabs.find(item => item.kind === tab.kind && item.id === tab.id)
    if (!current || current.publicId || owner.value === 'pending') return
    const epoch = generation
    try {
      // 编号是资源元数据；请求失败或返回较晚，都不能改变标签打开/关闭及选中状态。
      const resolved = (await api.post<ReaderTab>('/reader-workspace/resources', { kind: tab.kind, id: tab.id })).data
      if (epoch !== generation) return
      const target = state.value.tabs.find(item => item.kind === tab.kind && item.id === tab.id)
      if (target) { target.publicId = resolved.publicId; persist() }
    } catch { /* 本地标签已保存，下次打开时可再次获取资源编号。 */ }
  }
  function open(tab: ReaderTab) {
    visible.value = true
    void command({ action: 'open', tab })
    return identify(tab)
  }
  watch(dock.state, value => {
    if (restoring || !ready.value) return
    state.value = { ...state.value, layout: JSON.parse(JSON.stringify(value)) }
    persist()
  }, { deep: true, flush: 'sync' })
  watch([state, shelfOpen, shelfActive, settingsOpen, settingsActive], persist, { deep: true, flush: 'sync' })
  watch(owner, (next, previous) => {
    generation++
    const queued = previous === 'pending' ? pending : []
    const functional = { shelfOpen: shelfOpen.value, shelfActive: shelfActive.value, settingsOpen: settingsOpen.value, settingsActive: settingsActive.value }
    const wasVisible = visible.value
    pending = []
    restore()
    visible.value = false
    if (previous === 'pending' && typeof next === 'number') {
      for (const operation of queued) void command(operation)
      if (queued.length || functional.shelfOpen || functional.settingsOpen) {
        shelfOpen.value = functional.shelfOpen; shelfActive.value = functional.shelfActive
        settingsOpen.value = functional.settingsOpen; settingsActive.value = functional.settingsActive
        visible.value = wasVisible
      }
      for (const operation of queued) if (operation.action === 'open' && operation.tab) void identify(operation.tab)
    }
  }, { immediate: true, flush: 'sync' })
  const initialize = () => Promise.resolve()
  return { state, visible, ready, dock, initialize, command, open, shelfOpen, shelfActive, openShelf, settingsOpen, settingsActive, openSettings, closeSettings, activateTab, closeTab }
})

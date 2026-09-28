import { markRaw, onScopeDispose, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import api from '@/api'
import { useUserStore } from '@/store/userStore'
import { useDockLayout } from '@/components/docking/useDockLayout'
import { restoreDockLayout } from '@/components/docking/dockLayout'
import { pdfDockTools } from './readerDockTools'
import { applyLocalWorkspaceCommand, type ReaderTab, type ReaderWorkspaceState, type WorkspaceCommand } from './readerWorkspaceState'

export const workspaceTools = [...pdfDockTools]

/** 所有打开入口共用一个用户工作区。服务端按操作合并，不能用本地旧快照覆盖其他窗口的新标签。 */
export const useReaderWorkspace = defineStore('reader-workspace', () => {
  const user = useUserStore()
  const state = ref<ReaderWorkspaceState>({ tabs: [], active: '', layout: {}, revision: 0 })
  // 功能视图属于当前窗口，不作为图书提交给资源工作区 API。
  const shelfOpen = ref(false)
  const shelfActive = ref(false)
  function openShelf() { shelfOpen.value = true; shelfActive.value = true; visible.value = true }
  function activateTab(key: string) {
    if (key === 'view:bookshelf') { openShelf(); return Promise.resolve() }
    return command({ action: 'activate', key })
  }
  function closeTab(key: string) {
    if (key === 'view:bookshelf') { shelfOpen.value = false; shelfActive.value = false; return Promise.resolve() }
    return command({ action: 'close', key })
  }
  const visible = ref(false)
  const ready = ref(false)
  const busy = ref(false)
  const dock = markRaw(useDockLayout(null, workspaceTools))
  let pending: WorkspaceCommand[] = []
  let running: Promise<void> | undefined
  let loaded = false
  let refreshRequested = false
  let generation = 0
  let applying = false
  let disposed = false
  let retryDelay = 1000
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let layoutTimer: ReturnType<typeof setTimeout> | undefined

  function apply(next: ReaderWorkspaceState, layout = false) {
    // 服务端确认旧操作期间，用户可能继续操作；重放尚未确认的操作，避免界面回跳。
    state.value = pending.reduce(applyLocalWorkspaceCommand, next)
    if (layout && !layoutTimer && !pending.some(item => item.action === 'layout')) {
      applying = true
      dock.state.value = restoreDockLayout(workspaceTools, state.value.layout)
      applying = false
    }
  }
  function synchronize(): Promise<void> {
    if (disposed) return Promise.resolve()
    if (running) return running
    clearTimeout(retryTimer)
    retryTimer = undefined
    if (!user.isAuthenticated) { ready.value = true; return Promise.resolve() }
    const epoch = generation
    busy.value = true
    running = (async () => {
      try {
        if (!loaded || refreshRequested) {
          refreshRequested = false
          const next = (await api.get<ReaderWorkspaceState>('/reader-workspace')).data
          if (epoch !== generation) return
          apply(next, true)
          loaded = true
          ready.value = true
        }
        while (pending.length) {
          const operation = pending[0]!
          const next = (await api.post<ReaderWorkspaceState>('/reader-workspace/commands', operation)).data
          if (epoch !== generation) return
          pending.shift()
          apply(next)
        }
        retryDelay = 1000
      } catch {
        if (epoch !== generation) return
        // 操作保持原顺序；网络恢复后静默补交，失败不会影响本地阅读和分栏。
        if (!pending.length) refreshRequested = true
        retryTimer = setTimeout(() => { retryTimer = undefined; void synchronize() }, retryDelay)
        retryDelay = Math.min(retryDelay * 2, 30000)
      } finally {
        if (epoch === generation) { busy.value = false; running = undefined }
      }
    })()
    return running
  }
  const initialize = () => loaded ? Promise.resolve() : synchronize()
  function command(operation: WorkspaceCommand) {
    if (operation.action === 'open' || operation.action === 'activate') shelfActive.value = false
    state.value = applyLocalWorkspaceCommand(state.value, operation)
    if (!state.value.tabs.length && shelfOpen.value) shelfActive.value = true
    ready.value = true
    if (user.isAuthenticated) pending.push(operation)
    return synchronize()
  }
  function refresh() {
    refreshRequested = true
    return synchronize()
  }
  function open(tab: ReaderTab) {
    visible.value = true
    return command({ action: 'open', tab })
  }
  watch(dock.state, () => {
    if (!ready.value || applying) return
    clearTimeout(layoutTimer)
    layoutTimer = setTimeout(() => {
      layoutTimer = undefined
      void command({ action: 'layout', layout: JSON.parse(JSON.stringify(dock.state.value)) }).catch(() => undefined)
    }, 200)
  }, { deep: true, flush: 'sync' })
  watch(() => user.user?.id ?? (user.isAuthenticated ? 'pending' : 'guest'), (next, previous) => {
    if (previous === 'pending' && typeof next === 'number') return
    generation++
    shelfOpen.value = false
    shelfActive.value = false
    clearTimeout(layoutTimer)
    layoutTimer = undefined
    ready.value = false
    visible.value = false
    busy.value = false
    pending = []
    loaded = false
    refreshRequested = false
    running = undefined
    retryDelay = 1000
    clearTimeout(retryTimer)
    retryTimer = undefined
    apply({ tabs: [], active: '', layout: {}, revision: 0 }, true)
  }, { flush: 'sync' })
  const online = () => { void refresh() }
  window.addEventListener('online', online)
  onScopeDispose(() => {
    disposed = true
    generation++
    clearTimeout(retryTimer)
    clearTimeout(layoutTimer)
    window.removeEventListener('online', online)
  })
  return { state, visible, ready, busy, dock, initialize, refresh, command, open, shelfOpen, shelfActive, openShelf, activateTab, closeTab }
})

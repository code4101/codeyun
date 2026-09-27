import { markRaw, ref, shallowRef, watch } from 'vue'
import { defineStore } from 'pinia'
import api from '@/api'
import { useUserStore } from '@/store/userStore'
import { useDockLayout } from '@/components/docking/useDockLayout'
import { restoreDockLayout } from '@/components/docking/dockLayout'
import { pdfDockTools } from './readerDockTools'
import { applyLocalWorkspaceCommand, type ReaderTab, type ReaderWorkspaceState, type WorkspaceCommand } from './readerWorkspaceState'

export const workspaceTools = [{ id: 'library', title: '图书馆', icon: 'library' as const, position: 'left' as const, open: true }, ...pdfDockTools]

/** 所有打开入口共用一个用户工作区。服务端按操作合并，不能用本地旧快照覆盖其他窗口的新标签。 */
export const useReaderWorkspace = defineStore('reader-workspace', () => {
  const user = useUserStore()
  const state = ref<ReaderWorkspaceState>({ tabs: [], active: '', layout: {}, revision: 0 })
  const visible = ref(false)
  const ready = ref(false)
  const error = ref('')
  const busy = ref(false)
  const failedCommand = shallowRef<WorkspaceCommand | null>(null)
  const dock = markRaw(useDockLayout(null, workspaceTools))
  let queue = Promise.resolve()
  let generation = 0
  let applying = false
  let layoutTimer: ReturnType<typeof setTimeout> | undefined

  function apply(next: ReaderWorkspaceState, layout = false) {
    state.value = next
    if (layout) {
      applying = true
      dock.state.value = restoreDockLayout(workspaceTools, next.layout)
      applying = false
    }
  }
  function enqueue(task: () => Promise<void>) {
    const epoch = generation
    const result = queue.then(async () => {
      if (epoch !== generation) return
      busy.value = true
      try { await task(); if (epoch === generation && !failedCommand.value) error.value = '' }
      catch (cause) { if (epoch === generation) error.value = '工作区保存或加载失败，请重试'; throw cause }
      finally { if (epoch === generation) busy.value = false }
    })
    queue = result.catch(() => undefined)
    return result
  }
  async function load() {
    if (ready.value) return
    const epoch = generation
    const next = user.isAuthenticated ? (await api.get<ReaderWorkspaceState>('/reader-workspace')).data : state.value
    if (epoch !== generation) return
    apply(next, true)
    ready.value = true
  }
  const initialize = () => enqueue(load)
  function command(command: WorkspaceCommand) {
    return enqueue(async () => {
      const epoch = generation
      try {
        await load()
        if (epoch !== generation) return
        const next = user.isAuthenticated
          ? (await api.post<ReaderWorkspaceState>('/reader-workspace/commands', command)).data
          : applyLocalWorkspaceCommand(state.value, command)
        if (epoch === generation) {
          apply(next)
          if (failedCommand.value === command) failedCommand.value = null
        }
      } catch (cause) { if (epoch === generation) failedCommand.value = command; throw cause }
    })
  }
  async function refresh() {
    return enqueue(async () => {
      if (!user.isAuthenticated) return
      const epoch = generation
      const next = (await api.get<ReaderWorkspaceState>('/reader-workspace')).data
      if (epoch === generation) { apply(next, !layoutTimer); ready.value = true }
    })
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
    clearTimeout(layoutTimer)
    layoutTimer = undefined
    ready.value = false
    visible.value = false
    error.value = ''
    busy.value = false
    failedCommand.value = null
    apply({ tabs: [], active: '', layout: {}, revision: 0 }, true)
  }, { flush: 'sync' })
  const retry = () => failedCommand.value ? command(failedCommand.value) : refresh()
  return { state, visible, ready, busy, error, dock, initialize, refresh, command, open, retry }
})

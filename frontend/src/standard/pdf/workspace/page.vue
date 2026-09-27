<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api'
import ReaderWorkspace from '../library/ReaderWorkspace.vue'
import { useReaderWorkspace } from '../library/useReaderWorkspace'
import { readerLocation, readerTarget } from '../library/bookReaderRoute'
import { readerTabKey, type ReaderTab } from '../library/readerWorkspaceState'
const route = useRoute()
const router = useRouter()
const workspace = useReaderWorkspace()
const routeError = ref('')
let navigation = 0
let applyingRoute = false
function syncAddress() {
  if (applyingRoute || routeError.value || !workspace.ready) return
  const tab = workspace.state.tabs.find(tab => readerTabKey(tab) === workspace.state.active)
  const target = { ...readerLocation(tab), hash: route.hash }
  if (router.resolve(target).fullPath !== route.fullPath) void router.replace(target)
}
async function applyRoute() {
  const version = ++navigation
  applyingRoute = true
  routeError.value = ''
  try {
    const id = readerTarget(route.query)
    if (route.query.id !== undefined && !id) throw new Error('invalid resource id')
    if (id) {
      const tab = (await api.get<ReaderTab>(`/reader-workspace/resources/${id}`)).data
      if (version !== navigation) return
      const size = Number(route.query.pageSize)
      tab.bookshelfId = typeof route.query.bookshelf === 'string' ? route.query.bookshelf : ''
      tab.readingMode = route.query.mode === 'paginated' ? 'paginated' : 'scroll'
      tab.pageSize = Number.isInteger(size) && size >= 200 && size <= 20000 ? size : 1600
      if (!workspace.ready || workspace.state.active !== readerTabKey(tab)) await workspace.open(tab)
    } else await workspace.initialize()
  } catch {
    if (version === navigation) routeError.value = '无法打开这本书，请检查编号和访问权限。'
    return
  } finally { if (version === navigation) applyingRoute = false }
  if (version === navigation) syncAddress()
}
watch(() => route.fullPath, applyRoute, { immediate: true })
watch(() => workspace.state.active, syncAddress)
</script>
<template>
  <div v-if="routeError" role="alert">{{ routeError }} <button @click="applyRoute">重试</button></div>
  <ReaderWorkspace standalone />
</template>

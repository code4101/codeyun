<script setup lang="ts">
import { onBeforeUnmount, provide, ref, shallowReactive, watch } from 'vue'
import { createMemoryHistory, createRouter, routeLocationKey, routerKey, useRouter, type RouteLocationNormalizedLoaded } from 'vue-router'
import WorkbookResource from '../resource-view/page.vue'

const props = defineProps<{ workbookId: number; sheet?: string }>()
const emit = defineEmits<{ open: [id: number, sheet?: string]; sheet: [sheet?: string]; error: [message: string]; exit: []; changed: [] }>()
const outerRouter = useRouter()
const editor = ref<InstanceType<typeof WorkbookResource>>()
const ready = ref(false)
// 每个工作簿沿用完整资源页与公共路由契约，在内存中维护工作表地址。
// 分享链接仍由真实 /workbook 地址生成，页内切表不会离开星云工作区。
const history = createMemoryHistory()
const router = createRouter({ history, routes: [
  { path: '/workbook/:workbookId', name: 'PublicWorkbookResource', component: WorkbookResource },
  { path: '/attendance/workbook/:workbookId', name: 'IndependentAttendanceWorkbookResource', component: WorkbookResource },
  { path: '/login', name: 'Login', component: WorkbookResource },
  { path: '/:pathMatch(.*)*', component: WorkbookResource },
] })
const location = {} as RouteLocationNormalizedLoaded
for (const key in router.currentRoute.value) {
  Object.defineProperty(location, key, { enumerable: true, get: () => router.currentRoute.value[key as keyof RouteLocationNormalizedLoaded] })
}
provide(routerKey, router)
provide(routeLocationKey, shallowReactive(location))
async function flush() { await editor.value?.flush() }
router.beforeEach(async (to, from) => {
  try { if (from.name) await flush() } catch (error) { emit('error', String(error)); return false }
  if (to.path === '/notes/sheets') { emit('exit'); return false }
  if (!['PublicWorkbookResource', 'IndependentAttendanceWorkbookResource'].includes(String(to.name))) { void outerRouter.push(to.fullPath); return false }
  const id = Number(to.params.workbookId)
  if (id !== props.workbookId) { emit('open', id, typeof to.query.sheet === 'string' ? to.query.sheet : undefined); return false }
})
router.afterEach((to, _from, failure) => {
  if (!failure) emit('sheet', typeof to.query.sheet === 'string' ? to.query.sheet : undefined)
})
void router.replace({ path: `/workbook/${props.workbookId}`, query: { sheet: props.sheet } }).then(() => { ready.value = true })
watch(() => props.sheet, sheet => {
  if (ready.value && sheet !== router.currentRoute.value.query.sheet) {
    void router.replace({ path: router.currentRoute.value.path, query: { ...router.currentRoute.value.query, sheet } })
  }
})
onBeforeUnmount(() => history.destroy())
defineExpose({ flush, refresh: async () => { await editor.value?.refresh() } })
</script>
<template>
  <WorkbookResource v-if="ready" ref="editor" embedded @workbook-changed="emit('changed')" />
  <div v-else role="status">正在打开工作簿…</div>
</template>

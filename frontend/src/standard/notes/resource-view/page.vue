<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { User } from '@element-plus/icons-vue'

import {
  createNoteSheet,
  deleteWorkbook,
  deleteNoteSheet,
  fetchNoteSheet,
  fetchWorkbook,
  getNoteSheetApiErrorStatus,
  removeSheetFromWorkbook,
  reorderWorkbookSheets,
  saveAsWorkbook,
  updateNoteSheet,
  updateWorkbook,
  type NoteSheetDetail,
  type NoteSheetResourceAccess,
  type WorkbookDetail,
  type WorkbookRefItem,
} from '@/api/noteSheets'
import { useUserStore } from '@/store/userStore'
import { useSortableList } from '@/utils/useSortableList'
import {
  CODEYUN_PUBLIC_HOST,
  buildCodeyunUrlVariant,
  copyTextToClipboard,
  openUrlInNewWindow,
  resolveCodeyunUrl,
  type CodeyunLinkVariant,
} from '@/utils/codeyunLinks'
import { markBootPerf, markBootPerfAsync } from '@/utils/bootPerf'
import NoteSheetAccessDialog from '../components/NoteSheetAccessDialog.vue'

markBootPerf('resource-view.module')

const NoteSheetWorkspace = defineAsyncComponent(() => markBootPerfAsync(
  'resource-view.NoteSheetWorkspace.import',
  () => import('../components/NoteSheetWorkspace.vue'),
))

const APP_TITLE = 'CodeYun'
const SHEET_TAB_CONTEXT_MENU_WIDTH = 148
const SHEET_TAB_CONTEXT_MENU_HEIGHT = 280
const RESOURCE_LINK_SUBMENU_WIDTH = 176
const SHEET_ADVANCED_SUBMENU_WIDTH = 196
const WORKBOOK_CONTEXT_MENU_WIDTH = 148
const WORKBOOK_CONTEXT_MENU_HEIGHT = 300

type ResourceLinkMenuCommand = 'copy' | CodeyunLinkVariant

const resourceLinkMenuItems: Array<{ command: ResourceLinkMenuCommand; label: string }> = [
  { command: 'copy', label: '复制' },
  { command: 'current', label: '在当前域名打开' },
  { command: 'public', label: `在 ${CODEYUN_PUBLIC_HOST} 打开` },
]

type SheetTabContextMenuCommand =
  | 'create'
  | 'rename'
  | 'duplicate'
  | 'configure'
  | 'advanced'
  | 'hide_empty_columns'
  | 'detect_option_filters'
  | 'access'
  | 'link'
  | 'remove'
  | 'delete'

type SheetTabContextMenuItem = {
  command: SheetTabContextMenuCommand
  label: string
  danger?: boolean
  divided?: boolean
  linkSubmenu?: boolean
  advancedSubmenu?: boolean
  deleteSubmenu?: boolean
}

type WorkbookContextMenuCommand =
  | 'link'
  | 'rename'
  | 'access'
  | 'defined_names'
  | 'save_as'
  | 'template'
  | 'duplicate'
  | 'delete'

type WorkbookContextMenuItem = {
  command: WorkbookContextMenuCommand
  label: string
  danger?: boolean
  divided?: boolean
  linkSubmenu?: boolean
  saveAsSubmenu?: boolean
}

type SheetWorkspaceRouteView = 'lookup' | 'sheet'

type ResourceAccessIssue = {
  resourceType: 'sheet' | 'workbook'
  status: number | null
  message: string
}

type SheetWorkspaceLoadErrorPayload = {
  sheetId: number
  status: number | null
  message: string
}

type NoteSheetWorkspaceExpose = {
  openSheetSettings?: () => void
  hideEmptyColumns?: () => void
  detectAndSetOptionFilters?: () => void
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const userIdentityLabel = computed(() => userStore.user?.nickname || userStore.user?.username || '')

function openLoginPage() {
  void router.push({
    path: '/login',
    query: { redirect: route.fullPath },
  })
}

const loading = ref(false)
const workbook = ref<WorkbookDetail | null>(null)
const activeSheetId = ref<number | null>(null)
const prefetchedSheetDetail = ref<NoteSheetDetail | null>(null)
const sheetTabsRef = ref<HTMLElement | null>(null)
const sheetWorkspaceRef = ref<NoteSheetWorkspaceExpose | null>(null)
const standaloneSheetTitle = ref('')
const standaloneWorkbookTitle = ref('')
const standaloneParentWorkbookId = ref<number | null>(null)
const standaloneWorkbookItems = ref<WorkbookRefItem[]>([])
const errorText = ref('')
const sheetTabReorderSaving = ref(false)
let sheetTabClickSuppressedUntil = 0
const sheetTabContextMenu = ref({
  visible: false,
  sheetId: null as number | null,
  left: 0,
  top: 0,
})
const workbookContextMenu = ref({
  visible: false,
  left: 0,
  top: 0,
})
const sheetAccessDialogVisible = ref(false)
const sheetAccessDialogSheet = ref<WorkbookDetail['sheets'][number] | null>(null)
const workbookAccessDialogVisible = ref(false)
const resourceAccessIssue = ref<ResourceAccessIssue | null>(null)
const sheetWorkspaceReloadKey = ref(0)
const inlineLoginForm = reactive({
  username: '',
  password: '',
})
const inlineLoginError = ref('')
let workbookLoadSeq = 0

const isWorkbookMode = computed(() => String(route.name ?? '') === 'PublicWorkbookResource')
const workbookId = computed(() => normalizePositiveInt(route.params.workbookId))
const sheetId = computed(() => normalizePositiveInt(route.params.sheetId))
const querySheetId = computed(() => normalizePositiveInt(route.query.sheet))
const routeWorkspaceView = computed(() => normalizeWorkspaceViewQuery(route.query.view ?? route.query.mode ?? route.query.sheetView))
const activeSheet = computed(() => (
  workbook.value?.sheets.find((sheet) => sheet.id === activeSheetId.value) ?? null
))
const activeSheetPrefetchedDetail = computed(() => (
  prefetchedSheetDetail.value?.id === activeSheetId.value ? prefetchedSheetDetail.value : null
))
const sheetTabContextMenuSheet = computed(() => (
  workbook.value?.sheets.find((sheet) => sheet.id === sheetTabContextMenu.value.sheetId) ?? null
))
const canEditWorkbookSheets = computed(() => (
  workbook.value?.access?.capabilities.can_edit_config !== false
))
const canManageWorkbookSheets = computed(() => (
  workbook.value?.access?.capabilities.can_manage_access !== false
))
const canReorderWorkbookSheets = computed(() => (
  canEditWorkbookSheets.value && (workbook.value?.sheets.length ?? 0) > 1
))
const canEditSheetTabContextMenuSheet = computed(() => (
  sheetTabContextMenuSheet.value?.access?.capabilities.can_edit_config !== false
))
const canManageSheetTabContextMenuSheet = computed(() => (
  sheetTabContextMenuSheet.value?.access?.capabilities.can_manage_access !== false
))
const canRemoveSheetTabContextMenuSheet = computed(() => (
  !!sheetTabContextMenuSheet.value
  && canManageWorkbookSheets.value
  && canManageSheetTabContextMenuSheet.value
  && (workbook.value?.sheets.length ?? 0) > 1
))
const canDeleteSheetTabContextMenuSheet = computed(() => (
  !!sheetTabContextMenuSheet.value && canManageSheetTabContextMenuSheet.value
))
const sheetTabContextMenuItems = computed<SheetTabContextMenuItem[]>(() => {
  const sheet = sheetTabContextMenuSheet.value
  const items: Array<SheetTabContextMenuItem & { enabled: boolean }> = [
    { command: 'create', label: '新建工作表', enabled: canEditWorkbookSheets.value },
    { command: 'rename', label: '重命名', enabled: !!sheet && canEditSheetTabContextMenuSheet.value },
    {
      command: 'duplicate',
      label: '复制工作表',
      enabled: !!sheet && canEditWorkbookSheets.value && canEditSheetTabContextMenuSheet.value,
    },
    { command: 'configure', label: '设置表格', enabled: !!sheet && canEditSheetTabContextMenuSheet.value },
    {
      command: 'advanced',
      label: '高级功能',
      advancedSubmenu: true,
      enabled: !!sheet && canEditSheetTabContextMenuSheet.value,
    },
    { command: 'access', label: '设置权限', enabled: !!sheet && canManageSheetTabContextMenuSheet.value },
    { command: 'link', label: '打开链接', linkSubmenu: true, enabled: !!sheet },
    {
      command: 'delete',
      label: '删除',
      danger: true,
      divided: true,
      deleteSubmenu: canRemoveSheetTabContextMenuSheet.value,
      enabled: canDeleteSheetTabContextMenuSheet.value,
    },
  ]
  return items
    .filter((item) => item.enabled)
    .map((item) => ({
      command: item.command,
      label: item.label,
      danger: item.danger,
      divided: item.divided,
      linkSubmenu: item.linkSubmenu,
      advancedSubmenu: item.advancedSubmenu,
      deleteSubmenu: item.deleteSubmenu,
    }))
})
const workbookContextMenuItems = computed<WorkbookContextMenuItem[]>(() => {
  if (!workbook.value) {
    return []
  }
  const items: Array<WorkbookContextMenuItem & { enabled: boolean }> = [
    { command: 'link', label: '打开链接', linkSubmenu: true, enabled: true },
    { command: 'rename', label: '重命名', enabled: canManageWorkbookSheets.value },
    { command: 'access', label: '设置权限', enabled: canManageWorkbookSheets.value },
    { command: 'defined_names', label: '名称管理器', enabled: canEditWorkbookSheets.value },
    { command: 'save_as', label: '另存为', saveAsSubmenu: true, enabled: true },
    { command: 'delete', label: '删除工作簿', danger: true, divided: true, enabled: canManageWorkbookSheets.value },
  ]
  return items
    .filter((item) => item.enabled)
    .map((item) => ({
      command: item.command,
      label: item.label,
      danger: item.danger,
      divided: item.divided,
      linkSubmenu: item.linkSubmenu,
      saveAsSubmenu: item.saveAsSubmenu,
    }))
})
const standaloneBackWorkbook = computed(() => {
  if (isWorkbookMode.value || standaloneWorkbookItems.value.length !== 1) {
    return null
  }
  return standaloneWorkbookItems.value[0] ?? null
})
const standaloneWorkbookBackTo = computed(() => {
  const parentWorkbookId = standaloneParentWorkbookId.value ?? standaloneBackWorkbook.value?.id ?? null
  const currentSheetId = sheetId.value
  if (parentWorkbookId == null || currentSheetId == null) {
    return ''
  }

  const query = new URLSearchParams({ sheet: String(currentSheetId) })
  if (routeWorkspaceView.value) {
    query.set('view', routeWorkspaceView.value)
  }
  return `/workbook/${parentWorkbookId}?${query.toString()}`
})
const pageDocumentTitle = computed(() => {
  if (isWorkbookMode.value) {
    const workbookTitle = String(workbook.value?.title || '').trim()
    const sheetTitle = String(activeSheet.value?.title || '').trim()
    const segments = [sheetTitle, workbookTitle].filter(Boolean)
    return segments.length ? `${segments.join(' - ')} - ${APP_TITLE}` : APP_TITLE
  }

  const sheetTitle = standaloneSheetTitle.value.trim()
  const workbookTitle = standaloneWorkbookTitle.value.trim()
  const resourceTitle = workbookTitle && sheetTitle
    ? `${workbookTitle}/${sheetTitle}`
    : sheetTitle || workbookTitle
  return resourceTitle ? `${resourceTitle} - ${APP_TITLE}` : APP_TITLE
})
const resourceAccessTitle = computed(() => {
  const issue = resourceAccessIssue.value
  if (!issue) {
    return ''
  }
  return issue.resourceType === 'workbook'
    ? '没有权限访问该工作簿'
    : '没有权限访问该工作表'
})
const resourceAccessDescription = computed(() => (
  userStore.isAuthenticated
    ? '当前账号没有这个资源的访问权限，可以换账号后重试。'
    : '登录有权限的账号后，会自动重新打开当前链接。'
))
const currentAccountLabel = computed(() => {
  const user = userStore.user
  if (!user) {
    return ''
  }
  const nickname = String(user.nickname || '').trim()
  const username = String(user.username || '').trim()
  return nickname && username && nickname !== username
    ? `${nickname}（${username}）`
    : nickname || username
})

function normalizePositiveInt(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value
  const numeric = Number(raw)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function normalizeWorkspaceViewQuery(value: unknown): SheetWorkspaceRouteView | null {
  const raw = Array.isArray(value) ? value[0] : value
  const text = String(raw ?? '').trim().toLowerCase()
  if (['lookup', 'quick', 'search', '速查'].includes(text)) {
    return 'lookup'
  }
  if (['sheet', 'table', 'grid', '表格'].includes(text)) {
    return 'sheet'
  }
  return null
}

function isAccessDeniedStatus(status: number | null) {
  return status === 401 || status === 403
}

function getCleanWorkbookRouteQuery(targetSheetId?: number | null): LocationQueryRaw {
  const query: Record<string, string> = {}
  if (targetSheetId != null) {
    query.sheet = String(targetSheetId)
  }
  if (routeWorkspaceView.value) {
    query.view = routeWorkspaceView.value
  }
  const sheetPerfQuery = Array.isArray(route.query.sheetPerf)
    ? route.query.sheetPerf[0]
    : route.query.sheetPerf
  if (sheetPerfQuery != null && String(sheetPerfQuery).trim()) {
    query.sheetPerf = String(sheetPerfQuery)
  }
  return query
}

function setResourceAccessIssue(resourceType: ResourceAccessIssue['resourceType'], status: number | null, message?: string) {
  resourceAccessIssue.value = {
    resourceType,
    status,
    message: message || (resourceType === 'workbook' ? '没有权限访问该工作簿' : '没有权限访问该工作表'),
  }
}

function clearResourceAccessIssue() {
  resourceAccessIssue.value = null
  inlineLoginError.value = ''
}

function refreshCurrentResourceAfterAuth() {
  clearResourceAccessIssue()
  sheetWorkspaceReloadKey.value += 1
  if (isWorkbookMode.value) {
    void loadWorkbookResource()
  }
}

async function submitInlineLogin() {
  const username = inlineLoginForm.username.trim()
  const password = inlineLoginForm.password
  inlineLoginError.value = ''
  if (!username || !password) {
    inlineLoginError.value = '请输入用户名和密码'
    return
  }

  const success = await userStore.login(username, password)
  if (!success) {
    inlineLoginError.value = userStore.error || '登录失败'
    return
  }
  inlineLoginForm.password = ''
  refreshCurrentResourceAfterAuth()
}

function switchInlineLoginAccount() {
  userStore.logout()
  inlineLoginError.value = ''
}

function resolveSheetId() {
  const sheets = workbook.value?.sheets ?? []
  const validIds = new Set(sheets.map((sheet) => sheet.id))
  return [querySheetId.value, activeSheetId.value, sheets[0]?.id ?? null]
    .find((id) => id != null && validIds.has(id)) ?? null
}

function syncActiveSheetFromWorkbookRoute() {
  if (!isWorkbookMode.value || !workbook.value || workbook.value.id !== workbookId.value) {
    return false
  }

  const nextSheetId = resolveSheetId()
  activeSheetId.value = nextSheetId
  if (prefetchedSheetDetail.value?.id !== nextSheetId) {
    prefetchedSheetDetail.value = null
  }
  if (nextSheetId != null && nextSheetId !== querySheetId.value) {
    void router.replace({
      path: `/workbook/${workbook.value.id}`,
      query: getCleanWorkbookRouteQuery(nextSheetId),
    })
  }
  return true
}

async function redirectWorkbookRouteFromSheetQuery(): Promise<boolean> {
  const staleWorkbookId = workbookId.value
  const targetSheetId = querySheetId.value
  if (targetSheetId == null) {
    return false
  }

  let detail = null
  try {
    detail = await fetchNoteSheet(targetSheetId, { paginate: false })
  } catch (error) {
    if (isAccessDeniedStatus(getNoteSheetApiErrorStatus(error))) {
      return false
    }
    throw error
  }
  const targetWorkbookId = (
    detail?.workbook_items.find((item) => item.id !== staleWorkbookId)?.id
    ?? detail?.workbook_items[0]?.id
    ?? detail?.parent_workbook_id
    ?? null
  )
  if (!targetWorkbookId || targetWorkbookId === staleWorkbookId) {
    return false
  }

  void router.replace({
    path: `/workbook/${targetWorkbookId}`,
    query: getCleanWorkbookRouteQuery(targetSheetId),
  })
  return true
}

async function loadWorkbookResource() {
  if (!isWorkbookMode.value) {
    return
  }
  const requestSeq = ++workbookLoadSeq
  const targetWorkbookId = workbookId.value
  const targetSheetId = querySheetId.value
  markBootPerf('resource-view.loadWorkbook.start', {
    workbookId: targetWorkbookId,
    sheetId: targetSheetId,
  })
  if (targetWorkbookId == null) {
    errorText.value = '工作簿地址无效'
    workbook.value = null
    activeSheetId.value = null
    prefetchedSheetDetail.value = null
    return
  }

  loading.value = true
  errorText.value = ''
  clearResourceAccessIssue()
  prefetchedSheetDetail.value = null
  try {
    const workbookRequest = markBootPerfAsync(
      'resource-view.fetchWorkbook',
      () => fetchWorkbook(targetWorkbookId),
    )
    const sheetRequest = targetSheetId == null
      ? Promise.resolve<NoteSheetDetail | null>(null)
      : markBootPerfAsync(
          'resource-view.prefetchSheet',
          () => fetchNoteSheet(targetSheetId, {
            workbookId: targetWorkbookId,
            includeWorkbookContext: false,
          }),
        ).catch((error) => {
          console.warn('Failed to prefetch workbook sheet:', error)
          return null
        })
    const [detail, prefetchedDetail] = await Promise.all([workbookRequest, sheetRequest])
    markBootPerf('resource-view.loadWorkbook.responses', {
      hasWorkbook: !!detail,
      hasPrefetchedSheet: !!prefetchedDetail,
      prefetchedSheetId: prefetchedDetail?.id ?? null,
    })
    if (requestSeq !== workbookLoadSeq || !isWorkbookMode.value || workbookId.value !== targetWorkbookId) {
      return
    }
    if (!detail) {
      if (await redirectWorkbookRouteFromSheetQuery()) {
        return
      }
      if (requestSeq !== workbookLoadSeq || !isWorkbookMode.value || workbookId.value !== targetWorkbookId) {
        return
      }
      errorText.value = '工作簿不存在'
      workbook.value = null
      activeSheetId.value = null
      prefetchedSheetDetail.value = null
      return
    }
    workbook.value = detail
    activeSheetId.value = resolveSheetId()
    prefetchedSheetDetail.value = prefetchedDetail?.id === activeSheetId.value ? prefetchedDetail : null
    markBootPerf('resource-view.loadWorkbook.state-ready', {
      activeSheetId: activeSheetId.value,
      sheetCount: detail.workbook_items?.length ?? detail.sheets?.length ?? null,
      prefetchedUsable: !!prefetchedSheetDetail.value,
    })
    if (activeSheetId.value != null && activeSheetId.value !== querySheetId.value) {
      void router.replace({
        path: `/workbook/${detail.id}`,
        query: getCleanWorkbookRouteQuery(activeSheetId.value),
      })
    }
  } catch (error) {
    if (requestSeq !== workbookLoadSeq || !isWorkbookMode.value || workbookId.value !== targetWorkbookId) {
      return
    }
    console.warn('Failed to load public workbook resource:', error)
    const status = getNoteSheetApiErrorStatus(error)
    if (!isAccessDeniedStatus(status) && await redirectWorkbookRouteFromSheetQuery()) {
      return
    }
    if (requestSeq !== workbookLoadSeq || !isWorkbookMode.value || workbookId.value !== targetWorkbookId) {
      return
    }
    errorText.value = isAccessDeniedStatus(status) ? '没有权限访问该工作簿' : '工作簿加载失败'
    if (isAccessDeniedStatus(status)) {
      setResourceAccessIssue('workbook', status, errorText.value)
    }
    workbook.value = null
    activeSheetId.value = null
    prefetchedSheetDetail.value = null
  } finally {
    if (requestSeq === workbookLoadSeq) {
      loading.value = false
      markBootPerf('resource-view.loadWorkbook.finally', {
        activeSheetId: activeSheetId.value,
        hasWorkbook: !!workbook.value,
      })
    }
  }
}

function selectSheet(nextSheetId: number) {
  closeSheetTabContextMenu()
  closeWorkbookContextMenu()
  clearResourceAccessIssue()
  if (!workbook.value || nextSheetId === activeSheetId.value) {
    return
  }
  activeSheetId.value = nextSheetId
  if (prefetchedSheetDetail.value?.id !== nextSheetId) {
    prefetchedSheetDetail.value = null
  }
  void router.push({
    path: `/workbook/${workbook.value.id}`,
    query: getCleanWorkbookRouteQuery(nextSheetId),
  })
}

function handleSheetTabClick(nextSheetId: number) {
  if (Date.now() < sheetTabClickSuppressedUntil) {
    return
  }
  selectSheet(nextSheetId)
}

async function handleSheetTabReorder(oldIndex: number, newIndex: number) {
  const currentWorkbook = workbook.value
  if (!currentWorkbook || !canReorderWorkbookSheets.value || sheetTabReorderSaving.value) {
    return
  }
  if (
    oldIndex < 0
    || newIndex < 0
    || oldIndex >= currentWorkbook.sheets.length
    || newIndex >= currentWorkbook.sheets.length
  ) {
    return
  }

  sheetTabClickSuppressedUntil = Date.now() + 250
  closeSheetTabContextMenu()
  const previousSheets = [...currentWorkbook.sheets]
  const nextSheets = [...currentWorkbook.sheets]
  const [movedSheet] = nextSheets.splice(oldIndex, 1)
  if (!movedSheet) {
    return
  }
  nextSheets.splice(newIndex, 0, movedSheet)
  workbook.value = { ...currentWorkbook, sheets: nextSheets }

  sheetTabReorderSaving.value = true
  try {
    const detail = await reorderWorkbookSheets(currentWorkbook.id, {
      sheet_ids: nextSheets.map((sheet) => sheet.id),
    })
    workbook.value = detail
    const validIds = new Set(detail.sheets.map((sheet) => sheet.id))
    if (activeSheetId.value != null && !validIds.has(activeSheetId.value)) {
      activeSheetId.value = resolveSheetId()
    }
  } catch (error) {
    console.warn('Failed to reorder workbook sheets:', error)
    workbook.value = { ...currentWorkbook, sheets: previousSheets }
    ElMessage.error('保存工作表顺序失败')
  } finally {
    sheetTabReorderSaving.value = false
  }
}

useSortableList({
  listRef: sheetTabsRef,
  getDeps: () => [
    workbook.value?.id ?? null,
    canReorderWorkbookSheets.value,
    ...((workbook.value?.sheets ?? []).map((sheet) => sheet.id)),
  ],
  isEnabled: () => canReorderWorkbookSheets.value,
  handle: '.resource-sheet-tab',
  ghostClass: 'resource-sheet-tab-sortable-ghost',
  onReorder: handleSheetTabReorder,
})

async function refreshWorkbookAfterSheetMutation(preferredSheetId?: number | null) {
  if (!workbookId.value) {
    return
  }
  const detail = await fetchWorkbook(workbookId.value)
  if (!detail) {
    workbook.value = null
    activeSheetId.value = null
    errorText.value = '工作簿不存在'
    return
  }

  workbook.value = detail
  const validIds = new Set(detail.sheets.map((sheet) => sheet.id))
  const nextSheetId = [
    preferredSheetId ?? null,
    activeSheetId.value,
    detail.sheets[0]?.id ?? null,
  ].find((id) => id != null && validIds.has(id)) ?? null
  activeSheetId.value = nextSheetId
  void router.replace({
    path: `/workbook/${detail.id}`,
    query: getCleanWorkbookRouteQuery(nextSheetId),
  })
}

function closeSheetTabContextMenu() {
  sheetTabContextMenu.value.visible = false
}

function closeWorkbookContextMenu() {
  workbookContextMenu.value.visible = false
}

function positionSheetTabContextMenu(event: MouseEvent, submenuWidth = 0) {
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight
  const occupiedWidth = SHEET_TAB_CONTEXT_MENU_WIDTH + Math.max(0, submenuWidth - 2)
  sheetTabContextMenu.value.left = Math.max(8, Math.min(event.clientX, viewportWidth - occupiedWidth - 8))
  sheetTabContextMenu.value.top = Math.max(8, Math.min(event.clientY, viewportHeight - SHEET_TAB_CONTEXT_MENU_HEIGHT - 8))
}

function positionWorkbookContextMenu(event: MouseEvent, submenuWidth = 0) {
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight
  const occupiedWidth = WORKBOOK_CONTEXT_MENU_WIDTH + Math.max(0, submenuWidth - 2)
  workbookContextMenu.value.left = Math.max(8, Math.min(event.clientX, viewportWidth - occupiedWidth - 8))
  workbookContextMenu.value.top = Math.max(8, Math.min(event.clientY, viewportHeight - WORKBOOK_CONTEXT_MENU_HEIGHT - 8))
}

function openSheetTabContextMenu(event: MouseEvent, sheet: WorkbookDetail['sheets'][number]) {
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()
  closeWorkbookContextMenu()

  if (sheet.id !== activeSheetId.value) {
    selectSheet(sheet.id)
  }

  positionSheetTabContextMenu(event, Math.max(RESOURCE_LINK_SUBMENU_WIDTH, SHEET_ADVANCED_SUBMENU_WIDTH))
  sheetTabContextMenu.value.sheetId = sheet.id
  sheetTabContextMenu.value.visible = true
}

function openWorkbookContextMenu(event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()
  if (!workbook.value) {
    return
  }
  closeSheetTabContextMenu()
  positionWorkbookContextMenu(event, RESOURCE_LINK_SUBMENU_WIDTH)
  workbookContextMenu.value.visible = true
}

async function waitForSheetWorkspaceRef() {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await nextTick()
    if (sheetWorkspaceRef.value) {
      return sheetWorkspaceRef.value
    }
    await new Promise((resolve) => window.requestAnimationFrame(resolve))
  }
  return sheetWorkspaceRef.value
}

async function configureSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  closeSheetTabContextMenu()
  if (!sheet || !canEditSheetTabContextMenuSheet.value) {
    return
  }
  if (sheet.id !== activeSheetId.value) {
    selectSheet(sheet.id)
  }
  const workspace = await waitForSheetWorkspaceRef()
  workspace?.openSheetSettings?.()
}

async function runSheetAdvancedActionFromTabContextMenu(command: 'hide_empty_columns' | 'detect_option_filters') {
  const sheet = sheetTabContextMenuSheet.value
  closeSheetTabContextMenu()
  if (!sheet || !canEditSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限修改该工作表')
    return
  }
  if (sheet.id !== activeSheetId.value) {
    selectSheet(sheet.id)
  }
  const workspace = await waitForSheetWorkspaceRef()
  if (command === 'hide_empty_columns') {
    workspace?.hideEmptyColumns?.()
    return
  }
  workspace?.detectAndSetOptionFilters?.()
}

function resolveSheetResourceHref(targetSheetId: number) {
  return router.resolve({
    path: `/sheet/${targetSheetId}`,
    query: routeWorkspaceView.value ? { view: routeWorkspaceView.value } : undefined,
  }).href
}

function resolveWorkbookResourceHref(targetWorkbookId: number, targetSheetId?: number | null) {
  return router.resolve({
    path: `/workbook/${targetWorkbookId}`,
    query: getCleanWorkbookRouteQuery(targetSheetId),
  }).href
}

function resolveAbsoluteResourceHref(href: string) {
  return resolveCodeyunUrl(href)?.toString() ?? href
}

function openResourceHref(href: string, variant?: CodeyunLinkVariant) {
  const targetUrl = variant
    ? buildCodeyunUrlVariant(href, variant)
    : resolveAbsoluteResourceHref(href)
  if (!targetUrl) {
    ElMessage.warning('链接无法打开')
    return
  }
  openUrlInNewWindow(targetUrl)
}

async function copyResourceHref(href: string) {
  try {
    await copyTextToClipboard(resolveAbsoluteResourceHref(href))
    ElMessage.success('已复制链接')
  } catch (error) {
    console.warn('Failed to copy resource link:', error)
    ElMessage.error('复制链接失败')
  }
}

function getSheetTabResourceHref() {
  const sheet = sheetTabContextMenuSheet.value
  if (!sheet) {
    return ''
  }
  return resolveSheetResourceHref(sheet.id)
}

function getWorkbookResourceHref() {
  const currentWorkbook = workbook.value
  if (!currentWorkbook) {
    return ''
  }
  return resolveWorkbookResourceHref(currentWorkbook.id, activeSheetId.value)
}

function openSheetTabLinkFromContextMenu() {
  const href = getSheetTabResourceHref()
  closeSheetTabContextMenu()
  if (href) {
    openResourceHref(href)
  }
}

async function handleSheetTabLinkMenuCommand(command: ResourceLinkMenuCommand) {
  const href = getSheetTabResourceHref()
  closeSheetTabContextMenu()
  if (!href) {
    return
  }
  if (command === 'copy') {
    await copyResourceHref(href)
    return
  }
  openResourceHref(href, command)
}

function openWorkbookLinkFromContextMenu() {
  const href = getWorkbookResourceHref()
  closeWorkbookContextMenu()
  if (href) {
    openResourceHref(href)
  }
}

async function handleWorkbookLinkMenuCommand(command: ResourceLinkMenuCommand) {
  const href = getWorkbookResourceHref()
  closeWorkbookContextMenu()
  if (!href) {
    return
  }
  if (command === 'copy') {
    await copyResourceHref(href)
    return
  }
  openResourceHref(href, command)
}

function openSheetAccessFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  closeSheetTabContextMenu()
  if (!sheet || !canManageSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限管理该工作表')
    return
  }
  sheetAccessDialogSheet.value = sheet
  sheetAccessDialogVisible.value = true
}

function handleSheetAccessSaved(access: NoteSheetResourceAccess) {
  const currentWorkbook = workbook.value
  const sheet = sheetAccessDialogSheet.value
  if (!currentWorkbook || !sheet) {
    return
  }
  workbook.value = {
    ...currentWorkbook,
    sheets: currentWorkbook.sheets.map((item) => (
      item.id === sheet.id
        ? { ...item, access }
        : item
    )),
  }
}

function handleWorkbookAccessSaved(access: NoteSheetResourceAccess) {
  if (!workbook.value) {
    return
  }
  workbook.value = { ...workbook.value, access }
}

function resolveWorkbookHref(targetWorkbookId: number, targetSheetId?: number | null) {
  return router.resolve({
    path: `/workbook/${targetWorkbookId}`,
    query: targetSheetId != null ? { sheet: String(targetSheetId) } : undefined,
  }).href
}

function openWorkbook(targetWorkbook: WorkbookDetail, targetSheetId?: number | null) {
  const href = resolveWorkbookHref(targetWorkbook.id, targetSheetId)
  window.open(href, '_blank', 'noopener,noreferrer')
}

function handleWorkbookContextMenuCommand(command: WorkbookContextMenuCommand) {
  switch (command) {
    case 'link':
      openWorkbookLinkFromContextMenu()
      break
    case 'rename':
      void renameWorkbookFromContextMenu()
      break
    case 'access':
      openWorkbookAccessFromContextMenu()
      break
    case 'defined_names':
      void openDefinedNamesFromWorkbookContextMenu()
      break
    case 'save_as':
      void saveAsWorkbookFromContextMenu('duplicate')
      break
    case 'template':
      void saveAsWorkbookFromContextMenu('template')
      break
    case 'duplicate':
      void saveAsWorkbookFromContextMenu('duplicate')
      break
    case 'delete':
      void deleteWorkbookFromContextMenu()
      break
  }
}

async function renameWorkbookFromContextMenu() {
  const currentWorkbook = workbook.value
  closeWorkbookContextMenu()
  if (!currentWorkbook || !canManageWorkbookSheets.value) {
    ElMessage.warning('没有权限重命名该工作簿')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入工作簿名称', '重命名工作簿', {
      inputValue: currentWorkbook.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    const nextTitle = value.trim()
    if (nextTitle === currentWorkbook.title) {
      return
    }
    const detail = await updateWorkbook(currentWorkbook.id, { title: nextTitle })
    workbook.value = detail
  } catch {
    return
  }
}

function openWorkbookAccessFromContextMenu() {
  closeWorkbookContextMenu()
  if (!workbook.value || !canManageWorkbookSheets.value) {
    ElMessage.warning('没有权限管理该工作簿')
    return
  }
  workbookAccessDialogVisible.value = true
}

async function openDefinedNamesFromWorkbookContextMenu() {
  const currentWorkbook = workbook.value
  closeWorkbookContextMenu()
  if (!currentWorkbook || !canEditWorkbookSheets.value) {
    ElMessage.warning('没有权限修改该工作簿')
    return
  }
  if (activeSheetId.value == null) {
    ElMessage.warning('请先选择一个工作表')
    return
  }
  const workspace = await waitForSheetWorkspaceRef()
  if (!workspace) {
    ElMessage.warning('工作表还未加载完成')
    return
  }
  workspace.openDefinedNamesDialog?.()
}

async function saveAsWorkbookFromContextMenu(mode: 'template' | 'duplicate') {
  const currentWorkbook = workbook.value
  closeWorkbookContextMenu()
  if (!currentWorkbook) {
    return
  }

  const modeLabel = mode === 'template' ? '模版' : '副本'
  const defaultTitle = `${currentWorkbook.title} ${modeLabel}`
  try {
    const { value } = await ElMessageBox.prompt('请输入新工作簿名称', `另存为${modeLabel}`, {
      inputValue: defaultTitle,
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    const nextWorkbook = await saveAsWorkbook(currentWorkbook.id, {
      mode,
      title: value.trim(),
    })
    openWorkbook(nextWorkbook, nextWorkbook.sheets[0]?.id ?? null)
  } catch {
    return
  }
}

async function deleteWorkbookFromContextMenu() {
  const currentWorkbook = workbook.value
  closeWorkbookContextMenu()
  if (!currentWorkbook || !canManageWorkbookSheets.value) {
    ElMessage.warning('没有权限删除该工作簿')
    return
  }

  try {
    await ElMessageBox.confirm(
      `工作簿“${currentWorkbook.title}”会移入回收站，其中未被其它工作簿引用的工作表也会一起移入回收站。`,
      '删除工作簿',
      {
        confirmButtonText: '移入回收站',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteWorkbook(currentWorkbook.id)
    void router.push('/notes/sheets')
  } catch {
    return
  }
}

function handleSheetTabContextMenuCommand(command: SheetTabContextMenuCommand) {
  switch (command) {
    case 'create':
      void createSheetFromTabContextMenu()
      break
    case 'rename':
      void renameSheetFromTabContextMenu()
      break
    case 'duplicate':
      void duplicateSheetFromTabContextMenu()
      break
    case 'configure':
      void configureSheetFromTabContextMenu()
      break
    case 'advanced':
      break
    case 'hide_empty_columns':
      void runSheetAdvancedActionFromTabContextMenu('hide_empty_columns')
      break
    case 'detect_option_filters':
      void runSheetAdvancedActionFromTabContextMenu('detect_option_filters')
      break
    case 'access':
      openSheetAccessFromTabContextMenu()
      break
    case 'link':
      openSheetTabLinkFromContextMenu()
      break
    case 'remove':
      void removeSheetFromTabContextMenu()
      break
    case 'delete':
      void deleteSheetFromTabContextMenu()
      break
  }
}

async function createSheetFromTabContextMenu() {
  closeSheetTabContextMenu()
  if (!workbook.value || !canEditWorkbookSheets.value) {
    ElMessage.warning('没有权限新建工作表')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入工作表名称', '新建工作表', {
      inputValue: '未命名工作表',
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作表名称不能为空',
    })
    const sheet = await createNoteSheet({
      title: value.trim(),
      workbook_id: workbook.value.id,
    })
    await refreshWorkbookAfterSheetMutation(sheet.id)
  } catch {
    return
  }
}

async function renameSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  closeSheetTabContextMenu()
  if (!sheet || !canEditSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限重命名该工作表')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入工作表名称', '重命名工作表', {
      inputValue: sheet.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作表名称不能为空',
    })
    const nextTitle = value.trim()
    if (nextTitle === sheet.title) {
      return
    }
    await updateNoteSheet(sheet.id, {
      title: nextTitle,
      base_version: Number(sheet.version || 1),
    }, { workbookId: workbook.value?.id ?? null })
    await refreshWorkbookAfterSheetMutation(sheet.id)
  } catch (error) {
    if (getNoteSheetApiErrorStatus(error) === 409) {
      ElMessage.warning('工作表已被其他人更新，请刷新后重试')
    }
    return
  }
}

async function duplicateSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  const currentWorkbook = workbook.value
  closeSheetTabContextMenu()
  if (!sheet || !currentWorkbook || !canEditWorkbookSheets.value || !canEditSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限复制该工作表')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入新工作表名称', '复制工作表', {
      inputValue: `${sheet.title} 副本`,
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作表名称不能为空',
    })
    const detail = await fetchNoteSheet(sheet.id, { workbookId: currentWorkbook.id })
    if (!detail) {
      ElMessage.error('工作表不存在或不可访问')
      return
    }
    const nextSheet = await createNoteSheet({
      title: value.trim(),
      workbook_id: currentWorkbook.id,
      document_json: detail.document_json,
    })
    await refreshWorkbookAfterSheetMutation(nextSheet.id)
  } catch {
    return
  }
}

async function removeSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  const currentWorkbook = workbook.value
  closeSheetTabContextMenu()
  if (!sheet || !currentWorkbook || !canManageWorkbookSheets.value || !canManageSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限移出该工作表')
    return
  }
  if (currentWorkbook.sheets.length <= 1) {
    ElMessage.warning('工作簿至少保留一个工作表')
    return
  }

  try {
    await ElMessageBox.confirm(
      `将“${sheet.title}”从当前工作簿中移出，工作表本身不会删除。`,
      '移出工作簿',
      {
        confirmButtonText: '确认移出',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await removeSheetFromWorkbook(currentWorkbook.id, sheet.id)
    await refreshWorkbookAfterSheetMutation(activeSheetId.value === sheet.id ? null : activeSheetId.value)
  } catch {
    return
  }
}

async function deleteSheetFromTabContextMenu() {
  const sheet = sheetTabContextMenuSheet.value
  const currentWorkbook = workbook.value
  closeSheetTabContextMenu()
  if (!sheet || !currentWorkbook || !canManageSheetTabContextMenuSheet.value) {
    ElMessage.warning('没有权限删除该工作表')
    return
  }

  try {
    await ElMessageBox.confirm(
      `工作表“${sheet.title}”会移入回收站，并暂时从普通工作簿视图中隐藏。`,
      '删除工作表',
      {
        confirmButtonText: '移入回收站',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteNoteSheet(sheet.id)
    await refreshWorkbookAfterSheetMutation(activeSheetId.value === sheet.id ? null : activeSheetId.value)
  } catch {
    return
  }
}

function handleGlobalMouseDown(event: MouseEvent) {
  const target = event.target
  if (
    target instanceof HTMLElement
    && (target.closest('.sheet-tab-context-menu') || target.closest('.workbook-context-menu'))
  ) {
    return
  }
  if (sheetTabContextMenu.value.visible) {
    closeSheetTabContextMenu()
  }
  if (workbookContextMenu.value.visible) {
    closeWorkbookContextMenu()
  }
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeSheetTabContextMenu()
    closeWorkbookContextMenu()
  }
}

function handleSheetMissing() {
  errorText.value = '工作表不存在'
  clearResourceAccessIssue()
  if (!isWorkbookMode.value) {
    standaloneSheetTitle.value = ''
    standaloneWorkbookTitle.value = ''
    standaloneParentWorkbookId.value = null
    standaloneWorkbookItems.value = []
  }
}

function handleSheetLoadError(payload: SheetWorkspaceLoadErrorPayload) {
  const currentSheetId = isWorkbookMode.value ? activeSheetId.value : sheetId.value
  if (payload.sheetId !== currentSheetId) {
    return
  }
  if (isAccessDeniedStatus(payload.status)) {
    errorText.value = payload.message || '没有权限访问该工作表'
    setResourceAccessIssue('sheet', payload.status, errorText.value)
    if (!isWorkbookMode.value) {
      standaloneSheetTitle.value = ''
      standaloneWorkbookTitle.value = ''
      standaloneParentWorkbookId.value = null
      standaloneWorkbookItems.value = []
    }
    return
  }
  errorText.value = payload.message || '工作表加载失败'
}

function handleSheetSync(payload: {
  id: number
  title: string
  version: number
  updatedAt: number
  parentWorkbookId?: number | null
  workbookItems?: WorkbookRefItem[]
}) {
  clearResourceAccessIssue()
  if (!isWorkbookMode.value) {
    standaloneParentWorkbookId.value = payload.parentWorkbookId ?? null
    standaloneWorkbookItems.value = payload.workbookItems ?? []
    standaloneSheetTitle.value = payload.title || ''
    standaloneWorkbookTitle.value = standaloneWorkbookItems.value[0]?.title || ''
    return
  }
  if (!workbook.value) {
    return
  }
  const sheet = workbook.value.sheets.find((item) => item.id === payload.id)
  if (sheet) {
    sheet.title = payload.title
    sheet.updated_at = payload.updatedAt
  }
}

watch(
  pageDocumentTitle,
  (title) => {
    document.title = title
  },
  { immediate: true },
)

watch(
  sheetId,
  (nextSheetId, previousSheetId) => {
    if (nextSheetId !== previousSheetId && !isWorkbookMode.value) {
      standaloneSheetTitle.value = ''
      standaloneWorkbookTitle.value = ''
      standaloneParentWorkbookId.value = null
      standaloneWorkbookItems.value = []
    }
  },
)

watch(
  () => route.fullPath,
  () => {
    clearResourceAccessIssue()
    document.title = pageDocumentTitle.value
  },
)

watch(
  [workbookId, isWorkbookMode],
  () => {
    void loadWorkbookResource()
  },
)

watch(
  querySheetId,
  () => {
    if (!isWorkbookMode.value) {
      return
    }
    if (!syncActiveSheetFromWorkbookRoute() && !loading.value) {
      void loadWorkbookResource()
    }
  },
)

onMounted(() => {
  markBootPerf('resource-view.mounted', {
    workbookMode: isWorkbookMode.value,
    workbookId: workbookId.value,
    sheetId: querySheetId.value,
  })
  document.addEventListener('mousedown', handleGlobalMouseDown)
  document.addEventListener('keydown', handleGlobalKeydown)
  if (isWorkbookMode.value) {
    void loadWorkbookResource()
  } else if (sheetId.value == null) {
    errorText.value = '工作表地址无效'
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleGlobalMouseDown)
  document.removeEventListener('keydown', handleGlobalKeydown)
})
</script>

<template>
  <div class="sheet-resource-page" v-loading="loading">
    <template v-if="isWorkbookMode">
      <div v-if="workbook" class="resource-tabs-bar">
        <div
          class="resource-workbook-title"
          :title="workbook.title"
          @contextmenu="openWorkbookContextMenu"
        >
          {{ workbook.title }}
        </div>
        <div
          ref="sheetTabsRef"
          class="resource-sheet-tabs"
          :class="{ 'is-sortable': canReorderWorkbookSheets }"
        >
          <button
            v-for="sheet in workbook.sheets"
            :key="sheet.id"
            type="button"
            class="resource-sheet-tab"
            :class="{ active: sheet.id === activeSheetId }"
            :title="canReorderWorkbookSheets ? `${sheet.title}（拖拽调整顺序）` : sheet.title"
            @click="handleSheetTabClick(sheet.id)"
            @contextmenu.capture="(event) => openSheetTabContextMenu(event, sheet)"
          >
            {{ sheet.title }}
          </button>
        </div>
        <div class="resource-user-slot">
          <div v-if="userIdentityLabel" class="resource-user-identity">
            <el-icon><User /></el-icon>
            <span>{{ userIdentityLabel }}</span>
          </div>
          <button v-else type="button" class="resource-login-button" @click="openLoginPage">
            登录
          </button>
        </div>
      </div>
      <div
        v-if="sheetTabContextMenu.visible"
        class="sheet-tab-context-menu"
        :style="{ left: `${sheetTabContextMenu.left}px`, top: `${sheetTabContextMenu.top}px` }"
        @contextmenu.prevent.stop
        @mousedown.stop
      >
        <template v-for="item in sheetTabContextMenuItems" :key="item.command">
          <div v-if="item.divided" class="sheet-tab-context-menu-separator"></div>
          <div
            v-if="item.linkSubmenu"
            class="sheet-tab-context-menu-branch"
          >
            <button
              type="button"
              class="sheet-tab-context-menu-item has-submenu"
              @click="handleSheetTabContextMenuCommand(item.command)"
            >
              {{ item.label }}
            </button>
            <div class="sheet-tab-context-submenu resource-link-submenu">
              <button
                v-for="linkItem in resourceLinkMenuItems"
                :key="linkItem.command"
                type="button"
                class="sheet-tab-context-menu-item"
                @click="handleSheetTabLinkMenuCommand(linkItem.command)"
              >
                {{ linkItem.label }}
              </button>
            </div>
          </div>
          <div
            v-else-if="item.deleteSubmenu"
            class="sheet-tab-context-menu-branch"
          >
            <button
              type="button"
              class="sheet-tab-context-menu-item has-submenu"
              :class="{ danger: item.danger }"
              @click="handleSheetTabContextMenuCommand(item.command)"
            >
              {{ item.label }}
            </button>
            <div class="sheet-tab-context-submenu">
              <button
                type="button"
                class="sheet-tab-context-menu-item"
                @click="handleSheetTabContextMenuCommand('remove')"
              >
                移出工作簿
              </button>
              <button
                type="button"
                class="sheet-tab-context-menu-item danger"
                @click="handleSheetTabContextMenuCommand('delete')"
              >
                删除工作表
              </button>
            </div>
          </div>
          <div
            v-else-if="item.advancedSubmenu"
            class="sheet-tab-context-menu-branch"
          >
            <button
              type="button"
              class="sheet-tab-context-menu-item has-submenu"
              @click="handleSheetTabContextMenuCommand(item.command)"
            >
              {{ item.label }}
            </button>
            <div class="sheet-tab-context-submenu sheet-advanced-submenu">
              <button
                type="button"
                class="sheet-tab-context-menu-item"
                @click="handleSheetTabContextMenuCommand('hide_empty_columns')"
              >
                隐藏空列
              </button>
              <button
                type="button"
                class="sheet-tab-context-menu-item"
                @click="handleSheetTabContextMenuCommand('detect_option_filters')"
              >
                检测并设置选项筛选
              </button>
            </div>
          </div>
          <button
            v-else
            type="button"
            class="sheet-tab-context-menu-item"
            :class="{ danger: item.danger }"
            @click="handleSheetTabContextMenuCommand(item.command)"
          >
            {{ item.label }}
          </button>
        </template>
      </div>
      <div
        v-if="workbookContextMenu.visible"
        class="sheet-tab-context-menu workbook-context-menu"
        :style="{ left: `${workbookContextMenu.left}px`, top: `${workbookContextMenu.top}px` }"
        @contextmenu.prevent.stop
        @mousedown.stop
      >
        <template v-for="item in workbookContextMenuItems" :key="item.command">
          <div v-if="item.divided" class="sheet-tab-context-menu-separator"></div>
          <div
            v-if="item.linkSubmenu"
            class="sheet-tab-context-menu-branch"
          >
            <button
              type="button"
              class="sheet-tab-context-menu-item has-submenu"
              @click="handleWorkbookContextMenuCommand(item.command)"
            >
              {{ item.label }}
            </button>
            <div class="sheet-tab-context-submenu resource-link-submenu">
              <button
                v-for="linkItem in resourceLinkMenuItems"
                :key="linkItem.command"
                type="button"
                class="sheet-tab-context-menu-item"
                @click="handleWorkbookLinkMenuCommand(linkItem.command)"
              >
                {{ linkItem.label }}
              </button>
            </div>
          </div>
          <div
            v-else-if="item.saveAsSubmenu"
            class="sheet-tab-context-menu-branch"
          >
            <button
              type="button"
              class="sheet-tab-context-menu-item has-submenu"
              @click="handleWorkbookContextMenuCommand(item.command)"
            >
              {{ item.label }}
            </button>
            <div class="sheet-tab-context-submenu">
              <button
                type="button"
                class="sheet-tab-context-menu-item"
                @click="handleWorkbookContextMenuCommand('duplicate')"
              >
                副本
              </button>
              <button
                type="button"
                class="sheet-tab-context-menu-item"
                @click="handleWorkbookContextMenuCommand('template')"
              >
                模版
              </button>
            </div>
          </div>
          <button
            v-else
            type="button"
            class="sheet-tab-context-menu-item"
            :class="{ danger: item.danger }"
            @click="handleWorkbookContextMenuCommand(item.command)"
          >
            {{ item.label }}
          </button>
        </template>
      </div>

      <NoteSheetAccessDialog
        v-model="sheetAccessDialogVisible"
        resource-type="sheet"
        :resource-id="sheetAccessDialogSheet?.id ?? null"
        :title="sheetAccessDialogSheet?.title ?? ''"
        @saved="handleSheetAccessSaved"
      />
      <NoteSheetAccessDialog
        v-model="workbookAccessDialogVisible"
        resource-type="workbook"
        :resource-id="workbook?.id ?? null"
        :title="workbook?.title ?? ''"
        @saved="handleWorkbookAccessSaved"
      />

      <NoteSheetWorkspace
        v-if="activeSheetId && !resourceAccessIssue"
        ref="sheetWorkspaceRef"
        class="resource-sheet-workspace"
        :key="`workbook:${workbookId}:${sheetWorkspaceReloadKey}`"
        :workbook-id="workbookId"
        :workbook-title="workbook?.title ?? ''"
        :sheet-id="activeSheetId"
        :initial-detail="activeSheetPrefetchedDetail"
        :initial-workspace-view="routeWorkspaceView"
        default-height-mode="fill"
        runtime-height-mode="fill"
        :access-capabilities="activeSheet?.access?.capabilities ?? null"
        :show-title-input="false"
        empty-text="请选择工作表"
        @missing="handleSheetMissing"
        @load-error="handleSheetLoadError"
        @sheet-sync="handleSheetSync"
      />
      <el-empty v-else-if="workbook && !resourceAccessIssue" :description="errorText || '没有可访问的工作表'" />
    </template>

    <template v-else>
      <NoteSheetWorkspace
        v-if="sheetId && !resourceAccessIssue"
        class="resource-sheet-workspace"
        :key="`sheet:${sheetWorkspaceReloadKey}`"
        :sheet-id="sheetId"
        :initial-workspace-view="routeWorkspaceView"
        default-height-mode="fill"
        runtime-height-mode="fill"
        :show-title-input="false"
        :show-back-button="standaloneWorkbookBackTo !== ''"
        show-user-identity
        :back-to="standaloneWorkbookBackTo || '/notes/sheets'"
        back-label="回到工作簿"
        empty-text="工作表不存在"
        @missing="handleSheetMissing"
        @load-error="handleSheetLoadError"
        @sheet-sync="handleSheetSync"
      />
      <el-empty v-else-if="!resourceAccessIssue" :description="errorText || '工作表地址无效'" />
    </template>

    <div v-if="resourceAccessIssue" class="resource-access-panel">
      <div class="resource-access-content">
        <div class="resource-access-title">{{ resourceAccessTitle }}</div>
        <div class="resource-access-description">{{ resourceAccessDescription }}</div>

        <form
          v-if="!userStore.isAuthenticated"
          class="resource-login-form"
          @submit.prevent="submitInlineLogin"
        >
          <el-input
            v-model.trim="inlineLoginForm.username"
            placeholder="用户名"
            autocomplete="username"
          />
          <el-input
            v-model="inlineLoginForm.password"
            type="password"
            placeholder="密码"
            autocomplete="current-password"
            show-password
          />
          <el-alert
            v-if="inlineLoginError || userStore.error"
            :title="inlineLoginError || userStore.error || ''"
            type="error"
            :closable="false"
            show-icon
          />
          <div class="resource-access-actions">
            <el-button type="primary" native-type="submit" :loading="userStore.loading">
              登录后重试
            </el-button>
            <el-button
              text
              @click="router.push({ name: 'Login', query: { redirect: route.fullPath } })"
            >
              去登录页
            </el-button>
          </div>
        </form>

        <div v-else class="resource-account-panel">
          <div v-if="currentAccountLabel" class="resource-current-account">
            当前账号：{{ currentAccountLabel }}
          </div>
          <div class="resource-access-actions">
            <el-button type="primary" @click="refreshCurrentResourceAfterAuth">重试</el-button>
            <el-button plain @click="switchInlineLoginAccount">换账号登录</el-button>
          </div>
        </div>
      </div>
    </div>

    <el-empty v-if="errorText && !loading && isWorkbookMode && !workbook && !resourceAccessIssue" :description="errorText" />
  </div>
</template>

<style scoped>
.sheet-resource-page {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #fff;
  overflow: hidden;
  overscroll-behavior: none;
}

.resource-tabs-bar {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 0;
  min-height: 46px;
  padding: 8px 16px 0;
  border-bottom: 1px solid #efe4d3;
  overflow-x: auto;
}

.resource-workbook-title {
  flex: 0 0 auto;
  max-width: 320px;
  margin-right: 12px;
  padding: 0 2px 10px;
  color: #4b5563;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.resource-sheet-tabs {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 0;
}

.resource-user-slot {
  flex: 0 0 auto;
  margin-left: auto;
  padding: 0 2px 10px 16px;
}

.resource-user-identity,
.resource-login-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #475569;
  font-size: 14px;
  line-height: 20px;
  white-space: nowrap;
}

.resource-user-identity :deep(.el-icon) {
  color: #64748b;
  font-size: 14px;
}

.resource-login-button {
  border: 0;
  padding: 0;
  background: transparent;
  color: #2563eb;
  cursor: pointer;
}

.resource-login-button:hover {
  color: #1d4ed8;
}

.resource-sheet-tab {
  flex: 0 0 auto;
  border: 1px solid #ebe2d4;
  border-bottom: 0;
  border-radius: 8px 8px 0 0;
  background: #faf7f1;
  color: #6b5a44;
  padding: 7px 16px 8px;
  font-size: 14px;
  font-weight: 600;
  line-height: 20px;
  cursor: pointer;
}

.resource-sheet-tabs.is-sortable .resource-sheet-tab {
  cursor: grab;
}

.resource-sheet-tabs.is-sortable .resource-sheet-tab:active {
  cursor: grabbing;
}

.resource-sheet-tab:hover {
  background: #f1e7d9;
  color: #5c4932;
}

.resource-sheet-tab.active {
  background: #fff;
  color: #2f2414;
  box-shadow: inset 0 3px 0 #5b8def;
}

.resource-sheet-tab-sortable-ghost {
  opacity: 0.64;
  background: #eff6ff;
}

.sheet-tab-context-menu {
  position: fixed;
  z-index: 3000;
  box-sizing: border-box;
  min-width: 120px;
  padding: 4px 0;
  border: 1px solid #d8dce5;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 8px 20px rgb(15 23 42 / 16%);
}

.sheet-tab-context-menu-branch {
  position: relative;
}

.sheet-tab-context-menu-item {
  position: relative;
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 7px 18px;
  color: #1f2937;
  font-size: 14px;
  line-height: 20px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}

.sheet-tab-context-menu-item.has-submenu {
  padding-right: 30px;
}

.sheet-tab-context-menu-item.has-submenu::after {
  content: '>';
  position: absolute;
  top: 50%;
  right: 14px;
  transform: translateY(-50%);
  color: #9ca3af;
}

.sheet-tab-context-menu-item:hover:not(:disabled) {
  background: #f5f7fa;
}

.sheet-tab-context-submenu {
  position: absolute;
  top: -5px;
  left: calc(100% - 2px);
  display: none;
  box-sizing: border-box;
  min-width: 132px;
  padding: 4px 0;
  border: 1px solid #d8dce5;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 8px 20px rgb(15 23 42 / 16%);
}

.resource-link-submenu {
  min-width: 176px;
}

.sheet-advanced-submenu {
  min-width: 196px;
}

.sheet-tab-context-menu-branch:hover .sheet-tab-context-submenu,
.sheet-tab-context-menu-branch:focus-within .sheet-tab-context-submenu {
  display: block;
}

.sheet-tab-context-menu-item.danger {
  color: #c2410c;
}

.sheet-tab-context-menu-item:disabled {
  color: #b4b8c0;
  cursor: not-allowed;
}

.sheet-tab-context-menu-separator {
  height: 1px;
  margin: 4px 0;
  background: #edf0f5;
}

.resource-sheet-workspace {
  flex: 1 1 auto;
  min-height: 0;
}

.resource-access-panel {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 20px;
  box-sizing: border-box;
}

.resource-access-content {
  width: min(360px, 100%);
  display: grid;
  gap: 14px;
}

.resource-access-title {
  color: #1f2937;
  font-size: 18px;
  font-weight: 700;
  line-height: 26px;
  text-align: center;
}

.resource-access-description {
  color: #6b7280;
  font-size: 14px;
  line-height: 22px;
  text-align: center;
}

.resource-login-form {
  display: grid;
  gap: 10px;
}

.resource-account-panel {
  display: grid;
  gap: 12px;
  justify-items: center;
}

.resource-current-account {
  color: #4b5563;
  font-size: 14px;
  line-height: 22px;
}

.resource-access-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>

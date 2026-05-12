<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Delete,
  Edit,
  MoreFilled,
  Plus,
  Refresh,
  Search,
  Share,
} from '@element-plus/icons-vue'

import {
  createWorkbook,
  deleteWorkbook,
  fetchWorkbookAccess,
  fetchWorkbooks,
  saveAsWorkbook,
  updateWorkbook,
  updateWorkbookAccess,
  type NoteSheetResourceAccessGrantUpdate,
  type NoteSheetResourceRole,
  type WorkbookSummary,
} from '@/api/noteSheets'
import { useUserStore } from '@/store/userStore'

type WorkbookFilter = 'all' | 'mine' | 'other'
type AccessAnonymousRole = 'none' | 'viewer'

type AccessUserGrantDraft = {
  key: string
  username: string
  nickname: string
  subjectUserId?: number | null
  role: Exclude<NoteSheetResourceRole, 'none'>
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const workbooks = ref<WorkbookSummary[]>([])
const searchText = ref('')
const workbookFilter = ref<WorkbookFilter>('all')

const accessDialogVisible = ref(false)
const accessDialogLoading = ref(false)
const accessDialogSaving = ref(false)
const accessDialogWorkbook = ref<WorkbookSummary | null>(null)
const accessAnonymousRole = ref<AccessAnonymousRole>('none')
const accessUserGrants = ref<AccessUserGrantDraft[]>([])

const currentUserId = computed(() => userStore.user?.id ?? null)
const normalizedSearchText = computed(() => searchText.value.trim().toLowerCase())

const totalSheetCount = computed(() => (
  workbooks.value.reduce((total, workbook) => total + workbook.sheet_count, 0)
))

const filteredWorkbooks = computed(() => {
  const query = normalizedSearchText.value
  return workbooks.value.filter((workbook) => {
    const isMine = workbook.owner_user_id != null && workbook.owner_user_id === currentUserId.value
    if (workbookFilter.value === 'mine' && !isMine) {
      return false
    }
    if (workbookFilter.value === 'other' && isMine) {
      return false
    }
    if (!query) {
      return true
    }
    return workbook.title.toLowerCase().includes(query)
      || String(workbook.id).includes(query)
      || accessRoleLabel(workbook.access?.role).toLowerCase().includes(query)
  })
})

const filterOptions: Array<{ value: WorkbookFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'mine', label: '我创建的' },
  { value: 'other', label: '其他可访问' },
]

const accessDialogTitle = computed(() => {
  const title = accessDialogWorkbook.value?.title || '工作簿'
  return `共享权限：${title}`
})

const userAccessRoleOptions = [
  { value: 'deny', label: '无权限' },
  { value: 'viewer', label: '只读' },
  { value: 'editor', label: '可编辑' },
  { value: 'manager', label: '可管理' },
] as const

function canManageWorkbook(workbook: WorkbookSummary) {
  return workbook.access?.capabilities.can_manage_access ?? true
}

function accessRoleLabel(role?: NoteSheetResourceRole | null) {
  switch (role) {
    case 'manager':
      return '可管理'
    case 'editor':
      return '可编辑'
    case 'viewer':
      return '只读'
    case 'deny':
      return '无权限'
    default:
      return '未知'
  }
}

function formatDateTime(timestamp: number) {
  if (!timestamp) {
    return '-'
  }
  const date = new Date(timestamp * 1000)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:${minute}`
}

function normalizePositiveInt(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value
  const numeric = Number(raw)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function redirectLegacyWorkbookQuery() {
  const workbookId = normalizePositiveInt(route.query.workbook)
  if (workbookId == null) {
    return false
  }

  const sheetId = normalizePositiveInt(route.query.sheet)
  void router.replace({
    path: `/workbook/${workbookId}`,
    query: sheetId != null ? { sheet: String(sheetId) } : undefined,
  })
  return true
}

function createAccessUserGrantDraft(): AccessUserGrantDraft {
  return {
    key: `${Date.now()}:${Math.random().toString(36).slice(2)}`,
    username: '',
    nickname: '',
    subjectUserId: null,
    role: 'viewer',
  }
}

async function reloadWorkbooks() {
  loading.value = true
  try {
    workbooks.value = await fetchWorkbooks()
  } catch (error) {
    console.warn('Failed to load note sheet workbooks:', error)
    ElMessage.error('加载工作簿失败')
  } finally {
    loading.value = false
  }
}

async function initializeLibraryPage() {
  if (redirectLegacyWorkbookQuery()) {
    return
  }
  if (userStore.isAuthenticated && !userStore.user && !userStore.loading) {
    await userStore.fetchUserProfile()
  }
  await reloadWorkbooks()
}

function resolveWorkbookHref(workbookId: number, sheetId?: number | null) {
  return router.resolve({
    path: `/workbook/${workbookId}`,
    query: sheetId != null ? { sheet: String(sheetId) } : undefined,
  }).href
}

function openWorkbook(workbook: WorkbookSummary, sheetId?: number | null) {
  const href = resolveWorkbookHref(workbook.id, sheetId)
  window.open(href, '_blank', 'noopener,noreferrer')
}

async function handleCreateWorkbook() {
  try {
    const { value } = await ElMessageBox.prompt('请输入工作簿名称', '新建工作簿', {
      inputValue: '',
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    const workbook = await createWorkbook({ title: value.trim() })
    await reloadWorkbooks()
    openWorkbook(workbook)
  } catch {
    return
  }
}

async function handleRenameWorkbook(workbook: WorkbookSummary) {
  if (!canManageWorkbook(workbook)) {
    ElMessage.warning('没有权限重命名该工作簿')
    return
  }

  try {
    const { value } = await ElMessageBox.prompt('请输入工作簿名称', '重命名工作簿', {
      inputValue: workbook.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    const nextTitle = value.trim()
    if (nextTitle === workbook.title) {
      return
    }
    await updateWorkbook(workbook.id, { title: nextTitle })
    await reloadWorkbooks()
  } catch {
    return
  }
}

async function handleSaveAsWorkbook(workbook: WorkbookSummary, mode: 'template' | 'duplicate') {
  const modeLabel = mode === 'template' ? '模版' : '副本'
  const defaultTitle = `${workbook.title} ${modeLabel}`

  try {
    const { value } = await ElMessageBox.prompt('请输入新工作簿名称', `另存为${modeLabel}`, {
      inputValue: defaultTitle,
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim() ? true : '工作簿名称不能为空',
    })
    const nextWorkbook = await saveAsWorkbook(workbook.id, {
      mode,
      title: value.trim(),
    })
    await reloadWorkbooks()
    openWorkbook(nextWorkbook, nextWorkbook.sheets[0]?.id ?? null)
  } catch {
    return
  }
}

async function handleDeleteWorkbook(workbook: WorkbookSummary) {
  if (!canManageWorkbook(workbook)) {
    ElMessage.warning('没有权限删除该工作簿')
    return
  }

  try {
    await ElMessageBox.confirm(
      `删除工作簿“${workbook.title}”后，会同时删除其中未被其它工作簿引用的工作表及其共享权限。此操作不可恢复。`,
      '删除工作簿',
      {
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await deleteWorkbook(workbook.id)
    await reloadWorkbooks()
  } catch {
    return
  }
}

function normalizeAccessDialogFromGrants(
  grants: Array<{
    subject_type: 'anonymous' | 'user'
    subject_key: string
    subject_user_id?: number | null
    username: string
    nickname: string
    role: Exclude<NoteSheetResourceRole, 'none'>
  }>,
) {
  const anonymousGrant = grants.find((grant) => grant.subject_type === 'anonymous')
  accessAnonymousRole.value = anonymousGrant?.role === 'viewer' ? 'viewer' : 'none'
  accessUserGrants.value = grants
    .filter((grant) => grant.subject_type === 'user')
    .map((grant) => ({
      key: grant.subject_key,
      username: grant.username,
      nickname: grant.nickname,
      subjectUserId: grant.subject_user_id ?? null,
      role: grant.role,
    }))
}

async function openAccessDialog(workbook: WorkbookSummary) {
  if (!canManageWorkbook(workbook)) {
    ElMessage.warning('没有权限管理该工作簿')
    return
  }

  accessDialogWorkbook.value = workbook
  accessDialogVisible.value = true
  accessDialogLoading.value = true
  accessUserGrants.value = []
  accessAnonymousRole.value = 'none'

  try {
    const detail = await fetchWorkbookAccess(workbook.id)
    normalizeAccessDialogFromGrants(detail.grants)
  } catch (error) {
    console.warn('Failed to load workbook access grants:', error)
    accessDialogVisible.value = false
    ElMessage.error('读取共享权限失败')
  } finally {
    accessDialogLoading.value = false
  }
}

function addAccessUserGrant() {
  accessUserGrants.value = [...accessUserGrants.value, createAccessUserGrantDraft()]
}

function removeAccessUserGrant(key: string) {
  accessUserGrants.value = accessUserGrants.value.filter((grant) => grant.key !== key)
}

function buildAccessGrantUpdates(): NoteSheetResourceAccessGrantUpdate[] {
  const grants: NoteSheetResourceAccessGrantUpdate[] = []
  if (accessAnonymousRole.value === 'viewer') {
    grants.push({
      subject_type: 'anonymous',
      role: 'viewer',
    })
  }

  const seenUsernames = new Set<string>()
  for (const grant of accessUserGrants.value) {
    const username = grant.username.trim()
    if (!username || seenUsernames.has(username)) {
      continue
    }
    seenUsernames.add(username)
    grants.push({
      subject_type: 'user',
      username,
      subject_user_id: grant.subjectUserId ?? undefined,
      role: grant.role,
    })
  }
  return grants
}

async function saveAccessDialog() {
  const workbook = accessDialogWorkbook.value
  if (!workbook) {
    return
  }

  accessDialogSaving.value = true
  try {
    const detail = await updateWorkbookAccess(workbook.id, buildAccessGrantUpdates())
    normalizeAccessDialogFromGrants(detail.grants)
    ElMessage.success('共享权限已保存')
    accessDialogVisible.value = false
    await reloadWorkbooks()
  } catch (error) {
    console.warn('Failed to save workbook access grants:', error)
    ElMessage.error('保存共享权限失败，请检查用户名')
  } finally {
    accessDialogSaving.value = false
  }
}

function handleWorkbookCommand(command: string | number | object, workbook: WorkbookSummary) {
  switch (command) {
    case 'rename':
      void handleRenameWorkbook(workbook)
      break
    case 'access':
      void openAccessDialog(workbook)
      break
    case 'template':
      void handleSaveAsWorkbook(workbook, 'template')
      break
    case 'duplicate':
      void handleSaveAsWorkbook(workbook, 'duplicate')
      break
    case 'delete':
      void handleDeleteWorkbook(workbook)
      break
  }
}

onMounted(() => {
  void initializeLibraryPage()
})
</script>

<template>
  <div class="workbook-library-page" v-loading="loading">
    <header class="library-header">
      <div class="library-heading">
        <h1>星云表格</h1>
        <div class="library-count">
          {{ workbooks.length }} 个工作簿 / {{ totalSheetCount }} 个工作表
        </div>
      </div>

      <div class="library-actions">
        <el-input
          v-model="searchText"
          class="library-search"
          :prefix-icon="Search"
          clearable
          placeholder="搜索工作簿"
        />
        <button
          v-for="option in filterOptions"
          :key="option.value"
          type="button"
          class="filter-button"
          :class="{ active: workbookFilter === option.value }"
          @click="workbookFilter = option.value"
        >
          {{ option.label }}
        </button>
        <el-button :icon="Refresh" @click="reloadWorkbooks">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="handleCreateWorkbook">新建工作簿</el-button>
      </div>
    </header>

    <section class="workbook-table" aria-label="星云表格工作簿文件库">
      <div v-if="filteredWorkbooks.length" class="workbook-table-scroll">
        <table class="workbook-table-inner">
          <thead>
            <tr>
              <th scope="col">工作簿</th>
              <th scope="col">权限</th>
              <th scope="col">工作表</th>
              <th scope="col">更新时间</th>
              <th scope="col" class="workbook-actions-heading">操作</th>
              <th scope="col" class="workbook-spacer-cell" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="workbook in filteredWorkbooks"
              :key="workbook.id"
              class="workbook-row"
            >
              <td class="workbook-name-cell">
                <a
                  class="workbook-title-button"
                  :href="resolveWorkbookHref(workbook.id)"
                  target="_blank"
                  rel="noopener noreferrer"
                  :title="workbook.title"
                >
                  <span class="workbook-subtitle">
                    #{{ workbook.id }}
                  </span>
                  <span class="workbook-title">{{ workbook.title }}</span>
                </a>
              </td>
              <td class="workbook-role">{{ accessRoleLabel(workbook.access?.role) }}</td>
              <td class="workbook-sheet-count">{{ workbook.sheet_count }}</td>
              <td class="workbook-updated">{{ formatDateTime(workbook.updated_at) }}</td>
              <td class="workbook-actions-cell">
                <div class="workbook-row-actions">
                  <el-dropdown trigger="click" @command="(command) => handleWorkbookCommand(command, workbook)">
                    <el-button size="small" :icon="MoreFilled" title="更多操作" />
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item command="rename" :icon="Edit" :disabled="!canManageWorkbook(workbook)">
                          重命名
                        </el-dropdown-item>
                        <el-dropdown-item command="access" :icon="Share" :disabled="!canManageWorkbook(workbook)">
                          共享权限
                        </el-dropdown-item>
                        <el-dropdown-item command="template">
                          另存为模版
                        </el-dropdown-item>
                        <el-dropdown-item command="duplicate">
                          另存为副本
                        </el-dropdown-item>
                        <el-dropdown-item divided command="delete" :icon="Delete" :disabled="!canManageWorkbook(workbook)">
                          删除工作簿
                        </el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </div>
              </td>
              <td class="workbook-spacer-cell" aria-hidden="true"></td>
            </tr>
          </tbody>
        </table>
      </div>

      <el-empty
        v-else
        class="workbook-empty"
        :description="workbooks.length ? '没有匹配的工作簿' : '暂无工作簿'"
      />
    </section>

    <el-dialog
      v-model="accessDialogVisible"
      :title="accessDialogTitle"
      width="520px"
      class="resource-access-dialog"
    >
      <div v-loading="accessDialogLoading" class="resource-access-body">
        <div class="resource-access-row">
          <label class="resource-access-label">游客</label>
          <el-select v-model="accessAnonymousRole" class="resource-access-control">
            <el-option value="none" label="无权限" />
            <el-option value="viewer" label="只读" />
          </el-select>
        </div>

        <div class="resource-access-users-header">
          <span>指定用户</span>
          <el-button size="small" @click="addAccessUserGrant">添加</el-button>
        </div>

        <div v-if="accessUserGrants.length" class="resource-access-users">
          <div v-for="grant in accessUserGrants" :key="grant.key" class="resource-access-user-row">
            <el-input
              v-model="grant.username"
              class="resource-access-username"
              placeholder="username"
              :title="grant.nickname || grant.username"
            />
            <el-select v-model="grant.role" class="resource-access-role">
              <el-option
                v-for="option in userAccessRoleOptions"
                :key="option.value"
                :value="option.value"
                :label="option.label"
              />
            </el-select>
            <button
              type="button"
              class="resource-access-remove"
              title="移除"
              aria-label="移除"
              @click="removeAccessUserGrant(grant.key)"
            >
              -
            </button>
          </div>
        </div>
        <div v-else class="resource-access-empty">未指定用户</div>
      </div>

      <template #footer>
        <el-button @click="accessDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="accessDialogSaving" @click="saveAccessDialog">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.workbook-library-page {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 16px 18px;
  background: #f8fafc;
  overflow: hidden;
  gap: 14px;
}

.library-header {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.library-heading {
  display: grid;
  gap: 4px;
  min-width: 160px;
}

.library-heading h1 {
  margin: 0;
  color: #172033;
  font-size: 22px;
  font-weight: 700;
  line-height: 30px;
}

.library-count {
  color: #697386;
  font-size: 13px;
  line-height: 20px;
}

.library-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.library-search {
  width: 240px;
}

.filter-button {
  height: 32px;
  border: 1px solid #d8e0ea;
  border-radius: 6px;
  background: #fff;
  padding: 0 12px;
  color: #526071;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.filter-button:hover {
  border-color: #9ab9ee;
  color: #2f6fd6;
}

.filter-button.active {
  border-color: #2f6fd6;
  background: #edf4ff;
  color: #1f5fbe;
}

.workbook-table {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #dfe7f0;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.workbook-table-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.workbook-table-inner {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
}

.workbook-table-inner th,
.workbook-table-inner td {
  box-sizing: border-box;
  padding: 0 14px;
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.workbook-table-inner th {
  height: 38px;
  border-bottom: 1px solid #e5ebf2;
  background: #f3f6fa;
  color: #5a6677;
  font-size: 12px;
  font-weight: 700;
}

.workbook-table-inner td {
  height: 48px;
  border-bottom: 1px solid #eef2f6;
}

.workbook-table-inner th + th,
.workbook-table-inner td + td {
  padding-left: 24px;
}

.workbook-row:hover {
  background: #f8fbff;
}

.workbook-name-cell {
  max-width: min(52vw, 520px);
}

.workbook-title-button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: min(52vw, 520px);
  color: inherit;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.workbook-title {
  flex: 1 1 auto;
  min-width: 0;
  color: #182235;
  font-size: 14px;
  font-weight: 700;
  line-height: 22px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workbook-title-button:hover .workbook-title {
  color: #2368d1;
}

.workbook-subtitle {
  flex: 0 0 auto;
  min-width: 24px;
  color: #8a96a8;
  font-size: 12px;
  line-height: 22px;
}

.workbook-role,
.workbook-sheet-count,
.workbook-updated {
  color: #4f5d70;
  font-size: 13px;
  line-height: 20px;
  white-space: nowrap;
}

.workbook-sheet-count {
  color: #172033;
  font-weight: 700;
}

.workbook-actions-heading,
.workbook-actions-cell {
  text-align: right;
}

.workbook-spacer-cell {
  width: 100%;
  padding: 0 !important;
}

.workbook-row-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

.workbook-empty {
  flex: 1 1 auto;
  min-height: 220px;
}

.resource-access-body {
  min-height: 180px;
}

.resource-access-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.resource-access-label {
  flex: 0 0 72px;
  color: #5f6b7a;
  font-size: 14px;
  font-weight: 600;
}

.resource-access-control {
  flex: 1;
}

.resource-access-users-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid #edf1f5;
  color: #2f3a4a;
  font-size: 14px;
  font-weight: 600;
}

.resource-access-users {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.resource-access-user-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 128px 28px;
  align-items: center;
  gap: 8px;
}

.resource-access-username,
.resource-access-role {
  width: 100%;
}

.resource-access-remove {
  width: 28px;
  height: 28px;
  border: 1px solid #f0c4c4;
  border-radius: 6px;
  background: #fff;
  color: #c45656;
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
}

.resource-access-remove:hover {
  background: #fef2f2;
}

.resource-access-empty {
  margin-top: 12px;
  color: #8a95a5;
  font-size: 13px;
}

@media (max-width: 1100px) {
  .library-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .library-actions {
    justify-content: flex-start;
    width: 100%;
  }

  .workbook-table-inner th,
  .workbook-table-inner td {
    padding-right: 12px;
  }

  .workbook-table-inner th + th,
  .workbook-table-inner td + td {
    padding-left: 18px;
  }

  .workbook-name-cell,
  .workbook-title-button {
    max-width: min(48vw, 420px);
  }
}

@media (max-width: 760px) {
  .workbook-library-page {
    padding: 12px;
  }

  .library-search {
    width: 100%;
  }

  .workbook-table-inner {
    min-width: 720px;
  }

  .workbook-name-cell,
  .workbook-title-button {
    max-width: 260px;
  }
}
</style>

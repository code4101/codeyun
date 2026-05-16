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
  fetchWorkbooks,
  saveAsWorkbook,
  updateWorkbook,
  type NoteSheetResourceRole,
  type WorkbookSummary,
} from '@/api/noteSheets'
import { useUserStore } from '@/store/userStore'
import NoteSheetAccessDialog from '../components/NoteSheetAccessDialog.vue'

type WorkbookFilter = 'all' | 'mine' | 'other'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const workbooks = ref<WorkbookSummary[]>([])
const searchText = ref('')
const workbookFilter = ref<WorkbookFilter>('all')

const accessDialogVisible = ref(false)
const accessDialogWorkbook = ref<WorkbookSummary | null>(null)

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

function openAccessDialog(workbook: WorkbookSummary) {
  if (!canManageWorkbook(workbook)) {
    ElMessage.warning('没有权限管理该工作簿')
    return
  }

  accessDialogWorkbook.value = workbook
  accessDialogVisible.value = true
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
                        <el-dropdown-item v-if="canManageWorkbook(workbook)" command="rename" :icon="Edit">
                          重命名
                        </el-dropdown-item>
                        <el-dropdown-item v-if="canManageWorkbook(workbook)" command="access" :icon="Share">
                          共享权限
                        </el-dropdown-item>
                        <el-dropdown-item command="template">
                          另存为模版
                        </el-dropdown-item>
                        <el-dropdown-item command="duplicate">
                          另存为副本
                        </el-dropdown-item>
                        <el-dropdown-item v-if="canManageWorkbook(workbook)" divided command="delete" :icon="Delete">
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

    <NoteSheetAccessDialog
      v-model="accessDialogVisible"
      resource-type="workbook"
      :resource-id="accessDialogWorkbook?.id ?? null"
      :title="accessDialogWorkbook?.title ?? ''"
      @saved="() => reloadWorkbooks()"
    />
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

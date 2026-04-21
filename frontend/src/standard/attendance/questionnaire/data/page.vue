<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type InputInstance } from 'element-plus'
import { Delete, Search } from '@element-plus/icons-vue'

import {
  deleteAttendanceWjxData,
  fetchAttendanceWjxData,
  updateAttendanceWjxData,
  type AttendanceWjxDataItem,
  type AttendanceWjxDataPage,
} from '@/api/attendance'
import { useUserStore } from '@/store/userStore'

type RowDraft = {
  process_note: string
}

const PAGE_SIZE_OPTIONS = [20, 50, 100]
const PROCESS_STATUS_OPTIONS = [
  { label: '全部', value: '__all__' },
  { label: '未处理', value: '__empty__' },
  { label: '已处理', value: '已处理' },
]

const loading = ref(false)
const savingRowId = ref<number | null>(null)
const deletingRowId = ref<number | null>(null)
const editingRowId = ref<number | null>(null)
const pageData = ref<AttendanceWjxDataPage | null>(null)
const userStore = useUserStore()
const drafts = reactive<Record<number, RowDraft>>({})
const processNoteInputRefs = reactive<Record<number, InputInstance | null>>({})

const filters = reactive({
  keyword: '',
  processStatus: '__all__',
  page: 1,
  pageSize: 20,
})

const rows = computed(() => pageData.value?.items ?? [])
const canDeleteRows = computed(() => userStore.isAdmin)

function buildQueryParams() {
  return {
    page: filters.page,
    page_size: filters.pageSize,
    keyword: filters.keyword.trim() || undefined,
    process_status: filters.processStatus === '__all__' ? undefined : filters.processStatus,
  }
}

function ensureDraft(item: AttendanceWjxDataItem) {
  if (!drafts[item.id]) {
    drafts[item.id] = {
      process_note: item.process_note || '',
    }
  }
  return drafts[item.id]
}

function resetDraft(item: AttendanceWjxDataItem) {
  drafts[item.id] = {
    process_note: item.process_note || '',
  }
}

function updateProcessNoteDraft(item: AttendanceWjxDataItem, value: string) {
  const nextDraft = ensureDraft(item)
  nextDraft.process_note = value
}

async function loadPageData() {
  loading.value = true
  try {
    const nextPage = await fetchAttendanceWjxData(buildQueryParams())
    editingRowId.value = null
    pageData.value = nextPage
    nextPage.items.forEach(resetDraft)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '加载问卷数据页失败')
  } finally {
    loading.value = false
  }
}

async function handleSearch() {
  filters.page = 1
  await loadPageData()
}

async function handlePageChange(page: number) {
  filters.page = page
  await loadPageData()
}

async function handlePageSizeChange(pageSize: number) {
  filters.pageSize = pageSize
  filters.page = 1
  await loadPageData()
}

async function saveRow(item: AttendanceWjxDataItem) {
  const draft = ensureDraft(item)
  const nextProcessNote = draft.process_note.trim()
  const nextProcessStatus = nextProcessNote ? '已处理' : ''

  if (nextProcessNote === (item.process_note || '') && nextProcessStatus === (item.process_status || '')) {
    return
  }

  savingRowId.value = item.id
  try {
    const updated = await updateAttendanceWjxData(item.id, {
      process_status: nextProcessStatus,
      process_note: nextProcessNote,
    })
    if (pageData.value) {
      pageData.value.items = pageData.value.items.map(row => (row.id === updated.id ? updated : row))
    }
    resetDraft(updated)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '保存处理状态失败')
  } finally {
    savingRowId.value = null
  }
}

function setProcessNoteInputRef(rowId: number, instance: InputInstance | null) {
  if (instance) {
    processNoteInputRefs[rowId] = instance
  } else {
    delete processNoteInputRefs[rowId]
  }
}

function isProcessNoteReadonly(item: AttendanceWjxDataItem) {
  return Boolean((item.process_note || '').trim()) && editingRowId.value !== item.id
}

async function enableProcessNoteEdit(item: AttendanceWjxDataItem) {
  if (savingRowId.value === item.id || deletingRowId.value === item.id) {
    return
  }
  if (!(item.process_note || '').trim()) {
    return
  }
  editingRowId.value = item.id
  await nextTick()
  processNoteInputRefs[item.id]?.focus?.()
}

async function finishProcessNoteEdit(item: AttendanceWjxDataItem) {
  await saveRow(item)
  if (editingRowId.value === item.id) {
    editingRowId.value = null
  }
}

async function deleteRow(item: AttendanceWjxDataItem) {
  try {
    await ElMessageBox.confirm(`确定删除序号 ${item.seq} 吗？`, '删除问卷数据', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  deletingRowId.value = item.id
  try {
    await deleteAttendanceWjxData(item.id)
    delete drafts[item.id]
    if ((pageData.value?.items.length || 0) <= 1 && filters.page > 1) {
      filters.page -= 1
    }
    await loadPageData()
    ElMessage.success(`已删除序号 ${item.seq}`)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '删除问卷数据失败')
  } finally {
    deletingRowId.value = null
  }
}

function getForegroundStyle(color?: string | null) {
  if (!color) {
    return undefined
  }
  return {
    color,
  }
}

onMounted(() => {
  void loadPageData()
})
</script>

<template>
  <div class="attendance-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <h1>问卷数据</h1>
      </div>
    </section>

    <section class="panel-card">
      <div class="filter-row">
        <el-input
          v-model="filters.keyword"
          placeholder="搜课程、学号、姓名、修正需求"
          clearable
          class="filter-keyword"
          @keyup.enter="handleSearch"
        />
        <el-select v-model="filters.processStatus" class="filter-status">
          <el-option
            v-for="option in PROCESS_STATUS_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button :icon="Search" @click="handleSearch">查询</el-button>
      </div>

      <el-table
        :data="rows"
        row-key="id"
        border
        stripe
        table-layout="auto"
        class="data-table"
        :empty-text="loading ? '正在加载...' : '暂无问卷数据'"
      >
        <el-table-column prop="seq" label="序号" width="88" />
        <el-table-column label="提交/课程">
          <template #default="{ row }">
            <div class="info-stack">
              <div :style="getForegroundStyle(row.foreground_colors?.submitted)">{{ row.submitted_at_text || '-' }}</div>
              <div :style="getForegroundStyle(row.foreground_colors?.course)">{{ row.course_name || '-' }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="学员">
          <template #default="{ row }">
            <div class="info-stack">
              <div :style="getForegroundStyle(row.foreground_colors?.student)">{{ row.student_id_text || '-' }}</div>
              <div :style="getForegroundStyle(row.foreground_colors?.student)" class="muted-text">
                {{ row.student_name || '-' }}
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="修正需求" min-width="260" class-name="multiline-column">
          <template #default="{ row }">
            <div class="multiline-text">{{ row.correction_request || '-' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="补充说明" min-width="220" class-name="multiline-column">
          <template #default="{ row }">
            <div class="multiline-text">{{ row.extra_note || '-' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="处理状态">
          <template #default="{ row }">
            <div class="process-cell" @dblclick="enableProcessNoteEdit(row)">
              <el-input
                :ref="instance => setProcessNoteInputRef(row.id, instance)"
                :model-value="drafts[row.id]?.process_note ?? row.process_note"
                :class="{ 'process-input-readonly': isProcessNoteReadonly(row) }"
                size="small"
                placeholder="请填写处理结果"
                :clearable="!isProcessNoteReadonly(row)"
                :disabled="savingRowId === row.id || deletingRowId === row.id"
                :readonly="isProcessNoteReadonly(row)"
                @update:model-value="value => updateProcessNoteDraft(row, String(value ?? ''))"
                @blur="() => finishProcessNoteEdit(row)"
                @keyup.enter="() => finishProcessNoteEdit(row)"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column v-if="canDeleteRows" label="操作" width="108" align="center">
          <template #default="{ row }">
            <el-button
              type="danger"
              plain
              size="small"
              :icon="Delete"
              :loading="deletingRowId === row.id"
              :disabled="savingRowId === row.id"
              @click="deleteRow(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-row">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :current-page="pageData?.page || filters.page"
          :page-size="pageData?.page_size || filters.pageSize"
          :page-sizes="PAGE_SIZE_OPTIONS"
          :total="pageData?.total || 0"
          @current-change="handlePageChange"
          @size-change="handlePageSizeChange"
        />
      </div>
    </section>
  </div>
</template>

<style scoped>
.attendance-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.hero-panel {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding: 28px 30px;
  border-radius: 24px;
  background:
    radial-gradient(circle at top left, rgba(255, 221, 147, 0.32), transparent 32%),
    linear-gradient(135deg, rgba(111, 64, 29, 0.95), rgba(31, 86, 93, 0.92));
  color: #fff7ed;
  box-shadow: 0 18px 42px rgba(53, 39, 25, 0.18);
}

.hero-copy h1 {
  margin: 0;
  font-size: 30px;
  line-height: 1.1;
}

.panel-card {
  padding: 22px 24px;
  border-radius: 22px;
  background: #fffaf2;
  border: 1px solid rgba(121, 93, 55, 0.14);
  box-shadow: 0 12px 28px rgba(68, 48, 26, 0.08);
}

.filter-row {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}

.filter-keyword {
  max-width: 420px;
}

.filter-status {
  width: 140px;
}

.data-table {
  width: 100%;
}

.info-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.muted-text {
  font-size: 12px;
}

:deep(.multiline-column .cell) {
  white-space: normal;
  line-height: 1.6;
}

.multiline-text {
  white-space: normal;
  word-break: break-word;
  line-height: 1.6;
}

.process-cell {
  width: 100%;
  min-width: 180px;
}

:deep(.process-input-readonly .el-input__wrapper),
:deep(.process-input-readonly .el-input__wrapper:hover),
:deep(.process-input-readonly.is-focus .el-input__wrapper) {
  background: transparent;
  box-shadow: none;
  padding-left: 0;
  padding-right: 0;
}

:deep(.process-input-readonly .el-input__wrapper) {
  pointer-events: none;
}

:deep(.process-input-readonly .el-input__inner) {
  color: inherit;
}

.pagination-row {
  margin-top: 18px;
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 1120px) {
  .filter-row {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-keyword,
  .filter-status {
    max-width: none;
    width: 100%;
  }
}

@media (max-width: 960px) {
  .process-cell {
    width: 100%;
  }

  .pagination-row {
    justify-content: flex-start;
  }
}
</style>

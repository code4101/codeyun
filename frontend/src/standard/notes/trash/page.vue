<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, RefreshLeft } from '@element-plus/icons-vue'

import { fetchDeletedNotes, useNoteStore, type NoteNode } from '@/api/notes'
import {
  fetchNoteSheetTrash,
  restoreNoteSheet,
  restoreWorkbook,
  type NoteSheetSummary,
  type WorkbookSummary,
} from '@/api/noteSheets'
import { useUserStore } from '@/store/userStore'

type TrashResourceKind = 'note' | 'workbook' | 'sheet'

interface TrashRow {
  key: string
  kind: TrashResourceKind
  id: number
  title: string
  deletedAt: number | null
  context: string
  raw: NoteNode | WorkbookSummary | NoteSheetSummary
}

const router = useRouter()
const noteStore = useNoteStore()
const userStore = useUserStore()

const loading = ref(false)
const notes = ref<NoteNode[]>([])
const workbooks = ref<WorkbookSummary[]>([])
const sheets = ref<NoteSheetSummary[]>([])
const restoringKey = ref('')

const rows = computed<TrashRow[]>(() => {
  const noteRows = notes.value.map((note) => ({
    key: `note:${note.id}`,
    kind: 'note' as const,
    id: Number(note.id),
    title: note.title || '未命名文档',
    deletedAt: note.deleted_at ?? null,
    context: note.note_form === 'document' ? '文档' : '节点',
    raw: note,
  }))
  const workbookRows = workbooks.value.map((workbook) => ({
    key: `workbook:${workbook.id}`,
    kind: 'workbook' as const,
    id: workbook.id,
    title: workbook.title || '未命名工作簿',
    deletedAt: normalizeApiTimestamp(workbook.deleted_at),
    context: `${workbook.sheet_count} 个工作表`,
    raw: workbook,
  }))
  const sheetRows = sheets.value.map((sheet) => ({
    key: `sheet:${sheet.id}`,
    kind: 'sheet' as const,
    id: sheet.id,
    title: sheet.title || '未命名表格',
    deletedAt: normalizeApiTimestamp(sheet.deleted_at),
    context: formatSheetContext(sheet),
    raw: sheet,
  }))
  return [...noteRows, ...workbookRows, ...sheetRows]
    .sort((left, right) => (right.deletedAt ?? 0) - (left.deletedAt ?? 0))
})

function normalizeApiTimestamp(value?: number | null) {
  if (!value) return null
  return value > 10_000_000_000 ? value : value * 1000
}

function formatDateTime(timestamp: number | null) {
  if (!timestamp) return '-'
  const date = new Date(timestamp)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:${minute}`
}

function formatKind(kind: TrashResourceKind) {
  if (kind === 'workbook') return '工作簿'
  if (kind === 'sheet') return '工作表'
  return '文档'
}

function formatSheetContext(sheet: NoteSheetSummary) {
  const workbookTitles = sheet.workbook_items.map((item) => item.title).filter(Boolean)
  return workbookTitles.length ? workbookTitles.join(' / ') : '独立工作表'
}

async function reloadTrash() {
  loading.value = true
  try {
    const [deletedNotes, sheetTrash] = await Promise.all([
      fetchDeletedNotes(),
      userStore.isAuthenticated
        ? fetchNoteSheetTrash()
        : Promise.resolve({ sheets: [], workbooks: [] }),
    ])
    notes.value = deletedNotes
    workbooks.value = sheetTrash.workbooks || []
    sheets.value = sheetTrash.sheets || []
  } catch (error) {
    console.warn('Failed to load note trash:', error)
    ElMessage.error('加载回收站失败')
  } finally {
    loading.value = false
  }
}

async function restoreRow(row: TrashRow) {
  restoringKey.value = row.key
  try {
    if (row.kind === 'note') {
      const restored = await noteStore.restoreNote(row.id)
      if (!restored) return
    } else if (row.kind === 'workbook') {
      await restoreWorkbook(row.id)
    } else {
      await restoreNoteSheet(row.id)
    }
    ElMessage.success('已恢复')
    await reloadTrash()
  } finally {
    restoringKey.value = ''
  }
}

onMounted(() => {
  void reloadTrash()
})
</script>

<template>
  <div class="notes-trash-page" v-loading="loading">
    <header class="trash-header">
      <div class="trash-heading">
        <h1>回收站</h1>
        <div class="trash-count">{{ rows.length }} 个资源</div>
      </div>
      <div class="trash-actions">
        <el-button :icon="Refresh" @click="reloadTrash">刷新</el-button>
        <el-button plain @click="router.back()">返回</el-button>
      </div>
    </header>

    <section class="trash-table" aria-label="回收站资源">
      <div v-if="rows.length" class="trash-table-scroll">
        <table class="trash-table-inner">
          <thead>
            <tr>
              <th scope="col">资源</th>
              <th scope="col">类型</th>
              <th scope="col">位置</th>
              <th scope="col">删除时间</th>
              <th scope="col" class="trash-actions-heading">操作</th>
              <th scope="col" class="trash-spacer-cell" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.key" class="trash-row">
              <td>
                <div class="trash-name-cell">
                  <span class="trash-subtitle">#{{ row.id }}</span>
                  <span class="trash-title" :title="row.title">{{ row.title }}</span>
                </div>
              </td>
              <td class="trash-kind">{{ formatKind(row.kind) }}</td>
              <td class="trash-context" :title="row.context">{{ row.context }}</td>
              <td class="trash-deleted-at">{{ formatDateTime(row.deletedAt) }}</td>
              <td class="trash-actions-cell">
                <el-button
                  size="small"
                  :icon="RefreshLeft"
                  :loading="restoringKey === row.key"
                  @click="restoreRow(row)"
                >
                  恢复
                </el-button>
              </td>
              <td class="trash-spacer-cell" aria-hidden="true"></td>
            </tr>
          </tbody>
        </table>
      </div>
      <el-empty v-else class="trash-empty" description="回收站为空" />
    </section>
  </div>
</template>

<style scoped>
.notes-trash-page {
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

.trash-header {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.trash-heading {
  display: grid;
  gap: 4px;
  min-width: 160px;
}

.trash-heading h1 {
  margin: 0;
  color: #172033;
  font-size: 22px;
  font-weight: 700;
  line-height: 30px;
}

.trash-count {
  color: #697386;
  font-size: 13px;
  line-height: 20px;
}

.trash-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.trash-table {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #dfe7f0;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.trash-table-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.trash-table-inner {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
}

.trash-table-inner th,
.trash-table-inner td {
  box-sizing: border-box;
  height: 44px;
  padding: 0 14px;
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.trash-table-inner th {
  height: 38px;
  border-bottom: 1px solid #e5ebf2;
  background: #f3f6fa;
  color: #5a6677;
  font-size: 12px;
  font-weight: 700;
}

.trash-table-inner td {
  border-bottom: 1px solid #eef2f6;
}

.trash-row:hover {
  background: #f8fbff;
}

.trash-name-cell {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: min(52vw, 560px);
}

.trash-subtitle {
  flex: 0 0 auto;
  min-width: 24px;
  color: #8a96a8;
  font-size: 12px;
  line-height: 22px;
}

.trash-title {
  flex: 1 1 auto;
  min-width: 0;
  color: #182235;
  font-size: 14px;
  font-weight: 700;
  line-height: 22px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.trash-kind,
.trash-context,
.trash-deleted-at {
  color: #4f5d70;
  font-size: 13px;
  line-height: 20px;
}

.trash-context {
  max-width: min(32vw, 360px);
  overflow: hidden;
  text-overflow: ellipsis;
}

.trash-actions-heading,
.trash-actions-cell {
  text-align: right;
}

.trash-spacer-cell {
  width: 100%;
  padding: 0 !important;
}

.trash-empty {
  flex: 1 1 auto;
  min-height: 220px;
}
</style>

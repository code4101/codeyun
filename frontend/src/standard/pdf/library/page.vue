<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'

import {
  fetchPdfDocuments,
  importPdfDocumentFromLocalPath,
  type PdfDocumentSummary,
  type PdfResourceRole,
} from '@/api/pdfDocuments'
import { useUserStore } from '@/store/userStore'

type PdfFilter = 'all' | 'mine' | 'other'

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const documents = ref<PdfDocumentSummary[]>([])
const searchText = ref('')
const pdfFilter = ref<PdfFilter>('all')

const currentUserId = computed(() => userStore.user?.id ?? null)
const normalizedSearchText = computed(() => searchText.value.trim().toLowerCase())

const filteredDocuments = computed(() => {
  const query = normalizedSearchText.value
  return documents.value.filter((document) => {
    const isMine = document.owner_user_id != null && document.owner_user_id === currentUserId.value
    if (pdfFilter.value === 'mine' && !isMine) {
      return false
    }
    if (pdfFilter.value === 'other' && isMine) {
      return false
    }
    if (!query) {
      return true
    }
    return document.title.toLowerCase().includes(query)
      || String(document.id).includes(query)
      || document.source_device_id.toLowerCase().includes(query)
      || document.source_absolute_path.toLowerCase().includes(query)
      || accessRoleLabel(document.access?.role).toLowerCase().includes(query)
  })
})

const filterOptions: Array<{ value: PdfFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'mine', label: '我创建的' },
  { value: 'other', label: '其他可访问' },
]

function accessRoleLabel(role?: PdfResourceRole | null) {
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

function formatDateTime(timestamp?: number | null) {
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

function formatFileSize(sizeBytes?: number | null) {
  if (!sizeBytes || sizeBytes <= 0) {
    return '-'
  }
  const units = ['B', 'KB', 'MB', 'GB']
  let value = sizeBytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  const precision = value >= 10 || unitIndex === 0 ? 0 : 1
  return `${value.toFixed(precision)} ${units[unitIndex]}`
}

function formatCurrentPage(document: PdfDocumentSummary) {
  const page = document.my_state?.current_page
  return page && page > 1 ? `第 ${page} 页` : '-'
}

function resolvePdfHref(pdfId: number) {
  return router.resolve({ path: `/pdf/${pdfId}` }).href
}

async function reloadDocuments() {
  loading.value = true
  try {
    documents.value = await fetchPdfDocuments()
  } catch (error) {
    console.warn('Failed to load PDF documents:', error)
    ElMessage.error('加载 PDF 文档失败')
  } finally {
    loading.value = false
  }
}

async function handleImportLocalPdf() {
  try {
    const { value } = await ElMessageBox.prompt('请输入本机 PDF 绝对路径', '导入本机 PDF', {
      inputValue: '',
      confirmButtonText: '导入',
      cancelButtonText: '取消',
      inputValidator: (inputValue) => inputValue.trim().toLowerCase().endsWith('.pdf') ? true : '请输入 PDF 文件路径',
    })
    await importPdfDocumentFromLocalPath({ absolute_path: value.trim() })
    await reloadDocuments()
    ElMessage.success('PDF 已导入')
  } catch (error) {
    if (error === 'cancel' || error === 'close') {
      return
    }
    console.warn('Failed to import PDF document:', error)
    ElMessage.error('导入 PDF 失败')
  }
}

async function initializeLibraryPage() {
  if (userStore.isAuthenticated && !userStore.user && !userStore.loading) {
    await userStore.fetchUserProfile()
  }
  await reloadDocuments()
}

onMounted(() => {
  void initializeLibraryPage()
})
</script>

<template>
  <div class="pdf-library-page" v-loading="loading">
    <header class="library-header">
      <div class="library-heading">
        <h1>PDF 阅读器</h1>
        <div class="library-count">{{ documents.length }} 个文档</div>
      </div>

      <div class="library-actions">
        <el-input
          v-model="searchText"
          class="library-search"
          :prefix-icon="Search"
          clearable
          placeholder="搜索 PDF"
        />
        <button
          v-for="option in filterOptions"
          :key="option.value"
          type="button"
          class="filter-button"
          :class="{ active: pdfFilter === option.value }"
          @click="pdfFilter = option.value"
        >
          {{ option.label }}
        </button>
        <el-button type="primary" :icon="Plus" @click="handleImportLocalPdf">导入本机 PDF</el-button>
      </div>
    </header>

    <section class="pdf-table" aria-label="PDF 文档库">
      <div v-if="filteredDocuments.length" class="pdf-table-scroll">
        <table class="pdf-table-inner">
          <thead>
            <tr>
              <th scope="col">文档</th>
              <th scope="col">权限</th>
              <th scope="col">上次阅读</th>
              <th scope="col">大小</th>
              <th scope="col">更新时间</th>
              <th scope="col" class="pdf-spacer-cell" aria-hidden="true"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="document in filteredDocuments"
              :key="document.id"
              class="pdf-row"
            >
              <td class="pdf-name-cell">
                <a
                  class="pdf-title-button"
                  :href="resolvePdfHref(document.id)"
                  target="_blank"
                  rel="noopener noreferrer"
                  :title="document.source_absolute_path || document.title"
                >
                  <span class="pdf-subtitle">#{{ document.id }}</span>
                  <span class="pdf-title">{{ document.title }}</span>
                </a>
              </td>
              <td class="pdf-role">{{ accessRoleLabel(document.access?.role) }}</td>
              <td class="pdf-current-page">{{ formatCurrentPage(document) }}</td>
              <td class="pdf-size">{{ formatFileSize(document.size_bytes) }}</td>
              <td class="pdf-updated">{{ formatDateTime(document.updated_at) }}</td>
              <td class="pdf-spacer-cell" aria-hidden="true"></td>
            </tr>
          </tbody>
        </table>
      </div>

      <el-empty
        v-else
        class="pdf-empty"
        :description="documents.length ? '没有匹配的 PDF' : '暂无 PDF 文档'"
      />
    </section>
  </div>
</template>

<style scoped>
.pdf-library-page {
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

.pdf-table {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #dfe7f0;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.pdf-table-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.pdf-table-inner {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
}

.pdf-table-inner th,
.pdf-table-inner td {
  box-sizing: border-box;
  padding: 0 14px;
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.pdf-table-inner th {
  height: 38px;
  border-bottom: 1px solid #e5ebf2;
  background: #f3f6fa;
  color: #5a6677;
  font-size: 12px;
  font-weight: 700;
}

.pdf-table-inner td {
  height: 48px;
  border-bottom: 1px solid #eef2f6;
}

.pdf-table-inner th + th,
.pdf-table-inner td + td {
  padding-left: 24px;
}

.pdf-row:hover {
  background: #f8fbff;
}

.pdf-name-cell {
  max-width: min(52vw, 560px);
}

.pdf-title-button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: min(52vw, 560px);
  color: inherit;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.pdf-title {
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

.pdf-title-button:hover .pdf-title {
  color: #2368d1;
}

.pdf-subtitle {
  flex: 0 0 auto;
  min-width: 24px;
  color: #8a96a8;
  font-size: 12px;
  line-height: 22px;
}

.pdf-role,
.pdf-current-page,
.pdf-size,
.pdf-updated {
  color: #4f5d70;
  font-size: 13px;
  line-height: 20px;
  white-space: nowrap;
}

.pdf-current-page {
  color: #172033;
  font-weight: 700;
}

.pdf-spacer-cell {
  width: 100%;
  padding: 0 !important;
}

.pdf-empty {
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

  .pdf-table-inner th,
  .pdf-table-inner td {
    padding-right: 12px;
  }

  .pdf-table-inner th + th,
  .pdf-table-inner td + td {
    padding-left: 18px;
  }

  .pdf-name-cell,
  .pdf-title-button {
    max-width: min(48vw, 440px);
  }
}

@media (max-width: 760px) {
  .pdf-library-page {
    padding: 12px;
  }

  .library-search {
    width: 100%;
  }

  .pdf-table-inner {
    min-width: 720px;
  }

  .pdf-name-cell,
  .pdf-title-button {
    max-width: 260px;
  }
}
</style>

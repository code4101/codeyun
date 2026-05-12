<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, FolderOpened, QuestionFilled, Refresh, Upload } from '@element-plus/icons-vue'

import {
  fetchLatestEastmoneyAssetSnapshot,
  importEastmoneyTradeDetailFromOcr,
  refreshEastmoneySheetWorkbook,
  syncEastmoneyTradeData,
  type EastmoneyAssetSnapshot,
  type EastmoneySheetWorkbook,
  type EastmoneySheetWorkbookSheet,
} from '@/api/eastmoney'
import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

const EASTMONEY_SHEET_TABS = [
  { key: 'operation-history', label: '操作明细', emptyText: '暂无操作流水' },
  { key: 'local-history', label: '成交明细', emptyText: '暂无成交数据' },
  { key: 'positions', label: '持仓', emptyText: '暂无持仓数据' },
  { key: 'sync-runs', label: '同步记录', emptyText: '暂无同步记录' },
] as const

const syncing = ref(false)
const pageError = ref('')
const activeTab = ref('operation-history')
const latestAssetSnapshot = ref<EastmoneyAssetSnapshot | null>(null)
const pasteImportEnabled = ref(false)
const ocrImporting = ref(false)
const sheetWorkbookLoading = ref(false)
const sheetWorkbook = ref<EastmoneySheetWorkbook | null>(null)
const sheetReloadToken = ref(0)

const accountLabel = computed(() => latestAssetSnapshot.value?.account_label || '')
const summaryItems = computed(() => {
  const summary = latestAssetSnapshot.value?.raw_json ?? {}
  return [
    ['证券市值', summary['证券市值']],
    ['资金余额', summary['资金余额']],
    ['持仓盈亏', summary['持仓盈亏']],
  ].filter(([, value]) => value)
    .map(([label, value]) => ({
      label,
      value: formatSmartMoney(value),
      negative: parseMoneyValue(value) < 0,
    }))
})
const lastUpdatedAtText = computed(() => {
  return formatTime(latestAssetSnapshot.value?.captured_at)
})
const workbookId = computed(() => sheetWorkbook.value?.workbook.id ?? null)
const sheetTabs = computed(() => EASTMONEY_SHEET_TABS.map((tab) => ({
  ...tab,
  sheet: getSheetItem(tab.key),
})))

function formatDate(value: Date) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function defaultSyncDateParams() {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - 100)
  return {
    start_date: formatDate(start),
    end_date: formatDate(end),
  }
}

function formatTime(value: number | null | undefined) {
  return value ? new Date(value * 1000).toLocaleString() : ''
}

function parseMoneyValue(value: unknown) {
  const text = String(value ?? '').trim()
  if (!text || text === '-') return Number.NaN
  const multiplier = text.includes('万') ? 10000 : 1
  const normalized = text.replace(/,/g, '').replace(/，/g, '').replace(/[^\d.+-]/g, '')
  if (!normalized || normalized === '-' || normalized === '+') return Number.NaN
  return Number(normalized) * multiplier
}

function formatSmartMoney(value: unknown) {
  const text = String(value ?? '').trim()
  const numberValue = parseMoneyValue(text)
  if (!Number.isFinite(numberValue)) return text
  return formatSmartMoneyNumber(numberValue)
}

function formatSmartMoneyNumber(value: number) {
  if (!Number.isFinite(value)) return ''
  const normalizedValue = Object.is(value, -0) ? 0 : value
  const sign = normalizedValue < 0 ? '-' : ''
  const absValue = Math.abs(normalizedValue)
  if (absValue < 100) return `${sign}${absValue.toFixed(2)}`
  if (absValue < 10000) return `${sign}${absValue.toFixed(0)}`
  return `${sign}${(absValue / 10000).toFixed(2)}万`
}

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '读取失败'
}

function applySheetWorkbook(payload: EastmoneySheetWorkbook | undefined) {
  if (!payload) return
  sheetWorkbook.value = payload
  sheetReloadToken.value += 1
}

async function refreshSheetWorkbook(options: { silent?: boolean } = {}) {
  if (!options.silent) {
    sheetWorkbookLoading.value = true
  }
  try {
    applySheetWorkbook(await refreshEastmoneySheetWorkbook())
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    if (!options.silent) {
      sheetWorkbookLoading.value = false
    }
  }
}

function getSheetItem(key: string): EastmoneySheetWorkbookSheet | null {
  return sheetWorkbook.value?.sheets.find((sheet) => sheet.key === key) ?? null
}

function getSheetWorkspaceKey(key: string) {
  const sheet = getSheetItem(key)
  return `${workbookId.value ?? 'none'}:${sheet?.sheet_id ?? 'none'}:${sheet?.updated_at ?? 0}:${sheetReloadToken.value}`
}

function openWorkbookFile() {
  if (!workbookId.value) return
  const activeSheet = getSheetItem(activeTab.value)
  const query = activeSheet ? `?sheet=${activeSheet.sheet_id}` : ''
  window.open(`/workbook/${workbookId.value}${query}`, '_blank', 'noopener')
}

async function loadLatestAssetSnapshot() {
  try {
    latestAssetSnapshot.value = (await fetchLatestEastmoneyAssetSnapshot()).item
  } catch {
    latestAssetSnapshot.value = null
  }
}

async function syncToDatabase() {
  syncing.value = true
  pageError.value = ''
  try {
    const run = await syncEastmoneyTradeData(defaultSyncDateParams())
    applySheetWorkbook(run.sheet_workbook)
    if (run.status === 'login_required') {
      ElMessage.warning('证券交易系统未登录，请先完成资金账号登录后再同步。')
    } else if (run.status === 'success') {
      ElMessage.success(`同步完成：新增 ${run.inserted_count} 条，更新 ${run.updated_count} 条`)
      activeTab.value = 'operation-history'
    }
    await Promise.all([
      run.sheet_workbook ? Promise.resolve() : refreshSheetWorkbook({ silent: true }),
      loadLatestAssetSnapshot(),
    ])
  } catch (error) {
    pageError.value = getErrorMessage(error)
    ElMessage.error(pageError.value)
  } finally {
    syncing.value = false
  }
}

function extractClipboardImage(event: ClipboardEvent): File | null {
  const items = Array.from(event.clipboardData?.items || [])
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      return item.getAsFile()
    }
  }
  return null
}

function toggleTradeDetailPasteImport() {
  pasteImportEnabled.value = !pasteImportEnabled.value
  if (pasteImportEnabled.value) {
    ElMessage.info('已准备导入东方财富手机交易明细，请直接粘贴截图。')
  }
}

async function importTradeDetailImage(image: File) {
  ocrImporting.value = true
  try {
    const result = await importEastmoneyTradeDetailFromOcr(image)
    const record = result.record
    const actionText = result.created ? '新增' : '更新'
    const recordName = record.security_code || record.security_name || '成交明细'
    ElMessage.success(`已${actionText} ${recordName} ${record.trade_date}，可继续粘贴`)
    activeTab.value = 'local-history'
    applySheetWorkbook(result.sheet_workbook)
    await Promise.all([
      result.sheet_workbook ? Promise.resolve() : refreshSheetWorkbook({ silent: true }),
      loadLatestAssetSnapshot(),
    ])
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    ocrImporting.value = false
  }
}

async function handleWindowPaste(event: ClipboardEvent) {
  if (!pasteImportEnabled.value || ocrImporting.value) return
  const image = extractClipboardImage(event)
  if (!image) return
  event.preventDefault()
  event.stopPropagation()
  await importTradeDetailImage(image)
}

onMounted(() => {
  window.addEventListener('paste', handleWindowPaste)
  void refreshSheetWorkbook()
  void loadLatestAssetSnapshot()
})

onBeforeUnmount(() => {
  window.removeEventListener('paste', handleWindowPaste)
})
</script>

<template>
  <div class="eastmoney-page">
    <header class="page-toolbar">
      <div class="title-line">
        <h1>东方财富</h1>
        <el-tooltip placement="bottom-start">
          <template #content>
            <div class="tooltip-content">
              页面默认只读本地库并刷新一份真实星云表格文件；只有点击更新到库时才会打开东方财富交易页。遇到交易登录或验证码时需要人工处理。
            </div>
          </template>
          <el-icon class="help-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-tag v-if="accountLabel" size="small" effect="plain">{{ accountLabel }}</el-tag>
      </div>
      <div class="toolbar-actions">
        <el-button
          :icon="FolderOpened"
          :disabled="!workbookId"
          size="small"
          @click="openWorkbookFile"
        >
          打开星云表格
        </el-button>
        <el-button
          :icon="Refresh"
          :loading="sheetWorkbookLoading"
          size="small"
          @click="() => refreshSheetWorkbook()"
        >
          刷新表格文件
        </el-button>
        <el-button
          :icon="Upload"
          :loading="ocrImporting"
          size="small"
          :type="pasteImportEnabled ? 'primary' : 'default'"
          @click="toggleTradeDetailPasteImport"
        >
          {{ ocrImporting ? '识别中...' : pasteImportEnabled ? '关闭截图导入' : '粘贴截图导入' }}
        </el-button>
        <el-button
          :icon="Download"
          :loading="syncing"
          size="small"
          type="primary"
          @click="syncToDatabase"
        >
          更新到库
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="pageError"
      class="page-alert"
      :title="pageError"
      type="error"
      :closable="false"
      show-icon
    />
    <section v-if="summaryItems.length" class="summary-strip">
      <div v-for="item in summaryItems" :key="item.label" class="summary-item">
        <span>{{ item.label }}</span>
        <strong :class="{ negative: item.negative }">{{ item.value }}</strong>
      </div>
      <div v-if="lastUpdatedAtText" class="summary-item muted">
        <span>资产更新时间</span>
        <strong>{{ lastUpdatedAtText }}</strong>
      </div>
    </section>

    <el-tabs v-model="activeTab" class="data-tabs" v-loading="sheetWorkbookLoading">
      <el-tab-pane
        v-for="tab in sheetTabs"
        :key="tab.key"
        :label="tab.sheet ? `${tab.label} ${tab.sheet.row_count}条` : tab.label"
        :name="tab.key"
      >
        <NoteSheetWorkspace
          v-if="workbookId && tab.sheet"
          class="eastmoney-sheet-workspace"
          :key="getSheetWorkspaceKey(tab.key)"
          :workbook-id="workbookId"
          :sheet-id="tab.sheet.sheet_id"
          default-height-mode="content"
          :empty-text="tab.emptyText"
        />
        <el-empty
          v-else
          :description="sheetWorkbookLoading ? '正在刷新表格文件' : tab.emptyText"
        />
      </el-tab-pane>
    </el-tabs>

  </div>
</template>

<style scoped>
.eastmoney-page {
  display: flex;
  height: 100%;
  min-height: 100%;
  min-width: 0;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px 20px;
  color: #1f2937;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 10px;
}

.title-line {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.title-line h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  letter-spacing: 0;
}

.help-icon {
  color: #64748b;
  cursor: help;
}

.tooltip-content {
  max-width: 280px;
  line-height: 1.5;
}

.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.page-alert {
  flex: 0 0 auto;
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  align-items: flex-start;
  border-bottom: 1px solid #eef2f7;
  padding-bottom: 10px;
}

.summary-item {
  display: grid;
  grid-template-columns: auto;
  gap: 2px;
  min-width: 92px;
}

.summary-item span {
  color: #64748b;
  font-size: 12px;
}

.summary-item strong {
  color: #111827;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0;
}

.summary-item strong.negative {
  color: #15803d;
}

.summary-item.muted strong {
  color: #475569;
  font-size: 13px;
  font-weight: 500;
}

.data-tabs {
  min-width: 0;
}

.data-tabs :deep(.el-tabs__content) {
  min-height: 0;
  overflow: visible;
}

.data-tabs :deep(.el-tab-pane) {
  min-height: 0;
}

@media (max-width: 760px) {
  .eastmoney-page {
    padding: 10px;
  }

  .page-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .toolbar-actions {
    justify-content: flex-start;
    width: 100%;
  }

}
</style>

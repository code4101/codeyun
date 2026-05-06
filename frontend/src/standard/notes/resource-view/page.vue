<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchWorkbook, type WorkbookDetail } from '@/api/noteSheets'
import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

const APP_TITLE = 'CodeYun'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const workbook = ref<WorkbookDetail | null>(null)
const activeSheetId = ref<number | null>(null)
const standaloneSheetTitle = ref('')
const errorText = ref('')

const isWorkbookMode = computed(() => String(route.name ?? '') === 'PublicWorkbookResource')
const workbookId = computed(() => normalizePositiveInt(route.params.workbookId))
const sheetId = computed(() => normalizePositiveInt(route.params.sheetId))
const querySheetId = computed(() => normalizePositiveInt(route.query.sheet))
const activeSheet = computed(() => (
  workbook.value?.sheets.find((sheet) => sheet.id === activeSheetId.value) ?? null
))
const pageDocumentTitle = computed(() => {
  if (isWorkbookMode.value) {
    const workbookTitle = String(workbook.value?.title || '').trim()
    const sheetTitle = String(activeSheet.value?.title || '').trim()
    const segments = [sheetTitle, workbookTitle].filter(Boolean)
    return segments.length ? `${segments.join(' - ')} - ${APP_TITLE}` : APP_TITLE
  }

  const sheetTitle = standaloneSheetTitle.value.trim()
  return sheetTitle ? `${sheetTitle} - ${APP_TITLE}` : APP_TITLE
})

function normalizePositiveInt(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value
  const numeric = Number(raw)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function resolveSheetId() {
  const sheets = workbook.value?.sheets ?? []
  const validIds = new Set(sheets.map((sheet) => sheet.id))
  return [querySheetId.value, activeSheetId.value, sheets[0]?.id ?? null]
    .find((id) => id != null && validIds.has(id)) ?? null
}

async function loadWorkbookResource() {
  if (!isWorkbookMode.value) {
    return
  }
  if (workbookId.value == null) {
    errorText.value = '工作簿地址无效'
    workbook.value = null
    activeSheetId.value = null
    return
  }

  loading.value = true
  errorText.value = ''
  try {
    const detail = await fetchWorkbook(workbookId.value)
    if (!detail) {
      errorText.value = '工作簿不存在或不可访问'
      workbook.value = null
      activeSheetId.value = null
      return
    }
    workbook.value = detail
    activeSheetId.value = resolveSheetId()
    if (activeSheetId.value != null && activeSheetId.value !== querySheetId.value) {
      void router.replace({
        path: `/workbook/${detail.id}`,
        query: { ...route.query, sheet: String(activeSheetId.value) },
      })
    }
  } catch (error) {
    console.warn('Failed to load public workbook resource:', error)
    errorText.value = '没有权限访问该工作簿'
    workbook.value = null
    activeSheetId.value = null
  } finally {
    loading.value = false
  }
}

function selectSheet(nextSheetId: number) {
  if (!workbook.value || nextSheetId === activeSheetId.value) {
    return
  }
  activeSheetId.value = nextSheetId
  void router.push({
    path: `/workbook/${workbook.value.id}`,
    query: { ...route.query, sheet: String(nextSheetId) },
  })
}

function handleSheetMissing() {
  errorText.value = '工作表不存在或不可访问'
}

function handleSheetSync(payload: { id: number; title: string; version: number; updatedAt: number }) {
  if (!isWorkbookMode.value) {
    standaloneSheetTitle.value = payload.title || ''
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
    }
  },
)

watch(
  () => route.fullPath,
  () => {
    document.title = pageDocumentTitle.value
  },
)

watch(
  [workbookId, querySheetId, isWorkbookMode],
  () => {
    void loadWorkbookResource()
  },
)

onMounted(() => {
  if (isWorkbookMode.value) {
    void loadWorkbookResource()
  } else if (sheetId.value == null) {
    errorText.value = '工作表地址无效'
  }
})
</script>

<template>
  <div class="sheet-resource-page" v-loading="loading">
    <template v-if="isWorkbookMode">
      <div v-if="workbook" class="resource-tabs-bar">
        <div class="resource-workbook-title" :title="workbook.title">{{ workbook.title }}</div>
        <button
          v-for="sheet in workbook.sheets"
          :key="sheet.id"
          type="button"
          class="resource-sheet-tab"
          :class="{ active: sheet.id === activeSheetId }"
          @click="selectSheet(sheet.id)"
        >
          {{ sheet.title }}
        </button>
      </div>

      <NoteSheetWorkspace
        v-if="activeSheetId"
        class="resource-sheet-workspace"
        :key="`${workbookId}:${activeSheetId}`"
        :workbook-id="workbookId"
        :sheet-id="activeSheetId"
        :access-capabilities="activeSheet?.access?.capabilities ?? null"
        :show-title-input="false"
        empty-text="请选择工作表"
        @missing="handleSheetMissing"
        @sheet-sync="handleSheetSync"
      />
      <el-empty v-else-if="workbook" :description="errorText || '没有可访问的工作表'" />
    </template>

    <template v-else>
      <NoteSheetWorkspace
        v-if="sheetId"
        class="resource-sheet-workspace"
        :key="`sheet:${sheetId}`"
        :sheet-id="sheetId"
        :show-title-input="false"
        empty-text="工作表不存在或不可访问"
        @missing="handleSheetMissing"
        @sheet-sync="handleSheetSync"
      />
      <el-empty v-else :description="errorText || '工作表地址无效'" />
    </template>

    <el-empty v-if="errorText && !loading && isWorkbookMode && !workbook" :description="errorText" />
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

.resource-sheet-tab:hover {
  background: #f1e7d9;
  color: #5c4932;
}

.resource-sheet-tab.active {
  background: #fff;
  color: #2f2414;
  box-shadow: inset 0 3px 0 #5b8def;
}

.resource-sheet-workspace {
  flex: 1 1 auto;
  min-height: 0;
}
</style>

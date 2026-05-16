<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

function normalizePositiveInt(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function normalizeWorkspaceViewQuery(value: unknown): 'lookup' | 'sheet' | null {
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

function redirectToWorkbookWindow() {
  const workbookId = normalizePositiveInt(route.params.workbookId)
  const sheetId = normalizePositiveInt(route.query.sheet)
  const workspaceView = normalizeWorkspaceViewQuery(route.query.view ?? route.query.mode ?? route.query.sheetView)

  if (workbookId == null) {
    void router.replace('/notes/sheets')
    return
  }

  const nextQuery: Record<string, string> = {}
  if (sheetId != null) {
    nextQuery.sheet = String(sheetId)
  }
  if (workspaceView) {
    nextQuery.view = workspaceView
  }

  void router.replace({
    path: `/workbook/${workbookId}`,
    query: nextQuery,
  })
}

watch(
  [() => route.params.workbookId, () => route.query.sheet, () => route.query.view, () => route.query.mode, () => route.query.sheetView],
  () => {
    redirectToWorkbookWindow()
  },
)

onMounted(() => {
  redirectToWorkbookWindow()
})
</script>

<template>
  <div class="note-workbook-redirect" />
</template>

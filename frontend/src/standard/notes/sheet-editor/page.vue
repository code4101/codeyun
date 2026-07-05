<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

const NoteSheetWorkspace = defineAsyncComponent(() => import('../components/NoteSheetWorkspace.vue'))

const route = useRoute()
const router = useRouter()

const sheetId = computed(() => {
  const numeric = Number(route.params.sheetId ?? '')
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
})

const routeWorkspaceView = computed(() => normalizeWorkspaceViewQuery(route.query.view ?? route.query.mode ?? route.query.sheetView))

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

function handleMissing() {
  ElMessage.error('表格不存在或已删除')
  void router.replace('/notes/sheets')
}
</script>

<template>
  <NoteSheetWorkspace
    :sheet-id="sheetId"
    :initial-workspace-view="routeWorkspaceView"
    default-height-mode="fill"
    show-back-button
    back-to="/notes/sheets"
    back-label="返回星云表格"
    @missing="handleMissing"
  />
</template>

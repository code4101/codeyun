<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

function normalizePositiveInt(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function redirectToWorkbookWindow() {
  const workbookId = normalizePositiveInt(route.params.workbookId)
  const sheetId = normalizePositiveInt(route.query.sheet)

  if (workbookId == null) {
    void router.replace('/notes/sheets')
    return
  }

  const nextQuery: Record<string, string> = {}
  if (sheetId != null) {
    nextQuery.sheet = String(sheetId)
  }

  void router.replace({
    path: `/workbook/${workbookId}`,
    query: nextQuery,
  })
}

watch(
  [() => route.params.workbookId, () => route.query.sheet],
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

<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

function normalizePositiveInt(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
}

function redirectToManager() {
  const workbookId = normalizePositiveInt(route.params.workbookId)
  const sheetId = normalizePositiveInt(route.query.sheet)

  const nextQuery: Record<string, string> = {}
  if (workbookId != null) {
    nextQuery.workbook = String(workbookId)
  }
  if (sheetId != null) {
    nextQuery.sheet = String(sheetId)
  }

  void router.replace({
    path: '/notes/sheets',
    query: nextQuery,
  })
}

watch(
  [() => route.params.workbookId, () => route.query.sheet],
  () => {
    redirectToManager()
  },
)

onMounted(() => {
  redirectToManager()
})
</script>

<template>
  <div class="note-workbook-redirect" />
</template>

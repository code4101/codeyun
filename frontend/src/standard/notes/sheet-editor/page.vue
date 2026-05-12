<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

const route = useRoute()
const router = useRouter()

const sheetId = computed(() => {
  const numeric = Number(route.params.sheetId ?? '')
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null
})

function handleMissing() {
  ElMessage.error('表格不存在或已删除')
  void router.replace('/notes/sheets')
}
</script>

<template>
  <NoteSheetWorkspace
    :sheet-id="sheetId"
    default-height-mode="fill"
    show-back-button
    back-to="/notes/sheets"
    back-label="返回星云表格"
    @missing="handleMissing"
  />
</template>

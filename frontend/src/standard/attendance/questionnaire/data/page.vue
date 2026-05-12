<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { fetchAttendanceWjxDataSheetLocation } from '@/api/attendance'

const router = useRouter()

onMounted(async () => {
  try {
    const location = await fetchAttendanceWjxDataSheetLocation()
    await router.replace({
      path: `/workbook/${location.workbook_id}`,
      query: {
        sheet: String(location.sheet_id),
      },
    })
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '打开问卷数据表失败')
  }
})
</script>

<template>
  <div class="attendance-wjx-data-redirect" />
</template>

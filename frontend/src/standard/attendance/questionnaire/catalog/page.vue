<script setup lang="ts">
import AttendanceFeedbackFormCard from '@/components/attendance/AttendanceFeedbackFormCard.vue'
</script>

<template>
  <div class="catalog-page">
    <AttendanceFeedbackFormCard mode="workspace" />
  </div>
</template>

<style scoped>
.catalog-page {
  display: flex;
  justify-content: center;
  padding: 10px 0 28px;
}
</style>

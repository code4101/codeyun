<script setup lang="ts">
import AttendanceFeedbackFormCard from '@/components/attendance/AttendanceFeedbackFormCard.vue'
</script>

<template>
  <div class="feedback-page">
    <AttendanceFeedbackFormCard mode="workspace" />
  </div>
</template>

<style scoped>
.feedback-page {
  display: flex;
  justify-content: center;
  padding: 10px 0 28px;
}
</style>

<script setup lang="ts">
import AttendanceFeedbackFormCard from '@/components/attendance/AttendanceFeedbackFormCard.vue'
</script>

<template>
  <div class="collect-page">
    <AttendanceFeedbackFormCard mode="public" />
  </div>
</template>

<style scoped>
.collect-page {
  display: flex;
  justify-content: center;
  padding: 24px 18px 32px;
}

@media (max-width: 768px) {
  .collect-page {
    padding: 18px 12px 24px;
  }
}
</style>

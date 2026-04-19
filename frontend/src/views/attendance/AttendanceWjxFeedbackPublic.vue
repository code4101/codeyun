<script setup lang="ts">
import AttendanceFeedbackFormCard from '@/components/attendance/AttendanceFeedbackFormCard.vue'
</script>

<template>
  <div class="public-feedback-page">
    <div class="background-orb orb-a" />
    <div class="background-orb orb-b" />
    <div class="background-orb orb-c" />
    <AttendanceFeedbackFormCard mode="public" />
  </div>
</template>

<style scoped>
.public-feedback-page {
  min-height: 100vh;
  padding: 32px 18px;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at top left, rgba(173, 220, 255, 0.55), transparent 28%),
    radial-gradient(circle at 90% 18%, rgba(255, 226, 186, 0.5), transparent 22%),
    linear-gradient(180deg, #eef6ff 0%, #f8fbff 42%, #f7f0e4 100%);
}

.background-orb {
  position: absolute;
  border-radius: 999px;
  filter: blur(10px);
  opacity: 0.7;
  pointer-events: none;
}

.orb-a {
  width: 280px;
  height: 280px;
  left: -40px;
  top: 120px;
  background: rgba(115, 196, 255, 0.18);
}

.orb-b {
  width: 260px;
  height: 260px;
  right: -70px;
  top: 60px;
  background: rgba(255, 208, 145, 0.18);
}

.orb-c {
  width: 340px;
  height: 340px;
  right: 14%;
  bottom: -120px;
  background: rgba(140, 213, 197, 0.18);
}
</style>

<script setup lang="ts">
defineProps<{
  eyebrow: string
  title: string
  description: string
}>()
</script>

<template>
  <div class="attendance-structure-page">
    <section class="hero-card">
      <div class="eyebrow">{{ eyebrow }}</div>
      <h1>{{ title }}</h1>
      <p>{{ description }}</p>
    </section>
  </div>
</template>

<style scoped>
.attendance-structure-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.hero-card {
  padding: 24px 28px;
  border: 1px solid rgba(184, 149, 92, 0.2);
  border-radius: 18px;
  background:
    radial-gradient(circle at top right, rgba(255, 222, 160, 0.28), transparent 30%),
    linear-gradient(180deg, rgba(255, 251, 244, 0.98), rgba(252, 246, 236, 0.96));
  box-shadow: 0 16px 36px rgba(117, 86, 40, 0.08);
}

.eyebrow {
  margin-bottom: 10px;
  font-size: 12px;
  letter-spacing: 0.08em;
  color: #9b7b46;
}

h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.2;
  color: #3d2a10;
}

p {
  margin: 10px 0 0;
  max-width: 720px;
  font-size: 14px;
  line-height: 1.7;
  color: #6f5730;
}

@media (max-width: 720px) {
  .hero-card {
    padding: 18px 20px;
    border-radius: 14px;
  }

  h1 {
    font-size: 22px;
  }
}
</style>

<script setup lang="ts">
defineProps<{
  title: string;
  description?: string;
}>();
</script>

<template>
  <div class="doc-page">
    <div class="doc-header">
      <h1>{{ title }}</h1>
      <p v-if="description" class="description">{{ description }}</p>
    </div>
    <el-divider />
    <div class="doc-content">
      <slot></slot>
    </div>
  </div>
</template>

<style scoped>
.doc-page {
  padding: 20px;
  width: 100%; /* Fill width */
  box-sizing: border-box; /* Include padding in width */
}
.doc-header h1 {
  font-size: 24px;
  margin-bottom: 10px;
  margin-top: 0;
}
.description {
  color: #666;
  font-size: 14px;
}
.doc-content {
  margin-top: 20px;
}
</style>

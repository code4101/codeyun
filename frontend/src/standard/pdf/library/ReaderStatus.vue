<script setup lang="ts">
defineProps<{ message: string; error?: boolean; retry?: boolean }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="reader-status" :class="{ 'is-error': error }" :role="error ? 'alert' : 'status'">
    <span>{{ message }}</span>
    <button v-if="retry" type="button" @click="$emit('retry')">重试</button>
  </div>
</template>

<style scoped>
.reader-status { display: flex; flex: 1; min-height: 100px; align-items: center; justify-content: center; flex-direction: column; gap: 12px; padding: 16px; color: var(--reader-muted, #657286); font-size: 13px; text-align: center; }
.is-error { color: var(--el-color-danger, #b34b4b); }
button { padding: 6px 12px; border: 1px solid var(--reader-border, #ddd); border-radius: 4px; background: var(--reader-content, #fff); color: var(--reader-link, #2368d1); cursor: pointer; }
</style>

<script setup lang="ts">
defineProps<{
  visible: boolean
  loading?: boolean
  disabled?: boolean
}>()

defineEmits<{
  collect: []
}>()
</script>

<template>
  <el-button
    v-if="visible"
    type="primary"
    plain
    size="small"
    :loading="loading"
    :disabled="disabled"
    @click="$emit('collect')"
  >从游戏更新</el-button>
</template>

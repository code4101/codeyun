<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  user?: { id: number; username: string; nickname?: string; avatar_url?: string | null } | null
  size?: number
}>(), { size: 32 })
const failed = ref(false)
watch(() => props.user?.avatar_url, () => { failed.value = false })
const colors = ['#4279ca', '#6d63c2', '#26877e', '#b36b43', '#ad5881', '#527caa']
const background = computed(() => colors[(props.user?.id ?? 0) % colors.length])
const initials = computed(() => {
  const name = (props.user?.nickname || props.user?.username || '?').trim()
  return Array.from(name)[0]?.toLocaleUpperCase() || '?'
})
</script>

<template>
  <span class="user-avatar" :style="{ width: `${size}px`, height: `${size}px`, fontSize: `${size * .44}px`, background }" role="img" aria-label="用户头像">
    <img v-if="user?.avatar_url && !failed" :src="user.avatar_url" alt="" @error="failed = true" />
    <span v-else>{{ initials }}</span>
  </span>
</template>

<style scoped>
.user-avatar { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; overflow: hidden; border-radius: 24%; color: #fff; font-weight: 600; line-height: 1; user-select: none; }
.user-avatar img { width: 100%; height: 100%; object-fit: cover; }
</style>

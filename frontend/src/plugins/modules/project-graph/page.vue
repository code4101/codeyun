<script setup lang="ts">
import { useUserStore } from '@/store/userStore'
import GraphWorkspace from './GraphWorkspace.vue'
const user = useUserStore()
</script>
<template>
  <GraphWorkspace v-if="user.user" :key="user.user.id" />
  <div v-else>请登录后打开自己的绘图空间。</div>
</template>

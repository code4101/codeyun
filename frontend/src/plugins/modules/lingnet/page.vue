<script setup lang="ts">
import LingnetPanel from '@lingnet/LingnetPanel.vue'
import api from '@/api'

// 灵网的 SSE 用 fetch 直连，登录令牌与 axios 拦截器取同一处。
function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}
</script>

<template>
  <LingnetPanel :http="api" :auth-headers="authHeaders" api-root="/api" />
</template>

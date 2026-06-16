<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

onMounted(() => {
  void router.replace({
    path: '/notes/eastmoney/trade',
    query: route.query,
    hash: route.hash,
  })
})
</script>

<template>
  <div class="eastmoney-redirect-page">
    正在打开股票操作建议...
  </div>
</template>

<style scoped>
.eastmoney-redirect-page {
  min-height: 100%;
  display: grid;
  place-items: center;
  color: #64748b;
  font-size: 14px;
}
</style>

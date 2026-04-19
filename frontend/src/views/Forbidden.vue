<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { useUserStore } from '@/store/userStore'

const router = useRouter()
const userStore = useUserStore()

const secondaryActionLabel = computed(() => (
  userStore.isAuthenticated ? '回到首页' : '前往登录'
))

const handleSecondaryAction = () => {
  if (userStore.isAuthenticated) {
    router.push({ name: 'Home' })
    return
  }
  router.push({ name: 'Login' })
}
</script>

<template>
  <div class="forbidden-page">
    <div class="forbidden-card">
      <div class="status-code">403</div>
      <h1 class="status-title">当前账号无权访问该功能</h1>
      <p class="status-description">
        请登录带有对应功能权限的账号使用。
      </p>
      <div class="action-row">
        <el-button type="primary" @click="router.back()">返回上一页</el-button>
        <el-button plain @click="handleSecondaryAction">{{ secondaryActionLabel }}</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.forbidden-page {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 20px;
  box-sizing: border-box;
  background: linear-gradient(180deg, #f8fbff 0%, #f4f6fb 100%);
}

.forbidden-card {
  width: min(520px, 100%);
  padding: 32px;
  border: 1px solid #e4e7ed;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 20px 40px rgba(31, 45, 61, 0.08);
}

.status-code {
  font-size: 44px;
  line-height: 1;
  font-weight: 700;
  color: #409eff;
}

.status-title {
  margin: 16px 0 0;
  font-size: 24px;
  line-height: 1.3;
  color: #303133;
}

.status-description {
  margin: 12px 0 0;
  font-size: 14px;
  line-height: 1.8;
  color: #606266;
}

.action-row {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}

@media (max-width: 640px) {
  .forbidden-card {
    padding: 24px;
    border-radius: 14px;
  }

  .action-row {
    flex-direction: column;
  }
}
</style>

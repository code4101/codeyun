<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'dayjs/locale/zh-cn'
import { useFeatureAccessStore } from '@/store/featureAccessStore'

const route = useRoute()
const featureAccessStore = useFeatureAccessStore()
const routeReady = computed(() => route.matched.length > 0)

const loadingPhaseText = computed(() => {
  if (!routeReady.value) {
    return '正在匹配当前页面路由。'
  }
  if (featureAccessStore.loading) {
    return '正在请求功能权限上下文。'
  }
  if (featureAccessStore.error) {
    return '权限上下文请求失败，正在尝试进入页面。'
  }
  return '正在准备页面组件。'
})

const loadingDetailText = computed(() => {
  if (featureAccessStore.error) {
    return featureAccessStore.error
  }
  if (routeReady.value) {
    return `路由已匹配：${route.fullPath}`
  }
  return '等待 vue-router 返回首个匹配结果。'
})

onMounted(() => {
  window.dispatchEvent(new Event('codeyun:app-mounted'))
})
</script>

<template>
  <el-config-provider :locale="zhCn">
    <router-view v-if="routeReady" v-slot="{ Component }">
      <suspense>
        <component :is="Component" />
        <template #fallback>
          <div class="app-route-loading" role="status" aria-live="polite">
            <div class="app-route-loading__mark" aria-hidden="true"></div>
            <div>
              <p class="app-route-loading__title">CodeYun 正在连接</p>
              <p class="app-route-loading__text">正在加载页面资源。</p>
              <div class="app-route-loading__progress" aria-hidden="true"></div>
              <p class="app-route-loading__meta">{{ loadingDetailText }}</p>
            </div>
          </div>
        </template>
      </suspense>
    </router-view>
    <div v-else class="app-route-loading" role="status" aria-live="polite">
      <div class="app-route-loading__mark" aria-hidden="true"></div>
      <div>
        <p class="app-route-loading__title">CodeYun 正在连接</p>
        <p class="app-route-loading__text">{{ loadingPhaseText }}</p>
        <div class="app-route-loading__progress" aria-hidden="true"></div>
        <p class="app-route-loading__meta">{{ loadingDetailText }}</p>
      </div>
    </div>
  </el-config-provider>
</template>

<style>
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.app-route-loading {
  min-height: 100dvh;
  display: grid;
  grid-template-columns: auto minmax(0, max-content);
  justify-content: center;
  align-content: center;
  gap: 12px;
  padding: 24px;
  box-sizing: border-box;
  color: #4b5563;
  background: #ffffff;
}

.app-route-loading__mark {
  width: 28px;
  height: 28px;
  border: 2px solid #d7dee8;
  border-top-color: #409eff;
  border-radius: 999px;
  animation: app-route-loading-spin 0.9s linear infinite;
}

.app-route-loading__title {
  margin: 0;
  color: #1f2937;
  font-size: 16px;
  font-weight: 650;
  line-height: 1.35;
}

.app-route-loading__text {
  margin: 3px 0 0;
  font-size: 13px;
  line-height: 1.45;
}

.app-route-loading__progress {
  width: min(260px, 66vw);
  height: 3px;
  margin-top: 12px;
  overflow: hidden;
  border-radius: 999px;
  background: #e5eaf2;
}

.app-route-loading__progress::before {
  content: "";
  display: block;
  width: 44%;
  height: 100%;
  border-radius: inherit;
  background: #409eff;
  animation: app-route-loading-progress 1.3s ease-in-out infinite;
}

.app-route-loading__meta {
  margin: 8px 0 0;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.45;
}

@keyframes app-route-loading-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes app-route-loading-progress {
  0% {
    transform: translateX(-110%);
  }

  100% {
    transform: translateX(230%);
  }
}
</style>

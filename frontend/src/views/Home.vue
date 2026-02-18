<script setup lang="ts">
import { ref, onMounted } from 'vue';
import DocPage from '@/components/DocPage.vue';
import api from '@/api';

const systemInfo = ref<any>(null);

onMounted(async () => {
  try {
    const res = await api.get('/fs/info');
    systemInfo.value = res.data;
  } catch (error) {
    console.error('Failed to fetch system info:', error);
  }
});
</script>

<template>
  <DocPage title="欢迎使用 CodeYun" description="基于 Vue 3 和 FastAPI 构建的个人工具箱">
    <el-card class="box-card">
      <template #header>
        <div class="card-header">
          <span>系统状态</span>
        </div>
      </template>
      <div v-if="systemInfo">
        <p><strong>平台:</strong> {{ systemInfo.platform }}</p>
        <p><strong>Python 版本:</strong> {{ systemInfo.python_version }}</p>
        <p><strong>后端工作目录:</strong> {{ systemInfo.cwd }}</p>
      </div>
      <div v-else>
        <el-skeleton :rows="3" animated />
        <p style="color: red; margin-top: 10px;">正在连接后端...</p>
      </div>
    </el-card>
  </DocPage>
</template>

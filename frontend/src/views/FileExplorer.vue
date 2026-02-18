<script setup lang="ts">
import { ref } from 'vue';
import DocPage from '@/components/DocPage.vue';
import api from '@/api';

const path = ref('D:\\');
const files = ref<any[]>([]);
const loading = ref(false);

const listDir = async () => {
  loading.value = true;
  try {
    const res = await api.post('/fs/list_dir', { path: path.value });
    files.value = res.data.items;
  } catch (error) {
    console.error(error);
  } finally {
    loading.value = false;
  }
};
</script>

<template>
  <DocPage title="文件浏览器" description="浏览本地文件系统">
    <el-input v-model="path" placeholder="输入路径" style="margin-bottom: 20px;">
      <template #append>
        <el-button @click="listDir" :loading="loading">跳转</el-button>
      </template>
    </el-input>

    <el-table :data="files" style="width: 100%" v-loading="loading">
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="is_dir" label="类型" width="100">
        <template #default="scope">
          <el-tag v-if="scope.row.is_dir">目录</el-tag>
          <el-tag v-else type="info">文件</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="path" label="完整路径" />
    </el-table>
  </DocPage>
</template>

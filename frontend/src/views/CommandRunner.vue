<script setup lang="ts">
import { ref } from 'vue';
import DocPage from '@/components/DocPage.vue';
import api from '@/api';

const command = ref('');
const cwd = ref('');
const output = ref('');
const error = ref('');
const loading = ref(false);

const runCommand = async () => {
  loading.value = true;
  output.value = '';
  error.value = '';
  try {
    const res = await api.post('/pyxllib/exec_cmd', { 
      command: command.value,
      cwd: cwd.value || undefined
    });
    output.value = res.data.stdout;
    if (res.data.stderr) {
      error.value = res.data.stderr;
    }
  } catch (err: any) {
    error.value = err.message || 'Error executing command';
  } finally {
    loading.value = false;
  }
};
</script>

<template>
  <DocPage title="命令运行器" description="直接执行系统命令">
    <el-form label-position="top">
      <el-form-item label="工作目录 (可选)">
        <el-input v-model="cwd" placeholder="例如 D:\Projects" />
      </el-form-item>
      <el-form-item label="命令">
        <el-input 
          v-model="command" 
          type="textarea" 
          :rows="3" 
          placeholder="例如 dir /w"
          @keydown.ctrl.enter="runCommand"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="runCommand" :loading="loading">运行 (Ctrl+Enter)</el-button>
      </el-form-item>
    </el-form>

    <div v-if="output || error" class="output-area">
      <h3>输出结果</h3>
      <pre v-if="output" class="stdout">{{ output }}</pre>
      <pre v-if="error" class="stderr">{{ error }}</pre>
    </div>
  </DocPage>
</template>

<style scoped>
.output-area {
  margin-top: 20px;
  background: #f5f5f5;
  padding: 15px;
  border-radius: 4px;
}
.stdout {
  color: #333;
  white-space: pre-wrap;
}
.stderr {
  color: #d32f2f;
  white-space: pre-wrap;
  border-top: 1px solid #ddd;
  margin-top: 10px;
  padding-top: 10px;
}
</style>

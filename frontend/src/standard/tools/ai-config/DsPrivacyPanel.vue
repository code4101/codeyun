<template>
  <section class="ds-privacy-panel">
    <h3>隐私翻译官</h3>
    <p>由 DeepSeek 官方识别敏感数据，真实值保存在本机，下游 agent 使用变量完成任务。</p>
    <el-input v-model="source" type="textarea" :rows="5" :maxlength="80000"
      placeholder="输入需要交给其他 agent 的任务，可包含姓名、账号、密码等信息"
      :disabled="busy" autocomplete="off" />
    <div class="privacy-actions">
      <el-button type="primary" :loading="busy" :disabled="!source.trim()" @click="translate">
        {{ packet ? '继续此任务' : '生成脱敏任务' }}
      </el-button>
      <el-button :disabled="busy" @click="reset">新任务</el-button>
      <el-button v-if="packet" @click="copy">复制交接包</el-button>
    </div>
    <template v-if="packet">
      <p>已建立 {{ packet.variables.length }} 个变量 · {{ packet.session_id }}</p>
      <div class="privacy-variables">
        <el-tag v-for="item in packet.variables" :key="item.name">{{ item.name }} · {{ item.kind }}</el-tag>
      </div>
      <el-input :model-value="packet.handoff_prompt" type="textarea" :rows="9" readonly aria-label="脱敏交接包" />
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

interface Packet {
  session_id: string
  variables: { name: string; kind: string }[]
  handoff_prompt: string
}
// Deliberately memory-only: never persist original text in browser storage.
const source = ref('')
const packet = ref<Packet | null>(null)
const busy = ref(false)
const error = ref('')
async function translate() {
  busy.value = true
  error.value = ''
  try {
    const response = await api.post<Packet>('/ai-chat/privacy/prepare', {
      text: source.value, session_id: packet.value?.session_id || '',
    }, { timeout: 180000 })
    packet.value = response.data
    source.value = ''
  } catch {
    error.value = '翻译失败，请检查官方 DS 配置和当前账号权限后重试。'
  } finally {
    busy.value = false
  }
}
function reset() {
  source.value = ''
  packet.value = null
  error.value = ''
}
async function copy() {
  try {
    await navigator.clipboard.writeText(packet.value?.handoff_prompt || '')
    ElMessage.success('已复制脱敏交接包')
  } catch {
    error.value = '复制失败，请手动选择交接包内容。'
  }
}
</script>

<style scoped>
.ds-privacy-panel { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--el-border-color); }
.ds-privacy-panel h3 { margin: 0 0 10px; }
.ds-privacy-panel p { color: var(--el-text-color-secondary); font-size: 13px; overflow-wrap: anywhere; }
.privacy-actions, .privacy-variables { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.privacy-actions .el-button + .el-button { margin-left: 0; }
</style>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'

interface SoftwareBundle {
  id: string
  title: string
  description: string
  version: string
  platform: string
  created_at: string
  files: { name: string; bytes: number; sha256: string }[]
}
const items = ref<SoftwareBundle[]>([])
const loading = ref(true)
const error = ref('')
const downloading = ref('')
const size = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`
async function load() {
  loading.value = true
  error.value = ''
  try { items.value = (await api.get('/admin-downloads/catalog')).data.items }
  catch { error.value = '加载失败，请确认超管登录状态后重试。' }
  finally { loading.value = false }
}
async function download(bundle: SoftwareBundle, filename: string) {
  downloading.value = filename
  error.value = ''
  try {
    // Native download streams to disk and supports Range, without buffering ZIPs in JS.
    await api.post('/admin-downloads/session')
    const link = document.createElement('a')
    link.href = `/api/admin-downloads/${encodeURIComponent(bundle.id)}/files/${encodeURIComponent(filename)}`
    link.download = filename
    document.body.append(link)
    link.click()
    link.remove()
  } catch { error.value = '下载失败，请确认超管登录状态后重试。' }
  finally { downloading.value = '' }
}
onMounted(load)
</script>

<template>
  <section class="software-downloads">
    <header><div><h1>软件工具下载</h1><p>常用工具与预配置软件包</p></div><el-tag type="warning">仅超管可见与下载</el-tag></header>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <p v-if="loading" role="status">正在加载软件…</p>
    <el-empty v-else-if="!items.length && !error" description="暂未发布软件包" />
    <el-button v-if="error" @click="load">重新加载</el-button>
    <div class="software-grid">
      <article v-for="item in items" :key="item.id">
        <h2>{{ item.title }}</h2>
        <div class="meta"><el-tag v-if="item.platform" size="small">{{ item.platform }}</el-tag><span v-if="item.version">v{{ item.version }}</span><time v-if="item.created_at">{{ new Date(item.created_at).toLocaleDateString('zh-CN') }}</time></div>
        <p class="description">{{ item.description }}</p>
        <div v-for="file in item.files" :key="file.name" class="file">
          <strong>{{ file.name }}</strong><span>{{ size(file.bytes) }}</span>
          <el-button type="primary" :loading="downloading === file.name" @click="download(item, file.name)">下载软件包</el-button>
          <details><summary>SHA-256 校验值</summary><code>{{ file.sha256 }}</code></details>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.software-downloads { padding: 28px; max-width: 1200px; margin: auto; }
header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
h1 { margin: 0; font-size: 26px; } header p { margin: 8px 0 0; color: var(--el-text-color-secondary); }
.software-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); gap: 20px; margin-top: 20px; }
article { padding: 24px; border: 1px solid var(--el-border-color-light); border-radius: 12px; background: var(--el-bg-color); }
h2 { margin: 0 0 14px; font-size: 21px; }.meta { display: flex; gap: 12px; align-items: center; color: var(--el-text-color-secondary); font-size: 13px; flex-wrap: wrap; }
.description { white-space: pre-line; line-height: 1.8; color: var(--el-text-color-regular); }
.file { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; padding-top: 16px; border-top: 1px solid var(--el-border-color-lighter); }
.file strong { overflow-wrap: anywhere; }.file > span { color: var(--el-text-color-secondary); font-size: 13px; }
details { width: 100%; color: var(--el-text-color-secondary); font-size: 12px; } summary { cursor: pointer; } code { display: block; overflow-wrap: anywhere; margin-top: 8px; }
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import { detectOperatingSystem, groupSoftware, recommendedBundle, type SoftwareBundle } from './catalog'

const items = ref<SoftwareBundle[]>([])
const selected = ref<Record<string, string>>({})
const system = detectOperatingSystem(navigator.userAgent, navigator.platform, navigator.maxTouchPoints)
const software = computed(() => groupSoftware(items.value).map(group => ({
  ...group,
  active: group.bundles.find(bundle => bundle.id === selected.value[group.name]),
})))
const loading = ref(true)
const error = ref('')
const downloading = ref('')
const size = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`
async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = (await api.get('/admin-downloads/catalog')).data.items
    for (const group of groupSoftware(items.value)) {
      if (!group.bundles.some(bundle => bundle.id === selected.value[group.name])) {
        selected.value[group.name] = recommendedBundle(group.bundles, system)?.id ?? ''
      }
    }
  }
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
    <el-empty v-else-if="!software.length && !error" description="暂未发布软件包" />
    <el-button v-if="error" @click="load">重新加载</el-button>
    <div class="software-grid">
      <article v-for="app in software" :key="app.name">
        <h2>{{ app.name }}</h2>
        <label class="platform-picker">
          <span>操作系统</span>
          <el-select v-model="selected[app.name]" :aria-label="`${app.name} 操作系统`" placeholder="请选择操作系统">
            <el-option v-for="bundle in app.bundles" :key="bundle.id" :value="bundle.id"
              :label="`${bundle.platform || '通用'}${bundle.version ? ` · v${bundle.version}` : ''}`" />
          </el-select>
        </label>
        <p class="platform-hint">{{ recommendedBundle(app.bundles, system)?.id === app.active?.id && app.active ? '已根据当前系统自动选择，可手动切换。' : '请选择适合目标设备的版本。' }}</p>
        <template v-if="app.active">
        <div class="meta"><span v-if="app.active.version">v{{ app.active.version }}</span><time v-if="app.active.created_at">{{ new Date(app.active.created_at).toLocaleDateString('zh-CN') }}</time></div>
        <p class="description">{{ app.active.description }}</p>
        <div v-for="file in app.active.files" :key="file.name" class="file">
          <strong>{{ file.name }}</strong><span>{{ size(file.bytes) }}</span>
          <el-button type="primary" :loading="downloading === file.name" @click="download(app.active, file.name)">下载软件包</el-button>
          <details><summary>SHA-256 校验值</summary><code>{{ file.sha256 }}</code></details>
        </div>
        </template>
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
.platform-picker { display: flex; flex-direction: column; gap: 8px; font-size: 13px; color: var(--el-text-color-regular); }
.platform-picker .el-select { width: 100%; }
.platform-hint { color: var(--el-text-color-secondary); font-size: 12px; margin: 8px 0 16px; }
.description { white-space: pre-line; line-height: 1.8; color: var(--el-text-color-regular); }
.file { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; padding-top: 16px; border-top: 1px solid var(--el-border-color-lighter); }
.file strong { overflow-wrap: anywhere; }.file > span { color: var(--el-text-color-secondary); font-size: 13px; }
details { width: 100%; color: var(--el-text-color-secondary); font-size: 12px; } summary { cursor: pointer; } code { display: block; overflow-wrap: anywhere; margin-top: 8px; }
</style>

<template>
  <div class="web-outline-page">
    <header class="page-header">
      <div>
        <h1>网页目录提取</h1>
        <p>输入文章地址，提取原始标题并重建为带编号的目录。</p>
      </div>
    </header>

    <form class="extract-bar" @submit.prevent="extractOutline">
      <el-input
        v-model="url"
        size="large"
        clearable
        placeholder="https://example.com/article"
        aria-label="文章 URL"
      />
      <el-button type="primary" size="large" native-type="submit" :loading="loading">
        提取目录
      </el-button>
    </form>

    <el-alert
      v-if="error"
      class="result-alert"
      type="error"
      :title="error"
      show-icon
      :closable="false"
    />

    <section v-if="result" class="result-section">
      <div class="result-toolbar">
        <div class="result-title">
          <strong>{{ result.title }}</strong>
        </div>
        <div class="result-actions">
          <el-button @click="copyMarkdown">复制 Markdown</el-button>
          <el-button
            v-if="isLinuxDoTopic"
            type="primary"
            :loading="bookImporting"
            @click="createLibraryBook"
          >存入图书馆</el-button>
        </div>
      </div>

      <div class="outline-list">
        <div
          v-for="(item, index) in result.items"
          :key="`${item.source_index ?? 'inferred'}-${index}`"
          class="outline-row"
          :class="{ 'document-title': index === 0 }"
          :style="{ '--outline-depth': String(Math.max(0, item.level - 1)) }"
        >
          <span v-if="item.number" class="outline-number">{{ item.number }}</span>
          <span class="outline-title">{{ item.title }}</span>
          <el-tag v-if="item.inferred" size="small" type="warning" effect="plain">推断分组</el-tag>
        </div>
      </div>

      <el-collapse class="source-collapse">
        <el-collapse-item name="source">
          <template #title>
            <span>查看网页原始标题（{{ result.source_headings.length }}）</span>
          </template>
          <div class="source-list">
            <div v-for="heading in result.source_headings" :key="heading.source_index" class="source-row">
              <code>h{{ heading.html_level }}</code>
              <span>{{ heading.title }}</span>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </section>

    <el-empty v-else-if="!loading && !error" description="提取结果会显示在这里" :image-size="72" />
  </div>
</template>

<script setup lang="ts">
import axios from 'axios'
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { extractWebOutline, type WebOutlineResult } from '@/api/webOutline'
import { importLinuxDoBook } from '@/api/linuxDoBooks'

const url = ref('')
const loading = ref(false)
const error = ref('')
const result = ref<WebOutlineResult | null>(null)
const bookImporting = ref(false)
const isLinuxDoTopic = computed(() => {
  try {
    const parsed = new URL(result.value?.url || url.value)
    return ['linux.do', 'www.linux.do'].includes(parsed.hostname.toLowerCase())
      && /^\/t\/(?:topic|[^/]+)\/\d+/.test(parsed.pathname)
  } catch {
    return false
  }
})

function getErrorMessage(value: unknown) {
  if (axios.isAxiosError(value)) {
    const detail = value.response?.data?.detail
    if (typeof detail === 'string' && detail) {
      return detail
    }
    return value.message || '请求失败'
  }
  return value instanceof Error ? value.message : '请求失败'
}

async function extractOutline() {
  const target = url.value.trim()
  if (!target) {
    ElMessage.warning('请输入文章 URL')
    return
  }
  loading.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await extractWebOutline(target)
  } catch (value) {
    error.value = getErrorMessage(value)
  } finally {
    loading.value = false
  }
}

async function copyMarkdown() {
  if (!result.value) return
  try {
    await navigator.clipboard.writeText(result.value.markdown)
    ElMessage.success('Markdown 目录已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择内容')
  }
}

async function createLibraryBook() {
  const target = result.value?.url || url.value.trim()
  if (!target) return
  bookImporting.value = true
  try {
    const book = await importLinuxDoBook(target)
    ElMessage.success(`《${book.title}》已存入图书馆`)
  } catch (value) {
    ElMessage.error(getErrorMessage(value))
  } finally {
    bookImporting.value = false
  }
}
</script>

<style scoped>
.web-outline-page {
  box-sizing: border-box;
  width: min(960px, 100%);
  margin: 0 auto;
  padding: 24px;
}

.page-header h1 {
  margin: 0;
  color: #303133;
  font-size: 26px;
  font-weight: 650;
}

.page-header p {
  margin: 8px 0 0;
  color: #606266;
  font-size: 14px;
}

.extract-bar {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) auto;
  gap: 12px;
  align-items: center;
  margin-top: 22px;
}

.result-section {
  margin-top: 28px;
  border-top: 1px solid #ebeef5;
  padding-top: 18px;
}

.result-toolbar,
.result-title,
.result-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.result-toolbar {
  justify-content: space-between;
}

.result-title strong {
  font-size: 17px;
}

.result-alert {
  margin-top: 16px;
}

.outline-list {
  margin-top: 14px;
  border-top: 1px solid #ebeef5;
}

.outline-row {
  display: flex;
  min-height: 42px;
  align-items: center;
  gap: 9px;
  border-bottom: 1px solid #f0f2f5;
  padding: 0 10px 0 calc(10px + var(--outline-depth) * 24px);
  color: #303133;
}

.outline-row.document-title {
  font-weight: 650;
}

.outline-number {
  min-width: 42px;
  color: #909399;
  font-variant-numeric: tabular-nums;
}

.outline-title {
  min-width: 0;
  overflow-wrap: anywhere;
}

.source-collapse {
  margin-top: 12px;
}

.source-list {
  display: grid;
  gap: 6px;
}

.source-row {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 8px;
  align-items: baseline;
  color: #606266;
}

.source-row code {
  color: #909399;
}

:deep(.el-empty) {
  padding-top: 72px;
}

@media (max-width: 720px) {
  .web-outline-page {
    padding: 18px 14px;
  }

  .extract-bar {
    grid-template-columns: 1fr auto;
  }

  .extract-bar :deep(.el-input) {
    grid-column: 1 / -1;
  }

  .outline-row {
    padding-left: calc(6px + var(--outline-depth) * 14px);
  }
}
</style>

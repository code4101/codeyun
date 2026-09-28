<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { collectHtmlCompatibility, collectUrlCompatibility } from '@/router/urlCompatibility'
import feedbackEntry from '../../../../attendance-feedback/index.html?raw'

const router = useRouter()
const search = ref('')
const kind = ref('')
const rules = computed(() => [
  ...collectUrlCompatibility(router),
  ...collectHtmlCompatibility('/attendance-feedback/index.html', feedbackEntry),
])
const kinds = computed(() => [...new Set(rules.value.map(rule => rule.kind))])
const filtered = computed(() => {
  const text = search.value.trim().toLowerCase()
  return rules.value.filter(rule => (!kind.value || rule.kind === kind.value)
    && `${rule.from} ${rule.to} ${rule.source}`.toLowerCase().includes(text))
})
</script>

<template>
  <section class="url-compatibility">
    <h1>旧 URL 兼容</h1>
    <p class="description">列出当前前端已注册的跳转、别名和独立 HTML 入口规则。目标为下一跳；路径中的参数按实际访问地址替换。访问权限由目标页面控制。</p>
    <div class="filters">
      <el-input v-model="search" clearable placeholder="搜索旧地址或目标地址" aria-label="搜索兼容地址" />
      <el-select v-model="kind" clearable placeholder="全部兼容方式" aria-label="兼容方式">
        <el-option v-for="item in kinds" :key="item" :label="item" :value="item" />
      </el-select>
      <span class="count">{{ filtered.length }} / {{ rules.length }} 条</span>
    </div>
    <el-table :data="filtered" stripe empty-text="没有匹配的兼容规则">
      <el-table-column type="expand">
        <template #default="{ row }"><pre class="rule-detail">{{ row.detail }}</pre></template>
      </el-table-column>
      <el-table-column prop="from" label="旧 URL" min-width="300" sortable><template #default="{ row }"><code>{{ row.from }}</code></template></el-table-column>
      <el-table-column prop="to" label="目标 URL（下一跳）" min-width="280"><template #default="{ row }"><code>{{ row.to }}</code></template></el-table-column>
      <el-table-column prop="kind" label="兼容方式" width="150" />
      <el-table-column prop="source" label="来源" min-width="160" />
    </el-table>
  </section>
</template>

<style scoped>
.url-compatibility { padding: 24px; }
h1 { margin: 0 0 12px; font-size: 24px; }
.description { color: var(--el-text-color-secondary); line-height: 1.7; max-width: 900px; }
.filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin: 20px 0; }
.filters .el-input { width: min(420px, 100%); }
.filters .el-select { width: 180px; }
.count { color: var(--el-text-color-secondary); }
code { overflow-wrap: anywhere; }
.rule-detail { padding: 12px 24px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; line-height: 1.7; }
</style>

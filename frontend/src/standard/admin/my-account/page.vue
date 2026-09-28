<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { fetchAccountUsage, type AccountUsage, type UsageCategory } from '@/api/accountData'
import { useUserStore } from '@/store/userStore'
import AccountProfile from './AccountProfile.vue'

const userStore = useUserStore()
const usage = ref<AccountUsage | null>(null)
const loading = ref(false)
const error = ref('')
let request: AbortController | undefined
const colors: Record<string, string> = { notes: '#5684e8', sheets: '#3da997', library: '#bd8a48', project_graph: '#8d70cf' }
const categories = computed(() => [...(usage.value?.categories ?? [])].sort((a, b) => b.total_bytes - a.total_bytes))
const expanded = ref<Record<string, boolean>>({})
function setAllExpanded(value: boolean) {
  expanded.value = Object.fromEntries(categories.value.map(item => [item.key, value]))
}
const significantNumber = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 4 })
const resourceCount = computed(() => usage.value?.categories.reduce((sum, item) => sum + item.resource_count, 0) ?? 0)
const incomplete = computed(() => usage.value?.categories.some(item => item.unavailable_count > 0))
const updatedAt = computed(() => usage.value ? new Date(usage.value.generated_at * 1000).toLocaleString() : '')

function bytes(value: number) {
  if (value === 0) return '0 B'
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4)
  return `${significantNumber.format(value / 1024 ** unit)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][unit]}`
}
function breakdown(item: UsageCategory) {
  const parts: { label: string; count?: number; bytes: number; retained_bytes?: number }[] = [...item.parts]
  if (item.key === 'library') parts.push({ label: '服务器文件与书籍资源', bytes: item.file_bytes })
  return parts.sort((a, b) => b.bytes - a.bytes)
}
function percentage(value: number) {
  return usage.value?.total_bytes ? value / usage.value.total_bytes * 100 : 0
}
async function refresh() {
  request?.abort()
  const current = new AbortController()
  request = current
  loading.value = true
  error.value = ''
  try {
    const result = await fetchAccountUsage(current.signal)
    if (request === current) usage.value = result
  } catch {
    if (!current.signal.aborted) error.value = '统计加载失败，请重试。'
  } finally {
    if (request === current) loading.value = false
  }
}
watch(() => userStore.user?.id, () => { usage.value = null; expanded.value = {}; void refresh() }, { immediate: true })
onBeforeUnmount(() => { request?.abort() })
</script>

<template>
  <div class="my-account-page" :aria-busy="loading">
    <header class="page-heading">
      <h1>我的账号</h1>
    </header>
    <AccountProfile />
    <div class="storage-toolbar">
      <h2 class="section-title">存储用量</h2>
      <template v-if="usage">
        <span class="updated-at">更新于 {{ updatedAt }}</span>
        <button type="button" @click="setAllExpanded(true)">全部展开</button>
        <button type="button" @click="setAllExpanded(false)">全部收起</button>
      </template>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <p v-if="loading && !usage" role="status" class="loading">正在统计资源与存储空间…</p>
    <template v-if="usage">
      <el-alert v-if="incomplete" title="部分目录无法读取或包含链接，当前显示已统计部分。" type="warning" :closable="false" show-icon />
      <div class="usage-table-scroll">
        <table class="usage-table" aria-label="账号存储用量，按占用空间降序排列">
          <colgroup><col class="name-col" /><col class="size-col" /><col class="share-col" /><col class="count-col" /><col class="retained-col" /></colgroup>
          <thead><tr><th scope="col">名称</th><th scope="col" aria-sort="descending">占用空间 ↓</th><th scope="col">占总量</th><th scope="col">数量</th><th scope="col">其中回收站</th></tr></thead>
          <tbody>
            <tr class="total-row"><th scope="row">已统计合计</th><td>{{ bytes(usage.total_bytes) }}</td><td>{{ usage.total_bytes ? '100%' : '—' }}</td><td>{{ resourceCount.toLocaleString() }}</td><td>{{ bytes(categories.reduce((sum, item) => sum + item.retained_bytes, 0)) }}</td></tr>
          </tbody>
          <tbody v-for="item in categories" :key="item.key" :style="{ '--category-color': colors[item.key] }">
            <tr class="category-row" @click="expanded[item.key] = !expanded[item.key]">
              <th scope="row"><button type="button" class="tree-toggle" :aria-expanded="!!expanded[item.key]" :aria-label="`${expanded[item.key] ? '收起' : '展开'}${item.title}`" @click.stop="expanded[item.key] = !expanded[item.key]">
                <span class="chevron" :class="{ open: expanded[item.key] }" aria-hidden="true">›</span><span class="category-dot" aria-hidden="true" />{{ item.title }}
              </button></th>
              <td>{{ bytes(item.total_bytes) }}</td>
              <td class="share-cell"><span class="share-fill" :style="{ width: `${percentage(item.total_bytes)}%` }" aria-hidden="true" /><span>{{ significantNumber.format(percentage(item.total_bytes)) }}%</span></td>
              <td>{{ item.resource_count.toLocaleString() }}</td><td>{{ item.retained_bytes ? bytes(item.retained_bytes) : '—' }}</td>
            </tr>
            <template v-if="expanded[item.key]">
              <tr v-for="part in breakdown(item)" :key="part.label" class="detail-row">
                <th scope="row"><span class="tree-leaf">{{ part.label }}</span></th><td>{{ bytes(part.bytes) }}</td>
                <td class="share-cell"><span class="share-fill" :style="{ width: `${percentage(part.bytes)}%` }" aria-hidden="true" /><span>{{ significantNumber.format(percentage(part.bytes)) }}%</span></td>
                <td>{{ part.count === undefined ? '—' : part.count.toLocaleString() }}</td><td>{{ part.retained_bytes ? bytes(part.retained_bytes) : '—' }}</td>
              </tr>
              <tr v-if="item.external_reference_bytes || item.unknown_external_size_count" class="external-row"><td colspan="5">外部 PDF 引用 {{ bytes(item.external_reference_bytes) }}，不计入占用<span v-if="item.unknown_external_size_count">；{{ item.unknown_external_size_count }} 份大小未知</span></td></tr>
            </template>
          </tbody>
        </table>
      </div>
      <p class="ownership-note">共享资源只计入拥有者账号。展开分类查看明细，数量按各行对应的资源或记录统计。</p>
      <details class="accounting-notes"><summary>统计口径</summary><ul><li v-for="note in usage.notes" :key="note">{{ note }}</li></ul></details>
    </template>
  </div>
</template>

<style scoped>
.my-account-page { max-width: 1440px; padding: 24px; margin: 0 auto; color: #303133; box-sizing: border-box; }
.page-heading { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 22px; }
h1 { font-size: 24px; margin: 0 0 6px; }
.ownership-note, .updated-at { color: #737984; font-size: 12px; line-height: 1.6; }
.storage-toolbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 12px; }
.section-title { font-size: 16px; margin: 0; }
.updated-at { margin-left: auto; }
.storage-toolbar button { border: 0; background: none; padding: 4px 0; color: var(--el-color-primary); font: inherit; font-size: 12px; cursor: pointer; }
.usage-table-scroll { overflow-x: auto; border: 1px solid #e4e7ed; border-radius: 5px; }
.usage-table { width: 100%; min-width: 800px; table-layout: fixed; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
.name-col { width: 40%; } .size-col { width: 16%; } .share-col { width: 16%; } .count-col { width: 12%; } .retained-col { width: 16%; }
.usage-table th, .usage-table td { padding: 7px 12px; height: 34px; box-sizing: border-box; border-bottom: 1px solid #edf0f5; text-align: right; white-space: nowrap; }
.usage-table th:first-child { text-align: left; }
.usage-table thead th { background: #f5f7fa; color: #606266; font-weight: 500; }
.total-row { background: #f8fafc; font-weight: 600; }
.category-row { cursor: pointer; }
.category-row:hover, .detail-row:hover { background: #f5f8fc; }
.usage-table tbody:last-child tr:last-child > * { border-bottom: 0; }
.tree-toggle { display: flex; align-items: center; gap: 8px; width: 100%; border: 0; background: transparent; padding: 0; color: inherit; font: inherit; font-weight: 500; text-align: left; cursor: pointer; }
.tree-toggle:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
.chevron { display: inline-block; width: 12px; text-align: center; color: #858c97; font-size: 18px; line-height: 18px; }
.chevron.open { transform: rotate(90deg); }
.category-dot { width: 7px; height: 7px; flex-shrink: 0; border-radius: 2px; background: var(--category-color); }
.detail-row th { font-weight: 400; color: #737984; overflow: hidden; text-overflow: ellipsis; }
.tree-leaf { padding-left: 35px; }
.share-cell { position: relative; }
.share-fill { position: absolute; left: 0; top: 7px; bottom: 7px; background: var(--category-color); opacity: .15; pointer-events: none; }
.share-cell > span:last-child { position: relative; }
.external-row td { text-align: left; padding-left: 47px; color: #737984; font-size: 12px; white-space: normal; }
.ownership-note { margin: 10px 0; }
.accounting-notes { color: #737984; font-size: 12px; line-height: 1.8; }
.accounting-notes summary { cursor: pointer; width: fit-content; }
.accounting-notes ul { padding-left: 20px; margin: 8px 0; }
.loading { padding: 40px 0; text-align: center; color: #737984; }
@media (max-width: 760px) { .my-account-page { padding: 16px; } .updated-at { width: 100%; margin-left: 0; order: 1; } }
</style>

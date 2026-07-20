<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Delete, Edit, Link, Plus } from '@element-plus/icons-vue'

type CommonSite = {
  id: string
  title: string
  url: string
  description?: string
}

type SiteForm = {
  title: string
  url: string
  description: string
}

const STORAGE_KEY = 'codeyun.notes.commonSites.v1'
const DEFAULT_SITES_VERSION_KEY = 'codeyun.notes.commonSites.defaultsVersion'
const DEFAULT_SITES_VERSION = 2
const DEFAULT_SITES: CommonSite[] = [
  {
    id: 'codex-usage',
    title: 'codex余额',
    url: 'https://chatgpt.com/codex/cloud/settings/analytics#usage',
    description: 'ChatGPT Codex 云端用量页面',
  },
  {
    id: 'z-library',
    title: 'Z-Library',
    url: 'https://zh.z-library.sk/',
    description: '电子书网站',
  },
]

const sites = ref<CommonSite[]>([])
const dialogVisible = ref(false)
const editingId = ref<string | null>(null)
const keyword = ref('')
const form = reactive<SiteForm>({
  title: '',
  url: '',
  description: '',
})

const filteredSites = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return sites.value
  return sites.value.filter((site) =>
    [site.title, site.url, site.description ?? ''].some((value) => value.toLowerCase().includes(text)),
  )
})

const canUseLocalStorage = () =>
  typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'

const cloneDefaultSites = () => DEFAULT_SITES.map((site) => ({ ...site }))

const applyDefaultSitesMigration = (savedSites: CommonSite[]) => {
  const appliedVersion = Number(window.localStorage.getItem(DEFAULT_SITES_VERSION_KEY) ?? 0)
  if (appliedVersion >= DEFAULT_SITES_VERSION) return savedSites

  const existingIds = new Set(savedSites.map((site) => site.id))
  const migratedSites = [
    ...savedSites,
    ...DEFAULT_SITES.filter((site) => !existingIds.has(site.id)).map((site) => ({ ...site })),
  ]
  window.localStorage.setItem(DEFAULT_SITES_VERSION_KEY, String(DEFAULT_SITES_VERSION))
  return migratedSites
}

const loadSites = () => {
  if (!canUseLocalStorage()) {
    sites.value = cloneDefaultSites()
    return
  }
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (!raw) {
    sites.value = cloneDefaultSites()
    window.localStorage.setItem(DEFAULT_SITES_VERSION_KEY, String(DEFAULT_SITES_VERSION))
    return
  }
  try {
    const parsed = JSON.parse(raw) as CommonSite[]
    sites.value = Array.isArray(parsed)
      ? applyDefaultSitesMigration(parsed)
      : cloneDefaultSites()
  } catch {
    sites.value = cloneDefaultSites()
  }
}

const saveSites = () => {
  if (!canUseLocalStorage()) return
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sites.value))
}

const resetForm = () => {
  editingId.value = null
  form.title = ''
  form.url = ''
  form.description = ''
}

const openCreateDialog = () => {
  resetForm()
  dialogVisible.value = true
}

const openEditDialog = (site: CommonSite) => {
  editingId.value = site.id
  form.title = site.title
  form.url = site.url
  form.description = site.description ?? ''
  dialogVisible.value = true
}

const normalizeUrl = (value: string) => {
  const text = value.trim()
  if (!text) return ''
  if (/^https?:\/\//i.test(text)) return text
  return `https://${text}`
}

const submitSite = () => {
  const title = form.title.trim()
  const url = normalizeUrl(form.url)
  const description = form.description.trim()
  if (!title || !url) {
    ElMessage.warning('名称和网址都要填写')
    return
  }
  try {
    new URL(url)
  } catch {
    ElMessage.warning('网址格式不正确')
    return
  }
  if (editingId.value) {
    const current = sites.value.find((site) => site.id === editingId.value)
    if (current) {
      current.title = title
      current.url = url
      current.description = description
    }
  } else {
    sites.value.push({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      title,
      url,
      description,
    })
  }
  dialogVisible.value = false
  resetForm()
}

const removeSite = async (site: CommonSite) => {
  try {
    await ElMessageBox.confirm(`删除「${site.title}」？`, '删除常用网站', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    sites.value = sites.value.filter((item) => item.id !== site.id)
  } catch {
    // 用户取消删除。
  }
}

const copyUrl = async (site: CommonSite) => {
  try {
    await navigator.clipboard.writeText(site.url)
    ElMessage.success('已复制网址')
  } catch {
    ElMessage.error('复制失败')
  }
}

const openSite = (site: CommonSite) => {
  window.open(site.url, '_blank', 'noopener,noreferrer')
}

onMounted(loadSites)
watch(sites, saveSites, { deep: true })
</script>

<template>
  <main class="common-sites-page">
    <header class="page-header">
      <h1>常用网站</h1>
      <div class="header-actions">
        <el-input
          v-model="keyword"
          class="search-input"
          clearable
          placeholder="搜索"
        />
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">新增</el-button>
      </div>
    </header>

    <section class="site-list">
      <div
        v-for="site in filteredSites"
        :key="site.id"
        class="site-row"
      >
        <button class="site-main" type="button" @click="openSite(site)">
          <span class="site-title">{{ site.title }}</span>
          <span class="site-url">{{ site.url }}</span>
          <span v-if="site.description" class="site-description">{{ site.description }}</span>
        </button>
        <div class="row-actions">
          <el-tooltip content="打开" placement="top">
            <el-button :icon="Link" circle @click="openSite(site)" />
          </el-tooltip>
          <el-tooltip content="复制网址" placement="top">
            <el-button :icon="CopyDocument" circle @click="copyUrl(site)" />
          </el-tooltip>
          <el-tooltip content="编辑" placement="top">
            <el-button :icon="Edit" circle @click="openEditDialog(site)" />
          </el-tooltip>
          <el-tooltip content="删除" placement="top">
            <el-button :icon="Delete" circle type="danger" @click="removeSite(site)" />
          </el-tooltip>
        </div>
      </div>
      <el-empty v-if="!filteredSites.length" description="没有匹配的网站" />
    </section>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑网站' : '新增网站'"
      width="520px"
      @closed="resetForm"
    >
      <el-form label-width="64px" @submit.prevent>
        <el-form-item label="名称">
          <el-input v-model="form.title" autofocus />
        </el-form-item>
        <el-form-item label="网址">
          <el-input v-model="form.url" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.description" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitSite">保存</el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.common-sites-page {
  padding: 20px 24px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.page-header h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: #1f2937;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-input {
  width: 220px;
}

.site-list {
  border-top: 1px solid #e5e7eb;
}

.site-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
  min-height: 68px;
  border-bottom: 1px solid #edf0f3;
}

.site-main {
  display: grid;
  grid-template-columns: max-content minmax(160px, 1fr);
  gap: 4px 14px;
  align-items: baseline;
  min-width: 0;
  padding: 10px 0;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.site-main:hover .site-title {
  color: #2563eb;
}

.site-title {
  font-size: 15px;
  font-weight: 600;
  color: #111827;
  white-space: nowrap;
}

.site-url {
  min-width: 0;
  overflow: hidden;
  color: #4b5563;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.site-description {
  grid-column: 1 / -1;
  color: #6b7280;
  font-size: 13px;
}

.row-actions {
  display: flex;
  gap: 8px;
}

@media (max-width: 720px) {
  .common-sites-page {
    padding: 16px;
  }

  .page-header,
  .header-actions,
  .site-row {
    align-items: stretch;
    grid-template-columns: 1fr;
  }

  .page-header {
    flex-direction: column;
  }

  .search-input {
    width: 100%;
  }

  .site-main {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .row-actions {
    padding-bottom: 12px;
  }
}
</style>

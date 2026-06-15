<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  listGithubProjects,
  patchGithubProject,
  upsertGithubProject,
  type GithubProject,
  type GithubProjectUpsertRequest,
} from '@/api/githubProjects'

const loading = ref(false)
const saving = ref(false)
const noteSaving = ref(false)
const query = ref('')
const reviewFilter = ref<'all' | 'pending' | 'done'>('all')
const projects = ref<GithubProject[]>([])
const selectedId = ref<number | null>(null)
const noteDraft = ref('')
const addText = ref('')

const selectedProject = computed(() =>
  projects.value.find((project) => project.id === selectedId.value) ?? null,
)

const needsReviewParam = computed(() => {
  if (reviewFilter.value === 'pending') return true
  if (reviewFilter.value === 'done') return false
  return null
})

function formatDateTime(value: string | number | null | undefined) {
  if (!value) return ''
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  const hour = `${date.getHours()}`.padStart(2, '0')
  const minute = `${date.getMinutes()}`.padStart(2, '0')
  return `${year}/${month}/${day} ${hour}:${minute}`
}

function getTimestamp(value: string | number | null | undefined) {
  if (!value) return 0
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? 0 : date.getTime()
}

function trimUnitNumber(value: number) {
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2
  return value.toFixed(digits).replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1')
}

function formatCount(value: number | null | undefined) {
  const num = Number(value || 0)
  if (!Number.isFinite(num)) return '0'
  if (Math.abs(num) >= 100000000) return `${trimUnitNumber(num / 100000000)}亿`
  if (Math.abs(num) >= 10000) return `${trimUnitNumber(num / 10000)}万`
  return `${num}`
}

function sortByPublishedAt(a: GithubProject, b: GithubProject) {
  return getTimestamp(a.created_at_github) - getTimestamp(b.created_at_github)
}

function sortByUpdatedAt(a: GithubProject, b: GithubProject) {
  return getTimestamp(a.pushed_at || a.updated_at) - getTimestamp(b.pushed_at || b.updated_at)
}

function getString(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function getNumber(value: unknown) {
  const num = Number(value)
  return Number.isFinite(num) ? num : 0
}

function buildPayloadFromGithubJson(source: Record<string, unknown>): GithubProjectUpsertRequest | null {
  const githubRepoId = getNumber(source.id)
  const fullName = getString(source.full_name)
  if (!githubRepoId || !fullName.includes('/')) return null
  const license = source.license && typeof source.license === 'object'
    ? getString((source.license as Record<string, unknown>).spdx_id)
    : ''
  return {
    github_repo_id: githubRepoId,
    full_name: fullName,
    html_url: getString(source.html_url),
    default_branch: getString(source.default_branch),
    description: getString(source.description),
    homepage: getString(source.homepage),
    language: getString(source.language),
    license_spdx_id: license,
    topics: Array.isArray(source.topics) ? source.topics.map((item) => getString(item)).filter(Boolean) : [],
    stars: getNumber(source.stargazers_count),
    forks: getNumber(source.forks_count),
    open_issues: getNumber(source.open_issues_count),
    archived: Boolean(source.archived),
    disabled: Boolean(source.disabled),
    private: Boolean(source.private),
    created_at: getString(source.created_at),
    pushed_at: getString(source.pushed_at),
    updated_at: getString(source.updated_at),
    source: { source_type: 'manual', source_label: '手工粘贴' },
  }
}

function selectProject(project: GithubProject) {
  selectedId.value = project.id
  noteDraft.value = project.analysis_note
}

async function loadProjects() {
  loading.value = true
  try {
    const payload = await listGithubProjects({
      q: query.value.trim(),
      needs_review: needsReviewParam.value,
      limit: 200,
    })
    projects.value = payload.items
    if (!projects.value.some((project) => project.id === selectedId.value)) {
      const first = projects.value[0] ?? null
      selectedId.value = first?.id ?? null
      noteDraft.value = first?.analysis_note ?? ''
    }
  } finally {
    loading.value = false
  }
}

function buildPayload(): GithubProjectUpsertRequest | null {
  const text = addText.value.trim()
  if (!text) {
    ElMessage.error('先输入项目')
    return null
  }
  if (text.startsWith('{')) {
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>
      const payload = buildPayloadFromGithubJson(parsed)
      if (payload) return payload
    } catch {
      // Fall through to the compact line parser.
    }
  }

  const compactMatch = text.match(/^(\d+)\s+([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)$/)
  const githubRepoId = compactMatch ? Number(compactMatch[1]) : 0
  const fullName = compactMatch ? compactMatch[2] : ''
  if (!Number.isFinite(githubRepoId) || githubRepoId <= 0) {
    ElMessage.error('格式用：repo id owner/repo，或粘贴 GitHub API JSON')
    return null
  }
  return {
    github_repo_id: githubRepoId,
    full_name: fullName,
    html_url: `https://github.com/${fullName}`,
    default_branch: 'main',
    source: { source_type: 'manual', source_label: '手工录入' },
  }
}

async function submitProject() {
  const payload = buildPayload()
  if (!payload) return
  saving.value = true
  try {
    const result = await upsertGithubProject(payload)
    await loadProjects()
    selectedId.value = result.item.id
    noteDraft.value = result.item.analysis_note
    addText.value = ''
    ElMessage.success(result.created ? '已加入项目池' : result.changed ? '已记录更新' : '已更新来源')
  } finally {
    saving.value = false
  }
}

async function saveNote(markAnalyzed = false) {
  const project = selectedProject.value
  if (!project) return
  noteSaving.value = true
  try {
    const updated = await patchGithubProject(project.id, {
      analysis_note: noteDraft.value,
      needs_review: markAnalyzed ? false : project.needs_review,
    })
    projects.value = projects.value.map((item) => (item.id === updated.id ? updated : item))
    selectProject(updated)
    ElMessage.success(markAnalyzed ? '已保存并标记已分析' : '分析已保存')
  } finally {
    noteSaving.value = false
  }
}

function openProject(project: GithubProject) {
  const url = project.html_url || `https://github.com/${project.full_name}`
  window.open(url, '_blank', 'noopener,noreferrer')
}

onMounted(() => {
  void loadProjects()
})
</script>

<template>
  <div class="github-projects-page">
    <div class="project-toolbar">
      <el-input v-model="query" clearable placeholder="搜索项目、描述、语言" class="query-input" @keyup.enter="loadProjects" />
      <el-select v-model="reviewFilter" class="review-select" @change="loadProjects">
        <el-option label="全部" value="all" />
        <el-option label="待分析" value="pending" />
        <el-option label="已分析" value="done" />
      </el-select>
      <el-button :loading="loading" @click="loadProjects">查询</el-button>
    </div>

    <section class="upsert-panel">
      <el-input
        v-model="addText"
        placeholder="添加项目：repo id owner/repo，或粘贴 GitHub API JSON"
        class="add-input"
        @keyup.enter="submitProject"
      />
      <el-button type="primary" :loading="saving" @click="submitProject">保存</el-button>
    </section>

    <div class="project-workspace">
      <el-table
        v-loading="loading"
        :data="projects"
        table-layout="auto"
        :fit="false"
        class="project-table"
        highlight-current-row
        @row-click="selectProject"
      >
        <el-table-column label="项目" min-width="220">
          <template #default="{ row }">
            <button class="link-button" type="button" @click.stop="openProject(row)">
              {{ row.full_name }}
            </button>
            <span v-if="row.archived" class="muted-flag">archived</span>
          </template>
        </el-table-column>
        <el-table-column label="语言" prop="language" min-width="90" />
        <el-table-column label="Stars" prop="stars" min-width="90" align="right" sortable>
          <template #default="{ row }">
            <span :title="String(row.stars)">{{ formatCount(row.stars) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="发布" min-width="150" :sort-method="sortByPublishedAt" sortable>
          <template #default="{ row }">{{ formatDateTime(row.created_at_github) || '-' }}</template>
        </el-table-column>
        <el-table-column label="更新" min-width="150" :sort-method="sortByUpdatedAt" sortable>
          <template #default="{ row }">{{ formatDateTime(row.pushed_at || row.updated_at) }}</template>
        </el-table-column>
      </el-table>

      <section class="project-detail">
        <template v-if="selectedProject">
          <div class="detail-head">
            <div>
              <h2>{{ selectedProject.full_name }}</h2>
              <p>{{ selectedProject.description || '无描述' }}</p>
            </div>
            <el-button size="small" @click="openProject(selectedProject)">GitHub</el-button>
          </div>
          <dl class="meta-grid">
            <div><dt>repo id</dt><dd>{{ selectedProject.github_repo_id }}</dd></div>
            <div><dt>主分支</dt><dd>{{ selectedProject.default_branch || '-' }}</dd></div>
            <div><dt>created_at</dt><dd>{{ formatDateTime(selectedProject.created_at_github) || '-' }}</dd></div>
            <div><dt>pushed_at</dt><dd>{{ formatDateTime(selectedProject.pushed_at) || '-' }}</dd></div>
            <div><dt>updated_at</dt><dd>{{ formatDateTime(selectedProject.updated_at) || '-' }}</dd></div>
            <div><dt>来源</dt><dd>{{ selectedProject.source_refs.length }}</dd></div>
            <div><dt>更新记录</dt><dd>{{ selectedProject.update_notes.length }}</dd></div>
          </dl>
          <div class="topic-row">
            <el-tag v-for="topic in selectedProject.topics" :key="topic" size="small" effect="plain">{{ topic }}</el-tag>
          </div>
          <el-input
            v-model="noteDraft"
            type="textarea"
            :rows="9"
            resize="vertical"
            placeholder="分析备注"
          />
          <div class="detail-actions">
            <el-button :loading="noteSaving" @click="saveNote(false)">保存分析</el-button>
            <el-button type="primary" :loading="noteSaving" @click="saveNote(true)">标记已分析</el-button>
          </div>
        </template>
        <el-empty v-else description="没有项目" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.github-projects-page {
  min-height: 100%;
  padding: 18px;
  box-sizing: border-box;
  background: #f6f8fb;
}

.project-toolbar,
.upsert-panel {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.query-input {
  width: 280px;
}

.review-select {
  width: 120px;
}

.add-input {
  width: 520px;
  max-width: 100%;
}

.project-workspace {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 14px;
}

.project-table {
  max-width: 100%;
}

.link-button {
  border: 0;
  padding: 0;
  background: transparent;
  color: #2563eb;
  cursor: pointer;
  font: inherit;
}

.muted-flag {
  margin-left: 8px;
  color: #8a94a6;
  font-size: 12px;
}

.project-detail {
  width: min(100%, 980px);
  background: #fff;
  border: 1px solid #dfe5ef;
  border-radius: 8px;
  padding: 16px;
  box-sizing: border-box;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.detail-head h2 {
  margin: 0;
  font-size: 18px;
  line-height: 1.3;
}

.detail-head p {
  margin: 6px 0 0;
  color: #667085;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
  margin: 14px 0;
}

.meta-grid div {
  min-width: 0;
}

.meta-grid dt {
  color: #8a94a6;
  font-size: 12px;
}

.meta-grid dd {
  margin: 2px 0 0;
  color: #1f2937;
  overflow-wrap: anywhere;
}

.topic-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.detail-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

@media (max-width: 720px) {
  .detail-head {
    flex-direction: column;
  }

  .meta-grid {
    grid-template-columns: 1fr;
  }
}
</style>

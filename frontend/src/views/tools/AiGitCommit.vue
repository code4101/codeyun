<template>
  <div class="ai-git-commit-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <div class="eyebrow">AI工具 / AI提交</div>
        <h1>AI提交</h1>
        <p>切换项目后会自动同步 Git 变更；普通改动直接生成草稿，超大改动会先进入分层拆分。</p>
      </div>
    </section>

    <div class="workspace-grid">
      <aside class="control-panel">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">项目库</p>
              <h2>手动维护项目</h2>
            </div>
            <div class="panel-header-actions">
              <el-button
                text
                :icon="RefreshRight"
                :loading="repoStatusesLoading"
                :disabled="!savedRepos.length"
                @click="refreshRepoStatuses()"
              >
                刷新状态
              </el-button>
              <el-button type="primary" plain @click="openAddRepoDialog()">
                添加项目
              </el-button>
            </div>
          </div>

          <div v-if="!savedRepos.length" class="placeholder-card repo-library-placeholder">
            <p>先手动添加常用项目，之后就能快速看脏状态并切换到对应仓库提交。</p>
          </div>

          <div v-else ref="savedRepoListRef" class="saved-repo-list">
            <div
              v-for="repo in displayedSavedRepos"
              :key="repo.id"
              class="saved-repo-item"
              :class="{ 'is-active': repo.id === selectedSavedRepoId }"
              @click="selectSavedRepo(repo)"
            >
              <div class="saved-repo-top">
                <div class="saved-repo-top-main">
                  <span class="saved-repo-handle" @click.stop>
                    <SortableOrderHandle
                      :index="repo.order_index"
                      :total="displayedSavedRepos.length"
                      size="sm"
                      :disabled="savingSavedRepos || displayedSavedRepos.length <= 1"
                    />
                  </span>
                  <strong>{{ repo.name }}</strong>
                </div>
                <div class="saved-repo-top-side">
                  <el-tag size="small" effect="plain" :type="getSavedRepoStatusType(repo.id)">
                    {{ getSavedRepoStatusLabel(repo.id) }}
                  </el-tag>
                  <el-button text size="small" type="danger" @click.stop="removeSavedRepo(repo)">移除</el-button>
                </div>
              </div>

              <div class="saved-repo-meta">
                <span>{{ getSavedRepoDeviceLabel(repo) }}</span>
                <span v-if="repoStatusMap[repo.id]?.branch">· {{ repoStatusMap[repo.id]?.branch }}</span>
              </div>

              <p class="saved-repo-path">{{ repo.cwd }}</p>
              <p v-if="repoStatusMap[repo.id]?.error" class="saved-repo-error">
                {{ repoStatusMap[repo.id]?.error }}
              </p>
            </div>
          </div>
        </section>

        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">执行范围</p>
              <h2>仓库与模型</h2>
            </div>
            <el-tag v-if="selectedDevice" :type="selectedDevice.mode === 'local' ? 'success' : 'warning'" effect="plain">
              {{ selectedDevice.mode === 'local' ? '本地设备' : '远程设备' }}
            </el-tag>
          </div>

          <div v-if="selectedSavedRepo" class="selected-repo-banner">
            <span>当前项目：</span>
            <strong>{{ selectedSavedRepo.name }}</strong>
            <span class="selected-repo-banner-path">{{ selectedSavedRepo.cwd }}</span>
          </div>

          <el-form label-position="top" class="settings-form">
            <el-form-item label="设备">
              <el-select
                v-model="form.entryId"
                filterable
                placeholder="先选择一个设备"
                :disabled="!devices.length"
              >
                <el-option
                  v-for="device in devices"
                  :key="device.id"
                  :label="getDeviceLabel(device)"
                  :value="device.id"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="项目目录">
              <el-input
                v-model="form.cwd"
                clearable
                placeholder="例如 D:\\home\\chenkunze\\slns\\codeyun 或 /srv/app"
              />
            </el-form-item>

            <el-form-item label="AI 来源">
              <el-select
                v-model="form.providerId"
                filterable
                placeholder="选择 AI 来源"
                :disabled="!providers.length"
                @change="handleProviderChange"
              >
                <el-option
                  v-for="provider in providers"
                  :key="provider.id"
                  :label="provider.label"
                  :value="provider.id"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="模型">
              <el-select
                v-model="form.model"
                filterable
                allow-create
                default-first-option
                placeholder="选择或手动输入一个模型"
                :disabled="!form.providerId"
              >
                <el-option
                  v-for="modelName in availableModels"
                  :key="`${form.providerId}-${modelName}`"
                  :label="modelName"
                  :value="modelName"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="提交风格">
              <el-radio-group v-model="form.style" class="style-group">
                <el-radio-button label="summary">中文总结</el-radio-button>
                <el-radio-button label="conventional">Conventional</el-radio-button>
              </el-radio-group>
            </el-form-item>

            <div class="switch-row">
              <el-checkbox v-model="form.includeBody">AI 一并生成正文</el-checkbox>
              <el-checkbox v-model="form.addAll">提交前执行 `git add -A`</el-checkbox>
            </div>

            <div class="provider-hint">
              <span>当前来源：</span>
              <strong>{{ currentProviderLabel }}</strong>
              <span v-if="form.model"> / {{ form.model }}</span>
            </div>
          </el-form>

          <div class="action-row">
            <template v-if="requiresReduction">
              <el-button
                type="primary"
                :icon="MagicStick"
                :loading="reducing"
                :disabled="!canGenerate || isRunningPrimaryAction"
                @click="startReduction"
              >
                {{ reductionMeta ? '重新拆分' : '开始拆分' }}
              </el-button>
              <el-button
                type="success"
                :icon="Check"
                :loading="generatingAndCommitting"
                :disabled="!canGenerate || isRunningPrimaryAction"
                @click="generateAndCommit"
              >
                生成并提交
              </el-button>
            </template>
            <template v-else>
              <el-button
                type="primary"
                :icon="MagicStick"
                :loading="generating"
                :disabled="!canGenerate || isRunningPrimaryAction"
                @click="generateDraft"
              >
                AI生成
              </el-button>
              <el-button
                type="success"
                :icon="Check"
                :loading="generatingAndCommitting"
                :disabled="!canGenerate || isRunningPrimaryAction"
                @click="generateAndCommit"
              >
                生成并提交
              </el-button>
            </template>
          </div>
          <p v-if="requiresReduction" class="action-caption">
            当前改动超出单轮 AI 总结范围。可以先开始拆分查看草稿，也可以直接一键完成“拆分归纳 + 提交”。
          </p>
        </section>

        <section v-if="devices.length === 0" class="panel-card empty-card">
          <div class="empty-copy">
            <h3>还没有可用设备</h3>
            <p>先去集群管理里添加本地或远程设备，再回来做 AI 提交。</p>
            <el-button type="primary" plain @click="goToCluster">前往集群管理</el-button>
          </div>
        </section>

        <section v-if="lastCommit" class="panel-card success-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">最近一次提交</p>
              <h2>{{ lastCommit.summary }}</h2>
            </div>
            <el-tag type="success" effect="plain">{{ lastCommit.short_hash }}</el-tag>
          </div>
          <p class="success-path">{{ lastCommit.repo_root }}</p>
        </section>
      </aside>

      <section class="result-panel">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">仓库概览</p>
              <h2>当前工作区</h2>
            </div>
            <div class="panel-header-actions">
              <el-button
                text
                :icon="RefreshRight"
                :loading="inspecting"
                :disabled="!canInspect || isRunningPrimaryAction"
                @click="inspectChanges({ silentClean: true })"
              >
                重新读取
              </el-button>
              <el-tag v-if="inspectResult" :type="inspectResult.clean ? 'success' : 'warning'" effect="light">
                {{ inspectResult.clean ? '工作区干净' : `${inspectResult.changed_files.length} 个变更文件` }}
              </el-tag>
            </div>
          </div>

          <div v-if="!inspectResult" class="placeholder-card">
            <p>先选择或切换项目，页面会自动读取当前仓库变更；普通改动可直接生成，超大改动会先进入拆分。</p>
          </div>

          <template v-else>
            <div class="repo-meta-grid">
              <div class="repo-meta-item">
                <span class="meta-label">分支</span>
                <strong>{{ inspectResult.branch }}</strong>
              </div>
              <div class="repo-meta-item">
                <span class="meta-label">根目录</span>
                <strong class="meta-path">{{ inspectResult.repo_root }}</strong>
              </div>
              <div class="repo-meta-item">
                <span class="meta-label">变更文件</span>
                <strong>{{ inspectResult.changed_file_count }}</strong>
              </div>
              <div class="repo-meta-item">
                <span class="meta-label">估算变更行数</span>
                <strong>{{ inspectResult.estimated_changed_line_count }}</strong>
              </div>
            </div>

            <el-alert
              v-if="inspectResult.split_recommended"
              :title="inspectResult.split_reason || '这批改动建议拆成多次提交'"
              :type="inspectResult.oversized ? 'error' : 'warning'"
              :closable="false"
              show-icon
            />

            <div v-if="inspectResult.suggested_split_groups.length" class="split-group-list">
              <div class="inspect-title">建议拆分</div>
              <div
                v-for="group in inspectResult.suggested_split_groups"
                :key="group.label"
                class="split-group-item"
              >
                <div class="split-group-head">
                  <strong>{{ group.label }}</strong>
                  <span>{{ group.file_count }} 个文件</span>
                </div>
                <p v-if="group.sample_paths.length" class="split-group-samples">
                  {{ group.sample_paths.join('，') }}
                </p>
              </div>
            </div>

            <el-alert
              v-if="inspectResult.clean"
              title="当前工作区没有待提交改动"
              type="success"
              :closable="false"
            />

            <div v-else class="inspect-grid">
              <div class="inspect-block">
                <div class="inspect-title">状态</div>
                <pre class="code-block">{{ inspectStatusText }}</pre>
              </div>

              <div class="inspect-block">
                <div class="inspect-title">Diff 统计</div>
                <pre class="code-block">{{ inspectDiffText }}</pre>
              </div>
            </div>

            <div v-if="inspectResult.changed_files.length" class="changed-file-list">
              <div class="inspect-title">变更文件</div>
              <div
                v-for="file in inspectResult.changed_files"
                :key="`${file.status}-${file.path}`"
                class="changed-file-item"
              >
                <span class="changed-file-status" :class="getFileStatusClass(file)">
                  {{ getFileStatusLabel(file) }}
                </span>
                <span class="changed-file-path">{{ file.path }}</span>
              </div>
            </div>
          </template>
        </section>

        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">提交草稿</p>
              <h2>AI 生成结果</h2>
            </div>
            <el-tag v-if="draftModelLabel" type="info" effect="plain">
              {{ draftModelLabel }}
            </el-tag>
          </div>

          <div v-if="activeReductionRun" class="reduction-progress-card">
            <div class="reduction-progress-head">
              <strong>
                {{
                  activeReductionRun.status === 'running'
                    ? (activeReductionRun.auto_commit ? '拆分并提交中' : '分层拆分中')
                    : (activeReductionRun.status === 'failed' ? '分层拆分失败' : (activeReductionRun.auto_commit ? '拆分并提交完成' : '分层拆分完成'))
                }}
              </strong>
              <span>已切分 {{ activeReductionRun.completed_chunk_count }} 次会话</span>
            </div>
            <el-progress :percentage="reductionRunLevelProgressPercent" :stroke-width="8" :show-text="false" />
            <div class="reduction-progress-grid">
              <div class="reduction-progress-item">
                <span>原始单元</span>
                <strong>{{ activeReductionRun.source_unit_count }}</strong>
              </div>
              <div class="reduction-progress-item">
                <span>估算层数</span>
                <strong>{{ activeReductionRun.estimated_level_count || '-' }}</strong>
              </div>
              <div class="reduction-progress-item">
                <span>当前层</span>
                <strong>{{ activeReductionRun.current_level_chunk_count > 0 ? activeReductionRun.current_level_index + 1 : '-' }}</strong>
              </div>
              <div class="reduction-progress-item">
                <span>本层进度</span>
                <strong>{{ activeReductionRun.current_level_completed_chunk_count }} / {{ activeReductionRun.current_level_chunk_count || '-' }}</strong>
              </div>
            </div>
            <p v-if="activeReductionRun.error_message" class="run-error-text">
              {{ activeReductionRun.error_message }}
            </p>
          </div>

          <div v-if="!draftSubject.trim() && !draftBodyText.trim()" class="placeholder-card">
            <p>{{ requiresReduction ? '这批改动超出单轮 AI 总结范围，但仍支持一键“生成并提交”；开始拆分则用于先看草稿。' : 'AI 生成后，这里会展示可编辑的提交标题和正文。' }}</p>
          </div>

          <template v-else>
            <el-alert
              v-if="draftNeedsSplit"
              :title="draftReason || '这批改动可能更适合拆成多次提交'"
              type="warning"
              :closable="false"
              show-icon
            />

            <div v-if="reductionMeta" class="reduction-meta-card">
              <div class="inspect-title reduction-meta-title">分层拆分</div>
              <p v-if="reductionSummary" class="reduction-summary">{{ reductionSummary }}</p>
              <div class="reduction-meta-grid">
                <div class="reduction-meta-item">
                  <span>层级</span>
                  <strong>{{ reductionMeta.level_count }}</strong>
                </div>
                <div class="reduction-meta-item">
                  <span>原始单元</span>
                  <strong>{{ reductionMeta.source_unit_count }}</strong>
                </div>
                <div class="reduction-meta-item">
                  <span>叶子分组</span>
                  <strong>{{ reductionMeta.leaf_chunk_count }}</strong>
                </div>
                <div class="reduction-meta-item">
                  <span>摘要节点</span>
                  <strong>{{ reductionMeta.node_count }}</strong>
                </div>
              </div>
              <div v-if="reductionLevelItems.length" class="reduction-level-list">
                <div
                  v-for="item in reductionLevelItems"
                  :key="item.key"
                  class="reduction-level-item"
                >
                  <div class="reduction-level-main">
                    <strong>{{ item.label }}</strong>
                    <span>{{ item.value }}</span>
                  </div>
                  <div v-if="item.previewNodes.length" class="reduction-preview-list">
                    <div
                      v-for="preview in item.previewNodes"
                      :key="preview.node_id"
                      class="reduction-preview-item"
                    >
                      <strong>{{ preview.candidate_subject || preview.topic || '未命名摘要' }}</strong>
                      <p v-if="preview.summary">{{ preview.summary }}</p>
                      <span>{{ preview.source_ref_count }} 个来源单元</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <el-form label-position="top" class="draft-form">
              <el-form-item label="提交标题">
                <el-input
                  v-model="draftSubject"
                  maxlength="120"
                  show-word-limit
                  placeholder="例如：完善 AI 提交工具的仓库分析流程"
                />
              </el-form-item>

              <el-form-item label="提交正文">
                <el-input
                  v-model="draftBodyText"
                  type="textarea"
                  :rows="6"
                  placeholder="每行一条，会自动格式化成 commit body"
                />
              </el-form-item>
            </el-form>

            <div class="inspect-title">提交预览</div>
            <pre class="code-block preview-block">{{ commitPreview }}</pre>

            <div class="action-row">
              <el-button
                type="primary"
                :icon="Check"
                :loading="committing"
                :disabled="!canCommit || isRunningPrimaryAction"
                @click="commitChanges"
              >
                提交当前草稿
              </el-button>
            </div>
          </template>
        </section>
      </section>
    </div>

    <el-dialog
      v-model="addRepoDialogVisible"
      title="添加项目"
      width="500px"
      destroy-on-close
    >
      <el-form label-position="top">
        <el-form-item label="项目名称">
          <el-input
            v-model="addRepoForm.name"
            clearable
            placeholder="例如 codeyun、pyxllib"
          />
        </el-form-item>

        <el-form-item label="设备">
          <el-select
            v-model="addRepoForm.entryId"
            filterable
            placeholder="选择项目所在设备"
            :disabled="!devices.length"
          >
            <el-option
              v-for="device in devices"
              :key="device.id"
              :label="getDeviceLabel(device)"
              :value="device.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="项目目录">
          <el-input
            v-model="addRepoForm.cwd"
            clearable
            placeholder="例如 D:\\home\\chenkunze\\slns\\codeyun"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="addRepoDialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="savingSavedRepos" @click="submitAddRepo">
            保存项目
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Check, MagicStick, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import { useSortableList } from '@/utils/useSortableList'

import {
  fetchAiGitRepoStatuses,
  fetchAiGitSavedRepos,
  saveAiGitSavedRepos,
  touchAiGitSavedRepo,
  type AiGitRepoStatusItem,
  type AiGitSavedRepo,
} from '@/api/aiGitRepos'
import {
  commitDeviceEntryGit,
  fetchDeviceEntryGitReductionRun,
  generateAndCommitDeviceEntryGit,
  generateDeviceEntryGitMessage,
  inspectDeviceEntryGit,
  startDeviceEntryGitReductionRun,
  type GitChangedFile,
  type GitCommitResponse,
  type GitCommitStyle,
  type GitInspectResponse,
  type GitReductionRunRead,
  type GitReduceResponse,
  type GitReductionMeta,
} from '@/api/aiGitCommit'
import { taskStore, type Device } from '@/store/taskStore'
import { useAiProviderStore } from '@/store/aiProviderStore'
import { useUserStore } from '@/store/userStore'

interface PersistedAiGitCommitForm {
  entryId: string
  cwd: string
  providerId: string
  model: string
  style: GitCommitStyle
  includeBody: boolean
  addAll: boolean
}

interface AddRepoFormState {
  name: string
  entryId: string
  cwd: string
}

const STORAGE_KEY = 'codeyun_ai_git_commit_form_v1'

const router = useRouter()
const userStore = useUserStore()
const aiProviderStore = useAiProviderStore()

function loadPersistedForm(): PersistedAiGitCommitForm {
  const fallback: PersistedAiGitCommitForm = {
    entryId: '',
    cwd: '',
    providerId: '',
    model: '',
    style: 'summary',
    includeBody: true,
    addAll: true,
  }

  if (typeof window === 'undefined' || !window.localStorage) {
    return fallback
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return fallback
    }
    const parsed = JSON.parse(raw) as Partial<PersistedAiGitCommitForm>
    return {
      entryId: typeof parsed.entryId === 'string' ? parsed.entryId : fallback.entryId,
      cwd: typeof parsed.cwd === 'string' ? parsed.cwd : fallback.cwd,
      providerId: typeof parsed.providerId === 'string' ? parsed.providerId : fallback.providerId,
      model: typeof parsed.model === 'string' ? parsed.model : fallback.model,
      style: parsed.style === 'conventional' ? 'conventional' : fallback.style,
      includeBody: typeof parsed.includeBody === 'boolean' ? parsed.includeBody : fallback.includeBody,
      addAll: typeof parsed.addAll === 'boolean' ? parsed.addAll : fallback.addAll,
    }
  } catch {
    return fallback
  }
}

const persistedForm = loadPersistedForm()
const form = reactive<PersistedAiGitCommitForm>({
  ...persistedForm,
})

const inspecting = ref(false)
const generating = ref(false)
const committing = ref(false)
const generatingAndCommitting = ref(false)
const reducing = ref(false)
const inspectResult = ref<GitInspectResponse | null>(null)
const lastCommit = ref<GitCommitResponse | null>(null)
const reductionRun = ref<GitReductionRunRead | null>(null)
const draftSubject = ref('')
const draftBodyText = ref('')
const draftNeedsSplit = ref(false)
const draftReason = ref('')
const draftModelLabel = ref('')
const reductionMeta = ref<GitReductionMeta | null>(null)
const reductionSummary = ref('')
const reductionKeyPoints = ref<string[]>([])
const reductionRiskPoints = ref<string[]>([])
const savedRepos = ref<AiGitSavedRepo[]>([])
const repoStatusMap = ref<Record<string, AiGitRepoStatusItem>>({})
const savingSavedRepos = ref(false)
const repoStatusesLoading = ref(false)
const selectedSavedRepoId = ref('')
const addRepoDialogVisible = ref(false)
const savedRepoListRef = ref<HTMLElement | null>(null)
let reductionRunPollTimer: ReturnType<typeof setInterval> | null = null
let reductionRunPollInFlight = false
const addRepoForm = reactive<AddRepoFormState>({
  name: '',
  entryId: '',
  cwd: '',
})

const devices = computed(() => taskStore.devices)
const providers = computed(() => aiProviderStore.providers)
const selectedDevice = computed(() => devices.value.find(device => device.id === form.entryId) ?? null)
const selectedSavedRepo = computed(() => savedRepos.value.find(repo => repo.id === selectedSavedRepoId.value) ?? null)
const currentProvider = computed(() => aiProviderStore.getProviderById(form.providerId))
const currentProviderLabel = computed(() => currentProvider.value?.label || form.providerId.trim() || '未选择')
const availableModels = computed(() => {
  const items = aiProviderStore.getEffectiveModels(form.providerId)
  if (form.model.trim() && !items.includes(form.model.trim())) {
    return [form.model.trim(), ...items]
  }
  return items
})
const activeReductionRun = computed(() => reductionRun.value)
const reductionRunInProgress = computed(() => activeReductionRun.value?.status === 'running')
const displayedSavedRepos = computed(() =>
  [...savedRepos.value].sort((left, right) => {
    if (left.order_index !== right.order_index) {
      return left.order_index - right.order_index
    }
    return (left.created_at ?? 0) - (right.created_at ?? 0)
  }),
)

const canInspect = computed(() => Boolean(form.entryId && form.cwd.trim()))
const canGenerate = computed(() => Boolean(form.entryId && form.cwd.trim() && form.providerId && form.model.trim()))
const requiresReduction = computed(() => Boolean(inspectResult.value?.oversized))
const isRunningPrimaryAction = computed(() =>
  inspecting.value || generating.value || committing.value || generatingAndCommitting.value || reducing.value || reductionRunInProgress.value,
)
const normalizedBodyLines = computed(() =>
  draftBodyText.value
    .split(/\r?\n/)
    .map(line => line.trim().replace(/^[-*•]\s*/, ''))
    .filter(Boolean),
)
const canCommit = computed(() => Boolean(form.entryId && form.cwd.trim() && draftSubject.value.trim()))
const commitPreview = computed(() => {
  const subject = draftSubject.value.trim()
  if (!subject) {
    return ''
  }
  if (!normalizedBodyLines.value.length) {
    return subject
  }
  return `${subject}\n\n${normalizedBodyLines.value.map(line => `- ${line}`).join('\n')}`
})
const inspectStatusText = computed(() => {
  if (!inspectResult.value) {
    return ''
  }
  const lines = []
  if (inspectResult.value.branch_status.trim()) {
    lines.push(inspectResult.value.branch_status.trim())
  }
  lines.push(...inspectResult.value.status_lines)
  return lines.join('\n') || '(无状态输出)'
})
const inspectDiffText = computed(() => {
  if (!inspectResult.value) {
    return ''
  }
  return [
    '[未暂存]',
    inspectResult.value.diff_stat || '(空)',
    '',
    '[已暂存]',
    inspectResult.value.staged_diff_stat || '(空)',
  ].join('\n')
})
const reductionLevelItems = computed(() =>
  (reductionMeta.value?.levels || []).map(level => ({
    key: `${level.level}-${level.input_kind}`,
    label: level.input_kind === 'source' ? `第 ${level.level + 1} 层 · 原始单元` : `第 ${level.level + 1} 层 · 摘要归并`,
    value: `${level.chunk_count} 组 -> ${level.node_count} 个摘要`,
    previewNodes: level.preview_nodes || [],
  })),
)
const reductionRunLevelProgressPercent = computed(() => {
  const run = activeReductionRun.value
  if (!run || run.current_level_chunk_count <= 0) {
    return 0
  }
  return Math.min(100, Math.round((run.current_level_completed_chunk_count / run.current_level_chunk_count) * 100))
})

watch(
  () => ({
    entryId: form.entryId,
    cwd: form.cwd,
    providerId: form.providerId,
    model: form.model,
    style: form.style,
    includeBody: form.includeBody,
    addAll: form.addAll,
  }),
  value => {
    if (typeof window === 'undefined' || !window.localStorage) {
      return
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  },
  { deep: true },
)

watch(
  () => form.providerId,
  providerId => {
    if (!providerId) {
      form.model = ''
      return
    }
    const providerModels = aiProviderStore.getEffectiveModels(providerId)
    form.model = aiProviderStore.getEffectiveModel(providerId) || providerModels[0] || ''
  },
)

watch(
  () => [form.entryId, form.cwd],
  () => {
    syncSelectedSavedRepoFromForm()
  },
)

useSortableList({
  listRef: savedRepoListRef,
  getDeps: () => [
    displayedSavedRepos.value.length,
    savingSavedRepos.value,
    ...displayedSavedRepos.value.map(repo => repo.id),
  ] as const,
  isEnabled: () => displayedSavedRepos.value.length > 1 && !savingSavedRepos.value,
  ghostClass: 'saved-repo-sortable-ghost',
  onReorder: (oldIndex, newIndex) => reorderSavedRepos(oldIndex, newIndex),
})

onMounted(async () => {
  await Promise.all([
    taskStore.fetchDevices(),
    aiProviderStore.loadProviders(userStore.isAuthenticated),
    loadSavedRepos(),
  ])

  if (!form.entryId || !devices.value.some(device => device.id === form.entryId)) {
    form.entryId = devices.value[0]?.id || ''
  }
  if (!form.providerId || !providers.value.some(provider => provider.id === form.providerId)) {
    form.providerId = aiProviderStore.defaultProviderId || providers.value[0]?.id || ''
  }
  if (!form.model.trim()) {
    form.model = aiProviderStore.getEffectiveModel(form.providerId) || availableModels.value[0] || ''
  }

  syncSelectedSavedRepoFromForm()
  if (!form.cwd.trim() && displayedSavedRepos.value.length) {
    await selectSavedRepo(displayedSavedRepos.value[0], { touch: false })
  }
  if (savedRepos.value.length) {
    await refreshRepoStatuses({ silent: true })
  }
})

onBeforeUnmount(() => {
  stopReductionRunPolling()
})

function getDeviceLabel(device: Device) {
  const modeLabel = device.mode === 'local' ? '本地' : '远程'
  return `${device.name || device.device_id} · ${modeLabel}`
}

function normalizeRepoIdentityKey(entryId: string, cwd: string) {
  return `${entryId.trim().toLowerCase()}::${cwd.trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()}`
}

function inferRepoName(cwd: string) {
  const trimmed = cwd.trim().replace(/[\\/]+$/, '')
  if (!trimmed) {
    return ''
  }
  const parts = trimmed.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] || trimmed
}

function findSavedRepoByLocation(entryId: string, cwd: string) {
  const key = normalizeRepoIdentityKey(entryId, cwd)
  return savedRepos.value.find(repo => normalizeRepoIdentityKey(repo.entry_id, repo.cwd) === key) ?? null
}

function applySavedRepos(items: AiGitSavedRepo[]) {
  savedRepos.value = items
    .map((item, index) => ({
      ...item,
      order_index: Number.isFinite(item.order_index) ? item.order_index : index,
    }))
    .sort((left, right) => {
      if (left.order_index !== right.order_index) {
        return left.order_index - right.order_index
      }
      return (left.created_at ?? 0) - (right.created_at ?? 0)
    })
  const validRepoIds = new Set(items.map(item => item.id))
  repoStatusMap.value = Object.fromEntries(
    Object.entries(repoStatusMap.value).filter(([repoId]) => validRepoIds.has(repoId)),
  )
  syncSelectedSavedRepoFromForm()
}

function syncSelectedSavedRepoFromForm() {
  const matched = findSavedRepoByLocation(form.entryId, form.cwd)
  selectedSavedRepoId.value = matched?.id || ''
}

function clearDraftState() {
  draftSubject.value = ''
  draftBodyText.value = ''
  draftNeedsSplit.value = false
  draftReason.value = ''
  draftModelLabel.value = ''
  reductionMeta.value = null
  reductionSummary.value = ''
  reductionKeyPoints.value = []
  reductionRiskPoints.value = []
}

function resetWorkspaceResult() {
  stopReductionRunPolling()
  reductionRun.value = null
  inspectResult.value = null
  clearDraftState()
}

function stopReductionRunPolling() {
  if (reductionRunPollTimer !== null) {
    clearInterval(reductionRunPollTimer)
    reductionRunPollTimer = null
  }
}

async function refreshReductionRunSilently(entryId: string, runId: string) {
  if (reductionRunPollInFlight) {
    return
  }
  reductionRunPollInFlight = true
  try {
    const nextRun = await fetchDeviceEntryGitReductionRun(entryId, runId)
    reductionRun.value = nextRun
    if (nextRun.status === 'completed') {
      stopReductionRunPolling()
      if (nextRun.result) {
        applyDraftResponse(nextRun.result)
      }
      if (nextRun.commit) {
        lastCommit.value = nextRun.commit
        ElMessage.success(`已拆分归纳并提交：${nextRun.commit.short_hash}`)
        await loadInspectResult({ silentClean: true })
        if (selectedSavedRepoId.value) {
          await markSavedRepoAsUsed(selectedSavedRepoId.value)
        }
      } else {
        ElMessage.success(`已完成分层拆分，共 ${nextRun.level_count || nextRun.estimated_level_count || '-'} 层`)
      }
    } else if (nextRun.status === 'failed') {
      stopReductionRunPolling()
      ElMessage.error(nextRun.error_message || '分层拆分失败')
    }
  } catch (error: any) {
    stopReductionRunPolling()
    ElMessage.error(getErrorMessage(error))
  } finally {
    reductionRunPollInFlight = false
  }
}

function startReductionRunPolling(entryId: string, runId: string) {
  stopReductionRunPolling()
  void refreshReductionRunSilently(entryId, runId)
  reductionRunPollTimer = window.setInterval(() => {
    void refreshReductionRunSilently(entryId, runId)
  }, 1500)
}

function buildSavedRepoStatusFromInspect(repo: AiGitSavedRepo, inspect: GitInspectResponse): AiGitRepoStatusItem {
  return {
    repo_id: repo.id,
    name: repo.name,
    entry_id: repo.entry_id,
    cwd: repo.cwd,
    ok: true,
    clean: inspect.clean,
    branch: inspect.branch,
    branch_status: inspect.branch_status,
    repo_root: inspect.repo_root,
    changed_file_count: inspect.changed_files.length,
    changed_paths: inspect.changed_files.map(file => file.path),
    error: null,
  }
}

function applyInspectToSavedRepo(inspect: GitInspectResponse) {
  const matchedRepo = findSavedRepoByLocation(form.entryId, form.cwd)
  if (!matchedRepo) {
    return
  }

  repoStatusMap.value = {
    ...repoStatusMap.value,
    [matchedRepo.id]: buildSavedRepoStatusFromInspect(matchedRepo, inspect),
  }
}

async function loadSavedRepos() {
  try {
    const response = await fetchAiGitSavedRepos()
    applySavedRepos(response.items)
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
  }
}

async function persistSavedRepos(items: AiGitSavedRepo[], options: { successMessage?: string } = {}) {
  savingSavedRepos.value = true
  try {
    const response = await saveAiGitSavedRepos(items)
    applySavedRepos(response.items)
    if (options.successMessage) {
      ElMessage.success(options.successMessage)
    }
    return response.items
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
    throw error
  } finally {
    savingSavedRepos.value = false
  }
}

function buildOrderedSavedRepos(items: AiGitSavedRepo[]) {
  return items.map((item, index) => ({
    ...item,
    order_index: index,
  }))
}

async function refreshRepoStatuses(options: { repoIds?: string[]; silent?: boolean } = {}) {
  if (!savedRepos.value.length) {
    repoStatusMap.value = {}
    return
  }

  repoStatusesLoading.value = true
  try {
    const response = await fetchAiGitRepoStatuses(
      options.repoIds?.length ? { repo_ids: options.repoIds } : {},
    )
    const nextMap = { ...repoStatusMap.value }
    for (const item of response.items) {
      nextMap[item.repo_id] = item
    }
    repoStatusMap.value = nextMap
    if (!options.silent) {
      ElMessage.success('已刷新项目状态')
    }
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    repoStatusesLoading.value = false
  }
}

async function markSavedRepoAsUsed(repoId: string) {
  try {
    const response = await touchAiGitSavedRepo(repoId)
    if (!response.item) {
      return
    }
    applySavedRepos(
      savedRepos.value.map(repo => (repo.id === response.item?.id ? response.item : repo)),
    )
    selectedSavedRepoId.value = repoId
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
  }
}

async function reorderSavedRepos(oldIndex: number, newIndex: number) {
  if (
    oldIndex < 0
    || newIndex < 0
    || oldIndex >= displayedSavedRepos.value.length
    || newIndex >= displayedSavedRepos.value.length
    || oldIndex === newIndex
  ) {
    return
  }

  const previous = [...displayedSavedRepos.value]
  const nextItems = [...displayedSavedRepos.value]
  const [movedRepo] = nextItems.splice(oldIndex, 1)
  if (!movedRepo) {
    return
  }
  nextItems.splice(newIndex, 0, movedRepo)
  const orderedItems = buildOrderedSavedRepos(nextItems)

  applySavedRepos(orderedItems)
  try {
    await persistSavedRepos(orderedItems)
  } catch {
    applySavedRepos(previous)
  }
}

function getSavedRepoDeviceLabel(repo: AiGitSavedRepo) {
  const device = devices.value.find(item => item.id === repo.entry_id)
  return device ? getDeviceLabel(device) : repo.entry_id
}

function getSavedRepoStatusLabel(repoId: string) {
  const status = repoStatusMap.value[repoId]
  if (!status) {
    return '未检测'
  }
  if (!status.ok) {
    return '检测失败'
  }
  if (status.clean) {
    return '工作区干净'
  }
  return `${status.changed_file_count} 个改动`
}

function getSavedRepoStatusType(repoId: string) {
  const status = repoStatusMap.value[repoId]
  if (!status) {
    return 'info'
  }
  if (!status.ok) {
    return 'danger'
  }
  return status.clean ? 'success' : 'warning'
}

function openAddRepoDialog(prefillFromCurrent = false) {
  const initialEntryId = form.entryId || devices.value[0]?.id || ''
  const initialCwd = prefillFromCurrent ? form.cwd.trim() : form.cwd.trim()

  addRepoForm.name = inferRepoName(initialCwd)
  addRepoForm.entryId = initialEntryId
  addRepoForm.cwd = initialCwd
  addRepoDialogVisible.value = true
}

async function submitAddRepo() {
  const entryId = addRepoForm.entryId.trim()
  const cwd = addRepoForm.cwd.trim()
  const name = addRepoForm.name.trim() || inferRepoName(cwd)

  if (!entryId) {
    ElMessage.warning('请先为项目选择设备')
    return
  }
  if (!cwd) {
    ElMessage.warning('请先填写项目目录')
    return
  }

  const duplicate = findSavedRepoByLocation(entryId, cwd)
  if (duplicate) {
    addRepoDialogVisible.value = false
    await selectSavedRepo(duplicate)
    ElMessage.info('这个项目已经在列表里了，已帮你切换过去')
    return
  }

  const nextItems = [
    ...displayedSavedRepos.value,
    {
      id: '',
      name: name || inferRepoName(cwd) || cwd,
      entry_id: entryId,
      cwd,
      pinned: false,
      order_index: displayedSavedRepos.value.length,
      created_at: null,
      updated_at: null,
      last_used_at: null,
    },
  ]

  const updatedItems = await persistSavedRepos(buildOrderedSavedRepos(nextItems), { successMessage: '已添加项目' })
  addRepoDialogVisible.value = false
  const addedRepo = updatedItems.find(repo => normalizeRepoIdentityKey(repo.entry_id, repo.cwd) === normalizeRepoIdentityKey(entryId, cwd))
  if (addedRepo) {
    await selectSavedRepo(addedRepo)
    await refreshRepoStatuses({ repoIds: [addedRepo.id], silent: true })
  }
}

async function removeSavedRepo(repo: AiGitSavedRepo) {
  try {
    await ElMessageBox.confirm(
      `确定要从项目库中移除「${repo.name}」吗？`,
      '确认移除项目',
      {
        confirmButtonText: '移除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }

  const nextItems = buildOrderedSavedRepos(displayedSavedRepos.value.filter(item => item.id !== repo.id))
  await persistSavedRepos(nextItems, { successMessage: '已移除项目' })

  const nextStatusMap = { ...repoStatusMap.value }
  delete nextStatusMap[repo.id]
  repoStatusMap.value = nextStatusMap

  if (selectedSavedRepoId.value === repo.id) {
    selectedSavedRepoId.value = ''
  }
}

async function selectSavedRepo(repo: AiGitSavedRepo, options: { touch?: boolean } = {}) {
  if (isRunningPrimaryAction.value) {
    return
  }
  selectedSavedRepoId.value = repo.id
  form.entryId = repo.entry_id
  form.cwd = repo.cwd
  resetWorkspaceResult()
  if (options.touch !== false) {
    await markSavedRepoAsUsed(repo.id)
  }
  await inspectChanges({ silentClean: true })
}

function handleProviderChange(providerId: string) {
  form.providerId = providerId
  form.model = aiProviderStore.getEffectiveModel(providerId) || availableModels.value[0] || ''
}

function getFileStatusLabel(file: GitChangedFile) {
  if (file.untracked) {
    return '未跟踪'
  }
  if (file.staged && file.unstaged) {
    return '已暂存 + 未暂存'
  }
  if (file.staged) {
    return '已暂存'
  }
  return '未暂存'
}

function getFileStatusClass(file: GitChangedFile) {
  if (file.untracked) {
    return 'is-untracked'
  }
  if (file.staged && file.unstaged) {
    return 'is-mixed'
  }
  if (file.staged) {
    return 'is-staged'
  }
  return 'is-unstaged'
}

function buildAiConnectionPayload() {
  if (!form.providerId) {
    return {
      provider: null,
      base_url: null,
      api_key: null,
    }
  }
  const payload = aiProviderStore.buildConnectionPayload(form.providerId)
  return {
    provider: form.providerId,
    base_url: payload.base_url || null,
    api_key: payload.api_key || null,
  }
}

async function loadInspectResult(options: { silentClean?: boolean } = {}) {
  const nextInspect = await inspectDeviceEntryGit(form.entryId, {
    cwd: form.cwd.trim(),
  })
  inspectResult.value = nextInspect
  applyInspectToSavedRepo(nextInspect)
  if (nextInspect.clean && !options.silentClean) {
    ElMessage.success('当前工作区是干净的')
  }
  return nextInspect
}

async function inspectChanges(options: { silentClean?: boolean } = {}) {
  if (!canInspect.value) {
    ElMessage.warning('请先选择设备并填写项目目录')
    return
  }

  inspecting.value = true
  try {
    await loadInspectResult(options)
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    inspecting.value = false
  }
}

async function generateDraft() {
  if (!canGenerate.value) {
    ElMessage.warning('请先补全设备、项目目录、AI 来源和模型')
    return
  }

  generating.value = true
  draftNeedsSplit.value = false
  draftReason.value = ''
  try {
    const latestInspect = await loadInspectResult({ silentClean: true })
    if (latestInspect.clean) {
      ElMessage.success('当前工作区是干净的')
      return
    }
    if (latestInspect.oversized) {
      ElMessage.warning('当前改动超出单轮 AI 总结范围，请先开始拆分')
      return
    }

    const aiPayload = buildAiConnectionPayload()
    const response = await generateDeviceEntryGitMessage(form.entryId, {
      cwd: form.cwd.trim(),
      provider: aiPayload.provider,
      base_url: aiPayload.base_url,
      api_key: aiPayload.api_key,
      model: form.model.trim(),
      style: form.style,
      include_body: form.includeBody,
      max_files: 8,
    })
    applyDraftResponse(response)
    if (response.needs_split) {
      ElMessage.warning(response.reason || 'AI 认为这批改动更适合拆分提交')
    } else {
      ElMessage.success('已生成提交草稿')
    }
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    generating.value = false
  }
}

async function generateAndCommit() {
  if (!canGenerate.value) {
    ElMessage.warning('请先补全设备、项目目录、AI 来源和模型')
    return
  }

  generatingAndCommitting.value = true
  draftNeedsSplit.value = false
  draftReason.value = ''
  try {
    const latestInspect = await loadInspectResult({ silentClean: true })
    if (latestInspect.clean) {
      ElMessage.success('当前工作区是干净的')
      return
    }

    const aiPayload = buildAiConnectionPayload()
    if (latestInspect.oversized) {
      const run = await startDeviceEntryGitReductionRun(form.entryId, {
        cwd: form.cwd.trim(),
        provider: aiPayload.provider,
        base_url: aiPayload.base_url,
        api_key: aiPayload.api_key,
        model: form.model.trim(),
        style: form.style,
        include_body: form.includeBody,
        branch_factor: 10,
        auto_commit: true,
        add_all: form.addAll,
      })
      reductionRun.value = run
      startReductionRunPolling(form.entryId, run.id)
      ElMessage.success('已开始拆分归纳并提交')
    } else {
      const response = await generateAndCommitDeviceEntryGit(form.entryId, {
        cwd: form.cwd.trim(),
        provider: aiPayload.provider,
        base_url: aiPayload.base_url,
        api_key: aiPayload.api_key,
        model: form.model.trim(),
        style: form.style,
        include_body: form.includeBody,
        max_files: 8,
        add_all: form.addAll,
      })
      applyDraftResponse(response)
      lastCommit.value = response.commit
      ElMessage.success(`已生成并提交：${response.commit.short_hash}`)
      await loadInspectResult({ silentClean: true })
      if (selectedSavedRepoId.value) {
        await markSavedRepoAsUsed(selectedSavedRepoId.value)
      }
    }
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    generatingAndCommitting.value = false
  }
}

function applyDraftResponse(response: {
  inspect: GitInspectResponse
  subject: string
  body: string[]
  needs_split: boolean
  reason: string
  model: string
}) {
  inspectResult.value = response.inspect
  applyInspectToSavedRepo(response.inspect)
  draftSubject.value = response.subject
  draftBodyText.value = response.body.join('\n')
  draftNeedsSplit.value = response.needs_split
  draftReason.value = response.reason
  draftModelLabel.value = response.model
  if (!('reduction' in response)) {
    reductionMeta.value = null
    reductionSummary.value = ''
    reductionKeyPoints.value = []
    reductionRiskPoints.value = []
    return
  }
  const reductionResponse = response as GitReduceResponse
  reductionMeta.value = reductionResponse.reduction
  reductionSummary.value = reductionResponse.summary || reductionResponse.topic || ''
  reductionKeyPoints.value = reductionResponse.key_points || []
  reductionRiskPoints.value = reductionResponse.risk_points || []
}

async function startReduction() {
  if (!canGenerate.value) {
    ElMessage.warning('请先补全设备、项目目录、AI 来源和模型')
    return
  }

  reducing.value = true
  draftNeedsSplit.value = false
  draftReason.value = ''
  try {
    const latestInspect = await loadInspectResult({ silentClean: true })
    if (latestInspect.clean) {
      ElMessage.success('当前工作区是干净的')
      return
    }
    if (!latestInspect.oversized) {
      ElMessage.info('当前改动已经回到普通规模，可以直接使用 AI生成')
      return
    }

    const aiPayload = buildAiConnectionPayload()
    const run = await startDeviceEntryGitReductionRun(form.entryId, {
      cwd: form.cwd.trim(),
      provider: aiPayload.provider,
      base_url: aiPayload.base_url,
      api_key: aiPayload.api_key,
      model: form.model.trim(),
      style: form.style,
      include_body: form.includeBody,
      branch_factor: 10,
      auto_commit: false,
      add_all: form.addAll,
    })
    reductionRun.value = run
    startReductionRunPolling(form.entryId, run.id)
    ElMessage.success('已开始分层拆分')
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    reducing.value = false
  }
}

async function commitChanges() {
  if (!canCommit.value) {
    ElMessage.warning('请先生成或填写提交标题')
    return
  }

  const confirmText = [
    `设备：${selectedDevice.value ? getDeviceLabel(selectedDevice.value) : '未选择'}`,
    `目录：${form.cwd.trim()}`,
    '',
    commitPreview.value,
  ].join('\n')

  try {
    await ElMessageBox.confirm(confirmText, '确认执行 Git 提交', {
      confirmButtonText: '提交',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  committing.value = true
  try {
    const response = await commitDeviceEntryGit(form.entryId, {
      cwd: form.cwd.trim(),
      subject: draftSubject.value.trim(),
      body: normalizedBodyLines.value,
      add_all: form.addAll,
    })
    lastCommit.value = response
    ElMessage.success(`提交成功：${response.short_hash}`)
    await loadInspectResult({ silentClean: true })
    if (selectedSavedRepoId.value) {
      await markSavedRepoAsUsed(selectedSavedRepoId.value)
    }
  } catch (error: any) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    committing.value = false
  }
}

function goToCluster() {
  void router.push('/cluster/tasks')
}

function getErrorMessage(error: any) {
  return error?.response?.data?.detail || error?.message || '操作失败'
}
</script>

<style scoped>
.ai-git-commit-page {
  padding: 32px;
  background:
    radial-gradient(circle at top left, rgba(64, 158, 255, 0.12), transparent 28%),
    radial-gradient(circle at bottom right, rgba(103, 194, 58, 0.12), transparent 26%),
    linear-gradient(180deg, #f5f7fb 0%, #eef2f8 100%);
  min-height: 100%;
}

.hero-panel {
  background: linear-gradient(135deg, #0f172a 0%, #1d4ed8 58%, #1f9d7a 100%);
  color: #fff;
  border-radius: 28px;
  padding: 28px 32px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
}

.hero-copy {
  max-width: 860px;
}

.hero-copy h1 {
  margin: 10px 0 12px;
  font-size: 36px;
  line-height: 1.08;
}

.hero-copy p {
  margin: 0;
  max-width: 760px;
  color: rgba(255, 255, 255, 0.82);
  font-size: 15px;
  line-height: 1.7;
}

.eyebrow,
.panel-kicker {
  text-transform: uppercase;
  letter-spacing: 0.16em;
  font-size: 12px;
  font-weight: 700;
  opacity: 0.78;
}

.workspace-grid {
  margin-top: 24px;
  display: grid;
  grid-template-columns: minmax(320px, 380px) minmax(0, 1fr);
  gap: 20px;
}

.control-panel,
.result-panel {
  display: grid;
  gap: 20px;
  align-content: start;
}

.panel-card {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 24px;
  padding: 22px 24px;
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(10px);
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.panel-header-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.panel-header h2 {
  margin: 6px 0 0;
  font-size: 22px;
  line-height: 1.2;
  color: #0f172a;
}

.selected-repo-banner {
  display: grid;
  gap: 6px;
  padding: 14px 16px;
  margin-bottom: 18px;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.09), rgba(15, 118, 110, 0.1));
  color: #0f172a;
  font-size: 13px;
}

.selected-repo-banner-path {
  color: #475569;
  word-break: break-all;
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.style-group {
  width: 100%;
}

.style-group :deep(.el-radio-button) {
  flex: 1;
}

.style-group :deep(.el-radio-button__inner) {
  width: 100%;
}

.switch-row {
  display: grid;
  gap: 12px;
  margin: 8px 0 14px;
}

.provider-hint {
  padding: 12px 14px;
  border-radius: 14px;
  background: #f8fafc;
  color: #475569;
  font-size: 13px;
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 18px;
}

.action-caption {
  margin: 10px 2px 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
}

.empty-card,
.success-card {
  border-style: dashed;
}

.empty-copy h3 {
  margin: 0 0 8px;
  color: #0f172a;
}

.empty-copy p {
  margin: 0 0 16px;
  color: #64748b;
  line-height: 1.7;
}

.success-path {
  margin: 0;
  color: #0f766e;
  word-break: break-all;
}

.placeholder-card {
  min-height: 180px;
  display: grid;
  place-items: center;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.4);
  background: rgba(248, 250, 252, 0.7);
  color: #64748b;
  text-align: center;
  padding: 24px;
}

.repo-library-placeholder {
  min-height: 140px;
}

.saved-repo-list {
  display: grid;
  gap: 12px;
}

.saved-repo-item {
  padding: 16px;
  border-radius: 18px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.9), rgba(255, 255, 255, 0.96));
  cursor: pointer;
  transition:
    transform 0.16s ease,
    border-color 0.16s ease,
    box-shadow 0.16s ease;
}

.saved-repo-item:hover {
  transform: translateY(-1px);
  border-color: rgba(37, 99, 235, 0.32);
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.08);
}

.saved-repo-item.is-active {
  border-color: rgba(37, 99, 235, 0.52);
  box-shadow: 0 14px 30px rgba(29, 78, 216, 0.14);
  background: linear-gradient(180deg, rgba(239, 246, 255, 0.95), rgba(255, 255, 255, 0.98));
}

.saved-repo-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.saved-repo-top-main {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.saved-repo-top-side {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex: 0 0 auto;
}

.saved-repo-handle {
  display: inline-flex;
  flex: 0 0 auto;
}

.saved-repo-top strong {
  color: #0f172a;
  font-size: 15px;
  min-width: 0;
  word-break: break-word;
}

.saved-repo-meta {
  margin-top: 8px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
}

.saved-repo-path {
  margin: 10px 0 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}

.saved-repo-error {
  margin: 10px 0 0;
  color: #b91c1c;
  font-size: 12px;
  line-height: 1.6;
}

.saved-repo-sortable-ghost {
  opacity: 0.75;
  background: rgba(219, 234, 254, 0.9);
}

.repo-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.repo-meta-item {
  border-radius: 18px;
  background: #f8fafc;
  padding: 16px;
  display: grid;
  gap: 8px;
}

.split-group-list {
  margin-top: 18px;
}

.split-group-item {
  padding: 14px 16px;
  border-radius: 16px;
  background: #f8fafc;
  border: 1px solid rgba(226, 232, 240, 0.9);
}

.split-group-item + .split-group-item {
  margin-top: 10px;
}

.split-group-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #0f172a;
}

.split-group-head span {
  color: #475569;
  font-size: 12px;
}

.split-group-samples {
  margin: 8px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
  word-break: break-all;
}

.meta-label {
  color: #64748b;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.meta-path {
  word-break: break-all;
}

.inspect-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 18px;
}

.inspect-block {
  min-width: 0;
}

.inspect-title {
  margin: 18px 0 10px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #334155;
}

.code-block {
  margin: 0;
  padding: 16px;
  border-radius: 18px;
  background: #0f172a;
  color: #e2e8f0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.65;
}

.changed-file-list {
  margin-top: 18px;
}

.changed-file-item {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.9);
}

.changed-file-item:last-child {
  border-bottom: none;
}

.changed-file-status {
  display: inline-flex;
  justify-content: center;
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 700;
}

.changed-file-status.is-untracked {
  background: rgba(59, 130, 246, 0.14);
  color: #1d4ed8;
}

.changed-file-status.is-staged {
  background: rgba(34, 197, 94, 0.14);
  color: #15803d;
}

.changed-file-status.is-unstaged {
  background: rgba(249, 115, 22, 0.14);
  color: #c2410c;
}

.changed-file-status.is-mixed {
  background: rgba(168, 85, 247, 0.14);
  color: #7e22ce;
}

.changed-file-path {
  min-width: 0;
  word-break: break-all;
  color: #0f172a;
}

.draft-form {
  margin-top: 16px;
}

.reduction-progress-card {
  margin-top: 16px;
  padding: 16px;
  border-radius: 18px;
  background: #f8fafc;
  border: 1px solid rgba(226, 232, 240, 0.9);
}

.reduction-progress-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}

.reduction-progress-head strong {
  color: #0f172a;
  font-size: 14px;
}

.reduction-progress-head span {
  color: #64748b;
  font-size: 12px;
}

.reduction-progress-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.reduction-progress-item {
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
}

.reduction-progress-item span {
  color: #64748b;
  font-size: 12px;
}

.reduction-progress-item strong {
  color: #0f172a;
}

.reduction-meta-card {
  margin-top: 16px;
  padding: 16px;
  border-radius: 18px;
  background: #f8fafc;
  border: 1px solid rgba(226, 232, 240, 0.9);
}

.reduction-meta-title {
  margin-top: 0;
}

.reduction-summary {
  margin: 0 0 12px;
  color: #334155;
  line-height: 1.7;
}

.reduction-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.reduction-meta-item {
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
}

.reduction-meta-item span {
  color: #64748b;
  font-size: 12px;
}

.reduction-meta-item strong {
  color: #0f172a;
}

.reduction-level-list {
  margin-top: 12px;
  display: grid;
  gap: 8px;
}

.reduction-level-item {
  display: grid;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
  color: #334155;
}

.reduction-level-main {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.reduction-level-item strong {
  color: #0f172a;
  font-size: 13px;
}

.reduction-level-main span {
  color: #64748b;
  font-size: 12px;
  text-align: right;
}

.reduction-preview-list {
  display: grid;
  gap: 8px;
}

.reduction-preview-item {
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 12px;
  background: #f8fafc;
}

.reduction-preview-item strong {
  font-size: 12px;
}

.reduction-preview-item p {
  margin: 0;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
}

.reduction-preview-item span {
  color: #94a3b8;
  font-size: 11px;
}

.run-error-text {
  margin: 12px 0 0;
  color: #dc2626;
  font-size: 13px;
  line-height: 1.6;
}

.preview-block {
  margin-top: 10px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

@media (max-width: 1080px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }

  .repo-meta-grid,
  .inspect-grid,
  .reduction-meta-grid,
  .reduction-progress-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .ai-git-commit-page {
    padding: 20px;
  }

  .hero-panel,
  .panel-card {
    padding: 20px;
    border-radius: 20px;
  }

  .hero-copy h1 {
    font-size: 30px;
  }

  .panel-header,
  .saved-repo-top {
    flex-direction: column;
  }

  .panel-header-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .changed-file-item {
    grid-template-columns: 1fr;
  }
}
</style>

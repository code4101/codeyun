<template>
  <div class="ai-git-commit-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <div class="eyebrow">AI工具 / AI提交</div>
        <h1>AI提交</h1>
        <p>选择设备与仓库目录后，先读取 Git 变更，再用当前 AI 配置生成提交标题和正文，确认后执行 commit。</p>
      </div>
    </section>

    <div class="workspace-grid">
      <aside class="control-panel">
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
              <strong>{{ currentProvider?.label || '未选择' }}</strong>
              <span v-if="form.model"> / {{ form.model }}</span>
            </div>
          </el-form>

          <div class="action-row">
            <el-button
              :icon="RefreshRight"
              :loading="inspecting"
              :disabled="!canInspect"
              @click="inspectChanges"
            >
              读取变更
            </el-button>
            <el-button
              type="primary"
              :icon="MagicStick"
              :loading="generating"
              :disabled="!canGenerate"
              @click="generateDraft"
            >
              AI 生成
            </el-button>
          </div>
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
            <el-tag v-if="inspectResult" :type="inspectResult.clean ? 'success' : 'warning'" effect="light">
              {{ inspectResult.clean ? '工作区干净' : `${inspectResult.changed_files.length} 个变更文件` }}
            </el-tag>
          </div>

          <div v-if="!inspectResult" class="placeholder-card">
            <p>先选择设备和项目目录，然后点击“读取变更”或直接“AI 生成”。</p>
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

          <div v-if="!draftSubject.trim() && !draftBodyText.trim()" class="placeholder-card">
            <p>AI 生成后，这里会展示可编辑的提交标题和正文。</p>
          </div>

          <template v-else>
            <el-alert
              v-if="draftNeedsSplit"
              :title="draftReason || '这批改动可能更适合拆成多次提交'"
              type="warning"
              :closable="false"
              show-icon
            />

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
                :disabled="!canCommit"
                @click="commitChanges"
              >
                执行提交
              </el-button>
            </div>
          </template>
        </section>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Check, MagicStick, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  commitDeviceEntryGit,
  generateDeviceEntryGitMessage,
  inspectDeviceEntryGit,
  type GitChangedFile,
  type GitCommitResponse,
  type GitCommitStyle,
  type GitInspectResponse,
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
const inspectResult = ref<GitInspectResponse | null>(null)
const lastCommit = ref<GitCommitResponse | null>(null)
const draftSubject = ref('')
const draftBodyText = ref('')
const draftNeedsSplit = ref(false)
const draftReason = ref('')
const draftModelLabel = ref('')

const devices = computed(() => taskStore.devices)
const providers = computed(() => aiProviderStore.providers)
const selectedDevice = computed(() => devices.value.find(device => device.id === form.entryId) ?? null)
const currentProvider = computed(() => aiProviderStore.getProviderById(form.providerId))
const availableModels = computed(() => {
  const items = aiProviderStore.getEffectiveModels(form.providerId)
  if (form.model.trim() && !items.includes(form.model.trim())) {
    return [form.model.trim(), ...items]
  }
  return items
})

const canInspect = computed(() => Boolean(form.entryId && form.cwd.trim()))
const canGenerate = computed(() => Boolean(form.entryId && form.cwd.trim() && form.providerId && form.model.trim()))
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

onMounted(async () => {
  await Promise.all([
    taskStore.fetchDevices(),
    aiProviderStore.loadProviders(userStore.isAuthenticated),
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
})

function getDeviceLabel(device: Device) {
  const modeLabel = device.mode === 'local' ? '本地' : '远程'
  return `${device.name || device.device_id} · ${modeLabel}`
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

async function inspectChanges(options: { silentClean?: boolean } = {}) {
  if (!canInspect.value) {
    ElMessage.warning('请先选择设备并填写项目目录')
    return
  }

  inspecting.value = true
  try {
    const nextInspect = await inspectDeviceEntryGit(form.entryId, {
      cwd: form.cwd.trim(),
    })
    inspectResult.value = nextInspect
    if (nextInspect.clean && !options.silentClean) {
      ElMessage.success('当前工作区是干净的')
    }
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
    inspectResult.value = response.inspect
    draftSubject.value = response.subject
    draftBodyText.value = response.body.join('\n')
    draftNeedsSplit.value = response.needs_split
    draftReason.value = response.reason
    draftModelLabel.value = response.model
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
    await inspectChanges({ silentClean: true })
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

.panel-header h2 {
  margin: 6px 0 0;
  font-size: 22px;
  line-height: 1.2;
  color: #0f172a;
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

.preview-block {
  margin-top: 10px;
}

@media (max-width: 1080px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }

  .repo-meta-grid,
  .inspect-grid {
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

  .changed-file-item {
    grid-template-columns: 1fr;
  }
}
</style>

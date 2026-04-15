<template>
  <div class="ai-git-commit-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <div class="eyebrow">AI工具 / AI提交</div>
        <h1>AI提交</h1>
        <p>切换项目后会自动同步 Git 变更；普通改动直接生成草稿，超大改动会先进入分层拆分。</p>
      </div>
    </section>

    <div class="page-stack">
      <div class="top-grid">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">执行范围</p>
              <h2>仓库与模型</h2>
            </div>
            <div class="panel-header-actions">
              <el-button type="primary" plain @click="openAddRepoDialog()">
                添加项目
              </el-button>
            </div>
          </div>

          <el-form label-position="top" class="settings-form">
            <div class="settings-grid">
              <el-form-item label="设备">
                <el-select
                  v-model="form.entryId"
                  filterable
                  placeholder="先选择一个设备"
                  :disabled="!devices.length"
                  @change="handleEntryChange"
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
                <el-select
                  v-model="selectedSavedRepoId"
                  filterable
                  clearable
                  placeholder="从已添加项目中选择"
                  :disabled="!projectRepoOptions.length"
                  @change="handleProjectRepoChange"
                >
                  <el-option
                    v-for="repo in projectRepoOptions"
                    :key="repo.id"
                    :label="formatProjectRepoOptionLabel(repo)"
                    :value="repo.id"
                  />
                </el-select>
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
            </div>

            <div class="switch-row">
              <el-checkbox v-model="form.includeBody">AI 一并生成正文</el-checkbox>
              <el-checkbox v-model="form.addAll">提交前执行 `git add -A`</el-checkbox>
            </div>
          </el-form>

          <div v-if="devices.length === 0" class="inline-empty-state">
            <div class="empty-copy">
              <h3>还没有可用设备</h3>
              <p>先去集群管理里添加本地或远程设备，再回来做 AI 提交。</p>
              <el-button type="primary" plain @click="goToCluster">前往集群管理</el-button>
            </div>
          </div>

          <div v-if="lastCommit" class="inline-success-card">
            <div class="inline-success-head">
              <div>
                <p class="panel-kicker">最近一次提交</p>
                <h3>{{ lastCommit.summary }}</h3>
              </div>
              <el-tag type="success" effect="plain">{{ lastCommit.short_hash }}</el-tag>
            </div>
            <p class="success-path">{{ lastCommit.repo_root }}</p>
          </div>
        </section>
      </div>

      <section class="panel-card changes-panel">
        <div class="panel-header">
          <div>
            <p class="panel-kicker">工作区变更</p>
            <h2>当前改动</h2>
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
              {{ inspectResult.clean ? '工作区干净' : `${inspectResult.changed_file_count} 个变更文件` }}
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
              <span class="meta-label">变更文件</span>
              <strong>{{ inspectResult.changed_file_count }}</strong>
            </div>
            <div class="repo-meta-item is-added">
              <span class="meta-label">新增行数</span>
              <strong class="meta-value-added">{{ inspectResult.added_line_count }}</strong>
            </div>
            <div class="repo-meta-item is-deleted">
              <span class="meta-label">删除行数</span>
              <strong class="meta-value-deleted">{{ inspectResult.deleted_line_count }}</strong>
            </div>
          </div>

          <section class="history-card">
            <div class="history-card-header">
              <div class="history-card-copy">
                <strong>提交工作量趋势</strong>
                <span>{{ historySummaryText }}</span>
                <p v-if="historyError" class="history-error-text">{{ historyError }}</p>
              </div>

              <div class="history-card-actions">
                <div class="history-range-list">
                  <button
                    v-for="option in HISTORY_RANGE_OPTIONS"
                    :key="option.days"
                    type="button"
                    class="history-range-chip"
                    :class="{ 'is-active': option.days === historyRangeDays }"
                    :disabled="historyLoading"
                    @click="updateHistoryRangeDays(option.days)"
                  >
                    {{ option.label }}
                  </button>
                </div>
                <el-button
                  text
                  :icon="RefreshRight"
                  :loading="historyLoading"
                  :disabled="!canInspect || isRunningPrimaryAction"
                  @click="loadHistoryStats()"
                >
                  刷新走势
                </el-button>
              </div>
            </div>

            <div v-if="historyChartModel?.hasActivity" class="history-chart-shell">
              <svg class="history-chart" :viewBox="historyChartModel.viewBox" preserveAspectRatio="none" aria-hidden="true">
                <defs>
                  <linearGradient :id="`${historyChartIdPrefix}-added`" x1="0%" x2="0%" y1="0%" y2="100%">
                    <stop offset="0%" stop-color="#2563eb" stop-opacity="0.44" />
                    <stop offset="100%" stop-color="#2563eb" stop-opacity="0.08" />
                  </linearGradient>
                  <linearGradient :id="`${historyChartIdPrefix}-deleted`" x1="0%" x2="0%" y1="0%" y2="100%">
                    <stop offset="0%" stop-color="#ef4444" stop-opacity="0.4" />
                    <stop offset="100%" stop-color="#ef4444" stop-opacity="0.06" />
                  </linearGradient>
                </defs>

                <g class="history-chart-guides">
                  <line
                    v-for="guide in historyChartModel.yGuides"
                    :key="guide.key"
                    :x1="HISTORY_CHART_PADDING.left"
                    :x2="historyChartModel.chartWidth - HISTORY_CHART_PADDING.right"
                    :y1="guide.y"
                    :y2="guide.y"
                    class="history-chart-guide-line"
                  />
                  <text
                    v-for="guide in historyChartModel.yGuides"
                    :key="`${guide.key}-label`"
                    :x="historyChartModel.chartWidth - 4"
                    :y="guide.y - 6"
                    class="history-chart-guide-text"
                    text-anchor="end"
                  >
                    {{ guide.label }}
                  </text>
                </g>

                <line
                  :x1="HISTORY_CHART_PADDING.left"
                  :x2="historyChartModel.chartWidth - HISTORY_CHART_PADDING.right"
                  :y1="historyChartModel.bottomY"
                  :y2="historyChartModel.bottomY"
                  class="history-chart-baseline"
                />
                <path
                  :d="historyChartModel.deletedAreaPath"
                  :fill="`url(#${historyChartIdPrefix}-deleted)`"
                  class="history-chart-area"
                />
                <path
                  :d="historyChartModel.addedAreaPath"
                  :fill="`url(#${historyChartIdPrefix}-added)`"
                  class="history-chart-area"
                />
                <path :d="historyChartModel.deletedLinePath" class="history-chart-line is-deleted" />
                <path :d="historyChartModel.totalLinePath" class="history-chart-line is-total" />

                <text
                  v-for="tick in historyChartModel.xTicks"
                  :key="tick.key"
                  :x="tick.x"
                  :y="historyChartModel.chartHeight - 8"
                  class="history-chart-tick-text"
                  text-anchor="middle"
                >
                  {{ tick.label }}
                </text>
              </svg>

              <div class="history-legend-list">
                <div class="history-legend-item is-added">
                  <span class="history-legend-dot" />
                  <strong>新增 {{ formatHistoryNumber(historyStats?.total_added_line_count || 0) }}</strong>
                </div>
                <div class="history-legend-item is-deleted">
                  <span class="history-legend-dot" />
                  <strong>删除 {{ formatHistoryNumber(historyStats?.total_deleted_line_count || 0) }}</strong>
                </div>
                <div class="history-legend-item">
                  <span class="history-legend-dot is-neutral" />
                  <strong>提交 {{ formatHistoryNumber(historyStats?.total_commit_count || 0) }}</strong>
                </div>
                <div class="history-legend-item">
                  <span class="history-legend-dot is-peak" />
                  <strong>单日峰值 {{ formatHistoryNumber(historyChartModel.peakChurn) }}</strong>
                </div>
              </div>
            </div>

            <div v-else class="history-placeholder-card">
              <p v-if="historyLoading">正在读取提交历史…</p>
              <p v-else>最近 {{ historyRangeDays }} 天没有可展示的提交工作量。</p>
            </div>
          </section>

          <el-alert
            v-if="inspectResult.clean"
            title="当前工作区没有待提交改动"
            type="success"
            :closable="false"
          />

          <div v-else class="changes-workbench">
            <div class="changes-pane changes-file-pane">
              <div class="changes-pane-header">
                <div>
                  <strong>变更文件</strong>
                </div>
                <span class="changes-pane-count">{{ inspectResult.changed_file_count }}</span>
              </div>

              <div class="changes-file-list">
                <button
                  v-for="file in sortedChangedFiles"
                  :key="`${file.status}-${file.path}`"
                  type="button"
                  class="changed-file-item"
                  :class="[getFileChangeKindClass(file), { 'is-active': file.path === selectedChangedFilePath }]"
                  @click="selectChangedFile(file)"
                >
                  <span class="changed-file-path">{{ file.path }}</span>
                </button>
              </div>
            </div>

            <div class="changes-pane diff-preview-pane">
              <div v-if="selectedChangedFile" class="changes-pane-header diff-pane-header" :class="getFileChangeKindClass(selectedChangedFile)">
                <div class="diff-pane-title">
                  <strong>{{ selectedChangedFile.path }}</strong>
                  <span>当前文件差异</span>
                </div>
              </div>

              <div v-if="!selectedChangedFile" class="placeholder-card diff-placeholder">
                <p>左侧选择一个变更文件，这里展示当前 diff。</p>
              </div>

              <div v-else-if="selectedFileDiffLoading && !selectedFileDiff" class="placeholder-card diff-placeholder">
                <p>正在读取文件差异…</p>
              </div>

              <div v-else-if="selectedFileDiffError" class="placeholder-card diff-placeholder">
                <p>{{ selectedFileDiffError }}</p>
              </div>

              <div v-else-if="selectedFileDiff" class="diff-preview-content">
                <section
                  v-for="section in selectedFileDiff.sections"
                  :key="`${selectedFileDiff.path}-${section.kind}-${section.title}`"
                  class="diff-section"
                >
                  <pre class="diff-code"><code><span
                  v-for="(line, index) in getDiffLines(section.content)"
                  :key="`${section.kind}-${index}`"
                  class="diff-line"
                    :class="getDiffLineClass(line)"
                  >{{ line || ' ' }}</span></code></pre>
                </section>
              </div>

              <div v-else class="placeholder-card diff-placeholder">
                <p>当前文件没有可展示的差异。</p>
              </div>
            </div>
          </div>
        </template>
      </section>

      <section class="panel-card draft-panel">
        <div class="panel-header">
          <div>
            <p class="panel-kicker">提交草稿</p>
            <h2>AI 生成结果</h2>
          </div>
          <div class="panel-header-actions">
            <template v-if="devices.length > 0">
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
            </template>
            <el-tag v-if="draftModelLabel" type="info" effect="plain">
              {{ draftModelLabel }}
            </el-tag>
          </div>
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

import {
  fetchAiGitSavedRepos,
  saveAiGitSavedRepos,
  touchAiGitSavedRepo,
  type AiGitSavedRepo,
} from '@/api/aiGitRepos'
import {
  commitDeviceEntryGit,
  fetchDeviceEntryGitHistoryStats,
  fetchDeviceEntryGitFileDiff,
  fetchDeviceEntryGitReductionRun,
  generateAndCommitDeviceEntryGit,
  generateDeviceEntryGitMessage,
  inspectDeviceEntryGit,
  startDeviceEntryGitReductionRun,
  type GitChangedFile,
  type GitCommitResponse,
  type GitCommitStyle,
  type GitFileDiffResponse,
  type GitHistoryStatsResponse,
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

interface GitHistoryRangeOption {
  label: string
  days: number
}

interface GitHistoryChartPoint {
  date: string
  x: number
  deletedY: number
  totalY: number
}

interface GitHistoryChartModel {
  viewBox: string
  chartWidth: number
  chartHeight: number
  bottomY: number
  deletedAreaPath: string
  addedAreaPath: string
  deletedLinePath: string
  totalLinePath: string
  xTicks: Array<{ key: string; x: number; label: string }>
  yGuides: Array<{ key: string; y: number; label: string }>
  hasActivity: boolean
  peakChurn: number
}

const STORAGE_KEY = 'codeyun_ai_git_commit_form_v1'
const FIXED_COMMIT_STYLE: GitCommitStyle = 'summary'
const HISTORY_RANGE_OPTIONS: GitHistoryRangeOption[] = [
  { label: '30天', days: 30 },
  { label: '90天', days: 90 },
  { label: '180天', days: 180 },
  { label: '365天', days: 365 },
]
const DEFAULT_HISTORY_RANGE_DAYS = 180
const HISTORY_CHART_WIDTH = 920
const HISTORY_CHART_HEIGHT = 252
const HISTORY_CHART_PADDING = {
  top: 18,
  right: 20,
  bottom: 36,
  left: 18,
}
const historyChartIdPrefix = `ai-git-history-${Math.random().toString(36).slice(2, 8)}`

const router = useRouter()
const userStore = useUserStore()
const aiProviderStore = useAiProviderStore()

function loadPersistedForm(): PersistedAiGitCommitForm {
  const fallback: PersistedAiGitCommitForm = {
    entryId: '',
    cwd: '',
    providerId: '',
    model: '',
    style: FIXED_COMMIT_STYLE,
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
      style: FIXED_COMMIT_STYLE,
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
const savedRepos = ref<AiGitSavedRepo[]>([])
const savingSavedRepos = ref(false)
const selectedSavedRepoId = ref('')
const historyRangeDays = ref(DEFAULT_HISTORY_RANGE_DAYS)
const historyStats = ref<GitHistoryStatsResponse | null>(null)
const historyLoading = ref(false)
const historyError = ref('')
const selectedChangedFilePath = ref('')
const fileDiffMap = ref<Record<string, GitFileDiffResponse>>({})
const fileDiffErrorMap = ref<Record<string, string>>({})
const loadingFileDiffPath = ref('')
const addRepoDialogVisible = ref(false)
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
const projectRepoOptions = computed(() => {
  if (!form.entryId) {
    return displayedSavedRepos.value
  }
  return displayedSavedRepos.value.filter(repo => repo.entry_id === form.entryId)
})
const sortedChangedFiles = computed(() =>
  [...(inspectResult.value?.changed_files || [])].sort((left, right) => {
    const kindDelta = getFileChangeKindOrder(left) - getFileChangeKindOrder(right)
    if (kindDelta !== 0) {
      return kindDelta
    }
    return left.path.localeCompare(right.path, 'zh-CN')
  }),
)
const selectedChangedFile = computed(() => {
  const files = sortedChangedFiles.value
  if (!files.length) {
    return null
  }
  return files.find(file => file.path === selectedChangedFilePath.value) ?? files[0] ?? null
})
const selectedFileDiff = computed(() => {
  const path = selectedChangedFile.value?.path
  return path ? fileDiffMap.value[path] ?? null : null
})
const selectedFileDiffError = computed(() => {
  const path = selectedChangedFile.value?.path
  return path ? fileDiffErrorMap.value[path] || '' : ''
})
const selectedFileDiffLoading = computed(() => {
  const path = selectedChangedFile.value?.path
  return Boolean(path && loadingFileDiffPath.value === path)
})

const canInspect = computed(() => Boolean(form.entryId && form.cwd.trim()))
const canGenerate = computed(() => Boolean(form.entryId && form.cwd.trim() && form.providerId && form.model.trim()))
const requiresReduction = computed(() => Boolean(inspectResult.value?.oversized))
const isRunningPrimaryAction = computed(() =>
  inspecting.value || generating.value || committing.value || generatingAndCommitting.value || reducing.value || reductionRunInProgress.value,
)
const historySummaryText = computed(() => {
  const stats = historyStats.value
  if (!stats) {
    return `最近 ${historyRangeDays.value} 天的提交工作量走势`
  }
  return [
    `最近 ${stats.days} 天`,
    `${formatHistoryNumber(stats.total_commit_count)} 次提交`,
    `+${formatHistoryNumber(stats.total_added_line_count)}`,
    `-${formatHistoryNumber(stats.total_deleted_line_count)}`,
  ].join(' · ')
})
const historyChartModel = computed<GitHistoryChartModel | null>(() => buildGitHistoryChartModel(historyStats.value))
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
    style: FIXED_COMMIT_STYLE,
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
  if (!form.cwd.trim() && projectRepoOptions.value.length) {
    await selectSavedRepo(projectRepoOptions.value[0], { touch: false })
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
  syncSelectedSavedRepoFromForm()
}

function syncSelectedSavedRepoFromForm() {
  const matched = findSavedRepoByLocation(form.entryId, form.cwd)
  selectedSavedRepoId.value = matched?.id || ''
}

function resetFileDiffState(options: { keepSelection?: boolean } = {}) {
  if (!options.keepSelection) {
    selectedChangedFilePath.value = ''
  }
  fileDiffMap.value = {}
  fileDiffErrorMap.value = {}
  loadingFileDiffPath.value = ''
}

function resetHistoryState() {
  historyStats.value = null
  historyError.value = ''
  historyLoading.value = false
}

function syncSelectedChangedFile(options: { prefetch?: boolean } = {}) {
  const files = sortedChangedFiles.value
  if (!files.length) {
    selectedChangedFilePath.value = ''
    return
  }

  if (!files.some(file => file.path === selectedChangedFilePath.value)) {
    selectedChangedFilePath.value = files[0]?.path || ''
  }

  if (options.prefetch && selectedChangedFilePath.value) {
    void ensureFileDiff(selectedChangedFilePath.value, { silent: true })
  }
}

function clearDraftState() {
  draftSubject.value = ''
  draftBodyText.value = ''
  draftNeedsSplit.value = false
  draftReason.value = ''
  draftModelLabel.value = ''
  reductionMeta.value = null
  reductionSummary.value = ''
}

function resetWorkspaceResult() {
  stopReductionRunPolling()
  reductionRun.value = null
  inspectResult.value = null
  resetHistoryState()
  resetFileDiffState()
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
        await loadHistoryStats({ silent: true })
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

function formatProjectRepoOptionLabel(repo: AiGitSavedRepo) {
  return `${repo.name} · ${repo.cwd}`
}

function handleEntryChange() {
  const matchedRepo = findSavedRepoByLocation(form.entryId, form.cwd)
  if (matchedRepo) {
    selectedSavedRepoId.value = matchedRepo.id
    return
  }
  selectedSavedRepoId.value = ''
  form.cwd = ''
  resetWorkspaceResult()
}

async function handleProjectRepoChange(repoId?: string) {
  const nextRepo = displayedSavedRepos.value.find(repo => repo.id === repoId)
  if (!nextRepo) {
    selectedSavedRepoId.value = ''
    form.cwd = ''
    resetWorkspaceResult()
    return
  }
  await selectSavedRepo(nextRepo)
}

function handleProviderChange(providerId: string) {
  form.providerId = providerId
  form.model = aiProviderStore.getEffectiveModel(providerId) || availableModels.value[0] || ''
}

function getFileChangeKind(file: GitChangedFile) {
  const normalized = (file.status || '').toUpperCase()
  if (file.untracked || normalized.includes('A')) {
    return 'added'
  }
  if (normalized.includes('D')) {
    return 'deleted'
  }
  return 'modified'
}

function getFileChangeKindOrder(file: GitChangedFile) {
  const kind = getFileChangeKind(file)
  if (kind === 'added') {
    return 0
  }
  if (kind === 'modified') {
    return 1
  }
  return 2
}

function getFileChangeKindClass(file: GitChangedFile) {
  const kind = getFileChangeKind(file)
  if (kind === 'added') {
    return 'is-added'
  }
  if (kind === 'deleted') {
    return 'is-deleted'
  }
  return 'is-modified'
}

function getDiffLines(content: string) {
  if (!content) {
    return ['']
  }
  const rawLines = content.split(/\r?\n/)
  const filteredLines = rawLines.filter(line => !isGitDiffMetadataLine(line))
  if (filteredLines.some(line => line.trim())) {
    return filteredLines
  }
  return rawLines
}

function isGitDiffMetadataLine(line: string) {
  return (
    line.startsWith('diff --git ')
    || line.startsWith('index ')
    || line.startsWith('old mode ')
    || line.startsWith('new mode ')
    || line.startsWith('deleted file mode ')
    || line.startsWith('new file mode ')
    || line.startsWith('similarity index ')
    || line.startsWith('rename from ')
    || line.startsWith('rename to ')
    || line.startsWith('copy from ')
    || line.startsWith('copy to ')
    || line.startsWith('--- ')
    || line.startsWith('+++ ')
  )
}

function getDiffLineClass(line: string) {
  if (line.startsWith('+++') || line.startsWith('---')) {
    return 'is-file'
  }
  if (line.startsWith('@@')) {
    return 'is-hunk'
  }
  if (line.startsWith('+')) {
    return 'is-added'
  }
  if (line.startsWith('-')) {
    return 'is-removed'
  }
  return 'is-context'
}

function formatHistoryNumber(value: number) {
  return value.toLocaleString('zh-CN')
}

function formatHistoryDateLabel(dateText: string) {
  const [yearText, monthText, dayText] = dateText.split('-')
  const year = Number(yearText)
  const month = Number(monthText)
  const day = Number(dayText)
  if (!year || !month || !day) {
    return dateText
  }
  return `${month}/${day}`
}

function buildPathCommand(x: number, y: number) {
  return `${x.toFixed(1)} ${y.toFixed(1)}`
}

function buildAreaPath(points: GitHistoryChartPoint[], baselineY: number) {
  if (!points.length) {
    return ''
  }
  const commands = [`M ${buildPathCommand(points[0].x, baselineY)}`]
  for (const point of points) {
    commands.push(`L ${buildPathCommand(point.x, point.deletedY)}`)
  }
  commands.push(`L ${buildPathCommand(points[points.length - 1].x, baselineY)}`)
  commands.push('Z')
  return commands.join(' ')
}

function buildBandPath(points: GitHistoryChartPoint[]) {
  if (!points.length) {
    return ''
  }
  const commands = [`M ${buildPathCommand(points[0].x, points[0].deletedY)}`]
  for (const point of points) {
    commands.push(`L ${buildPathCommand(point.x, point.totalY)}`)
  }
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const point = points[index]
    commands.push(`L ${buildPathCommand(point.x, point.deletedY)}`)
  }
  commands.push('Z')
  return commands.join(' ')
}

function buildLinePath(points: Array<{ x: number; y: number }>) {
  if (!points.length) {
    return ''
  }
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${buildPathCommand(point.x, point.y)}`)
    .join(' ')
}

function buildHistoryXTicks(points: GitHistoryChartPoint[]) {
  if (!points.length) {
    return []
  }
  const indices = new Set<number>([0, points.length - 1])
  const tickCount = Math.min(5, points.length)
  for (let index = 1; index < tickCount - 1; index += 1) {
    indices.add(Math.round((index * (points.length - 1)) / (tickCount - 1)))
  }
  return [...indices]
    .sort((left, right) => left - right)
    .map(index => ({
      key: points[index]?.date || String(index),
      x: points[index]?.x || 0,
      label: formatHistoryDateLabel(points[index]?.date || ''),
    }))
}

function buildGitHistoryChartModel(stats: GitHistoryStatsResponse | null): GitHistoryChartModel | null {
  if (!stats?.points.length) {
    return null
  }
  const chartWidth = HISTORY_CHART_WIDTH
  const chartHeight = HISTORY_CHART_HEIGHT
  const plotWidth = chartWidth - HISTORY_CHART_PADDING.left - HISTORY_CHART_PADDING.right
  const plotHeight = chartHeight - HISTORY_CHART_PADDING.top - HISTORY_CHART_PADDING.bottom
  const bottomY = HISTORY_CHART_PADDING.top + plotHeight
  const peakChurn = Math.max(
    ...stats.points.map(point => point.added_line_count + point.deleted_line_count),
    0,
  )
  const normalizedPeak = Math.max(peakChurn, 1)
  const chartPoints = stats.points.map((point, index) => {
    const x = stats.points.length === 1
      ? HISTORY_CHART_PADDING.left + plotWidth / 2
      : HISTORY_CHART_PADDING.left + (plotWidth * index) / (stats.points.length - 1)
    const deletedTotal = point.deleted_line_count
    const churnTotal = point.deleted_line_count + point.added_line_count
    return {
      date: point.date,
      x,
      deletedY: HISTORY_CHART_PADDING.top + plotHeight - (deletedTotal / normalizedPeak) * plotHeight,
      totalY: HISTORY_CHART_PADDING.top + plotHeight - (churnTotal / normalizedPeak) * plotHeight,
    }
  })
  const yGuideRatios = [0.25, 0.5, 0.75, 1]

  return {
    viewBox: `0 0 ${chartWidth} ${chartHeight}`,
    chartWidth,
    chartHeight,
    bottomY,
    deletedAreaPath: buildAreaPath(chartPoints, bottomY),
    addedAreaPath: buildBandPath(chartPoints),
    deletedLinePath: buildLinePath(chartPoints.map(point => ({ x: point.x, y: point.deletedY }))),
    totalLinePath: buildLinePath(chartPoints.map(point => ({ x: point.x, y: point.totalY }))),
    xTicks: buildHistoryXTicks(chartPoints),
    yGuides: yGuideRatios.map(ratio => ({
      key: String(ratio),
      y: HISTORY_CHART_PADDING.top + plotHeight - ratio * plotHeight,
      label: formatHistoryNumber(Math.round(normalizedPeak * ratio)),
    })),
    hasActivity: peakChurn > 0,
    peakChurn,
  }
}

async function ensureFileDiff(path: string, options: { silent?: boolean } = {}) {
  const normalizedPath = path.trim()
  if (!normalizedPath || !form.entryId || !form.cwd.trim()) {
    return
  }
  if (fileDiffMap.value[normalizedPath] || loadingFileDiffPath.value === normalizedPath) {
    return
  }

  loadingFileDiffPath.value = normalizedPath
  fileDiffErrorMap.value = {
    ...fileDiffErrorMap.value,
    [normalizedPath]: '',
  }

  try {
    const response = await fetchDeviceEntryGitFileDiff(form.entryId, {
      cwd: form.cwd.trim(),
      path: normalizedPath,
    })
    fileDiffMap.value = {
      ...fileDiffMap.value,
      [normalizedPath]: response,
    }
  } catch (error: any) {
    const message = getErrorMessage(error)
    fileDiffErrorMap.value = {
      ...fileDiffErrorMap.value,
      [normalizedPath]: message,
    }
    if (!options.silent) {
      ElMessage.error(message)
    }
  } finally {
    if (loadingFileDiffPath.value === normalizedPath) {
      loadingFileDiffPath.value = ''
    }
  }
}

async function selectChangedFile(file: GitChangedFile) {
  selectedChangedFilePath.value = file.path
  await ensureFileDiff(file.path)
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
  const previousPath = selectedChangedFilePath.value
  const nextInspect = await inspectDeviceEntryGit(form.entryId, {
    cwd: form.cwd.trim(),
  })
  inspectResult.value = nextInspect
  resetFileDiffState({ keepSelection: true })
  selectedChangedFilePath.value = previousPath
  syncSelectedChangedFile({ prefetch: !nextInspect.clean })
  if (nextInspect.clean && !options.silentClean) {
    ElMessage.success('当前工作区是干净的')
  }
  return nextInspect
}

async function loadHistoryStats(options: { silent?: boolean } = {}) {
  if (!canInspect.value) {
    resetHistoryState()
    return null
  }

  historyLoading.value = true
  historyError.value = ''
  try {
    const response = await fetchDeviceEntryGitHistoryStats(form.entryId, {
      cwd: form.cwd.trim(),
      days: historyRangeDays.value,
    })
    historyStats.value = response
    return response
  } catch (error: any) {
    historyError.value = getErrorMessage(error)
    if (!options.silent) {
      ElMessage.error(historyError.value)
    }
    return null
  } finally {
    historyLoading.value = false
  }
}

async function updateHistoryRangeDays(days: number) {
  if (historyRangeDays.value === days) {
    return
  }
  historyRangeDays.value = days
  await loadHistoryStats({ silent: true })
}

async function inspectChanges(options: { silentClean?: boolean } = {}) {
  if (!canInspect.value) {
    ElMessage.warning('请先选择设备并填写项目目录')
    return
  }

  inspecting.value = true
  try {
    await loadInspectResult(options)
    await loadHistoryStats({ silent: true })
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
      style: FIXED_COMMIT_STYLE,
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
        style: FIXED_COMMIT_STYLE,
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
        style: FIXED_COMMIT_STYLE,
        include_body: form.includeBody,
        max_files: 8,
        add_all: form.addAll,
      })
      applyDraftResponse(response)
      lastCommit.value = response.commit
      ElMessage.success(`已生成并提交：${response.commit.short_hash}`)
      await loadInspectResult({ silentClean: true })
      await loadHistoryStats({ silent: true })
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
  const previousPath = selectedChangedFilePath.value
  inspectResult.value = response.inspect
  resetFileDiffState({ keepSelection: true })
  selectedChangedFilePath.value = previousPath
  syncSelectedChangedFile({ prefetch: !response.inspect.clean })
  draftSubject.value = response.subject
  draftBodyText.value = response.body.join('\n')
  draftNeedsSplit.value = response.needs_split
  draftReason.value = response.reason
  draftModelLabel.value = response.model
  if (!('reduction' in response)) {
    reductionMeta.value = null
    reductionSummary.value = ''
    return
  }
  const reductionResponse = response as GitReduceResponse
  reductionMeta.value = reductionResponse.reduction
  reductionSummary.value = reductionResponse.summary || reductionResponse.topic || ''
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
      style: FIXED_COMMIT_STYLE,
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
    await loadHistoryStats({ silent: true })
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

.page-stack {
  margin-top: 24px;
  display: grid;
  gap: 20px;
}

.top-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
  align-items: start;
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

.panel-header-main {
  min-width: 0;
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

.settings-grid {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 7fr);
  gap: 0 14px;
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.switch-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, max-content));
  gap: 12px 18px;
  margin: 8px 0 14px;
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 18px;
}

.inline-empty-state,
.inline-success-card {
  margin-top: 18px;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.3);
  background: rgba(248, 250, 252, 0.72);
  padding: 18px;
}

.inline-success-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.inline-success-head h3 {
  margin: 6px 0 0;
  font-size: 18px;
  line-height: 1.35;
  color: #0f172a;
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
  margin: 12px 0 0;
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

.changes-panel {
  overflow: hidden;
}

.repo-meta-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
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

.repo-meta-item.is-added {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.92), rgba(248, 250, 252, 1));
}

.repo-meta-item.is-deleted {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.92), rgba(248, 250, 252, 1));
}

.meta-label {
  color: #64748b;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.meta-value-added {
  color: #15803d;
}

.meta-value-deleted {
  color: #dc2626;
}

.history-card {
  margin-bottom: 18px;
  border-radius: 22px;
  border: 1px solid rgba(191, 219, 254, 0.8);
  background:
    radial-gradient(circle at top right, rgba(37, 99, 235, 0.08), transparent 30%),
    linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.98));
  padding: 16px 18px 18px;
}

.history-card-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 14px;
}

.history-card-copy {
  display: grid;
  gap: 4px;
}

.history-card-copy strong {
  color: #0f172a;
  font-size: 16px;
}

.history-card-copy span {
  color: #475569;
  font-size: 13px;
  line-height: 1.6;
}

.history-error-text {
  margin: 0;
  color: #b91c1c;
  font-size: 12px;
  line-height: 1.5;
}

.history-card-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.history-range-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.history-range-chip {
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  color: #475569;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease,
    color 0.16s ease,
    box-shadow 0.16s ease;
}

.history-range-chip:hover:not(:disabled) {
  border-color: rgba(37, 99, 235, 0.35);
  color: #1d4ed8;
}

.history-range-chip.is-active {
  border-color: rgba(37, 99, 235, 0.48);
  background: rgba(219, 234, 254, 0.82);
  color: #1d4ed8;
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.08);
}

.history-range-chip:disabled {
  cursor: default;
  opacity: 0.72;
}

.history-chart-shell {
  display: grid;
  gap: 12px;
}

.history-chart {
  width: 100%;
  height: 252px;
  display: block;
  border-radius: 18px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(241, 245, 249, 0.94));
}

.history-chart-guide-line {
  stroke: rgba(148, 163, 184, 0.24);
  stroke-dasharray: 4 6;
}

.history-chart-guide-text,
.history-chart-tick-text {
  fill: #64748b;
  font-size: 11px;
}

.history-chart-baseline {
  stroke: rgba(148, 163, 184, 0.45);
  stroke-width: 1;
}

.history-chart-area {
  stroke: none;
}

.history-chart-line {
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2;
}

.history-chart-line.is-deleted {
  stroke: rgba(220, 38, 38, 0.9);
}

.history-chart-line.is-total {
  stroke: rgba(29, 78, 216, 0.96);
}

.history-legend-list {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.history-legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(226, 232, 240, 0.9);
}

.history-legend-item strong {
  color: #0f172a;
  font-size: 13px;
}

.history-legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: #2563eb;
  flex: 0 0 auto;
}

.history-legend-item.is-deleted .history-legend-dot {
  background: #ef4444;
}

.history-legend-item.is-added .history-legend-dot {
  background: #2563eb;
}

.history-legend-dot.is-neutral {
  background: #64748b;
}

.history-legend-dot.is-peak {
  background: #0f766e;
}

.history-placeholder-card {
  min-height: 160px;
  display: grid;
  place-items: center;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.36);
  background: rgba(248, 250, 252, 0.76);
  color: #64748b;
  text-align: center;
  padding: 24px;
}

.inspect-title {
  margin: 18px 0 10px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #334155;
}

.changes-workbench {
  margin-top: 18px;
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  gap: 16px;
  min-height: 560px;
}

.changes-pane {
  min-width: 0;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 20px;
  background: rgba(248, 250, 252, 0.75);
  overflow: hidden;
}

.changes-pane-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
  background: rgba(255, 255, 255, 0.88);
}

.changes-pane-header strong {
  display: block;
  color: #0f172a;
  font-size: 15px;
}

.changes-pane-header span {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.changes-pane-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  height: 32px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.1);
  color: #1d4ed8;
  font-weight: 700;
}

.changes-file-list {
  display: grid;
  gap: 8px;
  padding: 12px;
  max-height: 720px;
  overflow: auto;
}

.changed-file-item {
  position: relative;
  width: 100%;
  text-align: left;
  display: grid;
  gap: 0;
  padding: 14px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 16px;
  background: #fff;
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    box-shadow 0.16s ease,
    transform 0.16s ease;
}

.changed-file-item:hover {
  transform: translateY(-1px);
  border-color: rgba(37, 99, 235, 0.3);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
}

.changed-file-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  border-radius: 16px 0 0 16px;
  background: rgba(148, 163, 184, 0.28);
}

.changed-file-item.is-active {
  border-color: rgba(37, 99, 235, 0.5);
  box-shadow: 0 14px 30px rgba(29, 78, 216, 0.12);
}

.changed-file-item.is-added {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.94), rgba(255, 255, 255, 1));
  border-color: rgba(34, 197, 94, 0.24);
}

.changed-file-item.is-added::before {
  background: #22c55e;
}

.changed-file-item.is-modified {
  background: linear-gradient(180deg, rgba(254, 252, 232, 0.96), rgba(255, 255, 255, 1));
  border-color: rgba(234, 179, 8, 0.28);
}

.changed-file-item.is-modified::before {
  background: #eab308;
}

.changed-file-item.is-deleted {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.94), rgba(255, 255, 255, 1));
  border-color: rgba(248, 113, 113, 0.24);
}

.changed-file-item.is-deleted::before {
  background: #ef4444;
}

.changed-file-item.is-active.is-added {
  background: linear-gradient(180deg, rgba(220, 252, 231, 0.94), rgba(255, 255, 255, 1));
}

.changed-file-item.is-active.is-modified {
  background: linear-gradient(180deg, rgba(254, 249, 195, 0.94), rgba(255, 255, 255, 1));
}

.changed-file-item.is-active.is-deleted {
  background: linear-gradient(180deg, rgba(254, 226, 226, 0.9), rgba(255, 255, 255, 1));
  box-shadow: 0 14px 30px rgba(29, 78, 216, 0.12);
}

.changed-file-path {
  min-width: 0;
  word-break: break-all;
  color: #0f172a;
  line-height: 1.6;
  padding-left: 8px;
}

.diff-pane-header {
  align-items: flex-start;
}

.diff-pane-header.is-added {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.95), rgba(255, 255, 255, 0.88));
}

.diff-pane-header.is-modified {
  background: linear-gradient(180deg, rgba(254, 252, 232, 0.96), rgba(255, 255, 255, 0.88));
}

.diff-pane-header.is-deleted {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.95), rgba(255, 255, 255, 0.88));
}

.diff-pane-title {
  min-width: 0;
}

.diff-pane-title strong {
  word-break: break-all;
}

.diff-preview-pane {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}

.diff-placeholder {
  min-height: 420px;
  margin: 16px;
}

.diff-preview-content {
  padding: 16px;
  display: grid;
  gap: 12px;
  max-height: 720px;
  overflow: auto;
}

.diff-section {
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 18px;
  background: #fff;
  overflow: hidden;
}

.diff-code {
  margin: 0;
  padding: 0;
  background: #ffffff;
  color: #0f172a;
  font-size: 13px;
  line-height: 1.65;
  overflow: auto;
}

.diff-code code {
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.diff-line {
  display: block;
  white-space: pre-wrap;
  word-break: break-word;
  padding: 4px 16px;
  color: #0f172a;
}

.diff-line.is-added {
  background: #e8f7e9;
  color: #0f172a;
}

.diff-line.is-removed {
  background: #fdecec;
  color: #0f172a;
}

.diff-line.is-hunk {
  background: #eaf3ff;
  color: #4b5563;
}

.diff-line.is-file {
  background: #eef5ff;
  color: #334155;
  font-weight: 600;
}

.diff-line.is-context {
  background: #ffffff;
  color: #0f172a;
}

.draft-panel {
  overflow: hidden;
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

.reduction-progress-head span,
.run-error-text {
  color: #64748b;
  font-size: 12px;
}

.reduction-progress-grid,
.reduction-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.reduction-progress-item,
.reduction-meta-item {
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
}

.reduction-progress-item span,
.reduction-meta-item span {
  color: #64748b;
  font-size: 12px;
}

.reduction-progress-item strong,
.reduction-meta-item strong {
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

.reduction-level-list {
  margin-top: 12px;
  display: grid;
  gap: 8px;
}

.reduction-level-item {
  display: grid;
  gap: 10px;
  padding: 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
}

.reduction-level-main {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.reduction-level-main strong {
  color: #0f172a;
}

.reduction-level-main span {
  color: #64748b;
  font-size: 12px;
}

.reduction-preview-list {
  display: grid;
  gap: 8px;
}

.reduction-preview-item {
  border-radius: 12px;
  background: #f8fafc;
  padding: 12px;
}

.reduction-preview-item strong {
  color: #0f172a;
}

.reduction-preview-item p {
  margin: 6px 0;
  color: #475569;
  line-height: 1.6;
}

.reduction-preview-item span {
  color: #64748b;
  font-size: 12px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

@media (max-width: 1280px) {
  .top-grid,
  .changes-workbench,
  .repo-meta-grid,
  .history-legend-list {
    grid-template-columns: 1fr;
  }

  .settings-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .ai-git-commit-page {
    padding: 20px;
  }

  .hero-panel,
  .panel-card {
    border-radius: 22px;
    padding: 18px;
  }

  .hero-copy h1 {
    font-size: 30px;
  }

  .panel-header,
  .history-card-header,
  .reduction-progress-head,
  .reduction-level-main,
  .inline-success-head,
  .changes-pane-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .switch-row,
  .reduction-progress-grid,
  .reduction-meta-grid,
  .split-group-chip-list,
  .history-legend-list {
    grid-template-columns: 1fr;
  }

  .changes-pane-count {
    min-width: 0;
  }

  .history-card-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .history-chart {
    height: 220px;
  }

  .changes-file-list,
  .diff-preview-content {
    max-height: none;
  }
}
</style>

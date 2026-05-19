<template>
  <div class="ai-reduction-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <div class="eyebrow">AI工具 / AI归纳</div>
        <h1>AI归纳</h1>
        <p>上传文本后先做全局归纳，再基于分层索引提问。适合超大文本，不依赖一次把原文全部塞给模型。</p>
      </div>
    </section>

    <div class="workspace-grid">
      <aside class="control-panel">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">文档库</p>
              <h2>文本资产</h2>
            </div>
            <div class="panel-header-actions">
              <input
                ref="fileInputRef"
                class="hidden-file-input"
                type="file"
                accept=".txt,.md,.markdown,.text,.json,.jsonl,.log,.yaml,.yml,.toml,.csv,.tsv,.py,.rst"
                @change="handleFilePicked"
              />
              <el-button
                text
                :icon="RefreshRight"
                :loading="loadingDocuments"
                @click="loadDocuments({ keepSelection: true, preserveOutputs: true })"
              >
                刷新
              </el-button>
              <el-button type="primary" plain :loading="uploading" @click="openFilePicker">
                上传文本
              </el-button>
            </div>
          </div>

          <div v-if="!documents.length" class="placeholder-card">
            <p>先上传一份文本类文件。第一版支持 txt、md、jsonl、log、yaml、csv 这类纯文本。</p>
          </div>

          <div v-else class="document-list">
            <button
              v-for="document in documents"
              :key="document.id"
              type="button"
              class="document-item"
              :class="{ 'is-active': document.id === selectedDocumentId }"
              @click="selectDocument(document.id)"
            >
              <div class="document-item-top">
                <strong>{{ document.title || document.original_filename }}</strong>
                <div class="document-item-actions">
                  <el-tag size="small" effect="plain" :type="getDocumentStatusTagType(document.status)">
                    {{ getDocumentStatusLabel(document.status) }}
                  </el-tag>
                  <span
                    role="button"
                    tabindex="0"
                    class="document-item-remove"
                    @click.stop="confirmDeleteDocument(document)"
                    @keydown.enter.stop.prevent="confirmDeleteDocument(document)"
                  >
                    删除
                  </span>
                </div>
              </div>
              <div class="document-item-meta">
                <span>{{ document.original_filename }}</span>
                <span>·</span>
                <span>{{ formatFileSize(document.size_bytes) }}</span>
              </div>
              <p class="document-item-summary">
                {{ document.latest_summary || '还没有归纳结果' }}
              </p>
            </button>
          </div>
        </section>

        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">执行配置</p>
              <h2>模型与动作</h2>
            </div>
          </div>

          <el-form label-position="top" class="settings-form">
            <el-form-item label="AI 来源">
              <el-select
                v-model="form.providerId"
                filterable
                placeholder="选择 AI 来源"
                :disabled="!providers.length"
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
                placeholder="选择或输入模型"
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
          </el-form>

          <div class="provider-hint">
            <span>当前来源：</span>
            <strong>{{ currentProviderLabel }}</strong>
            <span v-if="form.model"> / {{ form.model }}</span>
          </div>
          <p v-if="isVisionLikeModel" class="inline-hint warning-hint">
            当前是偏视觉模型，做纯文本归纳通常更慢也更不稳。优先建议 `qwen3.5:4b-instruct`。
          </p>

          <div class="action-stack">
            <el-button
              type="primary"
              :icon="MagicStick"
              :loading="indexing"
              :disabled="!canIndex || busy"
              @click="runIndexing"
            >
              {{ detail?.latest_run ? '重新归纳' : '开始归纳' }}
            </el-button>
          </div>
        </section>
      </aside>

      <section class="result-panel">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">当前文档</p>
              <h2>{{ selectedDocument?.title || '未选择文档' }}</h2>
            </div>
            <el-tag v-if="selectedDocument" effect="light" :type="getDocumentStatusTagType(selectedDocument.status)">
              {{ getDocumentStatusLabel(selectedDocument.status) }}
            </el-tag>
          </div>

          <div v-if="!selectedDocument" class="placeholder-card">
            <p>左侧选一份文档，或者先上传新的文本文件。</p>
          </div>

          <template v-else>
            <div class="doc-meta-grid">
              <div class="doc-meta-item">
                <span class="meta-label">文件名</span>
                <strong>{{ selectedDocument.original_filename }}</strong>
              </div>
              <div class="doc-meta-item">
                <span class="meta-label">大小</span>
                <strong>{{ formatFileSize(selectedDocument.size_bytes) }}</strong>
              </div>
              <div class="doc-meta-item">
                <span class="meta-label">字符数</span>
                <strong>{{ selectedDocument.source_char_count }}</strong>
              </div>
              <div class="doc-meta-item">
                <span class="meta-label">归纳次数</span>
                <strong>{{ selectedDocument.run_count }}</strong>
              </div>
            </div>

            <div v-if="detail?.latest_run" class="run-summary-card">
              <div class="run-summary-head">
                <strong>最近一次归纳</strong>
                <span>{{ detail.latest_run.model || detail.latest_run.provider }}</span>
              </div>
              <div v-if="runningRun" class="run-progress-card">
                <div class="run-progress-head">
                  <strong>归纳进度</strong>
                  <span>已切分 {{ runningRun.completed_chunk_count }} 次会话</span>
                </div>
                <el-progress :percentage="runningRunLevelProgressPercent" :stroke-width="8" :show-text="false" />
                <div class="run-progress-grid">
                  <div class="run-summary-item">
                    <span>source units</span>
                    <strong>{{ runningRun.source_unit_count }}</strong>
                  </div>
                  <div class="run-summary-item">
                    <span>估算层数</span>
                    <strong>{{ runningRun.estimated_level_count || '-' }}</strong>
                  </div>
                  <div class="run-summary-item">
                    <span>当前层</span>
                    <strong>{{ runningRun.current_level_chunk_count > 0 ? runningRun.current_level_index + 1 : '-' }}</strong>
                  </div>
                  <div class="run-summary-item">
                    <span>本层进度</span>
                    <strong>{{ runningRun.current_level_completed_chunk_count }} / {{ runningRun.current_level_chunk_count || '-' }}</strong>
                  </div>
                </div>
              </div>
              <div class="run-summary-grid">
                <div class="run-summary-item">
                  <span>source units</span>
                  <strong>{{ detail.latest_run.source_unit_count }}</strong>
                </div>
                <div class="run-summary-item">
                  <span>层数</span>
                  <strong>{{ detail.latest_run.level_count }}</strong>
                </div>
                <div class="run-summary-item">
                  <span>节点数</span>
                  <strong>{{ detail.latest_run.node_count }}</strong>
                </div>
                <div class="run-summary-item">
                  <span>会话数</span>
                  <strong>{{ detail.latest_run.completed_chunk_count }}</strong>
                </div>
                <div class="run-summary-item">
                  <span>状态</span>
                  <strong>{{ getRunStatusLabel(detail.latest_run.status) }}</strong>
                </div>
              </div>
              <p v-if="detail.latest_run.top_summary" class="run-summary-text">
                {{ detail.latest_run.top_summary }}
              </p>
              <p v-if="detail.latest_run.error_message" class="run-error-text">
                {{ detail.latest_run.error_message }}
              </p>
            </div>
          </template>
        </section>

        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">归纳结果</p>
              <h2>全局摘要</h2>
            </div>
          </div>

          <div v-if="!indexResult && !detail?.latest_run" class="placeholder-card">
            <p>归纳完成后，这里会显示顶层摘要、关键词和分层规模。</p>
          </div>

          <template v-else>
            <div class="summary-block">
              <h3>{{ currentSummaryResult?.topic || '未命名主题' }}</h3>
              <p>{{ currentSummaryResult?.summary || detail?.active_run?.top_summary || detail?.latest_run?.top_summary || '暂无摘要' }}</p>
            </div>

            <div v-if="currentSummaryResult?.keywords?.length" class="chip-list">
              <span
                v-for="keyword in currentSummaryResult.keywords"
                :key="keyword"
                class="chip-item"
              >
                {{ keyword }}
              </span>
            </div>

            <div v-if="currentSummaryResult?.possible_questions?.length" class="suggestion-list">
              <div class="inspect-title">可能问题</div>
              <div
                v-for="question in currentSummaryResult.possible_questions"
                :key="question"
                class="suggestion-item"
                @click="applySuggestedQuestion(question)"
              >
                {{ question }}
              </div>
            </div>

            <div v-if="currentReductionMeta" class="reduction-meta-grid">
              <div class="meta-stat">
                <span>总层数</span>
                <strong>{{ currentReductionMeta.level_count }}</strong>
              </div>
              <div class="meta-stat">
                <span>总节点</span>
                <strong>{{ currentReductionMeta.node_count }}</strong>
              </div>
              <div class="meta-stat">
                <span>run id</span>
                <strong class="mono">{{ currentReductionMeta.run_id }}</strong>
              </div>
            </div>
          </template>
        </section>

        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">提问</p>
              <h2>基于索引问答</h2>
            </div>
            <el-button
              type="primary"
              :loading="querying"
              :disabled="!canQuery || busy"
              @click="submitQuery"
            >
              提问
            </el-button>
          </div>

            <el-input
            v-model="questionText"
            type="textarea"
            :rows="4"
            resize="vertical"
            placeholder="例如：这份材料的核心结论是什么？里面有没有提到部署失败原因？"
            :disabled="!detail?.active_run"
          />

          <p v-if="!detail?.active_run" class="inline-hint">
            先完成一次归纳，提问时才会使用到整份文档的全局结构和命中片段。
          </p>

          <div v-if="queryResult" class="query-result">
            <div class="query-answer">
              <h3>回答</h3>
              <p>{{ queryResult.answer }}</p>
            </div>

            <div v-if="queryResult.summary" class="query-summary">
              <span class="meta-label">摘要</span>
              <p>{{ queryResult.summary }}</p>
            </div>

            <div v-if="queryResult.matched_nodes.length" class="matched-node-list">
              <div class="inspect-title">命中节点</div>
              <div
                v-for="node in queryResult.matched_nodes"
                :key="node.node_id"
                class="matched-node-item"
              >
                <div class="matched-node-head">
                  <strong>{{ node.topic || node.node_id }}</strong>
                  <span v-if="node.score">score {{ node.score }}</span>
                </div>
                <p>{{ node.summary }}</p>
              </div>
            </div>

            <div v-if="queryResult.follow_up_questions.length" class="suggestion-list">
              <div class="inspect-title">继续追问</div>
              <div
                v-for="question in queryResult.follow_up_questions"
                :key="question"
                class="suggestion-item"
                @click="applySuggestedQuestion(question)"
              >
                {{ question }}
              </div>
            </div>
          </div>
        </section>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MagicStick, RefreshRight } from '@element-plus/icons-vue'

import {
  deleteReductionDocument,
  fetchReductionDocumentDetail,
  fetchReductionDocuments,
  indexReductionDocument,
  queryReductionDocument,
  uploadReductionDocument,
  type ReductionDocumentDetailResponse,
  type ReductionDocumentIndexResponse,
  type ReductionDocumentQueryResponse,
  type ReductionDocumentRead,
} from '@/api/reductionDocuments'
import { useAiProviderStore } from '@/store/aiProviderStore'
import { useUserStore } from '@/store/userStore'

interface PersistedFormState {
  providerId: string
  model: string
}

const STORAGE_KEY = 'codeyun_ai_reduction_form_v1'

const userStore = useUserStore()
const aiProviderStore = useAiProviderStore()

function loadPersistedForm(): PersistedFormState {
  const fallback: PersistedFormState = {
    providerId: '',
    model: '',
  }
  if (typeof window === 'undefined' || !window.localStorage) {
    return fallback
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return fallback
    }
    const parsed = JSON.parse(raw) as Partial<PersistedFormState>
    return {
      providerId: typeof parsed.providerId === 'string' ? parsed.providerId : fallback.providerId,
      model: typeof parsed.model === 'string' ? parsed.model : fallback.model,
    }
  } catch {
    return fallback
  }
}

const form = reactive<PersistedFormState>({
  ...loadPersistedForm(),
})

const fileInputRef = ref<HTMLInputElement | null>(null)
const documents = ref<ReductionDocumentRead[]>([])
const selectedDocumentId = ref<number | null>(null)
const detail = ref<ReductionDocumentDetailResponse | null>(null)
const indexResult = ref<ReductionDocumentIndexResponse | null>(null)
const queryResult = ref<ReductionDocumentQueryResponse | null>(null)
const questionText = ref('')

const loadingDocuments = ref(false)
const loadingDetail = ref(false)
const uploading = ref(false)
const indexing = ref(false)
const querying = ref(false)
let progressPollTimer: ReturnType<typeof setInterval> | null = null
let progressPollInFlight = false

const providers = computed(() => aiProviderStore.providers)
const currentProvider = computed(() => aiProviderStore.getProviderById(form.providerId))
const currentProviderLabel = computed(() => currentProvider.value?.label || form.providerId.trim() || '未选择')
const availableModels = computed(() => {
  const items = aiProviderStore.getEffectiveModels(form.providerId)
  if (form.model.trim() && !items.includes(form.model.trim())) {
    return [form.model.trim(), ...items]
  }
  return items
})
const isVisionLikeModel = computed(() => /(?:^|[-_:])vl(?:$|[-_:])/i.test(form.model.trim()))
const selectedDocument = computed(() => documents.value.find(item => item.id === selectedDocumentId.value) ?? null)
const busy = computed(() => loadingDocuments.value || loadingDetail.value || uploading.value || indexing.value || querying.value)
const canIndex = computed(() => Boolean(selectedDocument.value && form.providerId && form.model.trim()))
const canQuery = computed(() => Boolean(selectedDocument.value && detail.value?.active_run && form.providerId && form.model.trim() && questionText.value.trim()))
const currentSummaryResult = computed(() => indexResult.value?.result ?? null)
const currentReductionMeta = computed(() => indexResult.value?.reduction ?? null)
const runningRun = computed(() => detail.value?.latest_run?.status === 'running' ? detail.value.latest_run : null)
const runningRunLevelProgressPercent = computed(() => {
  const run = runningRun.value
  if (!run || run.current_level_chunk_count <= 0) {
    return 0
  }
  return Math.min(100, Math.round((run.current_level_completed_chunk_count / run.current_level_chunk_count) * 100))
})

watch(
  () => ({ providerId: form.providerId, model: form.model }),
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
    form.model = choosePreferredReductionModel(providerId)
  },
)

onMounted(async () => {
  await aiProviderStore.loadProviders(userStore.isAuthenticated)
  if (!form.providerId || !providers.value.some(provider => provider.id === form.providerId)) {
    form.providerId = aiProviderStore.defaultProviderId || providers.value[0]?.id || ''
  }
  if (!form.model.trim()) {
    form.model = choosePreferredReductionModel(form.providerId)
  }
  await loadDocuments({ keepSelection: false, preserveOutputs: false })
})

onBeforeUnmount(() => {
  stopProgressPolling()
})

async function loadDocuments(options: { keepSelection: boolean; preserveOutputs: boolean }) {
  loadingDocuments.value = true
  try {
    const items = await fetchReductionDocuments()
    documents.value = items
    const keepCurrent = options.keepSelection && items.some(item => item.id === selectedDocumentId.value)
    const nextId = keepCurrent ? selectedDocumentId.value : (items[0]?.id ?? null)
    if (nextId !== selectedDocumentId.value) {
      selectedDocumentId.value = nextId
    }
    if (selectedDocumentId.value != null) {
      await loadDetail(selectedDocumentId.value, { preserveOutputs: options.preserveOutputs })
    } else {
      detail.value = null
      indexResult.value = null
      queryResult.value = null
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loadingDocuments.value = false
  }
}

async function loadDetail(documentId: number | null, options: { preserveOutputs: boolean } = { preserveOutputs: false }) {
  if (documentId == null) {
    detail.value = null
    return
  }
  loadingDetail.value = true
  try {
    detail.value = await fetchReductionDocumentDetail(documentId)
    if (!options.preserveOutputs) {
      indexResult.value = null
      queryResult.value = null
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loadingDetail.value = false
  }
}

function syncDocumentInList(document: ReductionDocumentRead) {
  const index = documents.value.findIndex(item => item.id === document.id)
  if (index >= 0) {
    documents.value.splice(index, 1, document)
  }
}

async function refreshCurrentDetailSilently(documentId: number) {
  if (progressPollInFlight) {
    return
  }
  progressPollInFlight = true
  try {
    const nextDetail = await fetchReductionDocumentDetail(documentId)
    detail.value = nextDetail
    syncDocumentInList(nextDetail.document)
    if (nextDetail.latest_run?.status !== 'running') {
      stopProgressPolling()
    }
  } catch {
    // 轮询失败不打断主流程，最终状态由主请求返回。
  } finally {
    progressPollInFlight = false
  }
}

function startProgressPolling(documentId: number) {
  stopProgressPolling()
  void refreshCurrentDetailSilently(documentId)
  progressPollTimer = window.setInterval(() => {
    void refreshCurrentDetailSilently(documentId)
  }, 1500)
}

function stopProgressPolling() {
  if (progressPollTimer !== null) {
    clearInterval(progressPollTimer)
    progressPollTimer = null
  }
}

async function selectDocument(documentId: number) {
  if (documentId === selectedDocumentId.value) {
    return
  }
  selectedDocumentId.value = documentId
  await loadDetail(documentId, { preserveOutputs: false })
}

function openFilePicker() {
  fileInputRef.value?.click()
}

async function handleFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) {
    return
  }
  uploading.value = true
  try {
    const uploaded = await uploadReductionDocument(file)
    ElMessage.success('文档已上传')
    await loadDocuments({ keepSelection: false, preserveOutputs: false })
    selectedDocumentId.value = uploaded.id
    await loadDetail(uploaded.id, { preserveOutputs: false })
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    uploading.value = false
  }
}

async function runIndexing() {
  if (!selectedDocument.value) {
    ElMessage.warning('先选择一份文档')
    return
  }
  indexing.value = true
  queryResult.value = null
  const documentId = selectedDocument.value.id
  startProgressPolling(documentId)
  try {
    const connectionPayload = aiProviderStore.buildConnectionPayload(form.providerId)
    indexResult.value = await indexReductionDocument(documentId, {
      provider: connectionPayload.provider,
      base_url: connectionPayload.base_url,
      api_key: connectionPayload.api_key,
      model: form.model.trim(),
    })
    ElMessage.success('归纳完成')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    stopProgressPolling()
    await loadDocuments({ keepSelection: true, preserveOutputs: true })
    await nextTick()
    indexing.value = false
  }
}

async function submitQuery() {
  if (!selectedDocument.value || !detail.value?.active_run) {
    ElMessage.warning('先完成一次归纳')
    return
  }
  if (!questionText.value.trim()) {
    ElMessage.warning('请输入问题')
    return
  }
  querying.value = true
  try {
    const connectionPayload = aiProviderStore.buildConnectionPayload(form.providerId)
    queryResult.value = await queryReductionDocument(selectedDocument.value.id, {
      query: questionText.value.trim(),
      provider: connectionPayload.provider,
      base_url: connectionPayload.base_url,
      api_key: connectionPayload.api_key,
      model: form.model.trim(),
      run_id: detail.value.active_run.id,
    })
    await loadDocuments({ keepSelection: true, preserveOutputs: true })
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    querying.value = false
  }
}

async function confirmDeleteDocument(document: ReductionDocumentRead) {
  if (busy.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `删除后会同时清理这份文档的归纳记录、本地附件和检索缓存。\n\n${document.title || document.original_filename}`,
      '删除文本资产',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  try {
    await deleteReductionDocument(document.id)
    if (selectedDocumentId.value === document.id) {
      selectedDocumentId.value = null
      detail.value = null
      indexResult.value = null
      queryResult.value = null
      questionText.value = ''
    }
    await loadDocuments({ keepSelection: false, preserveOutputs: false })
    ElMessage.success('文档已删除')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

function applySuggestedQuestion(question: string) {
  questionText.value = question
}

function getDocumentStatusLabel(status: string) {
  switch ((status || '').trim()) {
    case 'uploaded':
      return '待归纳'
    case 'indexed':
      return '已归纳'
    case 'running':
      return '处理中'
    case 'error':
      return '异常'
    default:
      return status || '未知'
  }
}

function getDocumentStatusTagType(status: string) {
  switch ((status || '').trim()) {
    case 'indexed':
      return 'success'
    case 'running':
      return 'warning'
    case 'error':
      return 'danger'
    default:
      return 'info'
  }
}

function getRunStatusLabel(status: string) {
  switch ((status || '').trim()) {
    case 'completed':
      return '已完成'
    case 'running':
      return '处理中'
    case 'failed':
      return '失败'
    default:
      return status || '未知'
  }
}

function choosePreferredReductionModel(providerId: string) {
  const models = aiProviderStore.getEffectiveModels(providerId)
  for (const candidate of ['qwen3.5:4b-instruct', 'qwen3.5:4b']) {
    if (models.includes(candidate)) {
      return candidate
    }
  }
  return aiProviderStore.getEffectiveModel(providerId) || models[0] || ''
}

function formatFileSize(value: number) {
  if (!Number.isFinite(value) || value <= 0) {
    return '0 B'
  }
  if (value < 1024) {
    return `${value} B`
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function getErrorMessage(error: unknown) {
  const maybeAxiosError = error as any
  const detail = maybeAxiosError?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }
  if (typeof maybeAxiosError?.message === 'string' && maybeAxiosError.message.trim()) {
    return maybeAxiosError.message
  }
  return '操作失败，请稍后重试'
}
</script>

<style scoped>
.ai-reduction-page {
  display: flex;
  flex-direction: column;
  gap: 24px;
  color: #17324d;
}

.hero-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 30px 34px;
  border-radius: 28px;
  background:
    radial-gradient(circle at top right, rgba(69, 127, 255, 0.28), transparent 34%),
    linear-gradient(135deg, #14254d 0%, #1e46a6 100%);
  box-shadow: 0 22px 44px rgba(20, 37, 77, 0.16);
}

.hero-copy {
  max-width: 760px;
}

.eyebrow {
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: rgba(238, 245, 255, 0.78);
}

.hero-panel h1 {
  margin: 0;
  font-size: 48px;
  line-height: 1;
  color: #ffffff;
}

.hero-panel p {
  margin: 12px 0 0;
  max-width: 680px;
  font-size: 16px;
  line-height: 1.7;
  color: rgba(238, 245, 255, 0.88);
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(300px, 360px) minmax(0, 1fr);
  gap: 24px;
  min-width: 0;
  align-items: start;
}

.control-panel,
.result-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
}

.panel-card {
  padding: 24px;
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(143, 168, 214, 0.22);
  box-shadow: 0 18px 40px rgba(27, 56, 118, 0.08);
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.panel-header h2 {
  margin: 4px 0 0;
  font-size: 18px;
  line-height: 1.35;
}

.panel-kicker {
  margin: 0;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #617898;
}

.panel-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.hidden-file-input {
  display: none;
}

.placeholder-card {
  padding: 18px 20px;
  border-radius: 20px;
  background: linear-gradient(180deg, rgba(241, 246, 255, 0.88), rgba(249, 251, 255, 0.96));
  color: #5f7189;
}

.placeholder-card p {
  margin: 0;
  line-height: 1.7;
}

.document-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.document-item {
  width: 100%;
  padding: 16px 18px;
  border: 1px solid rgba(162, 182, 219, 0.32);
  border-radius: 22px;
  background: #ffffff;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.document-item:hover {
  border-color: rgba(85, 126, 214, 0.54);
  box-shadow: 0 12px 30px rgba(52, 92, 173, 0.1);
  transform: translateY(-1px);
}

.document-item.is-active {
  border-color: #6d97ff;
  box-shadow: 0 16px 34px rgba(67, 101, 191, 0.16);
}

.document-item-top,
.document-item-meta,
.matched-node-head,
.run-summary-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.document-item-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.document-item-top strong {
  font-size: 18px;
  line-height: 1.3;
  color: #1d2d44;
}

.document-item-remove {
  padding: 0;
  background: transparent;
  color: #e85d5d;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}

.document-item-remove:focus-visible {
  outline: 2px solid rgba(109, 151, 255, 0.72);
  outline-offset: 4px;
}

.document-item-meta {
  justify-content: flex-start;
  margin-top: 8px;
  font-size: 13px;
  color: #667b99;
}

.document-item-summary {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: #54647a;
}

.settings-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.provider-hint,
.inline-hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: #60758f;
}

.warning-hint {
  margin-top: 10px;
  color: #b26114;
}

.action-stack {
  margin-top: 16px;
  display: flex;
}

.action-stack .el-button {
  width: 100%;
}

.doc-meta-grid,
.run-summary-grid,
.reduction-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.doc-meta-item,
.run-summary-item,
.meta-stat {
  padding: 16px 18px;
  border-radius: 20px;
  background: linear-gradient(180deg, rgba(243, 247, 255, 0.95), rgba(252, 253, 255, 0.98));
}

.meta-label,
.run-summary-item span,
.meta-stat span {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #7489a5;
}

.doc-meta-item strong,
.run-summary-item strong,
.meta-stat strong {
  font-size: 16px;
  line-height: 1.5;
  color: #1d2d44;
}

.run-summary-card {
  margin-top: 18px;
  padding: 18px;
  border-radius: 22px;
  background: rgba(245, 249, 255, 0.95);
  border: 1px solid rgba(162, 182, 219, 0.26);
}

.run-progress-card {
  margin-top: 16px;
  padding: 16px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(162, 182, 219, 0.22);
}

.run-progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #60758f;
}

.run-progress-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.run-summary-text,
.summary-block p,
.query-answer p,
.query-summary p,
.matched-node-item p {
  margin: 12px 0 0;
  line-height: 1.75;
  color: #40556f;
}

.run-error-text {
  margin: 12px 0 0;
  line-height: 1.75;
  color: #c14e4e;
}

.summary-block h3,
.query-answer h3 {
  margin: 0;
  font-size: 22px;
  color: #1b2d46;
}

.chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 18px;
}

.chip-item {
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(47, 122, 255, 0.1);
  color: #275cb7;
  font-size: 13px;
  font-weight: 600;
}

.suggestion-list,
.matched-node-list {
  margin-top: 22px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.inspect-title {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #69809d;
}

.suggestion-item,
.matched-node-item {
  padding: 14px 16px;
  border-radius: 18px;
  background: rgba(247, 250, 255, 0.95);
  border: 1px solid rgba(172, 190, 222, 0.26);
}

.suggestion-item {
  cursor: pointer;
  color: #315eac;
  transition: border-color 0.2s ease, transform 0.2s ease;
}

.suggestion-item:hover {
  border-color: rgba(84, 122, 208, 0.42);
  transform: translateY(-1px);
}

.query-result {
  margin-top: 22px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 13px;
}

@media (max-width: 1100px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .hero-panel {
    padding: 24px 22px;
  }

  .hero-panel h1 {
    font-size: 36px;
  }

  .panel-card {
    padding: 20px;
    border-radius: 24px;
  }

  .doc-meta-grid,
  .run-summary-grid,
  .reduction-meta-grid,
  .run-progress-grid {
    grid-template-columns: 1fr;
  }

  .panel-header,
  .document-item-top,
  .matched-node-head,
  .run-summary-head {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>

<template>
  <div class="codex-saver-page">
    <header class="page-head">
      <div>
        <div class="eyebrow">AI工具 / CodexSaver</div>
        <h1>CodexSaver</h1>
      </div>
      <div class="head-actions">
        <el-button :icon="RefreshRight" @click="loadAll">刷新</el-button>
        <el-button type="primary" :loading="saving" @click="saveConfig">保存</el-button>
      </div>
    </header>

    <section class="config-grid">
      <div class="config-block">
        <div class="block-title">模型</div>
        <div class="form-row">
          <label>来源</label>
          <el-select v-model="config.provider_id" filterable>
            <el-option
              v-for="provider in providers"
              :key="provider.id"
              :label="provider.label || provider.id"
              :value="provider.id"
            />
          </el-select>
        </div>
        <div class="form-row">
          <label>Flash</label>
          <el-input v-model="config.flash_model" placeholder="deepseek-v4-flash" />
        </div>
        <div class="form-row">
          <label>Pro</label>
          <el-input v-model="config.pro_model" placeholder="deepseek-v4-pro" />
        </div>
        <el-checkbox v-model="config.use_flash_gate">Flash 先处理，复杂任务转 Pro</el-checkbox>
        <div class="form-row">
          <label>默认决策</label>
          <el-segmented v-model="config.default_decision" :options="decisionOptions" />
        </div>
        <div class="form-row">
          <label>多模态</label>
          <el-segmented v-model="config.multimodal_decision" :options="decisionOptions" />
        </div>
      </div>

      <div class="config-block">
        <div class="block-title">应用</div>
        <el-checkbox v-model="config.auto_apply">默认自动应用 patch</el-checkbox>
        <el-checkbox v-model="config.require_verification_success">要求验证通过</el-checkbox>
        <div class="form-row">
          <label>写入边界</label>
          <el-select v-model="config.write_boundary_mode">
            <el-option label="不限制" value="none" />
            <el-option label="调用 cwd 内" value="cwd" />
            <el-option label="白名单" value="allowlist" />
          </el-select>
        </div>
        <el-input
          v-if="config.write_boundary_mode === 'allowlist'"
          v-model="allowedRootsText"
          type="textarea"
          :rows="2"
          placeholder="每行一个可写根目录"
        />
      </div>

      <div class="config-block">
        <div class="block-title">日志</div>
        <div class="form-row">
          <label>当前文件</label>
          <el-input v-model="config.log_file_name" />
        </div>
        <div class="form-row">
          <label>备份文件</label>
          <el-input v-model="config.log_backup_file_name" />
        </div>
        <div class="form-row">
          <label>上限</label>
          <el-input-number v-model="config.log_max_bytes" :min="4096" :step="65536" />
        </div>
      </div>

      <div class="config-block mcp-block">
        <div class="block-title">MCP Bearer</div>
        <div class="form-row">
          <label>URL</label>
          <el-input :model-value="mcpBearer.url" readonly />
        </div>
        <div class="form-row">
          <label>变量</label>
          <el-input :model-value="mcpBearer.environment_variable" readonly />
        </div>
        <div class="form-row">
          <label>Bearer</label>
          <el-input
            :model-value="mcpBearerDisplay"
            :type="mcpBearerVisible ? 'text' : 'password'"
            readonly
          >
            <template #append>
              <el-button
                :icon="mcpBearerVisible ? Hide : View"
                :loading="mcpBearerLoading"
                :disabled="!mcpBearer.configured"
                title="显示 MCP Bearer 值"
                @click="toggleMcpBearer"
              />
            </template>
          </el-input>
        </div>
        <div class="mcp-hint">
          Codex MCP 配置里使用 Bearer 令牌环境变量；值来自本机 CODEYUN_DEVICE_TOKEN。
        </div>
      </div>
    </section>

    <section class="section-head">
      <div>
        <h2>规则链</h2>
      </div>
      <el-button :icon="Plus" circle title="新增规则" @click="addRule" />
    </section>

    <div ref="ruleListRef" class="rule-list">
      <div v-for="(rule, index) in sortedRules" :key="rule.id" class="rule-row">
        <SortableOrderHandle :index="index" :total="sortedRules.length" size="sm" />
        <el-switch v-model="rule.enabled" />
        <el-input v-model="rule.label" class="rule-label" />
        <el-select v-model="rule.decision" class="decision-select">
          <el-option label="DeepSeek" value="deepseek" />
          <el-option label="拒绝" value="deny" />
        </el-select>
        <el-input
          :model-value="formatRuleList(rule.match.input_kinds)"
          class="rule-field"
          placeholder="input kind"
          @update:model-value="rule.match.input_kinds = parseList(String($event))"
        />
        <el-input
          :model-value="formatRuleList(rule.match.prompt_includes)"
          class="rule-field"
          placeholder="关键词"
          @update:model-value="rule.match.prompt_includes = parseList(String($event))"
        />
        <el-input
          :model-value="formatRuleList(rule.match.path_includes)"
          class="rule-field"
          placeholder="路径包含"
          @update:model-value="rule.match.path_includes = parseList(String($event))"
        />
        <el-input
          :model-value="formatRuleList(rule.match.file_extensions)"
          class="rule-field small"
          placeholder=".py,.ts"
          @update:model-value="rule.match.file_extensions = parseList(String($event))"
        />
        <el-input v-model="rule.reason" class="rule-reason" placeholder="原因" />
        <el-button :icon="Minus" circle title="删除规则" @click="removeRule(rule.id)" />
      </div>
    </div>

    <section class="preview-layout">
      <div class="preview-pane">
        <div class="section-head compact">
          <h2>预览</h2>
          <el-button :loading="previewing" @click="runPreview">判断</el-button>
        </div>
        <el-input v-model="preview.task" type="textarea" :rows="4" placeholder="任务摘要" />
        <div class="preview-fields">
          <el-input v-model="preview.cwd" placeholder="cwd" />
          <el-input v-model="preview.filesText" placeholder="文件，逗号分隔" />
          <el-input v-model="preview.inputKindsText" placeholder="input kinds，逗号分隔" />
        </div>
        <pre v-if="previewResult">{{ previewResult }}</pre>
      </div>

      <div class="preview-pane">
        <div class="section-head compact">
          <h2>状态</h2>
          <div>
            <el-button :loading="doctoring" @click="runDoctor">Doctor</el-button>
            <el-button :icon="RefreshRight" :loading="runtimeLoading" @click="refreshRuntime">刷新</el-button>
            <el-button @click="loadLogs">原始日志</el-button>
          </div>
        </div>
        <div class="status-hint">{{ runtimeHint }}</div>
        <pre v-if="doctorResult">{{ doctorResult }}</pre>
        <div class="runtime-board">
          <div class="runtime-column">
            <div class="runtime-title">
              <span>运行中</span>
              <el-tag :type="runtime.active.length ? 'warning' : 'success'" size="small">
                {{ runtime.active.length ? `${runtime.active.length} 个请求` : '空闲' }}
              </el-tag>
            </div>
            <div v-if="runtime.active.length" class="run-list">
              <div v-for="run in runtime.active" :key="run.id" class="run-card is-active">
                <div class="run-head">
                  <el-tag type="warning" size="small">{{ stageLabel(run.stage) }}</el-tag>
                  <span>{{ formatDuration(run.age_ms) }}</span>
                </div>
                <div class="run-task">{{ run.task || '无任务摘要' }}</div>
                <div class="run-meta">
                  <span>{{ run.model || '未选模型' }}</span>
                  <span v-if="run.model_tier">{{ run.model_tier }}</span>
                  <span>{{ run.input_kinds.join(', ') || 'text' }}</span>
                </div>
              </div>
            </div>
            <el-empty v-else description="当前没有正在运行的 CodexSaver 请求" />
          </div>

          <div class="runtime-column">
            <div class="runtime-title">
              <span>最近回复</span>
              <el-tag size="small">{{ runtime.recent.length }}</el-tag>
            </div>
            <div v-if="runtime.recent.length" class="run-list">
              <div v-for="run in runtime.recent" :key="run.id" class="run-card">
                <div class="run-head">
                  <el-tag :type="logTagType(run.status)" size="small">{{ statusLabel(run.status) }}</el-tag>
                  <span>{{ formatDuration(run.duration_ms || run.age_ms) }}</span>
                </div>
                <div class="run-task">{{ run.task || '无任务摘要' }}</div>
                <div class="run-summary">{{ run.summary || run.reason || run.error || '无回复摘要' }}</div>
                <div class="run-meta">
                  <span>{{ run.model || 'Codex' }}</span>
                  <span v-if="run.model_tier">{{ run.model_tier }}</span>
                  <span v-if="run.fallback">回退 {{ run.fallback }}</span>
                </div>
              </div>
            </div>
            <el-empty v-else description="还没有本进程内的运行记录" />
          </div>
        </div>
        <div v-if="logRows.length" class="log-list">
          <div v-for="row in logRows" :key="row.id" class="log-row">
            <div class="log-row-head">
              <el-tag :type="logTagType(row.status)" size="small">{{ row.status }}</el-tag>
              <span class="log-model">{{ row.model || 'Codex' }}</span>
              <span class="log-time">{{ row.duration_ms }}ms</span>
            </div>
            <div class="log-summary">{{ row.summary || row.reason || row.error || '无摘要' }}</div>
            <div class="log-meta">
              <span>{{ row.event }}</span>
              <span v-if="row.model_tier">{{ row.model_tier }}</span>
              <span v-if="row.escalated">Flash 转 Pro</span>
              <span v-if="row.fallback">回退 {{ row.fallback }}</span>
              <span v-if="row.hit_rule">{{ row.hit_rule }}</span>
              <span>{{ row.source }}</span>
            </div>
          </div>
        </div>
        <el-empty v-else-if="logsLoaded" description="没有日志。说明当前 cwd 下还没有请求经过 CodexSaver。" />
        <details v-if="logsText" class="raw-log-details">
          <summary>查看原始 JSON 日志</summary>
          <pre>{{ logsText }}</pre>
        </details>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Hide, Minus, Plus, RefreshRight, View } from '@element-plus/icons-vue'

import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import { fetchAiChatProviders, type AiChatProviderSummary } from '@/api/aiChat'
import {
  getCodexSaverConfig,
  getCodexSaverLogs,
  getCodexSaverMcpBearer,
  getCodexSaverRuntime,
  previewCodexSaverRoute,
  runCodexSaverDoctor,
  saveCodexSaverConfig,
  type CodexSaverConfig,
  type CodexSaverMcpBearerResponse,
  type CodexSaverRuntimeResponse,
  type CodexSaverRule,
} from '@/api/codexSaver'
import { useSortableList } from '@/utils/useSortableList'

const decisionOptions = [
  { label: 'DeepSeek', value: 'deepseek' },
  { label: '拒绝', value: 'deny' },
]

const emptyConfig = (): CodexSaverConfig => ({
  provider_id: 'deepseek',
  model: '',
  flash_model: 'deepseek-v4-flash',
  pro_model: 'deepseek-v4-pro',
  use_flash_gate: true,
  default_decision: 'deepseek',
  multimodal_decision: 'deny',
  auto_apply: true,
  write_boundary_mode: 'none',
  allowed_write_roots: [],
  log_file_name: '.codexsaver.log',
  log_backup_file_name: '.codexsaver.log.backup',
  log_max_bytes: 1024 * 1024,
  require_verification_success: false,
  rules: [],
})

const config = reactive<CodexSaverConfig>(emptyConfig())
const providers = ref<AiChatProviderSummary[]>([])
const saving = ref(false)
const previewing = ref(false)
const doctoring = ref(false)
const previewResult = ref('')
const doctorResult = ref('')
const logsText = ref('')
const logsLoaded = ref(false)
const runtimeLoading = ref(false)
let runtimeTimer: number | undefined
const ruleListRef = ref<HTMLElement | null>(null)
const mcpBearerLoading = ref(false)
const mcpBearerVisible = ref(false)
const mcpBearer = reactive<CodexSaverMcpBearerResponse>({
  url: 'http://localhost:8000/api/codex-saver/mcp/',
  environment_variable: 'MCP_BEARER_TOKEN',
  header_name: 'Authorization',
  header_scheme: 'Bearer',
  configured: false,
  token: '',
})

const preview = reactive({
  task: 'Update README docs for this module.',
  cwd: '',
  filesText: '',
  inputKindsText: 'text',
})

const emptyRuntime = (): CodexSaverRuntimeResponse => ({
  active: [],
  recent: [],
  now: 0,
})

const runtime = reactive<CodexSaverRuntimeResponse>(emptyRuntime())

const sortedRules = computed(() => config.rules.slice().sort((a, b) => a.order - b.order))

const allowedRootsText = computed({
  get: () => config.allowed_write_roots.join('\n'),
  set: (value: string) => {
    config.allowed_write_roots = value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
  },
})

const mcpBearerDisplay = computed(() => {
  if (!mcpBearer.configured) return '未配置 CODEYUN_DEVICE_TOKEN'
  return mcpBearerVisible.value ? mcpBearer.token : '已配置，点击眼睛查看'
})

const parseList = (value: string) => value.split(',').map((item) => item.trim()).filter(Boolean)
const formatRuleList = (values: string[]) => values.join(', ')

interface LogRow {
  id: string
  source: string
  event: string
  status: string
  model: string
  model_tier: string
  duration_ms: number
  reason: string
  summary: string
  error: string
  fallback: string
  escalated: boolean
  hit_rule: string
}

const logRows = computed<LogRow[]>(() => {
  const rows: LogRow[] = []
  for (const block of logsText.value.split(/\n(?=[A-Z]:\\|\/)/)) {
    const [source = '', ...lines] = block.split(/\r?\n/)
    for (const line of lines) {
      const text = line.trim()
      if (!text) continue
      try {
        const payload = JSON.parse(text) as Record<string, any>
        rows.push({
          id: `${source}:${rows.length}`,
          source,
          event: String(payload.event || ''),
          status: String(payload.status || payload.decision || ''),
          model: String(payload.model || ''),
          model_tier: String(payload.model_tier || ''),
          duration_ms: Number(payload.duration_ms || 0),
          reason: String(payload.reason || ''),
          summary: String(payload.summary || ''),
          error: String(payload.error || ''),
          fallback: String(payload.fallback || ''),
          escalated: Boolean(payload.escalated),
          hit_rule: Array.isArray(payload.hit_rules) && payload.hit_rules[0]
            ? String(payload.hit_rules[0].label || payload.hit_rules[0].id || '')
            : '',
        })
      } catch {
        rows.push({
          id: `${source}:${rows.length}`,
          source,
          event: 'raw',
          status: 'unknown',
          model: '',
          model_tier: '',
          duration_ms: 0,
          reason: text,
          summary: '',
          error: '',
          fallback: '',
          escalated: false,
          hit_rule: '',
        })
      }
    }
  }
  return rows.reverse()
})

const logTagType = (status: string) => {
  if (status === 'applied' || status === 'handled') return 'success'
  if (status === 'codex_required') return 'warning'
  if (status === 'failed') return 'danger'
  return 'info'
}

const runtimeHint = computed(() => {
  if (runtime.active.length) return '有请求正在运行；如果阶段和耗时长时间不变，优先检查 MCP 服务或 DeepSeek 响应。'
  return '当前没有正在运行的请求；最近回复来自本后端进程内存，重启后会清空。'
})

const stageLabel = (stage: string) => {
  const labels: Record<string, string> = {
    received: '已接收',
    routed: '已分流',
    provider: '检查模型',
    flash: 'Flash',
    pro: 'Pro',
    patch_check: '检查补丁',
    verification: '验证',
    apply: '应用',
    finished: '完成',
  }
  return labels[stage] || stage || '未知'
}

const statusLabel = (status: string) => {
  const labels: Record<string, string> = {
    handled: '已处理',
    applied: '已应用',
    codex_required: '回退 Codex',
    failed: '失败',
    running: '运行中',
  }
  return labels[status] || status || '未知'
}

const formatDuration = (ms: number) => {
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.round(ms / 100) / 10
  return `${seconds}s`
}

const normalizeRuleOrders = () => {
  sortedRules.value.forEach((rule, index) => {
    rule.order = (index + 1) * 10
  })
}

const replaceConfig = (nextConfig: CodexSaverConfig) => {
  Object.assign(config, emptyConfig(), nextConfig)
}

const loadAll = async () => {
  const [nextConfig, providerPayload, bearerPayload] = await Promise.all([
    getCodexSaverConfig(),
    fetchAiChatProviders(),
    getCodexSaverMcpBearer(false),
  ])
  replaceConfig(nextConfig)
  providers.value = providerPayload.items
  Object.assign(mcpBearer, bearerPayload)
  mcpBearerVisible.value = false
  await refreshRuntime()
}

const saveConfig = async () => {
  saving.value = true
  try {
    normalizeRuleOrders()
    replaceConfig(await saveCodexSaverConfig(config))
    ElMessage.success('已保存')
  } finally {
    saving.value = false
  }
}

const addRule = () => {
  config.rules.push({
    id: crypto.randomUUID(),
    label: '新规则',
    enabled: true,
    order: (config.rules.length + 1) * 10,
    match: {
      prompt_includes: [],
      path_includes: [],
      file_extensions: [],
      input_kinds: ['text'],
    },
    decision: 'deepseek',
    reason: '',
  })
}

const removeRule = (id: string) => {
  config.rules = config.rules.filter((rule) => rule.id !== id)
  normalizeRuleOrders()
}

const runPreview = async () => {
  previewing.value = true
  try {
    const result = await previewCodexSaverRoute({
      task: preview.task,
      cwd: preview.cwd,
      files: parseList(preview.filesText),
      input_kinds: parseList(preview.inputKindsText),
    })
    previewResult.value = JSON.stringify(result, null, 2)
  } finally {
    previewing.value = false
  }
}

const runDoctor = async () => {
  doctoring.value = true
  try {
    doctorResult.value = JSON.stringify(await runCodexSaverDoctor(preview.cwd), null, 2)
  } finally {
    doctoring.value = false
  }
}

const loadLogs = async () => {
  const payload = await getCodexSaverLogs(preview.cwd)
  logsText.value = payload.items.map((item) => `${item.path}\n${item.content}`).join('\n')
  logsLoaded.value = true
}

const refreshRuntime = async () => {
  runtimeLoading.value = true
  try {
    Object.assign(runtime, emptyRuntime(), await getCodexSaverRuntime())
  } finally {
    runtimeLoading.value = false
  }
}

const toggleMcpBearer = async () => {
  if (mcpBearerVisible.value) {
    mcpBearerVisible.value = false
    return
  }
  mcpBearerLoading.value = true
  try {
    Object.assign(mcpBearer, await getCodexSaverMcpBearer(true))
    mcpBearerVisible.value = true
  } finally {
    mcpBearerLoading.value = false
  }
}

useSortableList({
  listRef: ruleListRef,
  getDeps: () => [config.rules.length],
  onReorder: (oldIndex, newIndex) => {
    const ordered = sortedRules.value
    const [item] = ordered.splice(oldIndex, 1)
    ordered.splice(newIndex, 0, item)
    config.rules = ordered
    normalizeRuleOrders()
  },
})

watch(() => config.rules, normalizeRuleOrders, { deep: false })

onMounted(() => {
  void loadAll()
  runtimeTimer = window.setInterval(() => {
    void refreshRuntime()
  }, 1000)
})

onBeforeUnmount(() => {
  if (runtimeTimer !== undefined) {
    window.clearInterval(runtimeTimer)
  }
})
</script>

<style scoped>
.codex-saver-page {
  padding: 20px;
  color: #1f2937;
}

.page-head,
.section-head,
.rule-row,
.form-row,
.head-actions,
.preview-fields {
  display: flex;
  align-items: center;
  gap: 10px;
}

.page-head {
  justify-content: space-between;
  margin-bottom: 18px;
}

.eyebrow {
  margin-bottom: 4px;
  color: #64748b;
  font-size: 13px;
}

h1,
h2 {
  margin: 0;
  font-weight: 650;
  letter-spacing: 0;
}

h1 {
  font-size: 24px;
}

h2 {
  font-size: 16px;
}

.config-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.config-block,
.preview-pane {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 14px;
  background: #fff;
}

.block-title {
  margin-bottom: 10px;
  font-weight: 650;
}

.form-row {
  margin-bottom: 10px;
}

.form-row label {
  width: 72px;
  flex: 0 0 auto;
  color: #475569;
  font-size: 13px;
}

.form-row :deep(.el-select),
.form-row :deep(.el-input),
.form-row :deep(.el-input-number),
.form-row :deep(.el-segmented) {
  flex: 1;
}

.section-head {
  justify-content: space-between;
  margin: 16px 0 10px;
}

.section-head.compact {
  margin-top: 0;
}

.rule-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rule-row {
  min-height: 40px;
  padding: 8px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.rule-label {
  width: 150px;
}

.decision-select {
  width: 110px;
}

.rule-field {
  width: 130px;
}

.rule-field.small {
  width: 105px;
}

.rule-reason {
  min-width: 140px;
  flex: 1;
}

.preview-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
  margin-top: 18px;
}

.preview-fields {
  margin-top: 10px;
}

pre {
  max-height: 320px;
  margin: 12px 0 0;
  padding: 10px;
  overflow: auto;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  white-space: pre-wrap;
}

.log-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  margin-top: 12px;
  overflow: auto;
}

.runtime-board {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  margin-top: 12px;
}

.runtime-column {
  min-width: 0;
}

.runtime-title,
.run-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.runtime-title {
  margin-bottom: 8px;
  font-weight: 650;
}

.run-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow: auto;
}

.run-card {
  padding: 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.run-card.is-active {
  border-color: #f59e0b;
  background: #fffbeb;
}

.run-head {
  color: #64748b;
  font-size: 12px;
}

.run-task {
  margin-top: 8px;
  color: #111827;
  font-size: 13px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.run-summary {
  margin-top: 6px;
  color: #475569;
  font-size: 13px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.run-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
  color: #64748b;
  font-size: 12px;
}

.raw-log-details {
  margin-top: 12px;
  color: #475569;
  font-size: 13px;
}

.status-hint {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.mcp-hint {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.log-row {
  padding: 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
}

.log-row-head,
.log-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.log-model,
.log-time {
  color: #475569;
  font-size: 12px;
}

.log-summary {
  margin-top: 6px;
  color: #111827;
  font-size: 13px;
  line-height: 1.5;
}

.log-meta {
  flex-wrap: wrap;
  margin-top: 6px;
  color: #64748b;
  font-size: 12px;
}

@media (max-width: 1100px) {
  .config-grid,
  .preview-layout,
  .runtime-board {
    grid-template-columns: 1fr;
  }

  .rule-row {
    flex-wrap: wrap;
  }
}
</style>

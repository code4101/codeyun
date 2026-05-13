<template>
  <div class="evomind-page">
    <header class="page-head">
      <div>
        <div class="eyebrow">AI工具 / EvoMind</div>
        <h1>EvoMind</h1>
      </div>
      <div class="head-actions">
        <el-button :loading="scanning" :icon="MagicStick" @click="scanRealCases()">扫描真实记录</el-button>
        <el-button :loading="scanning" :icon="MagicStick" @click="scanLearningMarkerCases">扫描学习标记</el-button>
        <el-button :loading="scanning" :icon="RefreshRight" @click="rescanRealCases">重置缓存并重扫</el-button>
        <el-button :icon="RefreshRight" @click="resetWorkspaceState">重置</el-button>
        <el-button type="primary" :icon="Plus" @click="addCase">新增案例</el-button>
      </div>
    </header>
    <div v-if="lastScanSummary" class="scan-summary">{{ lastScanSummary }}</div>

    <section class="stage-row" aria-label="EvoMind workflow">
      <button
        v-for="stage in stageOptions"
        :key="stage.value"
        type="button"
        class="stage-button"
        :class="{ 'is-active': activeStage === stage.value }"
        @click="activeStage = stage.value"
      >
        <span>{{ stage.label }}</span>
        <strong>{{ stageCount(stage.value) }}</strong>
      </button>
    </section>

    <div class="workspace">
      <aside class="case-pane">
        <div class="pane-head">
          <h2>案例池</h2>
          <el-tag size="small" effect="plain">{{ state.cases.length }}</el-tag>
        </div>
        <div class="case-list">
          <button
            v-for="item in state.cases"
            :key="item.id"
            type="button"
            class="case-item"
            :class="{ 'is-active': item.id === state.selectedCaseId }"
            @click="state.selectedCaseId = item.id"
          >
            <span class="case-title">{{ item.title || ruleTitleFallback(item) }}</span>
            <span class="case-meta">
              <el-tag size="small" effect="plain" :type="signalTagType(item.signalType)">
                {{ signalLabel(item.signalType) }}
              </el-tag>
              <span>{{ item.domain || '未分域' }}</span>
            </span>
          </button>
        </div>
      </aside>

      <main v-if="selectedCase" class="main-pane">
        <section v-if="activeStage === 'case'" class="two-column case-stage">
          <div class="pane-section">
            <div class="pane-head">
              <h2>素材</h2>
              <el-button type="primary" :loading="caseCardGenerating" :icon="MagicStick" @click="deriveCaseCard">
                生成案例卡
              </el-button>
            </div>
            <div class="field-grid case-meta-grid">
              <label>
                <span>规则标题</span>
                <el-input
                  v-model="selectedCase.title"
                  placeholder="用短语概括可沉淀到 skill 的规则精髓，不是素材标题"
                />
              </label>
              <label>
                <span>领域</span>
                <el-input v-model="selectedCase.domain" />
              </label>
              <label>
                <span>信号</span>
                <el-select v-model="selectedCase.signalType">
                  <el-option label="显式学习标记" value="explicit_learning_marker" />
                  <el-option label="情绪/高摩擦" value="friction" />
                  <el-option label="反复纠正" value="repeated_correction" />
                  <el-option label="最终稿差异" value="final_artifact_delta" />
                </el-select>
              </label>
              <label>
                <span>强度</span>
                <el-select v-model="selectedCase.evidenceStrength">
                  <el-option label="P0" value="p0" />
                  <el-option label="P1" value="p1" />
                  <el-option label="P2" value="p2" />
                </el-select>
              </label>
            </div>
            <div v-if="selectedCase.source" class="source-line">
              <span>来源</span>
              <strong>{{ selectedCase.source.threadTitle || selectedCase.source.threadId }}</strong>
              <span>#{{ selectedCase.source.messageSeq }}</span>
              <span>score {{ selectedCase.source.score }}</span>
            </div>
            <div class="material-browser">
              <aside class="material-index" aria-label="素材结构">
                <button
                  v-for="(entry, index) in materialEntries"
                  :key="entry.key"
                  type="button"
                  class="material-item"
                  :class="{ 'is-active': entry.key === activeMaterialKey }"
                  @click="activeMaterialKey = entry.key"
                >
                  <span class="material-order">{{ index + 1 }}</span>
                  <span class="material-item-main">
                    <strong>{{ entry.label }}</strong>
                    <span>{{ entry.summary || '暂无内容' }}</span>
                  </span>
                  <el-tag size="small" effect="plain" :type="entry.tagType">{{ entry.tag }}</el-tag>
                </button>
              </aside>
              <section v-if="selectedMaterialEntry" class="material-detail">
                <div class="material-detail-head">
                  <div>
                    <h3>{{ selectedMaterialEntry.label }}</h3>
                    <span>{{ selectedMaterialEntry.hint }}</span>
                  </div>
                  <el-tag size="small" effect="plain">{{ selectedMaterialEntry.charCount }} 字</el-tag>
                </div>
                <el-input
                  v-if="selectedMaterialEntry.sourceKey"
                  :model-value="selectedMaterialEntry.value"
                  type="textarea"
                  :rows="18"
                  resize="vertical"
                  @update:model-value="updateSelectedMaterialText"
                />
                <section v-else class="evidence-group-detail">
                  <details
                    v-if="selectedMaterialEntry.processTurns?.length"
                    class="evidence-process-details"
                    open
                  >
                    <summary class="evidence-process-summary">
                      <span>过程 {{ selectedMaterialEntry.processTurns.length }} 条</span>
                      <small>同组连续消息</small>
                    </summary>
                    <section class="evidence-message-list">
                      <article
                        v-for="turn in selectedMaterialEntry.processTurns"
                        :key="`process-${turn.seq ?? turn.text}`"
                        class="evidence-message-card"
                      >
                        <div class="evidence-message-head">
                          <strong>{{ evidenceMessageTitle(turn) }}</strong>
                          <el-tag size="small" effect="plain" :type="evidenceTurnTagType(turn)">
                            {{ evidenceTurnLabel(turn) }}
                          </el-tag>
                        </div>
                        <pre class="evidence-message-text">{{ turn.text }}</pre>
                      </article>
                    </section>
                  </details>
                  <article v-if="selectedMaterialEntry.displayTurn" class="evidence-message-card">
                    <div class="evidence-message-head">
                      <strong>{{ evidenceMessageTitle(selectedMaterialEntry.displayTurn) }}</strong>
                      <el-tag size="small" effect="plain" :type="evidenceTurnTagType(selectedMaterialEntry.displayTurn)">
                        {{ evidenceTurnLabel(selectedMaterialEntry.displayTurn) }}
                      </el-tag>
                    </div>
                    <pre class="evidence-message-text">{{ selectedMaterialEntry.displayTurn.text }}</pre>
                  </article>
                </section>
              </section>
            </div>
          </div>

          <div class="pane-section">
            <div class="pane-head">
              <h2>案例卡</h2>
              <el-tag :type="statusTagType(selectedCase.status)" effect="plain">
                {{ statusLabel(selectedCase.status) }}
              </el-tag>
            </div>
            <label class="stack-field">
              <span>一句话规则</span>
              <el-input
                v-model="selectedCase.inferredRule"
                type="textarea"
                :rows="4"
                resize="vertical"
                placeholder="用完整句子表达：什么场景触发、先判断什么、具体怎么做、避免什么。"
              />
            </label>
            <div class="pattern-stack">
              <section class="pattern-block">
                <div class="minor-title">反面模式</div>
                <div class="pattern-list">
                  <div v-for="(pattern, index) in selectedCase.antiPatterns" :key="`anti-${index}`" class="pattern-row">
                    <span>{{ index + 1 }}</span>
                    <el-input
                      v-model="selectedCase.antiPatterns[index]"
                      type="textarea"
                      :autosize="{ minRows: 1, maxRows: 4 }"
                      resize="vertical"
                    />
                    <el-button :icon="Delete" circle title="删除" @click="selectedCase.antiPatterns.splice(index, 1)" />
                  </div>
                </div>
                <el-button text :icon="Plus" @click="selectedCase.antiPatterns.push('')">新增反面模式</el-button>
              </section>
              <section class="pattern-block">
                <div class="minor-title">正向范式</div>
                <div class="pattern-list">
                  <div v-for="(pattern, index) in selectedCase.positivePatterns" :key="`positive-${index}`" class="pattern-row">
                    <span>{{ index + 1 }}</span>
                    <el-input
                      v-model="selectedCase.positivePatterns[index]"
                      type="textarea"
                      :autosize="{ minRows: 1, maxRows: 4 }"
                      resize="vertical"
                    />
                    <el-button :icon="Delete" circle title="删除" @click="selectedCase.positivePatterns.splice(index, 1)" />
                  </div>
                </div>
                <el-button text :icon="Plus" @click="selectedCase.positivePatterns.push('')">新增正向范式</el-button>
              </section>
            </div>
          </div>
        </section>

        <section v-else-if="activeStage === 'proposal'" class="pane-section">
          <div class="pane-head">
            <h2>提案</h2>
            <div class="head-actions">
              <el-select v-model="proposalTarget" class="target-select">
                <el-option label="skill 优化" value="skill" />
                <el-option label="AGENTS.md" value="agents" />
                <el-option label="docs" value="docs" />
              </el-select>
              <el-button type="primary" :loading="proposalGenerating" :icon="MagicStick" @click="generateProposal">
                生成提案
              </el-button>
              <el-button :disabled="!selectedCase.proposal" @click="markProposalActive">标记生效</el-button>
            </div>
          </div>
          <div v-if="selectedCase.proposal" class="proposal-grid">
            <div class="proposal-meta">
              <label>
                <span>生命周期</span>
                <el-select v-model="selectedCase.proposal.lifecycle">
                  <el-option label="candidate" value="candidate" />
                  <el-option label="draft" value="draft" />
                  <el-option label="sandbox_passed" value="sandbox_passed" />
                  <el-option label="human_reviewed" value="human_reviewed" />
                  <el-option label="active" value="active" />
                </el-select>
              </label>
              <label>
                <span>目标</span>
                <el-input v-model="selectedCase.proposal.target" />
              </label>
              <label>
                <span>目标文件</span>
                <el-input v-model="selectedCase.proposal.targetPath" />
              </label>
              <label>
                <span>生成方式</span>
                <el-input v-model="selectedCase.proposal.generationMode" />
              </label>
              <label>
                <span>风险</span>
                <el-input v-model="selectedCase.proposal.risk" type="textarea" :rows="5" resize="vertical" />
              </label>
            </div>
            <label class="proposal-body">
              <span>Markdown 提案</span>
              <el-input v-model="selectedCase.proposal.content" type="textarea" :rows="23" resize="vertical" />
            </label>
          </div>
          <el-empty v-else description="当前案例还没有提案" />
        </section>

        <section v-else-if="activeStage === 'verify'" class="pane-section">
          <div class="pane-head">
            <h2>沙盒回放</h2>
            <div class="head-actions">
              <el-select v-model="verifyExecutor" class="executor-select">
                <el-option label="本地启发式" value="heuristic" />
                <el-option label="codex-cli 沙盒" value="codex_cli" disabled />
              </el-select>
              <el-button type="primary" @click="runReplay">运行</el-button>
            </div>
          </div>
          <div class="score-row">
            <div class="score-box">
              <span>Baseline</span>
              <strong>{{ selectedCase.evaluation?.baselineScore ?? '-' }}</strong>
            </div>
            <div class="score-box">
              <span>Candidate</span>
              <strong>{{ selectedCase.evaluation?.candidateScore ?? '-' }}</strong>
            </div>
            <div class="score-box">
              <span>少走轮次</span>
              <strong>{{ selectedCase.evaluation?.iterationSaving ?? '-' }}</strong>
            </div>
          </div>
          <template v-if="selectedCase.evaluation">
            <div class="check-list">
              <div v-for="check in selectedCase.evaluation.hardChecks" :key="check.label" class="check-row">
                <span>{{ check.label }}</span>
                <el-tag size="small" :type="check.passed ? 'success' : 'danger'" effect="plain">
                  {{ check.passed ? '通过' : '未通过' }}
                </el-tag>
              </div>
            </div>
            <div class="two-column replay-output">
              <label>
                <span>Baseline 输出倾向</span>
                <el-input v-model="selectedCase.evaluation.baselineOutput" type="textarea" :rows="8" resize="vertical" />
              </label>
              <label>
                <span>Candidate 输出倾向</span>
                <el-input v-model="selectedCase.evaluation.candidateOutput" type="textarea" :rows="8" resize="vertical" />
              </label>
            </div>
            <label class="stack-field">
              <span>评估报告</span>
              <el-input v-model="selectedCase.evaluation.report" type="textarea" :rows="8" resize="vertical" />
            </label>
          </template>
          <el-empty v-else description="当前案例还没有验证报告" />
        </section>

        <section v-else class="prompt-layout">
          <aside class="prompt-list-pane">
            <div class="pane-head">
              <h2>提示词</h2>
              <el-button :icon="Plus" circle title="新增提示词" @click="addPrompt" />
            </div>
            <div class="prompt-list">
              <button
                v-for="prompt in state.prompts"
                :key="prompt.id"
                type="button"
                class="prompt-item"
                :class="{ 'is-active': prompt.id === state.selectedPromptId }"
                @click="state.selectedPromptId = prompt.id"
              >
                <span>{{ prompt.title }}</span>
                <el-tag size="small" effect="plain" :type="prompt.enabled ? 'success' : 'info'">
                  {{ prompt.enabled ? '启用' : '停用' }}
                </el-tag>
              </button>
            </div>
          </aside>
          <section v-if="selectedPrompt" class="pane-section prompt-editor">
            <div class="pane-head">
              <h2>{{ selectedPrompt.title }}</h2>
              <div class="head-actions">
                <el-switch v-model="selectedPrompt.enabled" />
                <el-button :icon="RefreshRight" @click="resetSelectedPrompt">恢复</el-button>
                <el-button :icon="Delete" @click="removeSelectedPrompt">删除</el-button>
              </div>
            </div>
            <div class="field-grid">
              <label>
                <span>名称</span>
                <el-input v-model="selectedPrompt.title" />
              </label>
              <label>
                <span>阶段</span>
                <el-select v-model="selectedPrompt.stage">
                  <el-option label="案例捕捉" value="case" />
                  <el-option label="提案生成" value="proposal" />
                  <el-option label="沙盒评审" value="verify" />
                  <el-option label="删除防线" value="cleanup" />
                </el-select>
              </label>
              <label>
                <span>版本</span>
                <el-input v-model="selectedPrompt.version" />
              </label>
            </div>
            <label class="stack-field">
              <span>内容</span>
              <el-input v-model="selectedPrompt.content" type="textarea" :rows="25" resize="vertical" />
            </label>
          </section>
        </section>
      </main>
      <main v-else class="main-pane empty-main">
        <el-empty description="案例池为空">
          <div class="empty-actions">
            <el-button :loading="scanning" type="primary" :icon="MagicStick" @click="scanRealCases()">
              扫描真实记录
            </el-button>
            <el-button :icon="Plus" @click="addCase">新增案例</el-button>
          </div>
        </el-empty>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, MagicStick, Plus, RefreshRight } from '@element-plus/icons-vue'

import {
  consumeEvoMindPendingCaseImports,
  deriveEvoMindCaseCard,
  fetchEvoMindPendingCaseImports,
  generateEvoMindProposal,
  scanEvoMindCodexCases,
  type EvoMindCaseCandidate,
  type EvoMindEvidenceTurn,
  type EvoMindProposalCaseInput,
} from '@/api/evomind'

type StageValue = 'case' | 'proposal' | 'verify' | 'prompts'
type SignalType = 'explicit_learning_marker' | 'friction' | 'repeated_correction' | 'final_artifact_delta'
type EvidenceStrength = 'p0' | 'p1' | 'p2'
type CaseStatus = 'captured' | 'proposed' | 'verified' | 'active' | 'archived'
type PromptStage = 'case' | 'proposal' | 'verify' | 'cleanup'
type SummaryMaterialKey = 'originalTask' | 'badAttempt' | 'userCorrections' | 'finalPattern'
type MaterialKey = SummaryMaterialKey | `evidence:${number}`
type MaterialTagType = '' | 'success' | 'warning' | 'danger' | 'info'

interface MaterialField {
  key: SummaryMaterialKey
  label: string
  tag: string
  tagType: MaterialTagType
  hint: string
}

interface MaterialEntry {
  key: MaterialKey
  sourceKey?: SummaryMaterialKey
  label: string
  tag: string
  tagType: MaterialTagType
  hint: string
  value: string
  summary: string
  charCount: number
  turns?: EvoMindEvidenceTurn[]
  displayTurn?: EvoMindEvidenceTurn
  processTurns?: EvoMindEvidenceTurn[]
}

interface Proposal {
  id: string
  target: string
  targetType?: string
  targetPath?: string
  targetStatus?: string
  lifecycle: 'candidate' | 'draft' | 'sandbox_passed' | 'human_reviewed' | 'active'
  title?: string
  trigger?: string
  ruleText?: string
  scope?: string
  antiScope?: string
  content: string
  risk: string
  verificationPlan?: string[]
  generationMode?: string
  warning?: string
  createdAt: string
}

interface EvaluationCheck {
  label: string
  passed: boolean
}

interface Evaluation {
  id: string
  executor: string
  baselineScore: number
  candidateScore: number
  iterationSaving: number
  baselineOutput: string
  candidateOutput: string
  hardChecks: EvaluationCheck[]
  report: string
  createdAt: string
}

interface EvoCaseSource {
  rootDir: string
  threadId: string
  threadTitle: string
  messageSeq: number | null
  timestamp: string
  projectLabel: string
  workspaceRoot: string
  score: number
}

interface EvoCase {
  id: string
  title: string
  domain: string
  signalType: SignalType
  evidenceStrength: EvidenceStrength
  frictionLevel: 'low' | 'medium' | 'high'
  originalTask: string
  badAttempt: string
  userCorrections: string
  finalPattern: string
  inferredRule: string
  antiPatterns: string[]
  positivePatterns: string[]
  evidenceTurns: EvoMindEvidenceTurn[]
  status: CaseStatus
  proposal: Proposal | null
  evaluation: Evaluation | null
  source?: EvoCaseSource | null
  createdAt: string
  updatedAt: string
}

interface PromptTemplate {
  id: string
  title: string
  stage: PromptStage
  version: string
  enabled: boolean
  content: string
}

interface EvoMindState {
  cases: EvoCase[]
  prompts: PromptTemplate[]
  selectedCaseId: string
  selectedPromptId: string
}

const STORAGE_KEY = 'codeyun_evomind_workbench_v1'
const CASE_CARD_REWRITE_KEY = 'codeyun_evomind_case_card_rewrite_v3'
const REMOVED_DEMO_CASE_IDS = new Set(['case_ui_elegance', 'case_explicit_marker', 'real_019e1b14_5'])

const stageOptions: Array<{ label: string; value: StageValue }> = [
  { label: '案例', value: 'case' },
  { label: '提案', value: 'proposal' },
  { label: '验证', value: 'verify' },
  { label: '提示词', value: 'prompts' },
]

const activeStage = ref<StageValue>('case')
const activeMaterialKey = ref<MaterialKey>('userCorrections')
const proposalTarget = ref<'skill' | 'agents' | 'docs'>('skill')
const verifyExecutor = ref('heuristic')
const scanning = ref(false)
const caseCardGenerating = ref(false)
const proposalGenerating = ref(false)
const lastScanSummary = ref('')
const materialFields: MaterialField[] = [
  {
    key: 'originalTask',
    label: '原始请求',
    tag: '任务',
    tagType: 'info',
    hint: '用户最初要完成的业务动作或问题。',
  },
  {
    key: 'badAttempt',
    label: 'AI 初始问题',
    tag: '反面',
    tagType: 'warning',
    hint: 'AI 初稿、误解、遗漏或导致用户纠正的做法。',
  },
  {
    key: 'userCorrections',
    label: '用户纠正',
    tag: '关键',
    tagType: 'danger',
    hint: '用户明确指出的问题、偏好、业务边界和修正方向。',
  },
  {
    key: 'finalPattern',
    label: '最终范式',
    tag: '正向',
    tagType: 'success',
    hint: '后续回答、最终实现或可迁移的正向做法。',
  },
]

const makeId = (prefix: string) => `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
const nowText = () => new Date().toISOString()
type ScanRealCasesOptions = {
  maxThreads?: number
  maxCases?: number
  minScore?: number
  signalType?: SignalType | null
  label?: string
  quiet?: boolean
  resetCache?: boolean
}

function createDefaultPrompts(): PromptTemplate[] {
  return [
    {
      id: 'case_extractor',
      title: '案例捕捉',
      stage: 'case',
      version: 'v1',
      enabled: true,
      content: [
        '从会话轨迹中提取可复用案例卡。',
        '优先捕捉真实业务操作：页面、文件、接口、测试、命令、数据、UI 或业务对象的失败-纠正-最终做法。',
        '纯讨论、方案设想、概念说明、提示词讨论和元学习内容只能作为低优先级背景，不能作为默认首选案例。',
        '显式学习标记、用户明显生气、反复纠正、AI 初稿与最终稿结构差异需要回溯到具体操作对象后再入池。',
        '不要沉淀骂人文本本身，要抽取背后的失败模式、正向范式和适用边界。',
        '标题必须概括学到的规则精髓，不要复用素材标题、线程标题或具体任务对象名。',
        '输出字段：规则标题、反面模式、正向范式、可迁移规则、证据强度、适用范围、排除范围。',
      ].join('\n'),
    },
    {
      id: 'proposal_writer',
      title: '提案生成',
      stage: 'proposal',
      version: 'v1',
      enabled: true,
      content: [
        '根据案例卡生成 skill / AGENTS.md / docs 的变更提案。',
        '优化 skill 是核心目标；AGENTS.md 和 docs 只在规则明显属于项目级协作约定或设计说明时作为补充目标。',
        '必须说明为什么值得沉淀、会减少哪类用户迭代、风险是什么、如何回滚。',
        '不得直接写入正式规则；只生成可审查 proposal。',
      ].join('\n'),
    },
    {
      id: 'replay_judge',
      title: '回放评审',
      stage: 'verify',
      version: 'v1',
      enabled: true,
      content: [
        '对比 baseline 与 candidate 在同一历史任务上的表现。',
        '评分关注：是否避开反面模式、是否命中正向范式、是否减少用户纠正轮次、是否引入新坏习惯。',
        '禁止把训练用案例当作唯一通过依据；需要保留可复查的报告。',
      ].join('\n'),
    },
    {
      id: 'cleanup_guard',
      title: '删除防线',
      stage: 'cleanup',
      version: 'v1',
      enabled: true,
      content: [
        '删除或废弃 skill 前必须区分低频与低价值。',
        '低频但高风险兜底的 skill 不能直接删除。',
        '优先标记 deprecated / archived，并保留恢复路径。',
      ].join('\n'),
    },
  ]
}

function createDefaultState(): EvoMindState {
  const prompts = createDefaultPrompts()
  return {
    cases: [],
    prompts,
    selectedCaseId: '',
    selectedPromptId: prompts[0]?.id || '',
  }
}

function hasConcreteOperationSignal(item: EvoCase) {
  const haystack = [
    item.title,
    item.domain,
    item.originalTask,
    item.badAttempt,
    item.userCorrections,
    item.finalPattern,
    ...item.evidenceTurns.map((turn) => turn.text),
  ].join('\n')
  return /https?:\/\/|localhost|[A-Za-z]:[\\/]|\/api\/|\.(py|ts|tsx|js|vue|md|json|xlsx?)\b/i.test(haystack)
    || /<image>|截图|页面|按钮|右键|菜单|tab|sheet|表格|字段|列|行|单元格/i.test(haystack)
    || /文件|目录|数据库|缓存|接口|日志|报错|测试|运行|命令|脚本|函数|代码|配置|token|设备/.test(haystack)
    || /样式|排版|颜色|高度|宽度|加载|刷新|删除|重命名|筛选|排序|权限|鉴权/.test(haystack)
}

function shouldDropStoredCase(item: EvoCase) {
  if (REMOVED_DEMO_CASE_IDS.has(item.id)) return true
  if (!item.source || item.domain !== 'meta_learning') return false
  const haystack = `${item.title}\n${item.originalTask}\n${item.userCorrections}`
  return /EvoMind|样例|案例池|学习素材|提示词|skill|AGENTS|docs/i.test(haystack) && !hasConcreteOperationSignal(item)
}

function isWeakGeneratedRule(value: string) {
  const text = value.replace(/\s+/g, '')
  return !text
    || text.includes('先识别用户真正参考什么做判断以及下一步动作')
    || text.includes('功能完整前提下减少控件、概念和重复事实')
}

function isWeakPatternText(value: string) {
  const text = value.replace(/\s+/g, '')
  return !text
    || /^(对|好|嗯|是|不是|感觉|好像|这里|这个)[，,。.！!？?]?/.test(text)
    || text.includes('对，你感觉没错')
    || text.includes('好像还是大啊')
    || text.includes('先识别用户真正参考什么做判断以及下一步动作')
    || text.includes('功能完整前提下减少控件、概念和重复事实')
}

function ruleTitleFallback(item: EvoCase) {
  const rule = item.inferredRule.replace(/^(当|在|若|如果|遇到)/, '').replace(/[，；。].*$/, '').trim()
  return rule ? rule.slice(0, 18) : '待提炼规则'
}

function caseTitleNeedsRuleRewrite(item: EvoCase) {
  const title = item.title.trim()
  if (!title) return true
  if (/^(高摩擦|反复纠正|显式学习|最终差异|真实|Codex判断)[：:]/.test(title)) return true
  if (/案例|素材|线程|会话|方案讨论|问题修复/.test(title)) return true
  const materialTokens = ['新增文件', '敏感信息', '文件清单', '右栏', '左栏', '按钮', '菜单', '接口', '字段', '表格', '页面']
  const concreteHitCount = materialTokens.filter((token) => title.includes(token)).length
  return concreteHitCount >= 2
}

function caseCardNeedsCodexRewrite(item: EvoCase) {
  if (caseTitleNeedsRuleRewrite(item)) return true
  if (isWeakGeneratedRule(item.inferredRule)) return true
  return [...item.antiPatterns, ...item.positivePatterns].some(isWeakPatternText)
}

function simpleHash(value: string) {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0
  }
  return Math.abs(hash).toString(36)
}

function caseRewriteSignature(item: EvoCase) {
  return simpleHash([
    item.id,
    item.title,
    item.inferredRule,
    item.antiPatterns.join('|'),
    item.positivePatterns.join('|'),
    item.evidenceTurns.map((turn) => `${turn.seq}:${turn.role}:${turn.kind || ''}:${turn.label || ''}:${turn.text}`).join('|'),
  ].join('\n'))
}

function loadRewrittenCaseSignatures() {
  if (typeof window === 'undefined' || !window.localStorage) return new Set<string>()
  try {
    const parsed = JSON.parse(window.localStorage.getItem(CASE_CARD_REWRITE_KEY) || '[]')
    return new Set(Array.isArray(parsed) ? parsed.filter((item) => typeof item === 'string') : [])
  } catch {
    return new Set<string>()
  }
}

function saveRewrittenCaseSignatures(signatures: Set<string>) {
  if (typeof window === 'undefined' || !window.localStorage) return
  window.localStorage.setItem(CASE_CARD_REWRITE_KEY, JSON.stringify(Array.from(signatures).slice(-80)))
}

function buildReadableInferredRule(item: EvoCase) {
  if (/简洁|优雅|冗余|臃肿|复杂|重复|多余|过度/.test(item.userCorrections)) {
    return [
      '当用户纠正界面或方案过度复杂、重复、冗余时，不要只做局部缩小或表面精简；',
      '先确认用户正在依赖哪些信息做判断、下一步要执行什么动作，再删掉不服务这个判断的控件、概念和重复信息。',
    ].join('')
  }
  const corrections = toLines(item.userCorrections)
  const finalPatterns = toLines(item.finalPattern)
  const seed = finalPatterns[0] || corrections[0] || '把用户纠正抽象成可迁移判断规则，并保留适用边界'
  return `遇到同类任务时，不要直接复用默认方案；先根据用户纠正确认失败点，再按最终范式执行：${seed.replace(/[。；;]+$/, '')}。`
}

function normalizeStoredCase(item: EvoCase) {
  const normalized = {
    ...item,
    evidenceTurns: Array.isArray(item.evidenceTurns) ? item.evidenceTurns : [],
  }
  if (isWeakGeneratedRule(normalized.inferredRule)) {
    return {
      ...normalized,
      inferredRule: buildReadableInferredRule(normalized),
    }
  }
  return normalized
}

function loadState(): EvoMindState {
  if (typeof window === 'undefined' || !window.localStorage) {
    return createDefaultState()
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return createDefaultState()
    const parsed = JSON.parse(raw) as Partial<EvoMindState>
    if (!Array.isArray(parsed.cases) || !Array.isArray(parsed.prompts)) {
      return createDefaultState()
    }
    const cases = (parsed.cases as EvoCase[])
      .filter((item) => !shouldDropStoredCase(item))
      .map(normalizeStoredCase)
    return {
      cases,
      prompts: parsed.prompts as PromptTemplate[],
      selectedCaseId: cases.some((item) => item.id === parsed.selectedCaseId) ? parsed.selectedCaseId || '' : cases[0]?.id || '',
      selectedPromptId: parsed.selectedPromptId || parsed.prompts[0]?.id || '',
    }
  } catch {
    return createDefaultState()
  }
}

const state = reactive<EvoMindState>(loadState())

const selectedCase = computed(() => state.cases.find((item) => item.id === state.selectedCaseId) ?? null)
const selectedPrompt = computed(() => state.prompts.find((item) => item.id === state.selectedPromptId) ?? null)
const enabledPromptCount = computed(() => state.prompts.filter((item) => item.enabled).length)
const materialEntries = computed<MaterialEntry[]>(() => {
  const item = selectedCase.value
  if (!item) return []
  const summaryEntries = materialFields.map((field) => {
    const value = item[field.key] || ''
    return {
      ...field,
      sourceKey: field.key,
      value,
      summary: compactSummary(value),
      charCount: value.trim().length,
    }
  })
  return [...summaryEntries, ...buildEvidenceGroupEntries(item.evidenceTurns)]
})
const selectedMaterialEntry = computed(() => {
  return materialEntries.value.find((entry) => entry.key === activeMaterialKey.value) ?? materialEntries.value[0] ?? null
})

watch(
  state,
  (value) => {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
    }
  },
  { deep: true },
)

onMounted(() => {
  void repairExistingCaseCards()
  void importPendingCaseImportsWithRetry()
})

function stageCount(stage: StageValue) {
  if (stage === 'case') return state.cases.length
  if (stage === 'proposal') return state.cases.filter((item) => item.proposal).length
  if (stage === 'verify') return state.cases.filter((item) => item.evaluation).length
  return enabledPromptCount.value
}

function toLines(value: string) {
  return value
    .split(/\r?\n|；|;/)
    .map((item) => item.trim().replace(/^[-*\d.、\s]+/, ''))
    .filter(Boolean)
}

function compactSummary(value: string, limit = 72) {
  const text = value.replace(/\s+/g, ' ').trim()
  if (text.length <= limit) return text
  return `${text.slice(0, limit - 1)}…`
}

function evidenceRoleLabel(role: string) {
  if (role === 'user') return '用户'
  if (role === 'assistant') return 'AI'
  if (role === 'system') return '系统'
  return role || '未知'
}

function evidenceTurnLabel(turn: EvoMindEvidenceTurn) {
  if (turn.label) return turn.label
  if (turn.is_signal) return '关键'
  if (turn.role === 'assistant') return '反面'
  return '背景'
}

function evidenceTurnTagType(turn: EvoMindEvidenceTurn): MaterialTagType {
  if (turn.kind === 'key' || turn.is_signal) return 'danger'
  if (turn.kind === 'anti') return 'warning'
  if (turn.kind === 'positive') return 'success'
  if (turn.kind === 'task' || turn.kind === 'correction') return 'info'
  return ''
}

function evidenceMessageTitle(turn: EvoMindEvidenceTurn) {
  const seq = turn.seq ? `对话 ${turn.seq}` : '对话'
  return `${seq} · ${evidenceRoleLabel(turn.role)}`
}

function evidenceGroupSeqRange(turns: EvoMindEvidenceTurn[]) {
  const seqs = turns.map((turn) => turn.seq).filter((seq): seq is number => typeof seq === 'number')
  if (!seqs.length) return ''
  const first = Math.min(...seqs)
  const last = Math.max(...seqs)
  return first === last ? `对话 ${first}` : `对话 ${first}-${last}`
}

function evidenceGroupTag(turns: EvoMindEvidenceTurn[]) {
  const signalTurn = turns.find((turn) => turn.kind === 'key' || turn.is_signal)
  if (signalTurn) return evidenceTurnLabel(signalTurn)
  const positiveTurn = turns.find((turn) => turn.kind === 'positive')
  if (positiveTurn) return evidenceTurnLabel(positiveTurn)
  const antiTurn = turns.find((turn) => turn.kind === 'anti')
  if (antiTurn) return evidenceTurnLabel(antiTurn)
  const correctionTurn = turns.find((turn) => turn.kind === 'correction')
  if (correctionTurn) return evidenceTurnLabel(correctionTurn)
  return evidenceTurnLabel(turns[0] || { role: '', text: '' })
}

function evidenceGroupTagType(turns: EvoMindEvidenceTurn[]): MaterialTagType {
  if (turns.some((turn) => turn.kind === 'key' || turn.is_signal)) return 'danger'
  if (turns.some((turn) => turn.kind === 'positive')) return 'success'
  if (turns.some((turn) => turn.kind === 'anti')) return 'warning'
  if (turns.some((turn) => turn.kind === 'task' || turn.kind === 'correction')) return 'info'
  return ''
}

function buildEvidenceGroupEntries(turns: EvoMindEvidenceTurn[]) {
  const entries: MaterialEntry[] = []
  let index = 0
  let userTurnIndex = 0
  let groupIndex = 0

  while (index < turns.length) {
    const role = turns[index].role
    const group: EvoMindEvidenceTurn[] = []
    while (index < turns.length && turns[index].role === role) {
      group.push(turns[index])
      index += 1
    }
    if (!group.length) continue
    if (role === 'user') userTurnIndex += 1

    const displayTurn = group[group.length - 1]
    const processTurns = group.slice(0, -1)
    const roleLabel = evidenceRoleLabel(role)
    const turnLabel = userTurnIndex ? `回合 ${userTurnIndex}` : '证据'
    const seqRange = evidenceGroupSeqRange(group)
    const countText = group.length > 1 ? `，共 ${group.length} 条${roleLabel}消息` : ''
    entries.push({
      key: `evidence:${groupIndex}` as MaterialKey,
      label: `${turnLabel} · ${roleLabel}`,
      tag: evidenceGroupTag(group),
      tagType: evidenceGroupTagType(group),
      hint: [seqRange, countText.replace(/^，/, '')].filter(Boolean).join(' · '),
      value: group.map((turn) => turn.text || '').join('\n\n'),
      summary: compactSummary(displayTurn.text || ''),
      charCount: group.reduce((total, turn) => total + (turn.text || '').trim().length, 0),
      turns: group,
      displayTurn,
      processTurns,
    })
    groupIndex += 1
  }

  return entries
}

function updateSelectedMaterialText(value: string) {
  if (!selectedCase.value || !selectedMaterialEntry.value) return
  if (selectedMaterialEntry.value.sourceKey) {
    selectedCase.value[selectedMaterialEntry.value.sourceKey] = value
  }
  selectedCase.value.updatedAt = nowText()
}

function buildPromptRuleText(stage: PromptStage) {
  return state.prompts
    .filter((prompt) => prompt.enabled && prompt.stage === stage)
    .map((prompt) => [`# ${prompt.title} ${prompt.version}`, prompt.content].join('\n'))
    .join('\n\n')
}

function buildScanRuleText() {
  return buildPromptRuleText('case')
}

function buildProposalRuleText() {
  return buildPromptRuleText('proposal')
}

function serializeCaseForApi(item: EvoCase): EvoMindProposalCaseInput {
  return {
    id: item.id,
    title: item.title,
    domain: item.domain,
    signal_type: item.signalType,
    evidence_strength: item.evidenceStrength,
    friction_level: item.frictionLevel,
    original_task: item.originalTask,
    bad_attempt: item.badAttempt,
    user_corrections: item.userCorrections,
    final_pattern: item.finalPattern,
    inferred_rule: item.inferredRule,
    anti_patterns: item.antiPatterns,
    positive_patterns: item.positivePatterns,
    evidence_turns: item.evidenceTurns,
    source: item.source
      ? {
          root_dir: item.source.rootDir,
          thread_id: item.source.threadId,
          thread_title: item.source.threadTitle,
          message_seq: item.source.messageSeq,
          timestamp: item.source.timestamp,
          project_label: item.source.projectLabel,
          workspace_root: item.source.workspaceRoot,
          score: item.source.score,
        }
      : null,
  }
}

async function applyCaseCardResponse(item: EvoCase, response: Awaited<ReturnType<typeof deriveEvoMindCaseCard>>) {
  item.title = response.title
  item.domain = response.domain
  item.signalType = normalizeSignalType(response.signal_type)
  item.evidenceStrength = normalizeEvidenceStrength(response.evidence_strength)
  item.frictionLevel = response.friction_level === 'high' ? 'high' : response.friction_level === 'low' ? 'low' : 'medium'
  item.originalTask = response.original_task
  item.badAttempt = response.bad_attempt
  item.userCorrections = response.user_corrections
  item.finalPattern = response.final_pattern
  item.inferredRule = response.inferred_rule
  item.antiPatterns = [...response.anti_patterns]
  item.positivePatterns = [...response.positive_patterns]
  item.evidenceTurns = [...(response.evidence_turns || item.evidenceTurns || [])]
  item.status = 'captured'
  item.updatedAt = nowText()
}

async function repairExistingCaseCards() {
  const signatures = loadRewrittenCaseSignatures()
  const targets = state.cases
    .filter((item) => caseCardNeedsCodexRewrite(item))
    .filter((item) => !signatures.has(`${item.id}:${caseRewriteSignature(item)}`))
    .slice(0, 3)
  if (!targets.length) return

  caseCardGenerating.value = true
  let repaired = 0
  try {
    for (const item of targets) {
      const beforeSignature = `${item.id}:${caseRewriteSignature(item)}`
      const response = await deriveEvoMindCaseCard({
        case: serializeCaseForApi(item),
        case_rule_text: buildScanRuleText(),
      })
      await applyCaseCardResponse(item, response)
      signatures.add(beforeSignature)
      repaired += 1
    }
    saveRewrittenCaseSignatures(signatures)
    if (repaired) {
      ElMessage.success(`已用 Codex CLI 修正 ${repaired} 个已有案例卡`)
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    caseCardGenerating.value = false
  }
}

async function deriveCaseCard() {
  if (!selectedCase.value) return
  const item = selectedCase.value
  caseCardGenerating.value = true
  try {
    const response = await deriveEvoMindCaseCard({
      case: serializeCaseForApi(item),
      case_rule_text: buildScanRuleText(),
    })
    await applyCaseCardResponse(item, response)
    ElMessage.success(response.generation_mode === 'codex_cli' ? 'Codex CLI 案例卡已生成' : '案例卡已更新')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    caseCardGenerating.value = false
  }
}

function normalizeProposalLifecycle(value: string): Proposal['lifecycle'] {
  if (
    value === 'candidate'
    || value === 'draft'
    || value === 'sandbox_passed'
    || value === 'human_reviewed'
    || value === 'active'
  ) {
    return value
  }
  return 'candidate'
}

async function generateProposal() {
  if (!selectedCase.value) return
  const item = selectedCase.value
  proposalGenerating.value = true
  try {
    const response = await generateEvoMindProposal({
      target: proposalTarget.value,
      use_codex_cli: true,
      proposal_rule_text: buildProposalRuleText(),
      case: serializeCaseForApi(item),
    })
    item.proposal = {
      id: response.id,
      target: response.target,
      targetType: response.target_type,
      targetPath: response.target_path,
      targetStatus: response.target_status,
      lifecycle: normalizeProposalLifecycle(response.lifecycle),
      title: response.title,
      trigger: response.trigger,
      ruleText: response.rule_text,
      scope: response.scope,
      antiScope: response.anti_scope,
      content: response.content,
      risk: response.risk,
      verificationPlan: response.verification_plan,
      generationMode: response.generation_mode,
      warning: response.warning,
      createdAt: response.created_at || nowText(),
    }
    if (response.rule_text) {
      item.inferredRule = response.rule_text
    }
    if (response.anti_patterns.length) {
      item.antiPatterns = [...response.anti_patterns]
    }
    if (response.positive_patterns.length) {
      item.positivePatterns = [...response.positive_patterns]
    }
    item.status = 'proposed'
    item.updatedAt = nowText()
    activeStage.value = 'proposal'
    if (response.warning) {
      ElMessage.warning(response.warning)
    } else {
      ElMessage.success(response.generation_mode === 'codex_cli' ? 'Codex CLI 提案已生成' : '提案已生成')
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    proposalGenerating.value = false
  }
}

function runReplay() {
  if (!selectedCase.value) return
  const item = selectedCase.value
  const antiPenalty = Math.min(30, item.antiPatterns.filter(Boolean).length * 6)
  const frictionPenalty = item.frictionLevel === 'high' ? 8 : item.frictionLevel === 'medium' ? 4 : 0
  const promptBoost = state.prompts.filter((prompt) => prompt.enabled).length * 2
  const patternBoost = item.positivePatterns.filter(Boolean).length * 5
  const baselineScore = clampScore(64 - antiPenalty - frictionPenalty)
  const candidateScore = clampScore(baselineScore + 18 + patternBoost + promptBoost)
  const iterationSaving = Math.max(1, Math.min(5, Math.round((candidateScore - baselineScore) / 12)))
  item.evaluation = {
    id: makeId('eval'),
    executor: verifyExecutor.value,
    baselineScore,
    candidateScore,
    iterationSaving,
    baselineOutput: [
      '倾向：复用通用默认方案。',
      `高风险点：${item.antiPatterns[0] || '未识别反面模式'}`,
      '预计仍需要用户指出结构、边界或冗余问题。',
    ].join('\n'),
    candidateOutput: [
      '倾向：先检查案例卡反面模式，再应用正向范式。',
      `命中规则：${item.inferredRule || '暂无规则'}`,
      `预计减少 ${iterationSaving} 轮同类纠正。`,
    ].join('\n'),
    hardChecks: [
      { label: '提案存在', passed: Boolean(item.proposal) },
      { label: '反面模式非空', passed: item.antiPatterns.some(Boolean) },
      { label: '正向范式非空', passed: item.positivePatterns.some(Boolean) },
      { label: '提示词可见可控', passed: state.prompts.length > 0 },
    ],
    report: [
      `Candidate 比 baseline 高 ${candidateScore - baselineScore} 分。`,
      `主要改善来自：${item.positivePatterns.slice(0, 2).join('；') || '正向范式待补充'}。`,
      '当前执行器是本地启发式，只验证闭环结构；接入 codex-cli 后应替换为真实 worktree 回放。',
    ].join('\n'),
    createdAt: nowText(),
  }
  if (item.proposal) {
    item.proposal.lifecycle = 'sandbox_passed'
  }
  item.status = 'verified'
  item.updatedAt = nowText()
  ElMessage.success('回放报告已生成')
}

function clampScore(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)))
}

function normalizeSignalType(value: string): SignalType {
  if (
    value === 'explicit_learning_marker'
    || value === 'friction'
    || value === 'repeated_correction'
    || value === 'final_artifact_delta'
  ) {
    return value
  }
  return 'repeated_correction'
}

function normalizeEvidenceStrength(value: string): EvidenceStrength {
  if (value === 'p0' || value === 'p1' || value === 'p2') {
    return value
  }
  return 'p2'
}

function mapCandidateToCase(candidate: EvoMindCaseCandidate): EvoCase {
  return {
    id: candidate.id,
    title: candidate.title,
    domain: candidate.domain,
    signalType: normalizeSignalType(candidate.signal_type),
    evidenceStrength: normalizeEvidenceStrength(candidate.evidence_strength),
    frictionLevel: candidate.friction_level === 'high' ? 'high' : candidate.friction_level === 'low' ? 'low' : 'medium',
    originalTask: candidate.original_task,
    badAttempt: candidate.bad_attempt,
    userCorrections: candidate.user_corrections,
    finalPattern: candidate.final_pattern,
    inferredRule: candidate.inferred_rule,
    antiPatterns: [...candidate.anti_patterns],
    positivePatterns: [...candidate.positive_patterns],
    evidenceTurns: [...(candidate.evidence_turns || [])],
    status: 'captured',
    proposal: null,
    evaluation: null,
    source: {
      rootDir: candidate.source.root_dir,
      threadId: candidate.source.thread_id || '',
      threadTitle: candidate.source.thread_title || '',
      messageSeq: candidate.source.message_seq ?? null,
      timestamp: candidate.source.timestamp || '',
      projectLabel: candidate.source.project_label || '',
      workspaceRoot: candidate.source.workspace_root || '',
      score: candidate.source.score,
    },
    createdAt: nowText(),
    updatedAt: nowText(),
  }
}

async function scanRealCases(options: ScanRealCasesOptions = {}) {
  scanning.value = true
  try {
    const importLabel = options.label || '真实案例'
    const payload = await scanEvoMindCodexCases({
      max_threads: options.maxThreads ?? 240,
      max_cases: options.maxCases ?? 40,
      min_score: options.minScore ?? 55,
      signal_type: options.signalType ?? null,
      use_codex_cli: true,
      codex_cli_limit: options.maxCases ?? 40,
      reset_cache: Boolean(options.resetCache),
      scan_rule_text: buildScanRuleText(),
    })
    const existingIds = new Set(state.cases.map((item) => item.id))
    const imported = payload.items
      .filter((item) => !existingIds.has(item.id))
      .map(mapCandidateToCase)
    state.cases = [...imported, ...state.cases]
    if (imported[0]) {
      state.selectedCaseId = imported[0].id
      activeStage.value = 'case'
    }
    const scanMode = payload.analysis_mode === 'codex_cli_cache'
      ? 'Codex CLI 缓存命中'
      : payload.codex_cli_used ? 'Codex CLI 语义筛选' : '本地预筛选'
    const cacheText = payload.codex_cli_used
      ? `，缓存 ${payload.cache_hit_count}/${payload.cache_hit_count + payload.cache_miss_count}，规则 ${payload.cache_rule_hash.slice(0, 8)}`
      : ''
    lastScanSummary.value = `${importLabel}：${scanMode}，扫描 ${payload.scanned_threads}/${payload.total_threads} 个会话，预筛 ${payload.heuristic_candidate_count} 个候选，命中 ${payload.items.length} 个候选，导入 ${imported.length} 个新案例${cacheText}。`
    if (options.quiet) {
      return
    }
    if (imported.length) {
      ElMessage.success(`已导入 ${imported.length} 个${importLabel}`)
    } else {
      ElMessage.info(`没有新的${importLabel}可导入`)
    }
  } catch (error) {
    if (!options.quiet) {
      ElMessage.error(getErrorMessage(error))
    }
  } finally {
    scanning.value = false
  }
}

async function scanLearningMarkerCases() {
  await scanRealCases({
    maxThreads: 500,
    maxCases: 80,
    minScore: 0,
    signalType: 'explicit_learning_marker',
    label: '学习标记案例',
  })
}

function mergeCandidateCases(candidates: EvoMindCaseCandidate[]) {
  const existingIds = new Set(state.cases.map((item) => item.id))
  const imported = candidates
    .filter((item) => !existingIds.has(item.id))
    .map(mapCandidateToCase)
  if (!imported.length) return imported
  state.cases = [...imported, ...state.cases]
  state.selectedCaseId = imported[0].id
  activeStage.value = 'case'
  return imported
}

async function importPendingCaseImports() {
  const payload = await fetchEvoMindPendingCaseImports()
  if (!payload.items.length) return false
  const imported = mergeCandidateCases(payload.items)
  await consumeEvoMindPendingCaseImports()
  lastScanSummary.value = `待导入案例：读取 ${payload.items.length} 个候选，导入 ${imported.length} 个新案例。`
  return true
}

function importPendingCaseImportsWithRetry() {
  const delays = [0, 5000, 15000, 45000]
  delays.forEach((delay) => {
    window.setTimeout(() => {
      void importPendingCaseImports().catch(() => {
        // Backend may still be restarting after code changes; later retries cover it.
      })
    }, delay)
  })
}

async function rescanRealCases() {
  try {
    await ElMessageBox.confirm('重扫会清空 EvoMind 后端扫描缓存，并重新调用 Codex CLI 分析候选。', '重扫真实记录', {
      type: 'warning',
      confirmButtonText: '重扫',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await scanRealCases({ resetCache: true })
}

function markProposalActive() {
  if (!selectedCase.value?.proposal) return
  selectedCase.value.proposal.lifecycle = 'active'
  selectedCase.value.status = 'active'
  selectedCase.value.updatedAt = nowText()
  ElMessage.success('已标记为 active')
}

function addCase() {
  const item: EvoCase = {
    id: makeId('case'),
    title: '新案例',
    domain: 'meta_learning',
    signalType: 'explicit_learning_marker',
    evidenceStrength: 'p0',
    frictionLevel: 'medium',
    originalTask: '',
    badAttempt: '',
    userCorrections: '',
    finalPattern: '',
    inferredRule: '',
    antiPatterns: [],
    positivePatterns: [],
    evidenceTurns: [],
    status: 'captured',
    proposal: null,
    evaluation: null,
    source: null,
    createdAt: nowText(),
    updatedAt: nowText(),
  }
  state.cases.unshift(item)
  state.selectedCaseId = item.id
  activeStage.value = 'case'
}

function addPrompt() {
  const prompt: PromptTemplate = {
    id: makeId('prompt'),
    title: '新提示词',
    stage: 'case',
    version: 'v1',
    enabled: true,
    content: '',
  }
  state.prompts.unshift(prompt)
  state.selectedPromptId = prompt.id
}

async function removeSelectedPrompt() {
  if (!selectedPrompt.value) return
  if (state.prompts.length <= 1) {
    ElMessage.warning('至少保留一个提示词')
    return
  }
  try {
    await ElMessageBox.confirm(`删除提示词：${selectedPrompt.value.title}`, '删除提示词', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  const id = selectedPrompt.value.id
  state.prompts = state.prompts.filter((prompt) => prompt.id !== id)
  state.selectedPromptId = state.prompts[0]?.id || ''
}

function resetSelectedPrompt() {
  if (!selectedPrompt.value) return
  const defaults = createDefaultPrompts()
  const source = defaults.find((prompt) => prompt.id === selectedPrompt.value?.id)
  if (!source) {
    selectedPrompt.value.content = ''
    selectedPrompt.value.version = 'v1'
    return
  }
  Object.assign(selectedPrompt.value, source)
}

async function resetWorkspaceState() {
  try {
    await ElMessageBox.confirm('重置会清空本页 localStorage 中的 EvoMind 案例，并恢复默认提示词。', '重置 EvoMind', {
      type: 'warning',
      confirmButtonText: '重置',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  Object.assign(state, createDefaultState())
  ElMessage.success('已重置')
}

function signalLabel(value: SignalType) {
  const labels: Record<SignalType, string> = {
    explicit_learning_marker: '显式标记',
    friction: '高摩擦',
    repeated_correction: '反复纠正',
    final_artifact_delta: '最终差异',
  }
  return labels[value] || value
}

function signalTagType(value: SignalType) {
  if (value === 'explicit_learning_marker') return 'danger'
  if (value === 'friction') return 'warning'
  if (value === 'repeated_correction') return 'success'
  return 'info'
}

function statusLabel(value: CaseStatus) {
  const labels: Record<CaseStatus, string> = {
    captured: '已捕捉',
    proposed: '已提案',
    verified: '已验证',
    active: '已生效',
    archived: '已归档',
  }
  return labels[value] || value
}

function statusTagType(value: CaseStatus) {
  if (value === 'active' || value === 'verified') return 'success'
  if (value === 'proposed') return 'warning'
  if (value === 'archived') return 'info'
  return ''
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
  return '操作失败'
}
</script>

<style scoped>
.evomind-page {
  padding: 20px;
  color: #1f2937;
}

.page-head,
.head-actions,
.pane-head,
.case-meta,
.pattern-row,
.check-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.page-head {
  justify-content: space-between;
  margin-bottom: 16px;
}

.scan-summary {
  margin: -6px 0 12px;
  color: #64748b;
  font-size: 13px;
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

h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0;
}

.stage-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 14px;
}

.stage-button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 38px;
  padding: 8px 10px;
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #fff;
  color: #475569;
  text-align: left;
  cursor: pointer;
}

.stage-button.is-active {
  border-color: #2f8f83;
  background: #f0faf8;
  color: #155e55;
}

.stage-button strong {
  font-size: 18px;
}

.workspace {
  display: grid;
  grid-template-columns: 310px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.case-pane,
.pane-section,
.prompt-list-pane {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.case-pane,
.prompt-list-pane {
  padding: 12px;
}

.pane-section {
  padding: 14px;
}

.pane-head {
  justify-content: space-between;
  margin-bottom: 12px;
}

.case-list,
.prompt-list,
.pattern-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.case-item,
.prompt-item {
  width: 100%;
  padding: 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  text-align: left;
  cursor: pointer;
}

.case-item.is-active,
.prompt-item.is-active {
  border-color: #2f8f83;
  background: #f8fffd;
}

.case-title {
  display: block;
  margin-bottom: 8px;
  font-weight: 650;
  overflow-wrap: anywhere;
}

.case-meta {
  flex-wrap: wrap;
  color: #64748b;
  font-size: 12px;
}

.source-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
  padding: 8px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  color: #64748b;
  font-size: 12px;
}

.source-line strong {
  color: #334155;
  overflow-wrap: anywhere;
}

.case-meta-grid {
  margin-bottom: 12px;
}

.material-browser {
  display: grid;
  grid-template-columns: minmax(180px, 240px) minmax(0, 1fr);
  gap: 12px;
  margin-top: 12px;
}

.material-index {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.material-item {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-height: 58px;
  padding: 8px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  text-align: left;
  cursor: pointer;
}

.material-item.is-active {
  border-color: #2f8f83;
  background: #f8fffd;
}

.material-order {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-size: 12px;
  font-weight: 650;
}

.material-item-main {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.material-item-main strong {
  font-size: 13px;
}

.material-item-main span {
  overflow: hidden;
  color: #64748b;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.material-detail {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 10px;
}

.material-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  min-height: 36px;
}

.material-detail-head span {
  display: block;
  margin-top: 4px;
  color: #64748b;
  font-size: 12px;
}

.evidence-group-detail,
.evidence-message-list {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 10px;
}

.evidence-process-details {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
}

.evidence-process-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  cursor: pointer;
  color: #334155;
  font-size: 13px;
  font-weight: 650;
}

.evidence-process-summary small {
  color: #64748b;
  font-size: 12px;
  font-weight: 400;
}

.evidence-process-details[open] .evidence-message-list {
  padding: 0 10px 10px;
}

.evidence-message-card {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.evidence-message-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid #eef2f7;
}

.evidence-message-head strong {
  color: #334155;
  font-size: 13px;
}

.evidence-message-text {
  max-height: 520px;
  margin: 0;
  overflow: auto;
  padding: 10px;
  color: #334155;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.two-column,
.proposal-grid,
.prompt-layout,
.replay-output {
  display: grid;
  gap: 14px;
}

.two-column,
.replay-output {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.case-stage {
  grid-template-columns: minmax(0, 1fr);
}

.proposal-grid {
  grid-template-columns: 280px minmax(0, 1fr);
}

.prompt-layout {
  grid-template-columns: 260px minmax(0, 1fr);
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

label,
.stack-field,
.proposal-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

label > span,
.stack-field > span,
.proposal-body > span,
.minor-title {
  color: #475569;
  font-size: 13px;
  font-weight: 650;
}

.stack-field {
  margin-top: 12px;
}

.pattern-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 14px;
}

.pattern-block {
  min-width: 0;
}

.pattern-row {
  align-items: flex-start;
  min-width: 0;
}

.pattern-row > span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-size: 12px;
}

.pattern-row :deep(.el-textarea) {
  flex: 1 1 auto;
  min-width: 0;
}

.pattern-row .el-button {
  flex: 0 0 auto;
}

.proposal-meta {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.target-select {
  width: 130px;
}

.executor-select {
  width: 150px;
}

.score-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.score-box {
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
}

.score-box span {
  display: block;
  color: #64748b;
  font-size: 12px;
}

.score-box strong {
  display: block;
  margin-top: 6px;
  color: #0f766e;
  font-size: 26px;
  line-height: 1;
}

.check-list {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.check-row {
  justify-content: space-between;
  min-height: 34px;
  padding: 8px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
}

.prompt-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.prompt-editor {
  min-width: 0;
}

.empty-main {
  min-height: 360px;
  padding: 48px 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.empty-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}

:deep(.el-textarea__inner),
:deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #d8dee8 inset;
}

@media (max-width: 1180px) {
  .workspace,
  .two-column,
  .case-stage,
  .proposal-grid,
  .material-browser,
  .prompt-layout,
  .replay-output,
  .check-list {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .stage-row,
  .field-grid,
  .score-row {
    grid-template-columns: 1fr;
  }
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  applyAiTaskReviewAction,
  actOnAiTaskPlannerSuggestion,
  fetchAiTaskAutomationHealth,
  fetchAiTaskExecutionPacket,
  fetchAiTaskSpaceAudit,
  fetchAiTaskSpace,
  runAiTaskPlannerCheck,
  saveAiTaskSpace,
  appendAiTaskExecutionRecord,
  type AiTaskAutomationHealth,
  type AiTaskReviewAction,
  type ExecutionPacket,
  type ExecutionRecord,
  type ExecutionRecordStatus,
  type AutomationDirectiveAction,
  type CaptureItem,
  type PlannerSuggestion,
  type TaskEvidenceAttachment,
  type TaskDocument,
  type TaskKind,
  type TaskNode,
  type TaskSpaceAudit,
  type TaskSpace,
  type TaskStatus,
} from '@/api/aiTaskSpace'
import {
  Finished,
  List,
  MagicStick,
  Refresh,
} from '@element-plus/icons-vue'

type TaskTreeNode = TaskNode & {
  children: TaskTreeNode[]
}

type TaskTreeContextMenu = {
  visible: boolean
  x: number
  y: number
  taskId: string
}

type ActionQueueItem =
  | {
    kind: 'suggestion'
    id: string
    label: string
    title: string
    meta: string
    suggestion: PlannerSuggestion
  }
  | {
    kind: 'archive'
    id: string
    label: string
    title: string
    meta: string
    taskId: string
  }

const STORAGE_KEY = 'codeyun.notes.taskSystem.v2'
const LEGACY_STORAGE_KEY = 'codeyun.notes.taskSystem.v1'

const emptyDocument = (): TaskDocument => ({
  goal: '',
  currentState: '',
  context: '',
  knownFacts: '',
  dependencies: '',
  nextStep: '',
  doneCriteria: '',
  resultSummary: '',
})

const decodeHtmlEntities = (value: string) => {
  if (typeof document === 'undefined') return value
  const textarea = document.createElement('textarea')
  textarea.innerHTML = value
  return textarea.value
}

const normalizeDocumentText = (value: unknown) => {
  const text = String(value ?? '')
  if (!text.trim()) return ''
  if (!/<[a-z][\s\S]*>/i.test(text)) return text

  const plain = decodeHtmlEntities(
    text.trim()
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/p>\s*<p[^>]*>/gi, '\n')
      .replace(/<\/?(p|div)[^>]*>/gi, '')
      .replace(/<[^>]+>/g, ''),
  )
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

  return plain
}

const normalizeDocument = (document: Partial<TaskDocument> | undefined): TaskDocument => {
  const merged = { ...emptyDocument(), ...(document ?? {}) }
  return {
    goal: normalizeDocumentText(merged.goal),
    currentState: normalizeDocumentText(merged.currentState),
    context: normalizeDocumentText(merged.context),
    knownFacts: normalizeDocumentText(merged.knownFacts),
    dependencies: normalizeDocumentText(merged.dependencies),
    nextStep: normalizeDocumentText(merged.nextStep),
    doneCriteria: normalizeDocumentText(merged.doneCriteria),
    resultSummary: normalizeDocumentText(merged.resultSummary),
  }
}

const nowIso = () => new Date().toISOString()
const newId = (prefix: string) => `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`

const taskSpace = ref<TaskSpace>({
  version: 2,
  captures: [],
  tasks: [],
  plannerLogs: [],
  plannerSuggestions: [],
})
const selectedTaskId = ref<string | null>(null)
const showArchivedNodes = ref(true)
const expandedIds = ref<Set<string>>(new Set())
const loadingTaskSpace = ref(false)
const savingTaskSpace = ref(false)
const plannerRunning = ref(false)
const executionRecordSubmitting = ref(false)
const executionPacketLoading = ref(false)
const executionPacket = ref<ExecutionPacket | null>(null)
const taskSpaceAudit = ref<TaskSpaceAudit | null>(null)
const taskSpaceAuditLoading = ref(false)
const automationHealth = ref<AiTaskAutomationHealth | null>(null)
const automationHealthLoading = ref(false)
const suggestionActionSubmitting = ref<string | null>(null)
const selectedSuggestionId = ref<string | null>(null)
const executionRecordDraft = ref({
  summary: '',
  verification: '',
  remainingRisk: '',
  nextStep: '',
  status: 'progress' as ExecutionRecordStatus,
  stepsDone: 0,
  commandsRun: 0,
  filesChanged: 0,
})
const taskTreeContextMenu = ref<TaskTreeContextMenu>({
  visible: false,
  x: 0,
  y: 0,
  taskId: '',
})
let saveTimer: number | null = null
let saveInFlight: Promise<boolean> | null = null
let suppressNextSave = false

const canUseLocalStorage = () =>
  typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'

const formatTime = (value: string) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  const hour = `${date.getHours()}`.padStart(2, '0')
  const minute = `${date.getMinutes()}`.padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:${minute}`
}

const stripMarkdownForSummary = (value: string) =>
  value
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/[*_`>]/g, '')
    .replace(/\[(.*?)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()

const markdownSection = (text: string, title: string) => {
  const pattern = new RegExp(`^##\\s+${title}\\s*$([\\s\\S]*?)(?=^##\\s+|\\s*$)`, 'm')
  return pattern.exec(text)?.[1]?.trim() ?? ''
}

const trimCaptureLeadIn = (value: string) =>
  value
    .replace(/^用户(?:指出|反馈|要求|认为|说|强调)[：:]\s*/, '')
    .replace(/^原始反馈[：:]\s*/, '')
    .replace(/^原始文本[：:]\s*/, '')
    .trim()

const captureTaskText = (capture: CaptureItem) => {
  const raw = capture.rawText.trim()
  const taskText = markdownSection(raw, '原始反馈')
    || markdownSection(raw, '原始文本')
    || markdownSection(raw, '用户反馈')
    || markdownSection(raw, '用户请求')
    || raw
  const summary = trimCaptureLeadIn(stripMarkdownForSummary(taskText))
  if (!summary) return '未填写任务内容'
  return summary.length > 180 ? `${summary.slice(0, 180)}...` : summary
}

const normalizeEvidenceAttachments = (value: unknown): TaskEvidenceAttachment[] => {
  if (!Array.isArray(value)) return []
  return value
    .filter((item): item is Partial<TaskEvidenceAttachment> => typeof item === 'object' && item != null)
    .map((item) => ({
      id: String(item.id || item.url || item.filename || ''),
      name: String(item.name || item.filename || '截图'),
      mimeType: String(item.mimeType || ''),
      filename: String(item.filename || ''),
      url: String(item.url || ''),
      size: Number(item.size || 0),
      sha256: String(item.sha256 || ''),
    }))
    .filter((item) => item.id && item.url)
    .slice(0, 12)
}

const isImageAttachment = (attachment: TaskEvidenceAttachment) =>
  attachment.mimeType.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp)$/i.test(attachment.filename || attachment.url)

const statusLabel: Record<TaskStatus, string> = {
  inbox: '待运行',
  planned: '待运行',
  ready: '待运行',
  running: '待运行',
  blocked: '待运行',
  done: '已完成',
  review_for_archive: '已完成',
  archived: '已归档',
}

const kindLabel: Record<TaskKind, string> = {
  project: '项目',
  task: '任务',
}

const captureKindLabel: Record<string, string> = {
  task: '任务',
  context: '上下文',
  constraint: '约束',
  preference: '偏好',
  knowledge: '知识',
}

const suggestionKindLabel: Record<PlannerSuggestion['kind'], string> = {
  split: '拆分',
  merge: '合并',
  dependency: '依赖',
  document: '文档',
  archive: '归档',
}

const executionRecordStatusLabel: Record<ExecutionRecordStatus, string> = {
  progress: '进展',
  done: '完成',
  blocked: '待运行',
}

const executionModeLabel: Record<ExecutionPacket['decision']['mode'], string> = {
  skip: '跳过',
  ask_user: '旧确认',
  report_only: '只报告',
  execute_safe: '直接运行',
}

const executionModeTagType: Record<ExecutionPacket['decision']['mode'], 'success' | 'warning' | 'info' | 'danger'> = {
  skip: 'info',
  ask_user: 'warning',
  report_only: 'info',
  execute_safe: 'success',
}

const directiveActionLabel: Record<AutomationDirectiveAction, string> = {
  stop_for_audit: '先审计',
  skip: '跳过',
  ask_user: '旧确认',
  report_only: '只报告',
  execute_safe: '直接运行',
}

const directiveActionTagType: Record<AutomationDirectiveAction, 'success' | 'warning' | 'info' | 'danger'> = {
  stop_for_audit: 'danger',
  skip: 'info',
  ask_user: 'warning',
  report_only: 'info',
  execute_safe: 'success',
}

const defaultExecutionBudget = (mode: ExecutionPacket['decision']['mode']) => ({
  maxSteps: mode === 'skip' ? 0 : 999,
  maxFilesChanged: mode === 'skip' ? 0 : 999,
  maxCommands: mode === 'skip' ? 1 : 999,
  mayModifyCode: mode !== 'skip',
  requiresVerification: mode !== 'skip',
  stopConditions: [
    '任务空间审计出现 error',
  ],
})

const executionBudget = computed(() =>
  executionPacket.value
    ? (executionPacket.value.budget ?? defaultExecutionBudget(executionPacket.value.decision.mode))
    : null,
)

const automationDirective = computed(() => executionPacket.value?.automationDirective ?? null)
const executionStatusLabel = computed(() => {
  if (automationDirective.value) return directiveActionLabel[automationDirective.value.action]
  return executionPacket.value ? executionModeLabel[executionPacket.value.decision.mode] : ''
})
const executionStatusTagType = computed<'success' | 'warning' | 'info' | 'danger'>(() => {
  if (automationDirective.value) return directiveActionTagType[automationDirective.value.action]
  return executionPacket.value ? executionModeTagType[executionPacket.value.decision.mode] : 'info'
})
const executionBoundarySummary = computed(() => {
  const packet = executionPacket.value
  if (!packet?.hasTask) return []
  const budget = packet.budget ?? defaultExecutionBudget(packet.decision.mode)
  const directive = packet.automationDirective
  return [
    {
      key: 'execute',
      label: directive?.shouldExecute ? '待运行' : '不执行',
      active: Boolean(directive?.shouldExecute),
    },
    {
      key: 'modify',
      label: budget.mayModifyCode ? '可改代码' : '不改代码',
      active: budget.mayModifyCode,
    },
    {
      key: 'writeback',
      label: directive?.shouldWriteBack ? '需回写' : '不回写',
      active: Boolean(directive?.shouldWriteBack),
    },
    {
      key: 'permission',
      label: budget.mayModifyCode ? '完整权限' : '无执行权限',
      active: budget.mayModifyCode,
    },
  ]
})

const auditTagType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
  if (!taskSpaceAudit.value) return 'info'
  if ((taskSpaceAudit.value.summary.errors ?? 0) > 0) return 'danger'
  if ((taskSpaceAudit.value.summary.warnings ?? 0) > 0) return 'warning'
  return 'success'
})

const auditStatusText = computed(() => {
  if (taskSpaceAuditLoading.value) return '审计中'
  if (!taskSpaceAudit.value) return '未审计'
  const { errors, warnings, runningTasks } = taskSpaceAudit.value.summary
  if (errors > 0) return `${errors} 个错误`
  if (warnings > 0) return `${warnings} 个提醒`
  return `${runningTasks} 个待运行`
})

const visibleAuditIssues = computed(() => taskSpaceAudit.value?.issues.slice(0, 3) ?? [])
const pageStaleFromHealth = computed(() => {
  const pageFingerprint = taskSpace.value._fingerprint
  const healthFingerprint = automationHealth.value?.currentFingerprint
  return Boolean(pageFingerprint && healthFingerprint && pageFingerprint !== healthFingerprint)
})
const automationHealthTagType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
  if (automationHealthLoading.value || !automationHealth.value) return 'info'
  if (pageStaleFromHealth.value) return 'warning'
  return automationHealth.value.ok ? 'success' : 'danger'
})
const automationHealthText = computed(() => {
  if (automationHealthLoading.value) return '检测中'
  if (!automationHealth.value) return '未检测'
  if (pageStaleFromHealth.value) return '页面旧版'
  if (!automationHealth.value.ok) return `${automationHealth.value.failures.length} 个异常`
  const blockerCount = automationHealth.value.contract.blockerCount ?? automationHealth.value.contract.blockers?.length ?? 0
  if (blockerCount > 0 && !automationHealth.value.contract.selectedTaskId) return `${blockerCount} 个待运行`
  const action = automationHealth.value.contract.action
  return action ? directiveActionLabel[action] : '契约正常'
})
const taskSpaceWriteBlocked = computed(() => pageStaleFromHealth.value)
const visibleAutomationFailures = computed(() => automationHealth.value?.failures.slice(0, 3) ?? [])
const healthIssues = computed(() => [
  ...visibleAuditIssues.value.map((issue) => ({
    key: `audit-${issue.code}-${issue.taskId ?? issue.message}`,
    text: issue.message,
    title: issue.message,
    taskId: issue.taskId,
  })),
  ...visibleAutomationFailures.value.map((failure) => ({
    key: `automation-${failure.code}`,
    text: failure.message,
    title: failure.message,
    taskId: null,
  })),
].slice(0, 4))
const latestPlannerOutcome = computed(() => {
  const health = automationHealth.value
  if (health?.contract.stopReason) return health.contract.stopReason
  const blockerCount = health?.contract.blockerCount ?? health?.contract.blockers?.length ?? 0
  if (blockerCount > 0 && !health?.contract.selectedTaskId) return `本轮无候选，${blockerCount} 个待运行`
  const decision = latestPlannerLog.value?.planningDecision
  if (!decision) return '尚未运行规划检查'
  if (decision.selectedTaskId) return decision.selectedReason
  return '本轮没有可执行候选'
})

const activeStatuses: TaskStatus[] = ['inbox', 'planned', 'ready', 'running', 'blocked', 'done', 'review_for_archive', 'archived']
const directPlannerTasks = computed(() =>
  taskSpace.value.tasks.filter((task) => activeStatuses.includes(task.status)),
)
const visibleTreeTasks = computed(() =>
  directPlannerTasks.value.filter((task) => showArchivedNodes.value || !isTaskArchived(task)),
)
const inboxCaptures = computed(() => taskSpace.value.captures.filter((item) => item.status === 'inbox'))
const visibleInboxCaptures = computed(() =>
  [...inboxCaptures.value].sort((a, b) => b.capturedAt.localeCompare(a.capturedAt)).slice(0, 5),
)
const recentLogs = computed(() => [...taskSpace.value.plannerLogs].sort((a, b) => b.ranAt.localeCompare(a.ranAt)).slice(0, 8))
const latestPlannerLog = computed(() => recentLogs.value[0] ?? null)
const olderPlannerLogs = computed(() => recentLogs.value.slice(1))
const allOpenPlannerSuggestions = computed(() =>
  (taskSpace.value.plannerSuggestions ?? []).filter((suggestion) => suggestion.status === 'open'),
)
const openPlannerSuggestions = computed(() => allOpenPlannerSuggestions.value.slice(0, 5))
const hiddenPlannerSuggestionCount = computed(() =>
  Math.max(0, allOpenPlannerSuggestions.value.length - openPlannerSuggestions.value.length),
)
const selectedPlannerSuggestion = computed(() =>
  selectedSuggestionId.value == null
    ? null
    : (allOpenPlannerSuggestions.value.find((suggestion) => suggestion.id === selectedSuggestionId.value) ?? null),
)

const taskById = computed(() => new Map(taskSpace.value.tasks.map((task) => [task.id, task])))
const selectedTask = computed(() =>
  selectedTaskId.value == null ? null : (taskById.value.get(selectedTaskId.value) ?? null),
)
const isTaskArchived = (task: TaskNode) => Boolean(task.archivedAt || task.status === 'archived')
const taskDisplayStatusLabel = (task: TaskNode) => isTaskArchived(task) ? '已归档' : statusLabel[task.status]
const trimTaskTitlePrefix = (value: string) =>
  value
    .replace(/^后续完善/, '')
    .replace(/^补齐[「"](.+?)[」"]完成标准$/, '$1')
    .replace(/^拆分[「"](.+?)[」"]$/, '$1')
    .replace(/\s+/g, ' ')
    .trim()
const firstPositiveIndex = (...indexes: number[]) => {
  const positives = indexes.filter((index) => index > 0)
  return positives.length ? Math.min(...positives) : -1
}
const taskListTitle = (task: TaskNode) => {
  const title = trimTaskTitlePrefix(stripMarkdownForSummary(task.title || task.document.goal || '未命名任务'))
  const colonIndex = firstPositiveIndex(title.indexOf('：'), title.indexOf(':'))
  const shortTitle = colonIndex > 0 ? title.slice(0, colonIndex) : title
  return shortTitle.length > 18 ? `${shortTitle.slice(0, 18)}...` : shortTitle
}
const actionQueueItems = computed<ActionQueueItem[]>(() => {
  const items: ActionQueueItem[] = []

  for (const suggestion of allOpenPlannerSuggestions.value.slice(0, 2)) {
    items.push({
      kind: 'suggestion',
      id: `suggestion:${suggestion.id}`,
      label: suggestionKindLabel[suggestion.kind],
      title: displaySuggestionTitle(suggestion),
      meta: suggestion.preview?.summary || suggestion.proposedAction,
      suggestion,
    })
  }

  return items.slice(0, 5)
})
const hiddenActionQueueCount = computed(() =>
  Math.max(
    0,
    allOpenPlannerSuggestions.value.length
    - actionQueueItems.value.length,
  ),
)
const canSubmitExecutionRecord = computed(() =>
  Boolean(!taskSpaceWriteBlocked.value && executionPacket.value?.hasTask && executionPacket.value.writeback && automationDirective.value?.shouldWriteBack),
)
const canMarkSelectedDone = computed(() => {
  const task = selectedTask.value
  return Boolean(!taskSpaceWriteBlocked.value && task && !isTaskArchived(task) && !['done', 'review_for_archive'].includes(task.status))
})

const taskStats = computed(() => {
  const runnableTasks = directPlannerTasks.value.filter((task) => !isTaskArchived(task))
  const done = runnableTasks.filter((task) => task.status === 'done' || task.status === 'review_for_archive').length
  const pending = runnableTasks.length - done
  return {
    inboxCaptures: inboxCaptures.value.length,
    pending,
    done,
  }
})

const orderedTasks = computed(() =>
  [...visibleTreeTasks.value].sort((left, right) => {
    if (left.parentId !== right.parentId) return (left.parentId ?? '').localeCompare(right.parentId ?? '')
    if (left.sortOrder !== right.sortOrder) return left.sortOrder - right.sortOrder
    return left.createdAt.localeCompare(right.createdAt)
  }),
)

const taskTree = computed<TaskTreeNode[]>(() => {
  const children = new Map<string | null, TaskNode[]>()
  for (const task of orderedTasks.value) {
    const key = task.parentId && visibleTreeTasks.value.some((item) => item.id === task.parentId) ? task.parentId : null
    const group = children.get(key) ?? []
    group.push(task)
    children.set(key, group)
  }
  const build = (parentId: string | null): TaskTreeNode[] =>
    (children.get(parentId) ?? []).map((task) => ({
      ...task,
      children: build(task.id),
    }))
  return build(null)
})

const expandedTaskKeys = computed(() => [...expandedIds.value])
const taskTreeRenderKey = computed(() =>
  [
    taskSpace.value.tasks
      .map((task) => `${task.id}:${task.status}:${task.updatedAt}`)
      .join('|'),
    expandedTaskKeys.value.join(','),
    showArchivedNodes.value ? 'archived:on' : 'archived:off',
  ].join('::'),
)
const taskTreeProps = {
  label: 'title',
  children: 'children',
}

const seedTaskSpace = (): TaskSpace => {
  const timestamp = nowIso()
  const rootId = newId('task')
  const plannerId = newId('task')
  return {
    version: 2,
    captures: [
      {
        id: newId('cap'),
        rawText: '建立任务采集缓存 skill：聊天中只记录任务和上下文，不立即执行。',
        source: '设计种子',
        capturedAt: timestamp,
        status: 'triaged',
        tags: ['skill'],
        contextKind: 'task',
        projectPath: '',
        linkedTaskId: rootId,
      },
      {
        id: newId('cap'),
        rawText: '规划检查需要每轮重新整理任务空间，分析依赖，优先推进前置任务，必要时拆分和合并任务。',
        source: '设计种子',
        capturedAt: timestamp,
        status: 'triaged',
        tags: ['planner'],
        contextKind: 'constraint',
        projectPath: '',
        linkedTaskId: plannerId,
      },
    ],
    tasks: [
      {
        id: rootId,
        title: '建立 AI 任务采集与执行体系',
        kind: 'project',
        status: 'ready',
        parentId: null,
        sortOrder: 0,
        executionPolicy: 'auto_safe',
        risk: 'low',
        dependsOn: [],
        relatedTaskIds: [plannerId],
        suggestedSkill: '任务采集缓存',
        document: {
          goal: '把聊天中产生的想法先进入任务空间，再由周期规划器选择待运行任务执行。',
          currentState: '已形成采集流、任务树、规划检查、执行回写的 v1 架构。',
          context: '用户明确要求采集和执行解耦；执行完成后必须重新读取整个任务空间，不能依赖聊天记忆。',
          knownFacts: '任务正文应是当前棋局式状态文档；原始对话和执行记录放在证据层。',
          dependencies: '需要先验证任务空间模型，再接入真实 Codex 自动化。',
          nextStep: '完善任务系统页面，让任务文档、执行包和规划检查同时可见。',
          doneCriteria: 'Codex 可写入采集，规划器能整理为任务，页面可维护状态并保留完成记录。',
          resultSummary: '',
        },
        attachments: [],
        evidenceLog: [`${formatTime(timestamp)} 从讨论中创建目标节点。`],
        createdAt: timestamp,
        updatedAt: timestamp,
      },
      {
        id: plannerId,
        title: '实现规划检查重规划器',
        kind: 'task',
        status: 'ready',
        parentId: rootId,
        sortOrder: 0,
        executionPolicy: 'auto_safe',
        risk: 'low',
        dependsOn: [],
        relatedTaskIds: [rootId],
        suggestedSkill: '任务采集缓存',
        document: {
          goal: '每次规划检查重新读取任务空间，整理 Inbox、依赖和下一步，而不是随便挑一个任务。',
          currentState: '页面内先提供手动触发的规划检查模拟器。',
          context: '真实自动化后续应复用同一套任务空间数据结构。',
          knownFacts: '采集是流式的；规划是周期性的；执行是事务性的；任务空间是事实源。',
          dependencies: '依赖任务节点状态字段和证据日志。',
          nextStep: '用启发式规则整理待采集输入，选择待运行任务直接推进。',
          doneCriteria: '每次规划检查能写入规划日志，并更新任务状态或创建新任务。',
          resultSummary: '',
        },
        attachments: [],
        evidenceLog: [`${formatTime(timestamp)} 从体系目标拆出前置任务。`],
        createdAt: timestamp,
        updatedAt: timestamp,
      },
    ],
    plannerLogs: [],
  }
}

const migrateLegacyTasks = () => {
  if (!canUseLocalStorage()) return null
  const raw = window.localStorage.getItem(LEGACY_STORAGE_KEY)
  if (!raw) return null
  try {
    const legacy = JSON.parse(raw) as Array<{
      id: number
      title: string
      content: string
      parentId: number | null
      sortOrder: number
      status: 'open' | 'done'
      createdAt: string
      updatedAt: string
    }>
    if (!Array.isArray(legacy) || legacy.length === 0) return null
    const idMap = new Map<number, string>()
    for (const item of legacy) idMap.set(item.id, `legacy_${item.id}`)
    const tasks: TaskNode[] = legacy.map((item) => ({
      id: idMap.get(item.id) ?? `legacy_${item.id}`,
      title: item.title || `旧任务 #${item.id}`,
      kind: 'task',
      status: item.status === 'done' ? 'done' : 'ready',
      parentId: item.parentId == null ? null : (idMap.get(item.parentId) ?? null),
      sortOrder: item.sortOrder ?? item.id,
      executionPolicy: 'auto_safe',
      risk: 'low',
      dependsOn: [],
      relatedTaskIds: [],
      suggestedSkill: '',
      document: {
        goal: item.title || `旧任务 #${item.id}`,
        currentState: item.status === 'done' ? '旧任务已标记完成。' : '从旧任务系统迁移，等待重新整理。',
        context: item.content || '',
        knownFacts: '',
        dependencies: '',
        nextStep: item.status === 'done' ? '' : '由规划检查重新整理任务文档和依赖关系。',
        doneCriteria: '',
        resultSummary: item.status === 'done' ? item.content : '',
      },
      evidenceLog: [`${formatTime(nowIso())} 从旧任务系统迁移。`],
      attachments: [],
      createdAt: item.createdAt || nowIso(),
      updatedAt: item.updatedAt || nowIso(),
      completedAt: item.status === 'done' ? item.updatedAt : undefined,
    }))
    return {
      version: 2,
      captures: [],
      tasks,
      plannerLogs: [],
      plannerSuggestions: [],
    } satisfies TaskSpace
  } catch {
    return null
  }
}

const normalizeClientTaskSpace = (space: TaskSpace): TaskSpace => ({
  version: 2,
  captures: Array.isArray(space.captures)
    ? space.captures.map((capture) => ({
        ...capture,
        tags: Array.isArray(capture.tags) ? capture.tags : [],
        contextKind: capture.contextKind || 'task',
        projectPath: capture.projectPath || '',
        attachments: normalizeEvidenceAttachments(capture.attachments),
      }))
    : [],
  tasks: Array.isArray(space.tasks)
    ? space.tasks.map((task) => ({
        ...task,
        document: normalizeDocument(task.document),
        attachments: normalizeEvidenceAttachments(task.attachments),
        evidenceLog: Array.isArray(task.evidenceLog) ? task.evidenceLog : [],
        executionRecords: Array.isArray(task.executionRecords) ? task.executionRecords : [],
      }))
    : [],
  plannerLogs: Array.isArray(space.plannerLogs) ? space.plannerLogs : [],
  plannerSuggestions: Array.isArray(space.plannerSuggestions) ? space.plannerSuggestions : [],
  _fingerprint: space._fingerprint,
})

const readLocalMigrationTaskSpace = (): TaskSpace | null => {
  if (!canUseLocalStorage()) return null
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as TaskSpace
      if (parsed.version === 2 && Array.isArray(parsed.tasks) && Array.isArray(parsed.captures)) {
        return normalizeClientTaskSpace(parsed)
      }
    } catch {
      return null
    }
  }
  return migrateLegacyTasks()
}

const replaceTaskSpace = (space: TaskSpace, options: { preferLatestSelected?: boolean } = {}) => {
  suppressNextSave = true
  taskSpace.value = normalizeClientTaskSpace(space)
  const latestSelectedId = taskSpace.value.plannerLogs[0]?.selectedTaskId ?? null
  if (options.preferLatestSelected && latestSelectedId && taskSpace.value.tasks.some((task) => task.id === latestSelectedId)) {
    selectedTaskId.value = latestSelectedId
    expandedIds.value = expandedWithAncestors(latestSelectedId)
    return
  }
  selectedTaskId.value = (
    selectedTaskId.value && taskSpace.value.tasks.some((task) => task.id === selectedTaskId.value)
      ? selectedTaskId.value
      : latestSelectedId && taskSpace.value.tasks.some((task) => task.id === latestSelectedId)
        ? latestSelectedId
      : directPlannerTasks.value[0]?.id ?? null
  )
  expandedIds.value = expandedWithAncestors(selectedTaskId.value)
}

const loadTaskSpace = async () => {
  loadingTaskSpace.value = true
  try {
    const remote = normalizeClientTaskSpace(await fetchAiTaskSpace())
    const migration = readLocalMigrationTaskSpace()
    if (migration && !window.localStorage.getItem(`${STORAGE_KEY}.migrated`) && remote.tasks.length === 0) {
      const saved = await saveAiTaskSpace(migration)
      replaceTaskSpace(saved, { preferLatestSelected: true })
      window.localStorage.setItem(`${STORAGE_KEY}.migrated`, '1')
      return
    }
    replaceTaskSpace(remote, { preferLatestSelected: true })
  } catch (error) {
    console.warn('Failed to load AI task space from backend, using local fallback:', error)
    replaceTaskSpace(readLocalMigrationTaskSpace() ?? seedTaskSpace())
    ElMessage.warning('任务空间后端暂不可用，已使用本地兜底数据')
  } finally {
    loadingTaskSpace.value = false
  }
}

const ensureWritableTaskSpaceSnapshot = (showMessage = true) => {
  if (!taskSpaceWriteBlocked.value) return true
  if (showMessage) {
    ElMessage.warning('页面已不是最新任务空间，先载入最新后再写入')
  }
  return false
}

const flushTaskSpaceSave = async (options: { showStaleWarning?: boolean } = {}): Promise<boolean> => {
  if (!ensureWritableTaskSpaceSnapshot(options.showStaleWarning ?? true)) return false
  if (saveInFlight) return saveInFlight
  savingTaskSpace.value = true
  saveInFlight = (async () => {
    try {
      const saved = await saveAiTaskSpace(taskSpace.value)
      replaceTaskSpace(saved)
      return true
    } catch (error) {
      console.warn('Failed to save AI task space:', error)
      if (typeof error === 'object' && error != null && 'response' in error && (error as { response?: { status?: number } }).response?.status === 409) {
        await loadTaskSpace()
        void loadTaskSpaceAudit()
        ElMessage.warning('任务空间已被后台更新，已重新加载最新状态')
      } else {
        ElMessage.error('任务空间保存失败')
      }
      return false
    } finally {
      savingTaskSpace.value = false
      saveInFlight = null
    }
  })()
  return saveInFlight
}

const flushPendingTaskSpaceSave = async (): Promise<boolean> => {
  if (!ensureWritableTaskSpaceSnapshot()) return false
  if (saveTimer != null) {
    window.clearTimeout(saveTimer)
    saveTimer = null
    return flushTaskSpaceSave()
  }
  if (saveInFlight) return saveInFlight
  return true
}

const scheduleTaskSpaceSave = () => {
  if (loadingTaskSpace.value) return
  if (suppressNextSave) {
    suppressNextSave = false
    return
  }
  if (saveTimer != null) {
    window.clearTimeout(saveTimer)
  }
  saveTimer = window.setTimeout(() => {
    saveTimer = null
    void flushTaskSpaceSave({ showStaleWarning: false })
  }, 450)
}

const updateTask = (taskId: string, patch: Partial<TaskNode>) => {
  const timestamp = nowIso()
  taskSpace.value = {
    ...taskSpace.value,
    tasks: taskSpace.value.tasks.map((task) =>
      task.id === taskId
        ? {
            ...task,
            ...patch,
            updatedAt: timestamp,
          }
        : task,
    ),
  }
}

const updateSelectedTask = (patch: Partial<TaskNode>) => {
  if (!selectedTask.value) return
  updateTask(selectedTask.value.id, patch)
}

const updateSelectedDocument = (key: keyof TaskDocument, value: string) => {
  const current = selectedTask.value
  if (!current) return
  updateTask(current.id, {
    document: {
      ...current.document,
      [key]: normalizeDocumentText(value),
    },
  })
}

const addEvidence = (taskId: string, line: string) => {
  const current = taskById.value.get(taskId)
  if (!current) return
  updateTask(taskId, {
    evidenceLog: [`${formatTime(nowIso())} ${line}`, ...current.evidenceLog].slice(0, 30),
  })
}

const selectTask = (id: string) => {
  selectedTaskId.value = id
  expandedIds.value = expandedWithAncestors(id)
}

const selectPlannerSuggestion = (suggestion: PlannerSuggestion) => {
  selectedSuggestionId.value = suggestion.id
  const targetId = suggestion.taskId ?? suggestion.relatedTaskIds[0]
  if (targetId) selectTask(targetId)
}

const selectActionQueueItem = (item: ActionQueueItem) => {
  if (item.kind === 'suggestion') {
    selectPlannerSuggestion(item.suggestion)
    return
  }
  if (item.taskId) selectTask(item.taskId)
}

const displaySuggestionTitle = (suggestion: PlannerSuggestion) => {
  const label = suggestionKindLabel[suggestion.kind]
  const title = suggestion.title.trim()
  if (title.startsWith(label)) {
    return title.slice(label.length).trimStart().replace(/^[:：-]\s*/, '') || title
  }
  return title
}

const displaySuggestionCreateTitle = (title: string, suggestion: PlannerSuggestion) => {
  const parentTitle = suggestion.taskId ? taskById.value.get(suggestion.taskId)?.title?.trim() : ''
  const value = title.trim()
  if (parentTitle && value.startsWith(`${parentTitle}：`)) return value.slice(parentTitle.length + 1)
  if (parentTitle && value.startsWith(`${parentTitle}:`)) return value.slice(parentTitle.length + 1).trimStart()
  return value
}

const displayTaskTitleById = (taskId: string | undefined) => {
  if (!taskId) return ''
  return taskById.value.get(taskId)?.title || taskId
}

const mergePreviewTargetTitle = (suggestion: PlannerSuggestion) =>
  displayTaskTitleById(suggestion.preview?.targetTaskId || suggestion.taskId)

const mergePreviewSourceTitles = (suggestion: PlannerSuggestion) =>
  (suggestion.preview?.sourceTaskIds ?? suggestion.relatedTaskIds ?? [])
    .map((taskId) => displayTaskTitleById(taskId))
    .filter(Boolean)

const canApplyPlannerSuggestion = (suggestion: PlannerSuggestion) =>
  suggestion.kind === 'document'
  || suggestion.kind === 'dependency'
  || suggestion.kind === 'archive'
  || ((suggestion.kind === 'split' || suggestion.kind === 'merge') && Boolean(suggestion.preview))

const actOnPlannerSuggestion = async (suggestion: PlannerSuggestion, action: 'apply' | 'dismiss') => {
  if (suggestionActionSubmitting.value) return
  suggestionActionSubmitting.value = suggestion.id
  try {
    if (!(await flushPendingTaskSpaceSave())) return
    const saved = await actOnAiTaskPlannerSuggestion(suggestion.id, action, taskSpace.value._fingerprint ?? '')
    replaceTaskSpace(saved)
    if (selectedSuggestionId.value === suggestion.id) selectedSuggestionId.value = null
    if (suggestion.taskId) selectedTaskId.value = suggestion.taskId
    void loadExecutionPacket()
    void loadTaskSpaceAudit()
    void loadAutomationHealth()
    ElMessage.success(action === 'apply' ? '已应用整理建议' : '已忽略整理建议')
  } catch (error) {
    console.warn('Failed to act on planner suggestion:', error)
    if (typeof error === 'object' && error != null && 'response' in error && (error as { response?: { status?: number } }).response?.status === 409) {
      await loadTaskSpace()
      ElMessage.warning('任务空间已变化，已重新加载')
    } else {
      ElMessage.error(action === 'apply' ? '应用建议失败' : '忽略建议失败')
    }
  } finally {
    suggestionActionSubmitting.value = null
  }
}

const setTaskExpanded = (id: string, expanded: boolean) => {
  const next = new Set(expandedIds.value)
  if (expanded) next.add(id)
  else next.delete(id)
  expandedIds.value = next
}

const expandedWithAncestors = (taskId: string | null) => {
  const next = new Set(expandedIds.value)
  for (const task of taskSpace.value.tasks) {
    if (task.parentId == null && (showArchivedNodes.value || !isTaskArchived(task))) next.add(task.id)
  }
  let cursor = taskId ? taskById.value.get(taskId) : null
  while (cursor?.parentId) {
    next.add(cursor.parentId)
    cursor = taskById.value.get(cursor.parentId) ?? null
  }
  return next
}

const createChildTask = (parentId: string | null = selectedTask.value?.id ?? null) => {
  if (!ensureWritableTaskSpaceSnapshot()) return
  const timestamp = nowIso()
  const taskId = newId('task')
  const siblings = taskSpace.value.tasks.filter((task) => task.parentId === parentId)
  const task: TaskNode = {
    id: taskId,
    title: parentId ? '新的子任务' : '新的任务',
    kind: 'task',
    status: 'ready',
    parentId,
    sortOrder: siblings.length,
    executionPolicy: 'auto_safe',
    risk: 'low',
    dependsOn: [],
    relatedTaskIds: [],
    suggestedSkill: '',
    attachments: [],
    document: {
      ...emptyDocument(),
      goal: parentId ? '补充这个子任务要达成的目标。' : '补充这个任务要达成的目标。',
      currentState: '手动创建，等待整理。',
      nextStep: '补全文档式状态，判断依赖和完成标准。',
    },
    evidenceLog: [`${formatTime(timestamp)} 手动创建任务。`],
    createdAt: timestamp,
    updatedAt: timestamp,
  }
  taskSpace.value = {
    ...taskSpace.value,
    tasks: [...taskSpace.value.tasks, task],
  }
  if (parentId) expandedIds.value = new Set(expandedIds.value).add(parentId)
  selectedTaskId.value = taskId
}

const contextMenuTask = computed(() =>
  taskTreeContextMenu.value.taskId ? (taskById.value.get(taskTreeContextMenu.value.taskId) ?? null) : null,
)

const closeTaskTreeContextMenu = () => {
  if (!taskTreeContextMenu.value.visible) return
  taskTreeContextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    taskId: '',
  }
}

const openTaskTreeContextMenu = (event: MouseEvent, task: TaskTreeNode) => {
  event.preventDefault()
  event.stopPropagation()
  selectedTaskId.value = task.id
  taskTreeContextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    taskId: task.id,
  }
}

const createContextChildTask = () => {
  const task = contextMenuTask.value
  if (!task) return
  closeTaskTreeContextMenu()
  createChildTask(task.id)
}

const createContextSiblingTask = () => {
  const task = contextMenuTask.value
  if (!task) return
  closeTaskTreeContextMenu()
  createChildTask(task.parentId ?? null)
}

const updateContextTaskKind = (kind: TaskKind) => {
  const task = contextMenuTask.value
  if (!task || task.kind === kind) {
    closeTaskTreeContextMenu()
    return
  }
  closeTaskTreeContextMenu()
  updateTask(task.id, { kind })
}

const archiveContextTask = async () => {
  const task = contextMenuTask.value
  if (!task) return
  closeTaskTreeContextMenu()
  await submitTaskReviewAction(task.id, 'archive', '已归档')
}

const unarchiveContextTask = async () => {
  const task = contextMenuTask.value
  if (!task) return
  closeTaskTreeContextMenu()
  await submitTaskReviewAction(task.id, 'keep_unarchived', '已取消归档')
}

const submitTaskReviewAction = async (
  taskId: string,
  action: AiTaskReviewAction,
  successMessage: string,
) => {
  if (!(await flushPendingTaskSpaceSave())) return
  try {
    const saved = await applyAiTaskReviewAction(taskId, action, taskSpace.value._fingerprint ?? '')
    replaceTaskSpace(saved)
    selectedTaskId.value = taskId
    void loadExecutionPacket()
    void loadTaskSpaceAudit()
    void loadAutomationHealth()
    ElMessage.success(successMessage)
  } catch (error) {
    console.warn('Failed to apply AI task review action:', error)
    if (typeof error === 'object' && error != null && 'response' in error && (error as { response?: { status?: number } }).response?.status === 409) {
      await loadTaskSpace()
      ElMessage.warning('任务空间已变化，已重新加载')
    } else {
      ElMessage.error('任务审核动作失败')
    }
  }
}

const markDone = async () => {
  const current = selectedTask.value
  if (!current) return
  await submitTaskReviewAction(current.id, 'mark_done', '已标记完成')
}


const resetExecutionRecordDraft = () => {
  executionRecordDraft.value = {
    summary: '',
    verification: '',
    remainingRisk: '',
    nextStep: '',
    status: 'progress',
    stepsDone: 0,
    commandsRun: 0,
    filesChanged: 0,
  }
}

const submitExecutionRecord = async () => {
  const current = selectedTask.value
  const summary = executionRecordDraft.value.summary.trim()
  if (!current || !summary || executionRecordSubmitting.value) return
  executionRecordSubmitting.value = true
  try {
    if (!(await flushPendingTaskSpaceSave())) return
    replaceTaskSpace(await appendAiTaskExecutionRecord(current.id, {
      summary,
      verification: executionRecordDraft.value.verification.trim(),
      remainingRisk: executionRecordDraft.value.remainingRisk.trim(),
      nextStep: executionRecordDraft.value.nextStep.trim(),
      status: executionRecordDraft.value.status,
      packetId: executionPacket.value?.snapshot?.packetId ?? '',
      expectedTaskUpdatedAt: executionPacket.value?.snapshot?.taskUpdatedAt ?? '',
      stepsDone: executionRecordDraft.value.stepsDone,
      commandsRun: executionRecordDraft.value.commandsRun,
      filesChanged: executionRecordDraft.value.filesChanged,
    }))
    selectedTaskId.value = current.id
    resetExecutionRecordDraft()
    void loadTaskSpaceAudit()
  } catch (error) {
    console.warn('Failed to append execution record:', error)
    if (typeof error === 'object' && error != null && 'response' in error && (error as { response?: { status?: number } }).response?.status === 409) {
      await loadTaskSpace()
      void loadExecutionPacket()
      void loadTaskSpaceAudit()
      ElMessage.warning('任务已被后台更新，已重新加载最新执行包')
    } else {
      ElMessage.error('执行回写失败')
    }
  } finally {
    executionRecordSubmitting.value = false
  }
}

const loadExecutionPacket = async () => {
  const taskId = selectedTaskId.value
  if (!taskId) {
    executionPacket.value = null
    return
  }
  executionPacketLoading.value = true
  try {
    executionPacket.value = await fetchAiTaskExecutionPacket(taskId)
  } catch (error) {
    console.warn('Failed to load execution packet:', error)
    executionPacket.value = null
  } finally {
    executionPacketLoading.value = false
  }
}

const loadTaskSpaceAudit = async () => {
  taskSpaceAuditLoading.value = true
  try {
    taskSpaceAudit.value = await fetchAiTaskSpaceAudit()
  } catch (error) {
    console.warn('Failed to load AI task space audit:', error)
    taskSpaceAudit.value = null
  } finally {
    taskSpaceAuditLoading.value = false
  }
}

const loadAutomationHealth = async () => {
  automationHealthLoading.value = true
  try {
    automationHealth.value = await fetchAiTaskAutomationHealth()
  } catch (error) {
    console.warn('Failed to load AI task automation health:', error)
    automationHealth.value = null
  } finally {
    automationHealthLoading.value = false
  }
}

const loadLatestTaskSpaceSnapshot = async () => {
  if (saveTimer != null) {
    window.clearTimeout(saveTimer)
    saveTimer = null
  }
  await loadTaskSpace()
  await loadExecutionPacket()
  void loadTaskSpaceAudit()
  void loadAutomationHealth()
}

const runPlannerCheck = async () => {
  if (plannerRunning.value) return
  plannerRunning.value = true
  try {
    if (!(await flushPendingTaskSpaceSave())) return
    const saved = await runAiTaskPlannerCheck()
    replaceTaskSpace(saved, { preferLatestSelected: true })
    const selectedId = saved.plannerLogs[0]?.selectedTaskId
    if (selectedId) {
      selectedTaskId.value = selectedId
    }
    void loadExecutionPacket()
    void loadTaskSpaceAudit()
    void loadAutomationHealth()
  } catch (error) {
    console.warn('Failed to run planner check:', error)
    ElMessage.error('规划检查运行失败')
  } finally {
    plannerRunning.value = false
  }
}

const closeTaskTreeContextMenuOnEscape = (event: KeyboardEvent) => {
  if (event.key === 'Escape') closeTaskTreeContextMenu()
}

onMounted(async () => {
  window.addEventListener('click', closeTaskTreeContextMenu)
  window.addEventListener('scroll', closeTaskTreeContextMenu, true)
  window.addEventListener('keydown', closeTaskTreeContextMenuOnEscape)
  await loadTaskSpace()
  void loadTaskSpaceAudit()
  void loadAutomationHealth()
  expandedIds.value = expandedWithAncestors(selectedTaskId.value)
})

onBeforeUnmount(() => {
  window.removeEventListener('click', closeTaskTreeContextMenu)
  window.removeEventListener('scroll', closeTaskTreeContextMenu, true)
  window.removeEventListener('keydown', closeTaskTreeContextMenuOnEscape)
})

watch(taskSpace, scheduleTaskSpaceSave, { deep: true })
watch(selectedPlannerSuggestion, (suggestion) => {
  if (!suggestion && selectedSuggestionId.value) selectedSuggestionId.value = null
})
watch(selectedTaskId, () => {
  void loadExecutionPacket()
})
</script>

<template>
  <main class="task-system-page" v-loading="loadingTaskSpace">
    <header class="task-header">
      <div>
        <h1>AI 任务空间</h1>
        <p>查看任务树和用户提问内容。</p>
      </div>
      <div class="header-actions">
        <span class="save-state">{{ savingTaskSpace ? '保存中' : '后端任务空间' }}</span>
        <el-button :icon="Refresh" :loading="plannerRunning" :disabled="taskSpaceWriteBlocked" @click="runPlannerCheck">
          整理任务
        </el-button>
      </div>
    </header>

    <section class="stats-strip" aria-label="任务状态">
      <div class="stat-item">
        <span class="stat-value">{{ taskStats.inboxCaptures }}</span>
        <span class="stat-label">采集待整理</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ taskStats.pending }}</span>
        <span class="stat-label">待运行</span>
      </div>
      <div class="stat-item">
        <span class="stat-value">{{ taskStats.done }}</span>
        <span class="stat-label">已完成</span>
      </div>
    </section>

    <details v-if="visibleInboxCaptures.length" class="capture-inbox compact-details" open>
      <summary>采集待整理 {{ visibleInboxCaptures.length }}</summary>
      <div class="capture-inbox-list">
        <article v-for="capture in visibleInboxCaptures" :key="capture.id" class="capture-inbox-item">
          <div class="capture-inbox-meta">
            <span>{{ captureKindLabel[capture.contextKind] || capture.contextKind || '任务' }}</span>
            <span>{{ capture.source }}</span>
            <time>{{ formatTime(capture.capturedAt) }}</time>
          </div>
          <p>{{ captureTaskText(capture) }}</p>
          <div v-if="capture.attachments.length" class="evidence-attachments compact">
            <a
              v-for="attachment in capture.attachments"
              :key="attachment.id"
              class="evidence-attachment"
              :href="attachment.url"
              target="_blank"
              rel="noreferrer"
              :title="attachment.name"
            >
              <img v-if="isImageAttachment(attachment)" :src="attachment.url" :alt="attachment.name" />
              <span v-else>{{ attachment.name }}</span>
            </a>
          </div>
          <div v-if="capture.tags.length" class="capture-inbox-tags">
            <span v-for="tag in capture.tags" :key="tag">{{ tag }}</span>
          </div>
        </article>
      </div>
    </details>

    <section v-if="pageStaleFromHealth || healthIssues.length" class="health-strip" aria-label="任务空间健康">
      <button
        v-if="pageStaleFromHealth"
        class="stale-reload"
        type="button"
        :disabled="loadingTaskSpace"
        @click="loadLatestTaskSpaceSnapshot"
      >
        载入最新
      </button>
      <div v-if="healthIssues.length" class="audit-issues">
        <button
          v-for="issue in healthIssues"
          :key="issue.key"
          class="audit-issue"
          type="button"
          :title="issue.title"
          :disabled="!issue.taskId"
          @click="issue.taskId && selectTask(issue.taskId)"
        >
          {{ issue.text }}
        </button>
      </div>
      <span v-else class="audit-ok">
        {{ latestPlannerOutcome }}
      </span>
    </section>

    <section class="ai-task-workspace">
      <aside class="task-tree-panel">
        <div class="panel-title">
          <span><el-icon><List /></el-icon>任务树</span>
          <label class="tree-visibility-toggle" title="显示或隐藏已归档节点">
            <input v-model="showArchivedNodes" type="checkbox">
            归档
          </label>
          <button
            class="icon-plus"
            type="button"
            title="新增一级任务"
            aria-label="新增一级任务"
            :disabled="taskSpaceWriteBlocked"
            @click="createChildTask(null)"
          >
            +
          </button>
        </div>
        <el-tree
          v-if="taskTree.length"
          :key="taskTreeRenderKey"
          class="task-tree"
          :data="taskTree"
          :props="taskTreeProps"
          node-key="id"
          :default-expanded-keys="expandedTaskKeys"
          :auto-expand-parent="false"
          highlight-current
          :expand-on-click-node="false"
          :current-node-key="selectedTaskId"
          @node-click="(node: TaskTreeNode) => selectTask(node.id)"
          @node-expand="(node: TaskTreeNode) => setTaskExpanded(node.id, true)"
          @node-collapse="(node: TaskTreeNode) => setTaskExpanded(node.id, false)"
        >
          <template #default="{ data }">
            <span
              class="tree-node"
              :class="[data.status, { archived: isTaskArchived(data) }]"
              @contextmenu="openTaskTreeContextMenu($event, data)"
            >
              <span class="node-main">
                <span class="node-title" :title="data.title">{{ taskListTitle(data) }}</span>
                <span class="node-meta">{{ kindLabel[data.kind] }} · {{ taskDisplayStatusLabel(data) }}</span>
              </span>
            </span>
          </template>
        </el-tree>
        <div
          v-if="taskTreeContextMenu.visible && contextMenuTask"
          class="task-tree-context-menu"
          :style="{ left: `${taskTreeContextMenu.x}px`, top: `${taskTreeContextMenu.y}px` }"
          @click.stop
          @contextmenu.prevent
        >
          <button type="button" :disabled="taskSpaceWriteBlocked" @click="createContextChildTask">新增子任务</button>
          <button type="button" :disabled="taskSpaceWriteBlocked" @click="createContextSiblingTask">新增同级任务</button>
          <hr>
          <button type="button" :disabled="taskSpaceWriteBlocked || contextMenuTask.kind === 'project'" @click="updateContextTaskKind('project')">改为项目</button>
          <button type="button" :disabled="taskSpaceWriteBlocked || contextMenuTask.kind === 'task'" @click="updateContextTaskKind('task')">改为任务</button>
          <hr>
          <button v-if="!isTaskArchived(contextMenuTask)" type="button" :disabled="taskSpaceWriteBlocked" @click="archiveContextTask">归档节点</button>
          <button v-else type="button" :disabled="taskSpaceWriteBlocked" @click="unarchiveContextTask">取消归档</button>
        </div>
        <el-empty v-if="!taskTree.length" description="任务树为空" />

      </aside>

      <section class="task-detail-panel">
        <template v-if="selectedTask">
          <div class="detail-toolbar">
            <div class="title-line">
              <el-input
                :model-value="selectedTask.title"
                class="task-title-input"
                :disabled="taskSpaceWriteBlocked"
                @update:model-value="updateSelectedTask({ title: $event })"
              />
              <el-tag :type="selectedTask.status === 'done' ? 'success' : 'info'">
                {{ taskDisplayStatusLabel(selectedTask) }}
              </el-tag>
            </div>
            <div class="detail-actions">
              <el-button v-if="canMarkSelectedDone" size="small" :icon="Finished" @click="markDone">完成</el-button>
            </div>
          </div>

          <section class="task-question-panel">
            <div class="task-meta-strip">
              <span>{{ kindLabel[selectedTask.kind] }}</span>
              <span v-if="selectedTask.attachments.length">附件 {{ selectedTask.attachments.length }}</span>
            </div>
            <label>
              <span>{{ selectedTask.kind === 'project' ? '项目说明' : '用户提问' }}</span>
              <el-input :model-value="selectedTask.document.context" type="textarea" :autosize="{ minRows: 8, maxRows: 18 }" resize="none" :disabled="taskSpaceWriteBlocked" @update:model-value="updateSelectedDocument('context', $event)" />
            </label>
            <div v-if="selectedTask.attachments.length" class="evidence-attachments">
              <a
                v-for="attachment in selectedTask.attachments"
                :key="attachment.id"
                class="evidence-attachment"
                :href="attachment.url"
                target="_blank"
                rel="noreferrer"
                :title="attachment.name"
              >
                <img v-if="isImageAttachment(attachment)" :src="attachment.url" :alt="attachment.name" />
                <span v-else>{{ attachment.name }}</span>
              </a>
            </div>
            <label>
              <span>处理结果</span>
              <el-input :model-value="selectedTask.document.resultSummary" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" resize="none" :disabled="taskSpaceWriteBlocked" @update:model-value="updateSelectedDocument('resultSummary', $event)" />
            </label>
          </section>
        </template>
        <el-empty v-else description="选择一个任务查看状态文档" />
      </section>
    </section>
  </main>
</template>

<style scoped>
.task-system-page {
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  padding: 16px 18px;
  color: #1f2937;
  background: #f5f7fa;
}

.task-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.task-header h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
}

.task-header p {
  margin: 5px 0 0;
  color: #64748b;
  font-size: 13px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.save-state {
  color: #64748b;
  font-size: 12px;
}

.stats-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 1px;
  width: max-content;
  max-width: 100%;
  overflow: hidden;
  margin-bottom: 12px;
  border: 1px solid #e5e7eb;
  background: #e5e7eb;
}

.stat-item {
  display: grid;
  grid-template-columns: auto auto;
  align-items: baseline;
  gap: 7px;
  min-height: 38px;
  padding: 0 12px;
  background: #fff;
}

.stat-value {
  font-size: 18px;
  font-weight: 700;
}

.stat-label {
  color: #64748b;
  font-size: 12px;
}

.action-queue {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: -2px 0 12px;
  padding: 8px 10px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
}

.action-queue-title {
  flex: 0 0 auto;
  padding-top: 2px;
  color: #1e40af;
  font-size: 12px;
  font-weight: 700;
}

.action-queue-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.action-queue-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 7px;
  max-width: 360px;
  min-width: 180px;
  padding: 5px 8px;
  color: inherit;
  text-align: left;
  background: #fff;
  border: 1px solid #dbeafe;
  cursor: pointer;
}

.action-queue-item:hover:not(:disabled) {
  border-color: #93c5fd;
}

.action-queue-item:disabled {
  cursor: default;
  opacity: 0.75;
}

.action-queue-kind {
  padding: 1px 5px;
  color: #1d4ed8;
  font-size: 11px;
  line-height: 1.4;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
}

.action-queue-item.suggestion .action-queue-kind {
  color: #9a3412;
  background: #fff7ed;
  border-color: #fed7aa;
}

.action-queue-main {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.action-queue-main strong,
.action-queue-main span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.action-queue-main strong {
  color: #1f2937;
  font-size: 12px;
}

.action-queue-main span {
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}

.action-queue-more {
  align-self: stretch;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  color: #64748b;
  font-size: 12px;
  background: #fff;
  border: 1px solid #e2e8f0;
}

.capture-inbox {
  margin: -4px 0 12px;
  padding: 8px 10px;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.capture-inbox[open] summary {
  margin-bottom: 8px;
}

.capture-inbox-list {
  display: grid;
  gap: 6px;
}

.capture-inbox-item {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 7px 0;
  border-top: 1px solid #f1f5f9;
}

.capture-inbox-item:first-child {
  border-top: 0;
}

.capture-inbox-meta,
.capture-inbox-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  color: #64748b;
  font-size: 12px;
}

.capture-inbox-meta span,
.capture-inbox-tags span {
  max-width: 100%;
  overflow: hidden;
  padding: 1px 6px;
  background: #f8fafc;
  border: 1px solid #e5edf6;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.capture-inbox-meta time {
  color: #94a3b8;
}

.capture-inbox-item p {
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  color: #334155;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.evidence-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  margin: 8px 0 10px;
}

.evidence-attachments.compact {
  margin: 2px 0;
}

.evidence-attachment {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  width: 132px;
  height: 82px;
  color: #475569;
  font-size: 12px;
  line-height: 1.35;
  text-align: center;
  text-decoration: none;
  background: #f8fafc;
  border: 1px solid #dbe3ef;
}

.evidence-attachments.compact .evidence-attachment {
  width: 86px;
  height: 54px;
}

.evidence-attachment img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.evidence-attachment span {
  display: -webkit-box;
  overflow: hidden;
  padding: 6px;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.health-strip {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 34px;
  margin: -2px 0 12px;
  padding: 6px 10px;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.audit-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 600;
}

.audit-issues {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
}

.stale-reload {
  flex: 0 0 auto;
  padding: 3px 8px;
  color: #9a3412;
  font-size: 12px;
  line-height: 1.4;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  cursor: pointer;
}

.stale-reload:hover:not(:disabled) {
  color: #c2410c;
  border-color: #fdba74;
}

.stale-reload:disabled {
  cursor: wait;
  opacity: 0.65;
}

.audit-issue {
  max-width: 360px;
  overflow: hidden;
  padding: 3px 8px;
  border: 1px solid #fed7aa;
  color: #9a3412;
  background: #fff7ed;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.audit-issue:disabled {
  cursor: default;
}

.audit-ok {
  overflow: hidden;
  color: #64748b;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.automation-health-details {
  margin-left: auto;
  min-width: 0;
  max-width: 100%;
  flex: 0 1 auto;
}

.automation-health-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-width: 520px;
}

.automation-health-grid span {
  padding: 2px 7px;
  color: #64748b;
  font-size: 12px;
  background: #f8fafc;
  border: 1px solid #e5edf6;
}

.automation-run-summary {
  display: grid;
  gap: 3px;
  max-width: 760px;
  margin-top: 7px;
  min-width: 0;
}

.automation-run-summary p {
  overflow: hidden;
  margin: 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.automation-blockers {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.automation-blockers span {
  color: #64748b;
  font-size: 12px;
  font-weight: 650;
}

.automation-blockers button {
  overflow: hidden;
  min-width: 0;
  padding: 0;
  color: #9a3412;
  font-size: 12px;
  line-height: 1.45;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.automation-blockers button:hover {
  color: #c2410c;
  text-decoration: underline;
}

.automation-run-summary code {
  display: block;
  overflow-wrap: anywhere;
  padding: 6px 8px;
  color: #334155;
  font-size: 12px;
  line-height: 1.45;
  background: #f8fafc;
  border-left: 3px solid #cbd5e1;
}

.ai-task-workspace {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  gap: 12px;
  min-height: 0;
  flex: 1;
}

.task-tree-panel,
.task-detail-panel {
  box-sizing: border-box;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  background: #fff;
}

.task-tree-panel {
  display: flex;
  flex-direction: column;
}

.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 42px;
  padding: 0 12px;
  border-bottom: 1px solid #edf2f7;
  font-weight: 650;
}

.panel-title span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.subhead {
  padding: 10px 12px 6px;
  color: #475569;
  font-size: 12px;
  font-weight: 650;
}

.planner-log {
  min-height: 0;
  overflow: auto;
}

.planner-log-history {
  padding: 8px 12px 10px;
  border-top: 1px solid #f1f5f9;
}










.planner-suggestions {
  padding: 8px 12px 10px;
  border-top: 1px solid #f1f5f9;
}

.planner-suggestion {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  width: 100%;
  padding: 7px 0;
  color: inherit;
  background: transparent;
  border-top: 1px solid #f8fafc;
}

.planner-suggestion:first-of-type {
  border-top: 0;
}

.planner-suggestion:hover {
  background: #f8fafc;
}

.planner-suggestion-more {
  margin: 4px 0 0;
  padding-top: 7px;
  border-top: 1px solid #f1f5f9;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.45;
}

.suggestion-body {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.suggestion-select {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  min-width: 0;
  padding: 0;
  color: inherit;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.suggestion-kind {
  align-self: start;
  padding: 1px 5px;
  color: #64748b;
  font-size: 11px;
  line-height: 1.4;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
}

.planner-suggestion.warning .suggestion-kind {
  color: #9a3412;
  background: #fff7ed;
  border-color: #fed7aa;
}

.suggestion-main {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.suggestion-main strong {
  overflow: hidden;
  color: #334155;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.suggestion-main span {
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}

.suggestion-preview {
  min-width: 0;
  padding-left: 36px;
}

.suggestion-preview p,
.suggestion-preview ul {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}

.suggestion-preview ul {
  padding-left: 16px;
}

.suggestion-preview li {
  overflow-wrap: anywhere;
}

.suggestion-merge-preview {
  display: grid;
  gap: 3px;
  margin-top: 5px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}

.suggestion-merge-preview span {
  overflow-wrap: anywhere;
}

.suggestion-actions {
  display: inline-flex;
  align-items: start;
  gap: 5px;
}

.suggestion-actions button {
  padding: 1px 5px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.4;
  background: #fff;
  border: 1px solid #e2e8f0;
  cursor: pointer;
}

.suggestion-actions button:hover:not(:disabled) {
  color: #1d4ed8;
  border-color: #bfdbfe;
}

.suggestion-actions button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.planner-log-history .log-item {
  margin: 8px -12px 0;
}

.log-item p {
  margin: 6px 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.55;
}

.tree-visibility-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
}

.tree-visibility-toggle input {
  width: 13px;
  height: 13px;
  margin: 0;
}

.icon-plus {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  color: #fff;
  background: #2563eb;
  border: 0;
  cursor: pointer;
}

.task-tree {
  flex: 1;
  min-height: 220px;
  overflow: auto;
  padding: 6px 0;
  background: transparent;
}

.task-tree :deep(.el-tree-node__content) {
  min-width: 0;
  height: 42px;
}

.task-tree-context-menu {
  position: fixed;
  z-index: 30;
  display: grid;
  min-width: 132px;
  padding: 5px;
  background: #fff;
  border: 1px solid #dbe3ef;
  box-shadow: 0 8px 24px rgb(15 23 42 / 14%);
}

.task-tree-context-menu button {
  min-width: 0;
  padding: 6px 8px;
  color: #334155;
  font-size: 13px;
  line-height: 1.35;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.task-tree-context-menu button:hover:not(:disabled) {
  background: #eef4ff;
  color: #1d4ed8;
}

.task-tree-context-menu button:disabled {
  color: #cbd5e1;
  cursor: default;
}

.task-tree-context-menu hr {
  width: 100%;
  margin: 4px 0;
  border: 0;
  border-top: 1px solid #edf2f7;
}







.tree-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1;
  min-width: 0;
  gap: 8px;
}

.tree-node.running .node-title {
  color: #1d4ed8;
}

.tree-node.blocked .node-title {
  color: #b91c1c;
}

.tree-node.waiting .node-title {
  color: #9a3412;
}

.tree-node.waiting .node-meta {
  color: #c2410c;
}

.tree-node.done .node-title {
  color: #64748b;
  text-decoration: line-through;
}

.tree-node.archived .node-title {
  color: #94a3b8;
  text-decoration: none;
}

.tree-node.archived .node-meta {
  color: #cbd5e1;
}

.node-main {
  display: grid;
  min-width: 0;
}

.node-title {
  overflow: hidden;
  color: #1f2937;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-meta {
  overflow: hidden;
  color: #94a3b8;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}


.log-item {
  padding: 9px 12px;
  border-top: 1px solid #f1f5f9;
}

.log-time {
  color: #94a3b8;
  font-size: 12px;
}

.log-item ul {
  margin: 5px 0 0;
  padding-left: 18px;
  color: #475569;
  font-size: 12px;
  line-height: 1.5;
}

.planner-decision {
  margin-top: 8px;
  padding-top: 7px;
  border-top: 1px solid #f8fafc;
}

.planner-decision p {
  margin: 5px 0;
  color: #334155;
  font-size: 12px;
  line-height: 1.45;
}

.planner-decision-skipped {
  color: #94a3b8;
}

.task-detail-panel {
  overflow: auto;
  padding: 12px;
}

.detail-toolbar {
  display: grid;
  gap: 10px;
  margin-bottom: 12px;
}

.title-line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.task-title-input :deep(.el-input__wrapper) {
  padding-left: 0;
  box-shadow: none;
}

.task-title-input :deep(.el-input__inner) {
  color: #111827;
  font-size: 20px;
  font-weight: 650;
  text-overflow: ellipsis;
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.selected-suggestion-panel {
  display: grid;
  gap: 6px;
  margin: 0 0 12px;
  padding: 8px 10px;
  border-left: 3px solid #93c5fd;
  background: #f8fafc;
}

.selected-suggestion-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.selected-suggestion-head strong {
  min-width: 0;
  color: #1f2937;
  font-size: 13px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.selected-suggestion-panel p {
  margin: 0;
  color: #475569;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.selected-suggestion-summary {
  color: #64748b;
}

.selected-suggestion-list {
  margin: 0;
  padding-left: 18px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.selected-suggestion-list li {
  overflow-wrap: anywhere;
}

.selected-suggestion-list.ordered li {
  margin-bottom: 3px;
}

.selected-suggestion-list.ordered li span,
.selected-suggestion-list.ordered li small {
  display: block;
}

.selected-suggestion-list.ordered li small {
  color: #94a3b8;
  font-size: 12px;
}

.selected-merge-preview {
  display: grid;
  gap: 6px;
  padding-left: 2px;
  color: #64748b;
  font-size: 12px;
}

.selected-merge-preview div {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.selected-merge-preview span {
  color: #94a3b8;
  font-weight: 600;
}

.selected-merge-preview strong {
  min-width: 0;
  color: #334155;
  overflow-wrap: anywhere;
}

.selected-merge-preview ol {
  margin: 0;
  padding-left: 18px;
  line-height: 1.5;
}

.selected-merge-preview li {
  overflow-wrap: anywhere;
}

.selected-suggestion-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

.task-meta-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  color: #64748b;
  font-size: 12px;
}

.task-meta-line span {
  padding: 2px 7px;
  background: #f8fafc;
  border: 1px solid #e5edf6;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: 10px;
  margin-top: 10px;
}

.field-grid label,
.doc-grid label,
.task-board label,
.execution-extra-grid label {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.field-grid span,
.doc-grid span,
.task-board span,
.execution-extra-grid span {
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.document-section,
.execution-section,
.inspector-details {
  border-top: 1px solid #edf2f7;
  padding-top: 12px;
  margin-top: 12px;
}

.section-title {
  margin: 0 0 9px;
  color: #111827;
  font-size: 14px;
  font-weight: 650;
}

.doc-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 9px;
}

.task-board,
.task-question-panel {
  display: grid;
  gap: 10px;
  margin-top: 8px;
}

.task-question-panel label {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.task-question-panel label > span {
  color: #475569;
  font-size: 12px;
  font-weight: 650;
}

.doc-wide {
  grid-column: 1 / -1;
}


.task-meta-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  margin-bottom: 10px;
}

.task-meta-strip span {
  padding: 2px 7px;
  color: #64748b;
  background: #f8fafc;
  border: 1px solid #e5edf6;
  font-size: 12px;
  line-height: 1.45;
}

.decision-strip {
  margin-top: 12px;
}

.execution-brief {
  display: grid;
  gap: 8px;
  min-height: 34px;
  margin: 0;
  align-content: center;
}

.execution-brief-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.execution-brief-main span {
  overflow: hidden;
  min-width: 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.packet-planning-reason {
  margin: -3px 0 1px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}

.execution-boundary-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.execution-boundary-summary span {
  padding: 2px 7px;
  border: 1px solid #e2e8f0;
  color: #64748b;
  background: #f8fafc;
  font-size: 12px;
  line-height: 1.45;
}

.execution-boundary-summary span.active {
  border-color: #bfdbfe;
  color: #1d4ed8;
  background: #eff6ff;
}

.execution-empty {
  color: #94a3b8;
  font-size: 13px;
}

.execution-boundary[open] {
  margin-top: 4px;
}

.execution-boundary p {
  margin: 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.5;
}

.compact-details {
  min-width: 0;
}

.compact-details summary {
  width: fit-content;
  cursor: pointer;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
  list-style-position: inside;
}

.compact-details summary:hover {
  color: #2563eb;
}

.compact-details[open] summary {
  margin-bottom: 8px;
}

.detail-subsection {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #f1f5f9;
}

.packet-lists {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
}

.packet-lists div {
  min-width: 0;
  padding: 8px 10px;
  background: #f8fafc;
}

.packet-lists span {
  color: #475569;
  font-size: 12px;
  font-weight: 650;
}

.packet-lists ul {
  margin: 5px 0 0;
  padding-left: 16px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}

.packet-stops {
  padding: 8px 10px;
  background: #fff7ed;
}

.packet-stops span {
  color: #9a3412;
  font-size: 12px;
  font-weight: 650;
}

.packet-stops ul {
  margin: 5px 0 0;
  padding-left: 16px;
  color: #9a3412;
  font-size: 12px;
  line-height: 1.45;
}

.packet-stops p {
  margin: 5px 0 0;
  color: #9a3412;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.packet-suggestions {
  padding: 8px 10px;
  background: #f8fafc;
}

.packet-suggestions span {
  color: #475569;
  font-size: 12px;
  font-weight: 650;
}

.packet-suggestions ul {
  margin: 5px 0 0;
  padding-left: 16px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}

.packet-suggestions small {
  color: #94a3b8;
  font-size: 12px;
}

.writeback-command {
  padding: 7px 10px;
  background: #f8fafc;
}

.writeback-command summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.writeback-command summary small {
  color: #94a3b8;
  font-size: 12px;
  font-weight: 500;
}

.execution-brief code {
  display: block;
  overflow-wrap: anywhere;
  padding: 7px 9px;
  color: #334155;
  font-size: 12px;
  line-height: 1.45;
  background: #f8fafc;
  border-left: 3px solid #cbd5e1;
}

.execution-form {
  display: grid;
  grid-template-columns: auto minmax(260px, 1fr) auto;
  align-items: end;
  gap: 10px;
}

.execution-quick-writeback {
  display: grid;
  grid-template-columns: auto minmax(260px, 1fr) auto;
  align-items: end;
  gap: 10px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #edf2f7;
}

.execution-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.execution-section-head .section-title {
  margin-bottom: 0;
}

.execution-stop-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0;
  padding: 8px 10px;
  border-left: 3px solid #f59e0b;
  background: #fff7ed;
}

.execution-stop-banner div {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.execution-stop-banner strong {
  color: #9a3412;
  font-size: 13px;
  font-weight: 650;
}

.execution-stop-banner span {
  overflow: hidden;
  min-width: 0;
  color: #9a3412;
  font-size: 12px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.execution-form label,
.execution-quick-writeback label {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.execution-form span,
.execution-quick-writeback span {
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.execution-status {
  align-self: end;
}

.execution-status :deep(.el-segmented) {
  justify-self: start;
}

.execution-actions {
  display: flex;
  align-items: flex-end;
  justify-content: flex-end;
}

.execution-more {
  grid-column: 1 / -1;
}

.execution-form-note {
  margin: 0;
  color: #94a3b8;
  font-size: 12px;
}


.execution-extra-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}

.execution-budget-inputs {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
}

.execution-budget-inputs label {
  display: inline-grid;
  grid-template-columns: auto 132px;
  align-items: center;
  gap: 6px;
}

.execution-budget-inputs span {
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.execution-history {
  margin-top: 10px;
  overflow-x: hidden;
}

.execution-record-list {
  display: grid;
  gap: 8px;
  min-width: 0;
  margin-top: 10px;
  overflow-x: hidden;
}

.execution-record {
  display: grid;
  box-sizing: border-box;
  gap: 5px;
  max-width: 100%;
  min-width: 0;
  padding: 8px 9px;
  background: #f8fafc;
  border-left: 3px solid #94a3b8;
}

.execution-record p {
  min-width: 0;
  margin: 0;
  color: #475569;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.execution-record-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  max-width: 100%;
  min-width: 0;
  color: #334155;
  font-size: 12px;
  font-weight: 650;
}

.execution-record-head time {
  margin-left: auto;
  color: #64748b;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.record-packet,
.record-budget {
  min-width: 0;
  max-width: 100%;
  color: #94a3b8;
  font-weight: 400;
  overflow-wrap: anywhere;
}

.record-packet {
  max-width: 170px;
}

.evidence-list {
  display: grid;
  gap: 6px;
}

.evidence-list p {
  margin: 0;
  padding: 7px 9px;
  color: #475569;
  font-size: 12px;
  line-height: 1.45;
  background: #f8fafc;
  border-left: 3px solid #cbd5e1;
}

@media (max-width: 980px) {
  .ai-task-workspace {
    grid-template-columns: 1fr;
  }

  .task-tree-panel {
    overflow: visible;
  }

  .task-tree {
    flex: 0 0 auto;
    min-height: 0;
    max-height: 280px;
  }

  .task-detail-panel {
    grid-column: 1 / -1;
  }
}

@media (max-width: 760px) {
  .task-system-page {
    padding: 12px;
  }

  .task-header {
    flex-direction: column;
    align-items: stretch;
  }

  .header-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .stats-strip {
    align-self: flex-start;
    width: auto;
    background: transparent;
    border: 0;
  }

  .stat-item {
    border: 1px solid #e5e7eb;
  }

  .action-queue {
    flex-direction: column;
    gap: 7px;
  }

  .action-queue-list {
    width: 100%;
  }

  .action-queue-item {
    width: 100%;
    max-width: 100%;
  }

  .action-queue-main strong,
  .action-queue-main span {
    white-space: normal;
  }

  .health-strip {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .automation-health-details {
    width: 100%;
    margin-left: 0;
  }

  .automation-health-grid {
    max-width: 100%;
  }

  .automation-run-summary p {
    white-space: normal;
  }

  .automation-blockers button {
    white-space: normal;
  }

  .planner-suggestion {
    grid-template-columns: 1fr;
  }

  .archive-review-item {
    grid-template-columns: 1fr;
  }

  .archive-review-actions {
    justify-content: flex-start;
  }

  .suggestion-actions {
    justify-content: flex-start;
  }

  .title-line {
    grid-template-columns: 1fr;
  }

  .execution-brief-main {
    align-items: flex-start;
  }

  .execution-brief-main span {
    white-space: normal;
  }

  .execution-stop-banner {
    align-items: flex-start;
    flex-direction: column;
  }

  .execution-stop-banner span {
    white-space: normal;
  }

  .ai-task-workspace,
  .field-grid,
  .packet-lists,
  .execution-extra-grid,
  .execution-form,
  .execution-quick-writeback,
  .doc-grid {
    grid-template-columns: 1fr;
  }

  .stats-strip {
    width: 100%;
  }

  .execution-status :deep(.el-segmented) {
    width: 100%;
    max-width: 100%;
  }

  .execution-status :deep(.el-segmented__item) {
    min-width: 0;
    padding: 0 5px;
  }

  .execution-budget-inputs label {
    width: 100%;
    grid-template-columns: 42px minmax(0, 1fr);
  }

  .execution-budget-inputs :deep(.el-input-number) {
    width: 100%;
  }

  .execution-record-head {
    justify-content: flex-start;
  }

  .execution-record-head time {
    width: 100%;
    margin-left: 0;
  }

  .execution-actions {
    justify-content: flex-start;
  }
}
</style>











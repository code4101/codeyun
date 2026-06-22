import api from '@/api'

export type CaptureStatus = 'inbox' | 'triaged' | 'discarded'
export type TaskKind = 'project' | 'task'
export type TaskStatus =
  | 'inbox'
  | 'planned'
  | 'ready'
  | 'running'
  | 'blocked'
  | 'done'
  | 'review_for_archive'
  | 'archived'
export type ExecutionPolicy = 'manual_only' | 'ask_before_execute' | 'auto_report' | 'auto_safe'
export type TaskRisk = 'low' | 'medium' | 'high'
export type ExecutionRecordStatus = 'progress' | 'done' | 'blocked'

export type TaskEvidenceAttachment = {
  id: string
  name: string
  mimeType: string
  filename: string
  url: string
  size: number
  sha256: string
}

export type CaptureItem = {
  id: string
  rawText: string
  source: string
  capturedAt: string
  status: CaptureStatus
  tags: string[]
  contextKind: string
  projectPath: string
  attachments: TaskEvidenceAttachment[]
  linkedTaskId?: string
}

export type TaskDocument = {
  goal: string
  currentState: string
  context: string
  knownFacts: string
  dependencies: string
  nextStep: string
  doneCriteria: string
  resultSummary: string
}

export type ExecutionRecord = {
  id: string
  recordedAt: string
  summary: string
  verification: string
  remainingRisk: string
  nextStep: string
  status: ExecutionRecordStatus
  packetId?: string
  budgetUsed?: {
    stepsDone: number
    commandsRun: number
    filesChanged: number
  }
}

export type TaskNode = {
  id: string
  title: string
  kind: TaskKind
  status: TaskStatus
  parentId: string | null
  sortOrder: number
  executionPolicy: ExecutionPolicy
  risk: TaskRisk
  dependsOn: string[]
  relatedTaskIds: string[]
  suggestedSkill: string
  document: TaskDocument
  attachments: TaskEvidenceAttachment[]
  evidenceLog: string[]
  executionRecords?: ExecutionRecord[]
  createdAt: string
  updatedAt: string
  completedAt?: string
  archivedAt?: string
}

export type PlannerSuggestion = {
  id: string
  kind: 'split' | 'merge' | 'dependency' | 'document' | 'archive'
  severity: 'info' | 'warning'
  taskId?: string
  relatedTaskIds: string[]
  title: string
  rationale: string
  proposedAction: string
  preview?: {
    summary?: string
    creates?: Array<{
      title?: string
      kind?: TaskKind
      dependsOnPrevious?: boolean
      dependsOnTitle?: string
      document?: Partial<TaskDocument>
    }>
    updates?: Array<{
      taskId?: string
      field?: string
      value?: string
      note?: string
    }>
    targetTaskId?: string
    sourceTaskIds?: string[]
  }
  status: 'open' | 'dismissed' | 'applied'
  createdAt: string
  resolvedAt?: string
}

export type PlannerLog = {
  id: string
  ranAt: string
  summary: string
  selectedTaskId?: string | null
  planningDecision?: PlannerDecision
  actions: string[]
  suggestionIds?: string[]
}

export type ExecutionPacketDecisionMode = 'skip' | 'ask_user' | 'report_only' | 'execute_safe'
export type AutomationDirectiveAction = 'stop_for_audit' | 'skip' | 'ask_user' | 'report_only' | 'execute_safe'

export type PlannerDecision = {
  selectedTaskId?: string | null
  requestedTaskId?: string | null
  selectedReason: string
  candidateCount: number
  skippedCount: number
  candidates: Array<{
    taskId: string
    title: string
    status?: TaskStatus
    executionPolicy?: ExecutionPolicy
    risk?: TaskRisk
    kind?: TaskKind
    rank?: number[]
    reason: string
  }>
  skipped: Array<{
    taskId: string
    title: string
    reasons: string[]
  }>
}

export type AutomationDirective = {
  action: AutomationDirectiveAction
  shouldExecute: boolean
  shouldModifyCode: boolean
  shouldWriteBack: boolean
  writebackStatus?: ExecutionRecordStatus | null
  stopReason: string
  summaryHint: string
  requiredChecks: string[]
  completionTemplate?: {
    finalReport: string[]
    writeback: {
      status?: ExecutionRecordStatus | null
      summary: string
      verification: string
      remainingRisk: string
      nextStep: string
    }
    notes: string[]
  }
}

export type ExecutionPacket = {
  hasTask: boolean
  task: TaskNode | null
  decision: {
    mode: ExecutionPacketDecisionMode
    reason: string
    allowedActions: string[]
    forbiddenActions: string[]
  }
  budget?: {
    maxSteps: number
    maxFilesChanged: number
    maxCommands: number
    mayModifyCode: boolean
    requiresVerification: boolean
    stopConditions: string[]
  }
  planningDecision?: PlannerDecision
  plannerSuggestions?: Array<{
    id?: string
    kind?: PlannerSuggestion['kind']
    severity?: PlannerSuggestion['severity']
    taskId?: string
    relatedTaskIds?: string[]
    title?: string
    proposedAction?: string
    previewSummary?: string
  }>
  snapshot?: {
    packetId: string
    createdAt: string
    taskId: string
    taskUpdatedAt?: string | null
    plannerLogId?: string | null
    plannerRanAt?: string | null
    documentDigest: Pick<TaskDocument, 'goal' | 'currentState' | 'nextStep' | 'doneCriteria'>
  } | null
  writeback: {
    taskId: string
    username?: string
    endpoint: string
    cli: string
    argvTemplate?: string[]
    statuses: ExecutionRecordStatus[]
  } | null
  automationDirective?: AutomationDirective
  prompt: string
}

export type TaskSpaceAuditIssue = {
  code: string
  severity: 'error' | 'warning' | 'info'
  message: string
  taskId?: string
}

export type TaskSpaceAudit = {
  ok: boolean
  checkedAt: string
  summary: {
    tasks: number
    activeTasks: number
    inboxCaptures: number
    runningTasks: number
    errors: number
    warnings: number
    latestSelectedTaskId?: string | null
  }
  issues: TaskSpaceAuditIssue[]
}

export type AiTaskAutomationHealth = {
  ok: boolean
  mutated: boolean
  mode: 'current' | 'simulated_plan'
  checkedAt: string
  currentFingerprint?: string
  validatedFingerprint?: string
  syncCommand?: string
  failures: Array<{
    code: string
    message: string
  }>
  contract: {
    ok: boolean
    selectedTaskId?: string | null
    action?: AutomationDirectiveAction
    shouldExecute?: boolean
    shouldModifyCode?: boolean
    shouldWriteBack?: boolean
    writebackStatus?: ExecutionRecordStatus | null
    stopReason?: string
    summaryHint?: string
    requiredChecks?: string[]
    blockerCount?: number
    blockers?: Array<{
      taskId: string
      title: string
      reasons: string[]
    }>
    audit: TaskSpaceAudit['summary']
  }
  recentRun?: {
    latestPlannerLog?: PlannerLog | null
    selectedTask?: {
      id?: string
      title?: string
      status?: TaskStatus
      updatedAt?: string
    } | null
    latestExecutionRecord?: ExecutionRecord | null
  }
  automationToml?: {
    path: string
    exists: boolean
    config: null | {
      id?: string
      name?: string
      kind?: string
      status?: string
      rrule?: string
      model?: string
      reasoning_effort?: string
      execution_environment?: string
      cwds?: string[]
      promptMatches?: boolean
    }
    failures: Array<{
      code: string
      message: string
    }>
  } | null
}

export type TaskSpace = {
  version: 2
  captures: CaptureItem[]
  tasks: TaskNode[]
  plannerLogs: PlannerLog[]
  plannerSuggestions: PlannerSuggestion[]
  _fingerprint?: string
}

export async function fetchAiTaskSpace(): Promise<TaskSpace> {
  const response = await api.get('/ai-task-space')
  return response.data
}

export async function saveAiTaskSpace(taskSpace: TaskSpace): Promise<TaskSpace> {
  const response = await api.put('/ai-task-space', {
    task_space: taskSpace,
    expected_fingerprint: taskSpace._fingerprint ?? '',
  })
  return response.data
}

export async function createAiTaskCapture(
  rawText: string,
  source: string,
  options: { tags?: string[]; contextKind?: string; projectPath?: string; images?: Array<{ name?: string; mime_type?: string; data_base64: string }> } = {},
): Promise<TaskSpace> {
  const response = await api.post('/ai-task-space/captures', {
    raw_text: rawText,
    source,
    tags: options.tags ?? [],
    context_kind: options.contextKind ?? 'task',
    project_path: options.projectPath ?? '',
    images: options.images ?? [],
  })
  return response.data
}

export async function promoteAiTaskCapture(captureId: string): Promise<TaskSpace> {
  const response = await api.post(`/ai-task-space/captures/${encodeURIComponent(captureId)}/promote`)
  return response.data
}

export async function runAiTaskPlannerCheck(): Promise<TaskSpace> {
  const response = await api.post('/ai-task-space/planner/run-once')
  return response.data
}

export async function actOnAiTaskPlannerSuggestion(
  suggestionId: string,
  action: 'apply' | 'dismiss',
  expectedFingerprint = '',
): Promise<TaskSpace> {
  const response = await api.post(`/ai-task-space/planner/suggestions/${encodeURIComponent(suggestionId)}`, {
    action,
    expected_fingerprint: expectedFingerprint,
  })
  return response.data
}

export async function fetchAiTaskExecutionPacket(taskId?: string | null): Promise<ExecutionPacket> {
  const response = await api.get('/ai-task-space/planner/execution-packet', {
    params: taskId ? { task_id: taskId } : undefined,
  })
  return response.data
}

export async function fetchAiTaskSpaceAudit(): Promise<TaskSpaceAudit> {
  const response = await api.get('/ai-task-space/audit')
  return response.data
}

export async function fetchAiTaskAutomationHealth(): Promise<AiTaskAutomationHealth> {
  const response = await api.get('/ai-task-space/automation-health')
  return response.data
}

export async function appendAiTaskExecutionRecord(
  taskId: string,
  payload: {
    summary: string
    verification?: string
    remainingRisk?: string
    nextStep?: string
    status?: ExecutionRecordStatus
    packetId?: string
    expectedTaskUpdatedAt?: string
    stepsDone?: number
    commandsRun?: number
    filesChanged?: number
  },
): Promise<TaskSpace> {
  const response = await api.post(`/ai-task-space/tasks/${encodeURIComponent(taskId)}/execution-records`, {
    summary: payload.summary,
    verification: payload.verification ?? '',
    remaining_risk: payload.remainingRisk ?? '',
    next_step: payload.nextStep ?? '',
    status: payload.status ?? 'progress',
    packet_id: payload.packetId ?? '',
    expected_task_updated_at: payload.expectedTaskUpdatedAt ?? '',
    steps_done: payload.stepsDone ?? 0,
    commands_run: payload.commandsRun ?? 0,
    files_changed: payload.filesChanged ?? 0,
  })
  return response.data
}

export async function confirmAiTaskUserReady(taskId: string, note = '', expectedFingerprint = ''): Promise<TaskSpace> {
  const response = await api.post(`/ai-task-space/tasks/${encodeURIComponent(taskId)}/confirm-user-ready`, {
    note,
    expected_fingerprint: expectedFingerprint,
  })
  return response.data
}

export type AiTaskReviewAction = 'mark_done' | 'request_archive_review' | 'keep_unarchived' | 'archive'

export async function applyAiTaskReviewAction(
  taskId: string,
  action: AiTaskReviewAction,
  expectedFingerprint = '',
): Promise<TaskSpace> {
  const response = await api.post(`/ai-task-space/tasks/${encodeURIComponent(taskId)}/review-action`, {
    action,
    expected_fingerprint: expectedFingerprint,
  })
  return response.data
}

import api from '@/api'

export type CodexSaverDecision = 'deepseek' | 'deny'

export interface CodexSaverRuleMatch {
  prompt_includes: string[]
  path_includes: string[]
  file_extensions: string[]
  input_kinds: string[]
}

export interface CodexSaverRule {
  id: string
  label: string
  enabled: boolean
  order: number
  match: CodexSaverRuleMatch
  decision: CodexSaverDecision
  reason: string
}

export interface CodexSaverConfig {
  provider_id: string
  model: string
  flash_model: string
  pro_model: string
  use_flash_gate: boolean
  default_decision: CodexSaverDecision
  multimodal_decision: CodexSaverDecision
  auto_apply: boolean
  write_boundary_mode: 'none' | 'cwd' | 'allowlist'
  allowed_write_roots: string[]
  log_file_name: string
  log_backup_file_name: string
  log_max_bytes: number
  require_verification_success: boolean
  rules: CodexSaverRule[]
}

export interface CodexSaverRoutePreviewRequest {
  task: string
  cwd?: string
  context?: string
  files?: string[]
  input_kinds?: string[]
  verification_commands?: string[]
  allow_auto_apply?: boolean | null
}

export interface CodexSaverRoutePreviewResponse {
  decision: CodexSaverDecision
  reason: string
  hit_rules: CodexSaverRule[]
}

export interface CodexSaverLogsResponse {
  items: Array<{
    path: string
    content: string
  }>
}

export interface CodexSaverRuntimeRun {
  id: string
  status: string
  stage: string
  started_at: number
  updated_at: number
  finished_at: number | null
  age_ms: number
  duration_ms?: number
  task: string
  cwd: string
  input_kinds: string[]
  model: string
  model_tier: string
  summary: string
  reason: string
  error: string
  fallback: string
}

export interface CodexSaverRuntimeResponse {
  active: CodexSaverRuntimeRun[]
  recent: CodexSaverRuntimeRun[]
  now: number
}

export interface CodexSaverDoctorResponse {
  provider: Record<string, unknown>
  log: Record<string, unknown>
  mcp: Record<string, unknown>
}

export interface CodexSaverMcpBearerResponse {
  url: string
  environment_variable: string
  header_name: string
  header_scheme: string
  configured: boolean
  token: string
}

export function getCodexSaverConfig() {
  return api.get<CodexSaverConfig>('/codex-saver/config').then((response) => response.data)
}

export function saveCodexSaverConfig(payload: CodexSaverConfig) {
  return api.put<CodexSaverConfig>('/codex-saver/config', payload).then((response) => response.data)
}

export function previewCodexSaverRoute(payload: CodexSaverRoutePreviewRequest) {
  return api.post<CodexSaverRoutePreviewResponse>('/codex-saver/route-preview', payload).then((response) => response.data)
}

export function getCodexSaverLogs(cwd = '', maxBytes = 200000) {
  return api.get<CodexSaverLogsResponse>('/codex-saver/logs', {
    params: { cwd, max_bytes: maxBytes },
  }).then((response) => response.data)
}

export function getCodexSaverRuntime() {
  return api.get<CodexSaverRuntimeResponse>('/codex-saver/runtime').then((response) => response.data)
}

export function runCodexSaverDoctor(cwd = '') {
  return api.post<CodexSaverDoctorResponse>('/codex-saver/doctor', { cwd }).then((response) => response.data)
}

export function getCodexSaverMcpBearer(reveal = false) {
  return api.post<CodexSaverMcpBearerResponse>('/codex-saver/mcp-bearer', { reveal }).then((response) => response.data)
}

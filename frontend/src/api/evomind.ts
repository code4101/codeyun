import api from '@/api'

const EVOMIND_SCAN_TIMEOUT_MS = 10 * 60 * 1000

export interface EvoMindCodexScanRequest {
  root_dir?: string | null
  max_threads?: number
  max_cases?: number
  min_score?: number
  use_codex_cli?: boolean
  codex_cli_limit?: number
  reset_cache?: boolean
  scan_rule_text?: string | null
}

export interface EvoMindCaseSource {
  root_dir: string
  thread_id?: string | null
  thread_title?: string | null
  message_seq?: number | null
  timestamp?: string | null
  project_label?: string | null
  workspace_root?: string | null
  score: number
}

export interface EvoMindEvidenceTurn {
  seq?: number | null
  role: string
  kind?: string
  label?: string
  text: string
  timestamp?: string | null
  is_signal?: boolean
}

export interface EvoMindCaseCandidate {
  id: string
  title: string
  domain: string
  signal_type: string
  evidence_strength: string
  friction_level: string
  original_task: string
  bad_attempt: string
  user_corrections: string
  final_pattern: string
  inferred_rule: string
  anti_patterns: string[]
  positive_patterns: string[]
  evidence_turns?: EvoMindEvidenceTurn[]
  status: string
  source: EvoMindCaseSource
}

export interface EvoMindCodexScanResponse {
  root_dir: string
  total_threads: number
  scanned_threads: number
  skipped_threads: number
  scanned_messages: number
  heuristic_candidate_count: number
  analysis_mode: string
  codex_cli_used: boolean
  codex_cli_invoked: boolean
  cache_hit_count: number
  cache_miss_count: number
  cache_rule_hash: string
  cache_rule_mismatch: boolean
  cache_reset: boolean
  items: EvoMindCaseCandidate[]
}

export interface EvoMindProposalCaseInput {
  id: string
  title: string
  domain: string
  signal_type: string
  evidence_strength: string
  friction_level: string
  original_task: string
  bad_attempt: string
  user_corrections: string
  final_pattern: string
  inferred_rule: string
  anti_patterns: string[]
  positive_patterns: string[]
  evidence_turns?: EvoMindEvidenceTurn[]
  source?: Record<string, unknown> | null
}

export interface EvoMindProposalRequest {
  case: EvoMindProposalCaseInput
  target?: 'skill' | 'agents' | 'docs'
  use_codex_cli?: boolean
  proposal_rule_text?: string | null
}

export interface EvoMindCaseCardRequest {
  case: EvoMindProposalCaseInput
  case_rule_text?: string | null
}

export interface EvoMindCaseCardResponse {
  id: string
  title: string
  domain: string
  signal_type: string
  evidence_strength: string
  friction_level: string
  original_task: string
  bad_attempt: string
  user_corrections: string
  final_pattern: string
  inferred_rule: string
  anti_patterns: string[]
  positive_patterns: string[]
  evidence_turns?: EvoMindEvidenceTurn[]
  status: string
  generation_mode: string
}

export interface EvoMindProposalResponse {
  id: string
  source_case_id: string
  target_type: string
  target: string
  target_path: string
  target_status: string
  lifecycle: string
  title: string
  trigger: string
  rule_text: string
  scope: string
  anti_scope: string
  risk: string
  anti_patterns: string[]
  positive_patterns: string[]
  verification_plan: string[]
  content: string
  created_at: string
  generation_mode: string
  warning: string
}

export async function scanEvoMindCodexCases(payload: EvoMindCodexScanRequest = {}) {
  const response = await api.post<EvoMindCodexScanResponse>('/evomind/cases/scan-codex', payload, {
    timeout: EVOMIND_SCAN_TIMEOUT_MS,
  })
  return response.data
}

export async function deriveEvoMindCaseCard(payload: EvoMindCaseCardRequest) {
  const response = await api.post<EvoMindCaseCardResponse>('/evomind/cases/derive-card', payload, {
    timeout: EVOMIND_SCAN_TIMEOUT_MS,
  })
  return response.data
}

export async function generateEvoMindProposal(payload: EvoMindProposalRequest) {
  const response = await api.post<EvoMindProposalResponse>('/evomind/proposals/from-case', payload, {
    timeout: EVOMIND_SCAN_TIMEOUT_MS,
  })
  return response.data
}

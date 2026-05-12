import api from '@/api'

export interface WeChatArchiveStatus {
  db_path: string
  exists: boolean
  accounts: number
  chats: number
  messages: number
  latest_collected_at: string | null
}

export interface WeChatArchiveChat {
  id: number
  name: string
  chat_type: string | null
  remark: string | null
  group_member_count: number | null
  status: string | null
  last_error: string | null
  sync_enabled: number | null
  sync_priority: number | null
  sync_latest: number | null
  backfill_history: number | null
  loaded_count: number | null
  scroll_count: number | null
  reached_top: number | null
  last_incremental_at: string | null
  last_history_at: string | null
  last_success_at: string | null
  consecutive_failures: number | null
  next_due_at: string | null
  sync_updated_at: string | null
  message_count: number
  latest_collected_at: string | null
  first_message_time: string | null
  last_message_time: string | null
}

export interface WeChatArchiveMessage {
  id: number
  chat_id: number
  chat_name: string
  direction: string | null
  sender: string | null
  sender_remark: string | null
  message_type: string | null
  content: string | null
  media_path: string | null
  normalized_time: string | null
  raw_time_label: string | null
  raw_id: string | null
  raw: unknown
  fingerprint: string
  collected_at: string
}

export interface WeChatArchiveMessagePage {
  total: number
  items: WeChatArchiveMessage[]
  db_path: string
}

export interface WeChatArchiveMessageType {
  message_type: string | null
  count: number
}

export interface WeChatArchiveImportPayload {
  chat_name: string
  mode?: 'loaded' | 'scroll' | 'full'
  max_scrolls?: number | null
  exact?: boolean
  save_media?: boolean
}

export interface WeChatArchiveImportResult {
  chat_name: string
  matched_name: string | null
  chat_id: number | null
  db_path: string
  media_dir: string
  seen: number
  inserted: number
  scroll_count: number
  reached_top: boolean
  last_error: string | null
  status: WeChatArchiveStatus
}

export interface WeChatArchiveSyncPlanItem {
  chat_id: number | null
  name: string
  chat_type?: string | null
  enabled: boolean
  priority: number
  sync_latest: boolean
  backfill_history: boolean
  reached_top: boolean
  message_count: number
  latest_collected_at: string | null
  first_message_time?: string | null
  last_message_time: string | null
  last_incremental_at: string | null
  last_history_at: string | null
  last_success_at: string | null
  consecutive_failures: number
  next_due_at: string | null
  score: number
  reasons: string[]
  due: boolean
}

export interface WeChatArchiveSyncStartPayload {
  mode?: 'incremental' | 'latest' | 'history' | 'history_clearance' | 'full'
  chat_name?: string | null
  chat_names?: string[] | null
  max_runtime?: number
  max_chats?: number
  max_scrolls_total?: number
  max_scrolls_per_chat?: number
  exact?: boolean
  save_media?: boolean
}

export interface WeChatArchiveSyncStatus {
  active: boolean
  queue: {
    is_idle: boolean
    running: Record<string, unknown> | null
    pending: Record<string, unknown>[]
    recent: Record<string, unknown>[]
  }
  latest_queue_run: Record<string, unknown> | null
  latest_result: {
    mode: string
    started_at: number
    finished_at: number
    payload: Record<string, unknown>
    result: Record<string, unknown>
  } | null
  status: WeChatArchiveStatus
}

export async function fetchWeChatArchiveStatus() {
  const response = await api.get<WeChatArchiveStatus>('/wechat-archive/status')
  return response.data
}

export async function fetchWeChatArchiveChats() {
  const response = await api.get<{ items: WeChatArchiveChat[]; db_path: string }>('/wechat-archive/chats')
  return response.data
}

export async function fetchWeChatArchiveMessages(params: {
  chat_id?: number
  q?: string
  direction?: string
  message_type?: string
  limit?: number
  offset?: number
} = {}) {
  const response = await api.get<WeChatArchiveMessagePage>('/wechat-archive/messages', {
    params,
  })
  return response.data
}

export async function fetchWeChatArchiveMessageTypes(params: { chat_id?: number } = {}) {
  const response = await api.get<{ items: WeChatArchiveMessageType[] }>('/wechat-archive/message-types', {
    params,
  })
  return response.data
}

export async function importWeChatArchive(payload: WeChatArchiveImportPayload) {
  const response = await api.post<WeChatArchiveImportResult>('/wechat-archive/import', payload, {
    timeout: payload.mode === 'full' ? 30 * 60 * 1000 : 3 * 60 * 1000,
  })
  return response.data
}

export async function fetchWeChatArchiveSyncPlan(params: {
  max_chats?: number
  chat_name?: string
  kind?: 'incremental' | 'history'
} = {}) {
  const response = await api.get<{ items: WeChatArchiveSyncPlanItem[]; db_path: string }>('/wechat-archive/sync-plan', {
    params,
  })
  return response.data
}

export async function fetchWeChatArchiveSyncStatus() {
  const response = await api.get<WeChatArchiveSyncStatus>('/wechat-archive/sync-status')
  return response.data
}

export async function startWeChatArchiveSync(payload: WeChatArchiveSyncStartPayload) {
  const response = await api.post<{ queued: boolean; queue_task_id: string; sync_status: WeChatArchiveSyncStatus }>(
    '/wechat-archive/sync/start',
    payload,
    { timeout: 30 * 1000 },
  )
  return response.data
}

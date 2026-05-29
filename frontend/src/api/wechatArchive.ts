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

export interface WeChatDbStatus {
  db_storage_path: string
  device_id?: string
  source_format?: string
  self_username?: string | null
  exists: boolean
  ready: boolean
  databases: Record<string, boolean>
}

export interface WeChatDbDevice {
  id: string
  label: string
  current: boolean
  ready: boolean
  exists: boolean
  source_format: string
  db_storage_path: string
  self_username?: string | null
  can_sync_live: boolean
  error?: string | null
}

export interface WeChatDbLiveSyncResult {
  live_account_root: string
  elapsed_seconds: number
  copy: {
    source: string
    target: string
    copied: number
    unchanged: number
    errors: string[]
    error_count: number
  }
  decrypt: {
    source: string
    target: string
    decrypted: number
    skipped: number
    failed: string[]
    failed_count: number
  }
  media: {
    scanned_chats: number
    exported_files: number
    new_files: number
    errors: string[]
    error_count: number
  } | null
}

export interface WeChatDbSchemaItem {
  name: string
  path: string
  exists: boolean
  objects: number
  tables: string[]
}

export interface WeChatDbChat {
  username: string
  name: string
  table_name: string
  chat_type: string
  is_folded?: boolean
  is_folded_entry?: boolean
  message_count: number
  first_time: number | null
  last_time: number | null
  summary: string | null
  unread_count: number | null
  last_msg_type: number | null
  last_msg_type_normalized: number | null
  last_msg_sender: string | null
  last_msg_sender_name: string | null
  avatar_data_url?: string | null
}

export interface WeChatDbResourceExport {
  kind: 'image' | 'video' | 'file'
  file_name: string
  size: number
  source_path: string
  stored_path: string
  download_name: string
  md5: string
}

export interface WeChatDbResourceItem {
  resource_id: number | null
  type: number | null
  size: number
  data_index: string | null
  packed_text: string | null
  export?: WeChatDbResourceExport
}

export interface WeChatDbAppMessage {
  title?: string
  description?: string
  url?: string
  app_type?: number | null
  file_ext?: string
  total_size?: number | null
  md5?: string
  thumb_url?: string
  refer_content?: string
  refer?: {
    content?: string
    display_name?: string
    from_user?: string
    chat_user?: string
    type?: number | null
    create_time?: number | null
  }
}

export interface WeChatDbMessage {
  local_id: number
  server_id: number | null
  local_type: number | null
  local_type_normalized: number | null
  sort_seq: number | null
  sender_username: string | null
  sender_name: string | null
  sender_avatar_data_url?: string | null
  create_time: number | null
  create_time_text: string | null
  status: number | null
  upload_status: number | null
  download_status: number | null
  server_seq: number | null
  origin_source: number | null
  source: string | null
  message_content: string | null
  message_text: string | null
  compress_content: string | null
  source_text: string | null
  appmsg?: WeChatDbAppMessage | null
  packed_info_size: number | null
  resource: {
    resource_count: number
    total_size: number
    resource_types: string | null
    data_indexes: string | null
    items?: WeChatDbResourceItem[]
  } | null
}

export interface WeChatDbMessagePage {
  total: number
  items: WeChatDbMessage[]
  table_name: string
  db_storage_path: string
}

export interface WeChatDbMessageType {
  local_type: number
  count: number
}

export interface WeChatDbTableInfo {
  name: string
  count: number
  columns: string[]
}

export interface WeChatDbTablePage {
  database: string
  table: string
  columns: string[]
  total: number
  items: Record<string, unknown>[]
}

export async function fetchWeChatArchiveStatus() {
  const response = await api.get<WeChatArchiveStatus>('/wechat-archive/status')
  return response.data
}

export async function fetchWeChatDbDevices() {
  const response = await api.get<{ items: WeChatDbDevice[] }>('/wechat-archive/db-devices')
  return response.data
}

export async function fetchWeChatDbStatus(params: { device_id?: string } = {}) {
  const response = await api.get<WeChatDbStatus>('/wechat-archive/db-status', { params })
  return response.data
}

export async function syncWeChatDbFromLive(params: { device_id?: string } = {}) {
  const response = await api.post<WeChatDbLiveSyncResult>('/wechat-archive/db-sync-live', undefined, {
    params,
    timeout: 5 * 60 * 1000,
  })
  return response.data
}

export async function fetchWeChatDbSchema(params: { device_id?: string } = {}) {
  const response = await api.get<{ items: WeChatDbSchemaItem[]; db_storage_path: string; device_id?: string }>(
    '/wechat-archive/db-schema',
    { params },
  )
  return response.data
}

export async function fetchWeChatDbChats(params: {
  device_id?: string
  q?: string
  limit?: number
  offset?: number
  scope?: 'main' | 'folded' | 'all'
} = {}) {
  const response = await api.get<{ items: WeChatDbChat[]; total: number; db_storage_path: string }>(
    '/wechat-archive/db-chats',
    {
      params,
    },
  )
  return response.data
}

export async function fetchWeChatDbMessages(params: {
  device_id?: string
  chat_username: string
  q?: string
  message_type?: string
  limit?: number
  offset?: number
  order?: 'asc' | 'desc'
  include_resources?: boolean
}) {
  const response = await api.get<WeChatDbMessagePage>('/wechat-archive/db-messages', {
    params,
    timeout: params.include_resources === false ? 15000 : 2 * 60 * 1000,
  })
  return response.data
}

export async function fetchWeChatDbMessageCount(params: {
  device_id?: string
  chat_username: string
  q?: string
  message_type?: string
}) {
  const response = await api.get<{ total: number; table_name: string; db_storage_path: string }>(
    '/wechat-archive/db-message-count',
    {
      params,
    },
  )
  return response.data
}

export async function fetchWeChatDbMessageTypes(params: { device_id?: string; chat_username?: string } = {}) {
  const response = await api.get<{ items: WeChatDbMessageType[] }>('/wechat-archive/db-message-types', {
    params,
  })
  return response.data
}

export async function downloadWeChatDbMedia(item: WeChatDbResourceExport, deviceId?: string) {
  const [kind, storedName] = item.download_name.split('/')
  const response = await api.get<Blob>(`/wechat-archive/db-media/${kind}/${encodeURIComponent(storedName)}`, {
    params: deviceId ? { device_id: deviceId } : undefined,
    responseType: 'blob',
  })
  return response.data
}

export function weChatDbMediaUrl(item: WeChatDbResourceExport, deviceId?: string) {
  const [kind, storedName] = item.download_name.split('/')
  const query = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : ''
  return `/api/wechat-archive/db-media/${kind}/${encodeURIComponent(storedName)}${query}`
}

export async function fetchWeChatDbTables(params: { device_id?: string; database: string }) {
  const response = await api.get<{ items: WeChatDbTableInfo[] }>('/wechat-archive/db-tables', {
    params,
  })
  return response.data
}

export async function fetchWeChatDbTableRows(params: {
  device_id?: string
  database: string
  table: string
  q?: string
  limit?: number
  offset?: number
}) {
  const response = await api.get<WeChatDbTablePage>('/wechat-archive/db-table-rows', {
    params,
  })
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

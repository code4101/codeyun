/** 邮件记录与状态的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuMailRecord {
  id: string;
  mail_key: string;
  mail_id?: string;
  title: string;
  normalized_title?: string;
  mail_type?: string;
  create_time_text?: string;
  create_time_ms?: number | null;
  source?: string;
  status?: string;
  execution_status?: 'unclaimed' | 'claimed' | 'claimed_absent' | 'no_attachment' | string;
  desired_status?: '锁定' | '留存' | '可领' | string;
  present_in_runtime?: boolean;
  reward_getted?: boolean | null;
  has_attachment?: boolean;
  attachment_count?: number;
  last_runtime_sync_at?: string;
  locked?: boolean;
  action_policy?: string;
  last_action_error?: string;
  seen_count?: number;
  first_seen_at?: number;
  last_seen_at?: number;
  payload?: Record<string, any>;
  evidence?: Record<string, any>;
  created_at?: number;
  updated_at?: number;
}

export interface FanxiuMailRecordListResponse {
  ok: boolean;
  count: number;
  total?: number;
  offset?: number;
  limit?: number;
  records: FanxiuMailRecord[];
}

export interface FanxiuMailRecordUpdateResponse {
  ok: boolean;
  record: FanxiuMailRecord;
}

export interface FanxiuMailRuntimeSyncResponse {
  ok: boolean;
  complete: boolean;
  source: 'runtime_memory' | string;
  inserted: number;
  updated: number;
  absent: number;
  record_count: number;
  captured_at?: string;
}

export const getFanxiuMailRecords = (
  params: { limit?: number; offset?: number; status?: string; action_policy?: string; source?: 'all' | 'runtime_memory'; include_absent?: boolean } = {},
) => {
  return api
    .get<FanxiuMailRecordListResponse>('/fanxiu/mail-records', { params, timeout: 120000 })
    .then(res => res.data);
};

export const updateFanxiuMailRecordStatus = (mailKey: string, status: '锁定' | '留存' | '可领') => {
  return api
    .patch<FanxiuMailRecordUpdateResponse>(`/fanxiu/mail-records/${encodeURIComponent(mailKey)}`, { status }, { timeout: 30000 })
    .then(res => res.data);
};

export const syncFanxiuMailRuntime = () => {
  return api
    .post<FanxiuMailRuntimeSyncResponse>('/fanxiu/mail-records/sync-runtime', undefined, { timeout: 180000 })
    .then(res => res.data);
};

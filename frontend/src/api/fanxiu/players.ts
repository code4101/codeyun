/** 角色档案与服务器关系的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuPlayerProfileRecord {
  id: string;
  observation_id: string;
  observed_at: string;
  observed_date: string;
  source_kind: string;
  role_id?: string | number;
  role_id_text: string;
  name: string;
  battle_score?: number | null;
  battle_score_text?: string;
  attack_value?: number | null;
  attack_text?: string;
  xianlv_team_fight_score_max?: number | null;
  xianlv_team_fight_score_text?: string;
  xianlv_team_observed_at?: string;
  [key: string]: unknown;
}

export interface FanxiuPlayerProfileRecordListResponse {
  ok: boolean;
  count: number;
  records: FanxiuPlayerProfileRecord[];
  daily_count: number;
  daily_records: FanxiuPlayerProfileRecord[];
  xianlv_team_count: number;
  xianlv_team_records: FanxiuPlayerProfileRecord[];
  xianlv_team_daily_count: number;
  xianlv_team_daily_records: FanxiuPlayerProfileRecord[];
}

export interface FanxiuServerRelationServer {
  server_id: number;
  server_order: number;
  server_name: string;
}

export interface FanxiuServerRelationNode {
  key: string;
  label: string;
  servers?: FanxiuServerRelationServer[];
}

export interface FanxiuServerRelationGroup {
  key: string;
  label: string;
  children: FanxiuServerRelationNode[];
}

export interface FanxiuServerRelationTreeResponse {
  ok: boolean;
  version: number;
  ordering: 'protection_desc';
  groups: FanxiuServerRelationGroup[];
}

export type FanxiuServerRelationTreeUpdate = Omit<FanxiuServerRelationTreeResponse, 'ok'>;

export const getFanxiuPlayerProfiles = (params: { limit?: number } = {}) => {
  return api
    .get<FanxiuPlayerProfileRecordListResponse>('/fanxiu/business-data/player-profiles', { params, timeout: 120000 })
    .then(res => res.data);
};

export const getFanxiuServerRelations = () => {
  return api
    .get<FanxiuServerRelationTreeResponse>('/fanxiu/server-relations', { timeout: 30000 })
    .then(res => res.data);
};

export const updateFanxiuServerRelations = (payload: FanxiuServerRelationTreeUpdate) => {
  return api
    .put<FanxiuServerRelationTreeResponse>('/fanxiu/server-relations', payload, { timeout: 30000 })
    .then(res => res.data);
};

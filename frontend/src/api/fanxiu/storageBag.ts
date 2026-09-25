/** 储物袋库存与领取策略的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuStorageBagResponse {
  ok: boolean;
  state?: 'complete' | 'cached' | 'runtime_unavailable' | string;
  reason?: string | null;
  bag?: Record<string, any> | null;
}

export interface FanxiuStorageBagDeleteResponse {
  ok: boolean;
  deleted: boolean;
  base_id: number;
  atlas_count: number;
}

export interface FanxiuStorageBagAutoClaimUpdateResponse {
  ok: boolean;
  base_id: number;
  auto_claim: boolean;
}

export interface FanxiuStorageBagNoteUpdateResponse {
  ok: boolean;
  base_id: number;
  note: string;
}

export const getFanxiuBusinessStorageBag = () => {
  return api
    .get<FanxiuStorageBagResponse>('/fanxiu/business-data/storage-bag', { timeout: 120000 })
    .then(res => res.data);
};

export const syncFanxiuBusinessStorageBag = () => {
  return api
    .post<FanxiuStorageBagResponse>('/fanxiu/business-data/storage-bag/sync', undefined, { timeout: 120000 })
    .then(res => res.data);
};

export const deleteFanxiuStorageBagAtlasItem = (baseId: string | number) => {
  return api
    .delete<FanxiuStorageBagDeleteResponse>(`/fanxiu/business-data/storage-bag/atlas/${encodeURIComponent(String(baseId))}`, { timeout: 30000 })
    .then(res => res.data);
};

export const setFanxiuStorageBagAutoClaim = (baseId: string | number, autoClaim: boolean) => {
  return api
    .put<FanxiuStorageBagAutoClaimUpdateResponse>(
      `/fanxiu/business-data/storage-bag/atlas/${encodeURIComponent(String(baseId))}/auto-claim`,
      { auto_claim: autoClaim },
      { timeout: 30000 },
    )
    .then(res => res.data);
};

export const setFanxiuStorageBagNote = (baseId: string | number, note: string) => {
  return api
    .put<FanxiuStorageBagNoteUpdateResponse>(
      `/fanxiu/business-data/storage-bag/atlas/${encodeURIComponent(String(baseId))}/note`,
      { note },
      { timeout: 30000 },
    )
    .then(res => res.data);
};

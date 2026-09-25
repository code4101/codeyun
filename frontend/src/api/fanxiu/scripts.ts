/** 伪代码与可视化脚本的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export type FanxiuPseudoCodeCardScope = 'guard' | 'action';

export interface FanxiuPseudoCodeCard {
  id: string;
  scope: FanxiuPseudoCodeCardScope;
  title: string;
  body: string;
  enabled: boolean;
  order_index: number;
  created_at: number;
  updated_at: number;
}

export interface FanxiuPseudoCodeCardListResponse {
  items: FanxiuPseudoCodeCard[];
}

export interface FanxiuPseudoCodeCardCreatePayload {
  scope: FanxiuPseudoCodeCardScope;
  title?: string;
  body?: string;
  enabled?: boolean;
  order_index?: number;
}

export interface FanxiuPseudoCodeCardUpdatePayload {
  scope?: FanxiuPseudoCodeCardScope;
  title?: string;
  body?: string;
  enabled?: boolean;
  order_index?: number;
}

export interface FanxiuPseudoCodeCompilePayload {
  entry_id?: string;
  model?: string;
  timeout?: number;
}

export interface FanxiuPseudoCodeStartPayload {
  timeout?: number;
}

export interface FanxiuVisualScriptRunPayload {
  entry_id: string;
  card_id: string;
  timeout?: number;
  tick_interval?: number;
  title?: string;
  title_match?: 'contains' | 'exact';
  mode?: 'auto' | 'printwindow' | 'screen';
  area?: 'outer' | 'client';
  crop?: string;
  trim_border?: string;
  rotate?: string;
  fixed_width?: number;
  fixed_height?: number;
  frame_width?: number;
  frame_height?: number;
  quality?: number;
}

export interface FanxiuVisualScriptStopPayload {
  entry_id: string;
  card_id: string;
}

export interface FanxiuPseudoCodeRunResponse {
  ok: boolean;
  status: string;
  script_path: string;
  cache_hits: number;
  cache_misses: number;
  compiled_cards: number;
  log: string;
  result: string;
  updated_at: number;
}

export const listFanxiuPseudoCodeCards = () => {
  return api.get<FanxiuPseudoCodeCardListResponse>('/fanxiu/game-window2/pseudocode-cards').then(res => res.data);
};

export const createFanxiuPseudoCodeCard = (payload: FanxiuPseudoCodeCardCreatePayload) => {
  return api.post<FanxiuPseudoCodeCard>('/fanxiu/game-window2/pseudocode-cards', payload).then(res => res.data);
};

export const updateFanxiuPseudoCodeCard = (cardId: string, payload: FanxiuPseudoCodeCardUpdatePayload) => {
  return api.patch<FanxiuPseudoCodeCard>(`/fanxiu/game-window2/pseudocode-cards/${encodeURIComponent(cardId)}`, payload).then(res => res.data);
};

export const deleteFanxiuPseudoCodeCard = (cardId: string) => {
  return api.delete<{ ok: boolean; id: string }>(`/fanxiu/game-window2/pseudocode-cards/${encodeURIComponent(cardId)}`).then(res => res.data);
};

export const compileFanxiuPseudoCode = (payload: FanxiuPseudoCodeCompilePayload) => {
  return api.post<FanxiuPseudoCodeRunResponse>('/fanxiu/game-window2/pseudocode/compile', payload).then(res => res.data);
};

export const startFanxiuPseudoCode = (payload: FanxiuPseudoCodeStartPayload = {}) => {
  return api.post<FanxiuPseudoCodeRunResponse>('/fanxiu/game-window2/pseudocode/start', payload).then(res => res.data);
};

export const runFanxiuVisualScript = (payload: FanxiuVisualScriptRunPayload) => {
  return api.post<FanxiuPseudoCodeRunResponse>('/fanxiu/game-window2/visual-script/run', payload, { timeout: 0 }).then(res => res.data);
};

export const stopFanxiuVisualScript = (payload: FanxiuVisualScriptStopPayload) => {
  return api.post<{ ok: boolean; stopped: boolean }>('/fanxiu/game-window2/visual-script/stop', payload).then(res => res.data);
};

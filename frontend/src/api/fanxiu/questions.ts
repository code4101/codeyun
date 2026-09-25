/** 灵泉答题的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuChoiceOption {
  text: string;
  aliases?: string[];
  position?: number | null;
  status: -1 | 0 | 1;
  source?: string;
  updated_at?: number;
}

export interface FanxiuLingquanQuestion {
  id: string;
  group_name: string;
  question: string;
  answer: string;
  options: FanxiuChoiceOption[];
  options_complete: boolean;
  interaction_mode: 'text_input' | 'choice_click';
  order_index: number;
  source: string;
  created_at: number;
  updated_at: number;
}

export interface FanxiuLingquanQuestionListResponse {
  items: FanxiuLingquanQuestion[];
  groups: Array<{ name: string; count: number }>;
  total: number;
}

export type FanxiuLingquanQuestionInput = Pick<FanxiuLingquanQuestion, 'group_name' | 'question' | 'answer' | 'order_index'>;

export function getFanxiuLingquanQuestions(params?: { query?: string; group_name?: string }) {
  return api.get<FanxiuLingquanQuestionListResponse>('/fanxiu/lingquan-questions', { params }).then(res => res.data);
}

export function createFanxiuLingquanQuestion(payload: FanxiuLingquanQuestionInput) {
  return api.post<FanxiuLingquanQuestion>('/fanxiu/lingquan-questions', payload).then(res => res.data);
}

export function updateFanxiuLingquanQuestion(id: string, payload: Partial<FanxiuLingquanQuestionInput>) {
  return api.put<FanxiuLingquanQuestion>(`/fanxiu/lingquan-questions/${encodeURIComponent(id)}`, payload).then(res => res.data);
}

export function deleteFanxiuLingquanQuestion(id: string) {
  return api.delete<{ ok: boolean }>(`/fanxiu/lingquan-questions/${encodeURIComponent(id)}`).then(res => res.data);
}

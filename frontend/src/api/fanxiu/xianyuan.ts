/** 仙缘图鉴的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuXianyuanReward {
  level: number;
  reward_key: number;
  item_id: number;
  name: string;
  count: number;
  kind: string;
  state: number;
  state_name: string;
  description: string;
  target_support_kind?: string;
  target_support_mode?: string;
  optional_items?: Array<{
    item_id: number;
    name: string;
    kind: string;
  }>;
  optional_item_count?: number;
  contains_wujing?: boolean;
}

export interface FanxiuXianyuanGiftOption {
  item_id: number;
  name: string;
  description: string;
  hobby_id: number;
  hobby_name: string;
  favorability: number;
  gift_type: number;
  activity_gift: boolean;
  career_conditional: boolean;
}

export interface FanxiuXianyuanHobbyGroup {
  hobby_id: number;
  name: string;
  description: string;
  item_count: number;
  activity_gift: boolean;
}

export interface FanxiuXianyuanPerson {
  npc_id: number;
  name: string;
  name_lang_id?: number | null;
  open_state: number;
  relation_type: '可送礼' | '敌对' | '已结识';
  hostile: boolean;
  giftable: boolean;
  can_send_config: boolean;
  no_gift_description: string;
  career_desc: number;
  cant_send_flower: boolean;
  gift_restriction: string;
  hobby_groups: FanxiuXianyuanHobbyGroup[];
  gift_options: FanxiuXianyuanGiftOption[];
  gift_option_count: number;
  activity_flower_gift_count: number;
  favor_level: number;
  favor: number;
  reset_favor_level: number;
  reset_favor: number;
  space_type: number;
  reward_count: number;
  book_reward_count: number;
  reward_kinds: string[];
  claimable_count: number;
  claimed_count: number;
  rewards: FanxiuXianyuanReward[];
  selectable_rewards: FanxiuXianyuanReward[];
  selectable_reward_count: number;
  wujing_selectable_reward_count: number;
  reset_steps: Array<{
    step: number;
    start_level: number;
    end_level: number;
    favor_cost: number;
  }>;
  target_rewards: FanxiuXianyuanReward[];
  target_reward_count: number;
  target_support_kinds: string[];
  target_next_level?: number | null;
  target_level_distance?: number | null;
  target_current_favor?: number | null;
  target_required_favor?: number | null;
  target_favor_gap?: number | null;
  target_cycle_end_level?: number | null;
  target_cycle_start_level?: number | null;
  target_cycle_favor_cost?: number | null;
  target_cycle_reward_count?: number;
  target_best_reset_step?: number | null;
  target_average_wujing_cost?: number | null;
  target_reset_options?: Array<{
    step: number;
    start_level: number;
    end_level: number;
    favor_cost: number;
    reward_count: number;
    average_wujing_cost: number;
    reward_levels: number[];
  }>;
  target_recommendation_rank?: number | null;
}

export interface FanxiuXianyuanTargetGongfa {
  book_id: number;
  name: string;
  quality_grade_name: string;
  filter_category: string;
  jie: number;
  max_jie: number;
  wujing: number;
  max_wujing: number;
  tongxuan: number;
  max_tongxuan: number;
  upgrade_index: number;
}

export interface FanxiuXianyuanRecommendation {
  npc_id: number;
  name: string;
  next_level: number;
  level_distance: number;
  favor_gap: number;
  cycle_favor_cost: number;
  average_wujing_cost: number;
  reward_count: number;
  support_kinds: string[];
}

export interface FanxiuXianyuanAtlasSnapshot {
  people: FanxiuXianyuanPerson[];
  runtime_complete: boolean;
  runtime_error: string;
  runtime_updated_at: number;
  runtime_item_count: number;
  summary: Record<string, number>;
  runtime_debug: Record<string, unknown>;
  target_gongfa?: FanxiuXianyuanTargetGongfa | null;
  recommendation?: FanxiuXianyuanRecommendation | null;
}

export const getFanxiuXianyuanAtlas = () => (
  api.get<FanxiuXianyuanAtlasSnapshot>('/fanxiu/inventory/xianyuan-atlas').then(res => res.data)
);

export const collectFanxiuXianyuanAtlas = () => (
  api.post<FanxiuXianyuanAtlasSnapshot>('/fanxiu/inventory/xianyuan-atlas/collect', null, {
    timeout: 190_000,
  }).then(res => res.data)
);

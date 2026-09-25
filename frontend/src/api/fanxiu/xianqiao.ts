/** 仙桥机制目录的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuXianqiaoGradeCheckpoint {
  grade: number;
  level: number;
  cumulative_exp: number;
}

export interface FanxiuXianqiaoPart {
  id: number;
  name: string;
  unlock_text: string;
  max_level: number;
  total_exp: number;
  grade_checkpoints: FanxiuXianqiaoGradeCheckpoint[];
  core_attributes: Record<string, number>;
  ware_main_attribute: {
    key: string;
    name: string;
    initial_text: string;
    max_text: string;
  };
}

export interface FanxiuXianqiaoElement {
  id: number;
  name: string;
  summary: string;
  purpose: string;
  levels: Array<{
    level: number;
    required_count: number;
    effect: string;
  }>;
}

export interface FanxiuXianqiaoSystem {
  id: number;
  name: string;
  unlock_condition: string;
  unlock_text: string;
  parts: FanxiuXianqiaoPart[];
  elements: FanxiuXianqiaoElement[];
}

export interface FanxiuXianqiaoMechanics {
  systems: FanxiuXianqiaoSystem[];
  qualities: Array<{
    quality: number;
    name: string;
    max_level: number;
    element_slots: number;
    initial_element_slots: number;
    element_unlock_levels: number[];
    initial_side_attributes: number;
    side_attribute_unlock_levels: number[];
    base_feed_exp: number;
    invested_exp_return_rate: number;
    total_upgrade_exp: number;
  }>;
  trial: {
    daily_reward_times: number;
    extra_time_cost: number;
    extra_time_item_id: number;
    weekly_level_points: number[];
    default_weekly_points: number;
    modes: Array<{
      id: number;
      system_id: number;
      group: string;
      enemy: string;
      unlock_text: string;
      difficulty_min: number | null;
      difficulty_max: number | null;
      reward_tier_count: number;
    }>;
    buffs: Array<{
      id: number;
      kind: string;
      selection: string;
      description: string;
      max_level: number;
      max_point: number;
    }>;
  };
  rules: {
    part_count_per_system: number;
    core_max_level: number;
    core_grade_interval: number;
    element_level_thresholds: number[];
    feed_exp_return_rate: number;
    attribute_display_multiplier: number;
    bag_limit: number;
  };
}

export const getFanxiuXianqiaoMechanics = () => (
  api
    .get<FanxiuXianqiaoMechanics>('/fanxiu/resources/xianqiao/mechanics')
    .then(res => res.data)
);

/** 活动静态规则与奖励目录的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';
import type { FanxiuGongfaLinkedItem } from './gongfa';
import type { FanxiuFacetIndex, FanxiuTimelineHint } from './catalogCommon';


export interface FanxiuActivityStats {
  activity_count?: number;
  activity_gift_count?: number;
  activity_free_gift_count?: number;
  activity_signin_count?: number;
  activity_list_reward_count?: number;
  activity_fund_count?: number;
  activity_battle_pass_count?: number;
  activity_loop_count?: number;
  activity_boss_count?: number;
  activity_challenge_reward_count?: number;
  active_task_count?: number;
  open_function_count?: number;
  subpackage_reward_count?: number;
  catalog_card_count?: number;
  current_card_count?: number;
  stale_card_count?: number;
  activity_with_time_hint_count?: number;
  activity_with_reward_count?: number;
  activity_with_challenge_reward_count?: number;
  activity_with_loop_count?: number;
  activity_with_jump_target_count?: number;
  activity_kind_count?: number;
  activity_type_count?: number;
  time_kind_count?: number;
}

export interface FanxiuActivityOption {
  value: string;
  label: string;
  count: number;
  activity_type?: string | number;
}

export interface FanxiuActivityRewardRow {
  source?: string;
  row_key?: string | number;
  title?: string;
  meta?: string;
  source_activity_id?: string | number;
  rank_range?: string;
  rank_start?: string | number;
  rank_end?: string | number;
  rank_gatekeeper?: {
    activity_id?: string | number;
    rank?: string | number;
    index?: string | number;
    name?: string;
    server_id?: string | number;
    server_name?: string;
    subject?: string;
    progress?: string;
    score?: string | number;
    ext_score?: string | number;
    ext_score2?: string | number;
    group?: string | number;
    rank_list_size?: string | number;
    rank_vo_type?: string;
    source_path?: string;
    captured_at?: string;
    text?: string;
  };
  costs?: string[];
  reward_items?: FanxiuGongfaLinkedItem[];
  raw_rewards?: string[];
  condition?: string;
  server_day_start?: string | number;
  server_day_end?: string | number;
  world_level_start?: string | number;
  world_level_end?: string | number;
}

export interface FanxiuActivityRewardSection {
  key: string;
  title: string;
  count: number;
  rows: FanxiuActivityRewardRow[];
  rank_self?: {
    activity_id?: string | number;
    rank?: string | number;
    index?: string | number;
    name?: string;
    server_name?: string;
    subject?: string;
    progress?: string;
    text?: string;
    current_tier?: string;
    next_tier?: string;
    current_gatekeeper_rank?: string | number;
    next_gatekeeper_rank?: string | number;
    current_gatekeeper?: FanxiuActivityRewardRow['rank_gatekeeper'];
    next_gatekeeper?: FanxiuActivityRewardRow['rank_gatekeeper'];
  };
}

export interface FanxiuActivityChallengeLevel {
  level_id?: string | number;
  name?: string;
  stage?: string | number;
  layer?: string | number;
  sub_layer?: string | number;
  reward_title?: string;
  clear_rewards?: FanxiuGongfaLinkedItem[];
  find_rewards?: FanxiuGongfaLinkedItem[];
  clear_reward_text?: string;
  find_reward_text?: string;
  activity_ids?: Array<string | number>;
  source_level_id?: string | number;
}

export interface FanxiuActivityChallengeRarityStat {
  rarity_rank: number;
  item_id: string | number;
  item_name: string;
  icon?: string;
  quality?: string | number;
  total_count: string | number;
  level_count: string | number;
  level_ids?: Array<string | number>;
  level_range_text?: string;
  first_level_id?: string | number;
  first_reward_kind?: string;
}

export interface FanxiuActivityChallengeSection {
  key: string;
  title: string;
  source?: string;
  display_mode?: string;
  level_count?: number;
  reward_item_count?: number;
  stage_summary?: Array<{ stage?: string | number; level_count?: number }>;
  rarity_stats?: FanxiuActivityChallengeRarityStat[];
  default_threshold_rank?: string | number;
  default_threshold_item_id?: string | number;
  levels: FanxiuActivityChallengeLevel[];
}

export interface FanxiuActivityLoopEntry {
  loop_id?: string | number;
  day?: string | number;
  activity_id?: string | number;
  activity_name?: string;
}

export interface FanxiuActivityJumpTarget {
  id?: string | number;
  name?: string;
  description?: string;
  condition?: unknown;
  unlock?: string;
  lua_path?: string;
  window_id?: string | number;
  icon?: string;
}

export interface FanxiuActivityParsedTimeItem {
  kind?: string;
  token?: string;
  raw?: string;
  date?: string;
  time?: string;
  day?: string | number;
  time_code?: string;
  text?: string;
}

export interface FanxiuActivityParsedTimeField {
  field: string;
  label: string;
  raw?: string;
  summary?: string;
  items?: FanxiuActivityParsedTimeItem[];
}

export interface FanxiuActivityParsedConditionItem {
  token?: string;
  label?: string;
  value?: string;
  raw?: string;
  date?: string;
  dates?: string[];
  text?: string;
}

export interface FanxiuActivityParsedConditionGroup {
  join?: string;
  summary?: string;
  items?: FanxiuActivityParsedConditionItem[];
}

export interface FanxiuActivityParsedConditionField {
  field: string;
  label: string;
  raw?: string;
  summary?: string;
  raw_summary?: string;
  description?: string;
  code_summary?: string;
  groups?: FanxiuActivityParsedConditionGroup[];
}

export interface FanxiuActivityCard {
  id: string | number;
  name: string;
  little_name?: string;
  title_name?: string;
  activity_type?: string | number;
  base_id?: string | number;
  group_id?: string | number;
  parent_activity_id?: string | number;
  sub_type?: string | number;
  reward_group?: string | number;
  icon?: string;
  sort?: string | number;
  mainui_pos?: string | number;
  jump?: string | number;
  prepare_time?: unknown;
  start_time?: unknown;
  end_time?: unknown;
  reward_time?: unknown;
  close_panel_time?: unknown;
  open_condition?: unknown;
  join_condition?: unknown;
  show_condition?: unknown;
  force_hide_condition?: unknown;
  join_condition_description?: string;
  description?: string;
  time_fields?: FanxiuActivityParsedTimeField[];
  condition_fields?: FanxiuActivityParsedConditionField[];
  kind_keys?: string[];
  kind_names?: string[];
  time_kind?: string;
  time_kind_name?: string;
  time_hints?: FanxiuTimelineHint[];
  first_time_hint?: FanxiuTimelineHint | null;
  reward_sections?: FanxiuActivityRewardSection[];
  challenge_sections?: FanxiuActivityChallengeSection[];
  reward_preview?: string;
  loop_entries?: FanxiuActivityLoopEntry[];
  jump_target?: FanxiuActivityJumpTarget | null;
  source_row_key?: string | number;
  source_table?: string;
  presence_status?: string;
  is_stale?: boolean;
  last_seen_at?: string;
  missing_since?: string;
  terms?: string[];
}

export interface FanxiuActivitySearchItem {
  id: string | number;
  name: string;
  little_name?: string;
  title_name?: string;
  activity_type?: string | number;
  base_id?: string | number;
  icon?: string;
  kind_keys?: string[];
  kind_names?: string[];
  time_kind?: string;
  time_kind_name?: string;
  description_preview?: string;
  reward_preview?: string;
  time_hints?: FanxiuTimelineHint[];
  schedule_time_hints?: unknown[];
  first_time_hint?: FanxiuTimelineHint | null;
  loop_entries?: FanxiuActivityLoopEntry[];
  source_table?: string;
  presence_status?: string;
  is_stale?: boolean;
  last_seen_at?: string;
  missing_since?: string;
  terms?: string[];
  score?: number;
}

export interface FanxiuActivitySearchResponse {
  query: string;
  kind_key?: string;
  time_kind?: string;
  activity_type?: string;
  server_scope?: string;
  sort_by?: string;
  sort_order?: string;
  limit: number;
  offset: number;
  total: number;
  stats: FanxiuActivityStats;
  catalog_path: string;
  kind_options?: FanxiuActivityOption[];
  time_options?: FanxiuActivityOption[];
  activity_type_options?: FanxiuActivityOption[];
  facet_index?: FanxiuFacetIndex;
  items: FanxiuActivitySearchItem[];
}

export interface FanxiuActivityCardResponse {
  catalog_path: string;
  card: FanxiuActivityCard;
}

export const searchFanxiuActivityCards = (params: {
  query?: string;
  kind_key?: string;
  time_kind?: string;
  activity_type?: string;
  server_scope?: string;
  sort_by?: string;
  sort_order?: string;
  limit?: number;
  offset?: number;
  item_view?: 'default' | 'schedule';
  include_facets?: boolean;
} = {}) => {
  return api.get<FanxiuActivitySearchResponse>('/fanxiu/resources/activities/cards', { params }).then(res => res.data);
};

export const getFanxiuActivityCard = (activityId: string | number, params: { server_scope?: string } = {}) => {
  return api
    .get<FanxiuActivityCardResponse>('/fanxiu/resources/activities/card', { params: { activity_id: activityId, ...params } })
    .then(res => res.data);
};

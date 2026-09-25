/** 斗破玩法目录的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuDoupoTDAttrEntry {
  key: string;
  label: string;
  value?: string | number;
  formatted?: string;
  text: string;
  sort?: number;
}

export interface FanxiuDoupoTDLinkedItem {
  id?: string | number;
  name?: string;
  icon?: string;
  small_icon?: string;
  description?: string;
  description_rich?: string;
  quality_name?: string;
}

export interface FanxiuDoupoTDComposeCard {
  id: string | number;
  char_id?: string | number;
  partner_name?: string;
  name?: string;
  quality?: string | number;
  quality_name?: string;
  star?: string | number;
  title: string;
  show_item?: FanxiuDoupoTDLinkedItem | null;
  attrs?: FanxiuDoupoTDAttrEntry[];
  attr_text?: string;
}

export interface FanxiuDoupoTDRewardItem {
  type?: string;
  id?: string | number;
  count?: string | number;
  extra_mark?: string | number;
  item?: FanxiuDoupoTDLinkedItem | null;
  raw?: string;
  text?: string;
}

export interface FanxiuDoupoTDWeightedCardEntry {
  card_id?: string | number;
  title?: string;
  partner_id?: string | number;
  partner_name?: string;
  quality_name?: string;
  star?: string | number | null;
  weight?: string | number;
  chance_text?: string;
}

export interface FanxiuDoupoTDDrawSource {
  id?: string | number;
  sort?: string | number;
  item_id?: string | number;
  item?: FanxiuDoupoTDLinkedItem | null;
  total_weight?: string | number;
  entries?: FanxiuDoupoTDWeightedCardEntry[];
  rewards?: FanxiuDoupoTDRewardItem[];
}

export interface FanxiuDoupoTDComposeQualitySource {
  id?: string | number;
  quality?: string | number;
  quality_name?: string;
  total_weight?: string | number;
  entries?: FanxiuDoupoTDWeightedCardEntry[];
}

export interface FanxiuDoupoTDComposeProgressReward {
  id?: string | number;
  progress?: string | number;
  rewards?: FanxiuDoupoTDRewardItem[];
}

export interface FanxiuDoupoTDComposeBookEntry {
  id?: string | number;
  quality?: string | number;
  quality_name?: string;
  sort?: string | number;
  card_id?: string | number;
  title?: string;
  partner_id?: string | number;
  partner_name?: string;
}

export interface FanxiuDoupoTDSkill {
  id?: string | number;
  skill_type?: string | number;
  skill_title?: string;
  skill_title_rich?: string;
  skill_patch?: string;
  skill_icon?: string;
  skill_name?: string;
  skill_description?: string;
  skill_description_rich?: string;
}

export interface FanxiuDoupoTDBuffFlowFunction {
  name?: string;
  categories?: string[];
  calls?: string[];
  adds_buff?: boolean;
  removes_buff?: boolean;
  uses_random_gate?: boolean;
  uses_skill_filter?: boolean;
  uses_target_buff_check?: boolean;
  uses_friend_target_expansion?: boolean;
}

export interface FanxiuDoupoTDBuffFlowRuntime {
  hint?: string;
  categories?: string[];
  function_count?: number;
  flow_step_count?: number;
  key_functions?: FanxiuDoupoTDBuffFlowFunction[];
}

export interface FanxiuDoupoTDBuffRuntime {
  id?: string | number;
  source_kind?: string;
  found?: boolean;
  type?: string | number;
  type_name?: string;
  buff_class?: string;
  target_type?: string | number;
  target_type_name?: string;
  trigger_type?: string;
  layer_type?: string | number;
  layer_type_name?: string;
  duration?: string | number;
  interval?: string | number;
  damage?: string | number;
  add_attr?: string;
  timeline_id?: string | number;
  trigger_buff_ids?: Array<string | number>;
  kill_add_buff_ids?: Array<string | number>;
  buff_end_skill_ids?: Array<string | number>;
  semantic_flags?: string[];
  flow?: FanxiuDoupoTDBuffFlowRuntime;
}

export interface FanxiuDoupoTDSkillRuntime {
  timeline_ids?: Array<string | number>;
  buff_ids?: Array<string | number>;
  secondary_buff_ids?: Array<string | number>;
  buffs?: FanxiuDoupoTDBuffRuntime[];
}

export interface FanxiuDoupoTDLogicSkill {
  id?: string | number;
  skillType?: string | number;
  level?: string | number;
  baseSkill?: string | number;
  timeLineId?: string | number;
  pvpTimeLineId?: string | number;
  damage?: string | number;
  cd?: string | number;
  duration?: string | number;
  interval?: string | number;
  range?: string | number;
  atkRange?: string | number;
  buffId?: string | number | Array<string | number>;
  extSkill?: string | number;
  bulletCount?: string | number;
  bulletSpeed?: string | number;
  bulletDuration?: string | number;
  maxHit?: string | number;
  runtime?: FanxiuDoupoTDSkillRuntime;
}

export interface FanxiuDoupoTDSkillStrength {
  id?: string | number;
  quality_name?: string;
  level?: string | number;
  unlock_description?: string;
  skill_patch?: string;
  skill_icon?: string;
  skill_name?: string;
  skill_description?: string;
  skill_description_rich?: string;
}

export interface FanxiuDoupoTDLevelSummary {
  level_count?: number;
  min_level?: string | number;
  max_level?: string | number;
  level1_attrs?: Record<string, string | number>;
  max_level_attrs?: Record<string, string | number>;
  default_skill?: Array<string | number>;
  default_skill_enhance?: Array<string | number>;
}

export interface FanxiuDoupoTDPartnerCard {
  id: string | number;
  name: string;
  different?: string;
  position_type?: string | number;
  career_type?: string | number;
  positioning?: string;
  model?: string | number;
  quality?: string | number;
  icon?: string;
  big_icon?: string;
  head_icon?: string;
  skill_icon?: string;
  skill_name?: string;
  skill_description?: string;
  skill_description_rich?: string;
  skill_group?: string | number;
  unlock_level?: string | number;
  unlock_level1?: string | number;
  unlock_condition?: string;
  unlock_description?: string;
  unlock_description1?: string;
  sort?: string | number;
  can_battle?: string | number;
  damage_proportion?: string | number;
  change_ration?: string | number;
  light_icon?: string;
  draw_effect?: string;
  skills?: FanxiuDoupoTDSkill[];
  logic_skills?: FanxiuDoupoTDLogicSkill[];
  strengths?: FanxiuDoupoTDSkillStrength[];
  level_summary?: FanxiuDoupoTDLevelSummary;
  compose_cards?: FanxiuDoupoTDComposeCard[];
  draw_sources?: FanxiuDoupoTDDrawSource[];
  compose_quality_sources?: FanxiuDoupoTDComposeQualitySource[];
  compose_progress_rewards?: FanxiuDoupoTDComposeProgressReward[];
  compose_book_entries?: FanxiuDoupoTDComposeBookEntry[];
  compose_card_count?: number;
  skill_count?: number;
  strength_count?: number;
  terms?: string[];
}

export interface FanxiuDoupoTDPartnerSearchItem {
  id: string | number;
  name: string;
  icon?: string;
  head_icon?: string;
  big_icon?: string;
  positioning?: string;
  career_type?: string | number;
  position_type?: string | number;
  skill_name?: string;
  skill_description_preview?: string;
  compose_card_count?: number;
  skill_count?: number;
  strength_count?: number;
  terms?: string[];
  score?: number;
}

export interface FanxiuDoupoTDStats {
  partner_count?: number;
  compose_card_count?: number;
  skill_show_count?: number;
  skill_logic_count?: number;
  strength_count?: number;
  level_row_count?: number;
  draw_card_count?: number;
  compose_progress_count?: number;
  compose_book_count?: number;
  quality_count?: number;
}

export interface FanxiuDoupoTDPartnerSearchResponse {
  query: string;
  limit: number;
  offset: number;
  total: number;
  catalog_path?: string;
  stats: FanxiuDoupoTDStats;
  items: FanxiuDoupoTDPartnerSearchItem[];
}

export interface FanxiuDoupoTDPartnerCardResponse {
  catalog_path: string;
  card: FanxiuDoupoTDPartnerCard;
}

export interface FanxiuDoupoTDRewardResultResolution {
  runtime_reward_type?: string | number;
  runtime_reward_type_name?: string;
  code?: string | number;
  amount?: string | number;
  extra_mark?: string | number;
  extra_mark_name?: string;
  extra_mark_show_type?: string | number;
  extra_mark_eff_name?: string;
  resolution_rule?: string;
  note?: string;
}

export interface FanxiuDoupoTDRewardConfigRewardItem {
  source_table?: string;
  config_id?: string | number;
  different?: string | number;
  stage?: string | number;
  layer?: string | number;
  sub_layer?: string | number;
  reward_index?: string | number;
  reward_type?: string;
  item_id?: string | number;
  item_name?: string;
  quality_name?: string;
  count?: string | number;
  extra_mark?: string | number;
  text?: string;
  raw?: string;
  reward_title?: string;
  reward_result?: FanxiuDoupoTDRewardResultResolution;
}

export interface FanxiuDoupoTDRewardConfigSearchItem {
  source_table: string;
  config_id: string | number;
  different?: string | number;
  stage?: string | number;
  layer?: string | number;
  sub_layer?: string | number;
  show_pos_id?: string | number;
  name?: string;
  reward_title?: string;
  show_img?: string | number;
  reward_field?: string;
  reward_count?: string | number;
  reward_item_ids?: string;
  reward_items?: string;
  raw_rewards?: string;
  items?: FanxiuDoupoTDRewardConfigRewardItem[];
}

export interface FanxiuDoupoTDRewardConfigStats {
  level_config_count?: number;
  level_reward_row_count?: number;
  prelevel_config_count?: number;
  prelevel_reward_row_count?: number;
  reward_item_row_count?: number;
  unique_reward_item_count?: number;
  monster_group_count?: number;
  monster_drop_group_ref_count?: number;
  evidence_row_count?: number;
}

export interface FanxiuDoupoTDRewardConfigSearchResponse {
  source?: Record<string, string>;
  stats: FanxiuDoupoTDRewardConfigStats;
  total: number;
  items: FanxiuDoupoTDRewardConfigSearchItem[];
}

export interface FanxiuDoupoTDRewardConfigResponse {
  source?: Record<string, string>;
  stats: FanxiuDoupoTDRewardConfigStats;
  item: FanxiuDoupoTDRewardConfigSearchItem;
}

export const searchFanxiuDoupoTDPartnerCards = (params: {
  query?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuDoupoTDPartnerSearchResponse>('/fanxiu/resources/doupotd/partner-cards', { params })
    .then(res => res.data);
};

export const getFanxiuDoupoTDPartnerCard = (partnerId: string | number) => {
  return api
    .get<FanxiuDoupoTDPartnerCardResponse>('/fanxiu/resources/doupotd/partner-card', { params: { partner_id: partnerId } })
    .then(res => res.data);
};

export const searchFanxiuDoupoTDRewardConfigs = (params: {
  query?: string;
  source_table?: string;
  stage?: string;
  item_id?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuDoupoTDRewardConfigSearchResponse>('/fanxiu/resources/doupotd/reward-configs', { params })
    .then(res => res.data);
};

export const getFanxiuDoupoTDRewardConfig = (sourceTable: string, configId: string | number) => {
  return api
    .get<FanxiuDoupoTDRewardConfigResponse>('/fanxiu/resources/doupotd/reward-config', {
      params: { source_table: sourceTable, config_id: configId },
    })
    .then(res => res.data);
};

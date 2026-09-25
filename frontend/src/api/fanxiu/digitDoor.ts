/** 数字门玩法目录的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';
import type { FanxiuDoupoTDRewardResultResolution } from './doupo';


export interface FanxiuDigitDoorBuffRuntime {
  id?: string | number;
  type?: string | number;
  target_type?: string | number;
  trigger_type?: string;
  duration?: string | number;
  interval?: string | number | null;
  eff_type?: string | number;
  damage_raw?: string | number | null;
  damage_text?: string;
  add_attr?: string | null;
  shield?: string | number | null;
  slow_down?: string | number | null;
  timeline_id?: string | number | null;
}

export interface FanxiuDigitDoorSkillRuntime {
  skill_type?: string | number;
  skill_group?: string | number;
  timeline_id?: string | number;
  pvp_timeline_id?: string | number;
  cd_ms?: string | number;
  damage_raw?: string | number;
  damage_text?: string;
  duration_ms?: string | number;
  range?: string | number;
  buff_ids?: Array<string | number>;
  buffs?: FanxiuDigitDoorBuffRuntime[];
}

export interface FanxiuDigitDoorSkill {
  id?: string | number;
  partner_id?: string | number;
  belong_id?: string | number;
  base_skill?: string | number | null;
  level_show?: string | number;
  skill_title?: string;
  skill_title_plain?: string;
  skill_name?: string;
  skill_description?: string;
  skill_description_plain?: string;
  skill_icon?: string;
  skill_patch?: string;
  show_condition?: string;
  runtime?: FanxiuDigitDoorSkillRuntime;
}

export interface FanxiuDigitDoorLogicSkill {
  id?: string | number;
  char_id?: string | number;
  skill_type?: string | number;
  skill_group?: string | number;
  level?: string | number;
  timeline_id?: string | number;
  pvp_timeline_id?: string | number;
  cd_ms?: string | number;
  damage_raw?: string | number;
  damage_text?: string;
  duration_ms?: string | number;
  range?: string | number;
  bullet_count?: string | number | null;
  hit_num?: string | number | null;
  buff_ids?: Array<string | number>;
  buffs?: FanxiuDigitDoorBuffRuntime[];
}

export interface FanxiuDigitDoorSkillEnhanceEffect {
  id?: string | number;
  char_id?: string | number;
  skill?: string | number;
  skill_type?: string | number;
  buff_id?: string | number | null;
  buff?: FanxiuDigitDoorBuffRuntime | null;
  ext_release_count?: string | number | null;
  ext_hit_num?: string | number | null;
  ext_penetrate?: string | number | null;
  ext_atk_distance?: string | number | null;
  mutex_timeline?: string | number | null;
}

export interface FanxiuDigitDoorDoorEffect {
  id?: string | number;
  char_id?: string | number;
  customized_type?: string | number;
  door_type?: string | number;
  door_type_label?: string;
  door_effect?: string;
  effect_show?: string;
  effect_show_plain?: string;
  show_tips?: string;
  show_tips_plain?: string;
  refresh_weights?: string | number;
  put_back?: string | number;
  skill_ids?: Array<string | number>;
  skills?: FanxiuDigitDoorSkill[];
}

export interface FanxiuDigitDoorLevelMilestone {
  level?: string | number;
  attrs?: Record<string, string | number>;
  default_skill?: Array<string | number>;
  default_skill_enhance?: Array<string | number>;
}

export interface FanxiuDigitDoorCharacterCard {
  id: string | number;
  name: string;
  icon?: string;
  head_icon?: string;
  head_icon_alt?: string;
  big_icon?: string;
  bg_icon?: string;
  quality?: string | number;
  quality_label?: string;
  positioning?: string;
  position_type?: string | number;
  career_type?: string | number;
  skill_icon?: string;
  skill_name?: string;
  skill_description?: string;
  skill_description_plain?: string;
  unlock_level?: string | number;
  sort?: string | number;
  model?: string | number;
  can_battle?: string | number;
  min_level?: string | number;
  max_level?: string | number;
  level_count?: number;
  level_milestones?: FanxiuDigitDoorLevelMilestone[];
  skill_count?: number;
  logic_skill_count?: number;
  skill_enhance_effect_count?: number;
  door_effect_count?: number;
  skills?: FanxiuDigitDoorSkill[];
  logic_skills?: FanxiuDigitDoorLogicSkill[];
  skill_enhance_effects?: FanxiuDigitDoorSkillEnhanceEffect[];
  door_effects?: FanxiuDigitDoorDoorEffect[];
  terms?: string[];
}

export interface FanxiuDigitDoorCharacterSearchItem {
  id: string | number;
  name: string;
  icon?: string;
  head_icon?: string;
  big_icon?: string;
  positioning?: string;
  quality?: string | number;
  quality_label?: string;
  skill_name?: string;
  skill_description_preview?: string;
  skill_count?: number;
  logic_skill_count?: number;
  skill_enhance_effect_count?: number;
  enhance_count?: number;
  door_effect_count?: number;
  terms?: string[];
  score?: number;
}

export interface FanxiuDigitDoorEnhanceRef {
  id?: string | number;
  name?: string;
  description?: string;
  description_plain?: string;
  char_id?: string | number;
  type?: string | number;
  type_label?: string;
  quality?: string | number;
  quality_label?: string;
}

export interface FanxiuDigitDoorEnhanceLevelRange {
  char_id?: string | number;
  min_level?: string | number;
  max_level?: string | number;
}

export interface FanxiuDigitDoorEnhance {
  id?: string | number;
  char_id?: string | number;
  name?: string;
  type?: string | number;
  type_label?: string;
  quality?: string | number;
  quality_label?: string;
  description?: string;
  description_plain?: string;
  effect_id?: string | number;
  limit?: string | number;
  weight?: string | number;
  condition_raw?: string;
  conditions?: unknown[];
  prereq_ids?: Array<string | number>;
  prereqs?: FanxiuDigitDoorEnhanceRef[];
  mutex_ids?: Array<string | number>;
  mutexes?: FanxiuDigitDoorEnhanceRef[];
  level_ranges?: FanxiuDigitDoorEnhanceLevelRange[];
  unlock_show_ids?: Array<string | number>;
  unlock_show?: FanxiuDigitDoorEnhanceRef[];
}

export interface FanxiuDigitDoorEnhanceGroup {
  char_id?: string | number;
  name?: string;
  description?: string;
  description_plain?: string;
  enhance_count?: number;
  enhances?: FanxiuDigitDoorEnhance[];
}

export interface FanxiuDigitDoorEnhanceGroupSearchItem {
  id?: string | number;
  char_id?: string | number;
  name?: string;
  description_preview?: string;
  enhance_count?: number;
  condition_count?: number;
  prereq_count?: number;
  mutex_count?: number;
  level_range_count?: number;
  enhance_preview?: string;
  score?: number;
}

export interface FanxiuDigitDoorEnhanceGroupSearchResponse {
  query: string;
  limit: number;
  offset: number;
  total: number;
  catalog_path?: string;
  stats: FanxiuDigitDoorStats;
  items: FanxiuDigitDoorEnhanceGroupSearchItem[];
}

export interface FanxiuDigitDoorEnhanceGroupResponse {
  catalog_path: string;
  group: FanxiuDigitDoorEnhanceGroup;
}

export interface FanxiuDigitDoorStats {
  character_count?: number;
  level_row_count?: number;
  skill_show_count?: number;
  skill_logic_count?: number;
  skill_enhance_effect_count?: number;
  enhance_count?: number;
  door_effect_count?: number;
  buff_count?: number;
  level_config_count?: number;
  door_refresh_count?: number;
  stage_count?: number;
  pre_level_reward_count?: number;
  skill_enhance_group_count?: number;
  door_skill_ref_count?: number;
  door_skill_ref_unique_count?: number;
}

export interface FanxiuDigitDoorStageOption {
  id?: string | number;
  name?: string;
  reward_count?: number;
  level_count?: number;
}

export interface FanxiuDigitDoorRewardLinkedItem {
  id?: string | number;
  name?: string;
  icon?: string;
  small_icon?: string;
  quality_name?: string;
  description?: string;
}

export interface FanxiuDigitDoorRewardItem {
  type?: string;
  id?: string | number;
  count?: string | number;
  extra_mark?: string | number | null;
  item?: FanxiuDigitDoorRewardLinkedItem | null;
  raw?: string;
  text?: string;
  reward_result?: FanxiuDoupoTDRewardResultResolution;
}

export interface FanxiuDigitDoorMonsterRefreshSummary {
  level?: string | number;
  name?: string;
  stage?: string | number;
  layer?: string | number;
  sub_layer?: string | number;
  declared_monster_ids?: Array<string | number>;
  declared_monster_names?: string[];
  declared_monster_unresolved_ids?: Array<string | number>;
  refresh_point_count?: string | number;
  wave_count?: string | number;
  first_wave?: string | number;
  last_wave?: string | number;
  refresh_monster_ids?: Array<string | number>;
  refresh_monster_count?: string | number;
  max_attack?: string | number;
  max_hp?: string | number;
  confirmed?: boolean;
  report_path?: string;
}

export interface FanxiuDigitDoorMonsterRefreshPoint {
  id?: string | number;
  level?: string | number;
  refresh_wave?: string | number;
  game_type?: string | number;
  object_type?: string | number;
  monster_id?: string | number;
  monster_name?: string;
  base_id?: string | number;
  monster_type?: string | number;
  attack?: string | number;
  hp?: string | number;
  critical?: string | number;
  anti_critical?: string | number;
  atk_speed?: string | number;
  increase_damage?: string | number;
  reduce_damage?: string | number;
  kill_exp?: string | number;
  wave_time?: string | number;
  refresh_total_num?: string | number;
  refresh_time?: string | number;
  refresh_num?: string | number;
  refresh_offset_dis?: string | number;
  refresh_type?: string | number;
  refresh_pos?: string | number;
  next_wave_condition?: string;
  default_skill_ids?: string;
  unresolved_skill_ids?: string;
  value_projections?: FanxiuDigitDoorMonsterRefreshPointValueProjection[];
  attribute_projections?: FanxiuDigitDoorMonsterRefreshPointAttributeProjection[];
}

export interface FanxiuDigitDoorMonsterRefreshPointValueProjection {
  field?: string;
  raw_value?: string | number;
  projection?: string;
  formula?: string;
  meaning?: string;
  runtime_slot?: string;
}

export interface FanxiuDigitDoorMonsterRefreshPointAttributeProjection {
  field?: string;
  raw_value?: string | number;
  projection?: string;
  formula?: string;
  meaning?: string;
  runtime_slot?: string;
}

export interface FanxiuDigitDoorMonsterSkill {
  id?: string | number;
  type?: string | number;
  type_name?: string;
  trigger?: string | number;
  trigger_name?: string;
  timeline_id?: string | number;
  cd?: string | number;
  damage?: string | number;
  buff_id?: string | number;
  release_count?: string | number;
  duration?: string | number;
  hit_time?: string | number;
  distance?: string | number;
  hp_limit?: string | number;
  summon_monster_id?: string | number;
  summon_hp?: string | number;
  summon_attack?: string | number;
  runtime_hint?: string;
  value_projections?: FanxiuDigitDoorMonsterSkillValueProjection[];
  timeline_effect?: FanxiuDigitDoorMonsterSkillTimelineEffect | null;
  buff_effects?: FanxiuDigitDoorMonsterSkillBuffEffect[];
}

export interface FanxiuDigitDoorMonsterSkillValueProjection {
  field?: string;
  raw_value?: string | number;
  projection?: string;
  formula?: string;
  meaning?: string;
  runtime_slot?: string;
}

export interface FanxiuDigitDoorMonsterSkillTimelineEffect {
  skill_id?: string | number;
  timeline_id?: string | number;
  missing_timeline_id?: string | number;
  sections?: string[];
  effect_classes?: string[];
  effect_class_count?: string | number;
  timeline_files?: string[];
  class_flows?: FanxiuDigitDoorMonsterEffectClassFlow[];
  skill_data_accessors?: FanxiuDigitDoorMonsterSkillDataAccessor[];
}

export interface FanxiuDigitDoorMonsterEffectClassFlow {
  class_name?: string;
  source_file?: string;
  function_count?: string | number;
  flow_step_count?: string | number;
  flow_categories?: string[];
  flow_labels?: string[];
  flow_hint?: string;
}

export interface FanxiuDigitDoorMonsterSkillDataAccessor {
  class_name?: string;
  function?: string;
  accessor?: string;
  config_field?: string;
  source_data_class?: string;
  transform?: string;
}

export interface FanxiuDigitDoorMonsterSkillBuffEffect {
  skill_id?: string | number;
  buff_id?: string | number;
  buff_type?: string | number;
  buff_type_name?: string;
  buff_path?: string;
  target_type?: string | number;
  target_type_name?: string;
  trigger_type?: string;
  trigger_type_name?: string;
  duration?: string | number;
  interval?: string | number;
  eff_type?: string | number;
  plies_limit?: string | number;
  damage?: string | number;
  add_attr?: string;
  shield?: string | number;
  slow_down?: string | number;
  passive?: string | number | boolean;
  buff_timeline_id?: string | number;
  runtime_hint?: string;
  formula_projections?: FanxiuDigitDoorMonsterSkillBuffFormula[];
}

export interface FanxiuDigitDoorMonsterSkillBuffFormula {
  field?: string;
  raw_value?: string | number;
  projection?: string;
  formula?: string;
  meaning?: string;
  runtime_slot?: string;
}

export interface FanxiuDigitDoorMonsterRefreshMonster {
  monster_id?: string | number;
  name?: string;
  text_name?: string;
  base_id?: string | number;
  info_name?: string;
  type?: string | number;
  info_type?: string | number;
  model_id?: string | number;
  speed?: string | number;
  move_stop_distance?: string | number;
  default_skill_ids?: string;
  default_skill_count?: string | number;
  unresolved_skill_ids?: string;
  restrained_count?: string | number;
  drops?: string | number;
  weight?: string | number;
  reduce_damage?: string | number;
  evasion?: string | number;
  repel?: string | number;
  description?: string;
  unlock_level?: string | number;
  sort?: string | number;
  default_skills?: FanxiuDigitDoorMonsterSkill[];
}

export interface FanxiuDigitDoorMonsterRefreshDetail {
  summary?: FanxiuDigitDoorMonsterRefreshSummary;
  points?: FanxiuDigitDoorMonsterRefreshPoint[];
  monsters?: FanxiuDigitDoorMonsterRefreshMonster[];
  skills?: FanxiuDigitDoorMonsterSkill[];
}

export interface FanxiuDigitDoorDoorRefreshSummary {
  level?: string | number;
  point_count?: string | number;
  first_refresh_time?: string | number;
  last_refresh_time?: string | number;
  side_counts?: string;
  customized_types?: Array<string | number>;
  effect_pool_preview?: string;
  pool_semantic_preview?: string;
  replacement_pool_preview?: string;
  effect_option_preview?: string;
  effect_pool_count?: string | number;
  special_rule_count?: string | number;
  max_hp?: string | number;
  confirmed?: boolean;
  report_path?: string;
}

export interface FanxiuDigitDoorDoorPoolSemantic {
  customized_type?: string | number;
  semantic_label?: string;
  static_role?: string;
  source_field?: string;
  effect_count?: string | number;
  effect_ids?: Array<string | number>;
  effect_shows?: string;
  character_count?: string | number;
  character_ids?: Array<string | number>;
  character_names?: string;
}

export interface FanxiuDigitDoorDoorSpecialRuleOption {
  customized_type?: string | number;
  semantic_label?: string;
  rate?: string | number;
  rate_text?: string;
  source_field?: string;
  effect_options?: FanxiuDigitDoorDoorEffectOption[];
  effect_option_preview?: string;
}

export interface FanxiuDigitDoorDoorSpecialRule {
  kind?: string;
  customized_type?: string | number;
  semantic_label?: string;
  source_field?: string;
  effect_options?: FanxiuDigitDoorDoorEffectOption[];
  effect_option_preview?: string;
  trigger_probability?: string | number;
  trigger_probability_text?: string;
  options?: FanxiuDigitDoorDoorSpecialRuleOption[];
}

export interface FanxiuDigitDoorDoorEffectOption {
  effect_id?: string | number;
  customized_type?: string | number;
  door_type?: string | number;
  door_type_label?: string;
  refresh_weights?: string | number;
  put_back?: string | number;
  char_id?: string | number;
  char_name?: string;
  effect_show?: string;
  show_tips?: string;
  skill_ids?: Array<string | number>;
  skill_count?: string | number;
  skill_names?: Array<string | number>;
  effect_hints?: string[];
  effect_hint_preview?: string;
  display_text?: string;
}

export interface FanxiuDigitDoorDoorEffectPoolPoint {
  point_id?: string | number;
  start_refresh_time?: string | number;
  timing_projection?: string;
  position_projection?: string;
}

export interface FanxiuDigitDoorDoorEffectPool {
  customized_type?: string | number;
  semantic_label?: string;
  static_role?: string;
  refresh_weight_summary?: string;
  put_back_summary?: string;
  weighted_effect_count?: string | number;
  put_back_reusable_count?: string | number;
  effect_count?: string | number;
  effect_options?: FanxiuDigitDoorDoorEffectOption[];
  source_fields?: string[];
  source_labels?: string[];
  rate_texts?: string[];
  points?: FanxiuDigitDoorDoorEffectPoolPoint[];
  point_count?: string | number;
  point_time_preview?: string;
  effect_option_preview?: string;
}

export interface FanxiuDigitDoorDoorRefreshPoint {
  point_id?: string | number;
  level?: string | number;
  name?: string;
  side?: string | number;
  side_label?: string;
  start_refresh_time?: string | number;
  timing_projection?: string;
  door_type?: string | number;
  customized_type_values?: Array<string | number>;
  effect_pool_count?: string | number;
  effect_pool_ids?: Array<string | number>;
  effect_pool_preview?: string;
  effect_options?: FanxiuDigitDoorDoorEffectOption[];
  effect_option_preview?: string;
  pool_semantics?: FanxiuDigitDoorDoorPoolSemantic[];
  pool_semantic_text?: string;
  replacement_pool_semantics?: FanxiuDigitDoorDoorPoolSemantic[];
  replacement_pool_semantic_text?: string;
  positive_effect_count?: string | number;
  negative_effect_count?: string | number;
  debuff_door_type?: string | number;
  probability?: string | number;
  rate_list?: Array<string | number>;
  spx_door_type?: Array<string | number>;
  special_rule_projection?: string;
  special_rules?: FanxiuDigitDoorDoorSpecialRule[];
  special_rule_text?: string;
  door_damage?: string | number;
  attack?: string | number;
  volume?: string | number;
  hp?: string | number;
  refresh_offset_dis?: string | number;
  position_projection?: string;
  server_boundary?: string;
}

export interface FanxiuDigitDoorDoorRefreshDetail {
  summary?: FanxiuDigitDoorDoorRefreshSummary;
  effect_pools?: FanxiuDigitDoorDoorEffectPool[];
  points?: FanxiuDigitDoorDoorRefreshPoint[];
}

export interface FanxiuDigitDoorLevelSearchItem {
  id: string | number;
  name: string;
  stage?: string | number;
  group?: string | number;
  layer?: string | number;
  sub_layer?: string | number;
  type?: string | number;
  init_char?: string | number;
  recommend_tips?: string;
  reward_show_title?: string;
  reward_preview?: string;
  reward_count?: number;
  door_count?: number;
  customized_types?: Array<string | number>;
  monster_count?: number;
  score?: number;
}

export interface FanxiuDigitDoorStageReward {
  id?: string | number;
  name?: string;
  name_plain?: string;
  title?: string;
  title_plain?: string;
  rewardShow?: string[];
  reward_items?: FanxiuDigitDoorRewardItem[];
}

export interface FanxiuDigitDoorLevelConfig extends FanxiuDigitDoorLevelSearchItem {
  name_plain?: string;
  recommend_tips_plain?: string;
  monster?: Array<string | number>;
  reward?: string[];
  reward_items?: FanxiuDigitDoorRewardItem[];
  reward_show_title_plain?: string;
  scene_id?: string | number;
  show_img?: string | number;
  door_type_counts?: Record<string, number>;
  first_door_times?: Array<string | number>;
  door_refresh?: FanxiuDigitDoorDoorRefreshDetail | null;
  monster_refresh?: FanxiuDigitDoorMonsterRefreshDetail | null;
}

export interface FanxiuDigitDoorLevelSearchResponse {
  query: string;
  stage?: string;
  limit: number;
  offset: number;
  total: number;
  catalog_path?: string;
  stats: FanxiuDigitDoorStats;
  stage_options?: FanxiuDigitDoorStageOption[];
  items: FanxiuDigitDoorLevelSearchItem[];
}

export interface FanxiuDigitDoorLevelConfigResponse {
  catalog_path: string;
  stats: FanxiuDigitDoorStats;
  stage?: FanxiuDigitDoorStageReward | null;
  item: FanxiuDigitDoorLevelConfig;
}

export interface FanxiuDigitDoorCharacterSearchResponse {
  query: string;
  limit: number;
  offset: number;
  total: number;
  catalog_path?: string;
  stats: FanxiuDigitDoorStats;
  items: FanxiuDigitDoorCharacterSearchItem[];
}

export interface FanxiuDigitDoorCharacterCardResponse {
  catalog_path: string;
  card: FanxiuDigitDoorCharacterCard;
}

export const searchFanxiuDigitDoorCharacterCards = (params: {
  query?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuDigitDoorCharacterSearchResponse>('/fanxiu/resources/digitdoor/character-cards', { params })
    .then(res => res.data);
};

export const getFanxiuDigitDoorCharacterCard = (characterId: string | number) => {
  return api
    .get<FanxiuDigitDoorCharacterCardResponse>('/fanxiu/resources/digitdoor/character-card', { params: { character_id: characterId } })
    .then(res => res.data);
};

export const searchFanxiuDigitDoorLevelConfigs = (params: {
  query?: string;
  stage?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuDigitDoorLevelSearchResponse>('/fanxiu/resources/digitdoor/level-configs', { params })
    .then(res => res.data);
};

export const getFanxiuDigitDoorLevelConfig = (levelId: string | number) => {
  return api
    .get<FanxiuDigitDoorLevelConfigResponse>('/fanxiu/resources/digitdoor/level-config', { params: { level_id: levelId } })
    .then(res => res.data);
};

export const searchFanxiuDigitDoorEnhanceGroups = (params: {
  query?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuDigitDoorEnhanceGroupSearchResponse>('/fanxiu/resources/digitdoor/enhance-groups', { params })
    .then(res => res.data);
};

export const getFanxiuDigitDoorEnhanceGroup = (groupId: string | number) => {
  return api
    .get<FanxiuDigitDoorEnhanceGroupResponse>('/fanxiu/resources/digitdoor/enhance-group', { params: { group_id: groupId } })
    .then(res => res.data);
};

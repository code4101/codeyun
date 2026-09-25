/** 灵界特征的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


export interface FanxiuLingjieFeatureStats {
  gongfa_count?: number;
  feature_base_row_count?: number;
  feature_base_group_count?: number;
  main_feature_row_count?: number;
  main_feature_pin_row_count?: number;
  side_feature_jie_row_count?: number;
  side_feature_pin_row_count?: number;
  lingjie_gongfa_jie_row_count?: number;
  lingjie_gongfa_star_row_count?: number;
  linked_feature_group_count?: number;
  linked_main_pin_group_count?: number;
  linked_side_jie_group_count?: number;
  linked_side_pin_group_count?: number;
  linked_gongfa_name_count?: number;
  linked_item_count?: number;
}

export interface FanxiuLingjieFeatureItem {
  row_key?: string | number;
  id?: string | number;
  name?: string;
  describe?: string;
  quality?: string | number;
  icon?: string;
  effectValue?: string | number;
}

export interface FanxiuLingjieFeatureGroupLink {
  gongfa_id?: string | number;
  main_feature_id?: string | number;
  feature_type?: string | number;
  group?: string | number;
  feature_group?: string | number;
  key_feature?: string | number;
  weighted?: string | number;
  quality?: string | number;
  target_kinds?: string[];
  main_pin_count?: number;
  side_jie_count?: number;
  side_pin_count?: number;
  sample_names?: string;
  sample_features?: string;
  sample_describes?: string;
}

export interface FanxiuLingjieMainFeature {
  row_key?: string | number;
  id?: string | number;
  feature_type?: string | number;
  groups?: Array<string | number>;
  condition?: string | number;
  describe?: string;
  expanded_groups?: FanxiuLingjieFeatureGroupLink[];
}

export interface FanxiuLingjieCompactRow {
  row_key?: string | number;
  id?: string | number;
  gongfaId?: string | number;
  pin?: string | number;
  jie?: string | number;
  star?: string | number;
  quality?: string | number;
  featureGroup?: string | number;
  feature?: string | number;
  skill?: string | number;
  sortValue?: string | number;
  param?: unknown;
  cd?: string | number;
  name?: string;
  describe?: string;
}

export interface FanxiuLingjieRuntimeProfileSample {
  star?: string | number;
  projected_skill_id?: string | number;
  skill_name?: string;
  career?: string;
  timeline_id?: string | number;
  hit_count?: string | number;
  first_hit_ms?: string | number;
  last_hit_ms?: string | number;
  hit_times_ms?: string;
  hurt_percents?: string;
  total_hurt_percent?: string | number;
  damage_scope_types?: string;
  scope_params?: string;
  target_type?: string | number;
  target_max?: string | number;
  cd_time?: string | number;
}

export interface FanxiuLingjieRuntimeDamageFamily {
  family_id?: string;
  careers?: string;
  profile_count?: string | number;
  skill_count?: string | number;
  timeline_count?: string | number;
  channel?: string;
  hit_count?: string | number;
  first_hit_ms?: string | number;
  last_hit_ms?: string | number;
  hit_times_ms?: string;
  hurt_percents?: string;
  total_hurt_percent?: string | number;
  damage_scope_types?: string;
  scope_params?: string;
  scope?: string | number;
  target_type?: string | number;
  target_max?: string | number;
  cd_times?: string;
  fight_scores?: string;
  sample_timelines?: string;
}

export interface FanxiuLingjieRuntimeTimelineSample {
  timeline_id?: string | number;
  careers?: string;
  q_desc?: string;
  q_track_time?: string | number;
  hurt_event_count?: string | number;
  q_hurt_events?: string;
  effect_resources?: string;
  sound_ids?: string;
}

export interface FanxiuLingjieRuntimeSummary {
  projected_skill_count?: number;
  profile_count?: number;
  timeline_count?: number;
  careers?: string[];
  timeline_ids?: string[];
  profile_samples?: FanxiuLingjieRuntimeProfileSample[];
  damage_families?: FanxiuLingjieRuntimeDamageFamily[];
  timeline_samples?: FanxiuLingjieRuntimeTimelineSample[];
}

export interface FanxiuLingjieFeatureCard {
  gongfa_id: string | number;
  name: string;
  description?: string;
  icon?: string;
  quality?: string | number;
  item_count?: number;
  main_feature_count?: number;
  main_pin_count?: number;
  jie_count?: number;
  star_count?: number;
  feature_group_link_count?: number;
  main_pin_group_count?: number;
  side_jie_group_count?: number;
  side_pin_group_count?: number;
  main_feature_names?: string;
  side_feature_names?: string;
  jie_features?: string;
  star_skills?: string;
  items?: FanxiuLingjieFeatureItem[];
  main_features?: FanxiuLingjieMainFeature[];
  main_pin_rows?: FanxiuLingjieCompactRow[];
  jie_rows?: FanxiuLingjieCompactRow[];
  star_rows?: FanxiuLingjieCompactRow[];
  runtime_summary?: FanxiuLingjieRuntimeSummary;
}

export interface FanxiuLingjieFeatureSearchItem {
  gongfa_id: string | number;
  name: string;
  icon?: string;
  quality?: string | number;
  item_count?: number;
  item_names?: string[];
  description_preview?: string;
  main_feature_names?: string;
  side_feature_names?: string;
  main_feature_count?: number;
  main_pin_count?: number;
  jie_count?: number;
  star_count?: number;
  feature_group_link_count?: number;
  main_pin_group_count?: number;
  side_jie_group_count?: number;
  side_pin_group_count?: number;
  score?: number;
}

export interface FanxiuLingjieFeatureSearchResponse {
  limit: number;
  offset: number;
  total: number;
  catalog_path?: string;
  stats: FanxiuLingjieFeatureStats;
  items: FanxiuLingjieFeatureSearchItem[];
}

export const searchFanxiuLingjieFeatureCards = (params: {
  query?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuLingjieFeatureSearchResponse>('/fanxiu/resources/gongfa/lingjie-feature-cards', { params })
    .then(res => res.data);
};

export const getFanxiuLingjieFeatureCard = (gongfaId: string | number) => {
  return api
    .get<FanxiuLingjieFeatureCard>('/fanxiu/resources/gongfa/lingjie-feature-card', { params: { gongfa_id: gongfaId } })
    .then(res => res.data);
};

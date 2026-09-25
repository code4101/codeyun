/** 功法目录与图鉴的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';
import type { FanxiuFacetIndex, FanxiuTimelineHint } from './catalogCommon';


export interface FanxiuGongfaAtlasBook {
  book_id: number;
  name: string;
  skill_type?: number | null;
  skill_type_name: string;
  filter_category: string;
  quality_type_name: string;
  quality_grade_name: string;
  quality_grade_order: number;
  quality_grade_color: string;
  quality_family_name: string;
  upgrade_index: number;
  upgrade_priority?: number | null;
  upgrade_priority_pool: 'equipped_dependency' | 'fallback_learned';
  upgrade_first_usage?: Record<string, unknown> | null;
  sub_type_names: string[];
  grade: number;
  max_grade: number;
  jie: number;
  star: number;
  pin: number;
  wujing: number;
  tongxuan: number;
  quality: number;
  total_exp: number;
  max_star: number;
  max_jie: number;
  max_wujing: number;
  max_tongxuan: number;
  full: boolean;
  remaining_star?: number | null;
  remaining_fusion?: number | null;
  catalog_href: string;
}

export interface FanxiuGongfaAtlasSnapshot {
  books: FanxiuGongfaAtlasBook[];
  priority_book_ids: number[];
  runtime_complete: boolean;
  runtime_error: string;
  runtime_updated_at: number;
  runtime_item_count: number;
  summary: Record<string, number>;
  runtime_debug: Record<string, unknown>;
}

export interface FanxiuGongfaAtlasUsage {
  category: string;
  category_name: string;
  slot: number;
  equipped_name: string;
  location_name: string;
  role: 'main' | 'xian' | 'side' | 'grid' | string;
  role_name: string;
  grid?: number;
  source_skill_id?: number;
  effect_id?: number;
  effect_ids?: number[];
  effect_text: string;
  effect_rich_text: string;
}

export interface FanxiuGongfaAcquisitionChannel {
  kind: '融合' | '悟境' | '通玄' | string;
  source: string;
  title: string;
  detail: string;
  mode: string;
  item_id: number;
  npc_id?: number | null;
  level?: number | null;
}

export interface FanxiuGongfaAtlasBookDetail {
  book_id: number;
  usages: FanxiuGongfaAtlasUsage[];
  acquisition_channels: FanxiuGongfaAcquisitionChannel[];
  xianyuan_snapshot_available: boolean;
}

export interface FanxiuGongfaStats {
  gongfa_count?: number;
  skill_count?: number;
  linked_skill_count?: number;
  unmatched_skill_count?: number;
  cards_with_skills?: number;
  max_skill_count?: number;
  activity_count?: number;
  item_with_time_hint_count?: number;
  gongfa_with_time_hint_count?: number;
  progression_table_counts?: Record<string, number>;
  linked_progression_counts?: Record<string, number>;
  unmatched_progression_counts?: Record<string, number>;
}

export interface FanxiuGongfaSkill {
  row_key?: string;
  id?: string | number;
  origin_id?: string | number;
  name?: string;
  skill_name?: string;
  quality?: string | number;
  quality_name?: string;
  quality_color?: string;
  quality_tab?: string;
  pin?: string | number;
  group?: string | number;
  type?: string | number;
  type_name?: string;
  sub_type?: string | number;
  sub_type_name?: string;
  icon?: string;
  describe?: string;
  describe_rich?: string;
  describe_sections?: FanxiuGongfaProgressionSection[];
  effect_describe?: string;
  effect_describe_rich?: string;
  effect_describe_sections?: FanxiuGongfaProgressionSection[];
  additional_describe?: string;
  additional_describe_rich?: string;
  additional_describe_sections?: FanxiuGongfaProgressionSection[];
}

export interface FanxiuGongfaLinkedItem {
  id?: string | number;
  name?: string;
  icon?: string;
  small_icon?: string;
  quality?: string | number;
  count?: string | number;
  description?: string;
}

export interface FanxiuGongfaFazeTip {
  code?: string;
  reason?: string;
  text?: string;
}

export interface FanxiuGongfaFazeEffectResource {
  id?: string | number;
  type?: string | number;
  params?: string | number;
}

export interface FanxiuGongfaFazeResource {
  id?: string | number;
  sort?: string | number;
  name?: string;
  head_name?: string;
  effects?: string | number;
  effect_resource?: FanxiuGongfaFazeEffectResource | null;
  last_grade?: string | number;
  show_condition?: unknown;
  source?: string | number;
  tip_str?: string;
  tips?: FanxiuGongfaFazeTip[];
}

export interface FanxiuGongfaFeatureLink {
  feature?: string | number;
  source_gid?: string | number;
  source_jie?: string | number;
  source_name?: string;
  source_describe?: string;
  match_kind?: string;
  direct_match_count?: string | number;
  family_match_count?: string | number;
  config_ids?: string;
  config_descriptions?: string;
  timelines?: string;
  effect_paths?: string;
  sound_ids?: string;
  hit_frames?: string;
}

export interface FanxiuGongfaProgressionSection {
  title?: string;
  title_rich?: string;
  lines?: string[];
  rich_lines?: string[];
}

export interface FanxiuGongfaProgressionRow {
  row_key?: string;
  id?: string | number;
  gid?: string | number;
  pin?: string | number;
  jie?: string | number;
  star?: string | number;
  grade?: string | number;
  name?: string;
  title?: string;
  condition?: unknown;
  show_condition?: unknown;
  consume?: unknown;
  consume_items?: FanxiuGongfaLinkedItem[];
  skill?: unknown;
  feature?: unknown;
  attr?: unknown;
  attributes?: unknown;
  faze_id?: string | number;
  faze_resource?: FanxiuGongfaFazeResource | null;
  describe?: string;
  describe_rich?: string;
  describe_sections?: FanxiuGongfaProgressionSection[];
  top_describe?: string;
  top_describe_rich?: string;
  down_describe?: string;
  down_describe_rich?: string;
  upgrade_desc?: string;
  upgrade_desc_rich?: string;
  bag_effect?: unknown;
  skill_effect?: unknown;
  feature_link?: FanxiuGongfaFeatureLink;
}

export interface FanxiuGongfaCard {
  id: string | number;
  name: string;
  quality?: string | number;
  quality_name?: string;
  quality_rich_name?: string;
  quality_grade_name?: string;
  quality_family_name?: string;
  quality_rank?: string | number;
  quality_icon?: string;
  quality_type_id?: string | number;
  quality_type_name?: string;
  skill_type?: string | number;
  skill_type_name?: string;
  icon?: string;
  small_icon?: string;
  description?: string;
  description_rich?: string;
  consume?: unknown;
  consume_items?: FanxiuGongfaLinkedItem[];
  show_condition?: unknown;
  show_condition_items?: FanxiuGongfaLinkedItem[];
  sort?: string | number;
  level_group?: string | number;
  species?: string | number;
  source_row_key?: string | number;
  skill_count: number;
  skills: FanxiuGongfaSkill[];
  progression_counts: Record<string, number>;
  progression: Record<string, FanxiuGongfaProgressionRow[]>;
  time_hints?: FanxiuTimelineHint[];
  first_time_hint?: FanxiuTimelineHint | null;
  terms?: string[];
}

export interface FanxiuGongfaSearchItem {
  id: string | number;
  name: string;
  quality?: string | number;
  quality_name?: string;
  quality_rich_name?: string;
  quality_grade_name?: string;
  quality_family_name?: string;
  quality_rank?: string | number;
  quality_icon?: string;
  quality_type_id?: string | number;
  quality_type_name?: string;
  skill_type?: string | number;
  skill_type_name?: string;
  icon?: string;
  small_icon?: string;
  description_preview?: string;
  effect_preview?: string;
  skill_count: number;
  progression_counts: Record<string, number>;
  terms: string[];
  skill_names: string[];
  skill_type_names?: string[];
  first_time_hint?: FanxiuTimelineHint | null;
  score: number;
}

export interface FanxiuGongfaQualityOption {
  value: string;
  label: string;
  rich_label?: string;
  color?: string;
  count: number;
  quality?: string | number;
  quality_rank?: string | number;
  quality_sort?: string | number;
}

export interface FanxiuGongfaQualityPartOption {
  value: string;
  label: string;
  rich_label?: string;
  color?: string;
  count: number;
}

export interface FanxiuGongfaSkillTypeOption {
  value: string;
  label: string;
  count: number;
  skill_type?: string | number;
}

export interface FanxiuGongfaSearchResponse {
  query: string;
  quality_name?: string;
  quality_grade_name?: string;
  quality_family_name?: string;
  skill_type_name?: string;
  sort_by?: string;
  sort_order?: string;
  limit: number;
  offset: number;
  total: number;
  stats: FanxiuGongfaStats;
  catalog_path: string;
  quality_options?: FanxiuGongfaQualityOption[];
  quality_grade_options?: FanxiuGongfaQualityPartOption[];
  quality_family_options?: FanxiuGongfaQualityPartOption[];
  skill_type_options?: FanxiuGongfaSkillTypeOption[];
  facet_index?: FanxiuFacetIndex;
  items: FanxiuGongfaSearchItem[];
}

export interface FanxiuGongfaCardResponse {
  catalog_path: string;
  card: FanxiuGongfaCard;
}

export interface FanxiuGongfaHomeMakeStaticDetailRow {
  section: string;
  active_state: string;
  effect_id?: string | number;
  template_key: string;
  source_tables: string;
  config_keys: string;
  sort?: string | number;
  rich_text: string;
  plain_text: string;
}

export interface FanxiuGongfaHomeMakeStaticDetailResponse {
  source: string;
  export_root: string;
  params: {
    gongfa_id: number;
    star: number;
    jie: number;
    pin: number;
    include_inactive: boolean;
  };
  card: {
    id: string | number;
    name: string;
    icon?: string;
    quality?: string | number;
    skill_type?: string | number;
    description?: string;
    description_rich?: string;
    skill_id?: string | number;
  };
  rows: FanxiuGongfaHomeMakeStaticDetailRow[];
  warnings: string[];
  counts: {
    rows: number;
    side_effect_sources: number;
  };
}

export interface FanxiuGongfaHomeMakeBuffParameterLink {
  gongfa_id: string;
  side_jie_name: string;
  buff_id: string;
  buff_name: string;
  field: string;
  field_value: string;
  token: string;
  target_table: string;
  target_role: string;
  target_id: string;
  target_gongfa_id: string;
  target_name: string;
  target_description: string;
  source_file: string;
}

export interface FanxiuGongfaHomeMakeBuffParameterGroup {
  group_key: string;
  row_count: string | number;
  unique_buff_count: string | number;
  buff_ids: string;
  gongfa_names: string;
  side_jie_names: string;
  buff_name: string;
  buff_desc: string;
  desc_category: string;
  effect_type: string;
  buff_type: string;
  duration: string;
  duration_seconds: string;
  periodic_time: string;
  periodic_seconds: string;
  relation_type: string;
  layer: string;
  populated_parameter_fields: string;
  linked_targets: string;
  matching_rows: number;
  matching_buff_ids: string;
  link_count: number;
  links: FanxiuGongfaHomeMakeBuffParameterLink[];
}

export interface FanxiuGongfaHomeMakeBuffParameterSemanticsResponse {
  export_root: string;
  source: string;
  params: {
    gongfa_id: string;
    query: string;
    limit: number;
  };
  total: number;
  items: FanxiuGongfaHomeMakeBuffParameterGroup[];
  counts: {
    candidate_rows: number;
    groups: number;
    links: number;
    unique_buff_ids: number;
    populated_fields: Record<string, number>;
  };
  outputs: Record<string, string>;
}

export interface FanxiuGongfaHomeMakeXianShuFormulaItem {
  side_feature_id: string;
  side_feature_name: string;
  feature_group: string;
  jie: string;
  side_feature: string;
  star: string;
  xianjie_star_id: string;
  star_feature: string;
  buff_ids: string;
  buff_names: string;
  star_params: string;
  side_feature_params: string;
  combined_params: string;
  placeholder_count: string;
  rendered_plain: string;
  source_file: string;
  source_line: string;
  gongfa_ids: string;
  gongfa_names: string;
}

export interface FanxiuGongfaHomeMakeXianShuFormulaGroup {
  feature_group: string;
  rows: string | number;
  star_rows: string | number;
  side_feature_names: string;
  buff_names: string;
  sample_rendered_plain: string;
  gongfa_ids: string;
  gongfa_names: string;
}

export interface FanxiuGongfaHomeMakeXianShuFormulaCatalogResponse {
  export_root: string;
  source: string;
  params: {
    gongfa_id: string;
    query: string;
    limit: number;
    star: number;
  };
  total: number;
  items: FanxiuGongfaHomeMakeXianShuFormulaItem[];
  groups: FanxiuGongfaHomeMakeXianShuFormulaGroup[];
  counts: {
    rows: number;
    feature_groups: number;
    rows_with_buff_candidates: number;
  };
  outputs: Record<string, string>;
}

export interface FanxiuGongfaSpecialFazeGroup {
  gid: string;
  gongfa_name: string;
  stage_count: string;
  faze_count: string;
  effect_types: string;
  reason_codes: string;
  tip_texts: string;
  consume_items: string;
}

export interface FanxiuGongfaSpecialFazeStage {
  gid: string;
  gongfa_name: string;
  source_id: string;
  stage: string;
  source_name: string;
  faze_id: string;
  faze_name: string;
  effect_id: string;
  effect_type: string;
  effect_params: string;
  effect_attr: string;
  tip_codes: string;
  tip_texts: string;
  tip_pairs: string;
  consume: string;
  skill: string;
  attr: string;
  describe_preview: string;
}

export interface FanxiuGongfaSpecialFazeEffectType {
  effect_type: string;
  stage_count: string;
  gongfa_count: string;
  effect_id_count: string;
  sample_gongfa: string;
  sample_effect_ids: string;
  reason_codes: string;
  tip_texts: string;
  effect_params_sample: string;
  effect_attr_sample: string;
}

export interface FanxiuGongfaSpecialFazeReason {
  reason: string;
  stage_count: string;
  gongfa_count: string;
  effect_types: string;
  sample_gongfa: string;
  tip_texts: string;
}

export interface FanxiuGongfaSpecialFazeCatalogResponse {
  output_dir: string;
  paths: Record<string, string>;
  filters: {
    query: string;
    gid: string;
    effect_type: string;
    reason: string;
    limit: number;
    offset: number;
  };
  counts: {
    groups: number;
    stages: number;
    effect_types: number;
    reasons: number;
    filtered_groups: number;
    selected_stages: number;
  };
  groups: FanxiuGongfaSpecialFazeGroup[];
  selected: {
    gid: string;
    group: FanxiuGongfaSpecialFazeGroup | null;
    stages: FanxiuGongfaSpecialFazeStage[];
    effect_types: FanxiuGongfaSpecialFazeEffectType[];
    reasons: FanxiuGongfaSpecialFazeReason[];
  };
  top_effect_types: FanxiuGongfaSpecialFazeEffectType[];
  top_reasons: FanxiuGongfaSpecialFazeReason[];
}

export const searchFanxiuGongfaCards = (params: {
  query?: string;
  quality_name?: string;
  quality_grade_name?: string;
  quality_family_name?: string;
  skill_type_name?: string;
  sort_by?: string;
  sort_order?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api.get<FanxiuGongfaSearchResponse>('/fanxiu/resources/gongfa/cards', { params }).then(res => res.data);
};

export const getFanxiuGongfaCard = (gongfaId: string | number) => {
  return api
    .get<FanxiuGongfaCardResponse>('/fanxiu/resources/gongfa/card', { params: { gongfa_id: gongfaId } })
    .then(res => res.data);
};

export const getFanxiuGongfaHomeMakeStaticDetail = (
  gongfaId: string | number,
  params: { star?: number; jie?: number; pin?: number; include_inactive?: boolean } = {}
) => {
  return api
    .get<FanxiuGongfaHomeMakeStaticDetailResponse>('/fanxiu/resources/gongfa/homemake-static-detail', {
      params: { gongfa_id: gongfaId, ...params }
    })
    .then(res => res.data);
};

export const getFanxiuGongfaHomeMakeBuffParameterSemantics = (
  gongfaId?: string | number | null,
  params: { query?: string; limit?: number } = {}
) => {
  const requestParams: Record<string, string | number | undefined> = { ...params };
  if (gongfaId !== undefined && gongfaId !== null && String(gongfaId).trim()) {
    requestParams.gongfa_id = gongfaId;
  }
  return api
    .get<FanxiuGongfaHomeMakeBuffParameterSemanticsResponse>(
      '/fanxiu/resources/gongfa/homemake-buff-parameter-semantics',
      {
        params: requestParams,
        timeout: 60000,
      }
    )
    .then(res => res.data);
};

export const getFanxiuGongfaHomeMakeXianShuFormulaCatalog = (
  gongfaId?: string | number | null,
  params: { query?: string; limit?: number; star?: number } = {}
) => {
  const requestParams: Record<string, string | number | undefined> = { ...params };
  if (gongfaId !== undefined && gongfaId !== null && String(gongfaId).trim()) {
    requestParams.gongfa_id = gongfaId;
  }
  return api
    .get<FanxiuGongfaHomeMakeXianShuFormulaCatalogResponse>(
      '/fanxiu/resources/gongfa/homemake-xianshu-formula-catalog',
      {
        params: requestParams,
        timeout: 60000,
      }
    )
    .then(res => res.data);
};

export const getFanxiuGongfaSpecialFazeCatalog = (params: {
  query?: string;
  gid?: string | number | null;
  effect_type?: string;
  reason?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuGongfaSpecialFazeCatalogResponse>(
      '/fanxiu/resources/hot-update/gongfa-special-faze-catalog',
      {
        params,
        timeout: 60000,
      }
    )
    .then(res => res.data);
};

export const getFanxiuGongfaAtlas = () => (
  api.get<FanxiuGongfaAtlasSnapshot>('/fanxiu/inventory/gongfa-atlas').then(res => res.data)
);

export const getFanxiuGongfaAtlasBookDetail = (bookId: number) => (
  api.get<FanxiuGongfaAtlasBookDetail>(`/fanxiu/inventory/gongfa-atlas/books/${bookId}`).then(res => res.data)
);

export const collectFanxiuGongfaAtlas = () => (
  api.post<FanxiuGongfaAtlasSnapshot>('/fanxiu/inventory/gongfa-atlas/collect', null, {
    timeout: 130_000,
  }).then(res => res.data)
);

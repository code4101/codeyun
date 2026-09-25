/** 活动运行快照、兑换与榜单的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';
import type { NoteNode } from '../notes';
import { loadFanxiuNoteHelpers } from './noteAccess';


export interface FanxiuActivityItem {
  id: string;
  name: string;
  cross_count: number;
  start_date: string;
  end_date: string;
  note_id?: string | null;
}

export interface FanxiuActivityListSnapshot {
  items: FanxiuActivityItem[];
}

export interface FanxiuResourceRankingRow {
  rank: number;
  score: number;
  role_key: string;
  name: string;
  server_id?: number | null;
  server_name: string;
  club_name: string;
  is_self: boolean;
  is_reward_guard: boolean;
  reward_rank_start?: number | null;
  reward_rank_end?: number | null;
  talent_pill_count?: number | null;
  score_per_talent_pill?: number | null;
  has_player: boolean;
  is_last_player: boolean;
}

export interface FanxiuLingzhuangHuadaoRankingSnapshot {
  ok: boolean;
  available: boolean;
  complete: boolean;
  source: string;
  activity: {
    key: 'lingzhuang-huadao';
    name: string;
    resource_name: string;
    rank_activity_id: number;
  };
  captured_at: string;
  reason?: string;
  rank_list_size: number;
  loaded_rank_count: number;
  declared_rank_count?: number | null;
  reward_guard_ranks?: number[];
  self_ranking?: FanxiuResourceRankingRow | null;
  rankings: FanxiuResourceRankingRow[];
  plane_rank_activity_id: number;
  plane_rank_list_size: number;
  plane_loaded_rank_count: number;
  plane_declared_rank_count?: number | null;
  plane_self_ranking?: FanxiuResourceRankingRow | null;
  plane_rankings: FanxiuResourceRankingRow[];
}

export interface FanxiuLingzhuangStrengtheningSide {
  material_id: number;
  material_name: string;
  material_count?: number | null;
  equipment_level?: number | null;
  equipment_raw_level?: number | null;
  equipped?: boolean | null;
}

export interface FanxiuLingzhuangStrengtheningRow {
  part: string;
  initial: FanxiuLingzhuangStrengtheningSide;
  dongxuan: FanxiuLingzhuangStrengtheningSide;
}

export interface FanxiuLingzhuangTaskProgress {
  task_id: number;
  order: number;
  name: string;
  progress: number;
  target: number;
  finished: boolean;
  talent_pill_count: number;
}

export interface FanxiuYaochiFlowerTaskMilestone {
  task_id: number;
  order: number;
  name: string;
  target: number;
  talent_pill_count: number;
  must_get: boolean;
}

export interface FanxiuLingchongJingwuResourceItem {
  item_id: number;
  name: string;
  quality: number;
  count: number;
  /** Legacy key; entries are aptitude gift IDs, not pet types. */
  aptitude_gain_by_pet_type: Record<number, number>;
  aptitude_gain_by_gift_id?: Record<number, number>;
  minimum_aptitude_gain: number;
  maximum_aptitude_gain: number;
}

export interface FanxiuLingchongJingwuResourceSnapshot {
  activity_id: string;
  captured_at: string;
  source_kind: string;
  complete: boolean;
  items: FanxiuLingchongJingwuResourceItem[];
  total_count: number;
  reason: string;
  evidence: Record<string, unknown>;
}

export interface FanxiuLingchongJingwuTaskMilestone {
  task_id: number;
  order: number;
  name: string;
  target: number;
  progress: number;
  status: number;
  finished: boolean;
  talent_pill_count: number;
  rewards: string[];
}

export interface FanxiuLingchongJingwuTaskSnapshot {
  captured_at: string;
  source_kind: string;
  complete: boolean;
  declared_task_count: number;
  observed_task_count: number;
  reason: string;
  declared_task_ids: number[];
  observed_task_ids: number[];
  items: FanxiuLingchongJingwuTaskMilestone[];
  evidence: Record<string, unknown>;
}

export interface FanxiuExchangeActivityTaskMilestone {
  task_id: number;
  order: number;
  name: string;
  target: number;
  progress: number;
  status: number;
  finished: boolean;
  must_get: boolean;
  rewards: string[];
}

export interface FanxiuExchangeActivityTaskSnapshot {
  captured_at: string;
  source_kind: string;
  complete: boolean;
  declared_task_count: number;
  observed_task_count: number;
  reason: string;
  declared_task_ids: number[];
  observed_task_ids: number[];
  items: FanxiuExchangeActivityTaskMilestone[];
  evidence: Record<string, unknown>;
}

export interface FanxiuYaochiFlowerResourceItem {
  item_id: number;
  item_ids: number[];
  name: string;
  icon: string;
  small_icon: string;
  description: string;
  quality?: number | null;
  quality_color?: string;
  friendship: number;
  count?: number | null;
  total_friendship?: number | null;
}

export interface FanxiuYaochiFlowerResourceSnapshot {
  activity_id: string;
  captured_at: string;
  source_kind: string;
  complete: boolean;
  items: FanxiuYaochiFlowerResourceItem[];
  total_count?: number | null;
  total_friendship?: number | null;
  evidence: Record<string, unknown>;
}

export interface FanxiuLingzhuangScoreRound {
  round: number;
  target: number;
}

export interface FanxiuLingzhuangStrengtheningSnapshot {
  activity_id: string;
  game_task_activity_id?: number | null;
  captured_at: string;
  materials_captured_at: string;
  equipment_captured_at: string;
  task_progress_captured_at: string;
  source_kind: string;
  complete: boolean;
  warnings: string[];
  rows: FanxiuLingzhuangStrengtheningRow[];
  equipment_tasks: FanxiuLingzhuangTaskProgress[];
  equipment_current?: number | null;
  score_round?: number | null;
  score_total_rounds: number;
  score_current?: number | null;
  score_rounds: FanxiuLingzhuangScoreRound[];
  score_tasks: FanxiuLingzhuangTaskProgress[];
}

export interface RelationshipSample {
  id: string;
  captured_at: string;
  x: number;
  values: Record<string, number>;
}

export interface RelationshipDataset {
  namespace: string;
  entity_id: string;
  samples: RelationshipSample[];
}

export interface FanxiuExchangeShopItem {
  id: string;
  goods_id: number;
  item_id: number;
  source_order: number;
  priority_order?: number | null;
  locked: boolean;
  name: string;
  goods_num: number;
  token_cost: number;
  purchase_limit: number;
  purchased_count: number;
  row_total_tokens?: number | null;
  cumulative_tokens?: number | null;
  remaining_challenges?: number | null;
  discount?: number | null;
  original_price?: number | null;
}

export interface FanxiuExchangeRankingItem {
  reward_counts?: Record<string, number>;
  id: string;
  ranking_scope: string;
  rank: number;
  score: number;
  name: string;
  server_id?: number | null;
  server_name: string;
  club_name: string;
  is_self: boolean;
  is_reward_guard: boolean;
  reward_rank_start?: number | null;
  reward_rank_end?: number | null;
  talent_pill_count?: number | null;
  score_per_talent_pill?: number | null;
  has_player: boolean;
  is_last_player: boolean;
  captured_at: string;
  subject?: {
    kind: string;
    id?: string | null;
    name: string;
    server_id?: number | null;
    server_name: string;
    members?: Array<Record<string, unknown>> | null;
  } | null;
}

export interface FanxiuRankingScopeMetadata {
  scope?: string;
  key?: string;
  label: string;
  role: string;
  subject?: string;
  subject_kind?: string;
}

export interface FanxiuExchangeRankingPage {
  page: number;
  page_size: number;
  total: number;
  items: FanxiuExchangeRankingItem[];
  last_captured_at: string;
  entries?: FanxiuExchangeRankingItem[];
  reward_tiers?: FanxiuExchangeRankingItem[];
  entry_total?: number;
  declared_rank_count?: number;
  loaded_entry_count?: number;
  complete?: boolean;
  view_mode?: string;
  self_entry?: FanxiuExchangeRankingItem | null;
  last_entry?: FanxiuExchangeRankingItem | null;
  scope?: FanxiuRankingScopeMetadata;
  ranking_scope?: string;
  scope_label?: string;
  scope_role?: string;
  scope_subject?: string;
}

export interface FanxiuExchangeActivitySummary {
  id: string;
  instance_key: string;
  family: 'gameplay_rank' | 'resource_rank';
  label: string;
  activity_type: string;
  runtime_id: string;
  game_activity_id?: number | null;
  cross_count: number;
  prepare_at: string;
  start_date: string;
  end_date: string;
  start_at: string;
  end_at: string;
  captured_at: string;
  is_active: boolean;
  close_panel_date: string;
  close_panel_at: string;
  lifecycle_phase: 'scheduled' | 'active' | 'settlement' | 'closed';
  is_collectible: boolean;
}

export interface FanxiuExchangeActivityDetail extends FanxiuExchangeActivitySummary {
  game_rank_activity_id?: number | null;
  game_shop_base_id?: number | null;
  currency_type?: number | null;
  currency_name: string;
  current_currency: number;
  cumulative_currency: number;
  resource_strategy: Record<string, unknown>;
  instance_data: Record<string, unknown>;
  source_kind: string;
  yield_rate?: null;
  currency_fact_fresh: boolean;
  shop_fact_fresh: boolean;
  budget_ready: boolean;
  budget_block_reason: string;
  currency_captured_at: string;
  shop_snapshot_captured_at: string;
  shop_refresh_status: string;
  shop_refresh_reason: string;
  rankings_refresh_status: string;
  rankings_refresh_reason: string;
  exchange_plan: {
    current_prayer_cycle?: string;
    current_prayer_resource?: string;
    next_prayer_resource?: string | null;
    card_mail_resource?: string | null;
    locked_reserved_tokens?: number;
    priority_order_ids?: string[];
    priority_group_goods_ids?: Record<string, number[]>;
    target_budgets?: Record<string, FanxiuExchangeBudget>;
    economical_target_id?: string;
    closing_goods_items_complete?: boolean;
    closing_goods_complete?: boolean;
    next_prayer_cutoff_at?: string;
    activity_page_closes_after_next_prayer_cutoff?: boolean;
    card_mail_close_action?: 'leave_for_mail' | 'redeem_during_grace_period';
    [key: string]: unknown;
  };
  shop_items: FanxiuExchangeShopItem[];
}

export interface FanxiuExchangeBudget {
  target_total_tokens: number;
  target_remaining_tokens: number;
  current_currency: number;
  cumulative_currency: number;
  balance_gap: number;
  cumulative_gap: number;
  required_new_currency: number;
}

export interface FanxiuExchangeActivitySnapshot {
  activities: FanxiuExchangeActivitySummary[];
  selected_activity?: FanxiuExchangeActivityDetail | null;
}

export interface FanxiuExchangeActivityObservation {
  id: string;
  activity_id: string;
  captured_at: string;
  lifecycle_phase: string;
  snapshot_kind: string;
  current_currency: number;
  cumulative_currency: number;
  shop_status: string;
  rankings_status: string;
  payload: Record<string, unknown>;
}

export interface FanxiuExchangeActivityObservationPage {
  items: FanxiuExchangeActivityObservation[];
  total: number;
}

export interface FanxiuLatestExchangeActivitySnapshot {
  activity_type?: string | null;
  snapshot?: FanxiuExchangeActivitySnapshot | null;
}

export interface FanxiuScheduleRankingSnapshot {
  business_date: string;
  gameplay_rank: FanxiuLatestExchangeActivitySnapshot;
  resource_rank: FanxiuLatestExchangeActivitySnapshot;
}

export interface FanxiuYuandingSanshengTaskMilestone {
  task_id: number;
  order: number;
  name: string;
  target: number;
  talent_pill_count: number;
  must_get: boolean;
  rewards: string[];
}

export interface FanxiuWorldlineActivityItem {
  key: string;
  class?: string;
  bean_id?: string | number;
  id?: string | number;
  activityId?: string | number;
  name: string;
  activityType?: string | number;
  state?: string | number;
  prepareEndTime?: string | number | null;
  prepareEndTimeText?: string;
  startTime?: string | number | null;
  startTimeText?: string;
  endTime?: string | number | null;
  endTimeText?: string;
  closePanelTime?: string | number | null;
  closePanelTimeText?: string;
  daoNian?: string | number;
  scheduleId?: string | number;
  row?: string | number;
  loopDay?: string | number;
  avgWorldLevel?: string | number;
  crossGroup?: string | number;
  serverIds?: number[];
  serverCount?: number;
}

export interface FanxiuWorldlineActivityScheduleResponse {
  available: boolean;
  source_kind: string;
  created_at: string;
  runtime_current?: boolean;
  complete?: boolean;
  openServerTime?: string | number;
  openServerTimeText?: string;
  count: number;
  items: FanxiuWorldlineActivityItem[];
}

export const getFanxiuLatestWorldlineActivitySchedule = () => {
  return api
    .get<FanxiuWorldlineActivityScheduleResponse>('/fanxiu/activity-runtime-schedule/latest')
    .then(res => res.data);
};

export const getFanxiuActivityList = () => {
  return api.get<FanxiuActivityListSnapshot>('/fanxiu/activity-list').then(res => res.data);
};

export const saveFanxiuActivityList = (payload: FanxiuActivityListSnapshot) => {
  return api.put<FanxiuActivityListSnapshot>('/fanxiu/activity-list', payload).then(res => res.data);
};

export const getFanxiuLingzhuangHuadaoRankingSnapshot = () => {
  return api.get<FanxiuLingzhuangHuadaoRankingSnapshot>(
    '/fanxiu/dynamic-instrumentation/activity-ranks/lingzhuang-huadao',
    { timeout: 60_000 },
  ).then(res => res.data);
};

export const getFanxiuLingzhuangStrengtheningSnapshot = () => {
  return api.get<FanxiuLingzhuangStrengtheningSnapshot>(
    '/fanxiu/activity-list/lingzhuang-huadao/strengthening',
  ).then(res => res.data);
};

export const collectFanxiuLingzhuangStrengtheningSnapshot = (activityId: string) => {
  return api.post<FanxiuLingzhuangStrengtheningSnapshot>(
    `/fanxiu/activity-list/lingzhuang-huadao/${encodeURIComponent(activityId)}/strengthening/collect`,
    {},
    { timeout: 120000 },
  ).then(res => res.data);
};

export const getFanxiuLingzhuangRelationshipSamples = (activityId: string) => {
  return api.get<RelationshipDataset>(
    `/fanxiu/activity-list/lingzhuang-huadao/${encodeURIComponent(activityId)}/relationship-samples`,
  ).then(res => res.data);
};

export const recordFanxiuLingzhuangRelationshipSample = (activityId: string) => {
  return api.post<RelationshipDataset>(
    `/fanxiu/activity-list/lingzhuang-huadao/${encodeURIComponent(activityId)}/relationship-samples/record`,
  ).then(res => res.data);
};

export const getFanxiuExchangeActivitySnapshot = (activityType: string, activityId?: string) => {
  return api.get<FanxiuExchangeActivitySnapshot>(
    `/fanxiu/activity-list/exchange-events/${encodeURIComponent(activityType)}`,
    { params: activityId ? { activity_id: activityId } : undefined },
  ).then(res => res.data);
};

export const getFanxiuExchangeActivityObservations = (
  activityType: string,
  activityId: string,
) => {
  return api.get<FanxiuExchangeActivityObservationPage>(
    `/fanxiu/activity-list/exchange-events/${encodeURIComponent(activityType)}/${encodeURIComponent(activityId)}/observations`,
  ).then(res => res.data);
};

export const getLatestFanxiuExchangeActivitySnapshot = (activityTypes: string[]) => {
  return api.get<FanxiuLatestExchangeActivitySnapshot>(
    '/fanxiu/activity-list/latest-exchange-event',
    { params: { activity_types: activityTypes.join(',') } },
  ).then(res => res.data);
};

export const getFanxiuScheduleRankings = () => {
  return api.get<FanxiuScheduleRankingSnapshot>(
    '/fanxiu/schedule/rankings',
  ).then(res => res.data);
};

export const saveFanxiuExchangeActivityPriorities = (
  activityType: string,
  activityId: string,
  orderedGoodsIds: number[],
) => {
  return api.put<FanxiuExchangeActivityDetail>(
    `/fanxiu/activity-list/exchange-events/${encodeURIComponent(activityType)}/${encodeURIComponent(activityId)}/priorities`,
    { ordered_goods_ids: orderedGoodsIds },
  ).then(res => res.data);
};

export const planFanxiuExchangeActivityShop = (
  activityType: string,
  activityId: string,
) => {
  return api.post<FanxiuExchangeActivityDetail>(
    `/fanxiu/activity-list/exchange-events/${encodeURIComponent(activityType)}/${encodeURIComponent(activityId)}/plan`,
  ).then(res => res.data);
};

export const saveFanxiuExchangeActivityShopItemLock = (
  activityType: string,
  activityId: string,
  goodsId: number,
  locked: boolean,
) => {
  return api.put<FanxiuExchangeActivityDetail>(
    `/fanxiu/activity-list/exchange-events/${encodeURIComponent(activityType)}/${encodeURIComponent(activityId)}/shop-items/${goodsId}/lock`,
    { locked },
  ).then(res => res.data);
};

export const getFanxiuExchangeActivityRankings = (
  activityType: string,
  activityId: string,
  page = 1,
  pageSize = 20,
  rankingScope: string = 'personal',
) => {
  return api.get<FanxiuExchangeRankingPage>(
    `/fanxiu/activity-list/exchange-events/${encodeURIComponent(activityType)}/${encodeURIComponent(activityId)}/rankings`,
    { params: { page, page_size: pageSize, ranking_scope: rankingScope } },
  ).then(res => res.data);
};

export const getFanxiuExchangeActivityTasks = (
  activityType: string,
  activityId: string,
) => {
  return api.get<FanxiuExchangeActivityTaskSnapshot>(
    `/fanxiu/activity-list/exchange-events/${encodeURIComponent(activityType)}/${encodeURIComponent(activityId)}/tasks`,
  ).then(res => res.data);
};

export const getFanxiuYaochiFlowerFestivalTasks = (activityId: string) => {
  return api.get<{ items: FanxiuYaochiFlowerTaskMilestone[] }>(
    `/fanxiu/activity-list/yaochi-flower-festival/${encodeURIComponent(activityId)}/tasks`,
  ).then(res => res.data);
};

export const getFanxiuYuandingSanshengTasks = (activityId: string) => {
  return api.get<{ items: FanxiuYuandingSanshengTaskMilestone[] }>(
    `/fanxiu/activity-list/yuanding-sansheng/${encodeURIComponent(activityId)}/tasks`,
  ).then(res => res.data);
};

export const getFanxiuLingchongJingwuTasks = (activityId: string) => {
  return api.get<FanxiuLingchongJingwuTaskSnapshot>(
    `/fanxiu/activity-list/lingchong-jingwu/${encodeURIComponent(activityId)}/tasks`,
  ).then(res => res.data);
};

export const getFanxiuLingchongJingwuResources = (activityId: string) => {
  return api.get<FanxiuLingchongJingwuResourceSnapshot>(
    `/fanxiu/activity-list/lingchong-jingwu/${encodeURIComponent(activityId)}/resources`,
  ).then(res => res.data);
};

export const collectFanxiuLingchongJingwuResources = (activityId: string) => {
  return api.post<FanxiuLingchongJingwuResourceSnapshot>(
    `/fanxiu/activity-list/lingchong-jingwu/${encodeURIComponent(activityId)}/resources/collect`,
    {},
    { timeout: 120000 },
  ).then(res => res.data);
};

export const getFanxiuYaochiFlowerResources = () => {
  return api.get<FanxiuYaochiFlowerResourceSnapshot>(
    '/fanxiu/activity-list/yaochi-flower-festival/resources',
  ).then(res => res.data);
};

export const collectFanxiuYaochiFlowerResources = (activityId: string) => {
  return api.post<FanxiuYaochiFlowerResourceSnapshot>(
    `/fanxiu/activity-list/yaochi-flower-festival/${encodeURIComponent(activityId)}/resources/collect`,
    {},
    { timeout: 120000 },
  ).then(res => res.data);
};

export const collectFanxiuExchangeActivity = (
  activityType: string,
  activityId: string,
) => {
  return api.post<FanxiuExchangeActivityDetail>(
    `/fanxiu/activity-list/exchange-events/${encodeURIComponent(activityType)}/${encodeURIComponent(activityId)}/collect`,
    {},
    { timeout: 120000 },
  ).then(res => res.data);
};

export const getFanxiuActivityNote = (itemId: string) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote }) => (
    api
      .get<NoteNode | null>(`/fanxiu/activity-notes/${encodeURIComponent(itemId)}`)
      .then(res => (res.data ? normalizeFanxiuNote(res.data) : null))
  ));
};

export const saveFanxiuActivityNote = (itemId: string, data: Partial<NoteNode>) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote, toFanxiuPayload }) => (
    api
      .put<NoteNode>(`/fanxiu/activity-notes/${encodeURIComponent(itemId)}`, toFanxiuPayload(data))
      .then(res => normalizeFanxiuNote(res.data))
  ));
};

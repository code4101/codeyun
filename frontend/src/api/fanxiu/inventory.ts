/** 收藏图鉴与笔记的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';
import type { NoteNode } from '../notes';
import type { FanxiuGameRichTextSegment } from './catalogCommon';
import { loadFanxiuNoteHelpers } from './noteAccess';


export type FanxiuInventoryType = '' | '攻击' | '防御' | '灵力' | '辅助';
export type FanxiuMagicTreasureCategory = '法宝' | '先天古宝' | '后天古宝';

export interface FanxiuInventoryItem {
  id: string;
  name: string;
  category?: string;
  rank: number;
  shenlian: number;
  type: FanxiuInventoryType;
  quality: number | null;
  main_use: string;
  acquisition: string;
  date: string;
  note_id?: string | null;
}

export interface FanxiuWardrobeItem extends FanxiuInventoryItem {
  fashion_id: number;
  item_id: number;
  owned: boolean;
  category: string;
  type_id: number;
  max_level: number;
  show_max_level: number;
  is_max_level: boolean;
  is_forever: boolean;
  dress: boolean;
  condition: string;
  knowledge_source: 'item_catalog' | 'runtime_memory';
  catalog_icon: string;
  catalog_description: string;
  catalog_effect_description: string;
  catalog_quality_name: string;
  catalog_quality_color: string;
}
export type FanxiuInventorySectionSnapshot = Record<string, FanxiuInventoryItem[]>;

export interface FanxiuWardrobeHallSnapshot {
  shizhuang: FanxiuWardrobeItem[];
  wuqi: FanxiuWardrobeItem[];
  huanshen: FanxiuWardrobeItem[];
  beishi: FanxiuWardrobeItem[];
  yuqi: FanxiuWardrobeItem[];
  runtime_source: string;
  runtime_complete: boolean;
  runtime_error: string;
  runtime_updated_at: number;
  runtime_item_count: number;
  runtime_owned_count: number;
  runtime_debug: Record<string, unknown>;
}

export interface FanxiuSpiritBeastHallSnapshot {
  lingshou: FanxiuInventoryItem[];
  shengshou: FanxiuInventoryItem[];
}

export interface FanxiuMagicTreasureGradient {
  pin: number;
  level: number;
  pin_label: string;
  unlock_label: string;
  skill_name: string;
  summary_description: string;
  summary_segments: FanxiuGameRichTextSegment[];
  effect_description: string;
  effect_segments: FanxiuGameRichTextSegment[];
  schedule_description: string;
  schedule_segments: FanxiuGameRichTextSegment[];
  active: boolean;
  current: boolean;
}

export interface FanxiuMagicTreasureUpgradeEffect {
  stage: number;
  description: string;
  segments: FanxiuGameRichTextSegment[];
  unlocked: boolean;
  current: boolean;
}

export interface FanxiuMagicTreasureItem extends FanxiuInventoryItem {
  talisman_id: number;
  owned: boolean;
  category: FanxiuMagicTreasureCategory;
  wujing_level: number;
  mix_level: number;
  bind_id: number;
  num: number;
  knowledge_source: 'item_catalog' | 'runtime_memory';
  catalog_item_id?: number | null;
  catalog_name: string;
  catalog_icon: string;
  catalog_description: string;
  catalog_effect_description: string;
  catalog_quality?: number | null;
  catalog_quality_name: string;
  catalog_quality_color: string;
  catalog_refine_item_id?: number | null;
  catalog_refine_name: string;
  original_effect: string;
  upgrade_effects: FanxiuMagicTreasureUpgradeEffect[];
  shenlian_effect: string;
  shenlian_effect_segments: FanxiuGameRichTextSegment[];
  shenlian_schedule: string;
  shenlian_schedule_segments: FanxiuGameRichTextSegment[];
  shenlian_pin: number;
  shenlian_pin_label: string;
  shenlian_progress_nodes: number;
  shenlian_remaining_nodes: number;
  shenlian_next_pin: number;
  shenlian_next_level: number;
  shenlian_next_label: string;
  shenlian_next_skill_name: string;
  shenlian_max_pin: number;
  shenlian_gradients: FanxiuMagicTreasureGradient[];
}

export interface FanxiuMagicTreasureHallSnapshot {
  fabao: FanxiuMagicTreasureItem[];
  xiantiangubao: FanxiuMagicTreasureItem[];
  houtiangubao: FanxiuMagicTreasureItem[];
  runtime_source: string;
  runtime_complete: boolean;
  runtime_error: string;
  runtime_updated_at: number;
  runtime_item_count: number;
  runtime_debug: Record<string, unknown>;
}

export interface FanxiuSpiritArtifactPartRow {
  runtime_observation?: Record<string, unknown>;
  basic_scores?: Record<string, number>;
  runtime_empty_slot?: boolean | null;
  stage?: string;
  order: number;
  part_name: string;
  rank: number;
  realm: number;
  artifact_peerless_1: number;
  artifact_peerless_2: number;
  aura_peerless?: number;
  chaos_power: string;
  attack: string;
  stat_raw_values: Record<string, string>;
  exclusive_stats: Record<string, string>;
  exclusive_stat_raw_values: Record<string, string>;
  spirit_power: string;
  health: string;
  defense: string;
  runtime_base_id: number;
  runtime_item_id: string;
  runtime_ware_id: number;
  runtime_part: number;
  runtime_refine_num: number;
  runtime_is_break: boolean | null;
  a_codes?: string[];
  runtime_effects: FanxiuSpiritArtifactRuntimeEffect[];
}

export interface FanxiuSpiritArtifactRuntimeEffect {
  cleanse_id: number;
  value: number;
  base_value: number;
  add_value: number;
  quality: number;
  locked: boolean;
  name: string;
  official_name: string;
  code: string;
  type: number;
  attribute_id: string;
  attribute_name: string;
  projection: string;
  projection_base_value: number;
  percent: string;
}

export interface FanxiuSpiritArtifactItem {
  core_attributes?: string[];
  order: number;
  name: string;
  rows: FanxiuSpiritArtifactPartRow[];
}

export interface FanxiuSpiritArtifactMarketItem {
  order: number;
  artifact_name: string;
  part_name: string;
  cost: number;
}

export interface FanxiuSpiritArtifactStorageBagChoice {
  order: number;
  raw_name: string;
  artifact_name: string;
  part_name: string;
}

export interface FanxiuSpiritArtifactStorageBagItem {
  order: number;
  title: string;
  quantity: number;
  choices: FanxiuSpiritArtifactStorageBagChoice[];
}

export interface FanxiuSpiritArtifactHallSnapshot {
  runtime_observation_scope?: 'full' | 'mixed' | string;
  runtime_partial_updated_at?: number;
  artifacts: FanxiuSpiritArtifactItem[];
  market_currency_count: number;
  market_items: FanxiuSpiritArtifactMarketItem[];
  storage_bag_items: FanxiuSpiritArtifactStorageBagItem[];
  runtime_source: string;
  runtime_complete: boolean;
  runtime_error: string;
  runtime_updated_at: number;
  runtime_item_count: number;
  runtime_equipped_count: number;
  runtime_debug: Record<string, unknown>;
}

export const getFanxiuWardrobeHall = () => {
  return api.get<FanxiuWardrobeHallSnapshot>('/fanxiu/inventory/wardrobe-hall').then(res => res.data);
};

export const saveFanxiuWardrobeHall = (payload: FanxiuWardrobeHallSnapshot) => {
  return api.put<FanxiuWardrobeHallSnapshot>('/fanxiu/inventory/wardrobe-hall', payload).then(res => res.data);
};

export const getFanxiuWardrobeNote = (itemId: string) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote }) => (
    api
      .get<NoteNode | null>(`/fanxiu/inventory/wardrobe-notes/${encodeURIComponent(itemId)}`)
      .then(res => (res.data ? normalizeFanxiuNote(res.data) : null))
  ));
};

export const saveFanxiuWardrobeNote = (itemId: string, data: Partial<NoteNode>) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote, toFanxiuPayload }) => (
    api
      .put<NoteNode>(`/fanxiu/inventory/wardrobe-notes/${encodeURIComponent(itemId)}`, toFanxiuPayload(data))
      .then(res => normalizeFanxiuNote(res.data))
  ));
};

export const getFanxiuSpiritBeastHall = () => {
  return api.get<FanxiuSpiritBeastHallSnapshot>('/fanxiu/inventory/spirit-beast-hall').then(res => res.data);
};

export const saveFanxiuSpiritBeastHall = (payload: FanxiuSpiritBeastHallSnapshot) => {
  return api.put<FanxiuSpiritBeastHallSnapshot>('/fanxiu/inventory/spirit-beast-hall', payload).then(res => res.data);
};

export const getFanxiuSpiritBeastNote = (itemId: string) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote }) => (
    api
      .get<NoteNode | null>(`/fanxiu/inventory/spirit-beast-notes/${encodeURIComponent(itemId)}`)
      .then(res => (res.data ? normalizeFanxiuNote(res.data) : null))
  ));
};

export const saveFanxiuSpiritBeastNote = (itemId: string, data: Partial<NoteNode>) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote, toFanxiuPayload }) => (
    api
      .put<NoteNode>(`/fanxiu/inventory/spirit-beast-notes/${encodeURIComponent(itemId)}`, toFanxiuPayload(data))
      .then(res => normalizeFanxiuNote(res.data))
  ));
};

export const getFanxiuMagicTreasureHall = () => {
  return api.get<FanxiuMagicTreasureHallSnapshot>('/fanxiu/inventory/magic-treasure-hall').then(res => res.data);
};

export const saveFanxiuMagicTreasureHall = (payload: FanxiuMagicTreasureHallSnapshot) => {
  return api.put<FanxiuMagicTreasureHallSnapshot>('/fanxiu/inventory/magic-treasure-hall', payload).then(res => res.data);
};

export const getFanxiuSpiritArtifactHall = () => {
  return api.get<FanxiuSpiritArtifactHallSnapshot>('/fanxiu/inventory/spirit-artifact-hall').then(res => res.data);
};

export const syncFanxiuSpiritArtifactStorageBag = () => api
  .post<FanxiuSpiritArtifactHallSnapshot>('/fanxiu/inventory/spirit-artifact-storage-bag/sync', null, { timeout: 120000 })
  .then(res => res.data);

export const collectFanxiuWardrobeHall = () => {
  return api
    .post<FanxiuWardrobeHallSnapshot>('/fanxiu/inventory/wardrobe-hall/collect', null, {
      timeout: 125000,
    })
    .then(res => res.data);
};

export const collectFanxiuMagicTreasureHall = () => {
  return api
    .post<FanxiuMagicTreasureHallSnapshot>('/fanxiu/inventory/magic-treasure-hall/collect', null, {
      timeout: 125000,
    })
    .then(res => res.data);
};

export const saveFanxiuSpiritArtifactHall = (payload: FanxiuSpiritArtifactHallSnapshot) => {
  return api.put<FanxiuSpiritArtifactHallSnapshot>('/fanxiu/inventory/spirit-artifact-hall', payload).then(res => res.data);
};

export const getFanxiuMagicTreasureNote = (itemId: string) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote }) => (
    api
      .get<NoteNode | null>(`/fanxiu/inventory/magic-treasure-notes/${encodeURIComponent(itemId)}`)
      .then(res => (res.data ? normalizeFanxiuNote(res.data) : null))
  ));
};

export const saveFanxiuMagicTreasureNote = (itemId: string, data: Partial<NoteNode>) => {
  return loadFanxiuNoteHelpers().then(({ normalizeFanxiuNote, toFanxiuPayload }) => (
    api
      .put<NoteNode>(`/fanxiu/inventory/magic-treasure-notes/${encodeURIComponent(itemId)}`, toFanxiuPayload(data))
      .then(res => normalizeFanxiuNote(res.data))
  ));
};

import api from '@/api';
import type { NoteNode } from './notes';
import {
  deriveLegacySemanticsFromTaxonomy,
  deriveNoteTaxonomyFromLegacy,
  NOTE_CATEGORY_DEFAULT,
  NOTE_FORM_DEFAULT,
  NOTE_KIND_DEFAULT,
  NOTE_LIFECYCLE_STAGE_DEFAULT,
  NOTE_SCENE_DEFAULT
} from '@/utils/noteSemantics';
import { createEffectiveNoteTypes } from '@/utils/nodeConfig';

export interface FanxiuStatusConfig {
  status_path: string | null;
  auto_detected_path: string | null;
  effective_path: string | null;
  mode: 'configured' | 'auto' | 'unset';
  file_exists: boolean;
}

export interface FanxiuTaskStatusItem {
  name: string;
  scheduled_at: string;
  due: boolean;
  seconds_until_due: number;
  is_next: boolean;
}

export interface FanxiuAccountStatusItem {
  name: string;
  phone: string | null;
  is_current: boolean;
  has_due_task: boolean;
  due_count: number;
  task_count: number;
  next_task_name: string | null;
  next_task_at: string | null;
  tasks: FanxiuTaskStatusItem[];
}

export interface FanxiuRuntimeTimerItem {
  name: string;
  scheduled_at: string;
  due: boolean;
  seconds_until_due: number;
}

export interface FanxiuStatusSnapshot extends FanxiuStatusConfig {
  loaded_at: string;
  error: string | null;
  current_account: string | null;
  recommended_account: string | null;
  next_task_path: string | null;
  next_task_name: string | null;
  next_task_at: string | null;
  next_task_seconds_until_due: number | null;
  program_initialized: boolean;
  all_tasks_completed: boolean;
  watchdog_hash: string | null;
  runtime_timers: FanxiuRuntimeTimerItem[];
  accounts: FanxiuAccountStatusItem[];
  raw_status?: Record<string, unknown> | null;
}

export interface FanxiuProcessItem {
  pid: number;
  parent_pid: number | null;
  name: string;
  command_line: string;
  created_at: string | null;
  matched_reason: string;
}

export interface FanxiuProcessListResponse {
  items: FanxiuProcessItem[];
}

export interface LocalScriptProcessItem {
  pid: number;
  parent_pid: number | null;
  name: string;
  kind: string;
  script: string;
  script_path: string | null;
  command_line: string;
  cwd: string | null;
  created_at: string | null;
  runtime_seconds: number | null;
  project_hint: string;
  is_fanxiu: boolean;
}

export interface LocalScriptProcessListResponse {
  items: LocalScriptProcessItem[];
}

export interface FanxiuProcessTerminateResponse {
  matched: FanxiuProcessItem[];
  terminated: FanxiuProcessItem[];
  remaining: FanxiuProcessItem[];
  errors: Array<{ pid: number; error: string }>;
}

export interface FanxiuBehaviorTreeServiceStatus {
  key: string;
  title: string;
  running: boolean;
  state: string;
  state_label: string;
  pid: number | null;
  process_count: number;
  processes: FanxiuProcessItem[];
  registry: Record<string, unknown>;
  registry_pid_alive: boolean;
  heartbeat_age_seconds: number | null;
  started_at: string | null;
  heartbeat_at: string | null;
  last_error: string;
  root: string;
  registry_path: string;
  status_path: string;
  behavior_tree_log_path: string;
  service_log_path: string;
  script_path: string;
  python_path: string;
}

export interface FanxiuBehaviorTreeServiceResponse {
  status: string;
  service: FanxiuBehaviorTreeServiceStatus;
  pid?: number | null;
  stop_result?: FanxiuProcessTerminateResponse | Record<string, unknown>;
}

export interface FanxiuSunloginRotateStatus {
  running: boolean;
  pids: number[];
  primary_pid: number | null;
  started_at: string | null;
  runtime_seconds: number | null;
  command_line: string;
  target_title: string;
  preview_title: string;
  stdout_log: string;
  stderr_log: string;
  last_error: string;
  errors?: Array<{ pid: number; error: string }>;
}

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

export type FanxiuWardrobeItem = FanxiuInventoryItem;
export type FanxiuInventorySectionSnapshot = Record<string, FanxiuInventoryItem[]>;

export interface FanxiuWardrobeHallSnapshot {
  shizhuang: FanxiuInventoryItem[];
  wuqi: FanxiuInventoryItem[];
  huanshen: FanxiuInventoryItem[];
  beishi: FanxiuInventoryItem[];
  yuqi: FanxiuInventoryItem[];
}

export interface FanxiuSpiritBeastHallSnapshot {
  lingshou: FanxiuInventoryItem[];
  shengshou: FanxiuInventoryItem[];
}

export interface FanxiuMagicTreasureHallSnapshot {
  fabao: FanxiuInventoryItem[];
  xiantiangubao: FanxiuInventoryItem[];
  houtiangubao: FanxiuInventoryItem[];
}

export interface FanxiuSpiritArtifactPartRow {
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
}

export interface FanxiuSpiritArtifactItem {
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
  artifacts: FanxiuSpiritArtifactItem[];
  market_currency_count: number;
  market_items: FanxiuSpiritArtifactMarketItem[];
  storage_bag_items: FanxiuSpiritArtifactStorageBagItem[];
}

export interface FanxiuMagicTreasureOcrImportResponse {
  section_key: string;
  lines: string[];
  item: FanxiuInventoryItem;
}

export interface FanxiuSpiritArtifactRankPart {
  part_name: string;
  rank: number;
  realm: number;
  quality: string;
  background_color: string;
}

export interface FanxiuSpiritArtifactRankRecognitionResponse {
  matched: boolean;
  reason: string;
  artifact_name: string;
  title_text: string;
  lines: string[];
  parts: FanxiuSpiritArtifactRankPart[];
}

export interface FanxiuSpiritArtifactAttributeRecognitionItem {
  label: string;
  percent: string;
  raw_value: string;
  source_text: string;
}

export interface FanxiuSpiritArtifactAttributeRecognitionResponse {
  matched: boolean;
  reason: string;
  artifact_name: string;
  part_name: string;
  title_text: string;
  lines: string[];
  artifact_peerless_1: number;
  artifact_peerless_2: number;
  common_stats: Record<string, string>;
  exclusive_stats: Record<string, string>;
  attributes: FanxiuSpiritArtifactAttributeRecognitionItem[];
}

export interface FanxiuSpiritArtifactMarketRecognitionResponse {
  matched: boolean;
  reason: string;
  market_currency_count: number;
  lines: string[];
  items: FanxiuSpiritArtifactMarketItem[];
}

export interface FanxiuSpiritArtifactStorageBagRecognitionResponse {
  matched: boolean;
  reason: string;
  lines: string[];
  items: FanxiuSpiritArtifactStorageBagItem[];
}

export interface FanxiuFormationRequirementImportItem {
  text: string;
  effect_text: string;
}

export interface FanxiuFormationEffectDetailImportItem {
  effect_name: string;
  effect_detail: string;
}

export interface FanxiuFormationRequirementOcrImportResponse {
  lines: string[];
  requirements: FanxiuFormationRequirementImportItem[];
  effect_details: FanxiuFormationEffectDetailImportItem[];
}

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

export interface FanxiuRegionCharacterItem {
  id: string;
  region_name: string;
  server_name: string;
  guild_name: string;
  role_name: string;
  attack: string;
  cultivation_level: string;
  recorded_date: string;
  disabled?: boolean;
  created_at?: number;
  updated_at?: number;
  disabled_at?: number | null;
}

export interface FanxiuRegionCharacterSnapshot {
  characters: FanxiuRegionCharacterItem[];
}

export interface FanxiuRegionCharacterUpdate {
  guild_name?: string;
  role_name?: string;
  attack?: string;
  cultivation_level?: string;
  recorded_date?: string;
  disabled?: boolean;
}

export interface FanxiuRegionServerItem {
  id: string;
  region_name: string;
  order: number;
  name: string;
  open_date: string;
  mark_type?: string;
  mark_label?: string;
  mark_title?: string;
}

export interface FanxiuRegionAreaItem {
  id: string;
  number: number;
  name: string;
  start_date: string;
  end_date: string;
  known_count: number;
  servers: FanxiuRegionServerItem[];
}

export interface FanxiuRegionDataSnapshot {
  regions: FanxiuRegionAreaItem[];
}

export interface FanxiuRegionServerCandidate {
  region_name: string;
  server_name: string;
}

export interface FanxiuModaoInvasionExchangeItem {
  id: string;
  name: string;
  magic_crystal_cost: number;
  purchase_limit: number;
  checked: boolean;
}

export interface FanxiuModaoInvasionPersonalRankingItem {
  id: string;
  rank: number;
  name: string;
  plane: string;
  merit: number;
}

export interface FanxiuShouyuanExplorationIncomeSpeedItem {
  id: string;
  captured_date: string;
  search_count: number;
  beast_crystal: number;
  score: number;
  merit: number;
  remark: string;
}

export interface FanxiuShouyuanExplorationConsumptionEvaluationItem {
  id: string;
  label: string;
  current: number;
  target: number;
  speed: number;
}

export interface FanxiuModaoInvasionRecord {
  id: string;
  activity_id: string;
  label: string;
  personal_rankings: FanxiuModaoInvasionPersonalRankingItem[];
  items: FanxiuModaoInvasionExchangeItem[];
}

export interface FanxiuModaoInvasionSnapshot {
  records: FanxiuModaoInvasionRecord[];
}

export type FanxiuShouyuanExplorationExchangeItem = FanxiuModaoInvasionExchangeItem;
export type FanxiuShouyuanExplorationPersonalRankingItem = FanxiuModaoInvasionPersonalRankingItem;

export interface FanxiuShouyuanExplorationRecord {
  id: string;
  activity_id: string;
  label: string;
  personal_rankings: FanxiuShouyuanExplorationPersonalRankingItem[];
  income_speeds: FanxiuShouyuanExplorationIncomeSpeedItem[];
  consumption_evaluations: FanxiuShouyuanExplorationConsumptionEvaluationItem[];
  items: FanxiuShouyuanExplorationExchangeItem[];
}

export interface FanxiuShouyuanExplorationSnapshot {
  records: FanxiuShouyuanExplorationRecord[];
}

export interface FanxiuModaoInvasionOcrImportResponse {
  lines: string[];
  items: FanxiuModaoInvasionExchangeItem[];
}

export interface FanxiuModaoInvasionPersonalRankingOcrImportResponse {
  lines: string[];
  items: FanxiuModaoInvasionPersonalRankingItem[];
}

export interface FanxiuShouyuanExplorationOcrImportResponse {
  lines: string[];
  items: FanxiuShouyuanExplorationExchangeItem[];
}

export interface FanxiuShouyuanExplorationPersonalRankingOcrImportResponse {
  lines: string[];
  items: FanxiuShouyuanExplorationPersonalRankingItem[];
}

export interface FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse {
  lines: string[];
  item: FanxiuShouyuanExplorationIncomeSpeedItem;
}

export interface FanxiuRegionCharacterOcrImportResponse {
  lines: string[];
  item: FanxiuRegionCharacterItem;
  created?: boolean;
  skipped_reason?: string;
}

const normalizeFanxiuNote = (raw: any): NoteNode => {
  const normalizeTimestamp = (value: unknown) => {
    const numeric = typeof value === 'number' ? value : Number(value ?? 0);
    if (!Number.isFinite(numeric)) return 0;
    return numeric < 10000000000 ? numeric * 1000 : numeric;
  };

  const taxonomy = Array.isArray(raw.note_categories) || raw.primary_category || raw.note_form || raw.note_scene || raw.lifecycle_stage
    ? deriveLegacySemanticsFromTaxonomy(
      raw.note_categories,
      raw.primary_category ?? NOTE_CATEGORY_DEFAULT,
      raw.note_form ?? NOTE_FORM_DEFAULT,
      raw.note_scene ?? raw.note_kind ?? NOTE_SCENE_DEFAULT,
      raw.lifecycle_stage ?? raw.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
    )
    : {
      ...deriveNoteTaxonomyFromLegacy(
        raw.note_types,
        raw.node_type ?? 'memo',
        raw.note_kind ?? NOTE_SCENE_DEFAULT,
        raw.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
      ),
      note_types: raw.note_types,
      node_type: raw.node_type ?? 'memo',
      note_kind: raw.note_kind ?? NOTE_KIND_DEFAULT,
      node_status: raw.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
    };

  return {
    ...raw,
    id: Number.isFinite(Number(raw.id)) ? Math.trunc(Number(raw.id)) : 0,
    numeric_id: raw.numeric_id == null ? null : Number(raw.numeric_id),
    created_at: normalizeTimestamp(raw.created_at),
    updated_at: normalizeTimestamp(raw.updated_at),
    start_at: normalizeTimestamp(raw.start_at),
    note_types: createEffectiveNoteTypes(taxonomy.note_types ?? raw.note_types, taxonomy.node_type ?? raw.node_type ?? 'memo', raw.color ?? null),
    note_categories: taxonomy.note_categories,
    primary_category: taxonomy.primary_category,
    note_form: taxonomy.note_form,
    note_kind: taxonomy.note_kind ?? raw.note_kind ?? NOTE_KIND_DEFAULT,
    note_scene: taxonomy.note_scene,
    node_status: taxonomy.node_status ?? raw.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT,
    lifecycle_stage: taxonomy.lifecycle_stage,
    weight_mode: raw.weight_mode ?? null,
    private_level: typeof raw.private_level === 'number' ? raw.private_level : Number(raw.private_level ?? 0),
    can_edit: Boolean(raw.can_edit)
  };
};

const toFanxiuPayload = (data: Partial<NoteNode>) => {
  const payload: Record<string, any> = { ...data };
  if (typeof payload.start_at === 'number' && payload.start_at > 10000000000) {
    payload.start_at /= 1000;
  }
  return payload;
};

export const getFanxiuChars = () => {
  return api.get<NoteNode[]>('/fanxiu/chars').then(res => (res.data || []).map(normalizeFanxiuNote));
};

export const getFanxiuCharDetail = (charName: string) => {
  return api.get<NoteNode>(`/fanxiu/chars/${charName}`).then(res => normalizeFanxiuNote(res.data));
};

export const updateFanxiuChar = (charName: string, data: Partial<NoteNode>) => {
  return api.put<NoteNode>(`/fanxiu/chars/${charName}`, toFanxiuPayload(data)).then(res => normalizeFanxiuNote(res.data));
};

export const getFanxiuStatusConfig = () => {
  return api.get<FanxiuStatusConfig>('/fanxiu/status/config').then(res => res.data);
};

export const updateFanxiuStatusConfig = (statusPath: string | null) => {
  return api.put<FanxiuStatusConfig>('/fanxiu/status/config', { status_path: statusPath }).then(res => res.data);
};

export const getFanxiuStatus = () => {
  return api.get<FanxiuStatusSnapshot>('/fanxiu/status').then(res => res.data);
};

export const parseFanxiuStatus = (rawStatus: Record<string, unknown>) => {
  return api.post<FanxiuStatusSnapshot>('/fanxiu/status/parse', { raw_status: rawStatus }).then(res => res.data);
};

export const saveFanxiuStatus = (rawStatus: Record<string, unknown>) => {
  return api.put<FanxiuStatusSnapshot>('/fanxiu/status', { raw_status: rawStatus }).then(res => res.data);
};

export const getFanxiuProcesses = () => {
  return api.get<FanxiuProcessListResponse>('/fanxiu/processes').then(res => res.data);
};

export const getFanxiuBehaviorTreeService = () => {
  return api.get<FanxiuBehaviorTreeServiceStatus>('/fanxiu/behavior-tree-service').then(res => res.data);
};

export const startFanxiuBehaviorTreeService = () => {
  return api.post<FanxiuBehaviorTreeServiceResponse>('/fanxiu/behavior-tree-service/start').then(res => res.data);
};

export const stopFanxiuBehaviorTreeService = () => {
  return api.post<FanxiuBehaviorTreeServiceResponse>('/fanxiu/behavior-tree-service/stop').then(res => res.data);
};

export const getLocalScriptProcesses = () => {
  return api.get<LocalScriptProcessListResponse>('/fanxiu/scripts').then(res => res.data);
};

export const terminateFanxiuProcesses = () => {
  return api.post<FanxiuProcessTerminateResponse>('/fanxiu/processes/terminate').then(res => res.data);
};

export const getFanxiuSunloginRotateStatus = () => {
  return api.get<FanxiuSunloginRotateStatus>('/fanxiu/sunlogin-rotate').then(res => res.data);
};

export const startFanxiuSunloginRotate = () => {
  return api.post<FanxiuSunloginRotateStatus>('/fanxiu/sunlogin-rotate/start').then(res => res.data);
};

export const stopFanxiuSunloginRotate = () => {
  return api.post<FanxiuSunloginRotateStatus>('/fanxiu/sunlogin-rotate/stop').then(res => res.data);
};

export const getFanxiuWardrobeHall = () => {
  return api.get<FanxiuWardrobeHallSnapshot>('/fanxiu/inventory/wardrobe-hall').then(res => res.data);
};

export const saveFanxiuWardrobeHall = (payload: FanxiuWardrobeHallSnapshot) => {
  return api.put<FanxiuWardrobeHallSnapshot>('/fanxiu/inventory/wardrobe-hall', payload).then(res => res.data);
};

export const getFanxiuWardrobeNote = (itemId: string) => {
  return api
    .get<NoteNode | null>(`/fanxiu/inventory/wardrobe-notes/${encodeURIComponent(itemId)}`)
    .then(res => (res.data ? normalizeFanxiuNote(res.data) : null));
};

export const saveFanxiuWardrobeNote = (itemId: string, data: Partial<NoteNode>) => {
  return api
    .put<NoteNode>(`/fanxiu/inventory/wardrobe-notes/${encodeURIComponent(itemId)}`, toFanxiuPayload(data))
    .then(res => normalizeFanxiuNote(res.data));
};

export const getFanxiuSpiritBeastHall = () => {
  return api.get<FanxiuSpiritBeastHallSnapshot>('/fanxiu/inventory/spirit-beast-hall').then(res => res.data);
};

export const saveFanxiuSpiritBeastHall = (payload: FanxiuSpiritBeastHallSnapshot) => {
  return api.put<FanxiuSpiritBeastHallSnapshot>('/fanxiu/inventory/spirit-beast-hall', payload).then(res => res.data);
};

export const getFanxiuSpiritBeastNote = (itemId: string) => {
  return api
    .get<NoteNode | null>(`/fanxiu/inventory/spirit-beast-notes/${encodeURIComponent(itemId)}`)
    .then(res => (res.data ? normalizeFanxiuNote(res.data) : null));
};

export const saveFanxiuSpiritBeastNote = (itemId: string, data: Partial<NoteNode>) => {
  return api
    .put<NoteNode>(`/fanxiu/inventory/spirit-beast-notes/${encodeURIComponent(itemId)}`, toFanxiuPayload(data))
    .then(res => normalizeFanxiuNote(res.data));
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

export const saveFanxiuSpiritArtifactHall = (payload: FanxiuSpiritArtifactHallSnapshot) => {
  return api.put<FanxiuSpiritArtifactHallSnapshot>('/fanxiu/inventory/spirit-artifact-hall', payload).then(res => res.data);
};

export const recognizeFanxiuSpiritArtifactMarket = () => {
  return api
    .post<FanxiuSpiritArtifactMarketRecognitionResponse>('/fanxiu/inventory/spirit-artifact-market/recognize', null, {
      timeout: 120000,
    })
    .then(res => res.data);
};

export const recognizeFanxiuSpiritArtifactStorageBag = () => {
  return api
    .post<FanxiuSpiritArtifactStorageBagRecognitionResponse>(
      '/fanxiu/inventory/spirit-artifact-storage-bag/recognize',
      null,
      {
        timeout: 120000,
      },
    )
    .then(res => res.data);
};

export const getFanxiuMagicTreasureNote = (itemId: string) => {
  return api
    .get<NoteNode | null>(`/fanxiu/inventory/magic-treasure-notes/${encodeURIComponent(itemId)}`)
    .then(res => (res.data ? normalizeFanxiuNote(res.data) : null));
};

export const saveFanxiuMagicTreasureNote = (itemId: string, data: Partial<NoteNode>) => {
  return api
    .put<NoteNode>(`/fanxiu/inventory/magic-treasure-notes/${encodeURIComponent(itemId)}`, toFanxiuPayload(data))
    .then(res => normalizeFanxiuNote(res.data));
};

export const importFanxiuMagicTreasureFromOcr = (sectionKey: string, image: File) => {
  const formData = new FormData();
  formData.append('section_key', sectionKey);
  formData.append('image', image);
  return api
    .post<FanxiuMagicTreasureOcrImportResponse>('/fanxiu/inventory/magic-treasure-import/ocr', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
    .then(res => res.data);
};

export const recognizeFanxiuSpiritArtifactRanks = () => {
  return api
    .post<FanxiuSpiritArtifactRankRecognitionResponse>('/fanxiu/inventory/spirit-artifact-ranks/recognize', null, {
      timeout: 120000,
    })
    .then(res => res.data);
};

export const recognizeFanxiuSpiritArtifactAttributes = () => {
  return api
    .post<FanxiuSpiritArtifactAttributeRecognitionResponse>('/fanxiu/inventory/spirit-artifact-attributes/recognize', null, {
      timeout: 120000,
    })
    .then(res => res.data);
};

export const importFanxiuFormationRequirementsFromOcr = (image: File) => {
  const formData = new FormData();
  formData.append('image', image);
  return api
    .post<FanxiuFormationRequirementOcrImportResponse>('/fanxiu/formations/requirements-import/ocr', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
    .then(res => res.data);
};

export const getFanxiuActivityList = () => {
  return api.get<FanxiuActivityListSnapshot>('/fanxiu/activity-list').then(res => res.data);
};

export const saveFanxiuActivityList = (payload: FanxiuActivityListSnapshot) => {
  return api.put<FanxiuActivityListSnapshot>('/fanxiu/activity-list', payload).then(res => res.data);
};

export const getFanxiuRegionData = () => {
  return api.get<FanxiuRegionDataSnapshot>('/fanxiu/region-data').then(res => res.data);
};

export const getFanxiuRegionCharacters = () => {
  return api.get<FanxiuRegionCharacterSnapshot>('/fanxiu/region-data/characters').then(res => res.data);
};

export const getFanxiuRegionCharacterHistory = (params: Partial<Pick<FanxiuRegionCharacterItem, 'region_name' | 'server_name' | 'guild_name' | 'role_name'>> & { include_disabled?: boolean } = {}) => {
  return api.get<FanxiuRegionCharacterSnapshot>('/fanxiu/region-data/characters/history', { params }).then(res => res.data);
};

export const updateFanxiuRegionCharacter = (characterId: string, payload: FanxiuRegionCharacterUpdate) => {
  return api.patch<FanxiuRegionCharacterItem>(`/fanxiu/region-data/characters/${characterId}`, payload).then(res => res.data);
};

export const disableFanxiuRegionCharacter = (characterId: string) => {
  return api.delete<FanxiuRegionCharacterItem>(`/fanxiu/region-data/characters/${characterId}`).then(res => res.data);
};

export const importFanxiuRegionCharacterFromOcr = (
  image: File,
  serverCandidates: FanxiuRegionServerCandidate[] = [],
  targetServer: FanxiuRegionServerCandidate | null = null,
) => {
  const formData = new FormData();
  formData.append('image', image);
  formData.append('server_candidates', JSON.stringify(serverCandidates));
  if (targetServer) {
    formData.append('target_region_name', targetServer.region_name);
    formData.append('target_server_name', targetServer.server_name);
  }
  return api
    .post<FanxiuRegionCharacterOcrImportResponse>('/fanxiu/region-data/characters/import/ocr', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
    .then(res => res.data);
};

export const getFanxiuModaoInvasionExchangeList = () => {
  return api.get<FanxiuModaoInvasionSnapshot>('/fanxiu/activity-list/modao-invasion').then(res => res.data);
};

export const saveFanxiuModaoInvasionExchangeList = (payload: FanxiuModaoInvasionSnapshot) => {
  return api.put<FanxiuModaoInvasionSnapshot>('/fanxiu/activity-list/modao-invasion', payload).then(res => res.data);
};

export const importFanxiuModaoInvasionExchangeListFromOcr = (image: File) => {
  const formData = new FormData();
  formData.append('image', image);
  return api
    .post<FanxiuModaoInvasionOcrImportResponse>('/fanxiu/activity-list/modao-invasion/import/ocr', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
    .then(res => res.data);
};

export const importFanxiuModaoInvasionPersonalRankingsFromOcr = (image: File) => {
  const formData = new FormData();
  formData.append('image', image);
  return api
    .post<FanxiuModaoInvasionPersonalRankingOcrImportResponse>('/fanxiu/activity-list/modao-invasion/personal-rankings/import/ocr', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
    .then(res => res.data);
};

export const getFanxiuShouyuanExplorationExchangeList = () => {
  return api.get<FanxiuShouyuanExplorationSnapshot>('/fanxiu/activity-list/shouyuan-exploration').then(res => res.data);
};

export const saveFanxiuShouyuanExplorationExchangeList = (payload: FanxiuShouyuanExplorationSnapshot) => {
  return api.put<FanxiuShouyuanExplorationSnapshot>('/fanxiu/activity-list/shouyuan-exploration', payload).then(res => res.data);
};

export const importFanxiuShouyuanExplorationExchangeListFromOcr = (image: File) => {
  const formData = new FormData();
  formData.append('image', image);
  return api
    .post<FanxiuShouyuanExplorationOcrImportResponse>('/fanxiu/activity-list/shouyuan-exploration/import/ocr', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
    .then(res => res.data);
};

export const importFanxiuShouyuanExplorationPersonalRankingsFromOcr = (image: File) => {
  const formData = new FormData();
  formData.append('image', image);
  return api
    .post<FanxiuShouyuanExplorationPersonalRankingOcrImportResponse>('/fanxiu/activity-list/shouyuan-exploration/personal-rankings/import/ocr', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
    .then(res => res.data);
};

export const importFanxiuShouyuanExplorationIncomeSpeedFromOcr = (image: File) => {
  const formData = new FormData();
  formData.append('image', image);
  return api
    .post<FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse>('/fanxiu/activity-list/shouyuan-exploration/income-speeds/import/ocr', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 120000,
    })
    .then(res => res.data);
};

export const getFanxiuActivityNote = (itemId: string) => {
  return api
    .get<NoteNode | null>(`/fanxiu/activity-notes/${encodeURIComponent(itemId)}`)
    .then(res => (res.data ? normalizeFanxiuNote(res.data) : null));
};

export const saveFanxiuActivityNote = (itemId: string, data: Partial<NoteNode>) => {
  return api
    .put<NoteNode>(`/fanxiu/activity-notes/${encodeURIComponent(itemId)}`, toFanxiuPayload(data))
    .then(res => normalizeFanxiuNote(res.data));
};

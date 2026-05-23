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

export interface FanxiuPacketCaptureAddress {
  ip: string;
  port: number;
  label: string;
}

export interface FanxiuPacketCaptureDnsMapping {
  host: string;
  ips: string[];
  error: string | null;
}

export interface FanxiuPacketCaptureProcess {
  pid: number;
  name: string;
  exe: string | null;
  command_line: string;
  group: string;
}

export interface FanxiuPacketCaptureConnection {
  pid: number;
  process_name: string;
  process_group: string;
  protocol: string;
  status: string;
  local: FanxiuPacketCaptureAddress | null;
  remote: FanxiuPacketCaptureAddress | null;
  mapped_hosts: string[];
  is_fake_ip: boolean;
  remote_scope: string;
  signal_score: number;
  signal_label: string;
  signal_reason: string;
}

export interface FanxiuPacketCaptureSnapshot {
  captured_at: string;
  dns_server: string;
  dns_mappings: FanxiuPacketCaptureDnsMapping[];
  processes: FanxiuPacketCaptureProcess[];
  connections: FanxiuPacketCaptureConnection[];
  listeners: FanxiuPacketCaptureConnection[];
  warnings: string[];
  summary: Record<string, number>;
}

export interface FanxiuPacketProxyStatus {
  running: boolean;
  host: string;
  port: number;
  addresses: string[];
  event_count: number;
  last_error: string;
}

export interface FanxiuAndroidProxyStatus {
  available: boolean;
  adb_path: string;
  device_id: string;
  devices: string[];
  http_proxy: string;
  enabled: boolean;
  target_proxy: string;
  matches_target: boolean;
  last_error: string;
}

export interface FanxiuPacketCaptureSessionStatus {
  active: boolean;
  target_proxy: string;
  proxy: FanxiuPacketProxyStatus;
  android: FanxiuAndroidProxyStatus;
  last_error: string;
}

export interface FanxiuPacketPayloadDirection {
  length: number;
  hex: string;
  ascii: string;
  text: string;
  printable_ratio: number;
  guess: string;
}

export interface FanxiuPacketPayloadPreview {
  up: FanxiuPacketPayloadDirection;
  down: FanxiuPacketPayloadDirection;
}

export interface FanxiuPacketActivityFlow {
  key: string;
  protocol: string;
  remote: FanxiuPacketCaptureAddress;
  packets_up: number;
  packets_down: number;
  bytes_up: number;
  bytes_down: number;
  payload_bytes_up: number;
  payload_bytes_down: number;
  payload_preview: FanxiuPacketPayloadPreview;
  first_seen: string;
  last_seen: string;
}

export interface FanxiuPacketActivityStatus {
  running: boolean;
  bind_ip: string;
  interfaces: string[];
  started_at: string;
  last_error: string;
  total_packets: number;
  total_bytes: number;
  history_total: number;
  history_capacity: number;
  items: FanxiuPacketActivityFlow[];
}

export interface FanxiuPacketActivityPayloadEvent {
  id: number;
  captured_at: string;
  key: string;
  protocol: string;
  remote: FanxiuPacketCaptureAddress;
  direction: string;
  packet_bytes: number;
  payload_bytes: number;
  payload_preview: FanxiuPacketPayloadDirection;
}

export interface FanxiuPacketActivityHistoryResponse {
  items: FanxiuPacketActivityPayloadEvent[];
  total: number;
  offset: number;
  limit: number;
  history_capacity: number;
}

export interface FanxiuPacketActivityStreamDirection {
  packet_count: number;
  payload_bytes: number;
  sampled_bytes: number;
  dropped_bytes: number;
  truncated_packets: number;
  first_seen: string;
  last_seen: string;
  preview: FanxiuPacketPayloadDirection;
}

export interface FanxiuPacketActivityStreamResponse {
  key: string;
  max_bytes: number;
  up: FanxiuPacketActivityStreamDirection;
  down: FanxiuPacketActivityStreamDirection;
}

export interface FanxiuPacketProxyEvent {
  id: number;
  timeline_id: string;
  source: string;
  source_label: string;
  started_at: string;
  finished_at: string | null;
  active: boolean;
  error: string;
  client: string;
  event_type: string;
  method: string;
  target: string;
  url: string;
  request_headers: string;
  request_body_text: string;
  request_body_hex: string;
  response_status: string;
  response_headers: string;
  response_body_text: string;
  response_body_hex: string;
  bytes_up: number;
  bytes_down: number;
  plaintext_state: string;
  semantic_role: string;
  signal_score: number;
  signal_label: string;
  signal_reason: string;
}

export interface FanxiuPacketProxyEventListResponse {
  items: FanxiuPacketProxyEvent[];
  status: FanxiuPacketProxyStatus;
}

export interface FanxiuPacketProxyTimelineResponse {
  items: FanxiuPacketProxyEvent[];
  status: FanxiuPacketProxyStatus;
  total: number;
  offset: number;
  limit: number;
  summary: Record<string, number>;
  log_directory: string;
}

export interface FanxiuPacketProxySaveResponse {
  saved_at: string;
  path: string;
  event_count: number;
  status: FanxiuPacketProxyStatus;
}

export interface FanxiuPacketProxyLogFile {
  name: string;
  path: string;
  size: number;
  modified_at: string;
  event_count: number;
}

export interface FanxiuPacketProxyLogListResponse {
  items: FanxiuPacketProxyLogFile[];
  directory: string;
}

export interface FanxiuPacketProxyLogLoadResponse {
  log: FanxiuPacketProxyLogFile;
  items: FanxiuPacketProxyEvent[];
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

export interface FanxiuGameWindow2StreamToken {
  token: string;
  expires_in_seconds: number;
}

export interface FanxiuGameWindow2ClickPayload {
  entry_id: string;
  x: number;
  y: number;
  title?: string;
  mode?: 'auto' | 'printwindow' | 'screen';
  area?: 'outer' | 'client';
  crop?: string;
  trim_border?: string;
  rotate?: '0' | '90' | '180' | '270' | 'ccw' | 'cw' | 'none';
  fixed_width?: number;
  fixed_height?: number;
  frame_width?: number;
  frame_height?: number;
}

export interface FanxiuGameWindow2DragPayload {
  entry_id: string;
  start_x: number;
  start_y: number;
  end_x: number;
  end_y: number;
  duration_ms?: number;
  title?: string;
  mode?: 'auto' | 'printwindow' | 'screen';
  area?: 'outer' | 'client';
  crop?: string;
  trim_border?: string;
  rotate?: '0' | '90' | '180' | '270' | 'ccw' | 'cw' | 'none';
  fixed_width?: number;
  fixed_height?: number;
  frame_width?: number;
  frame_height?: number;
}

export interface FanxiuGameWindow2SaveFramePayload {
  entry_id: string;
  title?: string;
  mode?: 'auto' | 'printwindow' | 'screen';
  area?: 'outer' | 'client';
  crop?: string;
  trim_border?: string;
  rotate?: '0' | '90' | '180' | '270' | 'ccw' | 'cw' | 'none';
  fixed_width?: number;
  fixed_height?: number;
  quality?: number;
}

export interface FanxiuGameWindow2SaveFrameResponse {
  ok: boolean;
  index: number;
  filename: string;
  path: string;
  directory: string;
  width: number;
  height: number;
}

export interface FanxiuGameWindow2MatchBox {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FanxiuGameWindow2MatchPayload extends FanxiuGameWindow2SaveFramePayload {
  filename: string;
  box: FanxiuGameWindow2MatchBox;
}

export interface FanxiuGameWindow2MatchResponse {
  ok: boolean;
  index: number;
  source_filename: string;
  match_filename: string;
  path: string;
  directory: string;
  similarity: number;
  score: number;
  fixed_similarity?: number;
  fixed_score?: number;
  template_similarity?: number;
  template_score?: number;
  template_crop_similarity?: number;
  template_crop_score?: number;
  box: FanxiuGameWindow2MatchBox;
  current_box: FanxiuGameWindow2MatchBox;
  template_box?: FanxiuGameWindow2MatchBox;
  source_width: number;
  source_height: number;
  width: number;
  height: number;
}

export type FanxiuPseudoCodeCardScope = 'guard' | 'action';

export interface FanxiuPseudoCodeCard {
  id: string;
  scope: FanxiuPseudoCodeCardScope;
  title: string;
  body: string;
  enabled: boolean;
  order_index: number;
  created_at: number;
  updated_at: number;
}

export interface FanxiuPseudoCodeCardListResponse {
  items: FanxiuPseudoCodeCard[];
}

export interface FanxiuPseudoCodeCardCreatePayload {
  scope: FanxiuPseudoCodeCardScope;
  title?: string;
  body?: string;
  enabled?: boolean;
  order_index?: number;
}

export interface FanxiuPseudoCodeCardUpdatePayload {
  scope?: FanxiuPseudoCodeCardScope;
  title?: string;
  body?: string;
  enabled?: boolean;
  order_index?: number;
}

export interface FanxiuPseudoCodeCompilePayload {
  entry_id?: string;
  model?: string;
  timeout?: number;
}

export interface FanxiuPseudoCodeStartPayload {
  timeout?: number;
}

export interface FanxiuPseudoCodeRunResponse {
  ok: boolean;
  status: string;
  script_path: string;
  cache_hits: number;
  cache_misses: number;
  compiled_cards: number;
  log: string;
  result: string;
  updated_at: number;
}

export interface FanxiuGameWindow2ScreenshotItem {
  filename: string;
  stem: string;
  pre_label_filename: string;
  pre_label_exists: boolean;
  label_filename: string;
  label_exists: boolean;
  size: number;
  modified_at: string;
  width: number;
  height: number;
}

export interface FanxiuGameWindow2ScreenshotListResponse {
  directory: string;
  items: FanxiuGameWindow2ScreenshotItem[];
}

export interface FanxiuGameWindow2PreLabelBox {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FanxiuGameWindow2PreLabelPayload {
  version: number;
  image: string;
  size: {
    width: number;
    height: number;
  };
  boxes: FanxiuGameWindow2PreLabelBox[];
}

export interface FanxiuGameWindow2PreLabelResponse {
  exists: boolean;
  filename: string;
  payload: FanxiuGameWindow2PreLabelPayload;
}

export interface FanxiuGameWindow2ScreenshotDeleteResponse {
  filename: string;
  deleted: string[];
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

export interface FanxiuWikiCatalog {
  export_root: string;
  exists: boolean;
  text_count: number;
  text_assets: Record<string, number>;
  text_categories: Record<string, number>;
  text_display_kinds?: Record<string, number>;
  galleries: Record<string, number>;
}

export interface FanxiuWikiTextVariant {
  id: string;
  source: string;
  asset: string;
  key: string;
  locator: string;
  title: string;
  category: string;
  display_kind?: string;
  terms: string[];
  plain_preview: string;
  rich_preview: string;
  line_no: number;
  variant_preview?: string;
}

export interface FanxiuWikiTextItem extends Omit<FanxiuWikiTextVariant, 'locator'> {
  score: number;
  duplicate_count?: number;
  duplicate_keys?: string[];
  same_title_count?: number;
  variants?: FanxiuWikiTextVariant[];
}

export interface FanxiuWikiTextSearchResponse {
  query: string;
  asset: string;
  category: string;
  display_kind?: string;
  limit: number;
  offset: number;
  total: number;
  raw_total?: number;
  items: FanxiuWikiTextItem[];
}

export interface FanxiuWikiTextDetail extends Omit<FanxiuWikiTextItem, 'plain_preview' | 'rich_preview' | 'score'> {
  plain_text: string;
  rich_text: string;
}

export interface FanxiuWikiGalleryItem {
  kind: string;
  group: string;
  source: string;
  name: string;
  width: number;
  height: number;
  path: string;
}

export interface FanxiuWikiGalleryResponse {
  query: string;
  kind: string;
  limit: number;
  offset: number;
  total: number;
  items: FanxiuWikiGalleryItem[];
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

export interface FanxiuTimelineHint {
  date?: string;
  time?: string;
  kind?: string;
  confidence?: string;
  label?: string;
  source?: string;
  relation?: string;
  evidence?: string;
  time_code?: string;
  activity_id?: string | number;
  activity_name?: string;
  activity_little_name?: string;
  activity_base_id?: string | number;
  via_item_id?: string | number;
  via_item_name?: string;
  reward_row_id?: string | number;
  merged_count?: number;
  activity_ids?: Array<string | number>;
  sources?: string[];
  evidences?: string[];
}

export interface FanxiuWikiUserFields {
  object_type: string;
  object_id: string;
  note: string;
  source: string;
  updated_at?: string;
}

export type FanxiuGongfaUserFields = FanxiuWikiUserFields;

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
  user_fields?: FanxiuWikiUserFields;
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

export interface FanxiuFacetIndex {
  object_ids: string[];
  rows: Record<string, Record<string, string[]>>;
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

export interface FanxiuItemStats {
  item_count?: number;
  quality_count?: number;
  progression_linked_item_count?: number;
  activity_count?: number;
  item_with_time_hint_count?: number;
  type_count?: number;
  sub_type_count?: number;
  progression_table_counts?: Record<string, number>;
}

export interface FanxiuItemCard {
  id: string | number;
  name: string;
  quality?: string | number;
  quality_name?: string;
  quality_color?: string;
  quality_tab?: string;
  icon?: string;
  small_icon?: string;
  description?: string;
  effect_description?: string;
  type?: string | number;
  type_key?: string;
  type_name?: string;
  sub_type?: string | number;
  sub_type_key?: string;
  sub_type_raw_key?: string;
  sub_type_name?: string;
  type_sub_type_name?: string;
  overlay?: string | number;
  backpack?: string | number;
  effect_value?: unknown;
  can_use?: string | number | boolean;
  sort?: string | number;
  progression_counts?: Record<string, number>;
  progression?: Record<string, FanxiuGongfaProgressionRow[]>;
  optional_gift_group_id?: string | number;
  optional_gift_rewards?: FanxiuGongfaLinkedItem[];
  time_hints?: FanxiuTimelineHint[];
  first_time_hint?: FanxiuTimelineHint | null;
  source_row_key?: string | number;
  terms?: string[];
  user_fields?: FanxiuWikiUserFields;
}

export interface FanxiuItemSearchItem {
  id: string | number;
  name: string;
  quality?: string | number;
  quality_name?: string;
  quality_color?: string;
  quality_tab?: string;
  icon?: string;
  small_icon?: string;
  type?: string | number;
  type_key?: string;
  type_name?: string;
  sub_type?: string | number;
  sub_type_key?: string;
  sub_type_raw_key?: string;
  sub_type_name?: string;
  type_sub_type_name?: string;
  description_preview?: string;
  effect_preview?: string;
  progression_counts?: Record<string, number>;
  first_time_hint?: FanxiuTimelineHint | null;
  terms: string[];
  score: number;
}

export interface FanxiuItemQualityOption {
  value: string;
  label: string;
  count: number;
  quality?: string | number;
  quality_color?: string;
  quality_tab?: string;
}

export interface FanxiuItemTypeOption {
  value: string;
  label: string;
  count: number;
  type?: string | number;
  type_key?: string;
  type_name?: string;
  sub_type?: string | number;
  sub_type_raw_key?: string;
  sub_type_name?: string;
}

export interface FanxiuItemSearchResponse {
  query: string;
  quality_name?: string;
  type_key?: string;
  sub_type_key?: string;
  sort_by?: string;
  sort_order?: string;
  limit: number;
  offset: number;
  total: number;
  stats: FanxiuItemStats;
  catalog_path: string;
  quality_options?: FanxiuItemQualityOption[];
  type_options?: FanxiuItemTypeOption[];
  sub_type_options?: FanxiuItemTypeOption[];
  facet_index?: FanxiuFacetIndex;
  items: FanxiuItemSearchItem[];
}

export interface FanxiuItemCardResponse {
  catalog_path: string;
  card: FanxiuItemCard;
}

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
  user_fields?: FanxiuWikiUserFields;
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

export const getFanxiuPacketCaptureSnapshot = (dnsHosts: string[], resolveDns = true) => {
  return api
    .post<FanxiuPacketCaptureSnapshot>('/fanxiu/packet-capture/snapshot', { dns_hosts: dnsHosts, resolve_dns: resolveDns })
    .then(res => res.data);
};

export const getFanxiuPacketProxyStatus = () => {
  return api.get<FanxiuPacketProxyStatus>('/fanxiu/packet-capture/proxy/status').then(res => res.data);
};

export const getFanxiuPacketCaptureSessionStatus = () => {
  return api.get<FanxiuPacketCaptureSessionStatus>('/fanxiu/packet-capture/session/status').then(res => res.data);
};

export const getFanxiuPacketActivityStatus = () => {
  return api.get<FanxiuPacketActivityStatus>('/fanxiu/packet-capture/activity/status').then(res => res.data);
};

export const getFanxiuPacketActivityHistory = (params: { offset?: number; limit?: number; key?: string } = {}) => {
  return api
    .get<FanxiuPacketActivityHistoryResponse>('/fanxiu/packet-capture/activity/history', { params })
    .then(res => res.data);
};

export const getFanxiuPacketActivityStream = (params: { key?: string; max_bytes?: number } = {}) => {
  return api
    .get<FanxiuPacketActivityStreamResponse>('/fanxiu/packet-capture/activity/stream', { params })
    .then(res => res.data);
};

export const startFanxiuPacketActivity = (bindIp = '') => {
  return api
    .post<FanxiuPacketActivityStatus>('/fanxiu/packet-capture/activity/start', { bind_ip: bindIp })
    .then(res => res.data);
};

export const stopFanxiuPacketActivity = () => {
  return api.post<FanxiuPacketActivityStatus>('/fanxiu/packet-capture/activity/stop', {}).then(res => res.data);
};

export const clearFanxiuPacketActivity = () => {
  return api.delete<FanxiuPacketActivityStatus>('/fanxiu/packet-capture/activity').then(res => res.data);
};

export const startFanxiuPacketCaptureSession = (host: string, port: number) => {
  return api
    .post<FanxiuPacketCaptureSessionStatus>('/fanxiu/packet-capture/session/start', { host, port })
    .then(res => res.data);
};

export const stopFanxiuPacketCaptureSession = () => {
  return api
    .post<FanxiuPacketCaptureSessionStatus>('/fanxiu/packet-capture/session/stop', {})
    .then(res => res.data);
};

export const startFanxiuPacketProxy = (host: string, port: number) => {
  return api
    .post<FanxiuPacketProxyStatus>('/fanxiu/packet-capture/proxy/start', { host, port })
    .then(res => res.data);
};

export const stopFanxiuPacketProxy = () => {
  return api.post<FanxiuPacketProxyStatus>('/fanxiu/packet-capture/proxy/stop').then(res => res.data);
};

export const getFanxiuPacketProxyEvents = (limit = 200) => {
  return api
    .get<FanxiuPacketProxyEventListResponse>('/fanxiu/packet-capture/proxy/events', { params: { limit } })
    .then(res => res.data);
};

export const getFanxiuPacketProxyTimeline = (params: {
  offset?: number;
  limit?: number;
  event_filter?: 'candidate' | 'readable' | 'encrypted_or_resource' | 'all';
} = {}) => {
  return api
    .get<FanxiuPacketProxyTimelineResponse>('/fanxiu/packet-capture/proxy/timeline', { params })
    .then(res => res.data);
};

export const clearFanxiuPacketProxyEvents = () => {
  return api.delete<FanxiuPacketProxyEventListResponse>('/fanxiu/packet-capture/proxy/events').then(res => res.data);
};

export const saveFanxiuPacketProxyEvents = (label = '') => {
  return api
    .post<FanxiuPacketProxySaveResponse>('/fanxiu/packet-capture/proxy/events/save', { label })
    .then(res => res.data);
};

export const getFanxiuPacketProxyLogs = (limit = 50) => {
  return api
    .get<FanxiuPacketProxyLogListResponse>('/fanxiu/packet-capture/proxy/logs', { params: { limit } })
    .then(res => res.data);
};

export const loadFanxiuPacketProxyLog = (name: string, limit = 500) => {
  return api
    .get<FanxiuPacketProxyLogLoadResponse>('/fanxiu/packet-capture/proxy/logs/load', { params: { name, limit } })
    .then(res => res.data);
};

export const getFanxiuWikiCatalog = () => {
  return api.get<FanxiuWikiCatalog>('/fanxiu/resources/wiki/catalog').then(res => res.data);
};

export const searchFanxiuWikiTexts = (params: {
  query?: string;
  asset?: string;
  category?: string;
  display_kind?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api
    .get<FanxiuWikiTextSearchResponse>('/fanxiu/resources/wiki/texts', { params, timeout: 60000 })
    .then(res => res.data);
};

export const getFanxiuWikiText = (asset: string, key: string, options: { timeout?: number } = {}) => {
  return api
    .get<FanxiuWikiTextDetail>('/fanxiu/resources/wiki/text', {
      params: { asset, key },
      timeout: options.timeout ?? 30000,
    })
    .then(res => res.data);
};

export const searchFanxiuWikiGallery = (params: {
  query?: string;
  kind?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api.get<FanxiuWikiGalleryResponse>('/fanxiu/resources/wiki/gallery', { params }).then(res => res.data);
};

export const getFanxiuWikiMediaUrl = (path: string) => {
  return `/api/fanxiu/resources/wiki/media?path=${encodeURIComponent(path)}`;
};

export const getFanxiuResourceIconUrl = (name: string | null | undefined) => {
  const iconName = String(name || '').trim();
  return iconName ? `/api/fanxiu/resources/icon?name=${encodeURIComponent(iconName)}` : '';
};

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

export const updateFanxiuWikiUserFields = (
  objectType: string,
  objectId: string | number,
  payload: { note?: string; source?: string },
) => {
  return api
    .put<FanxiuWikiUserFields>('/fanxiu/resources/wiki/user-fields', payload, {
      params: { object_type: objectType, object_id: objectId },
    })
    .then(res => res.data);
};

export const updateFanxiuGongfaUserFields = (
  gongfaId: string | number,
  payload: { note?: string; source?: string },
) => updateFanxiuWikiUserFields('gongfa', gongfaId, payload);

export const searchFanxiuItemCards = (params: {
  query?: string;
  quality_name?: string;
  type_key?: string;
  sub_type_key?: string;
  sort_by?: string;
  sort_order?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api.get<FanxiuItemSearchResponse>('/fanxiu/resources/items/cards', { params }).then(res => res.data);
};

export const getFanxiuItemCard = (itemId: string | number) => {
  return api
    .get<FanxiuItemCardResponse>('/fanxiu/resources/items/card', { params: { item_id: itemId } })
    .then(res => res.data);
};

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

export const createFanxiuGameWindow2StreamToken = (entryId: string) => {
  return api
    .post<FanxiuGameWindow2StreamToken>('/fanxiu/game-window2/stream-token', { entry_id: entryId })
    .then(res => res.data);
};

export const clickFanxiuGameWindow2 = (payload: FanxiuGameWindow2ClickPayload) => {
  return api.post<Record<string, unknown>>('/fanxiu/game-window2/input/click', payload).then(res => res.data);
};

export const dragFanxiuGameWindow2 = (payload: FanxiuGameWindow2DragPayload) => {
  return api.post<Record<string, unknown>>('/fanxiu/game-window2/input/drag', payload).then(res => res.data);
};

export const saveFanxiuGameWindow2Frame = (payload: FanxiuGameWindow2SaveFramePayload) => {
  return api.post<FanxiuGameWindow2SaveFrameResponse>('/fanxiu/game-window2/save-frame', payload).then(res => res.data);
};

export const matchFanxiuGameWindow2Screenshot = (payload: FanxiuGameWindow2MatchPayload) => {
  return api.post<FanxiuGameWindow2MatchResponse>('/fanxiu/game-window2/match', payload).then(res => res.data);
};

export const getFanxiuGameWindow2MatchImage = (entryId: string, filename: string) => {
  return api
    .get<Blob>('/fanxiu/game-window2/match/image', {
      params: { entry_id: entryId, filename },
      responseType: 'blob',
    })
    .then(res => res.data);
};

export const listFanxiuPseudoCodeCards = () => {
  return api.get<FanxiuPseudoCodeCardListResponse>('/fanxiu/game-window2/pseudocode-cards').then(res => res.data);
};

export const createFanxiuPseudoCodeCard = (payload: FanxiuPseudoCodeCardCreatePayload) => {
  return api.post<FanxiuPseudoCodeCard>('/fanxiu/game-window2/pseudocode-cards', payload).then(res => res.data);
};

export const updateFanxiuPseudoCodeCard = (cardId: string, payload: FanxiuPseudoCodeCardUpdatePayload) => {
  return api.patch<FanxiuPseudoCodeCard>(`/fanxiu/game-window2/pseudocode-cards/${encodeURIComponent(cardId)}`, payload).then(res => res.data);
};

export const deleteFanxiuPseudoCodeCard = (cardId: string) => {
  return api.delete<{ ok: boolean; id: string }>(`/fanxiu/game-window2/pseudocode-cards/${encodeURIComponent(cardId)}`).then(res => res.data);
};

export const compileFanxiuPseudoCode = (payload: FanxiuPseudoCodeCompilePayload) => {
  return api.post<FanxiuPseudoCodeRunResponse>('/fanxiu/game-window2/pseudocode/compile', payload).then(res => res.data);
};

export const startFanxiuPseudoCode = (payload: FanxiuPseudoCodeStartPayload = {}) => {
  return api.post<FanxiuPseudoCodeRunResponse>('/fanxiu/game-window2/pseudocode/start', payload).then(res => res.data);
};

export const listFanxiuGameWindow2Screenshots = (entryId: string) => {
  return api
    .post<FanxiuGameWindow2ScreenshotListResponse>('/fanxiu/game-window2/screenshot/list', { entry_id: entryId })
    .then(res => res.data);
};

export const deleteFanxiuGameWindow2Screenshot = (entryId: string, filename: string) => {
  return api
    .post<FanxiuGameWindow2ScreenshotDeleteResponse>('/fanxiu/game-window2/screenshot/delete', { entry_id: entryId, filename })
    .then(res => res.data);
};

export const getFanxiuGameWindow2Screenshot = (entryId: string, filename: string) => {
  return api
    .get<Blob>('/fanxiu/game-window2/screenshot/image', {
      params: { entry_id: entryId, filename },
      responseType: 'blob',
    })
    .then(res => res.data);
};

export const getFanxiuGameWindow2PreLabel = (entryId: string, filename: string) => {
  return api
    .post<FanxiuGameWindow2PreLabelResponse>('/fanxiu/game-window2/screenshot/pre-label', { entry_id: entryId, filename })
    .then(res => res.data);
};

export const saveFanxiuGameWindow2PreLabel = (
  entryId: string,
  filename: string,
  payload: FanxiuGameWindow2PreLabelPayload,
) => {
  return api
    .put<FanxiuGameWindow2PreLabelResponse>('/fanxiu/game-window2/screenshot/pre-label', { entry_id: entryId, filename, payload })
    .then(res => res.data);
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

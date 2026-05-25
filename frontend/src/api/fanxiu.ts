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

export interface FanxiuTcpCaptureFile {
  name: string;
  path: string;
  relative_path: string;
  size: number;
  modified_at: string;
  decoded_path: string;
  decoded: boolean;
  capture_sha256: string;
  record_id: string;
  record_dir: string;
  stored_pcap: string;
  stored_decoded_path: string;
  stored: boolean;
}

export interface FanxiuTcpCaptureListResponse {
  export_root: string;
  capture_dir: string;
  store_capture_dir: string;
  items: FanxiuTcpCaptureFile[];
}

export interface FanxiuTcpRecordItem {
  record_id: string;
  record_dir: string;
  pcap_name: string;
  source_pcap: string;
  stored_pcap: string;
  decoded_path: string;
  decoded: boolean;
  stream: number;
  server_host: string;
  capture_sha256: string;
  created_at: string;
  summary: Record<string, unknown>;
}

export interface FanxiuTcpRecordListResponse {
  store_root: string;
  items: FanxiuTcpRecordItem[];
}

export interface FanxiuTcpBusinessEntry {
  id: string;
  decoded_at: string;
  record_id: string;
  pcap_name: string;
  source_kind: string;
  direction: 'c2s' | 's2c' | string;
  name: string;
  category: string;
  meaning: string;
  protocol_meaning: string;
  pro_id: number;
  sn: number;
  frame_index: number;
  display_text: string;
  display_segments: Array<{ text: string; kind?: string; key?: string }>;
  content: Record<string, unknown>;
}

export interface FanxiuTcpBusinessCategorySummary {
  category: string;
  meaning: string;
  count: number;
  protocols: string[];
}

export interface FanxiuTcpBusinessProtocolSample {
  id: string;
  decoded_at: string;
  direction: 'c2s' | 's2c' | string;
  display_text: string;
  display_segments: Array<{ text: string; kind?: string; key?: string }>;
  content: Record<string, unknown>;
  field_labels?: Record<string, Record<string, string>>;
}

export interface FanxiuTcpBusinessProtocolSummary {
  name: string;
  category: string;
  meaning: string;
  count: number;
  samples: FanxiuTcpBusinessProtocolSample[];
}

export interface FanxiuTcpBusinessEntryListResponse {
  page: number;
  page_size: number;
  total: number;
  category_summary: FanxiuTcpBusinessCategorySummary[];
  protocol_summary: FanxiuTcpBusinessProtocolSummary[];
  items: FanxiuTcpBusinessEntry[];
}

export interface FanxiuTcpProtocolCount {
  pro_id: number;
  name: string;
  count: number;
}

export interface FanxiuTcpDecodedFrame {
  direction: 'c2s' | 's2c';
  offset: number;
  frame_len: number;
  sn: number;
  pro_id: number;
  name?: string;
  payload_len: number;
  zlib?: boolean;
  parsed?: Record<string, unknown>;
  parsed_bytes?: number;
  remain?: number;
  parse_error?: string;
  payload_hex?: string;
  remain_hex?: string;
}

export interface FanxiuTcpDecodeResponse {
  export_root: string;
  pcap: string;
  stream: number;
  server_host: string;
  text_assets: string;
  output_path: string;
  capture_sha256: string;
  stream_candidates: Array<{ stream: number; packets: number; payload_bytes: number }>;
  record_id: string;
  record_dir: string;
  stored_pcap: string;
  stored_decoded_path: string;
  meta_path: string;
  summary: {
    c2s_bytes: number;
    s2c_bytes: number;
    c2s_frames: number;
    s2c_frames: number;
    c2s_protocols: FanxiuTcpProtocolCount[];
    s2c_protocols: FanxiuTcpProtocolCount[];
  };
  frames: FanxiuTcpDecodedFrame[];
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
  pcap_path: string;
  pcap_size: number;
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

export interface FanxiuProtocolSemanticFeature {
  key: string;
  title: string;
}

export interface FanxiuProtocolSemanticRow {
  id: string;
  packet: string;
  direction: string;
  module: string;
  operation: string;
  operation_side: string;
  role: string;
  read_fields: string;
  write_fields: string;
  handler_names: string;
  logic_names: string;
  net_function: string;
  flow_kind: string;
  assigned_fields: string;
  msg_fields: string;
  state_sinks: string;
  authority_class: string;
  gap_category: string;
  semantic_note: string;
  source_file_count: string;
  sample_files: string;
}

export interface FanxiuProtocolSemanticEdge {
  source_type: string;
  source: string;
  edge: string;
  target_type: string;
  target: string;
  evidence: string;
}

export interface FanxiuProtocolSemanticResponse {
  feature: string;
  title: string;
  export_root: string;
  outputs: {
    semantics: string;
    edges: string;
    report: string;
  };
  available_features: FanxiuProtocolSemanticFeature[];
  counts: {
    rows: number;
    edges: number;
    filtered_rows: number;
    filtered_edges: number;
    by_role: Record<string, number>;
    by_operation: Record<string, number>;
  };
  items: FanxiuProtocolSemanticRow[];
  edges: FanxiuProtocolSemanticEdge[];
  roles: string[];
  operations: string[];
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

export interface FanxiuWikiLinkIndexItem {
  alias: string;
  tab: 'item' | 'gongfa' | 'lingjie';
  id: string | number;
  title?: string;
  preview?: string;
  effect_text_preview?: string;
  effect_preview?: string;
  reward_preview?: string;
  kind?: string;
  priority?: number;
}

export interface FanxiuWikiLinkIndexResponse {
  items: FanxiuWikiLinkIndexItem[];
  total: number;
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
  show_effect?: string;
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
  active_task_count?: number;
  open_function_count?: number;
  subpackage_reward_count?: number;
  catalog_card_count?: number;
  current_card_count?: number;
  stale_card_count?: number;
  activity_with_time_hint_count?: number;
  activity_with_reward_count?: number;
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
  costs?: string[];
  reward_items?: FanxiuGongfaLinkedItem[];
  raw_rewards?: string[];
  condition?: string;
}

export interface FanxiuActivityRewardSection {
  key: string;
  title: string;
  count: number;
  rows: FanxiuActivityRewardRow[];
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

export const listFanxiuTcpCaptures = (limit = 50) => {
  return api
    .get<FanxiuTcpCaptureListResponse>('/fanxiu/packet-capture/tcp/captures', { params: { limit } })
    .then(res => res.data);
};

export const listFanxiuTcpRecords = (limit = 50) => {
  return api
    .get<FanxiuTcpRecordListResponse>('/fanxiu/packet-capture/tcp/records', { params: { limit } })
    .then(res => res.data);
};

export const listFanxiuTcpBusinessEntries = (params: { page?: number; page_size?: number; category?: string; protocol?: string; hidden_protocols?: string } = {}) => {
  return api
    .get<FanxiuTcpBusinessEntryListResponse>('/fanxiu/packet-capture/tcp/business-entries', { params })
    .then(res => res.data);
};

export const decodeFanxiuTcpCapture = (payload: { pcap: string; stream?: number; server_host?: string; persist?: boolean }) => {
  return api
    .post<FanxiuTcpDecodeResponse>('/fanxiu/packet-capture/tcp/decode', payload, { timeout: 120000 })
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

export const getFanxiuWikiLinkIndex = () => {
  return api.get<FanxiuWikiLinkIndexResponse>('/fanxiu/resources/wiki/link-index').then(res => res.data);
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

export const getFanxiuProtocolSemantics = (params: {
  feature?: string;
  query?: string;
  role?: string;
  operation?: string;
  limit?: number;
  edge_limit?: number;
} = {}) => {
  return api
    .get<FanxiuProtocolSemanticResponse>('/fanxiu/resources/protocol-semantics', { params, timeout: 60000 })
    .then(res => res.data);
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

export const searchFanxiuActivityCards = (params: {
  query?: string;
  kind_key?: string;
  time_kind?: string;
  activity_type?: string;
  sort_by?: string;
  sort_order?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  return api.get<FanxiuActivitySearchResponse>('/fanxiu/resources/activities/cards', { params }).then(res => res.data);
};

export const getFanxiuActivityCard = (activityId: string | number) => {
  return api
    .get<FanxiuActivityCardResponse>('/fanxiu/resources/activities/card', { params: { activity_id: activityId } })
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

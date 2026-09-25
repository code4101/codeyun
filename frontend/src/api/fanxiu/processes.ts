/** 进程与协议观测的前端契约；请求由统一 api 客户端发送。 */
import api from '@/api';


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

export const getFanxiuProcesses = () => {
  return api.get<FanxiuProcessListResponse>('/fanxiu/processes').then(res => res.data);
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

export const getLocalScriptProcesses = () => {
  return api.get<LocalScriptProcessListResponse>('/fanxiu/scripts').then(res => res.data);
};

export const terminateFanxiuProcesses = () => {
  // Hard-stop helper for legacy Fanxiu script leftovers. Prefer Kernel scheduler restart/wake actions for resident Kernel ops.
  return api.post<FanxiuProcessTerminateResponse>('/fanxiu/processes/terminate').then(res => res.data);
};

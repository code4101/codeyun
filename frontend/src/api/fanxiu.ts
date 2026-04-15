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
    id: String(raw.id),
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

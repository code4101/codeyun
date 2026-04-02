import api from '@/api';
import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { ElMessage } from 'element-plus';
import { useAiAppStore } from '@/store/aiAppStore';
import { useAiProviderStore } from '@/store/aiProviderStore';
import { useUserStore } from '@/store/userStore';
import { NOTE_WEIGHT_DEFAULT } from '@/utils/noteWeight';
import { createEffectiveNoteTypes, type NoteTypeAssignment } from '@/utils/nodeConfig';
import {
  deriveLegacySemanticsFromTaxonomy,
  deriveNoteTaxonomyFromLegacy,
  NOTE_CATEGORY_DEFAULT,
  NOTE_FORM_DEFAULT,
  NOTE_LIFECYCLE_STAGE_DEFAULT,
  NOTE_SCENE_DEFAULT
} from '@/utils/noteSemantics';
import {
  evaluateCompletionProgressExpr,
  getCompletionProgressExprFromCustomFields,
} from '@/utils/noteProgress';

export interface NoteNode {
  id: string;
  user_id?: number;
  title: string;
  content?: string;
  weight: number;
  node_type?: string | null;
  note_types?: NoteTypeAssignment[];
  note_categories?: NoteTypeAssignment[];
  primary_category?: string | null;
  note_form?: string | null;
  note_kind?: string | null;
  note_scene?: string | null;
  node_status?: string | null;
  lifecycle_stage?: string | null;
  color?: string | null;
  weight_mode?: string | null;
  private_level: number;
  custom_fields?: any[];
  completion_progress_expr?: string | null;
  completion_progress?: number | null;
  inherited_fields?: {
    direct?: any[];
    ancestors?: any[];
  };
  created_at: number;
  updated_at: number;
  start_at: number;
  history?: { ts: number; f: string; v: any }[];
  edge_count?: number;
  out_degree?: number;
  can_edit?: boolean;
}

export interface NoteEdge {
  id: string;
  source_id: string;
  target_id: string;
  source_handle?: string;
  target_handle?: string;
  label?: string;
  created_at: number;
}

export interface TabState {
  id: string;
  label: string;
  type: 'galaxy' | 'calendar' | 'list' | 'planet';
  data?: { noteId: string; mode?: 'planetary' | 'satellite' };
  closable: boolean;
}

export interface NoteQueryParams {
  created_start?: number;
  created_end?: number;
  updated_start?: number;
  updated_end?: number;
  limit?: number;
}

export interface NoteFilterRule {
  field: string;
  op: 'eq' | 'neq' | 'in' | 'not_in' | 'contains' | 'not_contains' | 'regex_search' | 'gte' | 'lte' | 'between';
  value?: any;
  values?: any[];
}

export interface NoteQueryRequest {
  scope?: {
    mode: 'all' | 'planetary' | 'satellite';
    seed_note_id?: string;
  };
  rules?: NoteFilterRule[];
  order_by?: string;
  order_desc?: boolean;
  skip?: number;
  limit?: number;
  include_edges?: boolean;
}

export interface NoteQueryResponse {
  nodes: NoteNode[];
  edges: NoteEdge[];
  total_nodes: number;
  total_edges: number;
}

export type NoteProgramMatcherKind = 'all' | 'none' | 'id' | 'field' | 'title_contains' | 'seed' | 'depth' | 'relative_month_window';
export type NoteProgramRuleAction = 'include' | 'exclude';

export type NoteTimePointExprKind = 'absolute' | 'relative';
export type NoteTimePointUnit = 'day' | 'week' | 'month';
export type NoteTimePointBoundary = 'start' | 'end';

export interface NoteTimePointExpr {
  kind: NoteTimePointExprKind;
  value?: number | null;
  unit?: NoteTimePointUnit;
  offset?: number;
  boundary?: NoteTimePointBoundary;
}

export interface NoteProgramMatcher {
  kind: NoteProgramMatcherKind;
  ids?: string[];
  field?: string | null;
  op?: 'eq' | 'neq' | 'in' | 'not_in' | 'contains' | 'not_contains' | 'regex_search' | 'gte' | 'lte' | 'between' | null;
  value?: any;
  values?: any[];
  ignore_case?: boolean;
  min_depth?: number;
  max_depth?: number | null;
  start_month_offset?: number;
  end_month_offset?: number;
  time_value?: NoteTimePointExpr | null;
  time_values?: NoteTimePointExpr[];
}

export interface NoteProgramRule {
  action: NoteProgramRuleAction;
  matcher: NoteProgramMatcher;
}

export interface NoteProgramChannel {
  default: boolean;
  rules: NoteProgramRule[];
}

export interface NoteProgramRequest {
  executor?: {
    kind: 'scan' | 'component';
    seed_ids?: string[];
    mode?: 'planetary' | 'satellite';
    max_depth?: number | null;
  };
  program?: {
    select: NoteProgramChannel;
    expand: NoteProgramChannel;
  };
  result?: {
    include_edges?: boolean;
    order_by?: string;
    order_desc?: boolean;
    skip?: number;
    limit?: number;
  };
}

export interface NoteProgramResponse {
  nodes: NoteNode[];
  edges: NoteEdge[];
  total_nodes: number;
  total_edges: number;
}

export interface NoteBatchUpdateRequest {
  ids: string[];
  patch: {
    private_level?: number;
    weight?: number;
    weight_delta?: number;
    note_categories?: NoteTypeAssignment[];
    primary_category?: string;
    note_form?: string;
    lifecycle_stage?: string;
  };
}

export interface NoteBatchUpdateResponse {
  updated_count: number;
  notes: NoteNode[];
}

export interface AiNoteCategorizeRequest {
  provider?: string | null;
  base_url?: string | null;
  api_key?: string | null;
  model?: string | null;
}

export interface AiNoteCategorizeResponse {
  app: string;
  provider: string;
  model: string;
  summary: string;
  note: NoteNode;
}

export interface NoteScopeState {
  titleKeyword: string;
  primaryCategory: string;
  lifecycleStage: string;
  startRange: number[];
  updatedRange: number[];
}

type NoteScopeStateInput = Partial<NoteScopeState> & {
  nodeType?: string;
  nodeStatus?: string;
};

export interface TabSession {
  tabId: string;
  noteIds: string[];
  edgeIds: string[];
  loading: boolean;
  requestVersion: number;
  lastLoadedAt?: number;
  lastQuery?: NoteQueryRequest | NoteProgramRequest | null;
  viewState: Record<string, any>;
}

const NOTE_SUMMARY_CACHE_LIMIT = 4000;
const NOTE_DETAIL_CACHE_LIMIT = 64;
const EDGE_CACHE_LIMIT = 12000;
const NOTE_TAB_VIEW_STATE_STORAGE_KEY = 'codeyun.notes.tab-view-state';
const NOTE_TAB_VIEW_STATE_STORAGE_VERSION = 2;
const NOTE_TAB_VIEW_STATE_LEGACY_KEYS = [
  'codeyun.notes.tab-view-state.v1',
  'codeyun.notes.tab-view-state.v2'
];

const dedupeIds = (ids: string[]) => Array.from(new Set(ids));

const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const normalizeInteger = (value: unknown, fallback: number = 0) => {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return Math.trunc(parsed);
  }
  return fallback;
};

const normalizeNote = (raw: any): NoteNode => ({
  ...raw,
  ...(() => {
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
          raw.node_type ?? 'note',
          raw.note_kind ?? NOTE_SCENE_DEFAULT,
          raw.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
        ),
        note_types: createEffectiveNoteTypes(raw.note_types, raw.node_type ?? 'note', raw.color ?? null),
        node_type: raw.node_type ?? 'note',
        note_kind: raw.note_kind ?? null,
        node_status: raw.node_status ?? null
      };

    return {
      id: String(raw.id),
      created_at: raw.created_at * 1000,
      updated_at: raw.updated_at * 1000,
      start_at: raw.start_at * 1000,
      note_types: createEffectiveNoteTypes(taxonomy.note_types ?? raw.note_types, taxonomy.node_type ?? raw.node_type ?? 'note', raw.color ?? null),
      note_categories: taxonomy.note_categories,
      primary_category: taxonomy.primary_category,
      note_form: taxonomy.note_form,
      note_kind: taxonomy.note_kind ?? raw.note_kind ?? null,
      note_scene: taxonomy.note_scene,
      node_status: taxonomy.node_status ?? raw.node_status ?? null,
      lifecycle_stage: taxonomy.lifecycle_stage,
      weight_mode: raw.weight_mode ?? null,
      private_level: normalizeInteger(raw.private_level, 0),
      completion_progress_expr: (() => {
        const expr = typeof raw.completion_progress_expr === 'string'
          ? raw.completion_progress_expr
          : getCompletionProgressExprFromCustomFields(raw.custom_fields);
        return expr ? expr : null;
      })(),
      completion_progress: isFiniteNumber(raw.completion_progress)
        ? Math.min(1, Math.max(0, Number(raw.completion_progress)))
        : evaluateCompletionProgressExpr(
          typeof raw.completion_progress_expr === 'string'
            ? raw.completion_progress_expr
            : getCompletionProgressExprFromCustomFields(raw.custom_fields)
        ),
      can_edit: Boolean(raw.can_edit)
    };
  })()
});

const normalizeEdge = (raw: any): NoteEdge => ({
  ...raw,
  id: String(raw.id),
  source_id: String(raw.source_id),
  target_id: String(raw.target_id),
  created_at: raw.created_at * 1000
});

const patchNoteDetails = (detailMap: Record<string, NoteNode>, incomingNotes: NoteNode[]) => {
  incomingNotes.forEach(note => {
    if (!detailMap[note.id]) return;
    const existing = detailMap[note.id];
    detailMap[note.id] = {
      ...existing,
      ...note,
      inherited_fields: note.inherited_fields ?? existing.inherited_fields
    };
  });
};

const stripNoteDetail = (note: NoteNode): NoteNode => {
  const { content, history, inherited_fields, ...summary } = note;
  return summary;
};

const cloneViewState = (value: Record<string, any>) => JSON.parse(JSON.stringify(value));
const canUseLocalStorage = () => typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';

const toApiTimestamp = (value: number) => value / 1000;

const normalizeRangeInput = (value: unknown): number[] => {
  if (!Array.isArray(value)) return [];

  const numbers = value
    .map(item => {
      if (typeof item === 'string') return Number(item);
      if (item instanceof Date) return item.getTime();
      return item;
    })
    .filter(isFiniteNumber);

  if (numbers.length < 2) return [];
  const [start, end] = numbers;
  return start <= end ? [start, end] : [end, start];
};

const appendRangeRules = (rules: NoteFilterRule[], field: 'start_at' | 'updated_at', range: number[]) => {
  const normalized = normalizeRangeInput(range);
  if (normalized.length !== 2) return;

  rules.push({
    field,
    op: 'between',
    values: normalized.map(toApiTimestamp)
  });
};

const normalizeProgramIds = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map(item => String(item ?? '').trim())
    .filter(Boolean);
};

export const createNoteProgramMatcher = (kind: NoteProgramMatcherKind = 'all'): NoteProgramMatcher => ({
  kind,
  ids: [],
  field: kind === 'field' ? 'node_status' : kind === 'relative_month_window' ? 'start_at' : null,
  op: kind === 'field' ? 'eq' : kind === 'relative_month_window' ? 'between' : null,
  value: kind === 'title_contains' ? '' : undefined,
  values: [],
  ignore_case: true,
  min_depth: 0,
  max_depth: null,
  start_month_offset: kind === 'relative_month_window' ? -1 : 0,
  end_month_offset: kind === 'relative_month_window' ? 1 : 0,
  time_value: null,
  time_values: []
});

export const createAbsoluteTimePoint = (value?: number | null): NoteTimePointExpr => ({
  kind: 'absolute',
  value: isFiniteNumber(value) ? value : null
});

export const createRelativeTimePoint = (
  unit: NoteTimePointUnit = 'month',
  offset: number = 0,
  boundary: NoteTimePointBoundary = 'start'
): NoteTimePointExpr => ({
  kind: 'relative',
  unit,
  offset,
  boundary
});

export const normalizeNoteTimePointExpr = (value?: Partial<NoteTimePointExpr> | null): NoteTimePointExpr => {
  const kind = value?.kind === 'relative' ? 'relative' : 'absolute';
  let numericValue: number | null = null;
  if (kind === 'absolute') {
    if (isFiniteNumber(value?.value)) {
      numericValue = Number(value?.value);
    } else if (typeof value?.value === 'string') {
      const parsed = Number(value.value);
      numericValue = Number.isFinite(parsed) ? parsed : null;
    }
  }

  return {
    kind,
    value: numericValue,
    unit: value?.unit === 'day' || value?.unit === 'week' || value?.unit === 'month' ? value.unit : 'month',
    offset: isFiniteNumber(value?.offset) ? Number(value?.offset) : 0,
    boundary: value?.boundary === 'end' ? 'end' : 'start'
  };
};

const normalizeNoteTimePointExprForApi = (value?: Partial<NoteTimePointExpr> | null): NoteTimePointExpr => {
  const normalized = normalizeNoteTimePointExpr(value);
  if (normalized.kind !== 'absolute') return normalized;

  const numericValue = typeof normalized.value === 'string' ? Number(normalized.value) : normalized.value;
  return {
    ...normalized,
    value: isFiniteNumber(numericValue) ? toApiTimestamp(Number(numericValue)) : null
  };
};

export const normalizeNoteProgramMatcher = (value?: Partial<NoteProgramMatcher> | null): NoteProgramMatcher => {
  const kind = value?.kind ?? 'all';
  const base = createNoteProgramMatcher(kind);
  const normalized: NoteProgramMatcher = {
    ...base,
    ...value,
    kind,
    ids: normalizeProgramIds(value?.ids ?? base.ids),
    field: typeof value?.field === 'string' ? value.field : base.field,
    op: value?.op ?? base.op,
    values: Array.isArray(value?.values) ? [...value!.values] : [...(base.values ?? [])],
    ignore_case: typeof value?.ignore_case === 'boolean' ? value.ignore_case : base.ignore_case,
    min_depth: isFiniteNumber(value?.min_depth) ? Number(value?.min_depth) : base.min_depth,
    max_depth: isFiniteNumber(value?.max_depth) ? Number(value?.max_depth) : base.max_depth,
    start_month_offset: isFiniteNumber(value?.start_month_offset) ? Number(value?.start_month_offset) : base.start_month_offset,
    end_month_offset: isFiniteNumber(value?.end_month_offset) ? Number(value?.end_month_offset) : base.end_month_offset,
    time_value: value?.time_value ? normalizeNoteTimePointExpr(value.time_value) : null,
    time_values: Array.isArray(value?.time_values) ? value.time_values.map(item => normalizeNoteTimePointExpr(item)) : []
  };

  if (normalized.kind === 'title_contains') {
    return {
      ...normalized,
      kind: 'field',
      field: 'title',
      op: 'contains',
      value: typeof normalized.value === 'string' ? normalized.value : '',
      values: [],
      time_value: null,
      time_values: []
    };
  }

  if (normalized.kind === 'relative_month_window') {
    return {
      ...normalized,
      kind: 'field',
      field: normalized.field === 'updated_at' ? 'updated_at' : 'start_at',
      op: 'between',
      time_value: null,
      time_values: [
        createRelativeTimePoint('month', normalized.start_month_offset ?? -1, 'start'),
        createRelativeTimePoint('month', normalized.end_month_offset ?? 1, 'end')
      ],
      value: undefined,
      values: []
    };
  }

  return normalized;
};

export const createNoteProgramRule = (
  action: NoteProgramRuleAction = 'include',
  kind: NoteProgramMatcherKind = 'all'
): NoteProgramRule => ({
  action,
  matcher: createNoteProgramMatcher(kind)
});

export const normalizeNoteProgramRule = (value?: Partial<NoteProgramRule> | null): NoteProgramRule => ({
  action: value?.action === 'exclude' ? 'exclude' : 'include',
  matcher: normalizeNoteProgramMatcher(value?.matcher)
});

export const createEmptyNoteProgramChannel = (defaultDecision: boolean = false): NoteProgramChannel => ({
  default: defaultDecision,
  rules: []
});

export const normalizeNoteProgramChannel = (value?: Partial<NoteProgramChannel> | null): NoteProgramChannel => ({
  default: typeof value?.default === 'boolean' ? value.default : false,
  rules: Array.isArray(value?.rules) ? value.rules.map(rule => normalizeNoteProgramRule(rule)) : []
});

export const cloneNoteProgramChannel = (value?: Partial<NoteProgramChannel> | null): NoteProgramChannel =>
  JSON.parse(JSON.stringify(normalizeNoteProgramChannel(value)));

export const createDefaultRecentMonthProgram = (
  field: 'start_at' | 'updated_at' = 'start_at'
): NoteProgramChannel => {
  return {
    default: false,
    rules: [
      {
        action: 'include',
        matcher: {
          kind: 'field',
          field,
          op: 'between',
          time_values: [
            createRelativeTimePoint('month', -1, 'start'),
            createRelativeTimePoint('month', 1, 'end')
          ]
        }
      }
    ]
  };
};

export const createFixedRangeProgram = (
  start: number,
  end: number,
  field: 'start_at' | 'updated_at' = 'start_at'
): NoteProgramChannel => {
  const normalizedStart = Math.min(start, end);
  const normalizedEnd = Math.max(start, end);

  return {
    default: false,
    rules: [
      {
        action: 'include',
        matcher: {
          kind: 'field',
          field,
          op: 'between',
          time_values: [
            createAbsoluteTimePoint(normalizedStart),
            createAbsoluteTimePoint(normalizedEnd)
          ]
        }
      }
    ]
  };
};

export const createFixedMonthProgram = (
  anchor: Date | number,
  field: 'start_at' | 'updated_at' = 'start_at'
): NoteProgramChannel => {
  const date = anchor instanceof Date ? anchor : new Date(anchor);
  const start = new Date(date.getFullYear(), date.getMonth(), 1).getTime();
  const end = new Date(date.getFullYear(), date.getMonth() + 1, 1).getTime() - 1;
  return createFixedRangeProgram(start, end, field);
};

const normalizeMatcherForApi = (matcher: NoteProgramMatcher): NoteProgramMatcher => {
  const normalized = normalizeNoteProgramMatcher(matcher);

  if (normalized.kind === 'field') {
    const field = normalized.field ?? '';
    const isTimeField = field === 'start_at' || field === 'updated_at';

    if (isTimeField && normalized.time_value) {
      normalized.time_value = normalizeNoteTimePointExprForApi(normalized.time_value);
      normalized.time_values = [];
      normalized.value = undefined;
      normalized.values = [];
      return normalized;
    }

    if (isTimeField && normalized.time_values && normalized.time_values.length > 0) {
      normalized.time_values = normalized.time_values.map(item => normalizeNoteTimePointExprForApi(item));
      normalized.time_value = null;
      normalized.value = undefined;
      normalized.values = [];
      return normalized;
    }

    if (normalized.op === 'between') {
      const range = normalizeRangeInput(normalized.values);
      normalized.values = isTimeField ? range.map(toApiTimestamp) : range;
      normalized.value = undefined;
    } else if (isTimeField) {
      const numericValue = typeof normalized.value === 'string' ? Number(normalized.value) : normalized.value;
      if (isFiniteNumber(numericValue)) {
        normalized.value = toApiTimestamp(numericValue);
      }
      normalized.values = [];
    }
  }

  if (normalized.kind === 'id') {
    normalized.ids = normalizeProgramIds(normalized.ids);
  }

  return normalized;
};

const shiftMonth = (year: number, month: number, offset: number): [number, number] => {
  const monthIndex = (year * 12 + (month - 1)) + offset;
  const shiftedYear = Math.floor(monthIndex / 12);
  const shiftedMonthIndex = ((monthIndex % 12) + 12) % 12;
  return [shiftedYear, shiftedMonthIndex + 1];
};

const resolveRelativeTimePoint = (
  unit: NoteTimePointUnit,
  offset: number,
  boundary: NoteTimePointBoundary,
  baseDate: Date = new Date()
): number => {
  if (unit === 'month') {
    const [startYear, startMonth] = shiftMonth(baseDate.getFullYear(), baseDate.getMonth() + 1, offset);
    const [endYear, endMonth] = shiftMonth(baseDate.getFullYear(), baseDate.getMonth() + 1, offset + 1);
    const startAt = new Date(startYear, startMonth - 1, 1, 0, 0, 0, 0).getTime();
    const endAt = new Date(endYear, endMonth - 1, 1, 0, 0, 0, 0).getTime() - 1;
    return boundary === 'start' ? startAt : endAt;
  }

  if (unit === 'week') {
    const weekStart = new Date(baseDate.getFullYear(), baseDate.getMonth(), baseDate.getDate(), 0, 0, 0, 0);
    const weekday = weekStart.getDay();
    const mondayOffset = weekday === 0 ? -6 : 1 - weekday;
    weekStart.setDate(weekStart.getDate() + mondayOffset + offset * 7);
    const weekEnd = new Date(weekStart.getTime());
    weekEnd.setDate(weekEnd.getDate() + 7);
    weekEnd.setMilliseconds(weekEnd.getMilliseconds() - 1);
    return boundary === 'start' ? weekStart.getTime() : weekEnd.getTime();
  }

  const dayStart = new Date(baseDate.getFullYear(), baseDate.getMonth(), baseDate.getDate(), 0, 0, 0, 0);
  dayStart.setDate(dayStart.getDate() + offset);
  const dayEnd = new Date(dayStart.getTime());
  dayEnd.setDate(dayEnd.getDate() + 1);
  dayEnd.setMilliseconds(dayEnd.getMilliseconds() - 1);
  return boundary === 'start' ? dayStart.getTime() : dayEnd.getTime();
};

const resolveNoteTimePointExpr = (expr?: Partial<NoteTimePointExpr> | null): number | null => {
  const normalized = normalizeNoteTimePointExpr(expr);
  if (normalized.kind === 'absolute') {
    return isFiniteNumber(normalized.value) ? Number(normalized.value) : null;
  }
  return resolveRelativeTimePoint(
    normalized.unit || 'month',
    normalized.offset || 0,
    normalized.boundary || 'start'
  );
};

const getNoteMatcherValue = (note: NoteNode, field: string) => {
  if (field.startsWith('custom_fields.')) {
    const key = field.split('.', 2)[1];
    const customFields = note.custom_fields || [];
    if (Array.isArray(customFields)) {
      for (const item of customFields) {
        if (Array.isArray(item) && item.length >= 3 && item[0] === key) {
          return item[2];
        }
      }
    }
    return null;
  }

  return (note as any)[field];
};

const compareProgramValue = (
  fieldValue: any,
  op: NonNullable<NoteProgramMatcher['op']>,
  value?: any,
  values: any[] = []
) => {
  if (op === 'eq') return fieldValue === value;
  if (op === 'neq') return fieldValue !== value;
  if (op === 'in') return values.includes(fieldValue);
  if (op === 'not_in') return !values.includes(fieldValue);
  if (op === 'contains') {
    if (fieldValue === undefined || fieldValue === null) return false;
    return String(fieldValue).toLowerCase().includes(String(value ?? '').toLowerCase());
  }
  if (op === 'not_contains') {
    if (fieldValue === undefined || fieldValue === null) return true;
    return !String(fieldValue).toLowerCase().includes(String(value ?? '').toLowerCase());
  }
  if (op === 'regex_search') {
    if (fieldValue === undefined || fieldValue === null) return false;
    try {
      return new RegExp(String(value ?? '')).test(String(fieldValue));
    } catch {
      return false;
    }
  }
  if (op === 'gte') return fieldValue !== undefined && fieldValue !== null && fieldValue >= value;
  if (op === 'lte') return fieldValue !== undefined && fieldValue !== null && fieldValue <= value;
  if (op === 'between') {
    if (!Array.isArray(values) || values.length < 2 || fieldValue === undefined || fieldValue === null) return false;
    const [start, end] = values;
    return start <= fieldValue && fieldValue <= end;
  }
  return false;
};

export const matchNoteProgramMatcherLocally = (
  note: NoteNode,
  matcher: NoteProgramMatcher
): boolean => {
  const normalized = normalizeNoteProgramMatcher(matcher);

  if (normalized.kind === 'all') return true;
  if (normalized.kind === 'none') return false;
  if (normalized.kind === 'id') return normalizeProgramIds(normalized.ids).includes(note.id);
  if (normalized.kind === 'field') {
    const field = normalized.field ?? '';
    const fieldValue = getNoteMatcherValue(note, field);
    const isTimeField = field === 'start_at' || field === 'updated_at';
    if (isTimeField && normalized.time_value) {
      return compareProgramValue(fieldValue, normalized.op || 'eq', resolveNoteTimePointExpr(normalized.time_value));
    }
    if (isTimeField && normalized.time_values.length > 0) {
      const resolved = normalized.time_values
        .map(item => resolveNoteTimePointExpr(item))
        .filter((item): item is number => isFiniteNumber(item));
      return compareProgramValue(fieldValue, normalized.op || 'between', undefined, resolved);
    }
    return compareProgramValue(fieldValue, normalized.op || 'eq', normalized.value, Array.isArray(normalized.values) ? normalized.values : []);
  }

  return false;
};

export const applyNoteProgramChannelLocally = (
  notes: NoteNode[],
  channel?: Partial<NoteProgramChannel> | null
): NoteNode[] => {
  const normalizedChannel = normalizeNoteProgramChannel(channel);

  return notes.filter(note => {
    let decision = normalizedChannel.default;

    for (const rule of normalizedChannel.rules) {
      const target = rule.action === 'include';
      if (decision === target) continue;
      if (matchNoteProgramMatcherLocally(note, rule.matcher)) {
        decision = target;
      }
    }

    return decision;
  });
};

export const createIncludeAllProgram = (): NoteProgramChannel => ({
  default: false,
  rules: [
    createNoteProgramRule('include', 'all')
  ]
});

export const buildScanNoteProgramRequest = (
  channel: NoteProgramChannel,
  overrides: Partial<NonNullable<NoteProgramRequest['result']>> = {}
): NoteProgramRequest => ({
  executor: {
    kind: 'scan'
  },
  program: {
    select: {
      default: normalizeNoteProgramChannel(channel).default,
      rules: normalizeNoteProgramChannel(channel).rules.map(rule => ({
        action: rule.action,
        matcher: normalizeMatcherForApi(rule.matcher)
      }))
    },
    expand: createEmptyNoteProgramChannel(false)
  },
  result: {
    include_edges: false,
    order_by: 'updated_at',
    order_desc: true,
    skip: 0,
    limit: 1000,
    ...overrides
  }
});

export const createEmptyNoteScopeState = (): NoteScopeState => ({
  titleKeyword: '',
  primaryCategory: '',
  lifecycleStage: '',
  startRange: [],
  updatedRange: []
});

export const normalizeNoteScopeState = (value?: NoteScopeStateInput | null): NoteScopeState => ({
  titleKeyword: typeof value?.titleKeyword === 'string' ? value.titleKeyword : '',
  primaryCategory: typeof value?.primaryCategory === 'string'
    ? value.primaryCategory
    : typeof value?.nodeType === 'string'
      ? value.nodeType
      : '',
  lifecycleStage: typeof value?.lifecycleStage === 'string'
    ? value.lifecycleStage
    : typeof value?.nodeStatus === 'string'
      ? value.nodeStatus
      : '',
  startRange: normalizeRangeInput(value?.startRange),
  updatedRange: normalizeRangeInput(value?.updatedRange)
});

export const buildNoteQueryFromScope = (
  scope: NoteScopeStateInput,
  overrides: Partial<NoteQueryRequest> = {}
): NoteQueryRequest => {
  const normalizedScope = normalizeNoteScopeState(scope);
  const rules: NoteFilterRule[] = [];

  if (normalizedScope.titleKeyword.trim()) {
    rules.push({
      field: 'title',
      op: 'contains',
      value: normalizedScope.titleKeyword.trim()
    });
  }

  if (normalizedScope.primaryCategory) {
    rules.push({
      field: 'primary_category',
      op: 'eq',
      value: normalizedScope.primaryCategory
    });
  }

  if (normalizedScope.lifecycleStage) {
    rules.push({
      field: 'lifecycle_stage',
      op: 'eq',
      value: normalizedScope.lifecycleStage
    });
  }

  appendRangeRules(rules, 'start_at', normalizedScope.startRange);
  appendRangeRules(rules, 'updated_at', normalizedScope.updatedRange);

  return {
    scope: { mode: 'all' },
    rules,
    order_by: 'updated_at',
    order_desc: true,
    limit: 1000,
    include_edges: true,
    ...overrides
  };
};

const buildTimeRules = (params: NoteQueryParams): NoteFilterRule[] => {
  const rules: NoteFilterRule[] = [];

  if (params.created_start !== undefined && params.created_end !== undefined) {
    rules.push({
      field: 'start_at',
      op: 'between',
      values: [toApiTimestamp(params.created_start), toApiTimestamp(params.created_end)]
    });
  } else if (params.created_start !== undefined) {
    rules.push({
      field: 'start_at',
      op: 'gte',
      value: toApiTimestamp(params.created_start)
    });
  } else if (params.created_end !== undefined) {
    rules.push({
      field: 'start_at',
      op: 'lte',
      value: toApiTimestamp(params.created_end)
    });
  }

  if (params.updated_start !== undefined && params.updated_end !== undefined) {
    rules.push({
      field: 'updated_at',
      op: 'between',
      values: [toApiTimestamp(params.updated_start), toApiTimestamp(params.updated_end)]
    });
  } else if (params.updated_start !== undefined) {
    rules.push({
      field: 'updated_at',
      op: 'gte',
      value: toApiTimestamp(params.updated_start)
    });
  } else if (params.updated_end !== undefined) {
    rules.push({
      field: 'updated_at',
      op: 'lte',
      value: toApiTimestamp(params.updated_end)
    });
  }

  return rules;
};

export const useNoteStore = defineStore('notes', () => {
  const noteMap = ref<Record<string, NoteNode>>({});
  const noteDetailMap = ref<Record<string, NoteNode>>({});
  const edgeMap = ref<Record<string, NoteEdge>>({});

  const noteTouchedAt = ref<Record<string, number>>({});
  const noteDetailTouchedAt = ref<Record<string, number>>({});
  const edgeTouchedAt = ref<Record<string, number>>({});

  const pendingRequests = ref(0);
  const loading = computed(() => pendingRequests.value > 0);

  const tabs = ref<TabState[]>([
    { id: 'calendar', label: '日历', type: 'calendar', closable: false },
    { id: 'galaxy', label: '星系', type: 'galaxy', closable: false },
    { id: 'list', label: '列表', type: 'list', closable: false }
  ]);
  const activeTabId = ref('calendar');

  const loadPersistedTabViewStates = (): Record<string, Record<string, any>> => {
    if (!canUseLocalStorage()) return {};

    try {
      NOTE_TAB_VIEW_STATE_LEGACY_KEYS.forEach(key => window.localStorage.removeItem(key));

      const raw = window.localStorage.getItem(NOTE_TAB_VIEW_STATE_STORAGE_KEY);
      if (!raw) return {};

      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        window.localStorage.removeItem(NOTE_TAB_VIEW_STATE_STORAGE_KEY);
        return {};
      }

      if (parsed.version !== NOTE_TAB_VIEW_STATE_STORAGE_VERSION || !parsed.tabs || typeof parsed.tabs !== 'object' || Array.isArray(parsed.tabs)) {
        window.localStorage.removeItem(NOTE_TAB_VIEW_STATE_STORAGE_KEY);
        return {};
      }

      return Object.fromEntries(
        Object.entries(parsed.tabs).filter(([, value]) => value && typeof value === 'object' && !Array.isArray(value))
      ) as Record<string, Record<string, any>>;
    } catch (error) {
      console.warn('Failed to load persisted note tab view state:', error);
      window.localStorage.removeItem(NOTE_TAB_VIEW_STATE_STORAGE_KEY);
      return {};
    }
  };

  const persistedTabViewStates = ref<Record<string, Record<string, any>>>(loadPersistedTabViewStates());

  const persistTabViewStates = () => {
    if (!canUseLocalStorage()) return;

    try {
      window.localStorage.setItem(
        NOTE_TAB_VIEW_STATE_STORAGE_KEY,
        JSON.stringify({
          version: NOTE_TAB_VIEW_STATE_STORAGE_VERSION,
          tabs: persistedTabViewStates.value
        })
      );
    } catch (error) {
      console.warn('Failed to persist note tab view state:', error);
    }
  };

  const createDefaultViewState = (tab: TabState) => {
    if (tab.type === 'galaxy') {
      return {
        dataProgram: createDefaultRecentMonthProgram('start_at'),
        viewProgram: createIncludeAllProgram()
      };
    }

    if (tab.type === 'calendar') {
      return {
        currentMonth: new Date().toISOString(),
        viewProgram: createIncludeAllProgram()
      };
    }

    if (tab.type === 'list') {
      return {
        dataProgram: createDefaultRecentMonthProgram('start_at'),
        viewProgram: createIncludeAllProgram()
      };
    }

    return {
      targetNoteId: tab.data?.noteId ?? null,
      graphMode: tab.data?.mode ?? 'planetary'
    };
  };

  const getPersistedViewState = (tabId: string) => {
    const value = persistedTabViewStates.value[tabId];
    return value ? cloneViewState(value) : null;
  };

  const saveTabViewState = (tabId: string, viewState: Record<string, any>) => {
    persistedTabViewStates.value[tabId] = cloneViewState(viewState);
    persistTabViewStates();
  };

  const removePersistedTabViewState = (tabId: string) => {
    if (!(tabId in persistedTabViewStates.value)) return;
    delete persistedTabViewStates.value[tabId];
    persistTabViewStates();
  };

  const createTabSession = (tab: TabState): TabSession => {
    const viewState = {
      ...cloneViewState(createDefaultViewState(tab)),
      ...(getPersistedViewState(tab.id) ?? {})
    };

    saveTabViewState(tab.id, viewState);

    return {
      tabId: tab.id,
      noteIds: [],
      edgeIds: [],
      loading: false,
      requestVersion: 0,
      lastQuery: null,
      viewState
    };
  };

  const tabSessions = ref<Record<string, TabSession>>(
    Object.fromEntries(tabs.value.map(tab => [tab.id, createTabSession(tab)]))
  );

  const bumpPending = (delta: number) => {
    pendingRequests.value = Math.max(0, pendingRequests.value + delta);
  };

  const touchNotes = (ids: string[]) => {
    const now = Date.now();
    ids.forEach(id => {
      noteTouchedAt.value[id] = now;
    });
  };

  const touchNoteDetails = (ids: string[]) => {
    const now = Date.now();
    ids.forEach(id => {
      noteDetailTouchedAt.value[id] = now;
    });
  };

  const touchEdges = (ids: string[]) => {
    const now = Date.now();
    ids.forEach(id => {
      edgeTouchedAt.value[id] = now;
    });
  };

  const pruneNoteSummaryCache = () => {
    const noteIds = Object.keys(noteMap.value);
    if (noteIds.length <= NOTE_SUMMARY_CACHE_LIMIT) return;

    const pinnedIds = new Set(
      Object.values(tabSessions.value).flatMap(session => session.noteIds)
    );

    const removableIds = noteIds
      .filter(id => !pinnedIds.has(id))
      .sort((a, b) => (noteTouchedAt.value[a] || 0) - (noteTouchedAt.value[b] || 0));

    const overflow = noteIds.length - NOTE_SUMMARY_CACHE_LIMIT;
    removableIds.slice(0, overflow).forEach(id => {
      delete noteMap.value[id];
      delete noteTouchedAt.value[id];
      delete noteDetailMap.value[id];
      delete noteDetailTouchedAt.value[id];
    });
  };

  const pruneNoteDetailCache = () => {
    const detailIds = Object.keys(noteDetailMap.value);
    if (detailIds.length <= NOTE_DETAIL_CACHE_LIMIT) return;

    const pinnedIds = new Set(
      Object.values(tabSessions.value).flatMap(session => session.noteIds)
    );

    const removableIds = detailIds
      .filter(id => !pinnedIds.has(id))
      .sort((a, b) => (noteDetailTouchedAt.value[a] || 0) - (noteDetailTouchedAt.value[b] || 0));

    const overflow = detailIds.length - NOTE_DETAIL_CACHE_LIMIT;
    removableIds.slice(0, overflow).forEach(id => {
      delete noteDetailMap.value[id];
      delete noteDetailTouchedAt.value[id];
    });
  };

  const pruneEdgeCache = () => {
    const edgeIds = Object.keys(edgeMap.value);
    if (edgeIds.length <= EDGE_CACHE_LIMIT) return;

    const pinnedIds = new Set(
      Object.values(tabSessions.value).flatMap(session => session.edgeIds)
    );

    const removableIds = edgeIds
      .filter(id => !pinnedIds.has(id))
      .sort((a, b) => (edgeTouchedAt.value[a] || 0) - (edgeTouchedAt.value[b] || 0));

    const overflow = edgeIds.length - EDGE_CACHE_LIMIT;
    removableIds.slice(0, overflow).forEach(id => {
      delete edgeMap.value[id];
      delete edgeTouchedAt.value[id];
    });
  };

  const pruneCaches = () => {
    pruneNoteSummaryCache();
    pruneNoteDetailCache();
    pruneEdgeCache();
  };

  const mergeNoteSummaries = (incomingNotes: NoteNode[]) => {
    const ids: string[] = [];
    incomingNotes.forEach(note => {
      const summary = stripNoteDetail(note);
      noteMap.value[note.id] = {
        ...noteMap.value[note.id],
        ...summary
      };
      ids.push(note.id);
    });
    touchNotes(ids);
    return ids;
  };

  const mergeNoteDetails = (incomingNotes: NoteNode[]) => {
    const ids: string[] = [];
    incomingNotes.forEach(note => {
      const existing = noteDetailMap.value[note.id];
      noteDetailMap.value[note.id] = {
        ...existing,
        ...note,
        inherited_fields: note.inherited_fields ?? existing?.inherited_fields
      };
      ids.push(note.id);
    });
    touchNoteDetails(ids);
    return ids;
  };

  const mergeEdges = (incomingEdges: NoteEdge[]) => {
    const ids: string[] = [];
    incomingEdges.forEach(edge => {
      edgeMap.value[edge.id] = edge;
      ids.push(edge.id);
    });
    touchEdges(ids);
    return ids;
  };

  const getTabById = (tabId: string) => tabs.value.find(tab => tab.id === tabId);

  const ensureTabSession = (tabId: string) => {
    const tab = getTabById(tabId);
    if (!tab) return null;

    if (!tabSessions.value[tabId]) {
      tabSessions.value[tabId] = createTabSession(tab);
    }

    if (tab.type === 'planet') {
      tabSessions.value[tabId].viewState = {
        ...tabSessions.value[tabId].viewState,
        targetNoteId: tab.data?.noteId ?? null,
        graphMode: tab.data?.mode ?? 'planetary'
      };
      saveTabViewState(tabId, tabSessions.value[tabId].viewState);
    }

    return tabSessions.value[tabId];
  };

  const getTabSession = (tabId: string) => ensureTabSession(tabId);

  const setTabData = (
    tabId: string,
    noteIds: string[],
    edgeIds: string[],
    lastQuery: NoteQueryRequest | NoteProgramRequest | null = null
  ) => {
    const session = ensureTabSession(tabId);
    if (!session) return;

    session.noteIds = dedupeIds(noteIds);
    session.edgeIds = dedupeIds(edgeIds);
    session.lastQuery = lastQuery;
    session.lastLoadedAt = Date.now();

    touchNotes(session.noteIds);
    touchEdges(session.edgeIds);
    pruneCaches();
  };

  const applyQueryResponseToTab = (
    tabId: string,
    request: NoteQueryRequest | NoteProgramRequest,
    data: NoteQueryResponse | NoteProgramResponse
  ) => {
    const fetchedNotes = data.nodes.map((note: any) => normalizeNote(note));
    const noteIds = mergeNoteSummaries(fetchedNotes);

    const fetchedEdges = data.edges.map((edge: any) => normalizeEdge(edge));
    const edgeIds = mergeEdges(fetchedEdges);

    setTabData(tabId, noteIds, edgeIds, request);
    return {
      noteIds,
      edgeIds
    };
  };

  const clearTabData = (tabId: string) => {
    const session = ensureTabSession(tabId);
    if (!session) return;

    session.noteIds = [];
    session.edgeIds = [];
    session.lastQuery = null;
    session.lastLoadedAt = Date.now();
  };

  const updateTabViewState = (tabId: string, patch: Record<string, any>) => {
    const session = ensureTabSession(tabId);
    if (!session) return;

    session.viewState = {
      ...session.viewState,
      ...patch
    };
    saveTabViewState(tabId, session.viewState);
  };

  const addNoteToTab = (tabId: string, noteId: string) => {
    const session = ensureTabSession(tabId);
    if (!session) return;

    if (!session.noteIds.includes(noteId)) {
      session.noteIds = [noteId, ...session.noteIds];
      session.lastLoadedAt = Date.now();
      touchNotes([noteId]);
    }

    const visibleNoteIds = new Set(session.noteIds);
    const missingEdgeIds = Object.values(edgeMap.value)
      .filter(edge => visibleNoteIds.has(edge.source_id) && visibleNoteIds.has(edge.target_id))
      .map(edge => edge.id)
      .filter(edgeId => !session.edgeIds.includes(edgeId));

    if (missingEdgeIds.length > 0) {
      session.edgeIds = [...missingEdgeIds, ...session.edgeIds];
    }

    pruneCaches();
  };

  const getNoteSummaryById = (id: string) => {
    return noteMap.value[id];
  };

  const getNoteById = (id: string) => {
    const summary = noteMap.value[id];
    const detail = noteDetailMap.value[id];
    if (!summary && !detail) return undefined;

    return detail ? { ...summary, ...detail } : summary;
  };

  const getEdgeById = (id: string) => edgeMap.value[id];

  const getTabNotes = (tabId: string) => {
    const session = ensureTabSession(tabId);
    if (!session) return [] as NoteNode[];

    return session.noteIds
      .map(id => getNoteSummaryById(id))
      .filter((note): note is NoteNode => Boolean(note));
  };

  const getTabEdges = (tabId: string) => {
    const session = ensureTabSession(tabId);
    if (!session) return [] as NoteEdge[];

    return session.edgeIds
      .map(id => getEdgeById(id))
      .filter((edge): edge is NoteEdge => Boolean(edge));
  };

  const addTab = (tab: TabState) => {
    const existing = tabs.value.find(t => t.id === tab.id);
    if (existing) {
      activeTabId.value = existing.id;
      if (tab.data) {
        existing.data = tab.data;
      }
      ensureTabSession(existing.id);
      return;
    }

    tabs.value.push(tab);
    ensureTabSession(tab.id);
    activeTabId.value = tab.id;
  };

  const removeTab = (tabId: string) => {
    const index = tabs.value.findIndex(t => t.id === tabId);
    if (index === -1) return;

    const tabToRemove = tabs.value[index];
    if (!tabToRemove.closable) return;

    tabs.value.splice(index, 1);
    delete tabSessions.value[tabId];
    removePersistedTabViewState(tabId);

    if (activeTabId.value === tabId) {
      const firstTab = tabs.value[0];
      if (firstTab) {
        activeTabId.value = firstTab.id;
      }
    }

    pruneCaches();
  };

  const setActiveTab = (tabId: string) => {
    if (tabs.value.find(t => t.id === tabId)) {
      activeTabId.value = tabId;
      ensureTabSession(tabId);
    }
  };

  const queryNotesForTab = async (tabId: string, request: NoteQueryRequest) => {
    const session = ensureTabSession(tabId);
    if (!session) return null;

    const requestVersion = session.requestVersion + 1;
    session.requestVersion = requestVersion;
    session.loading = true;
    bumpPending(1);
    try {
      const response = await api.post('/notes/query', request);
      const data = response.data as NoteQueryResponse;
      if (session.requestVersion !== requestVersion) return null;
      return applyQueryResponseToTab(tabId, request, data);
    } catch (error) {
      if (session.requestVersion !== requestVersion) return null;
      console.error('Failed to fetch notes or edges:', error);
      ElMessage.error('获取数据失败');
      return null;
    } finally {
      if (session.requestVersion === requestVersion) {
        session.loading = false;
      }
      bumpPending(-1);
    }
  };

  const queryNoteProgramForTab = async (tabId: string, request: NoteProgramRequest) => {
    const session = ensureTabSession(tabId);
    if (!session) return null;

    const requestVersion = session.requestVersion + 1;
    session.requestVersion = requestVersion;
    session.loading = true;
    bumpPending(1);
    try {
      const response = await api.post('/notes/query-program', request);
      const data = response.data as NoteProgramResponse;
      if (session.requestVersion !== requestVersion) return null;
      return applyQueryResponseToTab(tabId, request, data);
    } catch (error) {
      if (session.requestVersion !== requestVersion) return null;
      console.error('Failed to run note program:', error);
      ElMessage.error('执行筛选程序失败');
      return null;
    } finally {
      if (session.requestVersion === requestVersion) {
        session.loading = false;
      }
      bumpPending(-1);
    }
  };

  const fetchNotesForTab = async (tabId: string, params: NoteQueryParams = {}) => {
    return queryNotesForTab(tabId, {
      scope: { mode: 'all' },
      rules: buildTimeRules(params),
      order_by: 'updated_at',
      order_desc: true,
      limit: params.limit ?? 1000,
      include_edges: true
    });
  };

  const fetchNoteDetail = async (id: string) => {
    const cached = noteDetailMap.value[id];
    if (cached?.content !== undefined) {
      touchNoteDetails([id]);
      return getNoteById(id) || cached;
    }

    bumpPending(1);
    try {
      const response = await api.get(`/notes/${id}`);
      const detailedNote = normalizeNote(response.data);
      mergeNoteSummaries([detailedNote]);
      mergeNoteDetails([detailedNote]);
      pruneCaches();
      return getNoteById(id) || detailedNote;
    } catch (error) {
      console.error('Failed to fetch note detail:', error);
      ElMessage.error('获取节点内容失败');
      return null;
    } finally {
      bumpPending(-1);
    }
  };

  const fetchConnectedComponentForTab = async (
    tabId: string,
    id: string,
    mode: 'planetary' | 'satellite' = 'planetary'
  ) => {
    const result = await queryNotesForTab(tabId, {
      scope: {
        mode,
        seed_note_id: id
      },
      rules: [],
      order_by: 'updated_at',
      order_desc: true,
      limit: 5000,
      include_edges: true
    });

    if (result) {
      updateTabViewState(tabId, {
        targetNoteId: id,
        graphMode: mode
      });
    }

    return result;
  };

  const createNote = async (
    title: string,
    content: string,
    weight: number = NOTE_WEIGHT_DEFAULT,
    start_at?: number,
    node_type: string | null = 'note',
    node_status: string | null = 'idea',
    custom_fields: any[] = [],
    private_level: number = 0,
    color: string | null = null,
    weight_mode: string | null = null,
    note_kind: string | null = 'note',
    note_types: NoteTypeAssignment[] = [],
    note_categories: NoteTypeAssignment[] = [],
    primary_category: string | null = NOTE_CATEGORY_DEFAULT,
    note_form: string | null = NOTE_FORM_DEFAULT,
    note_scene: string | null = NOTE_SCENE_DEFAULT,
    lifecycle_stage: string | null = NOTE_LIFECYCLE_STAGE_DEFAULT,
    completion_progress_expr: string | null = null
  ) => {
    bumpPending(1);
    try {
      const taxonomy = note_categories.length > 0 || primary_category !== NOTE_CATEGORY_DEFAULT || note_form !== NOTE_FORM_DEFAULT || note_scene !== (note_kind ?? NOTE_SCENE_DEFAULT) || lifecycle_stage !== (node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT)
        ? deriveLegacySemanticsFromTaxonomy(
          note_categories,
          primary_category ?? NOTE_CATEGORY_DEFAULT,
          note_form ?? NOTE_FORM_DEFAULT,
          note_scene ?? note_kind ?? NOTE_SCENE_DEFAULT,
          lifecycle_stage ?? node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
        )
        : {
          ...deriveNoteTaxonomyFromLegacy(
            note_types,
            node_type ?? 'note',
            note_kind ?? NOTE_SCENE_DEFAULT,
            node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
          ),
          note_types,
          node_type,
          note_kind,
          node_status
        };
      const data: any = {
        title,
        content,
        weight,
        node_type: taxonomy.node_type ?? node_type,
        note_types: taxonomy.note_types ?? note_types,
        note_categories: taxonomy.note_categories,
        primary_category: taxonomy.primary_category,
        note_form: taxonomy.note_form,
        note_kind: taxonomy.note_kind ?? note_kind,
        note_scene: taxonomy.note_scene,
        node_status: taxonomy.node_status ?? node_status,
        lifecycle_stage: taxonomy.lifecycle_stage,
        color,
        weight_mode,
        custom_fields,
        completion_progress_expr,
        private_level: normalizeInteger(private_level, 0)
      };
      if (start_at !== undefined) data.start_at = start_at / 1000;

      const response = await api.post('/notes/', data);
      const newNote = normalizeNote(response.data);
      mergeNoteSummaries([newNote]);
      mergeNoteDetails([newNote]);
      pruneCaches();
      return getNoteById(newNote.id) || newNote;
    } catch (error) {
      console.error('Failed to create note:', error);
      ElMessage.error('创建任务失败');
      return null;
    } finally {
      bumpPending(-1);
    }
  };

  const updateNote = async (
    id: string,
    data: {
      title?: string;
      content?: string;
      weight?: number;
      start_at?: number;
      node_type?: string | null;
      note_types?: NoteTypeAssignment[];
      note_categories?: NoteTypeAssignment[];
      primary_category?: string | null;
      note_form?: string | null;
      note_kind?: string | null;
      note_scene?: string | null;
      node_status?: string | null;
      lifecycle_stage?: string | null;
      color?: string | null;
      weight_mode?: string | null;
      private_level?: number;
      custom_fields?: any[];
      completion_progress_expr?: string | null;
    }
  ) => {
    bumpPending(1);
    try {
      const updateData: any = { ...data };
      if (data.start_at !== undefined) updateData.start_at = data.start_at / 1000;
      if (data.private_level !== undefined) updateData.private_level = normalizeInteger(data.private_level, 0);

      const response = await api.put(`/notes/${id}`, updateData);
      const updatedNote = normalizeNote(response.data);
      mergeNoteSummaries([updatedNote]);
      mergeNoteDetails([updatedNote]);
      pruneCaches();
      return getNoteById(id) || updatedNote;
    } catch (error) {
      console.error('Failed to update note:', error);
      ElMessage.error('保存任务失败');
      return null;
    } finally {
      bumpPending(-1);
    }
  };

  const batchUpdateNotes = async (request: NoteBatchUpdateRequest) => {
    bumpPending(1);
    try {
      const response = await api.post('/notes/batch-update', request);
      const data = response.data as NoteBatchUpdateResponse;
      const updatedNotes = (data.notes || []).map((note: any) => normalizeNote(note));
      mergeNoteSummaries(updatedNotes);
      patchNoteDetails(noteDetailMap.value, updatedNotes);
      pruneCaches();
      return {
        ...data,
        notes: updatedNotes
      };
    } catch (error) {
      console.error('Failed to batch update notes:', error);
      ElMessage.error('批量更新失败');
      return null;
    } finally {
      bumpPending(-1);
    }
  };

  const deleteNote = async (id: string) => {
    bumpPending(1);
    try {
      await api.delete(`/notes/${id}`);

      delete noteMap.value[id];
      delete noteTouchedAt.value[id];
      delete noteDetailMap.value[id];
      delete noteDetailTouchedAt.value[id];

      const removedEdgeIds = Object.values(edgeMap.value)
        .filter(edge => edge.source_id === id || edge.target_id === id)
        .map(edge => edge.id);

      removedEdgeIds.forEach(edgeId => {
        delete edgeMap.value[edgeId];
        delete edgeTouchedAt.value[edgeId];
      });

      Object.values(tabSessions.value).forEach(session => {
        session.noteIds = session.noteIds.filter(noteId => noteId !== id);
        session.edgeIds = session.edgeIds.filter(edgeId => !removedEdgeIds.includes(edgeId));
      });

      pruneCaches();
      return true;
    } catch (error) {
      console.error('Failed to delete note:', error);
      ElMessage.error('删除任务失败');
      return false;
    } finally {
      bumpPending(-1);
    }
  };

  const createEdge = async (
    sourceId: string,
    targetId: string,
    sourceHandle?: string,
    targetHandle?: string
  ) => {
    bumpPending(1);
    try {
      const response = await api.post('/notes/edges/', {
        source_id: sourceId,
        target_id: targetId,
        source_handle: sourceHandle,
        target_handle: targetHandle
      });
      const newEdge = normalizeEdge(response.data);
      mergeEdges([newEdge]);

      Object.values(tabSessions.value).forEach(session => {
        const visibleNotes = new Set(session.noteIds);
        if (visibleNotes.has(sourceId) && visibleNotes.has(targetId) && !session.edgeIds.includes(newEdge.id)) {
          session.edgeIds = [...session.edgeIds, newEdge.id];
        }
      });

      pruneCaches();
      return newEdge;
    } catch (error) {
      console.error('Failed to create edge:', error);
      return null;
    } finally {
      bumpPending(-1);
    }
  };

  const deleteEdge = async (sourceId: string, targetId: string) => {
    bumpPending(1);
    try {
      await api.delete(`/notes/edges/?source=${sourceId}&target=${targetId}`);

      const removedEdgeIds = Object.values(edgeMap.value)
        .filter(edge => edge.source_id === sourceId && edge.target_id === targetId)
        .map(edge => edge.id);

      removedEdgeIds.forEach(edgeId => {
        delete edgeMap.value[edgeId];
        delete edgeTouchedAt.value[edgeId];
      });

      Object.values(tabSessions.value).forEach(session => {
        session.edgeIds = session.edgeIds.filter(edgeId => !removedEdgeIds.includes(edgeId));
      });

      return true;
    } catch (error) {
      console.error('Failed to delete edge:', error);
      return false;
    } finally {
      bumpPending(-1);
    }
  };

  const aiCategorizeNote = async (id: string) => {
    const userStore = useUserStore();
    const aiProviderStore = useAiProviderStore();
    const aiAppStore = useAiAppStore();

    aiAppStore.ensureLoaded();
    if (
      aiProviderStore.providers.length === 0
      || aiProviderStore.loadedForAuthState !== userStore.isAuthenticated
    ) {
      await aiProviderStore.loadProviders(userStore.isAuthenticated);
    }

    const appConfig = aiAppStore.getAppConfig('note-taxonomy');
    if (!appConfig.enabled) {
      ElMessage.warning('“笔记分类”应用当前已停用');
      return null;
    }

    const providerId = appConfig.provider || aiProviderStore.defaultProviderId || 'ollama';
    const providerMeta = aiProviderStore.getProviderById(providerId);
    if (!providerMeta) {
      ElMessage.error('当前 AI 应用绑定的来源不存在');
      return null;
    }

    const providerConfig = aiProviderStore.getProviderConfig(providerId);
    const effectiveBaseUrl = providerConfig.baseUrl.trim() || providerMeta.base_url.trim();
    if (!effectiveBaseUrl) {
      ElMessage.error(`请先在 AI 配置里为“${providerMeta.label}”填写地址`);
      return null;
    }

    if (providerMeta.requires_api_key && !aiProviderStore.hasEffectiveApiKey(providerId)) {
      const keyLabel = providerId.trim().toLowerCase() === 'ollama' ? '访问 Key' : 'API Key';
      ElMessage.error(`请先在 AI 配置里为“${providerMeta.label}”保存${keyLabel}，再使用“笔记分类”`);
      return null;
    }

    if (!aiProviderStore.hasEffectiveConnection(providerId)) {
      ElMessage.error(`AI 来源“${providerMeta.label}”尚未配置完成`);
      return null;
    }

    const modelName = appConfig.model.trim() || aiProviderStore.getEffectiveModel(providerId).trim();
    if (!modelName) {
      ElMessage.error('请先为“笔记分类”选择模型');
      return null;
    }

    const connectionPayload = aiProviderStore.buildConnectionPayload(providerId);

    bumpPending(1);
    try {
      const response = await api.post(`/notes/${encodeURIComponent(id)}/ai-categorize`, {
        ...connectionPayload,
        model: modelName,
      } satisfies AiNoteCategorizeRequest);

      const payload = response.data as {
        app: string;
        provider: string;
        model: string;
        summary: string;
        note: unknown;
      };
      const updatedNote = normalizeNote(payload.note);
      mergeNoteSummaries([updatedNote]);
      mergeNoteDetails([updatedNote]);
      pruneCaches();
      return {
        app: String(payload.app || 'note-taxonomy'),
        provider: String(payload.provider || providerId),
        model: String(payload.model || modelName),
        summary: typeof payload.summary === 'string' ? payload.summary : '',
        note: getNoteById(updatedNote.id) || updatedNote,
      } satisfies AiNoteCategorizeResponse;
    } catch (error) {
      console.error('Failed to categorize note with AI:', error);
      const maybeError = error as {
        response?: {
          data?: {
            detail?: string;
            message?: string;
          };
        };
        message?: string;
      };
      ElMessage.error(
        maybeError.response?.data?.detail
        || maybeError.response?.data?.message
        || maybeError.message
        || 'AI 分类失败'
      );
      return null;
    } finally {
      bumpPending(-1);
    }
  };

  return {
    loading,
    tabs,
    activeTabId,
    tabSessions,
    addTab,
    removeTab,
    setActiveTab,
    getTabSession,
    updateTabViewState,
    getTabNotes,
    getTabEdges,
    getNoteById,
    queryNotesForTab,
    queryNoteProgramForTab,
    fetchNotesForTab,
    fetchNoteDetail,
    fetchConnectedComponentForTab,
    createNote,
    updateNote,
    aiCategorizeNote,
    batchUpdateNotes,
    deleteNote,
    createEdge,
    deleteEdge,
    addNoteToTab,
    clearTabData
  };
});

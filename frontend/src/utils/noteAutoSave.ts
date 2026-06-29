import type { NoteNode } from '@/api/notes';
import type { NoteTypeAssignment } from './nodeConfig';
import {
  derivePrimaryCategory,
  deriveLegacySemanticsFromTaxonomy,
  NOTE_CATEGORY_DEFAULT,
  NOTE_FORM_DEFAULT,
  NOTE_LIFECYCLE_STAGE_DEFAULT,
  normalizeNoteCategories
} from './noteSemantics';

export type NoteCustomFieldType = 'string' | 'number' | 'boolean' | 'richtext';
export type NoteCustomFieldStoredValue = string | number | boolean;
export type NoteCustomFieldEditorValue = string | boolean;
export type NoteCustomFieldTuple = [string, NoteCustomFieldType, NoteCustomFieldStoredValue];

export interface NoteCustomFieldItem {
  localId: string;
  key: string;
  type: NoteCustomFieldType;
  value: NoteCustomFieldEditorValue;
}

export interface EditableNoteSnapshot {
  id: string;
  title: string;
  content: string;
  weight: number;
  start_at: number;
  note_categories: NoteTypeAssignment[];
  primary_category: string | null;
  note_form: string | null;
  lifecycle_stage: string | null;
  color: string | null;
  private_level: number;
  custom_fields: NoteCustomFieldTuple[];
}

export interface EditableNotePatch {
  title?: string;
  content?: string;
  weight?: number;
  start_at?: number;
  note_categories?: NoteTypeAssignment[];
  primary_category?: string | null;
  note_form?: string | null;
  lifecycle_stage?: string | null;
  color?: string | null;
  private_level?: number;
  custom_fields?: NoteCustomFieldTuple[];
}

const normalizeText = (value: unknown) => value == null ? '' : String(value);
const STANDARD_NUMBER_PATTERN = /^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$/;
const BOOLEAN_TRUE_TOKENS = new Set(['true', '1', 'yes', 'y', 'on']);
const BOOLEAN_FALSE_TOKENS = new Set(['false', '0', 'no', 'n', 'off', '']);
let noteCustomFieldLocalIdSeed = 0;

const createNoteCustomFieldLocalId = () => `note-custom-field-${noteCustomFieldLocalIdSeed++}`;
const normalizeTimestamp = (value: unknown) => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return Date.now();
};

export const normalizeNoteCustomFieldType = (type: unknown): NoteCustomFieldType => {
  if (type === 'number' || type === 'boolean' || type === 'richtext') return type;
  return 'string';
};

export const parseNoteCustomFieldNumber = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string') return null;

  const trimmed = value.trim();
  if (!trimmed || !STANDARD_NUMBER_PATTERN.test(trimmed)) return null;

  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

export const parseNoteCustomFieldBoolean = (value: unknown): boolean | null => {
  if (typeof value === 'boolean') return value;

  const parsedNumber = parseNoteCustomFieldNumber(value);
  if (parsedNumber !== null) return parsedNumber !== 0;

  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (BOOLEAN_TRUE_TOKENS.has(normalized)) return true;
    if (BOOLEAN_FALSE_TOKENS.has(normalized)) return false;
    return normalized.length > 0;
  }

  if (value == null) return false;
  return Boolean(value);
};

export const normalizeNoteCustomFieldValue = (
  type: NoteCustomFieldType,
  value: unknown
): NoteCustomFieldEditorValue => {
  if (type === 'boolean') return parseNoteCustomFieldBoolean(value) ?? false;
  if (type === 'richtext') return normalizeText(value);
  if (type === 'number') {
    if (typeof value === 'boolean') return value ? '1' : '0';
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    if (typeof value === 'string') {
      const parsed = parseNoteCustomFieldNumber(value);
      return parsed === null ? value : value.trim();
    }
  }
  return normalizeText(value);
};

export const convertNoteCustomFieldValue = (
  type: NoteCustomFieldType,
  value: unknown
): NoteCustomFieldEditorValue => {
  if (type === 'string' || type === 'richtext') {
    return typeof value === 'boolean' ? (value ? 'true' : 'false') : normalizeText(value);
  }
  if (type === 'number') {
    if (typeof value === 'boolean') return value ? '1' : '0';
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    const text = normalizeText(value);
    const parsed = parseNoteCustomFieldNumber(text);
    return parsed === null ? text : text.trim();
  }
  return parseNoteCustomFieldBoolean(value) ?? false;
};

export const serializeNoteCustomFieldValue = (
  type: NoteCustomFieldType,
  value: unknown
): NoteCustomFieldStoredValue => {
  if (type === 'boolean') return parseNoteCustomFieldBoolean(value) ?? false;
  if (type === 'richtext') return normalizeText(value);
  if (type === 'number') {
    const parsed = parseNoteCustomFieldNumber(value);
    return parsed ?? normalizeText(value);
  }
  return normalizeText(value);
};

export const createNoteCustomFieldItem = (
  key: unknown = '',
  type: unknown = 'string',
  value: unknown = ''
): NoteCustomFieldItem => {
  const normalizedType = normalizeNoteCustomFieldType(type);
  return {
    localId: createNoteCustomFieldLocalId(),
    key: typeof key === 'string' ? key : '',
    type: normalizedType,
    value: normalizeNoteCustomFieldValue(normalizedType, value)
  };
};

export const noteCustomFieldsToItems = (fields: unknown): NoteCustomFieldItem[] => {
  if (!fields) return [];

  if (Array.isArray(fields)) {
    const items: NoteCustomFieldItem[] = [];
    for (const field of fields) {
      if (Array.isArray(field) && field.length >= 3) {
        const [key, type, value] = field;
        if (typeof key !== 'string' || !key.trim()) continue;
        items.push(createNoteCustomFieldItem(key, type, value));
        continue;
      }

      if (field && typeof field === 'object') {
        const key = (field as any).key;
        if (typeof key !== 'string' || !key.trim()) continue;
        items.push(createNoteCustomFieldItem(key, (field as any).type, (field as any).value));
      }
    }
    return items;
  }

  if (typeof fields === 'object') {
    return Object.entries(fields as Record<string, unknown>).map(([key, value]) => {
      const inferredType: NoteCustomFieldType = typeof value === 'boolean'
        ? 'boolean'
        : typeof value === 'number'
          ? 'number'
          : 'string';
      return createNoteCustomFieldItem(key, inferredType, value);
    });
  }

  return [];
};

export const noteCustomFieldItemsToList = (items: NoteCustomFieldItem[]) => (
  items
    .map(item => {
      const key = item.key?.trim();
      if (!key) return null;
      const normalizedType = normalizeNoteCustomFieldType(item.type);
      return [key, normalizedType, serializeNoteCustomFieldValue(normalizedType, item.value)] as NoteCustomFieldTuple;
    })
    .filter((item): item is NoteCustomFieldTuple => Boolean(item))
);

export const createEditableNoteSnapshot = (
  note: Partial<NoteNode> | null | undefined,
  customFields?: unknown
): EditableNoteSnapshot | null => {
  if (!note?.id) return null;
  const hasExplicitCategories = Array.isArray(note.note_categories);
  const normalizedCategories = normalizeNoteCategories(
    note.note_categories,
    hasExplicitCategories ? (note.primary_category ?? null) : (note.primary_category ?? NOTE_CATEGORY_DEFAULT)
  );
  const primaryCategory = normalizedCategories.length
    ? derivePrimaryCategory(normalizedCategories, note.primary_category ?? NOTE_CATEGORY_DEFAULT)
    : (hasExplicitCategories ? null : (note.primary_category ?? NOTE_CATEGORY_DEFAULT));

  return {
    id: String(note.id),
    title: normalizeText(note.title),
    content: normalizeText(note.content),
    weight: typeof note.weight === 'number' && Number.isFinite(note.weight) ? note.weight : 0,
    start_at: normalizeTimestamp(note.start_at),
    note_categories: normalizedCategories,
    primary_category: primaryCategory,
    note_form: note.note_form ?? NOTE_FORM_DEFAULT,
    lifecycle_stage: note.lifecycle_stage ?? NOTE_LIFECYCLE_STAGE_DEFAULT,
    color: note.color ?? null,
    private_level: typeof note.private_level === 'number' && Number.isFinite(note.private_level) ? note.private_level : 0,
    custom_fields: noteCustomFieldItemsToList(noteCustomFieldsToItems(customFields ?? note.custom_fields ?? []))
  };
};

export const cloneEditableNoteSnapshot = (snapshot: EditableNoteSnapshot) =>
  JSON.parse(JSON.stringify(snapshot)) as EditableNoteSnapshot;

export const areEditableNoteSnapshotsEqual = (
  left: EditableNoteSnapshot,
  right: EditableNoteSnapshot
) => (
  left.id === right.id
  && left.title === right.title
  && left.content === right.content
  && left.weight === right.weight
  && left.start_at === right.start_at
  && left.primary_category === right.primary_category
  && JSON.stringify(left.note_categories) === JSON.stringify(right.note_categories)
  && left.note_form === right.note_form
  && left.lifecycle_stage === right.lifecycle_stage
  && left.color === right.color
  && left.private_level === right.private_level
  && JSON.stringify(left.custom_fields) === JSON.stringify(right.custom_fields)
);

export const buildEditableNotePatch = (
  snapshot: EditableNoteSnapshot,
  baseline: EditableNoteSnapshot | null
): EditableNotePatch => {
  if (!baseline) {
    return {
      title: snapshot.title,
      content: snapshot.content,
      weight: snapshot.weight,
      start_at: snapshot.start_at,
      note_categories: snapshot.note_categories,
      primary_category: snapshot.primary_category,
      note_form: snapshot.note_form,
      lifecycle_stage: snapshot.lifecycle_stage,
      color: snapshot.color,
      private_level: snapshot.private_level,
      custom_fields: snapshot.custom_fields
    };
  }

  const patch: EditableNotePatch = {};

  if (snapshot.title !== baseline.title) patch.title = snapshot.title;
  if (snapshot.content !== baseline.content) patch.content = snapshot.content;
  if (snapshot.weight !== baseline.weight) patch.weight = snapshot.weight;
  if (snapshot.start_at !== baseline.start_at) patch.start_at = snapshot.start_at;
  if (snapshot.primary_category !== baseline.primary_category) patch.primary_category = snapshot.primary_category;
  if (JSON.stringify(snapshot.note_categories) !== JSON.stringify(baseline.note_categories)) patch.note_categories = snapshot.note_categories;
  if (snapshot.note_form !== baseline.note_form) patch.note_form = snapshot.note_form;
  if (snapshot.lifecycle_stage !== baseline.lifecycle_stage) patch.lifecycle_stage = snapshot.lifecycle_stage;
  if (snapshot.color !== baseline.color) patch.color = snapshot.color;
  if (snapshot.private_level !== baseline.private_level) patch.private_level = snapshot.private_level;
  if (JSON.stringify(snapshot.custom_fields) !== JSON.stringify(baseline.custom_fields)) {
    patch.custom_fields = snapshot.custom_fields;
  }

  return patch;
};

export const applyEditableNoteSnapshot = (note: NoteNode, snapshot: EditableNoteSnapshot) => ({
  ...note,
  ...deriveLegacySemanticsFromTaxonomy(
    snapshot.note_categories,
    snapshot.primary_category ?? (snapshot.note_categories.length ? NOTE_CATEGORY_DEFAULT : null),
    snapshot.note_form ?? NOTE_FORM_DEFAULT,
    note.note_scene ?? note.note_kind ?? 'note',
    snapshot.lifecycle_stage ?? NOTE_LIFECYCLE_STAGE_DEFAULT
  ),
  id: snapshot.id,
  title: snapshot.title,
  content: snapshot.content,
  weight: snapshot.weight,
  start_at: snapshot.start_at,
  note_categories: cloneEditableNoteSnapshot(snapshot).note_categories,
  primary_category: snapshot.primary_category,
  note_form: snapshot.note_form,
  lifecycle_stage: snapshot.lifecycle_stage,
  color: snapshot.color,
  private_level: snapshot.private_level,
  custom_fields: cloneEditableNoteSnapshot(snapshot).custom_fields
});

export const noteSnapshotToNode = (
  source: Partial<NoteNode> | null | undefined,
  snapshot: EditableNoteSnapshot
): NoteNode => ({
  ...(source as NoteNode),
  ...deriveLegacySemanticsFromTaxonomy(
    snapshot.note_categories,
    snapshot.primary_category ?? (snapshot.note_categories.length ? NOTE_CATEGORY_DEFAULT : null),
    snapshot.note_form ?? NOTE_FORM_DEFAULT,
    source?.note_scene ?? source?.note_kind ?? 'note',
    snapshot.lifecycle_stage ?? NOTE_LIFECYCLE_STAGE_DEFAULT
  ),
  id: snapshot.id,
  title: snapshot.title,
  content: snapshot.content,
  weight: snapshot.weight,
  start_at: snapshot.start_at,
  note_categories: cloneEditableNoteSnapshot(snapshot).note_categories,
  primary_category: snapshot.primary_category,
  note_form: snapshot.note_form,
  lifecycle_stage: snapshot.lifecycle_stage,
  color: snapshot.color,
  private_level: snapshot.private_level,
  custom_fields: cloneEditableNoteSnapshot(snapshot).custom_fields
});

export const buildNoteDraftStorageKey = (noteId?: string | number | null, noteTitle?: string | null) => {
  const normalizedNoteId = String(noteId ?? '').trim();
  if (normalizedNoteId) return `codeyun.note-draft.${normalizedNoteId}`;
  if (noteTitle && noteTitle.trim()) return `codeyun.note-draft.title.${noteTitle.trim()}`;
  return null;
};

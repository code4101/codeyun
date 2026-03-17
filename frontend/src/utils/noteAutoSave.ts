import type { NoteNode } from '@/api/notes';

export type NoteCustomFieldType = 'string' | 'number' | 'boolean';
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
  node_type: string | null;
  node_status: string | null;
  color: string | null;
  private_level: number;
  custom_fields: NoteCustomFieldTuple[];
}

export interface EditableNotePatch {
  title?: string;
  content?: string;
  weight?: number;
  start_at?: number;
  node_type?: string | null;
  node_status?: string | null;
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
  if (type === 'number' || type === 'boolean') return type;
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
  if (type === 'string') {
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

  return {
    id: String(note.id),
    title: normalizeText(note.title),
    content: normalizeText(note.content),
    weight: typeof note.weight === 'number' && Number.isFinite(note.weight) ? note.weight : 0,
    start_at: normalizeTimestamp(note.start_at),
    node_type: note.node_type ?? null,
    node_status: note.node_status ?? null,
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
  && left.node_type === right.node_type
  && left.node_status === right.node_status
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
      node_type: snapshot.node_type,
      node_status: snapshot.node_status,
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
  if (snapshot.node_type !== baseline.node_type) patch.node_type = snapshot.node_type;
  if (snapshot.node_status !== baseline.node_status) patch.node_status = snapshot.node_status;
  if (snapshot.color !== baseline.color) patch.color = snapshot.color;
  if (snapshot.private_level !== baseline.private_level) patch.private_level = snapshot.private_level;
  if (JSON.stringify(snapshot.custom_fields) !== JSON.stringify(baseline.custom_fields)) {
    patch.custom_fields = snapshot.custom_fields;
  }

  return patch;
};

export const applyEditableNoteSnapshot = (note: NoteNode, snapshot: EditableNoteSnapshot) => ({
  ...note,
  id: snapshot.id,
  title: snapshot.title,
  content: snapshot.content,
  weight: snapshot.weight,
  start_at: snapshot.start_at,
  node_type: snapshot.node_type,
  node_status: snapshot.node_status,
  color: snapshot.color,
  private_level: snapshot.private_level,
  custom_fields: cloneEditableNoteSnapshot(snapshot).custom_fields
});

export const noteSnapshotToNode = (
  source: Partial<NoteNode> | null | undefined,
  snapshot: EditableNoteSnapshot
): NoteNode => ({
  ...(source as NoteNode),
  id: snapshot.id,
  title: snapshot.title,
  content: snapshot.content,
  weight: snapshot.weight,
  start_at: snapshot.start_at,
  node_type: snapshot.node_type,
  node_status: snapshot.node_status,
  color: snapshot.color,
  private_level: snapshot.private_level,
  custom_fields: cloneEditableNoteSnapshot(snapshot).custom_fields
});

export const buildNoteDraftStorageKey = (noteId?: string | null, noteTitle?: string | null) => {
  if (noteId && noteId.trim()) return `codeyun.note-draft.${noteId}`;
  if (noteTitle && noteTitle.trim()) return `codeyun.note-draft.title.${noteTitle.trim()}`;
  return null;
};

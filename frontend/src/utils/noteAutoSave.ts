import type { NoteNode } from '@/api/notes';

export type NoteCustomFieldType = 'string' | 'number' | 'boolean';

export interface NoteCustomFieldItem {
  key: string;
  type: NoteCustomFieldType;
  value: string | boolean;
}

export interface EditableNoteSnapshot {
  id: string;
  title: string;
  content: string;
  weight: number;
  start_at: number;
  node_type: string | null;
  node_status: string | null;
  private_level: number;
  custom_fields: Array<[string, NoteCustomFieldType, string | boolean]>;
}

export interface EditableNotePatch {
  title?: string;
  content?: string;
  weight?: number;
  start_at?: number;
  node_type?: string | null;
  node_status?: string | null;
  private_level?: number;
  custom_fields?: Array<[string, NoteCustomFieldType, string | boolean]>;
}

const normalizeText = (value: unknown) => value == null ? '' : String(value);
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

export const normalizeNoteCustomFieldValue = (
  type: NoteCustomFieldType,
  value: unknown
): string | boolean => {
  if (type === 'boolean') return value === true || value === 'true';
  return normalizeText(value);
};

export const noteCustomFieldsToItems = (fields: unknown): NoteCustomFieldItem[] => {
  if (!fields) return [];

  if (Array.isArray(fields)) {
    const items: NoteCustomFieldItem[] = [];
    for (const field of fields) {
      if (Array.isArray(field) && field.length >= 3) {
        const [key, type, value] = field;
        if (typeof key !== 'string' || !key.trim()) continue;
        const normalizedType = normalizeNoteCustomFieldType(type);
        items.push({
          key,
          type: normalizedType,
          value: normalizeNoteCustomFieldValue(normalizedType, value)
        });
        continue;
      }

      if (field && typeof field === 'object') {
        const key = (field as any).key;
        if (typeof key !== 'string' || !key.trim()) continue;
        const normalizedType = normalizeNoteCustomFieldType((field as any).type);
        items.push({
          key,
          type: normalizedType,
          value: normalizeNoteCustomFieldValue(normalizedType, (field as any).value)
        });
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
      return {
        key,
        type: inferredType,
        value: normalizeNoteCustomFieldValue(inferredType, value)
      };
    });
  }

  return [];
};

export const noteCustomFieldItemsToList = (items: NoteCustomFieldItem[]) => (
  items
    .map(item => {
      const key = item.key?.trim();
      if (!key) return null;
      return [key, normalizeNoteCustomFieldType(item.type), normalizeNoteCustomFieldValue(normalizeNoteCustomFieldType(item.type), item.value)] as [string, NoteCustomFieldType, string | boolean];
    })
    .filter((item): item is [string, NoteCustomFieldType, string | boolean] => Boolean(item))
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
  private_level: snapshot.private_level,
  custom_fields: cloneEditableNoteSnapshot(snapshot).custom_fields
});

export const buildNoteDraftStorageKey = (noteId?: string | null, noteTitle?: string | null) => {
  if (noteId && noteId.trim()) return `codeyun.note-draft.${noteId}`;
  if (noteTitle && noteTitle.trim()) return `codeyun.note-draft.title.${noteTitle.trim()}`;
  return null;
};

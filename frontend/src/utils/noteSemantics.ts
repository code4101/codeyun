import type { NoteTypeAssignment } from './nodeConfig';

export const NOTE_KIND_DEFAULT = 'note';
export const NOTE_KIND_FANXIU_CHAR = 'fanxiu_char';
export const NOTE_KIND_FANXIU_WARDROBE_ITEM = 'fanxiu_wardrobe_item';
export const NOTE_KIND_FANXIU_ACTIVITY_ITEM = 'fanxiu_activity_item';
export const NOTE_KIND_FANXIU_SPIRIT_BEAST_ITEM = 'fanxiu_spirit_beast_item';

export const NOTE_WEIGHT_MODE_EXPONENTIAL = 'exponential';
export const NOTE_WEIGHT_MODE_LINEAR = 'linear';

export const NOTE_CATEGORY_DEFAULT = 'general';
export const NOTE_FORM_DEFAULT = 'note';
export const NOTE_FORM_DOCUMENT = 'document';
export const NOTE_FORM_MEMO = 'memo';
export const NOTE_FORM_MUSIC = 'music';
export const NOTE_FORM_VIDEO = 'video';
export const NOTE_FORM_GAME = 'game';
export const NOTE_FORM_BOOK = 'book';
export const NOTE_LIFECYCLE_STAGE_DEFAULT = 'idea';
export const NOTE_SCENE_DEFAULT = NOTE_KIND_DEFAULT;

const NOTE_TYPE_WEIGHT_DEFAULT = 100;
const NOTE_TYPE_WEIGHT_MIN = 0;
const NOTE_TYPE_WEIGHT_MAX = 100;
const LEGACY_FORM_TYPE_TO_NOTE_FORM: Record<string, string> = {
  note: NOTE_FORM_DEFAULT,
  doc: NOTE_FORM_DOCUMENT,
  memo: NOTE_FORM_MEMO
};

const normalizeNoteTypeWeight = (value: unknown, fallback: number = NOTE_TYPE_WEIGHT_DEFAULT) => {
  const numeric = typeof value === 'number' ? value : Number(value);
  const normalized = Number.isFinite(numeric) ? Math.round(numeric) : fallback;
  return Math.min(NOTE_TYPE_WEIGHT_MAX, Math.max(NOTE_TYPE_WEIGHT_MIN, normalized));
};

const normalizeAssignments = (
  value: unknown,
  fallbackKey: string,
  mapKey?: (key: string) => string
): NoteTypeAssignment[] => {
  const list = Array.isArray(value) ? value : [];
  const normalized: NoteTypeAssignment[] = [];
  const indexByKey = new Map<string, number>();

  for (const item of list) {
    let key = '';
    let weight = NOTE_TYPE_WEIGHT_DEFAULT;

    if (item && typeof item === 'object' && !Array.isArray(item)) {
      key = typeof (item as any).key === 'string' ? (item as any).key.trim() : '';
      weight = normalizeNoteTypeWeight((item as any).weight);
    } else if (Array.isArray(item) && item.length >= 2) {
      key = typeof item[0] === 'string' ? item[0].trim() : '';
      weight = normalizeNoteTypeWeight(item[1]);
    }

    if (!key) continue;
    const resolvedKey = mapKey ? mapKey(key) : key;
    if (!resolvedKey) continue;

    const existingIndex = indexByKey.get(resolvedKey);
    if (existingIndex !== undefined) {
      normalized[existingIndex] = {
        key: resolvedKey,
        weight: normalizeNoteTypeWeight(normalized[existingIndex].weight + weight)
      };
      continue;
    }

    indexByKey.set(resolvedKey, normalized.length);
    normalized.push({ key: resolvedKey, weight });
  }

  if (normalized.length > 0) return normalized;
  return [{ key: fallbackKey, weight: NOTE_TYPE_WEIGHT_DEFAULT }];
};

export const normalizeNoteForm = (value: unknown, fallback: string = NOTE_FORM_DEFAULT) => {
  const normalized = String(value || '').trim().toLowerCase();
  if (
    normalized === NOTE_FORM_DOCUMENT
    || normalized === NOTE_FORM_MEMO
    || normalized === NOTE_FORM_MUSIC
    || normalized === NOTE_FORM_VIDEO
    || normalized === NOTE_FORM_GAME
    || normalized === NOTE_FORM_BOOK
    || normalized === NOTE_FORM_DEFAULT
  ) {
    return normalized;
  }
  return fallback;
};

export const normalizeLifecycleStage = (value: unknown, fallback: string = NOTE_LIFECYCLE_STAGE_DEFAULT) => {
  let normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'predone') normalized = 'done';
  return normalized || fallback;
};

export const normalizeNoteScene = (value: unknown, fallback: string = NOTE_SCENE_DEFAULT) => {
  const normalized = String(value || '').trim();
  return normalized || fallback;
};

export const normalizeNoteCategories = (
  value: unknown,
  fallbackCategory: string = NOTE_CATEGORY_DEFAULT
) => normalizeAssignments(
  value,
  fallbackCategory,
  key => (key === 'note' || key === 'doc' || key === 'memo') ? NOTE_CATEGORY_DEFAULT : key
);

export const derivePrimaryCategory = (
  noteCategories: unknown,
  fallbackCategory: string = NOTE_CATEGORY_DEFAULT
) => {
  const normalized = normalizeNoteCategories(noteCategories, fallbackCategory);
  if (!normalized.length) return fallbackCategory;

  let best = normalized[0];
  for (const item of normalized.slice(1)) {
    if (item.weight > best.weight) best = item;
  }
  return best.key || fallbackCategory;
};

const getLegacyGeneralTypeKey = (noteForm: string) => {
  if (noteForm === NOTE_FORM_DOCUMENT) return 'doc';
  if (noteForm === NOTE_FORM_MEMO) return 'memo';
  return 'note';
};

export const deriveNoteTaxonomyFromLegacy = (
  noteTypes: unknown,
  nodeType: string | null | undefined = 'note',
  noteKind: string | null | undefined = NOTE_KIND_DEFAULT,
  nodeStatus: string | null | undefined = NOTE_LIFECYCLE_STAGE_DEFAULT
) => {
  const fallbackType = typeof nodeType === 'string' && nodeType.trim() ? nodeType.trim() : 'note';
  const normalizedLegacyTypes = normalizeAssignments(noteTypes, fallbackType);
  const categoriesSeed: NoteTypeAssignment[] = [];
  let noteForm = NOTE_FORM_DEFAULT;
  let bestFormWeight = -1;

  for (const item of normalizedLegacyTypes) {
    const mappedForm = LEGACY_FORM_TYPE_TO_NOTE_FORM[item.key];
    if (mappedForm) {
      categoriesSeed.push({ key: NOTE_CATEGORY_DEFAULT, weight: item.weight });
      if (item.weight > bestFormWeight) {
        noteForm = mappedForm;
        bestFormWeight = item.weight;
      }
      continue;
    }
    categoriesSeed.push(item);
  }

  const noteCategories = normalizeNoteCategories(categoriesSeed, NOTE_CATEGORY_DEFAULT);
  return {
    note_categories: noteCategories,
    primary_category: derivePrimaryCategory(noteCategories, NOTE_CATEGORY_DEFAULT),
    note_form: normalizeNoteForm(noteForm),
    note_scene: normalizeNoteScene(noteKind, NOTE_SCENE_DEFAULT),
    lifecycle_stage: normalizeLifecycleStage(nodeStatus, NOTE_LIFECYCLE_STAGE_DEFAULT)
  };
};

export const deriveLegacySemanticsFromTaxonomy = (
  noteCategories: unknown,
  primaryCategory: string | null | undefined = NOTE_CATEGORY_DEFAULT,
  noteForm: string | null | undefined = NOTE_FORM_DEFAULT,
  noteScene: string | null | undefined = NOTE_SCENE_DEFAULT,
  lifecycleStage: string | null | undefined = NOTE_LIFECYCLE_STAGE_DEFAULT
) => {
  const normalizedPrimaryCategory = typeof primaryCategory === 'string' && primaryCategory.trim()
    ? primaryCategory.trim()
    : NOTE_CATEGORY_DEFAULT;
  const normalizedNoteForm = normalizeNoteForm(noteForm, NOTE_FORM_DEFAULT);
  const normalizedNoteScene = normalizeNoteScene(noteScene, NOTE_SCENE_DEFAULT);
  const normalizedLifecycleStage = normalizeLifecycleStage(lifecycleStage, NOTE_LIFECYCLE_STAGE_DEFAULT);
  const normalizedCategories = normalizeNoteCategories(noteCategories, normalizedPrimaryCategory);
  const generalLegacyType = getLegacyGeneralTypeKey(normalizedNoteForm);
  const legacyNoteTypes = normalizedCategories.map(item => ({
    key: item.key === NOTE_CATEGORY_DEFAULT ? generalLegacyType : item.key,
    weight: item.weight
  }));

  let primaryLegacyType = generalLegacyType;
  if (legacyNoteTypes.length > 0) {
    primaryLegacyType = legacyNoteTypes[0].key || generalLegacyType;
    for (const item of legacyNoteTypes.slice(1)) {
      if (item.weight > (legacyNoteTypes.find(candidate => candidate.key === primaryLegacyType)?.weight ?? -1)) {
        primaryLegacyType = item.key || generalLegacyType;
      }
    }
  }

  return {
    note_categories: normalizedCategories,
    primary_category: derivePrimaryCategory(normalizedCategories, normalizedPrimaryCategory),
    note_form: normalizedNoteForm,
    note_scene: normalizedNoteScene,
    lifecycle_stage: normalizedLifecycleStage,
    note_types: legacyNoteTypes,
    node_type: primaryLegacyType,
    note_kind: normalizedNoteScene,
    node_status: normalizedLifecycleStage
  };
};

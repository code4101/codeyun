import { fetchNoteCategoryPalette, updateNoteCategoryPalette } from '@/api/noteTypes';
import { fromHex, getReadableTextColor, mixWeightedColors, toHex } from '@/utils/colorMath';
import {
  getNoteTypePaletteLoadPromiseState,
  noteTypePaletteItemsState,
  noteTypePaletteLoadedState,
  setNoteTypePaletteLoadPromiseState,
} from '@/utils/noteTypePaletteState';

export { resetNoteTypePaletteState } from '@/utils/noteTypePaletteState';

export interface NodeTypeItem {
  id: string;
  label: string;
  description: string;
  baseColor: string;
  lightColor: string;
  order?: number;
  builtin?: boolean;
  source?: 'builtin' | 'custom' | 'legacy' | 'import';
  generatedFromColor?: string | null;
}

export interface NodeStatusItem {
  id: string;
  label: string;
  description: string;
}

export interface NoteFormItem {
  id: string;
  label: string;
  description: string;
}

export interface NoteTypeAssignment {
  key: string;
  weight: number;
}

export interface NoteTypePaletteItem {
  key: string;
  label: string;
  color: string;
  description?: string;
  order: number;
  builtin: boolean;
  source: 'builtin' | 'custom' | 'legacy' | 'import';
  generatedFromColor?: string | null;
  usageCount?: number;
}

export const NODE_TYPES: Record<string, NodeTypeItem> = {
  general: { id: 'general', label: '综合', description: '默认综合分类', baseColor: '#606266', lightColor: '#F4F4F5', order: 0, builtin: true, source: 'builtin' },
  project: { id: 'project', label: '项目', description: '长期性工作，非具体任务容器', baseColor: '#7B1FA2', lightColor: '#F3E5F5', order: 10, builtin: true, source: 'builtin' },
  module: { id: 'module', label: '模块', description: '项目的组成部分', baseColor: '#BA68C8', lightColor: '#FAF4FB', order: 20, builtin: true, source: 'builtin' },
  task: { id: 'task', label: '任务', description: '具体的执行事项', baseColor: '#409EFF', lightColor: '#ECF5FF', order: 30, builtin: true, source: 'builtin' },
  bug: { id: 'bug', label: '缺陷', description: '需要修复的问题', baseColor: '#F56C6C', lightColor: '#FEF0F0', order: 40, builtin: true, source: 'builtin' }
};

export const NODE_STATUSES: Record<string, NodeStatusItem> = {
  idea: { id: 'idea', label: '笔记', description: '普通记录' },
  todo: { id: 'todo', label: '想法', description: '灵感草稿' },
  doing: { id: 'doing', label: '待办', description: '准备执行' },
  done: { id: 'done', label: '完成', description: '已完成，可按进度展示' },
  delete: { id: 'delete', label: '废弃', description: '已取消' }
};

export const NOTE_FORMS: Record<string, NoteFormItem> = {
  note: { id: 'note', label: '笔记', description: '普通笔记形态' },
  document: { id: 'document', label: '文档', description: '偏正文排版的文档形态' },
  memo: { id: 'memo', label: '备忘', description: '更短平快的便签形态' },
  music: { id: 'music', label: '音乐', description: '音乐作品、专辑或音频素材' },
  video: { id: 'video', label: '影视', description: '电影、剧集、视频或影像资料' },
  game: { id: 'game', label: '游戏', description: '游戏作品、攻略记录或游玩资料' },
  book: { id: 'book', label: '书籍', description: '书籍、电子书或长篇阅读材料' }
};

export const NODE_TYPE_ORDER = ['general', 'project', 'module', 'task', 'bug'];
export const NODE_STATUS_ORDER = ['idea', 'todo', 'doing', 'done', 'delete'];
export const NOTE_FORM_ORDER = ['note', 'document', 'memo', 'music', 'video', 'game', 'book'];

export const NOTE_TYPE_WEIGHT_DEFAULT = 100;
export const NOTE_TYPE_WEIGHT_MIN = 0;
export const NOTE_TYPE_WEIGHT_MAX = 100;

const HEX_COLOR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
const LEGACY_COLOR_TYPE_PREFIX = 'legacy_color_';
const CUSTOM_NOTE_TYPE_PREFIX = 'custom_';

const normalizeNodeStatusId = (value: string | null | undefined) => {
  const normalized = String(value || '').trim().toLowerCase();
  if (!normalized) return 'idea';
  if (normalized === 'predone') return 'done';
  return normalized;
};

const paletteItems = noteTypePaletteItemsState as typeof noteTypePaletteItemsState & {
  value: Record<string, NoteTypePaletteItem>;
};
const paletteLoaded = noteTypePaletteLoadedState;

export const normalizeNodeColor = (value: string | null | undefined) => {
  if (!value) return null;
  const trimmed = value.trim();
  if (!HEX_COLOR_RE.test(trimmed)) return null;
  if (trimmed.length === 4) {
    const [hash, r, g, b] = trimmed;
    return `${hash}${r}${r}${g}${g}${b}${b}`.toUpperCase();
  }
  return trimmed.toUpperCase();
};

const mixWithWhite = (hex: string, ratio: number) => {
  const normalized = normalizeNodeColor(hex);
  if (!normalized) return '#FFFFFF';
  const amount = Math.min(1, Math.max(0, ratio));
  const r = Number.parseInt(normalized.slice(1, 3), 16);
  const g = Number.parseInt(normalized.slice(3, 5), 16);
  const b = Number.parseInt(normalized.slice(5, 7), 16);
  const mix = (channel: number) => Math.round(channel * (1 - amount) + 255 * amount);
  return `#${[mix(r), mix(g), mix(b)].map(channel => channel.toString(16).padStart(2, '0')).join('')}`.toUpperCase();
};

export const buildLegacyColorTypeKey = (color: string | null | undefined) => {
  const normalized = normalizeNodeColor(color);
  if (!normalized) return null;
  return `${LEGACY_COLOR_TYPE_PREFIX}${normalized.slice(1).toLowerCase()}`;
};

export const isLegacyColorTypeKey = (key: string | null | undefined) => typeof key === 'string' && key.startsWith(LEGACY_COLOR_TYPE_PREFIX);

export const getLegacyColorFromTypeKey = (key: string | null | undefined) => {
  if (!isLegacyColorTypeKey(key)) return null;
  const suffix = String(key).slice(LEGACY_COLOR_TYPE_PREFIX.length).trim();
  if (suffix.length !== 6 || /[^0-9a-f]/i.test(suffix)) return null;
  return `#${suffix.toUpperCase()}`;
};

const getBuiltinType = (key: string | null | undefined) => {
  let normalizedKey = typeof key === 'string' && key.trim() ? key.trim() : 'general';
  if (normalizedKey === 'note' || normalizedKey === 'doc' || normalizedKey === 'memo') normalizedKey = 'general';
  return NODE_TYPES[normalizedKey] ?? null;
};

const toPaletteItem = (
  value: Partial<NoteTypePaletteItem> & { key: string },
  fallbackOrder: number = 0
): NoteTypePaletteItem | null => {
  let key = String(value.key || '').trim();
  if (!key) return null;
  if (key === 'note') key = 'general';
  if (key === 'doc' || key === 'memo') return null;
  const builtin = Boolean(value.builtin) || Boolean(getBuiltinType(key));
  const color = normalizeNodeColor(value.color) ?? getLegacyColorFromTypeKey(key) ?? getBuiltinType(key)?.baseColor ?? NODE_TYPES.general.baseColor;
  const label = String(value.label || '').trim() || (isLegacyColorTypeKey(key) ? `旧色${(getLegacyColorFromTypeKey(key) || '#606266').slice(1)}` : getBuiltinType(key)?.label || key);
  const source = builtin ? 'builtin' : isLegacyColorTypeKey(key) ? 'legacy' : (value.source ?? 'custom');
  const generatedFromColor = normalizeNodeColor(value.generatedFromColor) ?? (source === 'legacy' ? getLegacyColorFromTypeKey(key) : null);
  const numericOrder = Number.isFinite(Number(value.order)) ? Math.trunc(Number(value.order)) : fallbackOrder;
  const usageCount = Number.isFinite(Number((value as any).usageCount)) ? Math.max(0, Number((value as any).usageCount)) : 0;
  return {
    key,
    label,
    color,
    order: numericOrder,
    builtin,
    source,
    generatedFromColor,
    usageCount
  };
};

export const normalizeNoteTypePaletteItems = (value: unknown) => {
  const list = Array.isArray(value) ? value : [];
  const normalized: NoteTypePaletteItem[] = [];
  const seen = new Set<string>();
  list.forEach((item, index) => {
    if (!item || typeof item !== 'object') return;
    const normalizedItem = toPaletteItem(item as Partial<NoteTypePaletteItem> & { key: string }, index * 10);
    if (!normalizedItem || seen.has(normalizedItem.key)) return;
    seen.add(normalizedItem.key);
    normalized.push(normalizedItem);
  });
  return normalized;
};

const applyPaletteItems = (items: NoteTypePaletteItem[]) => {
  paletteItems.value = items.reduce<Record<string, NoteTypePaletteItem>>((result, item) => {
    result[item.key] = item;
    return result;
  }, {});
  paletteLoaded.value = true;
};

export const ensureNoteTypePaletteLoaded = async (force: boolean = false) => {
  const paletteLoadPromise = getNoteTypePaletteLoadPromiseState() as Promise<NoteTypePaletteItem[]> | null;
  if (!force && paletteLoaded.value) return Object.values(paletteItems.value);
  if (!force && paletteLoadPromise) return paletteLoadPromise;
  const nextLoadPromise = fetchNoteCategoryPalette()
    .then(response => {
      const normalized = normalizeNoteTypePaletteItems(response.items.map(item => ({
        key: item.key,
        label: item.label,
        color: item.color,
        description: item.description || '',
        order: item.order,
        builtin: item.builtin,
        source: item.source,
        generatedFromColor: item.generated_from_color ?? null,
        usageCount: item.usage_count ?? 0
      })));
      applyPaletteItems(normalized);
      return normalized;
    })
    .finally(() => {
      setNoteTypePaletteLoadPromiseState(null);
    });
  setNoteTypePaletteLoadPromiseState(nextLoadPromise as Promise<unknown[]>);
  return nextLoadPromise;
};

export const saveNoteTypePalette = async (items: NoteTypePaletteItem[]) => {
  const payload = normalizeNoteTypePaletteItems(items);
  const response = await updateNoteCategoryPalette(payload.map(item => ({
    key: item.key,
    label: item.label,
    color: item.color,
    description: item.description || null,
    order: item.order,
    builtin: item.builtin,
    source: item.source,
    generated_from_color: item.generatedFromColor ?? null,
    usage_count: item.usageCount ?? 0
  })));
  const normalized = normalizeNoteTypePaletteItems(response.items.map(item => ({
    key: item.key,
    label: item.label,
    color: item.color,
    description: item.description || '',
    order: item.order,
    builtin: item.builtin,
    source: item.source,
    generatedFromColor: item.generated_from_color ?? null,
    usageCount: item.usage_count ?? 0
  })));
  applyPaletteItems(normalized);
  return normalized;
};

export const normalizeNoteTypeWeight = (value: unknown, fallback: number = NOTE_TYPE_WEIGHT_DEFAULT) => {
  const numeric = typeof value === 'number' ? value : Number(value);
  const normalized = Number.isFinite(numeric) ? Math.round(numeric) : fallback;
  return Math.min(NOTE_TYPE_WEIGHT_MAX, Math.max(NOTE_TYPE_WEIGHT_MIN, normalized));
};

export const normalizeNoteTypeAssignments = (
  value: unknown,
  fallbackType: string | null | undefined = 'general',
  options: { allowEmpty?: boolean } = {}
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
    if (key === 'note' || key === 'doc' || key === 'memo') key = 'general';

    const existingIndex = indexByKey.get(key);
    if (existingIndex !== undefined) {
      normalized[existingIndex] = { key, weight };
      continue;
    }

    indexByKey.set(key, normalized.length);
    normalized.push({ key, weight });
  }

  if (normalized.length > 0) return normalized;

  if (options.allowEmpty) return [];

  const fallback = typeof fallbackType === 'string' && fallbackType.trim() ? fallbackType.trim() : 'general';
  return [{ key: fallback, weight: NOTE_TYPE_WEIGHT_DEFAULT }];
};

export const createEffectiveNoteTypes = (
  noteTypes: unknown,
  fallbackType: string | null | undefined = 'general',
  legacyColor?: string | null
) => {
  const normalized = normalizeNoteTypeAssignments(noteTypes, fallbackType);
  const normalizedColor = normalizeNodeColor(legacyColor);
  const fallback = typeof fallbackType === 'string' && fallbackType.trim() ? fallbackType.trim() : 'general';
  if (!normalizedColor) return normalized;
  if (Array.isArray(noteTypes) && noteTypes.length > 0) {
    if (!(normalized.length === 1 && normalized[0].key === fallback && normalized[0].weight === NOTE_TYPE_WEIGHT_DEFAULT)) {
      return normalized;
    }
  }
  const legacyKey = buildLegacyColorTypeKey(normalizedColor);
  return legacyKey ? [{ key: legacyKey, weight: NOTE_TYPE_WEIGHT_DEFAULT }] : normalized;
};

export const derivePrimaryNodeType = (
  noteTypes: unknown,
  fallbackType: string | null | undefined = 'general'
) => {
  const normalized = normalizeNoteTypeAssignments(noteTypes, fallbackType);
  if (!normalized.length) return typeof fallbackType === 'string' && fallbackType.trim() ? fallbackType.trim() : 'general';

  let best = normalized[0];
  for (const item of normalized.slice(1)) {
    if (item.weight > best.weight) best = item;
  }
  return best.key;
};

const createDefaultTypeItem = (key: string): NodeTypeItem => {
  const builtin = getBuiltinType(key);
  if (builtin) return builtin;

  const legacyColor = getLegacyColorFromTypeKey(key);
  if (legacyColor) {
    return {
      id: key,
      label: `旧色${legacyColor.slice(1)}`,
      description: '从旧颜色字段兼容生成的类型',
      baseColor: legacyColor,
      lightColor: mixWithWhite(legacyColor, 0.88),
      order: 2000,
      builtin: false,
      source: 'legacy',
      generatedFromColor: legacyColor
    };
  }

  return {
    id: key,
    label: key,
    description: '自定义分类',
    baseColor: NODE_TYPES.general.baseColor,
    lightColor: NODE_TYPES.general.lightColor,
    order: 1000,
    builtin: false,
    source: 'custom',
    generatedFromColor: null
  };
};

export const getDefaultNodeTypeConfig = (typeKey: string | null | undefined) => {
  const normalizedKey = typeof typeKey === 'string' && typeKey.trim() ? typeKey.trim() : 'general';
  return createDefaultTypeItem(normalizedKey);
};

const resolvePaletteType = (typeKey: string | null | undefined): NodeTypeItem => {
  const normalizedKey = typeof typeKey === 'string' && typeKey.trim() ? typeKey.trim() : 'general';
  const base = getDefaultNodeTypeConfig(normalizedKey);
  const override = paletteItems.value[normalizedKey];
  if (!override) return base;
  const baseColor = normalizeNodeColor(override.color) ?? base.baseColor;
    return {
      ...base,
      label: override.label || base.label,
      baseColor,
      lightColor: mixWithWhite(baseColor, 0.88),
    order: override.order,
    builtin: override.builtin,
    source: override.source,
    generatedFromColor: override.generatedFromColor ?? base.generatedFromColor
  };
};

const hexToRgb = (hex: string) => {
  const normalized = normalizeNodeColor(hex) ?? '#FFFFFF';
  return {
    r: Number.parseInt(normalized.slice(1, 3), 16),
    g: Number.parseInt(normalized.slice(3, 5), 16),
    b: Number.parseInt(normalized.slice(5, 7), 16)
  };
};

const rgbToHex = (rgb: { r: number; g: number; b: number }) => (
  `#${[rgb.r, rgb.g, rgb.b].map(channel => Math.round(channel).toString(16).padStart(2, '0')).join('')}`.toUpperCase()
);

const relativeLuminance = (hex: string) => {
  const { r, g, b } = hexToRgb(hex);
  const normalize = (channel: number) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return normalize(r) * 0.2126 + normalize(g) * 0.7152 + normalize(b) * 0.0722;
};

const contrastRatio = (foreground: string, background: string) => {
  const fg = relativeLuminance(foreground);
  const bg = relativeLuminance(background);
  const lighter = Math.max(fg, bg);
  const darker = Math.min(fg, bg);
  return (lighter + 0.05) / (darker + 0.05);
};

const ensureReadableForeground = (foreground: string, background: string) => {
  const normalizedForeground = normalizeNodeColor(foreground) ?? NODE_TYPES.general.baseColor;
  const normalizedBackground = normalizeNodeColor(background) ?? '#FFFFFF';
  if (contrastRatio(normalizedForeground, normalizedBackground) >= 3) return normalizedForeground;
  return getReadableTextColor(hexToRgb(normalizedBackground));
};

export const resolveNoteTypesColor = (
  noteTypes: unknown,
  fallbackType: string | null | undefined = 'general'
) => {
  const normalized = normalizeNoteTypeAssignments(noteTypes, fallbackType);
  if (!normalized.length) return null;

  const mixedColor = mixWeightedColors(
    normalized.map(item => ({
      color: resolvePaletteType(item.key).baseColor,
      weight: item.weight
    })),
    { fillColor: '#FFFFFF', fillToWeight: 100 }
  );

  return mixedColor ? toHex(mixedColor) : null;
};

export const getNodeTheme = (
  typeStr: string | null | undefined,
  customColor?: string | null,
  noteTypes?: unknown
) => {
  const hasExplicitNoteTypes = Array.isArray(noteTypes) && noteTypes.length > 0;
  const primaryType = hasExplicitNoteTypes ? derivePrimaryNodeType(noteTypes, typeStr || 'general') : (typeStr || 'general');
  const type = resolvePaletteType(primaryType);
  const resolvedTypeColor = hasExplicitNoteTypes ? resolveNoteTypesColor(noteTypes, primaryType) : null;
  const normalizedCustomColor = normalizeNodeColor(customColor);
  const baseColor = resolvedTypeColor ?? normalizedCustomColor;
  if (!baseColor) return type;

  return {
    ...type,
    baseColor,
    lightColor: mixWithWhite(baseColor, 0.88)
  };
};

const createBaseNodeVisualStyle = (type: NodeTypeItem, status: NodeStatusItem) => {
  const style = {
    borderColor: '#000000',
    backgroundColor: '#ffffff',
    color: type.baseColor,
    borderWidth: '1px',
    borderStyle: 'solid',
    fontWeight: 'normal',
    textDecoration: 'none',
    opacity: '1',
  };

  switch (status.id) {
    case 'idea':
      style.borderStyle = 'solid';
      style.borderColor = '#ebeef5';
      break;
    case 'todo':
      style.borderStyle = 'dashed';
      style.borderColor = type.baseColor;
      break;
    case 'doing':
      style.borderStyle = 'solid';
      style.borderColor = type.baseColor;
      break;
    case 'done':
      style.borderStyle = 'solid';
      style.borderColor = type.baseColor;
      style.backgroundColor = type.lightColor;
      break;
    case 'delete':
      style.borderStyle = 'solid';
      style.borderColor = '#ebeef5';
      style.textDecoration = 'line-through';
      style.opacity = '0.6';
      break;
  }

  style.color = ensureReadableForeground(type.baseColor, style.backgroundColor);

  return style;
};

export const getNodeStyle = (
  typeStr: string | null | undefined,
  statusStr: string | null | undefined,
  customColor?: string | null,
  noteTypes?: unknown
) => {
  const type = getNodeTheme(typeStr, customColor, noteTypes);
  const status = NODE_STATUSES[normalizeNodeStatusId(statusStr)] || NODE_STATUSES.idea;
  return createBaseNodeVisualStyle(type, status);
};

export const getNodeDisplayStyleFromTheme = (
  type: NodeTypeItem,
  statusStr: string | null | undefined,
  completionProgress?: number | null
) => {
  const status = NODE_STATUSES[normalizeNodeStatusId(statusStr)] || NODE_STATUSES.idea;
  const foregroundColor = getReadableTextColor(fromHex(type.baseColor));
  const clampedProgress = typeof completionProgress === 'number' && Number.isFinite(completionProgress)
    ? Math.min(1, Math.max(0, completionProgress))
    : null;

  const style = {
    ...createBaseNodeVisualStyle(type, status),
    backgroundImage: 'none',
    fillTextColor: foregroundColor,
    emptyTextColor: foregroundColor,
    partialFillRatio: null as number | null,
  };

  switch (status.id) {
    case 'done':
      if (clampedProgress !== null && clampedProgress < 1) {
        const pct = `${(clampedProgress * 100).toFixed(2)}%`;
        style.backgroundColor = '#FFFFFF';
        style.backgroundImage = `linear-gradient(to right, ${type.baseColor} 0%, ${type.baseColor} ${pct}, #FFFFFF ${pct}, #FFFFFF 100%)`;
        style.color = '#111827';
        style.fillTextColor = foregroundColor;
        style.emptyTextColor = '#111827';
        style.partialFillRatio = clampedProgress;
      } else {
        style.backgroundColor = type.baseColor;
        style.backgroundImage = 'none';
        style.color = foregroundColor;
      }
      break;
  }

  return style;
};

export const getNodeDisplayStyle = (
  typeStr: string | null | undefined,
  statusStr: string | null | undefined,
  customColor?: string | null,
  noteTypes?: unknown,
  completionProgress?: number | null
) => {
  const type = getNodeTheme(typeStr, customColor, noteTypes);
  return getNodeDisplayStyleFromTheme(type, statusStr, completionProgress);
};

export const getOrderedNodeTypes = () => {
  const keys = paletteLoaded.value
    ? Object.keys(paletteItems.value)
    : NODE_TYPE_ORDER;
  const merged = new Map<string, NodeTypeItem>();
  keys.forEach(key => merged.set(key, resolvePaletteType(key)));
  return Array.from(merged.values()).sort((left, right) => {
    const leftOrder = Number.isFinite(left.order) ? Number(left.order) : 1000;
    const rightOrder = Number.isFinite(right.order) ? Number(right.order) : 1000;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return left.label.localeCompare(right.label, 'zh-Hans-CN');
  });
};

export const getEditableNoteTypePaletteItems = () => {
  if (paletteLoaded.value) {
    return Object.values(paletteItems.value)
      .map(item => ({ ...item }))
      .sort((left, right) => {
        if (left.order !== right.order) return left.order - right.order;
        return left.label.localeCompare(right.label, 'zh-Hans-CN');
      });
  }
  return getOrderedNodeTypes().map(type => ({
    key: type.id,
    label: type.label,
    color: type.baseColor,
    order: Number.isFinite(type.order) ? Number(type.order) : 1000,
    builtin: Boolean(type.builtin),
    source: type.source ?? (type.builtin ? 'builtin' : 'custom'),
    generatedFromColor: type.generatedFromColor ?? null,
    usageCount: 0
  }));
};

export const createCustomNoteType = (label: string = '新类型') => {
  const key = `${CUSTOM_NOTE_TYPE_PREFIX}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const customTypes = getEditableNoteTypePaletteItems().filter(item => !item.builtin);
  const highestOrder = customTypes.reduce((max, item) => Math.max(max, item.order), 900);
  return {
    key,
    label,
    color: '#409EFF',
    order: highestOrder + 10,
    builtin: false,
    source: 'custom' as const,
    generatedFromColor: null,
    usageCount: 0
  };
};

export const getOrderedNodeStatuses = () => NODE_STATUS_ORDER.map(k => NODE_STATUSES[k]);
export const getOrderedNoteForms = () => NOTE_FORM_ORDER.map(k => NOTE_FORMS[k]).filter(Boolean);
export const getNodeTypeConfig = (type: string) => resolvePaletteType(type);
export const getNodeStatusConfig = (status: string) => NODE_STATUSES[normalizeNodeStatusId(status)] || NODE_STATUSES.idea;
export const getNoteFormConfig = (noteForm: string | null | undefined) => NOTE_FORMS[noteForm || 'note'] || NOTE_FORMS.note;

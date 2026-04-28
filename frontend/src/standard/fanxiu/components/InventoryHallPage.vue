<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { ArrowDown, ArrowUp, Delete, Plus } from '@element-plus/icons-vue';
import type { NoteNode } from '@/api/notes';
import type {
  FanxiuInventoryItem,
  FanxiuInventorySectionSnapshot,
  FanxiuInventoryType,
} from '@/api/fanxiu';
import NoteSplitView from '@/components/NoteSplitView.vue';
import UniversalNoteEditor from '@/components/UniversalNoteEditor.vue';
import { useUserStore } from '@/store/userStore';
import { useAutoSave } from '@/utils/useAutoSave';
import { putJsonKeepalive } from '@/utils/keepaliveRequest';
import type { EditableNotePatch } from '@/utils/noteAutoSave';
import { useResizablePane } from '@/utils/useResizablePane';

interface SectionDefinition {
  key: string;
  title: string;
}

interface InventoryHallPageProps {
  title: string;
  resourceLabel: string;
  idPrefix: string;
  draftStorageKeyPrefix: string;
  splitPaneStorageKey: string;
  keepalivePathPrefix: string;
  sections: SectionDefinition[];
  sortMode?: 'date_desc' | 'quality_rank_desc';
  categoryOptions?: string[];
  categoryColumnLabel?: string;
  categoryColumnWidth?: number | string;
  categorySelectionLabelMap?: Record<string, string>;
  nameColumnMinWidth?: number | string;
  typeColumnWidth?: number | string;
  tableInternalScroll?: boolean;
  showViewFilters?: boolean;
  loadSnapshot: () => Promise<any>;
  saveSnapshot: (payload: FanxiuInventorySectionSnapshot) => Promise<any>;
  getNote: (itemId: string) => Promise<NoteNode | null>;
  saveNote: (itemId: string, data: Partial<NoteNode>) => Promise<NoteNode>;
  importImage?: (sectionKey: string, image: File) => Promise<Partial<FanxiuInventoryItem>>;
}

interface InventoryRowLocation {
  sectionKey: string;
  item: FanxiuInventoryItem;
}

type FocusableInputRef = {
  focus?: () => void;
  select?: () => void;
} | null;

const ITEM_TYPE_OPTIONS: FanxiuInventoryType[] = ['攻击', '防御', '灵力', '辅助'];
const QUALITY_LABELS = [
  '珍品',
  '绝品',
  '仙品一星',
  '仙品二星',
  '仙品三星',
  '仙品四星',
  '仙品五星',
  '仙品六星',
  '神品一星',
  '神品二星',
  '神品三星',
  '神品四星',
  '神品五星',
  '神品六星',
  '神品七星',
  '神品八星',
  '神品九星',
  '神品十星',
] as const;

const props = defineProps<InventoryHallPageProps>();
const userStore = useUserStore();
const loading = ref(false);
const editorLoading = ref(false);
const pageHydrated = ref(false);
const snapshot = ref<FanxiuInventorySectionSnapshot>(createEmptyState());
const noteCache = ref<Record<string, NoteNode | null | undefined>>({});
const currentEditingItemId = ref('');
const currentEditingNote = ref<NoteNode | undefined>(undefined);
const renamingItemId = ref('');
const qualityEditingItemId = ref('');
const qualityDrafts = ref<Record<string, string>>({});
const textFieldDrafts = ref<Record<string, string>>({});
const nameInputRefs = new Map<string, FocusableInputRef>();
const qualityInputRefs = new Map<string, FocusableInputRef>();
const textFieldTimers = new Map<string, ReturnType<typeof setTimeout>>();
const pendingImportSectionKey = ref('');
const importingSectionKey = ref('');

const canEdit = computed(() => {
  const username = userStore.user?.username;
  return username === '凡修手游' || userStore.isAdmin;
});

const currentEditingLocation = computed(() => findRowById(currentEditingItemId.value));
const currentEditingRow = computed(() => currentEditingLocation.value?.item ?? null);
const editorEmptyDescription = computed(() => {
  if (editorLoading.value) return '文档加载中...';
  if (!currentEditingItemId.value) return '点击上方条目开始编辑文档';
  if (!currentEditingRow.value?.name.trim()) return '请先填写条目名称，再编辑文档';
  return '当前条目暂无文档';
});
const editorVisible = computed(() => Boolean(currentEditingItemId.value));
const canImportImage = computed(() => canEdit.value && typeof props.importImage === 'function');
const showCategoryColumn = computed(() => Boolean(props.categoryOptions?.length));
const showViewFilters = computed(() => Boolean(props.showViewFilters));
const resolvedCategoryColumnWidth = computed(() => props.categoryColumnWidth ?? 96);
const resolvedTypeColumnWidth = computed(() => props.typeColumnWidth ?? 96);
const activeCategoryFilters = ref<string[]>([]);
const activeTypeFilters = ref<FanxiuInventoryType[]>([]);
const hasActiveViewFilters = computed(
  () => activeCategoryFilters.value.length > 0 || activeTypeFilters.value.length > 0,
);

function calculateTableMaxHeight(paneHeight: number) {
  if (!props.tableInternalScroll) {
    return undefined;
  }
  const sectionCount = Math.max(props.sections.length, 1);
  const titleAndPaddingHeight = 92;
  const cardChromeHeight = 92;
  const stackGapHeight = Math.max(sectionCount - 1, 0) * 14;
  const availableHeight =
    paneHeight - titleAndPaddingHeight - stackGapHeight - cardChromeHeight * sectionCount;
  return Math.max(140, Math.floor(availableHeight / sectionCount));
}

const committedTableMaxHeight = ref<number | undefined>(undefined);
const tableMaxHeight = computed(() => {
  if (!props.tableInternalScroll) {
    return undefined;
  }
  return committedTableMaxHeight.value;
});

const autoSave = useAutoSave<FanxiuInventorySectionSnapshot>({
  debounceMs: 800,
  equals: (left, right) => serializeSnapshot(left) === serializeSnapshot(right),
  storageKey: () => (canEdit.value ? buildDraftStorageKey() : null),
  save: async value => props.saveSnapshot(prepareSnapshot(value)),
  onError: (error: unknown) => {
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || `自动保存${props.resourceLabel}失败`);
  },
});

function calculateStructurePaneBounds() {
  const viewportHeight = window.innerHeight;
  const isNarrow = window.innerWidth < 960;
  const reservedHeight = isNarrow ? 170 : 130;
  const availableHeight = Math.max(460, viewportHeight - reservedHeight);
  const minEditorHeight = isNarrow ? 300 : 360;
  const maxHeight = Math.max(240, availableHeight - minEditorHeight);
  const adaptiveHeight = props.tableInternalScroll
    ? Math.min(
        maxHeight,
        isNarrow ? 460 : 540,
        Math.max(isNarrow ? 300 : 360, Math.floor(availableHeight * 0.38)),
      )
    : Math.min(
        maxHeight,
        Math.max(isNarrow ? 280 : 320, Math.floor(availableHeight * 0.7)),
      );

  return {
    adaptiveHeight,
    maxHeight,
  };
}

const {
  paneHeight: structurePaneHeight,
  isResizing: isStructurePaneResizing,
  startResizing,
} = useResizablePane({
  initialHeight: 560,
  getAdaptiveHeight: () => calculateStructurePaneBounds().adaptiveHeight,
  getResizeBounds: () => ({
    min: 220,
    max: calculateStructurePaneBounds().maxHeight,
  }),
  storageKey: props.splitPaneStorageKey,
});

watch(
  [structurePaneHeight, isStructurePaneResizing, () => props.tableInternalScroll, () => props.sections.length],
  () => {
    if (!props.tableInternalScroll) {
      committedTableMaxHeight.value = undefined;
      return;
    }
    if (isStructurePaneResizing.value) {
      return;
    }
    committedTableMaxHeight.value = calculateTableMaxHeight(structurePaneHeight.value);
  },
  { immediate: true },
);

function createEmptyState(): FanxiuInventorySectionSnapshot {
  return Object.fromEntries(props.sections.map(section => [section.key, []]));
}

function cloneNote(note: NoteNode): NoteNode {
  return JSON.parse(JSON.stringify(note));
}

function createEmptySnapshot(): FanxiuInventorySectionSnapshot {
  return createEmptyState();
}

function normalizeOptionalInt(value: unknown): number | null {
  if (value === null || value === undefined || String(value).trim() === '') {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.round(numeric) : null;
}

function normalizeItemType(value: unknown): FanxiuInventoryType {
  const normalized = String(value || '').trim() as FanxiuInventoryType;
  return ITEM_TYPE_OPTIONS.includes(normalized) ? normalized : '';
}

function normalizeQuality(value: unknown): number | null {
  const normalized = normalizeOptionalInt(value);
  if (normalized === null) {
    return null;
  }
  return Math.min(Math.max(normalized, 0), QUALITY_LABELS.length - 1);
}

function parseChineseOrdinalToken(token: string): number | null {
  const normalized = String(token || '').trim();
  if (!normalized) return null;
  if (/^\d+$/.test(normalized)) {
    const numeric = Number(normalized);
    return Number.isFinite(numeric) ? numeric : null;
  }
  const mapping: Record<string, number> = {
    一: 1,
    二: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9,
    十: 10,
  };
  return mapping[normalized] ?? null;
}

function parseQualityText(value: string): number | null | undefined {
  const normalized = String(value || '').trim().replace(/\s+/g, '');
  if (!normalized) {
    return null;
  }

  if (normalized === '珍' || normalized === '珍品') return 0;
  if (normalized === '绝' || normalized === '绝品') return 1;
  if (normalized === '仙' || normalized === '仙品') return 2;
  if (normalized === '神' || normalized === '神品') return 8;

  const exactIndex = QUALITY_LABELS.findIndex(label => label === normalized);
  if (exactIndex >= 0) {
    return exactIndex;
  }

  const xianMatch = normalized.match(/^仙(?:品)?((?:10)|[一二三四五六七八九十1-9])星?$/);
  if (xianMatch) {
    const star = parseChineseOrdinalToken(xianMatch[1]);
    if (star !== null && star >= 1 && star <= 6) {
      return star + 1;
    }
    if (star !== null && star >= 7 && star <= 10) {
      return star + 7;
    }
  }

  const shenMatch = normalized.match(/^神(?:品)?((?:10)|[一二三四五六七八九十1-9])星?$/);
  if (shenMatch) {
    const star = parseChineseOrdinalToken(shenMatch[1]);
    if (star !== null && star >= 1 && star <= 10) {
      return star + 7;
    }
  }

  return undefined;
}

function normalizeShenlian(value: unknown): number {
  const normalized = normalizeOptionalInt(value);
  if (normalized === null) {
    return 0;
  }
  return Math.max(normalized, 0);
}

function normalizeItem(item: Partial<FanxiuInventoryItem> | null | undefined): FanxiuInventoryItem {
  return {
    id: String(item?.id || buildRowId()),
    name: String(item?.name || ''),
    category: normalizeCategory(item?.category),
    rank: Number.isFinite(Number(item?.rank)) ? Number(item?.rank) : 0,
    shenlian: normalizeShenlian(item?.shenlian),
    type: normalizeItemType(item?.type),
    quality: normalizeQuality(item?.quality),
    main_use: String(item?.main_use || ''),
    acquisition: String(item?.acquisition || ''),
    date: normalizeDate(item?.date),
    note_id: item?.note_id ? String(item.note_id) : null,
  };
}

function normalizeCategory(value: unknown): string {
  const normalized = String(value || '').trim();
  if (!props.categoryOptions?.length) {
    return normalized;
  }
  if (normalized && props.categoryOptions.includes(normalized)) {
    return normalized;
  }
  return props.categoryOptions[0] || '';
}

function normalizeSnapshot(value: Partial<FanxiuInventorySectionSnapshot> | null | undefined): FanxiuInventorySectionSnapshot {
  const normalized = createEmptySnapshot();
  for (const section of props.sections) {
    const items = Array.isArray(value?.[section.key]) ? value?.[section.key] : [];
    normalized[section.key] = sortItems((items || []).map(normalizeItem));
  }
  return normalized;
}

function snapshotToState(value: Partial<FanxiuInventorySectionSnapshot> | null | undefined): FanxiuInventorySectionSnapshot {
  return normalizeSnapshot(value);
}

function serializeSnapshot(value: FanxiuInventorySectionSnapshot): string {
  return JSON.stringify(value);
}

function prepareSnapshot(value: Partial<FanxiuInventorySectionSnapshot>): FanxiuInventorySectionSnapshot {
  return normalizeSnapshot(value);
}

function buildRowId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${props.idPrefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function buildDraftStorageKey(): string {
  const scope = userStore.user?.id ?? userStore.user?.username ?? 'fanxiu';
  return `${props.draftStorageKeyPrefix}:${scope}`;
}

function buildTextFieldKey(rowId: string, field: 'main_use' | 'acquisition'): string {
  return `${rowId}:${field}`;
}

function clearTextFieldTimer(key: string) {
  const timer = textFieldTimers.get(key);
  if (!timer) return;
  clearTimeout(timer);
  textFieldTimers.delete(key);
}

function clearBufferedTextFieldState(rowId?: string) {
  const keys = Object.keys(textFieldDrafts.value);
  for (const key of keys) {
    if (!rowId || key.startsWith(`${rowId}:`)) {
      delete textFieldDrafts.value[key];
      clearTextFieldTimer(key);
    }
  }
}

function getBufferedTextFieldValue(row: FanxiuInventoryItem, field: 'main_use' | 'acquisition'): string {
  const key = buildTextFieldKey(row.id, field);
  return textFieldDrafts.value[key] ?? String(row[field] || '');
}

function commitBufferedTextField(row: FanxiuInventoryItem, field: 'main_use' | 'acquisition') {
  const key = buildTextFieldKey(row.id, field);
  clearTextFieldTimer(key);
  if (!(key in textFieldDrafts.value)) {
    return;
  }
  const nextValue = String(textFieldDrafts.value[key] || '');
  delete textFieldDrafts.value[key];
  if (row[field] !== nextValue) {
    row[field] = nextValue;
    markSnapshotDirty();
  }
}

function queueBufferedTextFieldUpdate(
  row: FanxiuInventoryItem,
  field: 'main_use' | 'acquisition',
  value: string,
) {
  const key = buildTextFieldKey(row.id, field);
  textFieldDrafts.value[key] = String(value || '');
  clearTextFieldTimer(key);
  textFieldTimers.set(
    key,
    setTimeout(() => {
      commitBufferedTextField(row, field);
    }, 320),
  );
}

function todayText(): string {
  const current = new Date();
  const year = current.getFullYear();
  const month = String(current.getMonth() + 1).padStart(2, '0');
  const day = String(current.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function normalizeDate(value: string | null | undefined): string {
  const text = String(value || '').trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : todayText();
}

function estimateTextUnits(text: string): number {
  let units = 0;
  for (const ch of String(text || '')) {
    units += /^[\u0000-\u00ff]$/.test(ch) ? 1 : 2;
  }
  return units;
}

function getNameInputWidth(text: string): string {
  const content = String(text || '').trim() || '输入名称';
  const units = Math.max(estimateTextUnits(content), 6);
  const widthPx = Math.min(640, Math.max(144, 32 + units * 13));
  return `${widthPx}px`;
}

function getCategorySelectionLabel(value: string) {
  const normalized = String(value || '').trim();
  if (!normalized) return '';
  return props.categorySelectionLabelMap?.[normalized] || normalized;
}

function matchesViewFilters(row: FanxiuInventoryItem): boolean {
  if (
    activeCategoryFilters.value.length > 0
    && !activeCategoryFilters.value.includes(normalizeCategory(row.category))
  ) {
    return false;
  }

  if (activeTypeFilters.value.length > 0 && !activeTypeFilters.value.includes(normalizeItemType(row.type))) {
    return false;
  }

  return true;
}

function getDisplayRows(sectionKey: string): FanxiuInventoryItem[] {
  const rows = snapshot.value[sectionKey] || [];
  if (!hasActiveViewFilters.value) {
    return rows;
  }
  return rows.filter(matchesViewFilters);
}

function compareByQualityRankDesc(left: FanxiuInventoryItem, right: FanxiuInventoryItem): number {
  const leftQuality = left.quality ?? -1;
  const rightQuality = right.quality ?? -1;
  const qualityCompare = rightQuality - leftQuality;
  if (qualityCompare !== 0) return qualityCompare;

  const rankCompare = right.rank - left.rank;
  if (rankCompare !== 0) return rankCompare;

  const shenlianCompare = normalizeShenlian(right.shenlian) - normalizeShenlian(left.shenlian);
  if (shenlianCompare !== 0) return shenlianCompare;

  const dateCompare = right.date.localeCompare(left.date);
  if (dateCompare !== 0) return dateCompare;

  return left.id.localeCompare(right.id);
}

function sortItems(items: FanxiuInventoryItem[]): FanxiuInventoryItem[] {
  return [...items].sort((left, right) => {
    if (props.sortMode === 'quality_rank_desc') {
      return compareByQualityRankDesc(left, right);
    }

    const dateCompare = right.date.localeCompare(left.date);
    if (dateCompare !== 0) return dateCompare;
    return left.id.localeCompare(right.id);
  });
}

function sortSection(sectionKey: string) {
  snapshot.value[sectionKey] = sortItems(snapshot.value[sectionKey] || []);
}

function shouldDeferInlineSort(): boolean {
  return props.sortMode === 'quality_rank_desc';
}

function sortSectionAfterInlineEdit(sectionKey: string) {
  if (!shouldDeferInlineSort()) {
    sortSection(sectionKey);
  }
}

function markSnapshotDirty(markOptions: { immediate?: boolean; delayMs?: number } = {}) {
  if (!pageHydrated.value) {
    return;
  }
  autoSave.markDirty(snapshot.value, markOptions);
}

function createNewRow(): FanxiuInventoryItem {
  return {
    id: buildRowId(),
    name: '',
    category: normalizeCategory(''),
    rank: 0,
    shenlian: 0,
    type: '',
    quality: null,
    main_use: '',
    acquisition: '',
    date: todayText(),
    note_id: null,
  };
}

function getSectionTitle(sectionKey: string): string {
  return props.sections.find(section => section.key === sectionKey)?.title || sectionKey;
}

function extractClipboardImage(event: ClipboardEvent): File | null {
  const items = Array.from(event.clipboardData?.items || []);
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      return item.getAsFile();
    }
  }
  return null;
}

async function importImageToSection(sectionKey: string, image: File) {
  if (!props.importImage) return;
  importingSectionKey.value = sectionKey;
  try {
    const imported = await props.importImage(sectionKey, image);
    const row = normalizeItem(imported);
    snapshot.value[sectionKey] = sortItems([row, ...(snapshot.value[sectionKey] || [])]);
    markSnapshotDirty();
    ElMessage.success(`已导入到 ${getSectionTitle(sectionKey)}，可继续粘贴`);
    if (!row.name.trim()) {
      void beginRenameRow(row.id);
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '截图导入失败');
  } finally {
    if (importingSectionKey.value === sectionKey) {
      importingSectionKey.value = '';
    }
  }
}

function toggleImportSection(sectionKey: string) {
  if (!canImportImage.value) return;
  if (pendingImportSectionKey.value === sectionKey) {
    pendingImportSectionKey.value = '';
    return;
  }
  pendingImportSectionKey.value = sectionKey;
  ElMessage.info(`已准备导入到 ${getSectionTitle(sectionKey)}，请直接粘贴截图`);
}

async function handleWindowPaste(event: ClipboardEvent) {
  const sectionKey = pendingImportSectionKey.value;
  if (!sectionKey || !props.importImage || importingSectionKey.value) {
    return;
  }
  const image = extractClipboardImage(event);
  if (!image) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  await importImageToSection(sectionKey, image);
}

function bindNameInputRef(itemId: string, instance: FocusableInputRef) {
  if (instance) {
    nameInputRefs.set(itemId, instance);
  } else {
    nameInputRefs.delete(itemId);
  }
}

function bindQualityInputRef(itemId: string, instance: FocusableInputRef) {
  if (instance) {
    qualityInputRefs.set(itemId, instance);
  } else {
    qualityInputRefs.delete(itemId);
  }
}

function shouldShowNameInput(row: FanxiuInventoryItem): boolean {
  return canEdit.value && (!row.name.trim() || renamingItemId.value === row.id);
}

function shouldShowQualityInput(row: FanxiuInventoryItem): boolean {
  return canEdit.value && qualityEditingItemId.value === row.id;
}

async function beginRenameRow(itemId: string) {
  if (!canEdit.value) return;
  renamingItemId.value = itemId;
  await nextTick();
  const input = nameInputRefs.get(itemId);
  input?.focus?.();
  input?.select?.();
}

function finishRenameRow(itemId: string) {
  const rowLocation = findRowById(itemId);
  if (rowLocation) {
    const trimmedName = rowLocation.item.name.trim();
    if (rowLocation.item.name !== trimmedName) {
      rowLocation.item.name = trimmedName;
      markSnapshotDirty();
    }
  }
  if (renamingItemId.value === itemId) {
    renamingItemId.value = '';
  }
}

function getQualityDraft(itemId: string): string {
  return qualityDrafts.value[itemId] ?? '';
}

async function beginEditQuality(itemId: string) {
  if (!canEdit.value) return;
  const rowLocation = findRowById(itemId);
  if (!rowLocation) return;
  qualityEditingItemId.value = itemId;
  qualityDrafts.value[itemId] = getQualityLabel(rowLocation.item.quality);
  await nextTick();
  const input = qualityInputRefs.get(itemId);
  input?.focus?.();
  input?.select?.();
}

function cancelEditQuality(itemId: string) {
  delete qualityDrafts.value[itemId];
  if (qualityEditingItemId.value === itemId) {
    qualityEditingItemId.value = '';
  }
}

function finishEditQuality(itemId: string) {
  const rowLocation = findRowById(itemId);
  if (!rowLocation) {
    cancelEditQuality(itemId);
    return;
  }

  const parsed = parseQualityText(getQualityDraft(itemId));
  if (parsed === undefined) {
    ElMessage.warning('品质格式无法识别');
    cancelEditQuality(itemId);
    return;
  }

  rowLocation.item.quality = parsed;
  sortSectionAfterInlineEdit(rowLocation.sectionKey);
  markSnapshotDirty();
  cancelEditQuality(itemId);
}

function findRowById(itemId: string): InventoryRowLocation | null {
  const targetId = String(itemId || '').trim();
  if (!targetId) return null;

  for (const section of props.sections) {
    const item = snapshot.value[section.key]?.find((entry) => entry.id === targetId);
    if (item) {
      return { sectionKey: section.key, item };
    }
  }
  return null;
}

function rowDateToTimestampMs(dateText: string): number {
  return new Date(`${normalizeDate(dateText)}T00:00:00`).getTime();
}

function timestampToDateText(timestampMs: number): string {
  if (!Number.isFinite(timestampMs) || timestampMs <= 0) {
    return todayText();
  }
  const current = new Date(timestampMs);
  const year = current.getFullYear();
  const month = String(current.getMonth() + 1).padStart(2, '0');
  const day = String(current.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function syncCachedNoteWithRow(item: FanxiuInventoryItem) {
  const cached = noteCache.value[item.id];
  if (!cached) return;
  const syncedNote = cloneNote({
    ...cached,
    title: item.name,
    weight: item.rank,
    start_at: rowDateToTimestampMs(item.date),
  });
  noteCache.value[item.id] = syncedNote;
  if (currentEditingItemId.value === item.id && currentEditingNote.value) {
    currentEditingNote.value = cloneNote(syncedNote);
  }
}

function applyLoadedNote(itemId: string, note: NoteNode) {
  const rowLocation = findRowById(itemId);
  if (!rowLocation) return;
  rowLocation.item.note_id = note.id;
  const syncedNote = cloneNote({
    ...note,
    title: rowLocation.item.name,
    weight: rowLocation.item.rank,
    start_at: rowDateToTimestampMs(rowLocation.item.date),
  });
  noteCache.value[itemId] = syncedNote;
  if (currentEditingItemId.value === itemId) {
    currentEditingNote.value = cloneNote(syncedNote);
  }
}

function buildInitialNotePayload(item: FanxiuInventoryItem): Partial<NoteNode> {
  return {
    title: item.name,
    content: '',
    weight: item.rank,
    start_at: rowDateToTimestampMs(item.date),
  };
}

function toKeepalivePayload(value: Partial<NoteNode>) {
  const payload: Record<string, unknown> = { ...value };
  if (typeof payload.start_at === 'number' && payload.start_at > 10000000000) {
    payload.start_at = payload.start_at / 1000;
  }
  return payload;
}

function getQualityLabel(value: number | null): string {
  if (value === null) {
    return '';
  }
  return QUALITY_LABELS[value] || '';
}

function getShenlianLabel(value: number): string {
  const normalized = normalizeShenlian(value);
  if (normalized === 0) {
    return '';
  }
  const whole = Math.floor(normalized / 8);
  const remainder = normalized % 8;

  if (remainder === 0) {
    return `${whole}阶`;
  }
  if (whole === 0) {
    return `${remainder}/8`;
  }
  return `${whole}阶${remainder}/8`;
}

function canIncreaseQuality(value: number | null): boolean {
  return value === null || value < QUALITY_LABELS.length - 1;
}

function canDecreaseQuality(value: number | null): boolean {
  return value !== null;
}

function adjustQuality(row: FanxiuInventoryItem, delta: 1 | -1) {
  if (!canEdit.value) return;
  const rowLocation = findRowById(row.id);
  const current = normalizeQuality(row.quality);
  if (current === null) {
    if (delta > 0) {
      row.quality = 0;
      if (rowLocation) sortSectionAfterInlineEdit(rowLocation.sectionKey);
      markSnapshotDirty();
    }
    return;
  }
  const next = current + delta;
  if (next < 0) {
    row.quality = null;
    if (rowLocation) sortSectionAfterInlineEdit(rowLocation.sectionKey);
    markSnapshotDirty();
    return;
  }
  row.quality = Math.min(Math.max(next, 0), QUALITY_LABELS.length - 1);
  if (rowLocation) sortSectionAfterInlineEdit(rowLocation.sectionKey);
  markSnapshotDirty();
}

function canIncreaseShenlian(value: number): boolean {
  return Number.isFinite(normalizeShenlian(value));
}

function canDecreaseShenlian(value: number): boolean {
  return normalizeShenlian(value) > 0;
}

function adjustShenlian(row: FanxiuInventoryItem, delta: 1 | -1) {
  if (!canEdit.value) return;
  const next = normalizeShenlian(row.shenlian) + delta;
  row.shenlian = Math.max(next, 0);
  markSnapshotDirty();
}

async function loadSnapshot() {
  pageHydrated.value = false;
  loading.value = true;
  try {
    const remoteSnapshot = normalizeSnapshot(await props.loadSnapshot());
    const { snapshot: restoredSnapshot, restored } = autoSave.loadSnapshot(remoteSnapshot, { draftStrategy: 'auto' });
    const effectiveSnapshot = prepareSnapshot(restoredSnapshot ?? remoteSnapshot);
    snapshot.value = snapshotToState(effectiveSnapshot);
    clearBufferedTextFieldState();

    if (currentEditingItemId.value && !findRowById(currentEditingItemId.value)) {
      currentEditingItemId.value = '';
      currentEditingNote.value = undefined;
    }

    pageHydrated.value = true;
    if (restored) {
      autoSave.markDirty(effectiveSnapshot);
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || `读取${props.resourceLabel}失败`);
  } finally {
    loading.value = false;
  }
}

function addRow(sectionKey: string) {
  const row = createNewRow();
  currentEditingItemId.value = '';
  currentEditingNote.value = undefined;
  editorLoading.value = false;
  snapshot.value[sectionKey].unshift(row);
  sortSection(sectionKey);
  markSnapshotDirty();
  void beginRenameRow(row.id);
}

function removeRow(sectionKey: string, rowId: string) {
  snapshot.value[sectionKey] = snapshot.value[sectionKey].filter((item) => item.id !== rowId);
  delete noteCache.value[rowId];
  nameInputRefs.delete(rowId);
  qualityInputRefs.delete(rowId);
  clearBufferedTextFieldState(rowId);
  if (renamingItemId.value === rowId) {
    renamingItemId.value = '';
  }
  if (qualityEditingItemId.value === rowId) {
    qualityEditingItemId.value = '';
  }
  delete qualityDrafts.value[rowId];
  if (currentEditingItemId.value === rowId) {
    currentEditingItemId.value = '';
    currentEditingNote.value = undefined;
  }
  markSnapshotDirty();
}

function handleDateChange(sectionKey: string) {
  sortSection(sectionKey);
  markSnapshotDirty();
}

async function handleRowClick(row: FanxiuInventoryItem) {
  if (!row.name.trim()) {
    currentEditingItemId.value = '';
    currentEditingNote.value = undefined;
    editorLoading.value = false;
    if (canEdit.value) void beginRenameRow(row.id);
    return;
  }

  currentEditingItemId.value = row.id;

  const cached = noteCache.value[row.id];
  if (cached) {
    currentEditingNote.value = cloneNote(cached);
    return;
  }

  editorLoading.value = true;
  currentEditingNote.value = undefined;
  try {
    const existingNote = await props.getNote(row.id);
    if (existingNote) {
      applyLoadedNote(row.id, existingNote);
      return;
    }

    if (!canEdit.value) {
      return;
    }

    const createdNote = await props.saveNote(row.id, buildInitialNotePayload(row));
    applyLoadedNote(row.id, createdNote);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '加载条目文档失败');
  } finally {
    editorLoading.value = false;
  }
}

async function handleSave(note: NoteNode, patch: EditableNotePatch = {}) {
  const row = currentEditingRow.value;
  if (!row) {
    throw new Error('未选中条目');
  }
  const payload = Object.keys(patch).length ? patch : note;
  const updatedNote = await props.saveNote(row.id, payload);
  applyLoadedNote(row.id, updatedNote);
  return updatedNote;
}

function handleSaveKeepalive(note: NoteNode, patch: EditableNotePatch = {}) {
  const row = currentEditingRow.value;
  if (!row) return;
  const payload = Object.keys(patch).length ? patch : note;
  putJsonKeepalive(
    `${props.keepalivePathPrefix}/${encodeURIComponent(row.id)}`,
    toKeepalivePayload(payload as Partial<NoteNode>),
  );
}

function onEditorNoteChange(note: NoteNode) {
  const rowLocation = currentEditingLocation.value;
  if (!rowLocation) return;
  let inventoryChanged = false;

  const nextNoteId = note.id || rowLocation.item.note_id || null;
  if (rowLocation.item.note_id !== nextNoteId) {
    rowLocation.item.note_id = nextNoteId;
    inventoryChanged = true;
  }

  if (typeof note.weight === 'number' && Number.isFinite(note.weight) && rowLocation.item.rank !== note.weight) {
    rowLocation.item.rank = Math.round(note.weight);
    sortSectionAfterInlineEdit(rowLocation.sectionKey);
    inventoryChanged = true;
  }

  if (typeof note.start_at === 'number' && Number.isFinite(note.start_at) && note.start_at > 0) {
    const nextDate = timestampToDateText(note.start_at);
    if (rowLocation.item.date !== nextDate) {
      rowLocation.item.date = nextDate;
      sortSection(rowLocation.sectionKey);
      inventoryChanged = true;
    }
  }

  const syncedNote = cloneNote({
    ...note,
    title: rowLocation.item.name,
    weight: rowLocation.item.rank,
    start_at: rowDateToTimestampMs(rowLocation.item.date),
  });
  noteCache.value[rowLocation.item.id] = syncedNote;
  currentEditingNote.value = cloneNote(syncedNote);
  if (inventoryChanged) {
    markSnapshotDirty();
  }
}

watch(
  () => [
    currentEditingRow.value?.id ?? '',
    currentEditingRow.value?.name ?? '',
    currentEditingRow.value?.rank ?? 0,
    currentEditingRow.value?.date ?? '',
  ],
  () => {
    if (currentEditingRow.value) {
      syncCachedNoteWithRow(currentEditingRow.value);
    }
  },
);

onMounted(() => {
  window.addEventListener('paste', handleWindowPaste);
  void loadSnapshot();
});

onBeforeUnmount(() => {
  window.removeEventListener('paste', handleWindowPaste);
  clearBufferedTextFieldState();
});
</script>

<template>
  <div class="wardrobe-layout">
    <NoteSplitView
      class="wardrobe-workspace"
      :top-height="structurePaneHeight"
      :show-editor="editorVisible"
      :empty-description="editorEmptyDescription"
      editor-mode="flow"
      :editor-min-height="320"
      @resize-start="startResizing"
    >
      <template #main>
        <div
          v-loading="loading"
          class="main-section"
          :class="{ 'main-section--table-scroll': tableInternalScroll }"
        >
          <h2 class="page-title">{{ title }}</h2>

          <div class="section-stack">
            <el-card v-for="section in sections" :key="section.key" class="section-card" shadow="never">
              <template #header>
                <div class="card-header">
                  <span>{{ section.title }}</span>
                  <div class="card-header__actions">
                    <div v-if="showViewFilters" class="card-header__filters">
                      <el-select
                        v-if="showCategoryColumn"
                        v-model="activeCategoryFilters"
                        multiple
                        collapse-tags
                        collapse-tags-tooltip
                        :max-collapse-tags="1"
                        size="small"
                        class="header-filter-select"
                        placeholder="分类"
                        clearable
                        @click.stop
                      >
                        <el-option
                          v-for="option in categoryOptions"
                          :key="option"
                          :label="getCategorySelectionLabel(option)"
                          :value="option"
                        />
                      </el-select>
                      <el-select
                        v-model="activeTypeFilters"
                        multiple
                        collapse-tags
                        collapse-tags-tooltip
                        :max-collapse-tags="1"
                        size="small"
                        class="header-filter-select"
                        placeholder="类型"
                        clearable
                        @click.stop
                      >
                        <el-option
                          v-for="option in ITEM_TYPE_OPTIONS"
                          :key="option"
                          :label="option"
                          :value="option"
                        />
                      </el-select>
                    </div>
                    <el-button
                      v-if="canImportImage"
                      type="primary"
                      link
                      :loading="importingSectionKey === section.key"
                      @click="toggleImportSection(section.key)"
                    >
                      {{
                        importingSectionKey === section.key
                          ? '识别中...'
                          : pendingImportSectionKey === section.key
                            ? '关闭粘贴导入'
                            : '粘贴截图导入'
                      }}
                    </el-button>
                    <el-button v-if="canEdit" type="primary" link :icon="Plus" @click="addRow(section.key)">
                      新增条目
                    </el-button>
                  </div>
                </div>
              </template>

              <div class="structure-table-wrap">
                <el-table
                  :data="getDisplayRows(section.key)"
                  border
                  stripe
                  size="small"
                  table-layout="auto"
                  class="compact-table"
                  row-key="id"
                  highlight-current-row
                  :current-row-key="currentEditingItemId"
                  empty-text="暂无条目"
                  :fit="false"
                  :max-height="tableMaxHeight"
                  @row-click="handleRowClick"
                >
                  <el-table-column type="index" label="编号" width="74" align="center" />

                  <el-table-column
                    v-if="showCategoryColumn"
                    :label="categoryColumnLabel || '分类'"
                    :width="resolvedCategoryColumnWidth"
                    align="center"
                    class-name="select-column"
                  >
                    <template #default="{ row }">
                      <div class="cell-control-wrap" @click.stop>
                        <el-select
                          v-model="row.category"
                          size="small"
                          :disabled="!canEdit"
                          class="type-select"
                          @change="markSnapshotDirty()"
                        >
                          <template #label="{ value }">
                            {{ getCategorySelectionLabel(String(value || '')) }}
                          </template>
                          <el-option
                            v-for="option in categoryOptions"
                            :key="option"
                            :label="option"
                            :value="option"
                          />
                        </el-select>
                      </div>
                    </template>
                  </el-table-column>

                  <el-table-column
                    label="名称"
                    class-name="name-column"
                    :min-width="nameColumnMinWidth"
                  >
                    <template #default="{ row }">
                      <el-input
                        v-if="shouldShowNameInput(row)"
                        :ref="(instance) => bindNameInputRef(row.id, instance as FocusableInputRef)"
                        v-model="row.name"
                        size="small"
                        :disabled="!canEdit"
                        :style="{ width: getNameInputWidth(row.name) }"
                        class="name-editor-input"
                        placeholder="输入名称"
                        @click.stop
                        @keydown.enter.stop.prevent="finishRenameRow(row.id)"
                        @keydown.esc.stop.prevent="finishRenameRow(row.id)"
                        @blur="finishRenameRow(row.id)"
                      />
                      <div
                        v-else
                        class="name-display"
                        :class="{ 'is-editable': canEdit }"
                        @dblclick.stop="beginRenameRow(row.id)"
                      >
                        {{ row.name || '-' }}
                      </div>
                    </template>
                  </el-table-column>

                  <el-table-column :width="resolvedTypeColumnWidth" label="类型" align="center" class-name="select-column">
                    <template #default="{ row }">
                      <div class="cell-control-wrap" @click.stop>
                        <el-select
                          v-model="row.type"
                          size="small"
                          clearable
                          :disabled="!canEdit"
                          class="type-select"
                          @change="markSnapshotDirty()"
                        >
                          <el-option
                            v-for="option in ITEM_TYPE_OPTIONS"
                            :key="option"
                            :label="option"
                            :value="option"
                          />
                        </el-select>
                      </div>
                    </template>
                  </el-table-column>

                  <el-table-column label="品质" width="120" align="center" class-name="stepper-column">
                    <template #default="{ row }">
                      <div v-if="shouldShowQualityInput(row)" class="cell-control-wrap" @click.stop>
                        <el-input
                          :ref="instance => bindQualityInputRef(row.id, instance as FocusableInputRef)"
                          :model-value="getQualityDraft(row.id)"
                          size="small"
                          class="quality-editor-input"
                          @update:model-value="value => (qualityDrafts[row.id] = String(value || ''))"
                          @keydown.enter.prevent="finishEditQuality(row.id)"
                          @keydown.esc.prevent="cancelEditQuality(row.id)"
                          @blur="finishEditQuality(row.id)"
                          @click.stop
                        />
                      </div>
                      <div v-else class="discrete-stepper" @click.stop>
                        <div
                          class="discrete-stepper__value discrete-stepper__value--selectable"
                          :class="{ 'is-editable': canEdit }"
                          @dblclick.stop="beginEditQuality(row.id)"
                        >
                          {{ getQualityLabel(row.quality) }}
                        </div>
                        <div class="discrete-stepper__controls">
                          <button
                            type="button"
                            class="discrete-stepper__button"
                            :disabled="!canEdit || !canIncreaseQuality(row.quality)"
                            @click.stop="adjustQuality(row, 1)"
                          >
                            <el-icon><ArrowUp /></el-icon>
                          </button>
                          <button
                            type="button"
                            class="discrete-stepper__button"
                            :disabled="!canEdit || !canDecreaseQuality(row.quality)"
                            @click.stop="adjustQuality(row, -1)"
                          >
                            <el-icon><ArrowDown /></el-icon>
                          </button>
                        </div>
                      </div>
                    </template>
                  </el-table-column>

                  <el-table-column label="阶级" width="76" align="center" class-name="number-column">
                    <template #default="{ row }">
                      <el-input-number
                        v-model="row.rank"
                        size="small"
                        :disabled="!canEdit"
                        :step="1"
                        controls-position="right"
                        style="width: 100%"
                        @change="() => { sortSectionAfterInlineEdit(section.key); markSnapshotDirty(); }"
                        @click.stop
                      />
                    </template>
                  </el-table-column>

                  <el-table-column label="神炼" width="102" align="center" class-name="stepper-column">
                    <template #default="{ row }">
                      <div class="discrete-stepper" @click.stop>
                        <div class="discrete-stepper__value">
                          {{ getShenlianLabel(row.shenlian) }}
                        </div>
                        <div class="discrete-stepper__controls">
                          <button
                            type="button"
                            class="discrete-stepper__button"
                            :disabled="!canEdit || !canIncreaseShenlian(row.shenlian)"
                            @click.stop="adjustShenlian(row, 1)"
                          >
                            <el-icon><ArrowUp /></el-icon>
                          </button>
                          <button
                            type="button"
                            class="discrete-stepper__button"
                            :disabled="!canEdit || !canDecreaseShenlian(row.shenlian)"
                            @click.stop="adjustShenlian(row, -1)"
                          >
                            <el-icon><ArrowDown /></el-icon>
                          </button>
                        </div>
                      </div>
                    </template>
                  </el-table-column>

                  <el-table-column label="主要用途" min-width="180">
                    <template #default="{ row }">
                      <el-input
                        :model-value="getBufferedTextFieldValue(row, 'main_use')"
                        size="small"
                        :disabled="!canEdit"
                        class="usage-input"
                        @update:model-value="value => queueBufferedTextFieldUpdate(row, 'main_use', String(value || ''))"
                        @blur="commitBufferedTextField(row, 'main_use')"
                        @click.stop
                      />
                    </template>
                  </el-table-column>

                  <el-table-column label="获取" min-width="170">
                    <template #default="{ row }">
                      <el-input
                        :model-value="getBufferedTextFieldValue(row, 'acquisition')"
                        size="small"
                        :disabled="!canEdit"
                        class="acquisition-input"
                        @update:model-value="value => queueBufferedTextFieldUpdate(row, 'acquisition', String(value || ''))"
                        @blur="commitBufferedTextField(row, 'acquisition')"
                        @click.stop
                      />
                    </template>
                  </el-table-column>

                  <el-table-column label="时间" width="168" align="center">
                    <template #default="{ row }">
                      <el-date-picker
                        v-model="row.date"
                        type="date"
                        :disabled="!canEdit"
                        value-format="YYYY-MM-DD"
                        format="YYYY-MM-DD"
                        style="width: 128px"
                        @click.stop
                        @change="handleDateChange(section.key)"
                      />
                    </template>
                  </el-table-column>

                  <el-table-column v-if="canEdit" label="操作" width="92" align="center">
                    <template #default="{ row }">
                      <el-button type="danger" link :icon="Delete" @click.stop="removeRow(section.key, row.id)">
                        删除
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </el-card>
          </div>
        </div>
      </template>

      <template #editor>
        <div v-loading="editorLoading" class="editor-shell" :class="{ 'is-collapsed': !currentEditingNote }">
          <div v-if="currentEditingNote" class="editor-container">
            <UniversalNoteEditor
              :model-value="currentEditingNote"
              :on-save="handleSave"
              :on-save-keepalive="handleSaveKeepalive"
              empty-text="文档加载中..."
              class="editor-instance"
              editor-layout="flow"
              @change="onEditorNoteChange"
              :readonly="currentEditingNote?.can_edit === false"
              :show-private-toggle="false"
              :lock-title="true"
              :lock-node-type="true"
              :lock-note-form="true"
            />
          </div>

          <div v-else class="empty-editor">
            <el-empty :description="editorEmptyDescription" :image-size="60" />
          </div>
        </div>
      </template>
    </NoteSplitView>
  </div>
</template>

<style scoped>
.wardrobe-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background-color: #f5f7fa;
  overflow-x: hidden;
  overflow-y: auto;
}

.wardrobe-workspace {
  min-height: 0;
}

.main-section {
  height: 100%;
  min-height: 0;
  padding: 20px;
  background-color: #f5f7fa;
  overflow: auto;
}

.main-section--table-scroll {
  overflow: hidden;
}

.page-title {
  margin: 0 0 16px;
  font-size: 28px;
  line-height: 1.2;
  color: var(--el-text-color-primary);
}

.section-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.section-card {
  border-radius: 14px;
}

.structure-table-wrap {
  width: 100%;
  overflow-x: auto;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}

.card-header__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.card-header__filters {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-filter-select {
  width: 96px;
}

.header-filter-select :deep(.el-select__wrapper) {
  min-height: 28px;
  height: 28px;
}

.compact-table {
  --inventory-row-font-size: 14px;
  --inventory-row-control-height: 24px;
  --inventory-row-cell-padding: 0px;
  --inventory-number-control-width: 22px;
  width: max-content;
  min-width: fit-content;
}

.compact-table :deep(.el-input__wrapper),
.compact-table :deep(.el-input-number .el-input__wrapper),
.compact-table :deep(.el-date-editor.el-input__wrapper),
.compact-table :deep(.el-select__wrapper) {
  min-height: var(--inventory-row-control-height);
  height: var(--inventory-row-control-height);
  padding-top: 0;
  padding-bottom: 0;
  box-shadow: none;
  background: transparent;
}

.compact-table :deep(.el-input-number) {
  height: var(--inventory-row-control-height);
  line-height: var(--inventory-row-control-height);
  vertical-align: middle;
}

.compact-table :deep(.el-input-number .el-input) {
  height: 100%;
}

.compact-table :deep(.number-column .cell),
.compact-table :deep(.select-column .cell),
.compact-table :deep(.stepper-column .cell) {
  display: flex;
  align-items: stretch;
  height: 100%;
  padding: 0 !important;
}

.compact-table :deep(.number-column.el-table__cell),
.compact-table :deep(.select-column.el-table__cell),
.compact-table :deep(.stepper-column.el-table__cell) {
  padding-top: 0 !important;
  padding-bottom: 0 !important;
}

.compact-table :deep(.number-column .el-input-number) {
  display: flex;
  width: 100% !important;
  min-width: 0;
  align-self: stretch;
}

.compact-table :deep(.number-column .el-input-number .el-input) {
  flex: 1 1 auto;
  width: 0;
  min-width: 0;
  height: 100%;
}

.compact-table :deep(.number-column .el-input-number .el-input__wrapper) {
  width: 100%;
  border-radius: 0;
}

.compact-table :deep(.number-column .el-input-number.is-controls-right .el-input__wrapper) {
  padding-left: 0;
  padding-right: var(--inventory-number-control-width);
}

.compact-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__decrease),
.compact-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__increase) {
  width: var(--inventory-number-control-width);
  height: calc(var(--inventory-row-control-height) / 2);
  line-height: calc(var(--inventory-row-control-height) / 2);
  right: 0;
  border-radius: 0;
}

.compact-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__increase) {
  top: 0;
}

.compact-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__decrease) {
  bottom: 0;
}

.compact-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__decrease .el-icon),
.compact-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__increase .el-icon) {
  transform: scale(0.9);
}

.cell-control-wrap {
  display: flex;
  width: 100%;
  min-width: 0;
  height: 100%;
  align-self: stretch;
}

.name-editor-input,
.type-select,
.usage-input,
.acquisition-input {
  display: inline-flex;
  vertical-align: middle;
}

.type-select {
  width: 100%;
  align-self: stretch;
}

.name-editor-input :deep(.el-input__wrapper),
.usage-input :deep(.el-input__wrapper),
.acquisition-input :deep(.el-input__wrapper) {
  min-height: var(--inventory-row-control-height);
  height: var(--inventory-row-control-height);
  padding: 0 7px;
}

.type-select :deep(.el-select__wrapper) {
  width: 100%;
  min-height: var(--inventory-row-control-height);
  height: var(--inventory-row-control-height);
  padding: 0 6px;
  border-radius: 0;
}

.quality-editor-input {
  width: 100%;
  align-self: stretch;
}

.quality-editor-input :deep(.el-input__wrapper) {
  min-height: var(--inventory-row-control-height);
  height: var(--inventory-row-control-height);
  padding: 0 6px;
  border-radius: 0;
}

.name-editor-input :deep(.el-input__inner),
.quality-editor-input :deep(.el-input__inner),
.usage-input :deep(.el-input__inner),
.acquisition-input :deep(.el-input__inner),
.type-select :deep(.el-select__selected-item) {
  height: var(--inventory-row-control-height);
  line-height: var(--inventory-row-control-height);
  font-size: var(--inventory-row-font-size);
}

.usage-input {
  width: 154px;
}

.acquisition-input {
  width: 146px;
}

.discrete-stepper {
  display: flex;
  width: 100%;
  min-height: 100%;
  height: 100%;
  border: 1px solid var(--el-border-color);
  background: transparent;
  align-self: stretch;
}

.discrete-stepper__value {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  font-size: 13px;
  white-space: nowrap;
}

.discrete-stepper__value--selectable {
  user-select: text;
}

.discrete-stepper__value.is-editable {
  cursor: text;
}

.discrete-stepper__controls {
  width: var(--inventory-number-control-width);
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--el-border-color);
}

.discrete-stepper__button {
  flex: 1 1 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--el-text-color-regular);
  cursor: pointer;
}

.discrete-stepper__button + .discrete-stepper__button {
  border-top: 1px solid var(--el-border-color);
}

.discrete-stepper__button:disabled {
  cursor: not-allowed;
  color: var(--el-text-color-placeholder);
}

.discrete-stepper__button:not(:disabled):hover {
  background: var(--el-fill-color-light);
}

.name-display {
  display: inline-flex;
  align-items: center;
  min-width: 24px;
  height: var(--inventory-row-control-height);
  padding: 0 7px;
  box-sizing: border-box;
  line-height: 1;
  white-space: nowrap;
  font-size: var(--inventory-row-font-size);
  color: var(--el-text-color-primary);
}

.name-display.is-editable {
  cursor: text;
}

.compact-table :deep(.el-input__wrapper.is-focus),
.compact-table :deep(.el-input-number .el-input__wrapper.is-focus),
.compact-table :deep(.el-date-editor.el-input__wrapper.is-focus),
.compact-table :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px var(--el-color-primary) inset;
  background: var(--el-fill-color-blank);
}

.compact-table :deep(.el-table__row) {
  cursor: pointer;
}

.compact-table :deep(.el-table__cell) {
  vertical-align: middle;
  padding: var(--inventory-row-cell-padding) 0;
  font-size: var(--inventory-row-font-size);
}

.compact-table :deep(.el-table__header-wrapper .el-table__cell) {
  padding: 6px 0;
  font-size: 13px;
}

.compact-table :deep(.el-input-number .el-input__inner),
.compact-table :deep(.el-date-editor .el-input__inner) {
  font-size: 13px;
  line-height: var(--inventory-row-control-height);
}

.compact-table :deep(.el-input-number .el-input__inner) {
  padding: 0;
  text-align: center;
}

.compact-table :deep(.name-column .cell) {
  display: flex;
  align-items: center;
  white-space: nowrap;
}

.editor-shell {
  background-color: #fff;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.editor-shell.is-collapsed {
  background-color: #fafafa;
}

.editor-container {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.editor-instance {
  min-height: 0;
}

.empty-editor {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 320px;
}

@media (max-width: 960px) {
  .main-section {
    padding: 16px;
  }

  .page-title {
    font-size: 24px;
  }
}
</style>

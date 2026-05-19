<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { Plus } from '@element-plus/icons-vue';
import { noteKey, type NoteNode } from '@/api/notes';
import {
  getFanxiuActivityList,
  getFanxiuActivityNote,
  saveFanxiuActivityList,
  saveFanxiuActivityNote,
  type FanxiuActivityItem,
  type FanxiuActivityListSnapshot,
} from '@/api/fanxiu';
import NoteSplitView from '@/components/NoteSplitView.vue';
import UniversalNoteEditor from '@/components/UniversalNoteEditor.vue';
import { useUserStore } from '@/store/userStore';
import { useAutoSave } from '@/utils/useAutoSave';
import { putJsonKeepalive } from '@/utils/keepaliveRequest';
import type { EditableNotePatch } from '@/utils/noteAutoSave';
import { useResizablePane } from '@/utils/useResizablePane';

type FocusableInputRef = {
  focus?: () => void;
  select?: () => void;
} | null;

type RowContextMenuState = {
  visible: boolean;
  x: number;
  y: number;
  rowId: string | null;
};

const ACTIVITY_CROSS_COUNT_OPTIONS = [0, 1, 2, 4, 8, 16, 32, 64] as const;
const ACTIVITY_CROSS_COUNT_SET = new Set<number>(ACTIVITY_CROSS_COUNT_OPTIONS);

const userStore = useUserStore();
const loading = ref(false);
const editorLoading = ref(false);
const pageHydrated = ref(false);
const snapshot = ref<FanxiuActivityListSnapshot>(createEmptySnapshot());
const noteCache = ref<Record<string, NoteNode | null | undefined>>({});
const currentEditingItemId = ref('');
const currentEditingNote = ref<NoteNode | undefined>(undefined);
const renamingItemId = ref('');
const nameInputRefs = new Map<string, FocusableInputRef>();
const todayMarker = ref(todayText());
const rowContextMenuRef = ref<HTMLElement | null>(null);
const rowContextMenu = ref<RowContextMenuState>({
  visible: false,
  x: 0,
  y: 0,
  rowId: null,
});
let todayTickerHandle: number | null = null;

const canEdit = computed(() => {
  const username = userStore.user?.username;
  return username === '凡修手游' || userStore.isAdmin;
});

const currentEditingRow = computed(() => findRowById(currentEditingItemId.value));
const currentContextMenuRow = computed(() => findRowById(rowContextMenu.value.rowId));
const editorEmptyDescription = computed(() => {
  if (editorLoading.value) return '文档加载中...';
  if (!currentEditingItemId.value) return '点击上方活动开始编辑文档';
  if (!currentEditingRow.value?.name.trim()) return '请先填写活动名称，再编辑文档';
  return '当前活动暂无文档';
});
const editorVisible = computed(() => Boolean(currentEditingItemId.value));
const rowContextMenuStyle = computed(() => ({
  left: `${rowContextMenu.value.x}px`,
  top: `${rowContextMenu.value.y}px`,
}));
const activityRowClassName = computed(() => {
  const currentDay = todayMarker.value;
  return ({ row }: { row: FanxiuActivityItem }) => (isActivityEnded(row, currentDay) ? 'is-ended-row' : '');
});

const autoSave = useAutoSave<FanxiuActivityListSnapshot>({
  debounceMs: 800,
  equals: (left, right) => serializeSnapshot(left) === serializeSnapshot(right),
  storageKey: () => (canEdit.value ? buildDraftStorageKey() : null),
  save: async value => saveFanxiuActivityList(prepareSnapshot(value)),
  onError: (error: unknown) => {
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '自动保存活动列表失败');
  },
});

function calculateStructurePaneBounds() {
  const viewportHeight = window.innerHeight;
  const isNarrow = window.innerWidth < 960;
  const reservedHeight = isNarrow ? 170 : 130;
  const availableHeight = Math.max(460, viewportHeight - reservedHeight);
  const minEditorHeight = isNarrow ? 300 : 360;
  const maxHeight = Math.max(240, availableHeight - minEditorHeight);
  const adaptiveHeight = Math.min(
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
  startResizing,
} = useResizablePane({
  initialHeight: 560,
  getAdaptiveHeight: () => calculateStructurePaneBounds().adaptiveHeight,
  getResizeBounds: () => ({
    min: 220,
    max: calculateStructurePaneBounds().maxHeight,
  }),
  storageKey: 'fanxiu:activity-list:split-pane-height',
});

function createEmptySnapshot(): FanxiuActivityListSnapshot {
  return { items: [] };
}

function cloneNote(note: NoteNode): NoteNode {
  return JSON.parse(JSON.stringify(note));
}

function normalizeItem(item: Partial<FanxiuActivityItem> | null | undefined): FanxiuActivityItem {
  const startDate = normalizeDate(item?.start_date);
  const { name, crossCount } = splitActivityNameAndCross(item?.name);
  return {
    id: String(item?.id || buildRowId()),
    name,
    cross_count: crossCount ?? normalizeActivityCrossCount(
      (item as any)?.cross_count ?? (item as any)?.crossCount ?? (item as any)?.cross,
    ),
    start_date: startDate,
    end_date: normalizeDate(item?.end_date, startDate),
    note_id: item?.note_id ? String(item.note_id) : null,
  };
}

function normalizeSnapshot(value: Partial<FanxiuActivityListSnapshot> | null | undefined): FanxiuActivityListSnapshot {
  const items = Array.isArray(value?.items) ? value.items : [];
  return {
    items: sortItems(items.map(normalizeItem)),
  };
}

function serializeSnapshot(value: FanxiuActivityListSnapshot): string {
  return JSON.stringify(value);
}

function prepareSnapshot(value: Partial<FanxiuActivityListSnapshot>): FanxiuActivityListSnapshot {
  return normalizeSnapshot(value);
}

function buildRowId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `activity-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function buildDraftStorageKey(): string {
  const scope = userStore.user?.id ?? userStore.user?.username ?? 'fanxiu';
  return `fanxiu:activity-list:${scope}`;
}

function closeRowContextMenu() {
  rowContextMenu.value.visible = false;
  rowContextMenu.value.rowId = null;
}

function bindRowContextMenuPosition(clientX: number, clientY: number) {
  rowContextMenu.value.x = clientX;
  rowContextMenu.value.y = clientY;

  void nextTick(() => {
    const menuEl = rowContextMenuRef.value;
    if (!menuEl) {
      return;
    }
    const margin = 12;
    const rect = menuEl.getBoundingClientRect();
    const maxX = Math.max(margin, window.innerWidth - rect.width - margin);
    const maxY = Math.max(margin, window.innerHeight - rect.height - margin);
    rowContextMenu.value.x = Math.min(clientX, maxX);
    rowContextMenu.value.y = Math.min(clientY, maxY);
  });
}

function openRowContextMenu(rowId: string, event: MouseEvent) {
  event.preventDefault();
  event.stopPropagation();
  rowContextMenu.value.visible = true;
  rowContextMenu.value.rowId = rowId;
  bindRowContextMenuPosition(event.clientX, event.clientY);
}

function todayText(): string {
  const current = new Date();
  const year = current.getFullYear();
  const month = String(current.getMonth() + 1).padStart(2, '0');
  const day = String(current.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function normalizeDate(value: string | null | undefined, fallback = todayText()): string {
  const text = String(value || '').trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : fallback;
}

function parseActivityCrossCount(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  const text = String(value).trim().replace(/跨$/, '').trim();
  if (!text) {
    return null;
  }
  const numeric = Number(text);
  if (!Number.isInteger(numeric)) {
    return null;
  }
  return ACTIVITY_CROSS_COUNT_SET.has(numeric) ? numeric : null;
}

function normalizeActivityCrossCount(value: unknown): number {
  return parseActivityCrossCount(value) ?? 0;
}

function splitActivityNameAndCross(value: unknown): { name: string; crossCount: number | null } {
  const text = String(value || '').trim();
  const match = /^(.*?)\s*(?<!\d)(64|32|16|8|4|2|1|0)\s*跨$/.exec(text);
  if (!match) {
    return { name: text, crossCount: null };
  }
  return {
    name: match[1].trim(),
    crossCount: normalizeActivityCrossCount(match[2]),
  };
}

function getCrossCountLabel(value: unknown): string {
  return `${normalizeActivityCrossCount(value)}跨`;
}

function refreshTodayMarker() {
  todayMarker.value = todayText();
}

function getWeekdayLabel(dateText: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateText || '').trim());
  if (!match) return '-';

  const [, yearText, monthText, dayText] = match;
  const date = new Date(Number(yearText), Number(monthText) - 1, Number(dayText));
  const weekdayLabels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  return weekdayLabels[date.getDay()] ?? '-';
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
  const widthPx = Math.min(560, Math.max(144, 32 + units * 13));
  return `${widthPx}px`;
}

function getActivitySortStatusWeight(row: FanxiuActivityItem, currentDay = todayMarker.value): number {
  return isActivityEnded(row, currentDay) ? 1 : 0;
}

function sortItems(items: FanxiuActivityItem[], currentDay = todayMarker.value): FanxiuActivityItem[] {
  return [...items].sort((left, right) => {
    const statusCompare =
      getActivitySortStatusWeight(left, currentDay) - getActivitySortStatusWeight(right, currentDay);
    if (statusCompare !== 0) return statusCompare;

    const startCompare = right.start_date.localeCompare(left.start_date);
    if (startCompare !== 0) return startCompare;
    const endCompare = right.end_date.localeCompare(left.end_date);
    if (endCompare !== 0) return endCompare;
    return left.id.localeCompare(right.id);
  });
}

function sortSnapshot() {
  snapshot.value.items = sortItems(snapshot.value.items);
}

function isActivityEnded(row: FanxiuActivityItem, currentDay = todayMarker.value): boolean {
  return normalizeDate(row.end_date, row.start_date) < currentDay;
}

function createNewRow(): FanxiuActivityItem {
  const today = todayText();
  return {
    id: buildRowId(),
    name: '',
    cross_count: 0,
    start_date: today,
    end_date: today,
    note_id: null,
  };
}

function bindNameInputRef(itemId: string, instance: FocusableInputRef) {
  if (instance) {
    nameInputRefs.set(itemId, instance);
  } else {
    nameInputRefs.delete(itemId);
  }
}

function shouldShowNameInput(row: FanxiuActivityItem): boolean {
  return canEdit.value && (!row.name.trim() || renamingItemId.value === row.id);
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
  const row = findRowById(itemId);
  if (row) {
    const { name, crossCount } = splitActivityNameAndCross(row.name);
    row.name = name;
    row.cross_count = crossCount ?? normalizeActivityCrossCount(row.cross_count);
  }
  if (renamingItemId.value === itemId) {
    renamingItemId.value = '';
  }
}

function handleCrossCountChange(row: FanxiuActivityItem) {
  row.cross_count = normalizeActivityCrossCount(row.cross_count);
}

function findRowById(itemId: string): FanxiuActivityItem | null {
  const targetId = String(itemId || '').trim();
  if (!targetId) return null;
  return snapshot.value.items.find(item => item.id === targetId) ?? null;
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

function syncCachedNoteWithRow(item: FanxiuActivityItem) {
  const cached = noteCache.value[item.id];
  if (!cached) return;
  const syncedNote = cloneNote({
    ...cached,
    title: item.name,
    start_at: rowDateToTimestampMs(item.start_date),
  });
  noteCache.value[item.id] = syncedNote;
  if (currentEditingItemId.value === item.id && currentEditingNote.value) {
    currentEditingNote.value = cloneNote(syncedNote);
  }
}

function applyLoadedNote(itemId: string, note: NoteNode) {
  const row = findRowById(itemId);
  if (!row) return;
  row.note_id = noteKey(note.id);
  const syncedNote = cloneNote({
    ...note,
    title: row.name,
    start_at: rowDateToTimestampMs(row.start_date),
  });
  noteCache.value[itemId] = syncedNote;
  if (currentEditingItemId.value === itemId) {
    currentEditingNote.value = cloneNote(syncedNote);
  }
}

function buildInitialNotePayload(item: FanxiuActivityItem): Partial<NoteNode> {
  return {
    title: item.name,
    content: '',
    start_at: rowDateToTimestampMs(item.start_date),
  };
}

function toKeepalivePayload(value: Partial<NoteNode>) {
  const payload: Record<string, unknown> = { ...value };
  if (typeof payload.start_at === 'number' && payload.start_at > 10000000000) {
    payload.start_at = payload.start_at / 1000;
  }
  return payload;
}

async function loadSnapshot() {
  pageHydrated.value = false;
  loading.value = true;
  try {
    const remoteSnapshot = normalizeSnapshot(await getFanxiuActivityList());
    const { snapshot: restoredSnapshot, restored } = autoSave.loadSnapshot(remoteSnapshot, { draftStrategy: 'auto' });
    const effectiveSnapshot = prepareSnapshot(restoredSnapshot ?? remoteSnapshot);
    snapshot.value = effectiveSnapshot;

    if (currentEditingItemId.value && !findRowById(currentEditingItemId.value)) {
      currentEditingItemId.value = '';
      currentEditingNote.value = undefined;
    }

    pageHydrated.value = true;
    if (restored) {
      autoSave.markDirty(effectiveSnapshot);
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取活动列表失败');
  } finally {
    loading.value = false;
  }
}

function addRow() {
  const row = createNewRow();
  currentEditingItemId.value = '';
  currentEditingNote.value = undefined;
  editorLoading.value = false;
  snapshot.value.items.unshift(row);
  void beginRenameRow(row.id);
}

function removeRow(rowId: string) {
  snapshot.value.items = snapshot.value.items.filter(item => item.id !== rowId);
  delete noteCache.value[rowId];
  nameInputRefs.delete(rowId);
  if (rowContextMenu.value.rowId === rowId) {
    closeRowContextMenu();
  }
  if (renamingItemId.value === rowId) {
    renamingItemId.value = '';
  }
  if (currentEditingItemId.value === rowId) {
    currentEditingItemId.value = '';
    currentEditingNote.value = undefined;
  }
}

function normalizeDateRange(row: FanxiuActivityItem, changedField: 'start' | 'end') {
  row.start_date = normalizeDate(row.start_date);
  row.end_date = normalizeDate(row.end_date, row.start_date);

  if (row.start_date <= row.end_date) {
    return;
  }

  if (changedField === 'start') {
    row.end_date = row.start_date;
    return;
  }

  row.start_date = row.end_date;
}

function handleDateChange(row: FanxiuActivityItem, changedField: 'start' | 'end') {
  normalizeDateRange(row, changedField);
  sortSnapshot();
}

async function handleRowClick(row: FanxiuActivityItem) {
  closeRowContextMenu();
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
    const existingNote = await getFanxiuActivityNote(row.id);
    if (existingNote) {
      applyLoadedNote(row.id, existingNote);
      return;
    }

    if (!canEdit.value) {
      return;
    }

    const createdNote = await saveFanxiuActivityNote(row.id, buildInitialNotePayload(row));
    applyLoadedNote(row.id, createdNote);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '加载活动文档失败');
  } finally {
    editorLoading.value = false;
  }
}

async function handleRowContextMenu(row: FanxiuActivityItem, _column: unknown, event: MouseEvent) {
  if (!canEdit.value) {
    return;
  }
  await handleRowClick(row);
  openRowContextMenu(row.id, event);
}

function handleContextMenuDelete() {
  const row = currentContextMenuRow.value;
  if (!row) {
    closeRowContextMenu();
    return;
  }
  closeRowContextMenu();
  removeRow(row.id);
}

function handleGlobalPointerDown(event: MouseEvent) {
  if (!rowContextMenu.value.visible) {
    return;
  }
  const target = event.target as Node | null;
  if (target && rowContextMenuRef.value?.contains(target)) {
    return;
  }
  closeRowContextMenu();
}

async function handleSave(note: NoteNode, patch: EditableNotePatch = {}) {
  const row = currentEditingRow.value;
  if (!row) {
    throw new Error('未选中活动');
  }
  const payload = Object.keys(patch).length ? patch : note;
  const updatedNote = await saveFanxiuActivityNote(row.id, payload);
  applyLoadedNote(row.id, updatedNote);
  return updatedNote;
}

function handleSaveKeepalive(note: NoteNode, patch: EditableNotePatch = {}) {
  const row = currentEditingRow.value;
  if (!row) return;
  const payload = Object.keys(patch).length ? patch : note;
  putJsonKeepalive(
    `/api/fanxiu/activity-notes/${encodeURIComponent(row.id)}`,
    toKeepalivePayload(payload as Partial<NoteNode>),
  );
}

function onEditorNoteChange(note: NoteNode) {
  const row = currentEditingRow.value;
  if (!row) return;

  row.note_id = note.id ? noteKey(note.id) : row.note_id || null;

  if (typeof note.start_at === 'number' && Number.isFinite(note.start_at) && note.start_at > 0) {
    const nextDate = timestampToDateText(note.start_at);
    if (row.start_date !== nextDate) {
      row.start_date = nextDate;
      sortSnapshot();
    }
  }

  const syncedNote = cloneNote({
    ...note,
    title: row.name,
    start_at: rowDateToTimestampMs(row.start_date),
  });
  noteCache.value[row.id] = syncedNote;
  currentEditingNote.value = cloneNote(syncedNote);
}

watch(
  snapshot,
  (value) => {
    if (!pageHydrated.value) {
      return;
    }
    autoSave.markDirty(prepareSnapshot(value));
  },
  { deep: true },
);

watch(
  () => [
    currentEditingRow.value?.id ?? '',
    currentEditingRow.value?.name ?? '',
    currentEditingRow.value?.start_date ?? '',
  ],
  () => {
    if (currentEditingRow.value) {
      syncCachedNoteWithRow(currentEditingRow.value);
    }
  },
);

watch(todayMarker, () => {
  sortSnapshot();
});

onMounted(() => {
  document.addEventListener('mousedown', handleGlobalPointerDown);
  window.addEventListener('resize', closeRowContextMenu);
  window.addEventListener('scroll', closeRowContextMenu, true);
  refreshTodayMarker();
  todayTickerHandle = window.setInterval(refreshTodayMarker, 60_000);
  void loadSnapshot();
});

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleGlobalPointerDown);
  window.removeEventListener('resize', closeRowContextMenu);
  window.removeEventListener('scroll', closeRowContextMenu, true);
  if (todayTickerHandle !== null) {
    window.clearInterval(todayTickerHandle);
    todayTickerHandle = null;
  }
});
</script>

<template>
  <div class="activity-layout">
    <NoteSplitView
      class="activity-workspace"
      :top-height="structurePaneHeight"
      :show-editor="editorVisible"
      :empty-description="editorEmptyDescription"
      editor-mode="flow"
      :editor-min-height="320"
      @resize-start="startResizing"
    >
      <template #main>
        <div v-loading="loading" class="main-section">
          <h2 class="page-title">活动列表</h2>

          <el-card class="activity-card" shadow="never">
            <template #header>
              <div class="card-header">
                <span>活动</span>
                <el-button v-if="canEdit" type="primary" link :icon="Plus" @click="addRow">
                  新增条目
                </el-button>
              </div>
            </template>

            <div class="structure-table-wrap">
              <el-table
                :data="snapshot.items"
                border
                stripe
                size="small"
                table-layout="auto"
                class="compact-table"
                row-key="id"
                highlight-current-row
                :current-row-key="currentEditingItemId"
                :row-class-name="activityRowClassName"
                empty-text="暂无活动"
                :fit="false"
                @row-click="handleRowClick"
                @row-contextmenu="handleRowContextMenu"
              >
                <el-table-column type="index" label="编号" width="74" align="center" />

                <el-table-column label="名称" class-name="name-column">
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

                <el-table-column label="跨数" width="92" align="center" class-name="cross-count-column">
                  <template #default="{ row }">
                    <el-select
                      v-if="canEdit"
                      v-model="row.cross_count"
                      size="small"
                      class="cross-count-select"
                      @click.stop
                      @change="handleCrossCountChange(row)"
                    >
                      <el-option
                        v-for="option in ACTIVITY_CROSS_COUNT_OPTIONS"
                        :key="option"
                        :label="getCrossCountLabel(option)"
                        :value="option"
                      />
                    </el-select>
                    <span v-else class="cross-count-display">{{ getCrossCountLabel(row.cross_count) }}</span>
                  </template>
                </el-table-column>

                <el-table-column
                  width="168"
                  align="center"
                  class-name="date-cell-column"
                  label-class-name="date-header-column"
                >
                  <template #header>
                    <span class="date-header-label">开始日期</span>
                  </template>
                  <template #default="{ row }">
                    <div class="date-picker-cell">
                      <el-date-picker
                        v-model="row.start_date"
                        type="date"
                        :disabled="!canEdit"
                        value-format="YYYY-MM-DD"
                        format="YYYY-MM-DD"
                        style="width: 128px"
                        @click.stop
                        @change="handleDateChange(row, 'start')"
                      />
                    </div>
                  </template>
                </el-table-column>

                <el-table-column label="开始周几" width="92" align="center">
                  <template #default="{ row }">
                    <span class="weekday-display">{{ getWeekdayLabel(row.start_date) }}</span>
                  </template>
                </el-table-column>

                <el-table-column
                  width="168"
                  align="center"
                  class-name="date-cell-column"
                  label-class-name="date-header-column"
                >
                  <template #header>
                    <span class="date-header-label">结束日期</span>
                  </template>
                  <template #default="{ row }">
                    <div class="date-picker-cell">
                      <el-date-picker
                        v-model="row.end_date"
                        type="date"
                        :disabled="!canEdit"
                        value-format="YYYY-MM-DD"
                        format="YYYY-MM-DD"
                        style="width: 128px"
                        @click.stop
                        @change="handleDateChange(row, 'end')"
                      />
                    </div>
                  </template>
                </el-table-column>

                <el-table-column label="结束周几" width="92" align="center">
                  <template #default="{ row }">
                    <span class="weekday-display">{{ getWeekdayLabel(row.end_date) }}</span>
                  </template>
                </el-table-column>

              </el-table>
            </div>
          </el-card>
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

    <Teleport to="body">
      <div
        v-if="canEdit && rowContextMenu.visible && currentContextMenuRow"
        ref="rowContextMenuRef"
        class="row-context-menu"
        :style="rowContextMenuStyle"
        @mousedown.stop
        @contextmenu.prevent
      >
        <button type="button" class="row-context-menu-item danger" @click="handleContextMenuDelete">
          删除
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.activity-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background-color: #f5f7fa;
  overflow-x: hidden;
  overflow-y: auto;
}

.activity-workspace {
  min-height: 0;
}

.main-section {
  height: 100%;
  min-height: 0;
  padding: 20px;
  background-color: #f5f7fa;
  overflow: auto;
}

.page-title {
  margin: 0 0 16px;
  font-size: 28px;
  line-height: 1.2;
  color: var(--el-text-color-primary);
}

.activity-card {
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

.compact-table {
  --activity-row-font-size: 14px;
  --activity-row-control-height: 24px;
  --activity-row-cell-padding: 5px;
  width: max-content;
  min-width: fit-content;
}

.compact-table :deep(.el-input__wrapper),
.compact-table :deep(.el-date-editor.el-input__wrapper),
.compact-table :deep(.el-select__wrapper) {
  min-height: var(--activity-row-control-height);
  height: var(--activity-row-control-height);
  padding-top: 0;
  padding-bottom: 0;
  box-shadow: none;
  background: transparent;
}

.name-editor-input {
  display: inline-flex;
  vertical-align: middle;
}

.name-editor-input :deep(.el-input__wrapper) {
  min-height: var(--activity-row-control-height);
  height: var(--activity-row-control-height);
  padding: 0 7px;
}

.name-editor-input :deep(.el-input__inner) {
  height: var(--activity-row-control-height);
  line-height: var(--activity-row-control-height);
  font-size: var(--activity-row-font-size);
}

.name-display {
  display: inline-flex;
  align-items: center;
  min-width: 24px;
  height: var(--activity-row-control-height);
  padding: 0 7px;
  box-sizing: border-box;
  line-height: 1;
  white-space: nowrap;
  font-size: var(--activity-row-font-size);
  color: var(--el-text-color-primary);
}

.name-display.is-editable {
  cursor: text;
}

.cross-count-select {
  width: 72px;
  vertical-align: middle;
}

.cross-count-select :deep(.el-select__wrapper) {
  padding: 0 7px;
}

.cross-count-display {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: var(--activity-row-control-height);
  font-size: var(--activity-row-font-size);
  color: var(--el-text-color-regular);
  white-space: nowrap;
}

.weekday-display {
  display: inline-flex;
  align-items: center;
  height: var(--activity-row-control-height);
  color: var(--el-text-color-regular);
  font-size: var(--activity-row-font-size);
  white-space: nowrap;
}

.compact-table :deep(.el-input__wrapper.is-focus),
.compact-table :deep(.el-date-editor.el-input__wrapper.is-focus),
.compact-table :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px var(--el-color-primary) inset;
  background: var(--el-fill-color-blank);
}

.compact-table :deep(.el-table__row) {
  cursor: pointer;
}

.compact-table :deep(.el-table__row.is-ended-row:not(.current-row) > .el-table__cell) {
  background: rgba(144, 147, 153, 0.08);
}

.compact-table :deep(.el-table__row.is-ended-row .cell),
.compact-table :deep(.el-table__row.is-ended-row .name-display),
.compact-table :deep(.el-table__row.is-ended-row .cross-count-display),
.compact-table :deep(.el-table__row.is-ended-row .weekday-display),
.compact-table :deep(.el-table__row.is-ended-row .el-input__inner),
.compact-table :deep(.el-table__row.is-ended-row .el-input__wrapper),
.compact-table :deep(.el-table__row.is-ended-row .el-select__selected-item),
.compact-table :deep(.el-table__row.is-ended-row .el-input__prefix),
.compact-table :deep(.el-table__row.is-ended-row .el-input__suffix) {
  color: var(--el-text-color-secondary);
}

.compact-table :deep(.el-table__cell) {
  vertical-align: middle;
  padding: var(--activity-row-cell-padding) 0;
  font-size: var(--activity-row-font-size);
}

.compact-table :deep(.el-table__header-wrapper .el-table__cell) {
  padding: 6px 0;
  font-size: 13px;
}

.date-header-label {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1.4;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}

.date-picker-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: var(--activity-row-control-height);
  line-height: 0;
}

.compact-table :deep(.el-table__body-wrapper .date-cell-column .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 0;
}

.compact-table :deep(.el-table__header-wrapper .date-header-column .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1.4;
}

.compact-table :deep(.date-cell-column .el-date-editor) {
  display: flex;
  align-items: center;
  height: var(--activity-row-control-height);
  line-height: 0;
}

.compact-table :deep(.date-cell-column .el-date-editor.el-input__wrapper) {
  display: inline-flex;
  align-items: center;
  padding: 0 7px;
}

.compact-table :deep(.date-cell-column .el-input__prefix),
.compact-table :deep(.date-cell-column .el-input__suffix) {
  display: inline-flex;
  align-items: center;
}

.compact-table :deep(.el-date-editor .el-input__inner) {
  font-size: 13px;
  height: var(--activity-row-control-height);
  line-height: var(--activity-row-control-height);
}

.compact-table :deep(.name-column .cell) {
  display: flex;
  align-items: center;
  white-space: nowrap;
}

.compact-table :deep(.cross-count-column .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
}

.row-context-menu {
  position: fixed;
  z-index: 2200;
  min-width: 116px;
  padding: 6px;
  border: 1px solid rgba(209, 221, 232, 0.92);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.16);
  backdrop-filter: blur(10px);
}

.row-context-menu-item {
  width: 100%;
  border: 0;
  border-radius: 10px;
  background: transparent;
  padding: 9px 12px;
  text-align: left;
  color: #173042;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.row-context-menu-item:hover {
  background: rgba(64, 158, 255, 0.08);
}

.row-context-menu-item.danger {
  color: #c45656;
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

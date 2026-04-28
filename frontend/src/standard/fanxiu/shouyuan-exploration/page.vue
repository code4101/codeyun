<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue';
import { ElMessage } from 'element-plus';
import { Delete, Plus } from '@element-plus/icons-vue';
import Sortable from 'sortablejs';
import SortableOrderHandle from '@/components/SortableOrderHandle.vue';
import {
  getFanxiuActivityList,
  getFanxiuShouyuanExplorationExchangeList,
  importFanxiuShouyuanExplorationExchangeListFromOcr,
  importFanxiuShouyuanExplorationIncomeSpeedFromOcr,
  importFanxiuShouyuanExplorationPersonalRankingsFromOcr,
  saveFanxiuShouyuanExplorationExchangeList,
  type FanxiuActivityItem,
  type FanxiuShouyuanExplorationConsumptionEvaluationItem,
  type FanxiuShouyuanExplorationExchangeItem,
  type FanxiuShouyuanExplorationIncomeSpeedItem,
  type FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse,
  type FanxiuShouyuanExplorationOcrImportResponse,
  type FanxiuShouyuanExplorationPersonalRankingItem,
  type FanxiuShouyuanExplorationPersonalRankingOcrImportResponse,
  type FanxiuShouyuanExplorationRecord,
  type FanxiuShouyuanExplorationSnapshot,
} from '@/api/fanxiu';
import { useUserStore } from '@/store/userStore';
import { useAutoSave } from '@/utils/useAutoSave';

type FanxiuShouyuanExplorationRecordInput = Partial<FanxiuShouyuanExplorationRecord> & {
  title?: string;
  remark?: string;
  personal_rankings?: Partial<FanxiuShouyuanExplorationPersonalRankingItem>[];
  income_speeds?: Partial<FanxiuShouyuanExplorationIncomeSpeedItem>[];
  consumption_evaluations?: Partial<FanxiuShouyuanExplorationConsumptionEvaluationItem>[];
  items?: Partial<FanxiuShouyuanExplorationExchangeItem>[];
};

type FanxiuShouyuanExplorationSnapshotInput = Partial<FanxiuShouyuanExplorationSnapshot> & {
  items?: Partial<FanxiuShouyuanExplorationExchangeItem>[];
  records?: FanxiuShouyuanExplorationRecordInput[];
  label?: string;
  title?: string;
  remark?: string;
};

const DEFAULT_RECORD_LABEL = '8跨';
const LOCAL_SELECTION_STORAGE_PREFIX = 'fanxiu:shouyuan-exploration-selection';
const SHOUYUAN_EXPLORATION_DRAFT_SCHEMA_VERSION = 'v3';
const ACTIVITY_CROSS_COUNT_OPTIONS = [0, 1, 2, 4, 8, 16, 32, 64] as const;
const ACTIVITY_CROSS_COUNT_SET = new Set<number>(ACTIVITY_CROSS_COUNT_OPTIONS);

type PendingImportSection = 'personal-ranking' | 'income-speed' | 'exchange';
type PendingImportTarget = { recordId: string; section: PendingImportSection };

const userStore = useUserStore();
const loading = ref(false);
const pageHydrated = ref(false);
const importing = ref(false);
const pendingImportTarget = ref<PendingImportTarget | null>(null);
const pendingAddActivityId = ref('');
const activityItems = ref<FanxiuActivityItem[]>([]);
const snapshot = ref<FanxiuShouyuanExplorationSnapshot>(createEmptySnapshot());
const selectedItemIdsByRecord = ref<Record<string, string[]>>({});
const exchangeTableBodyRefs = new Map<string, HTMLElement>();
const exchangeSortableInstances = new Map<string, Sortable>();
let exchangeSortableRefreshToken = 0;

const canEdit = computed(() => {
  const username = userStore.user?.username;
  return username === '凡修手游' || userStore.isAdmin;
});

const shouyuanActivities = computed(() => {
  return activityItems.value.filter(item => normalizeText(item.name).includes('兽渊探秘'));
});

const orderedRecords = computed(() => snapshot.value.records);

const activityById = computed<Record<string, FanxiuActivityItem>>(() => {
  return Object.fromEntries(shouyuanActivities.value.map(activity => [activity.id, activity])) as Record<string, FanxiuActivityItem>;
});

const unboundActivities = computed(() => {
  const boundActivityIds = new Set(
    orderedRecords.value
      .map(record => normalizeText(record.activity_id))
      .filter(Boolean),
  );
  return shouyuanActivities.value.filter(activity => !boundActivityIds.has(activity.id));
});

const latestEightCrossActivity = computed(() => {
  return [...shouyuanActivities.value]
    .filter(activity => normalizeActivityCrossCount(activity.cross_count) === 8)
    .sort(compareActivitiesByRecentDate)[0] ?? null;
});

const currentImportRecordLabel = computed(() => {
  const record = getRecordById(pendingImportTarget.value?.recordId);
  return record ? getRecordTitle(record) : '';
});

const currentImportSectionLabel = computed(() => {
  return pendingImportTarget.value ? getImportSectionLabel(pendingImportTarget.value.section) : '';
});

const selectionMetaByRecordId = computed<Record<string, Record<string, { order: number; accumulatedMagicCrystal: number }>>>(() => {
  const metaByRecordId: Record<string, Record<string, { order: number; accumulatedMagicCrystal: number }>> = {};

  for (const record of orderedRecords.value) {
    const itemMap = new Map(record.items.map(item => [item.id, item]));
    const selectedIds = selectedItemIdsByRecord.value[record.id] ?? [];
    const itemMeta: Record<string, { order: number; accumulatedMagicCrystal: number }> = {};
    let accumulatedMagicCrystal = 0;

    selectedIds.forEach((itemId, index) => {
      const item = itemMap.get(itemId);
      if (!item) {
        return;
      }

      accumulatedMagicCrystal += getTotalMagicCrystal(item);
      itemMeta[itemId] = {
        order: index + 1,
        accumulatedMagicCrystal,
      };
    });

    metaByRecordId[record.id] = itemMeta;
  }

  return metaByRecordId;
});

const recordTotalMagicCrystalByRecordId = computed<Record<string, number>>(() => {
  const totals: Record<string, number> = {};
  for (const record of orderedRecords.value) {
    totals[record.id] = record.items.reduce((sum, item) => sum + getTotalMagicCrystal(item), 0);
  }
  return totals;
});

const lastUnselectedItemIdByRecordId = computed<Record<string, string>>(() => {
  const result: Record<string, string> = {};

  for (const record of orderedRecords.value) {
    const selectedIdSet = new Set(getSelectionIds(record.id));
    for (let index = record.items.length - 1; index >= 0; index -= 1) {
      const itemId = record.items[index]?.id;
      if (itemId && !selectedIdSet.has(itemId)) {
        result[record.id] = itemId;
        break;
      }
    }
  }

  return result;
});

const autoSave = useAutoSave<FanxiuShouyuanExplorationSnapshot>({
  debounceMs: 800,
  equals: (left, right) => serializeSnapshot(left) === serializeSnapshot(right),
  storageKey: () => (canEdit.value ? buildDraftStorageKey() : null),
  save: async value => saveFanxiuShouyuanExplorationExchangeList(prepareSnapshot(value)),
  onError: (error: unknown) => {
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '自动保存兽渊探秘活动数据失败');
  },
});

const saveStatusText = computed(() => {
  if (autoSave.saveStatus.value === 'saving') return '保存中';
  if (autoSave.saveStatus.value === 'unsaved') return '未保存';
  return '已保存';
});

function createEmptySnapshot(): FanxiuShouyuanExplorationSnapshot {
  return { records: [] };
}

function buildDraftStorageKey() {
  const scope = userStore.user?.id ?? userStore.user?.username ?? 'fanxiu';
  return `fanxiu:shouyuan-exploration:${SHOUYUAN_EXPLORATION_DRAFT_SCHEMA_VERSION}:${scope}`;
}

function buildSelectionStorageKey() {
  const scope = userStore.user?.id ?? userStore.user?.username ?? 'fanxiu';
  return `${LOCAL_SELECTION_STORAGE_PREFIX}:${scope}`;
}

function buildEntityId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function buildRecordId(): string {
  return buildEntityId('shouyuan-exploration-record');
}

function buildRowId(): string {
  return buildEntityId('shouyuan-exploration-item');
}

function buildIncomeSpeedRowId(): string {
  return buildEntityId('shouyuan-exploration-income-speed');
}

function buildConsumptionEvaluationRowId(): string {
  return buildEntityId('shouyuan-exploration-consumption-evaluation');
}

function normalizeNonNegativeInt(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value ?? 0);
  if (!Number.isFinite(numeric)) {
    return 0;
  }
  return Math.max(0, Math.round(numeric));
}

function normalizeNonNegativeNumber(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value ?? 0);
  if (!Number.isFinite(numeric)) {
    return 0;
  }
  return Math.max(0, numeric);
}

function normalizeText(value: unknown): string {
  return String(value ?? '').trim();
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
  const text = normalizeText(value);
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

function normalizeActivityItem(item: Partial<FanxiuActivityItem> | null | undefined): FanxiuActivityItem {
  const { name, crossCount } = splitActivityNameAndCross(item?.name);
  return {
    id: normalizeText(item?.id) || buildEntityId('fanxiu-activity'),
    name,
    cross_count: crossCount ?? normalizeActivityCrossCount(
      (item as any)?.cross_count ?? (item as any)?.crossCount ?? (item as any)?.cross,
    ),
    start_date: normalizeText(item?.start_date),
    end_date: normalizeText(item?.end_date),
    note_id: normalizeText(item?.note_id) || null,
  };
}

function normalizeItem(
  item: Partial<FanxiuShouyuanExplorationExchangeItem> | null | undefined,
): FanxiuShouyuanExplorationExchangeItem {
  return {
    id: normalizeText(item?.id) || buildRowId(),
    name: normalizeText(item?.name),
    magic_crystal_cost: normalizeNonNegativeInt(item?.magic_crystal_cost),
    purchase_limit: normalizeNonNegativeInt(item?.purchase_limit),
    checked: false,
  };
}

function normalizePersonalRankingItem(
  item: Partial<FanxiuShouyuanExplorationPersonalRankingItem> | null | undefined,
): FanxiuShouyuanExplorationPersonalRankingItem {
  return {
    id: normalizeText(item?.id) || buildEntityId('shouyuan-exploration-personal-ranking'),
    rank: normalizeNonNegativeInt(item?.rank),
    name: normalizeText(item?.name),
    plane: normalizeText(item?.plane),
    merit: normalizeNonNegativeInt(item?.merit),
  };
}

function formatLocalIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function normalizeIsoDateText(value: unknown): string {
  const text = normalizeText(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    return text;
  }
  return formatLocalIsoDate(new Date());
}

function normalizeIncomeSpeedItem(
  item: Partial<FanxiuShouyuanExplorationIncomeSpeedItem> | null | undefined,
): FanxiuShouyuanExplorationIncomeSpeedItem {
  return {
    id: normalizeText(item?.id) || buildIncomeSpeedRowId(),
    captured_date: normalizeIsoDateText(item?.captured_date),
    search_count: normalizeNonNegativeInt(item?.search_count),
    beast_crystal: normalizeNonNegativeInt(item?.beast_crystal),
    score: normalizeNonNegativeInt(item?.score),
    merit: normalizeNonNegativeInt(item?.merit),
    remark: normalizeText(item?.remark),
  };
}

function normalizeConsumptionEvaluationItem(
  item: Partial<FanxiuShouyuanExplorationConsumptionEvaluationItem> | null | undefined,
): FanxiuShouyuanExplorationConsumptionEvaluationItem {
  return {
    id: normalizeText(item?.id) || buildConsumptionEvaluationRowId(),
    label: normalizeText(item?.label),
    current: normalizeNonNegativeNumber(item?.current),
    target: normalizeNonNegativeNumber(item?.target),
    speed: normalizeNonNegativeNumber(item?.speed),
  };
}

function createEmptyRecord(activityId: string): FanxiuShouyuanExplorationRecord {
  const activity = activityById.value[activityId];
  return {
    id: buildRecordId(),
    activity_id: activityId,
    label: normalizeText(activity?.name) || '兽渊探秘',
    personal_rankings: [],
    income_speeds: [],
    consumption_evaluations: [],
    items: [],
  };
}

function getActivityDateSortValue(activity: Pick<FanxiuActivityItem, 'start_date' | 'end_date'>) {
  const startTime = Date.parse(normalizeText(activity.start_date));
  const endTime = Date.parse(normalizeText(activity.end_date));
  return Math.max(
    Number.isFinite(startTime) ? startTime : 0,
    Number.isFinite(endTime) ? endTime : 0,
  );
}

function compareActivitiesByRecentDate(left: FanxiuActivityItem, right: FanxiuActivityItem) {
  const dateDiff = getActivityDateSortValue(right) - getActivityDateSortValue(left);
  if (dateDiff !== 0) {
    return dateDiff;
  }
  return normalizeText(right.id).localeCompare(normalizeText(left.id));
}

function ensureLatestEightCrossRecord() {
  const activity = latestEightCrossActivity.value;
  if (!activity) {
    return false;
  }

  if (snapshot.value.records.length === 0) {
    snapshot.value.records = [createEmptyRecord(activity.id)];
    return true;
  }

  if (snapshot.value.records.length !== 1) {
    return false;
  }

  const [record] = snapshot.value.records;
  const isUnboundDefaultRecord = !normalizeText(record.activity_id) && normalizeText(record.label) === DEFAULT_RECORD_LABEL;
  if (!isUnboundDefaultRecord) {
    return false;
  }

  record.activity_id = activity.id;
  record.label = normalizeText(activity.name) || DEFAULT_RECORD_LABEL;
  return true;
}

function normalizeActivityNameForMatch(value: unknown): string {
  return normalizeText(value).replace(/\s+/g, '');
}

function extractCrossCount(value: unknown): number | null {
  const match = normalizeActivityNameForMatch(value).match(/(?:^|\D)(64|32|16|8|4|2|1|0)跨/);
  return match ? normalizeActivityCrossCount(match[1]) : null;
}

function matchRecordToActivity(record: FanxiuShouyuanExplorationRecordInput | null | undefined, activity: FanxiuActivityItem): boolean {
  const explicitActivityId = normalizeText(record?.activity_id);
  if (explicitActivityId && explicitActivityId === activity.id) {
    return true;
  }

  const recordId = normalizeText(record?.id);
  if (recordId && recordId === activity.id) {
    return true;
  }

  const activityName = normalizeActivityNameForMatch(activity.name);
  const recordLabel = normalizeActivityNameForMatch(record?.label ?? record?.title);
  if (!recordLabel) {
    return false;
  }

  if (recordLabel === activityName || activityName.endsWith(recordLabel) || activityName.includes(recordLabel)) {
    return true;
  }

  const recordCrossCount = extractCrossCount(recordLabel);
  const activityCrossCount = normalizeActivityCrossCount(activity.cross_count);
  return Boolean(recordCrossCount && recordCrossCount === activityCrossCount);
}

function resolveRecordActivityId(record: FanxiuShouyuanExplorationRecordInput | null | undefined): string {
  const explicitActivityId = normalizeText(record?.activity_id);
  if (explicitActivityId && activityById.value[explicitActivityId]) {
    return explicitActivityId;
  }

  return shouyuanActivities.value.find(activity => matchRecordToActivity(record, activity))?.id || '';
}

function getPersonalRankingSortValue(rank: number) {
  return rank > 0 ? rank : Number.MAX_SAFE_INTEGER;
}

function sortPersonalRankings(items: FanxiuShouyuanExplorationPersonalRankingItem[]) {
  items.sort((left, right) => {
    const rankDiff = getPersonalRankingSortValue(left.rank) - getPersonalRankingSortValue(right.rank);
    if (rankDiff !== 0) {
      return rankDiff;
    }
    return left.id.localeCompare(right.id);
  });
  return items;
}

function normalizeGenericRecord(
  record: FanxiuShouyuanExplorationRecordInput | null | undefined,
  fallbackLabel: string,
  fallbackId: string,
): FanxiuShouyuanExplorationRecord {
  const personalRankings = Array.isArray(record?.personal_rankings) ? record.personal_rankings : [];
  const incomeSpeeds = Array.isArray(record?.income_speeds) ? record.income_speeds : [];
  const consumptionEvaluations = Array.isArray(record?.consumption_evaluations)
    ? record.consumption_evaluations
    : [];
  const items = Array.isArray(record?.items) ? record.items : [];
  const activityId = resolveRecordActivityId(record);
  const activity = activityById.value[activityId];

  return {
    id: normalizeText(record?.id) || fallbackId,
    activity_id: activityId,
    label: normalizeText(activity?.name) || normalizeText(record?.label ?? record?.title) || fallbackLabel,
    personal_rankings: sortPersonalRankings(personalRankings.map(normalizePersonalRankingItem)),
    income_speeds: incomeSpeeds.map(normalizeIncomeSpeedItem),
    consumption_evaluations: consumptionEvaluations.map(normalizeConsumptionEvaluationItem),
    items: items.map(normalizeItem),
  };
}

function normalizeSnapshot(
  value: FanxiuShouyuanExplorationSnapshotInput | null | undefined,
): FanxiuShouyuanExplorationSnapshot {
  let rawRecords: FanxiuShouyuanExplorationRecordInput[] = [];
  if (Array.isArray(value?.records)) {
    rawRecords = value.records;
  } else if (Array.isArray(value?.items)) {
    rawRecords = [
      {
        label: normalizeText(value?.label ?? value?.title) || DEFAULT_RECORD_LABEL,
        items: value.items,
      },
    ];
  }

  return {
    records: rawRecords
      .map((record, index) => normalizeGenericRecord(
        record,
        index === 0 ? DEFAULT_RECORD_LABEL : `record-${index + 1}`,
        normalizeText(record?.id) || buildRecordId(),
      ))
      .filter(record => Boolean(record.id)),
  };
}

function prepareSnapshot(value: FanxiuShouyuanExplorationSnapshotInput): FanxiuShouyuanExplorationSnapshot {
  const normalized = normalizeSnapshot(value);
  return {
    records: normalized.records.map(record => {
      const activity = activityById.value[record.activity_id];
      return {
        ...record,
        activity_id: normalizeText(record.activity_id),
        label: normalizeText(activity?.name) || normalizeText(record.label) || DEFAULT_RECORD_LABEL,
        personal_rankings: sortPersonalRankings(record.personal_rankings.map(normalizePersonalRankingItem)),
        income_speeds: record.income_speeds.map(normalizeIncomeSpeedItem),
        consumption_evaluations: record.consumption_evaluations.map(normalizeConsumptionEvaluationItem),
        items: record.items.map(normalizeItem),
      };
    }),
  };
}

function serializeSnapshot(value: FanxiuShouyuanExplorationSnapshot): string {
  return JSON.stringify(prepareSnapshot(value));
}

function createEmptyRow(): FanxiuShouyuanExplorationExchangeItem {
  return {
    id: buildRowId(),
    name: '',
    magic_crystal_cost: 0,
    purchase_limit: 0,
    checked: false,
  };
}

function createEmptyPersonalRankingRow(): FanxiuShouyuanExplorationPersonalRankingItem {
  return {
    id: buildEntityId('shouyuan-exploration-personal-ranking'),
    rank: 0,
    name: '',
    plane: '',
    merit: 0,
  };
}

function getRecordById(recordId: string | null | undefined) {
  const normalizedRecordId = normalizeText(recordId);
  if (!normalizedRecordId) {
    return null;
  }
  return orderedRecords.value.find(record => record.id === normalizedRecordId) || null;
}

function getRecordActivity(record: Pick<FanxiuShouyuanExplorationRecord, 'activity_id'> | null | undefined) {
  const activityId = normalizeText(record?.activity_id);
  return activityId ? activityById.value[activityId] || null : null;
}

function getRecordTitle(record: Pick<FanxiuShouyuanExplorationRecord, 'activity_id' | 'label'>) {
  const activity = getRecordActivity(record);
  if (activity) {
    return formatActivityDisplay(activity);
  }
  return normalizeText(record.label) || '未绑定活动';
}

function getImportSectionLabel(section: PendingImportSection) {
  if (section === 'personal-ranking') {
    return '个人榜';
  }
  return section === 'income-speed' ? '收益速度' : '兑换宝阁';
}

function formatActivityDateRange(activity: FanxiuActivityItem | null) {
  if (!activity) {
    return '-';
  }
  const startDate = normalizeText(activity.start_date);
  const endDate = normalizeText(activity.end_date);
  if (startDate && endDate) {
    return `${startDate} 至 ${endDate}`;
  }
  return startDate || endDate || '-';
}

function formatActivityDisplay(activity: FanxiuActivityItem | null | undefined) {
  if (!activity) {
    return '';
  }

  const activityName = formatActivityNameWithCross(activity);
  const dateRange = formatActivityDateRange(activity);
  if (activityName && dateRange !== '-') {
    return `${activityName} · ${dateRange}`;
  }
  return activityName || (dateRange !== '-' ? dateRange : '');
}

function formatActivityNameWithCross(activity: FanxiuActivityItem | null | undefined) {
  if (!activity) {
    return '';
  }
  const activityName = normalizeText(activity.name);
  const crossCount = normalizeActivityCrossCount(activity.cross_count);
  if (activityName && crossCount > 0) {
    return `${activityName} · ${getCrossCountLabel(crossCount)}`;
  }
  return activityName;
}

function normalizeRowText(row: FanxiuShouyuanExplorationExchangeItem) {
  row.name = normalizeText(row.name);
}

function normalizePersonalRankingRow(row: FanxiuShouyuanExplorationPersonalRankingItem) {
  row.rank = normalizeNonNegativeInt(row.rank);
  row.name = normalizeText(row.name);
  row.plane = normalizeText(row.plane);
  row.merit = normalizeNonNegativeInt(row.merit);
}

function getIncomeSpeedPerHundred(
  row: FanxiuShouyuanExplorationIncomeSpeedItem,
  field: 'beast_crystal' | 'score' | 'merit',
) {
  const searchCount = normalizeNonNegativeInt(row.search_count);
  if (!searchCount) {
    return 0;
  }
  return Math.floor((normalizeNonNegativeInt(row[field]) * 100) / searchCount);
}

function normalizeIncomeSpeedRemark(row: FanxiuShouyuanExplorationIncomeSpeedItem) {
  row.remark = normalizeText(row.remark);
}

function createEmptyConsumptionEvaluationRow(): FanxiuShouyuanExplorationConsumptionEvaluationItem {
  return {
    id: buildConsumptionEvaluationRowId(),
    label: '',
    current: 0,
    target: 0,
    speed: 0,
  };
}

function normalizeConsumptionEvaluationRow(row: FanxiuShouyuanExplorationConsumptionEvaluationItem) {
  row.label = normalizeText(row.label);
  row.current = normalizeNonNegativeNumber(row.current);
  row.target = normalizeNonNegativeNumber(row.target);
  row.speed = normalizeNonNegativeNumber(row.speed);
}

function formatConsumptionEvaluationValue(value: number) {
  const numeric = normalizeNonNegativeNumber(value);
  if (!numeric) {
    return '0';
  }
  return numeric
    .toFixed(2)
    .replace(/\.0+$/, '')
    .replace(/(\.\d*?)0+$/, '$1');
}

function getConsumptionEvaluationCost(row: FanxiuShouyuanExplorationConsumptionEvaluationItem) {
  const speed = normalizeNonNegativeNumber(row.speed);
  if (!speed) {
    return '-';
  }
  const remaining = Math.max(normalizeNonNegativeNumber(row.target) - normalizeNonNegativeNumber(row.current), 0);
  return formatConsumptionEvaluationValue(remaining / speed);
}

function getTotalMagicCrystal(row: FanxiuShouyuanExplorationExchangeItem) {
  return normalizeNonNegativeInt(row.magic_crystal_cost) * normalizeNonNegativeInt(row.purchase_limit);
}

function formatMagicCrystalWan(value: number) {
  return `${(normalizeNonNegativeInt(value) / 10000).toFixed(2)}万`;
}

function formatCompactSignificant(value: number, significantDigits = 4) {
  const numeric = Math.abs(value);
  if (!Number.isFinite(numeric) || numeric === 0) {
    return '0';
  }

  const integerDigits = Math.floor(Math.log10(numeric)) + 1;
  const fractionDigits = Math.max(0, significantDigits - integerDigits);
  return numeric
    .toFixed(fractionDigits)
    .replace(/\.0+$/, '')
    .replace(/(\.\d*?)0+$/, '$1');
}

function formatChineseCompactNumber(value: number) {
  const numeric = normalizeNonNegativeInt(value);
  if (!numeric) {
    return '0';
  }
  if (numeric >= 100000000) {
    return `${formatCompactSignificant(numeric / 100000000)}亿`;
  }
  if (numeric >= 10000) {
    return `${formatCompactSignificant(numeric / 10000)}万`;
  }
  return formatCompactSignificant(numeric);
}

function getSelectionIds(recordId: string) {
  return selectedItemIdsByRecord.value[recordId] ?? [];
}

function getSelectedCount(recordId: string) {
  return getSelectionIds(recordId).length;
}

function getLastSelectedItemId(recordId: string) {
  const selectedIds = getSelectionIds(recordId);
  return selectedIds[selectedIds.length - 1] || '';
}

function getLastUnselectedItemId(recordId: string) {
  return lastUnselectedItemIdByRecordId.value[recordId] ?? '';
}

function getItemSelectionOrder(recordId: string, itemId: string) {
  return selectionMetaByRecordId.value[recordId]?.[itemId]?.order ?? null;
}

function isHypotheticalConsumeCell(recordId: string, itemId: string) {
  return getLastUnselectedItemId(recordId) === itemId;
}

function formatConsumeMagicCrystalWan(recordId: string, itemId: string) {
  const accumulatedMagicCrystal = selectionMetaByRecordId.value[recordId]?.[itemId]?.accumulatedMagicCrystal;
  if (accumulatedMagicCrystal == null) {
    return isHypotheticalConsumeCell(recordId, itemId)
      ? formatMagicCrystalWan(recordTotalMagicCrystalByRecordId.value[recordId] ?? 0)
      : '';
  }
  return formatMagicCrystalWan(accumulatedMagicCrystal);
}

function normalizeSelectionIds(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const normalized: string[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    const itemId = normalizeText(item);
    if (!itemId || seen.has(itemId)) {
      continue;
    }
    seen.add(itemId);
    normalized.push(itemId);
  }
  return normalized;
}

function normalizeSelectionMap(value: unknown): Record<string, string[]> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {};
  }

  const normalized: Record<string, string[]> = {};
  for (const [recordId, selectedIds] of Object.entries(value)) {
    const normalizedRecordId = normalizeText(recordId);
    if (!normalizedRecordId) {
      continue;
    }
    const normalizedIds = normalizeSelectionIds(selectedIds);
    if (normalizedIds.length) {
      normalized[normalizedRecordId] = normalizedIds;
    }
  }
  return normalized;
}

function pruneSelectionMap(selectionMap: Record<string, string[]>): Record<string, string[]> {
  const pruned: Record<string, string[]> = {};
  for (const record of orderedRecords.value) {
    const selectedIds = selectionMap[record.id] ?? [];
    if (!selectedIds.length) {
      continue;
    }
    const itemIdSet = new Set(record.items.map(item => item.id));
    const validIds = selectedIds.filter(itemId => itemIdSet.has(itemId));
    if (validIds.length) {
      pruned[record.id] = validIds;
    }
  }
  return pruned;
}

function loadLocalSelectionMap(): Record<string, string[]> {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(buildSelectionStorageKey());
    if (!raw) {
      return {};
    }
    return normalizeSelectionMap(JSON.parse(raw));
  } catch {
    return {};
  }
}

function persistLocalSelectionMap() {
  if (typeof window === 'undefined') {
    return;
  }

  const pruned = pruneSelectionMap(selectedItemIdsByRecord.value);
  selectedItemIdsByRecord.value = pruned;

  try {
    if (Object.keys(pruned).length) {
      window.localStorage.setItem(buildSelectionStorageKey(), JSON.stringify(pruned));
    } else {
      window.localStorage.removeItem(buildSelectionStorageKey());
    }
  } catch {
    // 本地存储失败时静默降级，不影响页面主流程。
  }
}

function restoreLocalSelectionMap() {
  selectedItemIdsByRecord.value = pruneSelectionMap(loadLocalSelectionMap());
  persistLocalSelectionMap();
}

function isItemSelected(recordId: string, itemId: string) {
  return getSelectionIds(recordId).includes(itemId);
}

function setItemSelected(recordId: string, itemId: string, selected: boolean) {
  const selectedIds = getSelectionIds(recordId);
  const nextSelectedIds = selected
    ? (selectedIds.includes(itemId) ? selectedIds : [...selectedIds, itemId])
    : selectedIds.filter(id => id !== itemId);

  if (
    nextSelectedIds.length === selectedIds.length &&
    nextSelectedIds.every((id, index) => id === selectedIds[index])
  ) {
    return;
  }

  selectedItemIdsByRecord.value = {
    ...selectedItemIdsByRecord.value,
    [recordId]: nextSelectedIds,
  };
  persistLocalSelectionMap();
}

function syncPendingAddActivityId() {
  const validIds = new Set(unboundActivities.value.map(activity => activity.id));
  if (!validIds.has(pendingAddActivityId.value)) {
    pendingAddActivityId.value = unboundActivities.value[0]?.id ?? '';
  }
}

function addRecord() {
  if (!canEdit.value) {
    return;
  }

  const activityId = normalizeText(pendingAddActivityId.value) || unboundActivities.value[0]?.id || '';
  if (!activityId) {
    ElMessage.warning('活动列表里没有可新增的兽渊探秘活动');
    return;
  }

  snapshot.value.records = [createEmptyRecord(activityId), ...snapshot.value.records];
  syncPendingAddActivityId();
}

function removeRecord(recordId: string) {
  snapshot.value.records = snapshot.value.records.filter(record => record.id !== recordId);

  if (pendingImportTarget.value?.recordId === recordId) {
    pendingImportTarget.value = null;
  }

  if (recordId in selectedItemIdsByRecord.value) {
    const nextSelectionMap = { ...selectedItemIdsByRecord.value };
    delete nextSelectionMap[recordId];
    selectedItemIdsByRecord.value = nextSelectionMap;
    persistLocalSelectionMap();
  }
}

function getActivityOptionsForRecord(record: FanxiuShouyuanExplorationRecord) {
  return shouyuanActivities.value.filter(activity => {
    if (activity.id === record.activity_id) {
      return true;
    }
    return !orderedRecords.value.some(other => other.id !== record.id && other.activity_id === activity.id);
  });
}

function handleRecordActivityChange(record: FanxiuShouyuanExplorationRecord, nextActivityIdRaw: string) {
  const nextActivityId = normalizeText(nextActivityIdRaw);
  if (!nextActivityId || nextActivityId === record.activity_id) {
    return;
  }

  const duplicated = orderedRecords.value.some(other => other.id !== record.id && other.activity_id === nextActivityId);
  if (duplicated) {
    ElMessage.warning('这个活动已经绑定过了');
    return;
  }

  const activity = activityById.value[nextActivityId];
  record.activity_id = nextActivityId;
  record.label = normalizeText(activity?.name) || record.label;
}

function resolveTemplateElement(el: Element | ComponentPublicInstance | null): HTMLElement | null {
  if (el instanceof HTMLElement) {
    return el;
  }
  const componentElement = (el as ComponentPublicInstance | null)?.$el;
  return componentElement instanceof HTMLElement ? componentElement : null;
}

function destroyExchangeSortable(recordId: string) {
  const instance = exchangeSortableInstances.get(recordId);
  if (!instance) {
    return;
  }
  instance.destroy();
  exchangeSortableInstances.delete(recordId);
}

function destroyExchangeSortables() {
  for (const recordId of exchangeSortableInstances.keys()) {
    destroyExchangeSortable(recordId);
  }
}

function moveExchangeRow(recordId: string, oldIndex: number, newIndex: number) {
  if (!canEdit.value) {
    return;
  }

  const record = getRecordById(recordId);
  if (
    !record ||
    oldIndex < 0 ||
    newIndex < 0 ||
    oldIndex >= record.items.length ||
    newIndex >= record.items.length ||
    oldIndex === newIndex
  ) {
    return;
  }

  const [item] = record.items.splice(oldIndex, 1);
  if (!item) {
    return;
  }
  record.items.splice(newIndex, 0, item);
}

function refreshExchangeSortables() {
  const activeRecordIds = new Set(orderedRecords.value.map(record => record.id));
  for (const recordId of exchangeSortableInstances.keys()) {
    if (!activeRecordIds.has(recordId)) {
      destroyExchangeSortable(recordId);
    }
  }

  if (!canEdit.value) {
    destroyExchangeSortables();
    return;
  }

  for (const record of orderedRecords.value) {
    destroyExchangeSortable(record.id);
    if (record.items.length <= 1) {
      continue;
    }

    const el = exchangeTableBodyRefs.get(record.id);
    if (!el) {
      continue;
    }

    exchangeSortableInstances.set(record.id, Sortable.create(el, {
      animation: 150,
      draggable: 'tr.exchange-row',
      ghostClass: 'exchange-sortable-ghost',
      handle: '.sortable-order-handle',
      onEnd: ({ oldIndex, newIndex }) => {
        if (oldIndex == null || newIndex == null) {
          return;
        }
        moveExchangeRow(record.id, oldIndex, newIndex);
      },
    }));
  }
}

function queueRefreshExchangeSortables() {
  const token = ++exchangeSortableRefreshToken;
  void nextTick(() => {
    if (token === exchangeSortableRefreshToken) {
      refreshExchangeSortables();
    }
  });
}

function setExchangeTableBodyRef(recordId: string, el: Element | ComponentPublicInstance | null) {
  const element = resolveTemplateElement(el);
  if (!element) {
    exchangeTableBodyRefs.delete(recordId);
    destroyExchangeSortable(recordId);
    return;
  }

  if (exchangeTableBodyRefs.get(recordId) === element) {
    return;
  }

  exchangeTableBodyRefs.set(recordId, element);
  queueRefreshExchangeSortables();
}

function addRow(record: FanxiuShouyuanExplorationRecord) {
  record.items.push(createEmptyRow());
}

function addPersonalRankingRow(record: FanxiuShouyuanExplorationRecord) {
  record.personal_rankings.push(createEmptyPersonalRankingRow());
  sortPersonalRankings(record.personal_rankings);
}

function removeRow(recordId: string, rowId: string) {
  const record = getRecordById(recordId);
  if (!record) return;

  const rowIndex = record.items.findIndex(item => item.id === rowId);
  if (rowIndex >= 0) {
    record.items.splice(rowIndex, 1);
  }

  const selectedIds = getSelectionIds(recordId);
  const nextSelectedIds = selectedIds.filter(id => id !== rowId);
  if (nextSelectedIds.length !== selectedIds.length) {
    selectedItemIdsByRecord.value = {
      ...selectedItemIdsByRecord.value,
      [recordId]: nextSelectedIds,
    };
    persistLocalSelectionMap();
  }
}

function removePersonalRankingRow(recordId: string, rowId: string) {
  const record = getRecordById(recordId);
  if (!record) return;

  const rowIndex = record.personal_rankings.findIndex(item => item.id === rowId);
  if (rowIndex >= 0) {
    record.personal_rankings.splice(rowIndex, 1);
  }
}

function removeIncomeSpeedRow(recordId: string, rowId: string) {
  const record = getRecordById(recordId);
  if (!record) return;

  const rowIndex = record.income_speeds.findIndex(item => item.id === rowId);
  if (rowIndex >= 0) {
    record.income_speeds.splice(rowIndex, 1);
  }
}

function addConsumptionEvaluationRow(record: FanxiuShouyuanExplorationRecord) {
  record.consumption_evaluations.push(createEmptyConsumptionEvaluationRow());
}

function removeConsumptionEvaluationRow(recordId: string, rowId: string) {
  const record = getRecordById(recordId);
  if (!record) return;

  const rowIndex = record.consumption_evaluations.findIndex(item => item.id === rowId);
  if (rowIndex >= 0) {
    record.consumption_evaluations.splice(rowIndex, 1);
  }
}

function handlePersonalRankingRankChange(record: FanxiuShouyuanExplorationRecord, row: FanxiuShouyuanExplorationPersonalRankingItem) {
  normalizePersonalRankingRow(row);
  sortPersonalRankings(record.personal_rankings);
}

function buildMergeKey(item: Pick<FanxiuShouyuanExplorationExchangeItem, 'name' | 'magic_crystal_cost' | 'purchase_limit'>) {
  return [
    normalizeText(item.name),
    normalizeNonNegativeInt(item.magic_crystal_cost),
    normalizeNonNegativeInt(item.purchase_limit),
  ].join('::');
}

function applyImportedItems(recordId: string, response: FanxiuShouyuanExplorationOcrImportResponse) {
  const record = getRecordById(recordId);
  if (!record) {
    return { insertedCount: 0, skippedCount: response.items?.length || 0 };
  }

  const existingKeys = new Set(record.items.map(buildMergeKey));
  let insertedCount = 0;
  let skippedCount = 0;

  for (const rawItem of response.items || []) {
    const item = normalizeItem(rawItem);
    const mergeKey = buildMergeKey(item);
    if (!item.name || existingKeys.has(mergeKey)) {
      skippedCount += 1;
      continue;
    }
    existingKeys.add(mergeKey);
    record.items.push(item);
    insertedCount += 1;
  }

  return { insertedCount, skippedCount };
}

function applyImportedPersonalRankings(recordId: string, response: FanxiuShouyuanExplorationPersonalRankingOcrImportResponse) {
  const record = getRecordById(recordId);
  if (!record) {
    return { insertedCount: 0, updatedCount: 0, skippedCount: response.items?.length || 0 };
  }

  let insertedCount = 0;
  let updatedCount = 0;
  let skippedCount = 0;

  for (const rawItem of response.items || []) {
    const item = normalizePersonalRankingItem(rawItem);
    if (!item.rank || !item.name) {
      skippedCount += 1;
      continue;
    }

    const existingIndex = record.personal_rankings.findIndex(entry => entry.rank === item.rank);
    if (existingIndex >= 0) {
      const existing = record.personal_rankings[existingIndex];
      record.personal_rankings.splice(existingIndex, 1, {
        ...item,
        id: existing.id || item.id,
      });
      updatedCount += 1;
      continue;
    }

    record.personal_rankings.push(item);
    insertedCount += 1;
  }

  sortPersonalRankings(record.personal_rankings);
  return { insertedCount, updatedCount, skippedCount };
}

function buildIncomeSpeedMergeKey(item: FanxiuShouyuanExplorationIncomeSpeedItem) {
  return [
    normalizeText(item.captured_date),
    normalizeNonNegativeInt(item.search_count),
    normalizeNonNegativeInt(item.beast_crystal),
    normalizeNonNegativeInt(item.score),
    normalizeNonNegativeInt(item.merit),
  ].join('::');
}

function applyImportedIncomeSpeed(recordId: string, response: FanxiuShouyuanExplorationIncomeSpeedOcrImportResponse) {
  const record = getRecordById(recordId);
  if (!record || !response.item) {
    return { insertedCount: 0, skippedCount: response.item ? 0 : 1 };
  }

  const item = normalizeIncomeSpeedItem(response.item);
  if (!item.search_count) {
    return { insertedCount: 0, skippedCount: 1 };
  }

  const existingKeys = new Set(record.income_speeds.map(buildIncomeSpeedMergeKey));
  if (existingKeys.has(buildIncomeSpeedMergeKey(item))) {
    return { insertedCount: 0, skippedCount: 1 };
  }

  record.income_speeds.unshift(item);
  return { insertedCount: 1, skippedCount: 0 };
}

async function importImage(target: PendingImportTarget, image: File) {
  importing.value = true;
  try {
    if (target.section === 'income-speed') {
      const response = await importFanxiuShouyuanExplorationIncomeSpeedFromOcr(image);
      const { insertedCount } = applyImportedIncomeSpeed(target.recordId, response);

      if (!insertedCount) {
        ElMessage.warning('这条收益速度记录已经导入过了');
        return;
      }

      ElMessage.success('新增 1 条收益速度记录，可继续粘贴');
      return;
    }

    if (target.section === 'exchange') {
      const response = await importFanxiuShouyuanExplorationExchangeListFromOcr(image);
      const { insertedCount, skippedCount } = applyImportedItems(target.recordId, response);

      if (!insertedCount) {
        ElMessage.warning(skippedCount ? '识别到了条目，但当前活动的兑换宝阁里已经都有了' : '截图里没有可导入的新条目');
        return;
      }

      const summaryParts = [`新增 ${insertedCount} 条`];
      if (skippedCount) {
        summaryParts.push(`跳过 ${skippedCount} 条重复项`);
      }
      ElMessage.success(`${summaryParts.join('，')}，可继续粘贴`);
      return;
    }

    const response = await importFanxiuShouyuanExplorationPersonalRankingsFromOcr(image);
    const { insertedCount, updatedCount, skippedCount } = applyImportedPersonalRankings(target.recordId, response);

    if (!insertedCount && !updatedCount) {
      ElMessage.warning(skippedCount ? '识别到了名次，但没有可导入的有效排名' : '截图里没有可导入的个人榜名次');
      return;
    }

    const summaryParts: string[] = [];
    if (insertedCount) {
      summaryParts.push(`新增 ${insertedCount} 条`);
    }
    if (updatedCount) {
      summaryParts.push(`覆盖 ${updatedCount} 条`);
    }
    if (skippedCount) {
      summaryParts.push(`跳过 ${skippedCount} 条无效项`);
    }
    ElMessage.success(`${summaryParts.join('，')}，可继续粘贴`);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '截图导入失败');
  } finally {
    importing.value = false;
  }
}

function isPendingImport(recordId: string, section: PendingImportSection) {
  return pendingImportTarget.value?.recordId === recordId && pendingImportTarget.value?.section === section;
}

function toggleImport(recordId: string, section: PendingImportSection) {
  if (!canEdit.value) return;

  const isCurrentTarget = isPendingImport(recordId, section);
  pendingImportTarget.value = isCurrentTarget ? null : { recordId, section };
  if (pendingImportTarget.value) {
    const record = getRecordById(recordId);
    ElMessage.info(
      `已准备导入 ${record ? getRecordTitle(record) : currentImportRecordLabel.value} 的${getImportSectionLabel(section)}截图，请直接粘贴`,
    );
  }
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

async function handleWindowPaste(event: ClipboardEvent) {
  const target = pendingImportTarget.value;
  if (!target || importing.value) {
    return;
  }

  const image = extractClipboardImage(event);
  if (!image) {
    return;
  }

  event.preventDefault();
  event.stopPropagation();
  await importImage(target, image);
}

async function loadSnapshot() {
  pageHydrated.value = false;
  loading.value = true;
  try {
    const [remoteSnapshot, activitySnapshot] = await Promise.all([
      getFanxiuShouyuanExplorationExchangeList(),
      getFanxiuActivityList(),
    ]);

    activityItems.value = Array.isArray(activitySnapshot?.items)
      ? activitySnapshot.items.map(normalizeActivityItem)
      : [];

    const normalizedRemoteSnapshot = normalizeSnapshot(remoteSnapshot);
    const { snapshot: restoredSnapshot, restored } = autoSave.loadSnapshot(normalizedRemoteSnapshot, { draftStrategy: 'discard' });
    snapshot.value = prepareSnapshot((restoredSnapshot ?? normalizedRemoteSnapshot) as FanxiuShouyuanExplorationSnapshotInput);
    const autoBound = ensureLatestEightCrossRecord();
    restoreLocalSelectionMap();
    syncPendingAddActivityId();
    pageHydrated.value = true;

    if (restored || autoBound) {
      autoSave.markDirty(snapshot.value);
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取兽渊探秘活动数据失败');
  } finally {
    loading.value = false;
  }
}

watch(unboundActivities, () => {
  syncPendingAddActivityId();
}, { immediate: true });

watch(
  () => snapshot.value.records,
  () => {
    if (!pageHydrated.value) {
      return;
    }

    if (pendingImportTarget.value?.recordId && !getRecordById(pendingImportTarget.value.recordId)) {
      pendingImportTarget.value = null;
    }

    persistLocalSelectionMap();
    autoSave.markDirty(snapshot.value);
  },
  { deep: true },
);

watch(
  () => [
    canEdit.value,
    orderedRecords.value.map(record => `${record.id}:${record.items.length}`).join('|'),
  ] as const,
  () => queueRefreshExchangeSortables(),
  { immediate: true },
);

onMounted(() => {
  window.addEventListener('paste', handleWindowPaste);
  void loadSnapshot();
});

onBeforeUnmount(() => {
  window.removeEventListener('paste', handleWindowPaste);
  destroyExchangeSortables();
});
</script>

<template>
  <div class="shouyuan-exploration-page" v-loading="loading">
    <div class="page-header">
      <h2 class="page-title">活动列表 · 兽渊探秘</h2>
      <div class="page-header__meta">
        <span class="save-status">{{ saveStatusText }}</span>
      </div>
    </div>

    <el-card shadow="never" class="table-card">
      <div v-if="canEdit && shouyuanActivities.length" class="page-actions">
        <el-select
          v-model="pendingAddActivityId"
          filterable
          class="page-actions__select"
          placeholder="选择要新增的兽渊探秘活动"
          no-data-text="暂无可新增的兽渊探秘活动"
        >
          <el-option
            v-for="activity in unboundActivities"
            :key="activity.id"
            :label="formatActivityDisplay(activity)"
            :value="activity.id"
          />
        </el-select>

        <el-button type="primary" :icon="Plus" :disabled="!pendingAddActivityId" @click="addRecord">
          新增活动
        </el-button>
      </div>

      <div v-if="orderedRecords.length" class="record-list">
        <section
          v-for="record in orderedRecords"
          :key="record.id"
          class="record-block"
        >
          <div class="record-header">
            <div class="record-header__title">
              <h3 class="record-title">{{ getRecordTitle(record) }}</h3>
              <span v-if="pendingImportTarget?.recordId === record.id" class="import-hint">
                当前已开启 {{ currentImportSectionLabel }} 粘贴导入
              </span>
            </div>

            <el-button
              v-if="canEdit"
              type="danger"
              link
              :icon="Delete"
              @click="removeRecord(record.id)"
            >
              删除活动
            </el-button>
          </div>

          <div class="activity-meta">
            <div class="field-row">
              <span class="field-label">活动</span>
              <el-select
                v-if="canEdit"
                :model-value="record.activity_id"
                filterable
                placeholder="选择活动列表中的兽渊探秘活动"
                no-data-text="暂无可选的兽渊探秘活动"
                @change="value => handleRecordActivityChange(record, String(value ?? ''))"
              >
                <el-option
                  v-for="activity in getActivityOptionsForRecord(record)"
                  :key="activity.id"
                  :label="formatActivityDisplay(activity)"
                  :value="activity.id"
                />
              </el-select>
              <span v-else class="field-value">{{ getRecordTitle(record) }}</span>
            </div>
          </div>

          <section class="section-block">
            <div class="section-header">
              <div class="section-header__title">
                <h4 class="section-title">个人榜</h4>
              </div>
              <div class="section-header__actions">
                <el-button
                  v-if="canEdit"
                  type="primary"
                  plain
                  :loading="importing && isPendingImport(record.id, 'personal-ranking')"
                  @click="toggleImport(record.id, 'personal-ranking')"
                >
                  {{
                    importing && isPendingImport(record.id, 'personal-ranking')
                      ? '识别中...'
                      : isPendingImport(record.id, 'personal-ranking')
                        ? '关闭粘贴导入'
                        : '粘贴截图导入'
                  }}
                </el-button>
                <el-button v-if="canEdit" type="primary" :icon="Plus" @click="addPersonalRankingRow(record)">
                  新增名次
                </el-button>
              </div>
            </div>

            <div class="personal-ranking-table-shell">
              <table class="personal-ranking-table">
                <thead>
                  <tr>
                    <th class="col-rank">名次</th>
                    <th class="col-player-name">姓名</th>
                    <th class="col-plane">位面</th>
                    <th class="col-merit">探秘积分</th>
                    <th v-if="canEdit" class="col-actions">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!record.personal_rankings.length">
                    <td :colspan="canEdit ? 5 : 4" class="exchange-empty">暂无名次</td>
                  </tr>
                  <tr v-for="row in record.personal_rankings" :key="row.id">
                    <td class="col-rank number-column">
                      <el-input-number
                        v-if="canEdit"
                        v-model="row.rank"
                        size="small"
                        :min="0"
                        :step="1"
                        :controls="false"
                        @change="() => handlePersonalRankingRankChange(record, row)"
                      />
                      <span v-else>{{ row.rank || '-' }}</span>
                    </td>
                    <td class="col-player-name">
                      <el-input
                        v-if="canEdit"
                        v-model="row.name"
                        size="small"
                        placeholder="姓名"
                        @blur="normalizePersonalRankingRow(row)"
                      />
                      <span v-else>{{ row.name || '-' }}</span>
                    </td>
                    <td class="col-plane">
                      <el-input
                        v-if="canEdit"
                        v-model="row.plane"
                        size="small"
                        placeholder="位面"
                        @blur="normalizePersonalRankingRow(row)"
                      />
                      <span v-else>{{ row.plane || '-' }}</span>
                    </td>
                    <td class="col-merit merit-column">
                      <div v-if="canEdit" class="personal-merit-cell">
                        <el-input-number
                          v-model="row.merit"
                          size="small"
                          :min="0"
                          :step="1"
                          :controls="false"
                          @change="() => normalizePersonalRankingRow(row)"
                        />
                        <span v-if="row.merit" class="personal-merit-preview">
                          {{ formatChineseCompactNumber(row.merit) }}
                        </span>
                      </div>
                      <span v-else>{{ row.merit ? formatChineseCompactNumber(row.merit) : '-' }}</span>
                    </td>
                    <td v-if="canEdit" class="col-actions">
                      <el-button
                        type="danger"
                        link
                        :icon="Delete"
                        @click="removePersonalRankingRow(record.id, row.id)"
                      >
                        删除
                      </el-button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="section-block">
            <div class="section-header">
              <div class="section-header__title">
                <h4 class="section-title">团队榜</h4>
              </div>
            </div>
            <div class="section-empty">功能待定</div>
          </section>

          <section class="section-block">
            <div class="section-header">
              <div class="section-header__title">
                <h4 class="section-title">收益速度</h4>
              </div>

              <div class="section-header__actions">
                <el-button
                  v-if="canEdit"
                  type="primary"
                  plain
                  :loading="importing && isPendingImport(record.id, 'income-speed')"
                  @click="toggleImport(record.id, 'income-speed')"
                >
                  {{
                    importing && isPendingImport(record.id, 'income-speed')
                      ? '识别中...'
                      : isPendingImport(record.id, 'income-speed')
                        ? '关闭粘贴导入'
                        : '粘贴截图导入'
                  }}
                </el-button>
              </div>
            </div>

            <div class="income-speed-table-shell">
              <table class="income-speed-table">
                <thead>
                  <tr>
                    <th class="col-date">识别日期</th>
                    <th class="col-search-count">探查次数</th>
                    <th class="col-income">每百兽晶</th>
                    <th class="col-income">每百积分</th>
                    <th class="col-income">每百功勋</th>
                    <th class="col-remark">备注</th>
                    <th v-if="canEdit" class="col-actions">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!record.income_speeds.length">
                    <td :colspan="canEdit ? 7 : 6" class="exchange-empty">暂无记录</td>
                  </tr>
                  <tr v-for="row in record.income_speeds" :key="row.id">
                    <td class="col-date">{{ row.captured_date || '-' }}</td>
                    <td class="col-search-count">{{ row.search_count || '-' }}</td>
                    <td class="col-income">{{ getIncomeSpeedPerHundred(row, 'beast_crystal') }}</td>
                    <td class="col-income">{{ getIncomeSpeedPerHundred(row, 'score') }}</td>
                    <td class="col-income">{{ getIncomeSpeedPerHundred(row, 'merit') }}</td>
                    <td class="col-remark">
                      <el-input
                        v-if="canEdit"
                        v-model="row.remark"
                        size="small"
                        placeholder="备注"
                        @blur="normalizeIncomeSpeedRemark(row)"
                      />
                      <span v-else>{{ row.remark || '-' }}</span>
                    </td>
                    <td v-if="canEdit" class="col-actions">
                      <el-button
                        type="danger"
                        link
                        :icon="Delete"
                        @click="removeIncomeSpeedRow(record.id, row.id)"
                      >
                        删除
                      </el-button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="section-block">
            <div class="section-header">
              <div class="section-header__title">
                <h4 class="section-title">消耗评估</h4>
              </div>

              <div class="section-header__actions">
                <el-button v-if="canEdit" type="primary" :icon="Plus" @click="addConsumptionEvaluationRow(record)">
                  新增评估
                </el-button>
              </div>
            </div>

            <div class="consumption-evaluation-table-shell">
              <table class="consumption-evaluation-table">
                <thead>
                  <tr>
                    <th class="col-label">标签</th>
                    <th class="col-number">当前</th>
                    <th class="col-number">目标</th>
                    <th class="col-number">速度</th>
                    <th class="col-cost">消耗</th>
                    <th v-if="canEdit" class="col-actions">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!record.consumption_evaluations.length">
                    <td :colspan="canEdit ? 6 : 5" class="exchange-empty">暂无评估</td>
                  </tr>
                  <tr v-for="row in record.consumption_evaluations" :key="row.id">
                    <td class="col-label">
                      <el-input
                        v-if="canEdit"
                        v-model="row.label"
                        size="small"
                        placeholder="标签"
                        @blur="normalizeConsumptionEvaluationRow(row)"
                      />
                      <span v-else>{{ row.label || '-' }}</span>
                    </td>
                    <td class="col-number number-column">
                      <el-input-number
                        v-if="canEdit"
                        v-model="row.current"
                        size="small"
                        :min="0"
                        :step="1"
                        :controls="false"
                        @change="() => normalizeConsumptionEvaluationRow(row)"
                      />
                      <span v-else>{{ formatConsumptionEvaluationValue(row.current) }}</span>
                    </td>
                    <td class="col-number number-column">
                      <el-input-number
                        v-if="canEdit"
                        v-model="row.target"
                        size="small"
                        :min="0"
                        :step="1"
                        :controls="false"
                        @change="() => normalizeConsumptionEvaluationRow(row)"
                      />
                      <span v-else>{{ formatConsumptionEvaluationValue(row.target) }}</span>
                    </td>
                    <td class="col-number number-column">
                      <el-input-number
                        v-if="canEdit"
                        v-model="row.speed"
                        size="small"
                        :min="0"
                        :step="1"
                        :controls="false"
                        @change="() => normalizeConsumptionEvaluationRow(row)"
                      />
                      <span v-else>{{ formatConsumptionEvaluationValue(row.speed) }}</span>
                    </td>
                    <td class="col-cost">{{ getConsumptionEvaluationCost(row) }}</td>
                    <td v-if="canEdit" class="col-actions">
                      <el-button
                        type="danger"
                        link
                        :icon="Delete"
                        @click="removeConsumptionEvaluationRow(record.id, row.id)"
                      >
                        删除
                      </el-button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="section-block">
            <div class="section-header">
              <div class="section-header__title">
                <h4 class="section-title">兑换宝阁</h4>
              </div>

              <div class="section-header__actions">
                <el-button
                  v-if="canEdit"
                  type="primary"
                  plain
                  :loading="importing && isPendingImport(record.id, 'exchange')"
                  @click="toggleImport(record.id, 'exchange')"
                >
                  {{
                    importing && isPendingImport(record.id, 'exchange')
                      ? '识别中...'
                      : isPendingImport(record.id, 'exchange')
                        ? '关闭粘贴导入'
                        : '粘贴截图导入'
                  }}
                </el-button>
                <el-button v-if="canEdit" type="primary" :icon="Plus" @click="addRow(record)">
                  新增条目
                </el-button>
              </div>
            </div>

            <div class="exchange-table-shell">
              <table class="exchange-table">
                <thead>
                  <tr>
                    <th class="col-index">编号</th>
                    <th class="col-selected">选中</th>
                    <th class="col-name">名称</th>
                    <th class="col-number">所需兽渊代币</th>
                    <th class="col-number">限购数量</th>
                    <th class="col-total">总兽渊代币</th>
                    <th class="col-consume">消耗兽渊代币</th>
                    <th v-if="canEdit" class="col-actions">操作</th>
                  </tr>
                </thead>
                <tbody :ref="el => setExchangeTableBodyRef(record.id, el)">
                  <tr v-if="!record.items.length">
                    <td :colspan="canEdit ? 8 : 7" class="exchange-empty">暂无条目</td>
                  </tr>
                  <tr
                    v-for="(row, index) in record.items"
                    :key="row.id"
                    :class="{ 'exchange-row': true, 'is-last-selected-row': getLastSelectedItemId(record.id) === row.id }"
                  >
                    <td class="col-index">
                      <SortableOrderHandle
                        v-if="canEdit"
                        :index="index"
                        :total="record.items.length"
                        size="sm"
                      />
                      <span v-else>{{ index + 1 }}</span>
                    </td>
                    <td class="col-selected">
                      <div class="selection-cell">
                        <el-checkbox
                          :model-value="isItemSelected(record.id, row.id)"
                          :disabled="!canEdit"
                          @change="value => setItemSelected(record.id, row.id, Boolean(value))"
                        />
                        <span v-if="getItemSelectionOrder(record.id, row.id)" class="selection-order">
                          {{ getItemSelectionOrder(record.id, row.id) }}
                        </span>
                      </div>
                    </td>
                    <td class="col-name">
                      <el-input
                        v-if="canEdit"
                        v-model="row.name"
                        size="small"
                        placeholder="名称"
                        @blur="normalizeRowText(row)"
                      />
                      <span v-else>{{ row.name || '-' }}</span>
                    </td>
                    <td class="col-number number-column">
                      <el-input-number
                        v-if="canEdit"
                        v-model="row.magic_crystal_cost"
                        size="small"
                        :min="0"
                        :step="1"
                        controls-position="right"
                      />
                      <span v-else>{{ row.magic_crystal_cost }}</span>
                    </td>
                    <td class="col-number number-column">
                      <el-input-number
                        v-if="canEdit"
                        v-model="row.purchase_limit"
                        size="small"
                        :min="0"
                        :step="1"
                        controls-position="right"
                      />
                      <span v-else>{{ row.purchase_limit }}</span>
                    </td>
                    <td class="col-total">{{ formatMagicCrystalWan(getTotalMagicCrystal(row)) }}</td>
                    <td
                      class="col-consume"
                      :class="{ 'is-hypothetical-consume': isHypotheticalConsumeCell(record.id, row.id) }"
                    >
                      {{ formatConsumeMagicCrystalWan(record.id, row.id) }}
                    </td>
                    <td v-if="canEdit" class="col-actions">
                      <el-button
                        type="danger"
                        link
                        :icon="Delete"
                        @click="removeRow(record.id, row.id)"
                      >
                        删除
                      </el-button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

          </section>
        </section>
      </div>

      <div v-else class="empty-state">
        <div class="empty-state__title">
          {{ shouyuanActivities.length ? '暂无已添加活动' : '活动列表里暂无兽渊探秘活动' }}
        </div>
        <div class="empty-state__description">
          {{
            shouyuanActivities.length
              ? '从上方选择一个兽渊探秘活动后新增，它会作为新的活动文档出现在最上面。'
              : '先到“活动列表”里配置兽渊探秘活动，这里才能绑定新增。'
          }}
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.shouyuan-exploration-page {
  --shouyuan-number-control-width: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
}

.page-header,
.page-actions,
.record-header,
.record-header__title,
.section-header,
.section-header__actions,
.page-header__meta {
  display: flex;
  align-items: center;
  gap: 12px;
}

.page-header,
.record-header,
.section-header {
  justify-content: space-between;
  flex-wrap: wrap;
}

.page-title {
  margin: 0;
  font-size: 32px;
  font-weight: 700;
  color: #111827;
}

.save-status,
.import-hint,
.field-label {
  font-size: 13px;
  color: #6b7280;
}

.table-card :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.page-actions__select {
  width: min(360px, 100%);
}

.record-list {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.record-block {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.record-block + .record-block {
  border-top: 1px solid #ebeef5;
  padding-top: 24px;
}

.record-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #111827;
}

.activity-meta {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.field-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
}

.field-value {
  min-height: 32px;
  font-size: 14px;
  color: #111827;
}

.section-block {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-header__title {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.section-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: #111827;
}

.section-empty {
  padding: 16px;
  border: 1px dashed #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  color: #6b7280;
  background: #fafafa;
}

.personal-ranking-table :deep(.el-input),
.personal-ranking-table :deep(.el-input-number),
.income-speed-table :deep(.el-input),
.consumption-evaluation-table :deep(.el-input),
.consumption-evaluation-table :deep(.el-input-number),
.exchange-table :deep(.el-input),
.exchange-table :deep(.el-input-number),
.field-row :deep(.el-input),
.field-row :deep(.el-select) {
  width: 100%;
}

.personal-ranking-table-shell,
.income-speed-table-shell,
.consumption-evaluation-table-shell,
.exchange-table-shell {
  width: 100%;
  overflow-x: auto;
}

.personal-ranking-table,
.income-speed-table,
.consumption-evaluation-table,
.exchange-table {
  width: fit-content;
  min-width: 0;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 14px;
  color: #374151;
  background: #fff;
}

.personal-ranking-table th,
.personal-ranking-table td,
.income-speed-table th,
.income-speed-table td,
.consumption-evaluation-table th,
.consumption-evaluation-table td,
.exchange-table th,
.exchange-table td {
  padding: 8px;
  border: 1px solid #ebeef5;
  vertical-align: middle;
  background: #fff;
}

.personal-ranking-table th,
.income-speed-table th,
.consumption-evaluation-table th,
.exchange-table th {
  font-weight: 600;
  color: #6b7280;
  text-align: center;
  white-space: nowrap;
}

.personal-ranking-table tbody tr:nth-child(even) td,
.income-speed-table tbody tr:nth-child(even) td,
.consumption-evaluation-table tbody tr:nth-child(even) td,
.exchange-table tbody tr:nth-child(even) td {
  background: #fafcff;
}

.exchange-table tbody tr.exchange-row {
  transition: background-color 0.15s ease;
}

.exchange-table tbody tr.exchange-sortable-ghost td {
  background: #eff6ff !important;
}

.exchange-table .sortable-order-handle {
  margin: 0 auto;
}

.personal-ranking-table .col-rank {
  width: 56px;
  min-width: 56px;
  text-align: center;
}

.personal-ranking-table .col-player-name {
  width: 180px;
  min-width: 180px;
}

.personal-ranking-table .col-plane {
  width: 150px;
  min-width: 150px;
}

.personal-ranking-table .col-merit {
  width: 148px;
  min-width: 148px;
  text-align: right;
}

.personal-ranking-table .col-actions {
  width: 88px;
  min-width: 88px;
  text-align: center;
}

.income-speed-table .col-date {
  width: 112px;
  min-width: 112px;
  text-align: center;
}

.income-speed-table .col-search-count {
  width: 84px;
  min-width: 84px;
  text-align: center;
}

.income-speed-table .col-income {
  width: 96px;
  min-width: 96px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.income-speed-table .col-remark {
  width: 220px;
  min-width: 220px;
}

.income-speed-table .col-actions {
  width: 88px;
  min-width: 88px;
  text-align: center;
}

.consumption-evaluation-table .col-label {
  width: 180px;
  min-width: 180px;
}

.consumption-evaluation-table .col-number {
  width: 112px;
  min-width: 112px;
  text-align: right;
}

.consumption-evaluation-table .col-cost {
  width: 96px;
  min-width: 96px;
  text-align: right;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.consumption-evaluation-table .col-actions {
  width: 88px;
  min-width: 88px;
  text-align: center;
}

.consumption-evaluation-table td.col-label :deep(.el-input__wrapper),
.consumption-evaluation-table td.col-number :deep(.el-input-number),
.consumption-evaluation-table td.col-number :deep(.el-input-number .el-input__wrapper) {
  width: 100%;
}

.consumption-evaluation-table :deep(.number-column .el-input-number) {
  display: flex;
  width: 100% !important;
  min-width: 0;
}

.consumption-evaluation-table :deep(.number-column .el-input-number .el-input) {
  flex: 1 1 auto;
  width: 0;
  min-width: 0;
  height: 100%;
}

.consumption-evaluation-table :deep(.number-column .el-input-number .el-input__wrapper) {
  width: 100%;
}

.consumption-evaluation-table :deep(.number-column .el-input-number.is-controls-right .el-input__wrapper) {
  padding-right: calc(var(--shouyuan-number-control-width) + 6px);
}

.consumption-evaluation-table :deep(.number-column .el-input-number .el-input__inner) {
  padding: 0;
  text-align: right;
}

.consumption-evaluation-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__decrease),
.consumption-evaluation-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__increase) {
  width: var(--shouyuan-number-control-width);
  right: 0;
}

.personal-ranking-table td.col-player-name :deep(.el-input__wrapper),
.personal-ranking-table td.col-plane :deep(.el-input__wrapper),
.personal-ranking-table td.col-rank :deep(.el-input-number),
.personal-ranking-table td.col-rank :deep(.el-input-number .el-input__wrapper),
.personal-ranking-table td.col-merit :deep(.el-input-number),
.personal-ranking-table td.col-merit :deep(.el-input-number .el-input__wrapper) {
  width: 100%;
}

.personal-ranking-table :deep(.number-column .el-input-number),
.personal-ranking-table :deep(.merit-column .el-input-number) {
  display: flex;
  width: 100% !important;
  min-width: 0;
}

.personal-ranking-table :deep(.number-column .el-input-number .el-input),
.personal-ranking-table :deep(.merit-column .el-input-number .el-input) {
  flex: 1 1 auto;
  width: 0;
  min-width: 0;
  height: 100%;
}

.personal-ranking-table :deep(.number-column .el-input-number .el-input__wrapper),
.personal-ranking-table :deep(.merit-column .el-input-number .el-input__wrapper) {
  width: 100%;
}

.personal-ranking-table :deep(.number-column .el-input-number .el-input__inner),
.personal-ranking-table :deep(.merit-column .el-input-number .el-input__inner) {
  padding: 0;
  text-align: right;
}

.personal-merit-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}

.personal-merit-preview {
  font-size: 12px;
  line-height: 1;
  color: #6b7280;
  white-space: nowrap;
}

.exchange-table tbody tr.is-last-selected-row td {
  background: #fff1f2 !important;
  border-top-color: #fda4af;
  border-bottom-color: #fda4af;
}

.exchange-table tbody tr.is-last-selected-row td:first-child {
  border-left-color: #fda4af;
}

.exchange-table tbody tr.is-last-selected-row td:last-child {
  border-right-color: #fda4af;
}

.exchange-table tbody tr.is-last-selected-row .selection-order,
.exchange-table tbody tr.is-last-selected-row .col-consume,
.exchange-table tbody tr.is-last-selected-row .col-total {
  color: #dc2626;
  font-weight: 600;
}

.exchange-table tbody tr.is-last-selected-row td.col-name :deep(.el-input__wrapper),
.exchange-table tbody tr.is-last-selected-row td.col-number :deep(.el-input-number .el-input__wrapper) {
  background: #fff7f7;
  box-shadow: 0 0 0 1px #fecaca inset;
}

.exchange-table .col-index {
  width: 40px;
  min-width: 40px;
  text-align: center;
}

.exchange-table .col-selected {
  width: 52px;
  min-width: 52px;
  text-align: center;
}

.exchange-table .col-name {
  width: 240px;
  min-width: 240px;
}

.exchange-table .col-number {
  width: 84px;
  min-width: 84px;
  text-align: center;
}

.exchange-table .col-total {
  width: 92px;
  min-width: 92px;
  text-align: center;
  white-space: nowrap;
}

.exchange-table .col-consume {
  width: 72px;
  min-width: 72px;
  text-align: center;
  white-space: nowrap;
}

.exchange-table td.col-consume.is-hypothetical-consume {
  color: #6b7280;
  font-weight: 600;
  background: #f3f4f6 !important;
}

.exchange-table .col-actions {
  width: 88px;
  min-width: 88px;
  text-align: center;
}

.exchange-empty {
  padding: 16px 8px !important;
  color: #9ca3af;
  text-align: center;
}

.exchange-table td.col-name :deep(.el-input__wrapper),
.exchange-table td.col-number :deep(.el-input-number),
.exchange-table td.col-number :deep(.el-input-number .el-input__wrapper) {
  width: 100%;
}

.exchange-table :deep(.number-column .el-input-number) {
  display: flex;
  width: 100% !important;
  min-width: 0;
}

.exchange-table :deep(.number-column .el-input-number .el-input) {
  flex: 1 1 auto;
  width: 0;
  min-width: 0;
  height: 100%;
}

.exchange-table :deep(.number-column .el-input-number .el-input__wrapper) {
  width: 100%;
}

.exchange-table :deep(.number-column .el-input-number.is-controls-right .el-input__wrapper) {
  padding-right: calc(var(--shouyuan-number-control-width) + 6px);
}

.exchange-table :deep(.number-column .el-input-number .el-input__inner) {
  padding: 0;
  text-align: right;
}

.exchange-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__decrease),
.exchange-table :deep(.number-column .el-input-number.is-controls-right .el-input-number__increase) {
  width: var(--shouyuan-number-control-width);
  right: 0;
}

.selection-cell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 0;
}

.selection-order {
  min-width: 18px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  color: #2563eb;
  text-align: left;
}

.empty-state {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 20px 4px;
}

.empty-state__title {
  font-size: 18px;
  font-weight: 700;
  color: #111827;
}

.empty-state__description {
  font-size: 14px;
  color: #6b7280;
}

@media (max-width: 768px) {
  .shouyuan-exploration-page {
    padding: 16px 12px 20px;
  }

  .page-title {
    font-size: 28px;
  }

  .page-actions,
  .record-header,
  .section-header,
  .section-header__actions {
    align-items: stretch;
  }

  .page-actions {
    flex-direction: column;
  }

  .page-actions__select {
    width: 100%;
  }

  .field-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }
}
</style>


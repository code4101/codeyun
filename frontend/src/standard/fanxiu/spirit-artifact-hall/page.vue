<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { ArrowDown, ArrowUp, Refresh } from '@element-plus/icons-vue';
import {
  getFanxiuSpiritArtifactHall,
  syncFanxiuSpiritArtifactStorageBag,
  saveFanxiuSpiritArtifactHall,
  type FanxiuSpiritArtifactHallSnapshot,
} from '@/api/fanxiu';

type StatColumnKey =
  | 'chaosPower'
  | 'attack'
  | 'spiritPower'
  | 'health'
  | 'defense';

type StatColumn = {
  key: StatColumnKey;
  label: string;
  baseValue: string;
  baseRawValue: number;
  minWidth: number;
};

type ExclusiveStatColumn = {
  key: string;
  label: string;
  baseValue: string;
  baseRawValue: number;
  minWidth: number;
};

type SpiritArtifactPartRow = Record<StatColumnKey, string> & {
  order: number;
  partName: string;
  rank: number;
  realm: number;
  artifactPeerless1: number;
  artifactPeerless2: number;
  statRawValues: Record<StatColumnKey, string>;
  exclusiveStats: Record<string, string>;
  exclusiveStatRawValues: Record<string, string>;
  runtimeBaseId: number;
  runtimeItemId: string;
  runtimeWareId: number;
  runtimePart: number;
  runtimeRefineNum: number;
  runtimeIsBreak: boolean | null;
  stage: string;
  runtimeEffects: FanxiuSpiritArtifactHallSnapshot["artifacts"][number]["rows"][number]["runtime_effects"];
};

type SpiritArtifact = {
  order: number;
  name: string;
  exclusiveStats: ExclusiveStatColumn[];
  rows: SpiritArtifactPartRow[];
};

type SpiritArtifactMarketItem = {
  order: number;
  artifactName: string;
  partName: string;
  cost: number;
};

type SpiritArtifactStorageBagChoice = {
  order: number;
  rawName: string;
  artifactName: string;
  partName: string;
};

type SpiritArtifactStorageBagItem = {
  order: number;
  title: string;
  quantity: number;
  choices: SpiritArtifactStorageBagChoice[];
};

type StatEditScope = 'common' | 'exclusive';

type EditingStatCell = {
  artifactName: string;
  rowOrder: number;
  scope: StatEditScope;
  key: string;
} | null;

const leadingStatColumns: StatColumn[] = [
  { key: 'chaosPower', label: '混沌道威', baseValue: '0.5万', baseRawValue: 5000, minWidth: 90 },
  { key: 'attack', label: '攻击', baseValue: '1万', baseRawValue: 10000, minWidth: 64 },
];

const trailingStatColumns: StatColumn[] = [
  { key: 'spiritPower', label: '灵力', baseValue: '120万', baseRawValue: 1200000, minWidth: 78 },
  { key: 'health', label: '气血', baseValue: '120万', baseRawValue: 1200000, minWidth: 78 },
  { key: 'defense', label: '守御', baseValue: '1万', baseRawValue: 10000, minWidth: 64 },
];
const statColumnByKey = Object.fromEntries(
  [...leadingStatColumns, ...trailingStatColumns].map(column => [column.key, column]),
) as Record<StatColumnKey, StatColumn>;

const emptyStats: Record<StatColumnKey, string> = {
  chaosPower: '',
  attack: '',
  spiritPower: '',
  health: '',
  defense: '',
};

const statColumnKeys: StatColumnKey[] = ['chaosPower', 'attack', 'spiritPower', 'health', 'defense'];
const backendStatKeyMap: Record<StatColumnKey, string> = {
  chaosPower: 'chaos_power',
  attack: 'attack',
  spiritPower: 'spirit_power',
  health: 'health',
  defense: 'defense',
};
const commonStatLabelKeyMap: Record<string, StatColumnKey> = {
  混沌道威: 'chaosPower',
  混沌灵威: 'chaosPower',
  攻击: 'attack',
  灵力: 'spiritPower',
  气血: 'health',
  守御: 'defense',
  防御: 'defense',
};
const stageStyles: Record<string, { color: string; background: string; borderColor: string; description: string }> = {
  错升: { color: '#b91c1c', background: '#fef2f2', borderColor: '#fca5a5', description: '已突破，但本灵器 A 类尚未全部满／巅，需要重置培养，与阶数无关' },
  初始: { color: '#475569', background: '#f1f5f9', borderColor: '#cbd5e1', description: '尚无红色本体，或红色本体不足6阶；无红色按培养进度0阶理解' },
  预备: { color: '#0e7490', background: '#ecfeff', borderColor: '#67e8f9', description: '已有红色本体且至少6阶；升阶不等于突破' },
  突破: { color: '#166534', background: '#f0fdf4', borderColor: '#86efac', description: '已实际突破，且本灵器全部 A 类达到满／巅' },
  无双: { color: '#1d4ed8', background: '#eff6ff', borderColor: '#93c5fd', description: '满足突破条件，并有灵器无双' },
  道威: { color: '#7e22ce', background: '#faf5ff', borderColor: '#d8b4fe', description: '满足无双条件，并有混沌道威' },
  巅峰: { color: '#92400e', background: '#fffbeb', borderColor: '#fbbf24', description: '满足道威条件，并有巅词缀' },
  待识别: { color: '#64748b', background: 'transparent', borderColor: '#cbd5e1', description: '尚无完整阶数或词缀数据，需同步灵器' },
};
function stageStyle(stage: string) {
  return stageStyles[stage] || stageStyles.待识别!;
}
const artifactPeerlessSteps = [0, 25, 30];
const SAVE_DEBOUNCE_MS = 800;
type ArtifactPeerlessKey = 'artifactPeerless1' | 'artifactPeerless2';
const artifactNameAliases: Record<string, string> = {
  青冥岁月灯: '青暝岁月灯',
};

const artifactSeeds = [
  {
    name: '血晶摩诃剑',
    parts: ['柄', '刃', '穗', '鞘', '珠', '纹'],
    exclusiveStats: [
      { key: '暴击附伤', label: '暴击附伤', baseValue: '1万', baseRawValue: 10000, minWidth: 88 },
      { key: '暴击', label: '暴击', baseValue: '3万', baseRawValue: 30000, minWidth: 64 },
    ],
  },
  {
    name: '天月落星幡',
    parts: ['镜', '幅', '带', '杆', '印', '纹'],
    exclusiveStats: [
      { key: '功法附伤', label: '功法附伤', baseValue: '6万', baseRawValue: 60000, minWidth: 88 },
      { key: '招架', label: '招架', baseValue: '3万', baseRawValue: 30000, minWidth: 64 },
      { key: '神通吸血', label: '神通吸血', baseValue: '1万', baseRawValue: 10000, minWidth: 88 },
    ],
  },
  {
    name: '弥罗宝光幢',
    parts: ['焰', '柱', '环', '座', '珠', '纹'],
    exclusiveStats: [
      { key: '法宝附伤', label: '法宝附伤', baseValue: '6万', baseRawValue: 60000, minWidth: 88 },
      { key: '炼体附伤', label: '炼体附伤', baseValue: '6万', baseRawValue: 60000, minWidth: 88 },
      { key: '闪避', label: '闪避', baseValue: '3万', baseRawValue: 30000, minWidth: 64 },
    ],
  },
  {
    name: '鸿古干天戈',
    parts: ['锋', '芒', '珠', '坠', '柄', '气'],
    exclusiveStats: [
      { key: '灵兽附伤', label: '灵兽附伤', baseValue: '6万', baseRawValue: 60000, minWidth: 88 },
      { key: '仙语附伤', label: '仙语附伤', baseValue: '6万', baseRawValue: 60000, minWidth: 88 },
      { key: '全技能减伤', label: '全技能减伤', baseValue: '1万', baseRawValue: 10000, minWidth: 100 },
    ],
  },
  {
    name: '青暝岁月灯',
    parts: ['盏', '芯', '穗', '杆', '纹', '荧'],
    exclusiveStats: [
      { key: '灵宝抵御', label: '灵宝抵御', baseValue: '2.4万', baseRawValue: 24000, minWidth: 100 },
      { key: '功法抵御', label: '功法抵御', baseValue: '2.4万', baseRawValue: 24000, minWidth: 100 },
      { key: '全技能减伤', label: '全技能减伤', baseValue: '0.8万', baseRawValue: 8000, minWidth: 108 },
    ],
  },
  {
    name: '苍烟神火炉',
    parts: ['饰', '盖', '身', '柄', '光', '座'],
    exclusiveStats: [
      { key: '招架', label: '招架', baseValue: '2.4万', baseRawValue: 24000, minWidth: 76 },
      { key: '灵兽附伤', label: '灵兽附伤', baseValue: '4.8万', baseRawValue: 48000, minWidth: 100 },
      { key: '法宝附伤', label: '法宝附伤', baseValue: '4.8万', baseRawValue: 48000, minWidth: 100 },
    ],
  },
  {
    name: '御海镇神图',
    parts: ['卷', '瑚', '海', '轴', '灵', '山'],
    exclusiveStats: [
      { key: '仙语附伤', label: '仙语附伤', baseValue: '4.8万', baseRawValue: 48000, minWidth: 100 },
      { key: '灵暴附伤', label: '灵暴附伤', baseValue: '0.8万', baseRawValue: 8000, minWidth: 100 },
      { key: '灵暴', label: '灵暴', baseValue: '2.4万', baseRawValue: 24000, minWidth: 76 },
    ],
  },
  {
    name: '六界轮回盘',
    parts: ['珠', '盘', '焰', '环', '荧', '晶'],
    exclusiveStats: [
      { key: '神识全技能增伤', label: '神识全技能增伤', baseValue: '', baseRawValue: 0, minWidth: 120 },
      { key: '神识暴击', label: '神识暴击', baseValue: '', baseRawValue: 0, minWidth: 88 },
      { key: '神识暴击附伤', label: '神识暴击附伤', baseValue: '', baseRawValue: 0, minWidth: 112 },
      { key: '神识最终增伤', label: '神识最终增伤', baseValue: '', baseRawValue: 0, minWidth: 104 },
    ],
  },
];

function createExclusiveStats(columns: ExclusiveStatColumn[], savedStats: unknown = {}) {
  const rawStats = savedStats && typeof savedStats === 'object' ? savedStats as Record<string, unknown> : {};
  return Object.fromEntries(columns.map(column => [column.key, normalizeStatText(rawStats[column.key])]));
}

function createStatRawValues(savedStats: unknown = {}) {
  const rawStats = savedStats && typeof savedStats === 'object' ? savedStats as Record<string, unknown> : {};
  return Object.fromEntries(
    statColumnKeys.map(key => [key, normalizeStatText(rawStats[backendStatKeyMap[key]] ?? rawStats[key])]),
  ) as Record<StatColumnKey, string>;
}

function createExclusiveStatRawValues(columns: ExclusiveStatColumn[], savedStats: unknown = {}) {
  const rawStats = savedStats && typeof savedStats === 'object' ? savedStats as Record<string, unknown> : {};
  return Object.fromEntries(columns.map(column => [column.key, normalizeStatText(rawStats[column.key])]));
}

function formatStatColumnLabel(column: { label: string; baseValue?: string }) {
  return column.baseValue ? `${column.label}${column.baseValue}` : column.label;
}

function formatRuntimeTime(timestamp: number) {
  return timestamp > 0 ? new Date(timestamp * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '';
}

function createPartRow(
  partName: string,
  index: number,
  exclusiveStats: ExclusiveStatColumn[] = [],
): SpiritArtifactPartRow {
  return {
    order: index + 1,
    partName,
    rank: 0,
    realm: 0,
    artifactPeerless1: 0,
    artifactPeerless2: 0,
    statRawValues: createStatRawValues(),
    exclusiveStats: createExclusiveStats(exclusiveStats),
    exclusiveStatRawValues: createExclusiveStatRawValues(exclusiveStats),
    runtimeBaseId: 0,
    runtimeItemId: '',
    runtimeWareId: 0,
    runtimePart: 0,
    runtimeRefineNum: 0,
    runtimeIsBreak: null,
    stage: "待识别",
    runtimeEffects: [],
    ...emptyStats,
  };
}

const loading = ref(false);
const syncingStorageBag = ref(false);

async function syncStorageBag() {
  syncingStorageBag.value = true;
  try {
    const snapshot = await syncFanxiuSpiritArtifactStorageBag();
    storageBagItems.value = normalizeStorageBagItems(snapshot.storage_bag_items);
    ElMessage.success('储物袋数量与自选奖励已同步');
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '同步储物袋失败');
  } finally {
    syncingStorageBag.value = false;
  }
}
const runtimeComplete = ref(false);
const runtimeSource = ref('');
const runtimeError = ref('');
const runtimeEquippedCount = ref(0);
const runtimeUpdatedAt = ref(0);
const saving = ref(false);
const pageHydrated = ref(false);
const editingStatCell = ref<EditingStatCell>(null);
const editingStatRawValue = ref('');
const statEditInputRef = ref<any>(null);
const marketItems = ref<SpiritArtifactMarketItem[]>([]);
const marketCurrencyCount = ref(0);
const storageBagItems = ref<SpiritArtifactStorageBagItem[]>([]);
const artifacts = ref<SpiritArtifact[]>(artifactSeeds.map((artifact, index) => ({
  order: index + 1,
  name: artifact.name,
  exclusiveStats: artifact.exclusiveStats,
  rows: artifact.parts.map((partName, partIndex) => createPartRow(partName, partIndex, artifact.exclusiveStats)),
})));
let saveTimer: ReturnType<typeof setTimeout> | null = null;

function normalizeArtifactPeerless(value: number) {
  return artifactPeerlessSteps.includes(value) ? value : 0;
}

function getArtifactPeerlessIndex(value: number) {
  return artifactPeerlessSteps.indexOf(normalizeArtifactPeerless(value));
}

function formatArtifactPeerless(value: number) {
  return `${normalizeArtifactPeerless(value)}%`;
}

function canStepArtifactPeerless(row: SpiritArtifactPartRow, key: ArtifactPeerlessKey, direction: -1 | 1) {
  const currentIndex = getArtifactPeerlessIndex(row[key]);
  const nextIndex = currentIndex + direction;
  return nextIndex >= 0 && nextIndex < artifactPeerlessSteps.length;
}

function stepArtifactPeerless(row: SpiritArtifactPartRow, key: ArtifactPeerlessKey, direction: -1 | 1) {
  if (!canStepArtifactPeerless(row, key, direction)) {
    return;
  }
  row[key] = artifactPeerlessSteps[getArtifactPeerlessIndex(row[key]) + direction];
}

function hasArtifactPeerless2Column(artifact: SpiritArtifact) {
  return artifact.rows.some(row => normalizeNonNegativeInteger(row.realm) > 0);
}

function normalizeNonNegativeInteger(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.trunc(value));
}

function normalizeStatText(value: unknown) {
  return String(value ?? '').trim();
}

function parsePercentText(value: unknown) {
  const text = normalizeStatText(value).replace(/\s+/g, '');
  const matched = text.match(/^(\d+(?:\.\d+)?)%$/);
  if (!matched) {
    return null;
  }
  const percent = Number(matched[1]);
  return Number.isFinite(percent) && percent >= 0 ? percent : null;
}

function parseRawAttributeValue(value: unknown) {
  const text = normalizeStatText(value).replace(/[,，\s]/g, '');
  if (!text) {
    return null;
  }
  const matched = text.match(/^(\d+(?:\.\d+)?)(万)?$/);
  if (!matched) {
    return null;
  }
  const numeric = Number(matched[1]);
  if (!Number.isFinite(numeric) || numeric < 0) {
    return null;
  }
  return Math.round(numeric * (matched[2] ? 10000 : 1));
}

function formatRawValueAsPercent(rawValue: number, baseRawValue: number) {
  if (!Number.isFinite(rawValue) || !Number.isFinite(baseRawValue) || baseRawValue <= 0) {
    return '';
  }
  return `${Math.round(rawValue * 100 / baseRawValue)}%`;
}

function deriveRawValueFromPercent(percentText: unknown, baseRawValue: number) {
  const percent = parsePercentText(percentText);
  if (percent === null || !Number.isFinite(baseRawValue) || baseRawValue <= 0) {
    return '';
  }
  return String(Math.round(percent * baseRawValue / 100));
}

function normalizeStatDisplayValue(value: unknown, baseRawValue: number) {
  const text = normalizeStatText(value);
  if (!text) {
    return '';
  }
  if (parsePercentText(text) !== null) {
    return text;
  }
  if (baseRawValue <= 0) {
    return text;
  }
  const rawValue = parseRawAttributeValue(text);
  return rawValue === null ? text : formatRawValueAsPercent(rawValue, baseRawValue);
}

function normalizeSavedRawValue(percentValue: unknown, rawValue: unknown, baseRawValue: number) {
  const rawText = normalizeStatText(rawValue);
  if (rawText) {
    const parsedRaw = parseRawAttributeValue(rawText);
    return parsedRaw === null ? rawText : String(parsedRaw);
  }
  const statText = normalizeStatText(percentValue);
  if (!statText) {
    return '';
  }
  if (parsePercentText(statText) !== null) {
    return deriveRawValueFromPercent(statText, baseRawValue);
  }
  const parsedRaw = parseRawAttributeValue(statText);
  return parsedRaw === null ? '' : String(parsedRaw);
}

function createDefaultArtifacts() {
  return artifactSeeds.map((artifact, index) => ({
    order: index + 1,
    name: artifact.name,
    exclusiveStats: artifact.exclusiveStats,
    rows: artifact.parts.map((partName, partIndex) => createPartRow(partName, partIndex, artifact.exclusiveStats)),
  }));
}

function getMarketItemKey(item: Pick<SpiritArtifactMarketItem, 'artifactName' | 'partName'>) {
  return `${item.artifactName}::${item.partName}`;
}

function normalizeMarketCost(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? Math.trunc(numeric) : 80;
}

function normalizeMarketCurrencyCount(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? Math.trunc(numeric) : 0;
}

function normalizeCanonicalArtifactName(value: unknown) {
  const artifactName = normalizeStatText(value);
  return artifactNameAliases[artifactName] || artifactName;
}

function normalizeMarketItems(items: unknown): SpiritArtifactMarketItem[] {
  if (!Array.isArray(items)) {
    return [];
  }

  const seen = new Set<string>();
  const normalizedItems: SpiritArtifactMarketItem[] = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') {
      continue;
    }
    const rawItem = item as Record<string, any>;
    const artifactName = normalizeCanonicalArtifactName(rawItem.artifact_name ?? rawItem.artifactName);
    const seed = artifactSeeds.find(candidate => candidate.name === artifactName);
    if (!seed) {
      continue;
    }
    const partName = normalizeStatText(rawItem.part_name ?? rawItem.partName);
    if (!seed.parts.includes(partName)) {
      continue;
    }
    const itemKey = getMarketItemKey({ artifactName, partName });
    if (seen.has(itemKey)) {
      continue;
    }
    seen.add(itemKey);
    normalizedItems.push({
      order: normalizedItems.length + 1,
      artifactName,
      partName,
      cost: normalizeMarketCost(rawItem.cost),
    });
  }
  return normalizedItems;
}

function normalizeStorageBagQuantity(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? Math.trunc(numeric) : 0;
}

function getStorageBagChoiceKey(choice: Pick<SpiritArtifactStorageBagChoice, 'artifactName' | 'partName'>) {
  return `${choice.artifactName}::${choice.partName}`;
}

function normalizeStorageBagChoices(choices: unknown): SpiritArtifactStorageBagChoice[] {
  if (!Array.isArray(choices)) {
    return [];
  }

  const seen = new Set<string>();
  const normalizedChoices: SpiritArtifactStorageBagChoice[] = [];
  for (const choice of choices) {
    if (!choice || typeof choice !== 'object') {
      continue;
    }
    const rawChoice = choice as Record<string, any>;
    const artifactName = normalizeCanonicalArtifactName(rawChoice.artifact_name ?? rawChoice.artifactName);
    const seed = artifactSeeds.find(candidate => candidate.name === artifactName);
    if (!seed) {
      continue;
    }
    const partName = normalizeStatText(rawChoice.part_name ?? rawChoice.partName);
    if (!seed.parts.includes(partName)) {
      continue;
    }
    const choiceKey = getStorageBagChoiceKey({ artifactName, partName });
    if (seen.has(choiceKey)) {
      continue;
    }
    seen.add(choiceKey);
    normalizedChoices.push({
      order: normalizedChoices.length + 1,
      rawName: normalizeStatText(rawChoice.raw_name ?? rawChoice.rawName),
      artifactName,
      partName,
    });
  }
  return normalizedChoices;
}

function sortStorageBagItems(items: SpiritArtifactStorageBagItem[]): SpiritArtifactStorageBagItem[] {
  const digits: Record<string, number> = {
    一: 1, 壹: 1, 二: 2, 贰: 2, 三: 3, 叁: 3, 四: 4, 肆: 4,
    五: 5, 伍: 5, 六: 6, 陆: 6, 七: 7, 柒: 7, 八: 8, 捌: 8, 九: 9, 玖: 9,
  };
  const groups = new Map<string, number>();
  // 保留不同宝匣类型的出现顺序，同类后缀按数值排列，避免录入顺序影响壹、贰。
  return items.map(item => {
    const match = item.title.match(/^(.*?)[·・.．\s]*([一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]+|\d+)$/);
    const group = match?.[1] ?? item.title;
    const suffix = match?.[2] ?? '';
    const tens = suffix.split(/[十拾]/);
    const number = /^\d+$/.test(suffix) ? Number(suffix)
      : tens.length === 2 ? (digits[tens[0]] ?? 1) * 10 + (digits[tens[1]] ?? 0)
      : digits[suffix] ?? 0;
    if (!groups.has(group)) groups.set(group, groups.size);
    // 珍藏匣跨多个灵器，固定置顶，便于优先查看通用自选。
    return { item, groupOrder: item.title === '珍藏灵器自选匣' ? -1 : groups.get(group)!, number };
  }).sort((left, right) => left.groupOrder - right.groupOrder || left.number - right.number)
    .map(({ item }, index) => ({ ...item, order: index + 1 }));
}

function normalizeStorageBagItems(items: unknown): SpiritArtifactStorageBagItem[] {
  if (!Array.isArray(items)) {
    return [];
  }

  const seen = new Set<string>();
  const normalizedItems: SpiritArtifactStorageBagItem[] = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') {
      continue;
    }
    const rawItem = item as Record<string, any>;
    const title = normalizeStatText(rawItem.title);
    if (!title || seen.has(title)) {
      continue;
    }
    const choices = normalizeStorageBagChoices(rawItem.choices);
    if (choices.length <= 0) {
      continue;
    }
    seen.add(title);
    normalizedItems.push({
      order: normalizedItems.length + 1,
      title,
      quantity: normalizeStorageBagQuantity(rawItem.quantity),
      choices,
    });
  }
  return sortStorageBagItems(normalizedItems);
}

function snapshotToArtifacts(snapshot: FanxiuSpiritArtifactHallSnapshot): SpiritArtifact[] {
  const runtimeArtifacts = snapshot.artifacts || [];
  if (runtimeArtifacts.length <= 0) {
    return createDefaultArtifacts();
  }
  return runtimeArtifacts.map((savedArtifact, artifactIndex) => {
    const seed = artifactSeeds.find(candidate => candidate.name === savedArtifact.name);
    const dynamicKeys = new Set<string>();
    for (const row of savedArtifact.rows || []) {
      Object.keys((row as any).exclusive_stats || (row as any).exclusiveStats || {}).forEach(key => dynamicKeys.add(key));
    }
    const exclusiveStats: ExclusiveStatColumn[] = [
      ...(seed?.exclusiveStats || []),
      ...[...dynamicKeys]
        .filter(key => !seed?.exclusiveStats.some(column => column.key === key))
        .map(key => ({ key, label: key, baseValue: '', baseRawValue: 0, minWidth: Math.max(76, key.length * 14) })),
    ];
    const savedRows = [...(savedArtifact.rows || [])].sort((left, right) => left.order - right.order);
    return {
      order: artifactIndex + 1,
      name: savedArtifact.name,
      exclusiveStats,
      rows: savedRows.map((savedRow, partIndex) => {
        const partName = savedRow.part_name || seed?.parts[partIndex] || `部位 ${partIndex + 1}`;
        const rawSavedRow = (savedRow || {}) as any;
        const savedStatRawValues = rawSavedRow.stat_raw_values || rawSavedRow.statRawValues || {};
        const statRawValues = Object.fromEntries(
          statColumnKeys.map(key => [
            key,
            normalizeSavedRawValue(
              rawSavedRow[backendStatKeyMap[key]],
              savedStatRawValues[backendStatKeyMap[key]] ?? savedStatRawValues[key],
              statColumnByKey[key].baseRawValue,
            ),
          ]),
        ) as Record<StatColumnKey, string>;
        const savedExclusiveStats = rawSavedRow.exclusive_stats || rawSavedRow.exclusiveStats || {};
        const savedExclusiveStatRawValues = rawSavedRow.exclusive_stat_raw_values || rawSavedRow.exclusiveStatRawValues || {};
        return {
          order: partIndex + 1,
          partName,
          rank: normalizeNonNegativeInteger(savedRow?.rank ?? 0),
          realm: normalizeNonNegativeInteger(savedRow?.realm ?? 0),
          artifactPeerless1: normalizeArtifactPeerless(
            normalizeNonNegativeInteger(savedRow?.artifact_peerless_1 ?? savedRow?.aura_peerless ?? 0),
          ),
          artifactPeerless2: normalizeArtifactPeerless(normalizeNonNegativeInteger(savedRow?.artifact_peerless_2 ?? 0)),
          statRawValues,
          chaosPower: normalizeStatDisplayValue(rawSavedRow.chaos_power, statColumnByKey.chaosPower.baseRawValue),
          attack: normalizeStatDisplayValue(rawSavedRow.attack, statColumnByKey.attack.baseRawValue),
          exclusiveStats: Object.fromEntries(
            exclusiveStats.map(column => [
              column.key,
              normalizeStatDisplayValue(savedExclusiveStats[column.key], column.baseRawValue),
            ]),
          ),
          exclusiveStatRawValues: Object.fromEntries(
            exclusiveStats.map(column => [
              column.key,
              normalizeSavedRawValue(
                savedExclusiveStats[column.key],
                savedExclusiveStatRawValues[column.key],
                column.baseRawValue,
              ),
            ]),
          ),
          spiritPower: normalizeStatDisplayValue(rawSavedRow.spirit_power, statColumnByKey.spiritPower.baseRawValue),
          health: normalizeStatDisplayValue(rawSavedRow.health, statColumnByKey.health.baseRawValue),
          defense: normalizeStatDisplayValue(rawSavedRow.defense, statColumnByKey.defense.baseRawValue),
          runtimeBaseId: normalizeNonNegativeInteger(rawSavedRow.runtime_base_id),
          runtimeItemId: String(rawSavedRow.runtime_item_id || ''),
          runtimeWareId: normalizeNonNegativeInteger(rawSavedRow.runtime_ware_id),
          runtimePart: normalizeNonNegativeInteger(rawSavedRow.runtime_part),
          runtimeRefineNum: normalizeNonNegativeInteger(rawSavedRow.runtime_refine_num),
          runtimeIsBreak: typeof rawSavedRow.runtime_is_break === 'boolean' ? rawSavedRow.runtime_is_break : null,
          stage: savedRow?.stage || "待识别",
          runtimeEffects: savedRow?.runtime_effects || [],
        };
      }),
    };
  });
}

function artifactsToSnapshot(): FanxiuSpiritArtifactHallSnapshot {
  return {
    artifacts: artifacts.value.map(artifact => ({
      order: artifact.order,
      name: artifact.name,
      rows: artifact.rows.map(row => ({
        order: row.order,
        part_name: row.partName,
        rank: normalizeNonNegativeInteger(row.rank),
        realm: normalizeNonNegativeInteger(row.realm),
        artifact_peerless_1: normalizeArtifactPeerless(row.artifactPeerless1),
        artifact_peerless_2: normalizeArtifactPeerless(row.artifactPeerless2),
        chaos_power: normalizeStatText(row.chaosPower),
        attack: normalizeStatText(row.attack),
        stat_raw_values: Object.fromEntries(
          statColumnKeys.map(key => [backendStatKeyMap[key], normalizeStatText(row.statRawValues[key])]),
        ),
        exclusive_stats: createExclusiveStats(artifact.exclusiveStats, row.exclusiveStats),
        exclusive_stat_raw_values: Object.fromEntries(
          artifact.exclusiveStats.map(column => [column.key, normalizeStatText(row.exclusiveStatRawValues[column.key])]),
        ),
        spirit_power: normalizeStatText(row.spiritPower),
        health: normalizeStatText(row.health),
        defense: normalizeStatText(row.defense),
        runtime_base_id: row.runtimeBaseId,
        runtime_item_id: row.runtimeItemId,
        runtime_ware_id: row.runtimeWareId,
        runtime_part: row.runtimePart,
        runtime_refine_num: row.runtimeRefineNum,
        runtime_is_break: row.runtimeIsBreak,
        runtime_effects: row.runtimeEffects,
      })),
    })),
    market_currency_count: normalizeMarketCurrencyCount(marketCurrencyCount.value),
    market_items: marketItems.value.map((item, index) => ({
      order: index + 1,
      artifact_name: item.artifactName,
      part_name: item.partName,
      cost: normalizeMarketCost(item.cost),
    })),
    storage_bag_items: storageBagItems.value.map((item, index) => ({
      order: index + 1,
      title: item.title,
      quantity: normalizeStorageBagQuantity(item.quantity),
      choices: item.choices.map((choice, choiceIndex) => ({
        order: choiceIndex + 1,
        raw_name: choice.rawName,
        artifact_name: choice.artifactName,
        part_name: choice.partName,
      })),
    })),
    runtime_source: runtimeSource.value,
    runtime_complete: runtimeComplete.value,
    runtime_error: runtimeError.value,
    runtime_updated_at: runtimeUpdatedAt.value,
    runtime_item_count: runtimeEquippedCount.value,
    runtime_equipped_count: runtimeEquippedCount.value,
    runtime_debug: {},
  };
}

function clearSaveTimer() {
  if (!saveTimer) {
    return;
  }
  clearTimeout(saveTimer);
  saveTimer = null;
}

async function saveArtifacts() {
  if (!pageHydrated.value) {
    return;
  }
  clearSaveTimer();
  saving.value = true;
  try {
    const saved = await saveFanxiuSpiritArtifactHall(artifactsToSnapshot());
    for (const artifact of artifacts.value) {
      const savedArtifact = saved.artifacts.find(item => item.name === artifact.name);
      for (const row of artifact.rows) {
        row.stage = savedArtifact?.rows.find(item => item.part_name === row.partName)?.stage || '待识别';
      }
    }
  } catch (error) {
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '保存灵器数据失败');
  } finally {
    saving.value = false;
  }
}

function scheduleSave(immediate = false) {
  if (!pageHydrated.value) {
    return;
  }
  clearSaveTimer();
  if (immediate) {
    void saveArtifacts();
    return;
  }
  saveTimer = setTimeout(() => {
    void saveArtifacts();
  }, SAVE_DEBOUNCE_MS);
}

async function loadArtifacts() {
  pageHydrated.value = false;
  loading.value = true;
  try {
    const snapshot = await getFanxiuSpiritArtifactHall();
    artifacts.value = snapshotToArtifacts(snapshot);
    marketCurrencyCount.value = normalizeMarketCurrencyCount(snapshot.market_currency_count);
    marketItems.value = normalizeMarketItems(snapshot.market_items);
    storageBagItems.value = normalizeStorageBagItems(snapshot.storage_bag_items);
    runtimeComplete.value = Boolean(snapshot.runtime_complete);
    runtimeSource.value = snapshot.runtime_source || '';
    runtimeError.value = snapshot.runtime_error || '';
    runtimeEquippedCount.value = normalizeNonNegativeInteger(snapshot.runtime_equipped_count);
    runtimeUpdatedAt.value = Number(snapshot.runtime_updated_at || 0);
    await nextTick();
    pageHydrated.value = true;
  } catch (error) {
    artifacts.value = createDefaultArtifacts();
    marketCurrencyCount.value = 0;
    marketItems.value = [];
    storageBagItems.value = [];
    runtimeComplete.value = false;
    runtimeSource.value = '';
    runtimeError.value = '';
    runtimeEquippedCount.value = 0;
    runtimeUpdatedAt.value = 0;
    await nextTick();
    // 读取失败时保留只读默认表，避免 watch 把空白兜底数据自动写回仓库。
    pageHydrated.value = false;
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '读取灵器数据失败');
  } finally {
    loading.value = false;
  }
}

function getArtifactPartRow(artifactName: string, partName: string) {
  const artifact = artifacts.value.find(candidate => candidate.name === artifactName);
  const row = artifact?.rows.find(candidate => candidate.partName === partName);
  return { artifact, row };
}

function getMarketItemCurrentRank(item: SpiritArtifactMarketItem) {
  const { row } = getArtifactPartRow(item.artifactName, item.partName);
  return normalizeNonNegativeInteger(row?.rank ?? 0);
}

function formatMarketArtifactName(item: SpiritArtifactMarketItem) {
  const artifact = artifacts.value.find(candidate => candidate.name === item.artifactName);
  return artifact ? `${artifact.order} ${artifact.name}` : item.artifactName;
}

function formatMarketPartName(item: SpiritArtifactMarketItem) {
  const artifact = artifacts.value.find(candidate => candidate.name === item.artifactName);
  const row = artifact?.rows.find(candidate => candidate.partName === item.partName);
  return row ? `${row.order} ${row.partName}` : item.partName;
}

function getStorageBagChoiceCurrentRank(choice: SpiritArtifactStorageBagChoice) {
  const { row } = getArtifactPartRow(choice.artifactName, choice.partName);
  return normalizeNonNegativeInteger(row?.rank ?? 0);
}

function getStorageBagChoiceCurrentRealm(choice: SpiritArtifactStorageBagChoice) {
  const { row } = getArtifactPartRow(choice.artifactName, choice.partName);
  return normalizeNonNegativeInteger(row?.realm ?? 0);
}

function formatStorageBagChoiceArtifactName(choice: SpiritArtifactStorageBagChoice) {
  const { artifact } = getArtifactPartRow(choice.artifactName, choice.partName);
  return artifact ? `${artifact.order} ${artifact.name}` : choice.artifactName;
}

function formatStorageBagChoicePartName(choice: SpiritArtifactStorageBagChoice) {
  const { artifact, row } = getArtifactPartRow(choice.artifactName, choice.partName);
  return artifact && row ? `${row.order} ${row.partName}` : choice.partName;
}

function isEditingStatCell(artifact: SpiritArtifact, row: SpiritArtifactPartRow, scope: StatEditScope, key: string) {
  const editing = editingStatCell.value;
  return Boolean(
    editing
      && editing.artifactName === artifact.name
      && editing.rowOrder === row.order
      && editing.scope === scope
      && editing.key === key,
  );
}

function getStatRawValue(
  row: SpiritArtifactPartRow,
  scope: StatEditScope,
  key: string,
  percentValue: string,
  baseRawValue: number,
) {
  const rawValue = scope === 'common'
    ? row.statRawValues[key as StatColumnKey]
    : row.exclusiveStatRawValues[key];
  return normalizeStatText(rawValue) || deriveRawValueFromPercent(percentValue, baseRawValue);
}

function getActiveStatEditInput() {
  const inputRef = statEditInputRef.value;
  return Array.isArray(inputRef) ? inputRef.find(Boolean) : inputRef;
}

function startStatCellEdit(
  artifact: SpiritArtifact,
  row: SpiritArtifactPartRow,
  scope: StatEditScope,
  key: string,
  percentValue: string,
  baseRawValue: number,
) {
  if (runtimeComplete.value) {
    return;
  }
  editingStatCell.value = {
    artifactName: artifact.name,
    rowOrder: row.order,
    scope,
    key,
  };
  editingStatRawValue.value = getStatRawValue(row, scope, key, percentValue, baseRawValue);
  nextTick(() => {
    const input = getActiveStatEditInput();
    input?.focus?.();
    input?.select?.();
  });
}

function cancelStatCellEdit() {
  editingStatCell.value = null;
  editingStatRawValue.value = '';
}

function commitStatCellEdit(
  artifact: SpiritArtifact,
  row: SpiritArtifactPartRow,
  scope: StatEditScope,
  key: string,
  baseRawValue: number,
) {
  if (!isEditingStatCell(artifact, row, scope, key)) {
    return;
  }

  const inputText = normalizeStatText(editingStatRawValue.value);
  const percentInput = parsePercentText(inputText);
  let nextPercent = '';
  let nextRawValue = '';

  if (inputText) {
    if (percentInput !== null) {
      nextPercent = `${Math.round(percentInput)}%`;
      nextRawValue = deriveRawValueFromPercent(nextPercent, baseRawValue);
    } else {
      const rawValue = parseRawAttributeValue(inputText);
      if (rawValue === null) {
        ElMessage.warning('请输入整数属性值');
        nextTick(() => {
          const input = getActiveStatEditInput();
          input?.focus?.();
          input?.select?.();
        });
        return;
      }
      nextRawValue = String(rawValue);
      nextPercent = formatRawValueAsPercent(rawValue, baseRawValue);
    }
  }

  if (scope === 'common') {
    row[key as StatColumnKey] = nextPercent;
    row.statRawValues[key as StatColumnKey] = nextRawValue;
  } else {
    row.exclusiveStats[key] = nextPercent;
    row.exclusiveStatRawValues[key] = nextRawValue;
  }
  cancelStatCellEdit();
}

watch(
  [marketItems, marketCurrencyCount],
  () => {
    scheduleSave();
  },
  { deep: true },
);

onMounted(() => {
  void loadArtifacts();
});

onBeforeUnmount(() => {
  if (saveTimer) {
    void saveArtifacts();
  }
});
</script>

<template>
  <div class="spirit-artifact-page" v-loading="loading">
    <div class="page-header">
      <h2 class="page-title">道具仓库 · {{ artifacts.length }} 灵器</h2>
    </div>

    <div class="recognition-toolbar">
      <el-button
        v-if="runtimeComplete"
        type="primary"
        :icon="Refresh"
        :loading="loading"
        class="recognition-button"
        @click="loadArtifacts"
      >
        刷新数据库快照
      </el-button>
      <el-tag
        v-if="runtimeComplete"
        :type="runtimeSource === 'lua_main_state_server_sync' ? 'success' : 'primary'"
        effect="plain"
        :title="runtimeSource"
      >
        {{ runtimeSource === 'lua_main_state_server_sync' ? '服务器装配引用' : '游戏运行态主槽' }}
        · {{ runtimeEquippedCount }}/{{ artifacts.length * 6 }}
      </el-tag>
      <span v-if="runtimeComplete && runtimeUpdatedAt" class="runtime-time">
        {{ formatRuntimeTime(runtimeUpdatedAt) }}
      </span>
      <el-tag v-if="!runtimeComplete" type="warning" effect="plain">
        {{ runtimeError ? '运行态不可用，显示已保存数据' : '等待游戏运行态' }}
      </el-tag>
      <span v-if="saving" class="save-status">保存中...</span>
    </div>

    <section class="market-panel">
      <div class="market-heading">
        <div class="market-title-group">
          <h3 class="market-title">仙市 / 珍宝阁</h3>
          <span class="market-currency">灵器铸形元魄：{{ marketCurrencyCount }}</span>
        </div>
      </div>
      <el-table
        v-if="marketItems.length"
        :data="marketItems"
        border
        size="small"
        table-layout="auto"
        :fit="false"
        class="market-table"
      >
        <el-table-column label="#" width="54" align="center">
          <template #default="{ $index }">
            <span>{{ $index + 1 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="灵器" min-width="120">
          <template #default="{ row }">
            <span class="market-item-name">{{ formatMarketArtifactName(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="部位" width="70" align="center">
          <template #default="{ row }">
            <span>{{ formatMarketPartName(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="兑换所需" width="90" align="right">
          <template #default="{ row }">
            <span>{{ row.cost }}</span>
          </template>
        </el-table-column>
        <el-table-column label="当前阶数" width="90" align="right">
          <template #default="{ row }">
            <span>{{ getMarketItemCurrentRank(row) }}阶</span>
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="market-empty">暂无珍宝阁灵器清单</div>
    </section>

    <section class="storage-bag-panel">
      <div class="storage-bag-heading">
        <h3 class="storage-bag-title">储物袋</h3>
        <el-button size="small" :icon="Refresh" :loading="syncingStorageBag" @click="syncStorageBag">
          同步储物袋
        </el-button>
      </div>
      <div v-if="storageBagItems.length" class="storage-bag-list">
        <div
          v-for="item in storageBagItems"
          :key="item.title"
          class="storage-bag-item"
        >
          <div class="storage-bag-item-heading">
            <span class="storage-bag-item-title">{{ item.order }} {{ item.title }}</span>
            <span class="storage-bag-quantity">数量：{{ item.quantity }}</span>
          </div>
          <el-table
            :data="item.choices"
            border
            size="small"
            table-layout="auto"
            :fit="false"
            class="storage-bag-table"
          >
            <el-table-column label="#" width="54" align="center">
              <template #default="{ $index }">
                <span>{{ $index + 1 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="自选名称" min-width="120">
              <template #default="{ row }">
                <span class="storage-bag-choice-raw">{{ row.rawName || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="灵器" min-width="120">
              <template #default="{ row }">
                <span class="storage-bag-choice-name">{{ formatStorageBagChoiceArtifactName(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="部位" width="70" align="center">
              <template #default="{ row }">
                <span>{{ formatStorageBagChoicePartName(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="当前阶数" width="90" align="right">
              <template #default="{ row }">
                <span>{{ getStorageBagChoiceCurrentRank(row) }}阶</span>
              </template>
            </el-table-column>
            <el-table-column label="当前境数" width="90" align="right">
              <template #default="{ row }">
                <span>{{ getStorageBagChoiceCurrentRealm(row) }}境</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
      <div v-else class="storage-bag-empty">暂无储物袋自选箱</div>
    </section>

    <section
      v-for="artifact in artifacts"
      :key="artifact.name"
      class="artifact-panel"
    >
      <div class="artifact-heading">
        <h3 class="artifact-title">
          <span class="artifact-order">{{ artifact.order }}</span>
          <span>{{ artifact.name }}</span>
        </h3>
      </div>

      <div class="table-wrap">
        <el-table
          :data="artifact.rows"
          border
          size="small"
          table-layout="auto"
          :fit="false"
          class="artifact-table"
        >
          <el-table-column label="部位" min-width="84">
            <template #default="{ row }">
              <span class="part-cell">{{ row.order }} {{ row.partName }}</span>
            </template>
          </el-table-column>
          <el-table-column label="阶段" width="88" align="center">
            <template #default="{ row }">
              <el-tooltip :content="stageStyle(row.stage).description" placement="top">
                <span class="stage-badge" :style="stageStyle(row.stage)">{{ row.stage }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="阶数" width="90" align="center">
            <template #default="{ row }">
              <el-input-number
                v-model="row.rank"
                :min="0"
                :step="1"
                step-strictly
                controls-position="right"
                size="small"
                class="integer-input"
                :disabled="runtimeComplete"
              />
            </template>
          </el-table-column>
          <el-table-column label="境数" width="90" align="center">
            <template #default="{ row }">
              <el-input-number
                v-model="row.realm"
                :min="0"
                :step="1"
                step-strictly
                controls-position="right"
                size="small"
                class="integer-input"
                :disabled="runtimeComplete"
              />
            </template>
          </el-table-column>
          <el-table-column label="灵器无双" width="112" align="center">
            <template #default="{ row }">
              <div class="percent-stepper">
                <span
                  class="percent-stepper__value"
                  :class="{ 'percent-stepper__value--empty': row.artifactPeerless1 === 0 }"
                >
                  {{ formatArtifactPeerless(row.artifactPeerless1) }}
                </span>
                <span class="percent-stepper__controls">
                  <el-button
                    :icon="ArrowUp"
                    :disabled="runtimeComplete || !canStepArtifactPeerless(row, 'artifactPeerless1', 1)"
                    size="small"
                    text
                    class="percent-stepper__button"
                    :aria-label="`提高 ${row.partName} 灵器无双档位`"
                    @click.stop="stepArtifactPeerless(row, 'artifactPeerless1', 1)"
                  />
                  <el-button
                    :icon="ArrowDown"
                    :disabled="runtimeComplete || !canStepArtifactPeerless(row, 'artifactPeerless1', -1)"
                    size="small"
                    text
                    class="percent-stepper__button"
                    :aria-label="`降低 ${row.partName} 灵器无双档位`"
                    @click.stop="stepArtifactPeerless(row, 'artifactPeerless1', -1)"
                  />
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            v-if="hasArtifactPeerless2Column(artifact)"
            label="灵器无双2"
            width="112"
            align="center"
          >
            <template #default="{ row }">
              <div class="percent-stepper">
                <span
                  class="percent-stepper__value"
                  :class="{ 'percent-stepper__value--empty': row.artifactPeerless2 === 0 }"
                >
                  {{ formatArtifactPeerless(row.artifactPeerless2) }}
                </span>
                <span class="percent-stepper__controls">
                  <el-button
                    :icon="ArrowUp"
                    :disabled="runtimeComplete || !canStepArtifactPeerless(row, 'artifactPeerless2', 1)"
                    size="small"
                    text
                    class="percent-stepper__button"
                    :aria-label="`提高 ${row.partName} 灵器无双2档位`"
                    @click.stop="stepArtifactPeerless(row, 'artifactPeerless2', 1)"
                  />
                  <el-button
                    :icon="ArrowDown"
                    :disabled="runtimeComplete || !canStepArtifactPeerless(row, 'artifactPeerless2', -1)"
                    size="small"
                    text
                    class="percent-stepper__button"
                    :aria-label="`降低 ${row.partName} 灵器无双2档位`"
                    @click.stop="stepArtifactPeerless(row, 'artifactPeerless2', -1)"
                  />
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            v-for="column in leadingStatColumns"
            :key="column.key"
            :prop="column.key"
            :label="formatStatColumnLabel(column)"
            :min-width="column.minWidth"
            align="right"
          >
            <template #default="{ row }">
              <div
                class="stat-edit-cell"
                :title="row[column.key] ? `双击编辑原始值：${getStatRawValue(row, 'common', column.key, row[column.key], column.baseRawValue)}` : '双击录入原始值'"
                @dblclick.stop="startStatCellEdit(artifact, row, 'common', column.key, row[column.key], column.baseRawValue)"
              >
                <el-input
                  v-if="isEditingStatCell(artifact, row, 'common', column.key)"
                  ref="statEditInputRef"
                  v-model="editingStatRawValue"
                  size="small"
                  class="stat-edit-input"
                  @blur="commitStatCellEdit(artifact, row, 'common', column.key, column.baseRawValue)"
                  @keydown.enter.prevent="commitStatCellEdit(artifact, row, 'common', column.key, column.baseRawValue)"
                  @keydown.esc.prevent="cancelStatCellEdit"
                />
                <span v-else class="empty-cell stat-edit-cell__display">{{ row[column.key] || '-' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            v-for="column in artifact.exclusiveStats"
            :key="column.key"
            :label="formatStatColumnLabel(column)"
            :min-width="column.minWidth"
            align="right"
          >
            <template #default="{ row }">
              <div
                class="stat-edit-cell"
                :title="row.exclusiveStats[column.key] ? `双击编辑原始值：${getStatRawValue(row, 'exclusive', column.key, row.exclusiveStats[column.key], column.baseRawValue)}` : '双击录入原始值'"
                @dblclick.stop="startStatCellEdit(artifact, row, 'exclusive', column.key, row.exclusiveStats[column.key], column.baseRawValue)"
              >
                <el-input
                  v-if="isEditingStatCell(artifact, row, 'exclusive', column.key)"
                  ref="statEditInputRef"
                  v-model="editingStatRawValue"
                  size="small"
                  class="stat-edit-input"
                  @blur="commitStatCellEdit(artifact, row, 'exclusive', column.key, column.baseRawValue)"
                  @keydown.enter.prevent="commitStatCellEdit(artifact, row, 'exclusive', column.key, column.baseRawValue)"
                  @keydown.esc.prevent="cancelStatCellEdit"
                />
                <span v-else class="empty-cell stat-edit-cell__display">{{ row.exclusiveStats[column.key] || '-' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            v-for="column in trailingStatColumns"
            :key="column.key"
            :prop="column.key"
            :label="formatStatColumnLabel(column)"
            :min-width="column.minWidth"
            align="right"
          >
            <template #default="{ row }">
              <div
                class="stat-edit-cell"
                :title="row[column.key] ? `双击编辑原始值：${getStatRawValue(row, 'common', column.key, row[column.key], column.baseRawValue)}` : '双击录入原始值'"
                @dblclick.stop="startStatCellEdit(artifact, row, 'common', column.key, row[column.key], column.baseRawValue)"
              >
                <el-input
                  v-if="isEditingStatCell(artifact, row, 'common', column.key)"
                  ref="statEditInputRef"
                  v-model="editingStatRawValue"
                  size="small"
                  class="stat-edit-input"
                  @blur="commitStatCellEdit(artifact, row, 'common', column.key, column.baseRawValue)"
                  @keydown.enter.prevent="commitStatCellEdit(artifact, row, 'common', column.key, column.baseRawValue)"
                  @keydown.esc.prevent="cancelStatCellEdit"
                />
                <span v-else class="empty-cell stat-edit-cell__display">{{ row[column.key] || '-' }}</span>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.stage-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 48px;
  padding: 2px 8px;
  border: 1px solid;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.spirit-artifact-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
  padding: 20px;
  background: #f5f7fa;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.page-title {
  margin: 0;
  color: #111827;
  font-size: 24px;
  font-weight: 600;
  line-height: 1.3;
}

.recognition-toolbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 10px;
  align-self: stretch;
  margin: -4px -20px 0;
  padding: 8px 20px 10px;
  border-bottom: 1px solid #e5e7eb;
  background: rgba(245, 247, 250, 0.96);
  backdrop-filter: blur(6px);
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.04);
}

.save-status {
  color: #64748b;
  font-size: 13px;
}

.runtime-time {
  color: #909399;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.market-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.market-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid #edf0f3;
}

.market-title-group {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}

.market-title {
  margin: 0;
  color: #0f172a;
  font-size: 17px;
  font-weight: 650;
  line-height: 1.3;
}

.market-currency {
  color: #64748b;
  font-size: 13px;
  font-weight: 600;
}

.market-table {
  width: max-content;
  min-width: fit-content;
}

.market-table :deep(.el-table__cell) {
  padding-top: 6px;
  padding-bottom: 6px;
}

.market-table :deep(.cell) {
  padding-left: 8px;
  padding-right: 8px;
  white-space: nowrap;
}

.market-item-name {
  color: #0f172a;
  font-weight: 600;
}

.market-empty {
  color: #94a3b8;
  font-size: 13px;
}

.storage-bag-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.storage-bag-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid #edf0f3;
}

.storage-bag-title {
  margin: 0;
  color: #0f172a;
  font-size: 17px;
  font-weight: 650;
  line-height: 1.3;
}

.storage-bag-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.storage-bag-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.storage-bag-item + .storage-bag-item {
  padding-top: 12px;
  border-top: 1px solid #edf0f3;
}

.storage-bag-item-heading {
  display: inline-flex;
  align-items: baseline;
  gap: 10px;
}

.storage-bag-item-title {
  color: #0f172a;
  font-size: 14px;
  font-weight: 650;
}

.storage-bag-quantity {
  color: #64748b;
  font-size: 13px;
  font-weight: 600;
}

.storage-bag-table {
  width: max-content;
  min-width: fit-content;
}

.storage-bag-table :deep(.el-table__cell) {
  padding-top: 6px;
  padding-bottom: 6px;
}

.storage-bag-table :deep(.cell) {
  padding-left: 8px;
  padding-right: 8px;
  white-space: nowrap;
}

.storage-bag-choice-name {
  color: #0f172a;
  font-weight: 600;
}

.storage-bag-choice-raw,
.storage-bag-empty {
  color: #94a3b8;
  font-size: 13px;
}

.artifact-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.artifact-heading {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #edf0f3;
}

.artifact-title {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  color: #0f172a;
  font-size: 19px;
  font-weight: 650;
  line-height: 1.3;
}

.artifact-order {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: #ecfdf5;
  color: #047857;
  font-size: 15px;
  font-weight: 700;
}

.table-wrap {
  width: 100%;
  overflow-x: auto;
}

.artifact-table {
  width: max-content;
  min-width: fit-content;
}

.artifact-table :deep(.el-table__cell) {
  padding-top: 7px;
  padding-bottom: 7px;
}

.artifact-table :deep(.cell) {
  padding-left: 4px;
  padding-right: 4px;
  white-space: nowrap;
  word-break: keep-all;
}

.artifact-table :deep(th.el-table__cell) {
  padding-top: 6px;
  padding-bottom: 6px;
}

.part-cell {
  color: #0f172a;
  font-weight: 600;
}

.empty-cell {
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}

.stat-edit-cell {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  min-width: 56px;
  min-height: 24px;
  font-variant-numeric: tabular-nums;
  cursor: text;
}

.stat-edit-cell__display {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  width: 100%;
  min-height: 24px;
}

.stat-edit-input {
  width: 76px;
}

.stat-edit-input :deep(.el-input__wrapper) {
  padding-left: 6px;
  padding-right: 6px;
}

.stat-edit-input :deep(.el-input__inner) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.integer-input {
  width: 72px;
}

.integer-input :deep(.el-input__wrapper) {
  padding-left: 6px;
}

.integer-input :deep(.el-input__inner) {
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.percent-stepper {
  display: inline-grid;
  grid-template-columns: 52px 22px;
  align-items: stretch;
  width: 74px;
  height: 24px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fff;
  overflow: hidden;
}

.percent-stepper__value {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #0f172a;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.percent-stepper__value--empty {
  color: #94a3b8;
}

.percent-stepper__controls {
  display: grid;
  grid-template-rows: 1fr 1fr;
  border-left: 1px solid #dcdfe6;
}

.percent-stepper__button {
  width: 22px;
  min-width: 22px;
  height: 12px;
  padding: 0;
  border-radius: 0;
  color: #606266;
}

.percent-stepper__button + .percent-stepper__button {
  margin-left: 0;
  border-top: 1px solid #dcdfe6;
}

.percent-stepper__button :deep(.el-icon) {
  font-size: 10px;
}

@media (max-width: 720px) {
  .spirit-artifact-page {
    padding: 12px;
  }

  .recognition-toolbar {
    margin-right: -12px;
    margin-left: -12px;
    padding-right: 12px;
    padding-left: 12px;
    overflow-x: auto;
  }

  .market-panel {
    padding: 12px;
  }

  .storage-bag-panel {
    padding: 12px;
  }

  .artifact-panel {
    padding: 12px;
  }

  .artifact-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>

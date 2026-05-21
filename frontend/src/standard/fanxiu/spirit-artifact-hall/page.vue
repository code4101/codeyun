<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { Aim, ArrowDown, ArrowUp } from '@element-plus/icons-vue';
import {
  getFanxiuSpiritArtifactHall,
  recognizeFanxiuSpiritArtifactAttributes,
  recognizeFanxiuSpiritArtifactMarket,
  recognizeFanxiuSpiritArtifactRanks,
  recognizeFanxiuSpiritArtifactStorageBag,
  saveFanxiuSpiritArtifactHall,
  type FanxiuSpiritArtifactAttributeRecognitionResponse,
  type FanxiuSpiritArtifactHallSnapshot,
  type FanxiuSpiritArtifactMarketRecognitionResponse,
  type FanxiuSpiritArtifactRankRecognitionResponse,
  type FanxiuSpiritArtifactStorageBagRecognitionResponse,
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
const artifactPeerlessSteps = [0, 25, 30];
const SAVE_DEBOUNCE_MS = 800;
const recognizedRankQualities = new Set(['red', 'blue_purple']);
const recognizedCommonStatKeyMap: Record<string, StatColumnKey> = {
  chaos_power: 'chaosPower',
  attack: 'attack',
  spirit_power: 'spiritPower',
  health: 'health',
  defense: 'defense',
};
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
    ...emptyStats,
  };
}

const recognizingRanks = ref(false);
const recognizingAttributes = ref(false);
const recognizingMarket = ref(false);
const recognizingStorageBag = ref(false);
const loading = ref(false);
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

function getStorageBagItemKey(item: Pick<SpiritArtifactStorageBagItem, 'title'>) {
  return normalizeStatText(item.title);
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
  return normalizedItems;
}

function snapshotToArtifacts(snapshot: FanxiuSpiritArtifactHallSnapshot): SpiritArtifact[] {
  const savedByName = new Map((snapshot.artifacts || []).map(artifact => [artifact.name, artifact]));
  return artifactSeeds.map((seed, artifactIndex) => {
    const savedArtifact = savedByName.get(seed.name);
    const savedRowsByPart = new Map((savedArtifact?.rows || []).map(row => [row.part_name, row]));
    return {
      order: artifactIndex + 1,
      name: seed.name,
      exclusiveStats: seed.exclusiveStats,
      rows: seed.parts.map((partName, partIndex) => {
        const savedRow = savedRowsByPart.get(partName);
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
            seed.exclusiveStats.map(column => [
              column.key,
              normalizeStatDisplayValue(savedExclusiveStats[column.key], column.baseRawValue),
            ]),
          ),
          exclusiveStatRawValues: Object.fromEntries(
            seed.exclusiveStats.map(column => [
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
    await saveFanxiuSpiritArtifactHall(artifactsToSnapshot());
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
    await nextTick();
    pageHydrated.value = true;
  } catch (error) {
    artifacts.value = createDefaultArtifacts();
    marketCurrencyCount.value = 0;
    marketItems.value = [];
    storageBagItems.value = [];
    await nextTick();
    pageHydrated.value = true;
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '读取灵器数据失败');
  } finally {
    loading.value = false;
  }
}

function applyRecognizedRanks(result: FanxiuSpiritArtifactRankRecognitionResponse) {
  const artifact = artifacts.value.find(item => item.name === result.artifact_name);
  if (!artifact) {
    ElMessage.warning(result.artifact_name ? `识别到 ${result.artifact_name}，但当前页面没有对应灵器` : '未识别到灵器名称');
    return false;
  }

  let updatedCount = 0;
  let skippedCount = 0;
  let unchangedCount = 0;
  for (const part of result.parts) {
    if (!recognizedRankQualities.has(part.quality)) {
      skippedCount += 1;
      continue;
    }
    const row = artifact.rows.find(item => item.partName === part.part_name);
    if (!row) {
      continue;
    }
    let rowUpdated = false;
    const recognizedRank = normalizeNonNegativeInteger(part.rank);
    const recognizedRealm = normalizeNonNegativeInteger(part.realm);
    if (recognizedRank > normalizeNonNegativeInteger(row.rank)) {
      row.rank = recognizedRank;
      rowUpdated = true;
    }
    if (recognizedRealm > normalizeNonNegativeInteger(row.realm)) {
      row.realm = recognizedRealm;
      rowUpdated = true;
    }
    if (rowUpdated) {
      updatedCount += 1;
    } else {
      unchangedCount += 1;
    }
  }

  if (updatedCount <= 0) {
    ElMessage.warning(`识别到 ${artifact.name}，但没有比当前更高的有效结果`);
    return false;
  }
  const skippedText = skippedCount > 0 ? `，跳过 ${skippedCount} 个无效颜色部位` : '';
  const unchangedText = unchangedCount > 0 ? `，忽略 ${unchangedCount} 个未升值部位` : '';
  ElMessage.success(`已回填 ${artifact.name} ${updatedCount} 个升值部位${skippedText}${unchangedText}`);
  return true;
}

function resetRecognizedAttributeFields(artifact: SpiritArtifact, row: SpiritArtifactPartRow) {
  row.artifactPeerless1 = 0;
  row.artifactPeerless2 = 0;
  row.chaosPower = '';
  row.attack = '';
  row.spiritPower = '';
  row.health = '';
  row.defense = '';
  row.statRawValues = createStatRawValues();
  row.exclusiveStats = createExclusiveStats(artifact.exclusiveStats);
  row.exclusiveStatRawValues = createExclusiveStatRawValues(artifact.exclusiveStats);
}

function normalizeRecognizedRawValue(rawValue: unknown) {
  const parsedRaw = parseRawAttributeValue(rawValue);
  return parsedRaw === null ? normalizeStatText(rawValue) : String(parsedRaw);
}

function applyRecognizedAttributeRawValues(
  artifact: SpiritArtifact,
  row: SpiritArtifactPartRow,
  result: FanxiuSpiritArtifactAttributeRecognitionResponse,
) {
  for (const attribute of result.attributes || []) {
    const rawValue = normalizeRecognizedRawValue(attribute.raw_value);
    if (!rawValue) {
      continue;
    }

    const commonKey = commonStatLabelKeyMap[attribute.label];
    if (commonKey) {
      row.statRawValues[commonKey] = rawValue;
      continue;
    }

    if (artifact.exclusiveStats.some(column => column.key === attribute.label)) {
      row.exclusiveStatRawValues[attribute.label] = rawValue;
    }
  }
}

function applyRecognizedAttributes(result: FanxiuSpiritArtifactAttributeRecognitionResponse) {
  const artifact = artifacts.value.find(item => item.name === result.artifact_name);
  if (!artifact) {
    ElMessage.warning(result.artifact_name ? `识别到 ${result.artifact_name}，但当前页面没有对应灵器` : '未识别到灵器名称');
    return false;
  }
  const row = artifact.rows.find(item => item.partName === result.part_name);
  if (!row) {
    ElMessage.warning(result.part_name ? `识别到 ${artifact.name}·${result.part_name}，但没有匹配到对应部位` : '未识别到灵器部位');
    return false;
  }

  resetRecognizedAttributeFields(artifact, row);

  let recognizedCount = 0;
  const peerless1 = normalizeArtifactPeerless(normalizeNonNegativeInteger(result.artifact_peerless_1));
  const peerless2 = normalizeArtifactPeerless(normalizeNonNegativeInteger(result.artifact_peerless_2));
  if (peerless1 > 0) {
    row.artifactPeerless1 = peerless1;
    recognizedCount += 1;
  }
  if (peerless2 > 0) {
    row.artifactPeerless2 = peerless2;
    recognizedCount += 1;
  }

  Object.entries(result.common_stats || {}).forEach(([backendKey, value]) => {
    const rowKey = recognizedCommonStatKeyMap[backendKey];
    if (!rowKey) {
      return;
    }
    row[rowKey] = normalizeStatText(value);
    recognizedCount += 1;
  });

  Object.entries(result.exclusive_stats || {}).forEach(([key, value]) => {
    if (!(key in row.exclusiveStats)) {
      return;
    }
    row.exclusiveStats[key] = normalizeStatText(value);
    recognizedCount += 1;
  });
  applyRecognizedAttributeRawValues(artifact, row, result);

  const suffix = recognizedCount > 0 ? `，回填 ${recognizedCount} 个有效属性` : '，未识别到有效属性';
  ElMessage.success(`已重置 ${artifact.name}·${row.partName}${suffix}`);
  return true;
}

async function recognizeRanks() {
  if (recognizingRanks.value) {
    return;
  }
  recognizingRanks.value = true;
  try {
    const result = await recognizeFanxiuSpiritArtifactRanks();
    if (!result.matched) {
      ElMessage.info(result.reason || '未识别到灵器画面');
      return;
    }
    if (applyRecognizedRanks(result)) {
      scheduleSave(true);
    }
  } catch (error) {
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '识别阶数失败');
  } finally {
    recognizingRanks.value = false;
  }
}

async function recognizeAttributes() {
  if (recognizingAttributes.value) {
    return;
  }
  recognizingAttributes.value = true;
  try {
    const result = await recognizeFanxiuSpiritArtifactAttributes();
    if (!result.matched) {
      ElMessage.info(result.reason || '未识别到灵器洗炼属性画面');
      return;
    }
    if (applyRecognizedAttributes(result)) {
      scheduleSave(true);
    }
  } catch (error) {
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '识别属性失败');
  } finally {
    recognizingAttributes.value = false;
  }
}

function applyRecognizedMarket(result: FanxiuSpiritArtifactMarketRecognitionResponse) {
  const recognizedItems = normalizeMarketItems(result.items);
  if (recognizedItems.length <= 0) {
    ElMessage.warning('未识别到可兑换灵器部件');
    return false;
  }

  const recognizedCurrencyCount = normalizeMarketCurrencyCount(result.market_currency_count);
  const currencyUpdated = marketCurrencyCount.value !== recognizedCurrencyCount;
  marketCurrencyCount.value = recognizedCurrencyCount;

  const existingByKey = new Map(marketItems.value.map(item => [getMarketItemKey(item), item]));
  let addedCount = 0;
  let updatedCount = 0;
  for (const item of recognizedItems) {
    const itemKey = getMarketItemKey(item);
    const existingItem = existingByKey.get(itemKey);
    if (existingItem) {
      if (existingItem.cost !== item.cost) {
        existingItem.cost = item.cost;
        updatedCount += 1;
      }
      continue;
    }
    marketItems.value.push({
      ...item,
      order: marketItems.value.length + 1,
    });
    existingByKey.set(itemKey, marketItems.value[marketItems.value.length - 1]);
    addedCount += 1;
  }
  marketItems.value = marketItems.value.map((item, index) => ({ ...item, order: index + 1 }));

  const unchangedCount = recognizedItems.length - addedCount - updatedCount;
  const updatedText = updatedCount > 0 ? `，更新 ${updatedCount} 项` : '';
  const unchangedText = unchangedCount > 0 ? `，已有 ${unchangedCount} 项` : '';
  const currencyText = `，元魄 ${marketCurrencyCount.value}`;
  ElMessage.success(`珍宝阁清单新增 ${addedCount} 项${updatedText}${unchangedText}${currencyText}`);
  return addedCount > 0 || updatedCount > 0 || currencyUpdated;
}

async function recognizeMarket() {
  if (recognizingMarket.value) {
    return;
  }
  recognizingMarket.value = true;
  try {
    const result = await recognizeFanxiuSpiritArtifactMarket();
    if (!result.matched) {
      ElMessage.info(result.reason || '未识别到珍宝阁灵器清单');
      return;
    }
    if (applyRecognizedMarket(result)) {
      scheduleSave(true);
    }
  } catch (error) {
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '识别珍宝阁失败');
  } finally {
    recognizingMarket.value = false;
  }
}

function applyRecognizedStorageBag(result: FanxiuSpiritArtifactStorageBagRecognitionResponse) {
  const recognizedItems = normalizeStorageBagItems(result.items);
  if (recognizedItems.length <= 0) {
    ElMessage.warning('未识别到储物袋自选类型');
    return false;
  }

  const existingByKey = new Map(storageBagItems.value.map(item => [getStorageBagItemKey(item), item]));
  let addedItemCount = 0;
  let updatedItemCount = 0;
  let addedChoiceCount = 0;

  for (const item of recognizedItems) {
    const itemKey = getStorageBagItemKey(item);
    const existingItem = existingByKey.get(itemKey);
    if (!existingItem) {
      storageBagItems.value.push({
        ...item,
        order: storageBagItems.value.length + 1,
        choices: item.choices.map((choice, index) => ({ ...choice, order: index + 1 })),
      });
      existingByKey.set(itemKey, storageBagItems.value[storageBagItems.value.length - 1]);
      addedItemCount += 1;
      addedChoiceCount += item.choices.length;
      continue;
    }

    let itemUpdated = false;
    const nextQuantity = normalizeStorageBagQuantity(item.quantity);
    if (nextQuantity > 0 && existingItem.quantity !== nextQuantity) {
      existingItem.quantity = nextQuantity;
      itemUpdated = true;
    }

    const existingChoiceKeys = new Set(existingItem.choices.map(choice => getStorageBagChoiceKey(choice)));
    for (const choice of item.choices) {
      const choiceKey = getStorageBagChoiceKey(choice);
      if (existingChoiceKeys.has(choiceKey)) {
        continue;
      }
      existingItem.choices.push({
        ...choice,
        order: existingItem.choices.length + 1,
      });
      existingChoiceKeys.add(choiceKey);
      addedChoiceCount += 1;
      itemUpdated = true;
    }
    existingItem.choices = existingItem.choices.map((choice, index) => ({ ...choice, order: index + 1 }));
    if (itemUpdated) {
      updatedItemCount += 1;
    }
  }

  storageBagItems.value = storageBagItems.value.map((item, index) => ({
    ...item,
    order: index + 1,
    choices: item.choices.map((choice, choiceIndex) => ({ ...choice, order: choiceIndex + 1 })),
  }));

  const updatedText = updatedItemCount > 0 ? `，更新 ${updatedItemCount} 个箱子` : '';
  ElMessage.success(`储物袋新增 ${addedItemCount} 个箱子，新增 ${addedChoiceCount} 个类型${updatedText}`);
  return addedItemCount > 0 || updatedItemCount > 0 || addedChoiceCount > 0;
}

async function recognizeStorageBag() {
  if (recognizingStorageBag.value) {
    return;
  }
  recognizingStorageBag.value = true;
  try {
    const result = await recognizeFanxiuSpiritArtifactStorageBag();
    if (!result.matched) {
      ElMessage.info(result.reason || '未识别到储物袋自选箱');
      return;
    }
    if (applyRecognizedStorageBag(result)) {
      scheduleSave(true);
    }
  } catch (error) {
    const anyError = error as any;
    ElMessage.error(anyError?.response?.data?.detail || anyError?.message || '识别储物袋失败');
  } finally {
    recognizingStorageBag.value = false;
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
  [artifacts, marketItems, marketCurrencyCount, storageBagItems],
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
      <h2 class="page-title">道具仓库 · 4 灵器</h2>
    </div>

    <div class="recognition-toolbar">
      <el-button
        type="primary"
        :icon="Aim"
        :loading="recognizingRanks"
        class="recognition-button"
        @click="recognizeRanks"
      >
        识别阶数
      </el-button>
      <el-button
        type="primary"
        plain
        :icon="Aim"
        :loading="recognizingAttributes"
        class="recognition-button"
        @click="recognizeAttributes"
      >
        识别属性
      </el-button>
      <span v-if="saving" class="save-status">保存中...</span>
    </div>

    <section class="market-panel">
      <div class="market-heading">
        <div class="market-title-group">
          <h3 class="market-title">仙市 / 珍宝阁</h3>
          <span class="market-currency">灵器铸形元魄：{{ marketCurrencyCount }}</span>
        </div>
        <el-button
          type="primary"
          plain
          :icon="Aim"
          :loading="recognizingMarket"
          size="small"
          class="market-restock-button"
          @click="recognizeMarket"
        >
          进货
        </el-button>
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
        <el-button
          type="primary"
          plain
          :icon="Aim"
          :loading="recognizingStorageBag"
          size="small"
          class="storage-bag-recognize-button"
          @click="recognizeStorageBag"
        >
          识别自选
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
                    :disabled="!canStepArtifactPeerless(row, 'artifactPeerless1', 1)"
                    size="small"
                    text
                    class="percent-stepper__button"
                    :aria-label="`提高 ${row.partName} 灵器无双档位`"
                    @click.stop="stepArtifactPeerless(row, 'artifactPeerless1', 1)"
                  />
                  <el-button
                    :icon="ArrowDown"
                    :disabled="!canStepArtifactPeerless(row, 'artifactPeerless1', -1)"
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
                    :disabled="!canStepArtifactPeerless(row, 'artifactPeerless2', 1)"
                    size="small"
                    text
                    class="percent-stepper__button"
                    :aria-label="`提高 ${row.partName} 灵器无双2档位`"
                    @click.stop="stepArtifactPeerless(row, 'artifactPeerless2', 1)"
                  />
                  <el-button
                    :icon="ArrowDown"
                    :disabled="!canStepArtifactPeerless(row, 'artifactPeerless2', -1)"
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

.recognition-button {
  min-width: 112px;
  font-weight: 600;
}

.save-status {
  color: #64748b;
  font-size: 13px;
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

.market-restock-button {
  flex: 0 0 auto;
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

.storage-bag-recognize-button {
  flex: 0 0 auto;
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

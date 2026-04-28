<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import type {
  FanxiuFormationEffectDetailImportItem,
  FanxiuFormationRequirementImportItem,
  FanxiuInventoryItem,
  FanxiuInventoryType,
  FanxiuMagicTreasureHallSnapshot,
} from '@/api/fanxiu';
import { getFanxiuMagicTreasureHall, importFanxiuFormationRequirementsFromOcr } from '@/api/fanxiu';
import { useUserStore } from '@/store/userStore';
import FormationSlotList from './FormationSlotList.vue';
import FormationRequirementList from './FormationRequirementList.vue';

type QualityFamily = '珍品' | '绝品' | '仙品' | '神品';
type QualityMatchMode = 'any' | 'family' | 'eq' | 'gte';
type NumberMatchMode = 'any' | 'eq' | 'gte';
type FormationRequirementKind = 'count' | 'rank_sum' | 'tags_each' | 'effect_count' | 'effect_ref' | 'formation_rank' | 'formation_rank_and';

interface FormationRequirementMatcher {
  namesAny: string[];
  type: FanxiuInventoryType | '';
  qualityMode: QualityMatchMode;
  qualityFamily: QualityFamily | '';
  qualityValue: number | null;
  rankMode: NumberMatchMode;
  rankValue: number | null;
}

interface FormationRequirement {
  id: string;
  text: string;
  effectText: string;
  effectDetail: string;
  parseError: string;
  kind: FormationRequirementKind;
  matcher: FormationRequirementMatcher;
  tags: string[];
  effectMarker: string;
  formationRankThreshold: number | null;
  nestedRequirement: ParsedRequirementDraft | null;
  threshold: number;
}

interface FormationSlot {
  itemId: string | null;
  locked: boolean;
}

interface FormationCard {
  id: string;
  presetKey: string | null;
  name: string;
  rank: number;
  remark: string;
  coreNames: string[];
  presetDefaultsInitialized: boolean;
  slots: FormationSlot[];
  requirements: FormationRequirement[];
}

interface FormationCardStoragePayload {
  version: 4;
  cards: FormationCard[];
}

interface LegacyFormationCardStoragePayload {
  version?: number;
  cards?: Array<{
    id?: string;
    templateId?: string;
    slots?: Array<{ itemId?: string | null; locked?: boolean }>;
  }>;
}

interface LegacyTemplateDraft {
  id: string;
  name: string;
  coreNames: string[];
  rawConditionsText: string;
}

interface LegacyTemplateStoragePayload {
  version?: number;
  templates?: Array<Partial<LegacyTemplateDraft> & { rawConditions?: string[] }>;
}

interface RequirementState {
  requirement: FormationRequirement;
  triggered: boolean;
  invalid: boolean;
  pending: boolean;
  progressText: string;
}

type ParsedRequirementDraft = {
  kind: FormationRequirementKind;
  matcher?: Partial<FormationRequirementMatcher>;
  tags?: string[];
  effectMarker?: string;
  formationRankThreshold?: number;
  nestedRequirement?: ParsedRequirementDraft | null;
  threshold: number;
};

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

const QUALITY_FAMILY_OPTIONS: Array<{ value: QualityFamily; label: string }> = [
  { value: '珍品', label: '珍品' },
  { value: '绝品', label: '绝品' },
  { value: '仙品', label: '仙品' },
  { value: '神品', label: '神品' },
];

const TYPE_OPTIONS: Array<{ value: FanxiuInventoryType; label: string }> = [
  { value: '攻击', label: '攻击' },
  { value: '防御', label: '防御' },
  { value: '灵力', label: '灵力' },
  { value: '辅助', label: '辅助' },
];

const REQUIREMENT_KIND_OPTIONS: Array<{ value: FormationRequirementKind; label: string }> = [
  { value: 'count', label: '数量达到' },
  { value: 'rank_sum', label: '合计阶数达到' },
  { value: 'tags_each', label: '词缀各达到' },
];

const QUALITY_MODE_OPTIONS: Array<{ value: QualityMatchMode; label: string }> = [
  { value: 'any', label: '不限品质' },
  { value: 'family', label: '属于品质' },
  { value: 'eq', label: '等于品质' },
  { value: 'gte', label: '至少品质' },
];

const RANK_MODE_OPTIONS: Array<{ value: NumberMatchMode; label: string }> = [
  { value: 'any', label: '不限阶级' },
  { value: 'eq', label: '等于阶级' },
  { value: 'gte', label: '至少阶级' },
];

const DEFAULT_SORT_PROGRAM = ['quality', 'rank', 'shenlian', 'date'] as const;
const COMMON_TAG_OPTIONS = ['持续', '召唤', '追击', '灵犀'];
const FORMATION_STORAGE_VERSION = 6;

const PRESET_FORMATIONS: Array<{
  key: string;
  name: string;
  coreNames: string[];
  rawConditions: string[];
}> = [
  {
    key: 'baji-fenguang',
    name: '八极分光阵',
    coreNames: ['狼首玉如意', '朱雀环'],
    rawConditions: [
      '入阵1个绝品法宝',
      '入阵1个仙品法宝',
      '入阵2个仙品法宝',
      '入阵4个绝品2阶法宝',
      '入阵8个法宝',
      '入阵8个绝品法宝',
      '入阵法宝的阶数合计12阶',
      '入阵绝品以上法宝合计16阶',
      '入阵绝品以上法宝合计24阶',
      '入阵绝品以上法宝合计30阶',
    ],
  },
  {
    key: 'diandao-wuxing',
    name: '颠倒五行阵',
    coreNames: ['乌龙夺', '引魂钟'],
    rawConditions: [
      '入阵1个仙品法宝',
      '入阵5个仙品法宝',
      '入阵1个三阶攻击法宝',
      '入阵1个仙品攻击法宝',
      '入阵1个仙品三星法宝',
      '入阵2个绝品三阶法宝',
      '入阵1个四十九阶以上法宝',
      '入阵法宝的合计阶数达到20阶',
      '入阵带灵犀和追击词缀的法宝各1个',
      '入阵带持续和召唤词缀的法宝各1个',
    ],
  },
  {
    key: 'beidou-liangyi',
    name: '北斗两仪阵',
    coreNames: ['古开山斧', '三尖两刃'],
    rawConditions: [
      '入阵1个仙品法宝',
      '入阵1个仙品三星法宝',
      '入阵1个仙品四星法宝',
      '入阵2个仙品四星法宝',
      '入阵4个神品一星法宝',
      '入阵仙品以上法宝合计18阶',
      '入阵法宝的阶数合计24阶',
      '入阵仙品以上法宝合计27阶',
      '入阵仙品以上法宝合计36阶',
      '入阵2个49阶以上法宝',
    ],
  },
  {
    key: 'miaoyi-zhenlong',
    name: '妙弈珍珑棋阵',
    coreNames: ['妙弈珍珑棋'],
    rawConditions: [
      '上阵2个仙品法宝',
      '上阵4个仙品法宝',
      '上阵6个仙品法宝',
      '上阵8个仙品法宝',
      '入阵仙品以上法宝合计五十阶',
      '入阵仙品以上法宝合计六十阶',
      '入阵1个仙品白玉棋石',
      '入阵1个仙品黑邃棋石',
      '入阵1个神品白玉棋石或黑邃棋石',
      '入阵1个神品妙弈珍珑棋',
    ],
  },
  {
    key: 'wanmu-zhenling',
    name: '万木真灵阵',
    coreNames: ['观天镜', '映影晶'],
    rawConditions: [
      '入阵绝品以上法宝合计二十八阶',
      '入阵2个仙品以上的防御型法宝',
      '入阵2个仙品以上的灵力型法宝',
      '入阵仙品以上法宝合计二十阶',
      '入阵仙品观天镜',
      '入阵仙品映影晶',
      '入阵2个仙品四星以上灵力型法宝',
      '入阵2个神品一星法宝',
      '入阵2个三十七阶以上法宝',
      '入阵3个四十九阶以上法宝',
    ],
  },
];

const userStore = useUserStore();
const loading = ref(false);
const loadError = ref('');
const storageReady = ref(false);
const snapshot = ref<FanxiuMagicTreasureHallSnapshot>({
  fabao: [],
  xiantiangubao: [],
  houtiangubao: [],
});
const cards = ref<FormationCard[]>([]);
const pendingRequirementImportCardId = ref('');
const importingRequirementCardId = ref('');

const storageKey = computed(() => {
  const scope = userStore.user?.id ?? userStore.user?.username ?? 'fanxiu';
  return `fanxiu:magic-treasure-formations:${scope}`;
});

const legacyTemplateStorageKey = computed(() => {
  const scope = userStore.user?.id ?? userStore.user?.username ?? 'fanxiu';
  return `fanxiu:magic-treasure-formation-templates:${scope}`;
});

const inventoryItems = computed<FanxiuInventoryItem[]>(() => [
  ...snapshot.value.fabao,
  ...snapshot.value.xiantiangubao,
  ...snapshot.value.houtiangubao,
]);

const inventoryMap = computed(() => new Map(inventoryItems.value.map(item => [item.id, item])));

const tagOptions = computed(() => {
  const tags = new Set(COMMON_TAG_OPTIONS);
  for (const item of inventoryItems.value) {
    for (const tag of getItemTags(item)) tags.add(tag);
    for (const card of cards.value) {
      for (const requirement of card.requirements) {
        for (const tag of requirement.tags) {
          if (tag) tags.add(tag);
        }
      }
    }
  }
  return [...tags].sort((left, right) => left.localeCompare(right, 'zh-CN'));
});

const usedItemIds = computed(() => {
  const used = new Set<string>();
  for (const card of cards.value) {
    for (const slot of card.slots) {
      if (slot.itemId) used.add(slot.itemId);
    }
  }
  return used;
});

const unusedCount = computed(() => Math.max(inventoryItems.value.length - usedItemIds.value.size, 0));

watch(cards, () => {
  if (!storageReady.value || typeof window === 'undefined') return;
  const payload: FormationCardStoragePayload = {
    version: FORMATION_STORAGE_VERSION,
    cards: cards.value,
  };
  window.localStorage.setItem(storageKey.value, JSON.stringify(payload));
}, { deep: true });

watch(storageKey, async () => {
  if (!storageReady.value) return;
  const hasStoredCards = loadCardsFromStorage();
  if (!hasStoredCards) {
    cards.value = buildDefaultCards();
  }
  reconcileCardsWithInventory();
}, { flush: 'post' });

onMounted(async () => {
  window.addEventListener('paste', handleWindowPaste);
  const hasStoredCards = loadCardsFromStorage();
  await loadInventory();
  if (!hasStoredCards) {
    cards.value = buildDefaultCards();
  }
  reconcileCardsWithInventory();
  storageReady.value = true;
});

onBeforeUnmount(() => {
  window.removeEventListener('paste', handleWindowPaste);
});

async function loadInventory() {
  loading.value = true;
  loadError.value = '';
  try {
    snapshot.value = await getFanxiuMagicTreasureHall();
  } catch (error: any) {
    loadError.value = error?.message || '加载法宝仓库失败';
  } finally {
    loading.value = false;
  }
}

function loadCardsFromStorage() {
  if (typeof window === 'undefined') return false;
  const raw = window.localStorage.getItem(storageKey.value);
  if (!raw) {
    cards.value = [];
    return false;
  }

  try {
    const parsed = JSON.parse(raw) as FormationCardStoragePayload | LegacyFormationCardStoragePayload;
    if (
      parsed
      && typeof parsed === 'object'
      && Number(parsed.version) === FORMATION_STORAGE_VERSION
      && Array.isArray(parsed.cards)
    ) {
      cards.value = parsed.cards.map(normalizeCard);
      return true;
    }

    if (
      parsed
      && typeof parsed === 'object'
      && Number(parsed.version) >= 3
      && Array.isArray(parsed.cards)
    ) {
      cards.value = migrateFormationCards(parsed.cards.map(normalizeCard));
      return true;
    }

    const legacyCards = migrateLegacyCards(parsed);
    if (legacyCards.length) {
      cards.value = migrateFormationCards(legacyCards);
      return true;
    }
  } catch {
    cards.value = [];
  }

  cards.value = [];
  return false;
}

function migrateLegacyCards(payload: LegacyFormationCardStoragePayload) {
  const rawCards = Array.isArray(payload?.cards) ? payload.cards : [];
  if (!rawCards.length) return [];

  const legacyTemplateMap = loadLegacyTemplateMap();
  return rawCards.map(rawCard => {
    const templateId = String(rawCard.templateId || '').trim();
    const template = legacyTemplateMap.get(templateId);
    return normalizeCard({
      id: rawCard.id,
      presetKey: PRESET_FORMATIONS.some(item => item.key === templateId) ? templateId : null,
      name: template?.name || '未命名阵图',
      coreNames: template?.coreNames || [],
      slots: rawCard.slots,
      requirements: template ? buildRequirementsFromRawConditions(template.rawConditionsText) : [],
    });
  });
}

function getPresetFormation(key: string) {
  return PRESET_FORMATIONS.find(item => item.key === key) || null;
}

function getActiveRequirementTexts(card: FormationCard) {
  return card.requirements
    .map(requirement => normalizeRequirementMergeKey(requirement.text))
    .filter(Boolean);
}

function getPresetRequirementMatchScore(card: FormationCard, presetKey: string) {
  const preset = getPresetFormation(presetKey);
  if (!preset) return 0;
  const activeTexts = new Set(getActiveRequirementTexts(card));
  if (!activeTexts.size) return 0;
  let score = 0;
  for (const rawCondition of preset.rawConditions) {
    if (activeTexts.has(normalizeRequirementMergeKey(rawCondition))) {
      score += 1;
    }
  }
  return score;
}

function buildPresetRequirementsByKey(presetKey: string) {
  const preset = getPresetFormation(presetKey);
  return preset ? buildRequirementsFromRawConditions(preset.rawConditions.join('\n')) : [];
}

function migrateFormationCards(sourceCards: FormationCard[]) {
  const cardsToMigrate = sourceCards.map(card => normalizeCard(card));
  const diandaoCard = cardsToMigrate.find(card => card.presetKey === 'diandao-wuxing');
  const beidouCard = cardsToMigrate.find(card => card.presetKey === 'beidou-liangyi');
  if (!diandaoCard || !beidouCard) return cardsToMigrate;

  const diandaoAsDiandao = getPresetRequirementMatchScore(diandaoCard, 'diandao-wuxing');
  const diandaoAsBeidou = getPresetRequirementMatchScore(diandaoCard, 'beidou-liangyi');
  const beidouAsBeidou = getPresetRequirementMatchScore(beidouCard, 'beidou-liangyi');
  const beidouAsDiandao = getPresetRequirementMatchScore(beidouCard, 'diandao-wuxing');

  const diandaoLooksLikeBeidou = diandaoAsBeidou >= 4 && diandaoAsBeidou > diandaoAsDiandao;
  const beidouLooksLikeDiandao = beidouAsDiandao >= 4 && beidouAsDiandao > beidouAsBeidou;

  if (diandaoLooksLikeBeidou && beidouLooksLikeDiandao) {
    const swappedRequirements = diandaoCard.requirements;
    diandaoCard.requirements = beidouCard.requirements;
    beidouCard.requirements = swappedRequirements;
    return cardsToMigrate;
  }

  const beidouHasMeaningfulData = getActiveRequirementTexts(beidouCard).length >= 3;
  if (diandaoLooksLikeBeidou && !beidouHasMeaningfulData) {
    beidouCard.requirements = diandaoCard.requirements;
    diandaoCard.requirements = buildPresetRequirementsByKey('diandao-wuxing');
  }

  const miaoyiIndex = cardsToMigrate.findIndex(card => card.presetKey === 'miaoyi-zhenlong');
  const wanmuIndex = cardsToMigrate.findIndex(card => card.presetKey === 'wanmu-zhenling');
  if (miaoyiIndex >= 0 && wanmuIndex >= 0 && miaoyiIndex > wanmuIndex) {
    const [miaoyiCard] = cardsToMigrate.splice(miaoyiIndex, 1);
    if (miaoyiCard) {
      cardsToMigrate.splice(wanmuIndex, 0, miaoyiCard);
    }
  }

  return cardsToMigrate;
}

function loadLegacyTemplateMap() {
  const defaults = new Map(
    PRESET_FORMATIONS.map(template => [template.key, {
      id: template.key,
      name: template.name,
      coreNames: [...template.coreNames],
      rawConditionsText: template.rawConditions.join('\n'),
    } satisfies LegacyTemplateDraft]),
  );
  if (typeof window === 'undefined') return defaults;

  const raw = window.localStorage.getItem(legacyTemplateStorageKey.value);
  if (!raw) return defaults;

  try {
    const parsed = JSON.parse(raw) as LegacyTemplateStoragePayload;
    const templates = Array.isArray(parsed?.templates) ? parsed.templates : [];
    for (const template of templates) {
      const normalized = normalizeLegacyTemplateDraft(template);
      defaults.set(normalized.id, normalized);
    }
  } catch {
    return defaults;
  }

  return defaults;
}

function normalizeLegacyTemplateDraft(
  raw: Partial<LegacyTemplateDraft> & { rawConditions?: string[] },
): LegacyTemplateDraft {
  const rawConditionsText = typeof raw.rawConditionsText === 'string'
    ? raw.rawConditionsText
    : Array.isArray(raw.rawConditions)
      ? raw.rawConditions.join('\n')
      : '';

  return {
    id: String(raw.id || buildCardId()),
    name: String(raw.name || '未命名阵图').trim() || '未命名阵图',
    coreNames: Array.isArray(raw.coreNames)
      ? raw.coreNames.map(item => String(item || '').trim()).filter(Boolean)
      : [],
    rawConditionsText,
  };
}

function normalizeCard(raw: Partial<FormationCard>): FormationCard {
  const rank = Number(raw.rank);
  const slots = Array.from({ length: 8 }, (_, index) => {
    const slot = raw.slots?.[index];
    return {
      itemId: typeof slot?.itemId === 'string' ? slot.itemId : null,
      locked: Boolean(slot?.locked),
    };
  });

  return {
    id: String(raw.id || buildCardId()),
    presetKey: typeof raw.presetKey === 'string' && raw.presetKey.trim() ? raw.presetKey : null,
    name: String(raw.name || '未命名阵图').trim() || '未命名阵图',
    rank: Number.isFinite(rank) ? Math.max(0, Math.floor(rank)) : 1,
    remark: typeof raw.remark === 'string' ? raw.remark.trim() : '',
    coreNames: Array.isArray(raw.coreNames)
      ? [...new Set(raw.coreNames.map(item => String(item || '').trim()).filter(Boolean))]
      : getPresetFormation(typeof raw.presetKey === 'string' ? raw.presetKey : '')?.coreNames || [],
    presetDefaultsInitialized: Boolean((raw as any).presetDefaultsInitialized),
    slots,
    requirements: Array.isArray(raw.requirements)
      ? raw.requirements.map(normalizeRequirement)
      : [],
  };
}

function stringifyRequirement(raw: Partial<FormationRequirement>) {
  const kind: FormationRequirementKind = raw.kind === 'rank_sum'
    || raw.kind === 'tags_each'
    || raw.kind === 'effect_count'
    || raw.kind === 'effect_ref'
    || raw.kind === 'formation_rank'
    || raw.kind === 'formation_rank_and'
    ? raw.kind
    : 'count';
  if (kind === 'formation_rank') {
    const formationRankThreshold = Math.max(1, Number(raw.formationRankThreshold || 1) || 1);
    return `阵法神通达到${formationRankThreshold}阶`;
  }
  if (kind === 'formation_rank_and') {
    const formationRankThreshold = Math.max(1, Number(raw.formationRankThreshold || 1) || 1);
    const nestedText = raw.nestedRequirement ? stringifyParsedRequirementDraft(raw.nestedRequirement) : '';
    return nestedText ? `阵法神通达到${formationRankThreshold}阶并且${nestedText}` : '';
  }
  if (kind === 'effect_count') {
    const threshold = Math.max(1, Number(raw.threshold || 1) || 1);
    return `点亮${threshold}条件法效果激活技能`;
  }

  if (kind === 'effect_ref') {
    const marker = normalizeEffectMarkerToken(raw.effectMarker);
    return marker ? `点亮阵法效果${marker}激活技能` : '';
  }

  if (kind === 'tags_each') {
    const tags = Array.isArray(raw.tags)
      ? raw.tags.map(item => String(item || '').trim()).filter(Boolean)
      : [];
    if (!tags.length) return '';
    const threshold = Math.max(1, Number(raw.threshold || 1) || 1);
    return `入阵带${tags.join('和')}词缀的法宝各${threshold}个`;
  }

  const matcher = normalizeRequirementMatcher(raw.matcher);
  const qualityText = matcher.qualityMode === 'family'
    ? matcher.qualityFamily
    : matcher.qualityMode === 'eq'
      ? getQualityLabel(matcher.qualityValue)
      : matcher.qualityMode === 'gte'
        ? `${getQualityLabel(matcher.qualityValue)}以上`
        : '';
  const rankText = matcher.rankMode === 'eq'
    ? `${matcher.rankValue ?? ''}阶`
    : matcher.rankMode === 'gte'
      ? `${matcher.rankValue ?? ''}阶以上`
      : '';
  const typeText = matcher.type ? `${matcher.type}` : '';
  const nameText = matcher.namesAny.length ? matcher.namesAny.join('或') : '';
  const matcherSuffix = nameText ? nameText : typeText ? `${typeText}法宝` : '法宝';
  const subjectText = `${qualityText}${rankText}${matcherSuffix}`;
  const threshold = Math.max(1, Number(raw.threshold || 1) || 1);

  if (kind === 'rank_sum') {
    if (!qualityText && !rankText && !typeText && !nameText) {
      return `入阵法宝的阶数合计${threshold}阶`;
    }
    return `入阵${subjectText}合计${threshold}阶`;
  }

  return `入阵${threshold}个${subjectText}`;
}

function stringifyParsedRequirementDraft(raw: ParsedRequirementDraft) {
  return stringifyRequirement(raw as Partial<FormationRequirement>);
}

function normalizeRequirement(raw: Partial<FormationRequirement>): FormationRequirement {
  const text = typeof raw.text === 'string'
    ? normalizeRequirementRawText(raw.text)
    : stringifyRequirement(raw);
  const parsed = text ? parseRequirementText(text) : null;
  const kind: FormationRequirementKind = parsed?.kind || (
    raw.kind === 'rank_sum'
    || raw.kind === 'tags_each'
    || raw.kind === 'effect_count'
    || raw.kind === 'effect_ref'
    || raw.kind === 'formation_rank'
    || raw.kind === 'formation_rank_and'
      ? raw.kind
      : 'count'
  );
  return {
    id: String(raw.id || buildRequirementId()),
    text,
    effectText: normalizeRequirementEffectText(raw.effectText),
    effectDetail: normalizeRequirementEffectDetail(raw.effectDetail),
    parseError: text && !parsed ? '无法解析' : '',
    kind,
    matcher: kind === 'tags_each' || kind === 'effect_count' || kind === 'effect_ref' || kind === 'formation_rank' || kind === 'formation_rank_and'
      ? createDefaultMatcher()
      : normalizeRequirementMatcher(parsed?.matcher),
    tags: kind === 'tags_each' && Array.isArray(parsed?.tags)
      ? [...new Set(parsed.tags.map(item => String(item || '').trim()).filter(Boolean))]
      : [],
    effectMarker: kind === 'effect_ref'
      ? normalizeEffectMarkerToken(parsed?.effectMarker || raw.effectMarker)
      : '',
    formationRankThreshold: kind === 'formation_rank' || kind === 'formation_rank_and'
      ? Math.max(1, Number(parsed?.formationRankThreshold || raw.formationRankThreshold || 1) || 1)
      : null,
    nestedRequirement: kind === 'formation_rank_and'
      ? normalizeParsedRequirementDraft(parsed?.nestedRequirement || raw.nestedRequirement || null)
      : null,
    threshold: Math.max(1, Number(parsed?.threshold || 1) || 1),
  };
}

function normalizeParsedRequirementDraft(raw: ParsedRequirementDraft | null | undefined): ParsedRequirementDraft | null {
  if (!raw) return null;
  if (raw.kind === 'formation_rank') {
    return {
      kind: 'formation_rank',
      formationRankThreshold: Math.max(1, Number(raw.formationRankThreshold || 1) || 1),
      threshold: 1,
    };
  }
  if (raw.kind === 'formation_rank_and') {
    return null;
  }
  if (raw.kind === 'tags_each') {
    const tags = Array.isArray(raw.tags)
      ? [...new Set(raw.tags.map(item => String(item || '').trim()).filter(Boolean))]
      : [];
    if (!tags.length) return null;
    return {
      kind: 'tags_each',
      tags,
      threshold: Math.max(1, Number(raw.threshold || 1) || 1),
    };
  }
  if (raw.kind === 'effect_count') {
    return {
      kind: 'effect_count',
      threshold: Math.max(1, Number(raw.threshold || 1) || 1),
    };
  }
  if (raw.kind === 'effect_ref') {
    const effectMarker = normalizeEffectMarkerToken(raw.effectMarker);
    if (!effectMarker) return null;
    return {
      kind: 'effect_ref',
      effectMarker,
      threshold: 1,
    };
  }
  return {
    kind: raw.kind === 'rank_sum' ? 'rank_sum' : 'count',
    matcher: normalizeRequirementMatcher(raw.matcher),
    threshold: Math.max(1, Number(raw.threshold || 1) || 1),
  };
}

function normalizeRequirementMatcher(raw?: Partial<FormationRequirementMatcher>): FormationRequirementMatcher {
  const qualityMode: QualityMatchMode = raw?.qualityMode === 'family' || raw?.qualityMode === 'eq' || raw?.qualityMode === 'gte'
    ? raw.qualityMode
    : 'any';
  const rankMode: NumberMatchMode = raw?.rankMode === 'eq' || raw?.rankMode === 'gte'
    ? raw.rankMode
    : 'any';
  return {
    namesAny: Array.isArray(raw?.namesAny)
      ? [...new Set(raw.namesAny.map(item => String(item || '').trim()).filter(Boolean))]
      : [],
    type: raw?.type === '攻击' || raw?.type === '防御' || raw?.type === '灵力' || raw?.type === '辅助'
      ? raw.type
      : '',
    qualityMode,
    qualityFamily: raw?.qualityFamily === '珍品' || raw?.qualityFamily === '绝品' || raw?.qualityFamily === '仙品' || raw?.qualityFamily === '神品'
      ? raw.qualityFamily
      : '',
    qualityValue: Number.isInteger(raw?.qualityValue) ? Number(raw.qualityValue) : null,
    rankMode,
    rankValue: Number.isInteger(raw?.rankValue) ? Number(raw.rankValue) : null,
  };
}

function createDefaultMatcher(): FormationRequirementMatcher {
  return {
    namesAny: [],
    type: '',
    qualityMode: 'any',
    qualityFamily: '',
    qualityValue: null,
    rankMode: 'any',
    rankValue: null,
  };
}

function createDefaultRequirement(): FormationRequirement {
  return {
    id: buildRequirementId(),
    text: '',
    effectText: '',
    effectDetail: '',
    parseError: '',
    kind: 'count',
    matcher: createDefaultMatcher(),
    tags: [],
    effectMarker: '',
    formationRankThreshold: null,
    nestedRequirement: null,
    threshold: 1,
  };
}

function buildDefaultCards() {
  return PRESET_FORMATIONS.map(template => createPresetCard(template));
}

function createPresetCard(
  template: { key: string; name: string; coreNames: string[]; rawConditions: string[] },
) {
  const defaultSlots = buildPresetDefaultSlots(template.key);
  return normalizeCard({
    id: `formation-${template.key}`,
    presetKey: template.key,
    name: template.name,
    rank: 1,
    remark: '',
    coreNames: template.coreNames,
    presetDefaultsInitialized: defaultSlots.some(slot => Boolean(slot.itemId)),
    slots: defaultSlots,
    requirements: buildRequirementsFromRawConditions(template.rawConditions.join('\n')),
  });
}

function reconcileCardsWithInventory() {
  const validItemIds = new Set(inventoryItems.value.map(item => item.id));
  cards.value = cards.value.map(card => {
    const nextCard = {
      ...card,
      slots: card.slots.map(slot => ({
        itemId: slot.itemId && validItemIds.has(slot.itemId) ? slot.itemId : null,
        locked: slot.itemId && validItemIds.has(slot.itemId) ? Boolean(slot.locked) : false,
      })),
    };

    if (!nextCard.presetDefaultsInitialized && nextCard.presetKey) {
      const hasFilledSlots = nextCard.slots.some(slot => Boolean(slot.itemId));
      if (hasFilledSlots) {
        nextCard.presetDefaultsInitialized = true;
      } else {
        const presetSlots = buildPresetDefaultSlots(nextCard.presetKey);
        if (presetSlots.some(slot => Boolean(slot.itemId))) {
          nextCard.slots = presetSlots;
          nextCard.presetDefaultsInitialized = true;
        }
      }
    }

    return nextCard;
  });
}

function buildPresetDefaultSlots(presetKey: string) {
  const preset = getPresetFormation(presetKey);
  const slots = Array.from({ length: 8 }, () => ({ itemId: null, locked: false }));
  if (!preset?.coreNames.length) return slots;

  const usedIds = new Set<string>();
  const sortedInventory = [...inventoryItems.value].sort(compareMagicTreasureItems);
  preset.coreNames.forEach((name, index) => {
    if (index >= slots.length) return;
    const matchedItem = sortedInventory.find(item => item.name === name && !usedIds.has(item.id));
    if (!matchedItem) return;
    usedIds.add(matchedItem.id);
    slots[index] = { itemId: matchedItem.id, locked: true };
  });
  return slots;
}

function buildCardId() {
  return `formation-${Math.random().toString(36).slice(2, 10)}`;
}

function buildRequirementId() {
  return `formation-requirement-${Math.random().toString(36).slice(2, 10)}`;
}

function addBlankCard() {
  const customCount = cards.value.filter(card => !card.presetKey).length + 1;
  cards.value = [
    ...cards.value,
    normalizeCard({
      id: buildCardId(),
      presetKey: null,
      name: `新阵图${customCount}`,
      rank: 1,
      remark: '',
      coreNames: [],
      presetDefaultsInitialized: true,
      requirements: [],
    }),
  ];
}

function removeCard(cardId: string) {
  cards.value = cards.value.filter(card => card.id !== cardId);
  if (pendingRequirementImportCardId.value === cardId) {
    pendingRequirementImportCardId.value = '';
  }
  if (importingRequirementCardId.value === cardId) {
    importingRequirementCardId.value = '';
  }
}

function addRequirement(cardId: string) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  card.requirements.push(createDefaultRequirement());
}

function removeRequirement(cardId: string, requirementId: string) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  card.requirements = card.requirements.filter(requirement => requirement.id !== requirementId);
}

function moveRequirement(cardId: string, oldIndex: number, newIndex: number) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  if (
    oldIndex < 0 ||
    newIndex < 0 ||
    oldIndex >= card.requirements.length ||
    newIndex >= card.requirements.length ||
    oldIndex === newIndex
  ) {
    return;
  }
  const nextRequirements = [...card.requirements];
  const [movedRequirement] = nextRequirements.splice(oldIndex, 1);
  if (!movedRequirement) return;
  nextRequirements.splice(newIndex, 0, movedRequirement);
  card.requirements = nextRequirements;
}

function updateRequirementText(cardId: string, requirementId: string, text: string) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  const requirement = card.requirements.find(entry => entry.id === requirementId);
  if (!requirement) return;
  requirement.text = text;
  syncRequirementText(requirement);
}

function syncRequirementText(requirement: FormationRequirement) {
  const next = normalizeRequirement({
    id: requirement.id,
    text: requirement.text,
    effectText: requirement.effectText,
    effectDetail: requirement.effectDetail,
  });
  requirement.text = next.text;
  requirement.parseError = next.parseError;
  requirement.kind = next.kind;
  requirement.matcher = next.matcher;
  requirement.tags = next.tags;
  requirement.threshold = next.threshold;
}

function updateRequirementEffectText(cardId: string, requirementId: string, effectText: string) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  const requirement = card.requirements.find(entry => entry.id === requirementId);
  if (!requirement) return;
  requirement.effectText = normalizeRequirementEffectText(effectText);
}

function updateRequirementEffectDetail(cardId: string, requirementId: string, effectDetail: string) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  const requirement = card.requirements.find(entry => entry.id === requirementId);
  if (!requirement) return;
  requirement.effectDetail = normalizeRequirementEffectDetail(effectDetail);
}

function normalizeRequirementMergeKey(text: string) {
  return normalizeRequirementRawText(text).replace(/\s+/g, '').trim();
}

function normalizeRequirementRawText(text: string | null | undefined) {
  return String(text || '')
    .trim()
    .replace(/\s*[（(]?\s*\d+\s*\/\s*\d+\s*[)）]?\s*$/, '')
    .trim();
}

function normalizeEffectMarkerToken(text: string | null | undefined) {
  const normalized = String(text || '').trim();
  if (!normalized) return '';
  const markerMatch = normalized.match(/[【\[]([^】\]]+)[】\]]/);
  if (markerMatch?.[1]) {
    return `【${markerMatch[1].trim()}】`;
  }
  return normalized;
}

function normalizeRequirementEffectText(text: string | null | undefined) {
  const normalized = String(text || '').trim();
  if (!normalized) return '';
  return normalized
    .replace(/[［\[]/g, '【')
    .replace(/[］\]]/g, '】')
    .replace(/\s*[:：]\s*/g, '')
    .replace(/【\s*/g, '【')
    .replace(/\s*】/g, '】')
    .trim();
}

function normalizeRequirementEffectDetail(text: string | null | undefined) {
  const lines = String(text || '')
    .split(/\r?\n+/)
    .map(line => line.trim())
    .filter(Boolean);
  return [...new Set(lines)].join('\n');
}

function mergeRequirementEffectDetail(left: string, right: string) {
  const lines = [
    ...normalizeRequirementEffectDetail(left).split('\n'),
    ...normalizeRequirementEffectDetail(right).split('\n'),
  ].map(line => line.trim()).filter(Boolean);
  return [...new Set(lines)].join('\n');
}

function normalizeEffectNameToken(text: string | null | undefined) {
  return normalizeRequirementEffectText(text)
    .replace(/^(?:名字|效果)\s*/, '')
    .replace(/^(?:【[^】]+】)+/, '')
    .trim();
}

function normalizeEffectLookupKey(text: string | null | undefined) {
  return normalizeEffectNameToken(text)
    .replace(/[【】［］\[\]（）()·•・:：\s]/g, '')
    .trim();
}

function effectNameMatches(left: string, right: string) {
  const leftKey = normalizeEffectLookupKey(left);
  const rightKey = normalizeEffectLookupKey(right);
  if (!leftKey || !rightKey) return false;
  return leftKey === rightKey || leftKey.includes(rightKey) || rightKey.includes(leftKey);
}

function extractEffectNameTokens(effectText: string) {
  const tokens: string[] = [];
  for (const part of String(effectText || '').split(/[；;]/)) {
    const normalized = normalizeEffectNameToken(part);
    if (normalized && !tokens.includes(normalized)) {
      tokens.push(normalized);
    }
  }
  return tokens;
}

function mergeRequirementEffectText(left: string, right: string) {
  const parts: string[] = [];
  for (const chunk of [left, right]) {
    for (const item of String(chunk || '').split(/[；;]+/)) {
      const normalized = normalizeRequirementEffectText(item);
      if (normalized && !parts.includes(normalized)) {
        parts.push(normalized);
      }
    }
  }
  return parts.join('；');
}

function applyImportedRequirements(cardId: string, importedRequirements: FanxiuFormationRequirementImportItem[]) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return { insertedCount: 0, mergedCount: 0 };

  const existingByKey = new Map<string, FormationRequirement>();
  for (const requirement of card.requirements) {
    const key = normalizeRequirementMergeKey(requirement.text);
    if (key) {
      existingByKey.set(key, requirement);
    }
  }

  let insertedCount = 0;
  let mergedCount = 0;
  for (const imported of importedRequirements) {
    const text = String(imported.text || '').trim();
    if (!text) continue;
    const effectText = String(imported.effect_text || '').trim();
    const key = normalizeRequirementMergeKey(text);
    if (!key) continue;

    const existing = existingByKey.get(key);
    if (existing) {
      existing.effectText = mergeRequirementEffectText(existing.effectText, effectText);
      if (!existing.text.trim()) {
        existing.text = text;
        syncRequirementText(existing);
      }
      mergedCount += 1;
      continue;
    }

    const requirement = normalizeRequirement({
      text,
      effectText,
    });
    card.requirements.push(requirement);
    existingByKey.set(key, requirement);
    insertedCount += 1;
  }

  return { insertedCount, mergedCount };
}

function formatImportedEffectDetailLine(effectName: string, detailText: string, effectNameCount: number) {
  const normalizedName = normalizeEffectNameToken(effectName);
  const normalizedDetail = normalizeRequirementEffectDetail(detailText);
  if (!normalizedDetail) return '';
  if (effectNameCount > 1 && normalizedName) {
    return `${normalizedName} ${normalizedDetail}`;
  }
  return normalizedDetail;
}

function applyImportedEffectDetails(cardId: string, importedEffectDetails: FanxiuFormationEffectDetailImportItem[]) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return { updatedCount: 0, unmatchedCount: 0 };

  let updatedCount = 0;
  let unmatchedCount = 0;

  for (const imported of importedEffectDetails) {
    const effectName = normalizeEffectNameToken(imported.effect_name);
    const effectDetail = normalizeRequirementEffectDetail(imported.effect_detail);
    if (!effectName || !effectDetail) continue;

    const matchedRequirements = card.requirements.filter(requirement =>
      extractEffectNameTokens(requirement.effectText).some(token => effectNameMatches(token, effectName)),
    );
    if (!matchedRequirements.length) {
      unmatchedCount += 1;
      continue;
    }

    for (const requirement of matchedRequirements) {
      const mergedDetail = mergeRequirementEffectDetail(
        requirement.effectDetail,
        formatImportedEffectDetailLine(effectName, effectDetail, extractEffectNameTokens(requirement.effectText).length),
      );
      if (mergedDetail !== requirement.effectDetail) {
        requirement.effectDetail = mergedDetail;
        updatedCount += 1;
      }
    }
  }

  return { updatedCount, unmatchedCount };
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

async function importRequirementImage(cardId: string, image: File) {
  importingRequirementCardId.value = cardId;
  try {
    const imported = await importFanxiuFormationRequirementsFromOcr(image);
    const { insertedCount, mergedCount } = applyImportedRequirements(cardId, imported.requirements);
    const { updatedCount, unmatchedCount } = applyImportedEffectDetails(cardId, imported.effect_details);
    if (!insertedCount && !mergedCount && !updatedCount) {
      if (unmatchedCount) {
        ElMessage.warning(`识别到 ${unmatchedCount} 条词缀说明，但当前卡片没有同名词条`);
        return;
      }
      ElMessage.warning('截图里没有可导入的新触发条件或词缀说明');
      return;
    }
    const summaryParts: string[] = [];
    if (insertedCount) summaryParts.push(`新增 ${insertedCount} 条`);
    if (mergedCount) summaryParts.push(`合并 ${mergedCount} 条`);
    if (updatedCount) summaryParts.push(`补全 ${updatedCount} 条说明`);
    const suffix = unmatchedCount ? `，另有 ${unmatchedCount} 条说明未匹配` : '';
    ElMessage.success(`${summaryParts.join('，')}${suffix}，可继续粘贴`);
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '触发条件截图导入失败');
  } finally {
    if (importingRequirementCardId.value === cardId) {
      importingRequirementCardId.value = '';
    }
  }
}

function toggleRequirementImport(cardId: string) {
  if (pendingRequirementImportCardId.value === cardId) {
    pendingRequirementImportCardId.value = '';
    return;
  }
  pendingRequirementImportCardId.value = cardId;
  ElMessage.info('已准备导入触发条件或词缀说明，请直接粘贴截图');
}

async function handleWindowPaste(event: ClipboardEvent) {
  const cardId = pendingRequirementImportCardId.value;
  if (!cardId || importingRequirementCardId.value) {
    return;
  }
  const image = extractClipboardImage(event);
  if (!image) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  await importRequirementImage(cardId, image);
}

function updateSlotItem(cardId: string, slotIndex: number, itemId: string | null) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  card.slots[slotIndex].itemId = itemId;
  if (!itemId) card.slots[slotIndex].locked = false;
}

function toggleSlotLock(cardId: string, slotIndex: number) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card || !card.slots[slotIndex].itemId) return;
  card.slots[slotIndex].locked = !card.slots[slotIndex].locked;
}

function moveSlot(cardId: string, oldIndex: number, newIndex: number) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  if (
    oldIndex < 0 ||
    newIndex < 0 ||
    oldIndex >= card.slots.length ||
    newIndex >= card.slots.length ||
    oldIndex === newIndex
  ) {
    return;
  }
  const nextSlots = [...card.slots];
  const [movedSlot] = nextSlots.splice(oldIndex, 1);
  if (!movedSlot) return;
  nextSlots.splice(newIndex, 0, movedSlot);
  card.slots = nextSlots;
}

function clearUnlocked(cardId: string) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;
  for (const slot of card.slots) {
    if (!slot.locked) slot.itemId = null;
  }
}

function getItemTags(item: Pick<FanxiuInventoryItem, 'main_use'>) {
  return [...new Set(
    String(item.main_use || '')
      .split(/[，,]/)
      .map(text => text.trim())
      .filter(Boolean),
  )];
}

function itemHasTag(item: Pick<FanxiuInventoryItem, 'main_use'>, tag: string) {
  if (!tag.trim()) return false;
  return getItemTags(item).includes(tag.trim());
}

function extractEffectMarkers(effectText: string) {
  const markers: string[] = [];
  for (const match of String(effectText || '').matchAll(/[【\[]([^】\]]+)[】\]]/g)) {
    const marker = normalizeEffectMarkerToken(match[0]);
    if (marker && !markers.includes(marker)) {
      markers.push(marker);
    }
  }
  return markers;
}

function isBaseRequirementKind(kind: FormationRequirementKind) {
  return kind === 'count' || kind === 'rank_sum' || kind === 'tags_each';
}

function isEffectBearingRequirementKind(kind: FormationRequirementKind) {
  return kind !== 'effect_count' && kind !== 'effect_ref';
}

function getQualityLabel(value: number | null | undefined) {
  if (!Number.isInteger(value) || value == null || value < 0 || value >= QUALITY_LABELS.length) return '';
  return QUALITY_LABELS[value];
}

function getQualityFamily(value: number | null | undefined): QualityFamily | null {
  const label = getQualityLabel(value);
  if (!label) return null;
  if (label.startsWith('神品')) return '神品';
  if (label.startsWith('仙品')) return '仙品';
  if (label === '绝品') return '绝品';
  return '珍品';
}

function getQualityValueByText(text: string): number | null {
  const normalized = text.trim();
  if (normalized === '珍品') return 0;
  if (normalized === '绝品') return 1;
  if (normalized === '仙品') return QUALITY_LABELS.indexOf('仙品一星');
  if (normalized === '神品') return QUALITY_LABELS.indexOf('神品一星');
  const direct = QUALITY_LABELS.indexOf(normalized as typeof QUALITY_LABELS[number]);
  return direct >= 0 ? direct : null;
}

function formatItemOption(item: FanxiuInventoryItem) {
  const parts = [
    item.name,
    item.type,
    getQualityLabel(item.quality),
    item.rank ? `${item.rank}阶` : '',
  ].filter(Boolean);
  return parts.join(' · ');
}

let measureDisplayTextWidthCanvas: HTMLCanvasElement | null = null;
let resolvedMeasureFont: string | null = null;

function getMeasureFont(sizePx = 14, weight = 400) {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return `${weight} ${sizePx}px sans-serif`;
  }
  if (resolvedMeasureFont) {
    return `${weight} ${sizePx}px ${resolvedMeasureFont}`;
  }

  const rootStyle = window.getComputedStyle(document.documentElement);
  const bodyStyle = window.getComputedStyle(document.body);
  const family =
    rootStyle.getPropertyValue('--el-font-family').trim()
    || bodyStyle.fontFamily.trim()
    || 'sans-serif';
  resolvedMeasureFont = family;
  return `${weight} ${sizePx}px ${family}`;
}

function measureDisplayTextWidth(text: string, font = getMeasureFont()) {
  const normalized = String(text || '');
  if (typeof document === 'undefined') {
    return estimateDisplayUnits(normalized) * 11;
  }

  const canvas = measureDisplayTextWidthCanvas ??= document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) {
    return estimateDisplayUnits(normalized) * 11;
  }

  context.font = font;
  return context.measureText(normalized).width;
}

function getSlotListStyle(card: FormationCard) {
  const labels = card.slots
    .map(slot => (slot.itemId ? inventoryMap.value.get(slot.itemId) : null))
    .filter(Boolean)
    .map(item => formatItemOption(item as FanxiuInventoryItem));
  const widestLabel = labels.length
    ? labels.reduce((best, current) => (measureDisplayTextWidth(current) >= measureDisplayTextWidth(best) ? current : best), labels[0])
    : '请选择';
  const widthPx = Math.max(112, Math.min(460, Math.ceil(measureDisplayTextWidth(widestLabel) + 78)));
  return {
    '--slot-select-width': `${widthPx}px`,
  };
}

function getAvailableItemsForSlot(cardId: string, slotIndex: number) {
  const usedByOthers = new Set<string>();
  for (const card of cards.value) {
    for (let index = 0; index < card.slots.length; index += 1) {
      const slot = card.slots[index];
      if (!slot.itemId) continue;
      if (card.id === cardId && index === slotIndex) continue;
      usedByOthers.add(slot.itemId);
    }
  }

  return [...inventoryItems.value]
    .filter(item => !usedByOthers.has(item.id))
    .sort(compareMagicTreasureItems);
}

function getAvailableItemOptionsForSlot(cardId: string, slotIndex: number) {
  return getAvailableItemsForSlot(cardId, slotIndex).map(item => ({
    id: item.id,
    label: formatItemOption(item),
  }));
}

function compareMagicTreasureItems(left: FanxiuInventoryItem, right: FanxiuInventoryItem) {
  for (const field of DEFAULT_SORT_PROGRAM) {
    if (field === 'quality') {
      const diff = (right.quality ?? -1) - (left.quality ?? -1);
      if (diff !== 0) return diff;
      continue;
    }
    if (field === 'rank') {
      const diff = right.rank - left.rank;
      if (diff !== 0) return diff;
      continue;
    }
    if (field === 'shenlian') {
      const diff = right.shenlian - left.shenlian;
      if (diff !== 0) return diff;
      continue;
    }
    if (field === 'date') {
      const diff = right.date.localeCompare(left.date);
      if (diff !== 0) return diff;
    }
  }
  return left.name.localeCompare(right.name, 'zh-CN');
}

function getItemById(itemId: string | null | undefined) {
  return itemId ? inventoryMap.value.get(itemId) || null : null;
}

function getCardItems(card: FormationCard) {
  return card.slots.map(slot => getItemById(slot.itemId)).filter(Boolean) as FanxiuInventoryItem[];
}

function normalizeRequirementNameToken(text: string | null | undefined) {
  return String(text || '')
    .replace(/\s+/g, '')
    .replace(/[·•・]/g, '·')
    .trim();
}

function matchesRequirementMatcher(item: FanxiuInventoryItem, matcher: FormationRequirementMatcher) {
  if (matcher.namesAny.length) {
    const normalizedItemName = normalizeRequirementNameToken(item.name);
    const hasMatchedName = matcher.namesAny.some(name => normalizeRequirementNameToken(name) === normalizedItemName);
    if (!hasMatchedName) return false;
  }
  if (matcher.type && item.type !== matcher.type) return false;

  const quality = typeof item.quality === 'number' ? item.quality : null;
  if (matcher.qualityMode === 'family') {
    const family = getQualityFamily(quality);
    if (!family || family !== matcher.qualityFamily) return false;
  } else if (matcher.qualityMode === 'eq') {
    if (quality == null || quality !== matcher.qualityValue) return false;
  } else if (matcher.qualityMode === 'gte') {
    if (quality == null || matcher.qualityValue == null || quality < matcher.qualityValue) return false;
  }

  if (matcher.rankMode === 'eq') {
    if (matcher.rankValue == null || item.rank !== matcher.rankValue) return false;
  } else if (matcher.rankMode === 'gte') {
    if (matcher.rankValue == null || item.rank < matcher.rankValue) return false;
  }

  return true;
}

function getBaseRequirementState(requirement: FormationRequirement, items: FanxiuInventoryItem[]): RequirementState {
  if (requirement.kind === 'count') {
    const count = items.filter(item => matchesRequirementMatcher(item, requirement.matcher)).length;
    return {
      requirement,
      triggered: count >= requirement.threshold,
      invalid: false,
      pending: false,
      progressText: `${count}/${requirement.threshold}`,
    };
  }

  if (requirement.kind === 'rank_sum') {
    const rankSum = items
      .filter(item => matchesRequirementMatcher(item, requirement.matcher))
      .reduce((sum, item) => sum + item.rank, 0);
    return {
      requirement,
      triggered: rankSum >= requirement.threshold,
      invalid: false,
      pending: false,
      progressText: `${rankSum}/${requirement.threshold}阶`,
    };
  }

  const progress = requirement.tags.map(tag => {
    const count = items.filter(item => itemHasTag(item, tag)).length;
    return `${tag} ${count}/${requirement.threshold}`;
  });
  return {
    requirement,
    triggered: requirement.tags.every(tag =>
      items.filter(item => itemHasTag(item, tag)).length >= requirement.threshold,
    ),
    invalid: false,
    pending: false,
    progressText: progress.join(' · '),
  };
}

function getRequirementState(
  requirement: FormationRequirement,
  items: FanxiuInventoryItem[],
  cardRank = 1,
  allRequirements: FormationRequirement[] = [requirement],
): RequirementState {
  if (!requirement.text.trim()) {
    return {
      requirement,
      triggered: false,
      invalid: false,
      pending: true,
      progressText: '待填写',
    };
  }

  if (requirement.parseError) {
    return {
      requirement,
      triggered: false,
      invalid: true,
      pending: false,
      progressText: requirement.parseError,
    };
  }

  if (isBaseRequirementKind(requirement.kind)) {
    return getBaseRequirementState(requirement, items);
  }

  if (requirement.kind === 'formation_rank') {
    const requiredRank = Math.max(1, Number(requirement.formationRankThreshold || 1) || 1);
    return {
      requirement,
      triggered: cardRank >= requiredRank,
      invalid: false,
      pending: false,
      progressText: `${cardRank}/${requiredRank}阶`,
    };
  }

  if (requirement.kind === 'formation_rank_and') {
    const requiredRank = Math.max(1, Number(requirement.formationRankThreshold || 1) || 1);
    if (!requirement.nestedRequirement || !isBaseRequirementKind(requirement.nestedRequirement.kind)) {
      return {
        requirement,
        triggered: false,
        invalid: true,
        pending: false,
        progressText: '无法解析',
      };
    }
    const nestedRequirement = normalizeRequirement({
      text: stringifyParsedRequirementDraft(requirement.nestedRequirement),
      effectText: requirement.effectText,
    });
    const nestedState = getRequirementState(nestedRequirement, items, cardRank, allRequirements);
    const rankSatisfied = cardRank >= requiredRank;
    return {
      requirement,
      triggered: rankSatisfied && nestedState.triggered,
      invalid: nestedState.invalid,
      pending: false,
      progressText: `${cardRank}/${requiredRank}阶 · ${nestedState.progressText}`,
    };
  }

  const effectBearingRequirements = allRequirements.filter(entry =>
    entry.id !== requirement.id
    && entry.text.trim()
    && !entry.parseError
    && isEffectBearingRequirementKind(entry.kind),
  );
  const effectBearingStates = effectBearingRequirements.map(entry => getRequirementState(entry, items, cardRank, allRequirements));

  if (requirement.kind === 'effect_count') {
    const triggeredCount = effectBearingStates.filter(state => state.triggered && state.requirement.effectText.trim()).length;
    return {
      requirement,
      triggered: triggeredCount >= requirement.threshold,
      invalid: false,
      pending: false,
      progressText: `${triggeredCount}/${requirement.threshold}`,
    };
  }

  const targetMarker = normalizeEffectMarkerToken(requirement.effectMarker);
  if (!targetMarker) {
    return {
      requirement,
      triggered: false,
      invalid: true,
      pending: false,
      progressText: '无法解析',
    };
  }

  const triggered = effectBearingStates.some(state =>
    state.triggered && extractEffectMarkers(state.requirement.effectText).includes(targetMarker),
  );
  return {
    requirement,
    triggered,
    invalid: false,
    pending: false,
    progressText: `${targetMarker} ${triggered ? 1 : 0}/1`,
  };
}

function getCardState(card: FormationCard) {
  const items = getCardItems(card);
  const requirementStates = card.requirements.map(requirement => getRequirementState(requirement, items, card.rank, card.requirements));
  const activeRequirementStates = requirementStates.filter(state => state.requirement.text.trim());
  const triggeredCount = requirementStates.filter(state => state.triggered).length;
  return {
    items,
    requirementStates,
    triggeredCount,
    activeRequirementCount: activeRequirementStates.length,
    filledCount: items.length,
  };
}

function getCardSummary(card: FormationCard) {
  const state = getCardState(card);
  return `${state.filledCount}/8 · 已触发 ${state.triggeredCount}/${state.activeRequirementCount}`;
}

function getRequirementStates(card: FormationCard) {
  return getCardState(card).requirementStates;
}

function estimateDisplayUnits(text: string) {
  return [...text].reduce((sum, char) => {
    if (/[\u0000-\u00ff]/.test(char)) return sum + 0.6;
    return sum + 1;
  }, 0);
}

function getThresholdLabel(requirement: FormationRequirement) {
  if (requirement.kind === 'rank_sum') return '目标阶数';
  if (requirement.kind === 'tags_each') return '各需数量';
  return '目标数量';
}

function getEffectiveRequirements(card: FormationCard) {
  return card.requirements.filter(requirement => requirement.text.trim() && !requirement.parseError);
}

function smartPlace(cardId: string) {
  const card = cards.value.find(entry => entry.id === cardId);
  if (!card) return;

  const emptyIndexes = card.slots
    .map((slot, index) => ({ slot, index }))
    .filter(entry => !entry.slot.itemId)
    .map(entry => entry.index);

  if (!emptyIndexes.length) {
    ElMessage.info('这个阵图已经放满了');
    return;
  }

  const currentItems = getCardItems(card);
  const currentNames = new Set(currentItems.map(item => item.name));
  const usedElsewhere = new Set<string>();
  for (const otherCard of cards.value) {
    if (otherCard.id === card.id) continue;
    for (const slot of otherCard.slots) {
      if (slot.itemId) usedElsewhere.add(slot.itemId);
    }
  }

  const available = inventoryItems.value.filter(item => {
    if (usedElsewhere.has(item.id)) return false;
    if (card.slots.some(slot => slot.itemId === item.id)) return false;
    return true;
  });

  const remainingFillCount = emptyIndexes.length;
  const remainingCandidates = available;
  const effectiveRequirements = getEffectiveRequirements(card);
  const searchCandidates = buildSearchCandidatePool(remainingCandidates, effectiveRequirements, card.rank, remainingFillCount);
  if (searchCandidates.length < remainingFillCount) {
    ElMessage.warning('剩余可用法宝不足，没法补满空位');
    return;
  }

  const chosen = remainingFillCount
    ? searchBestCandidates({
      baseItems: currentItems,
      candidates: searchCandidates,
      cardRank: card.rank,
      pickCount: remainingFillCount,
      requirements: effectiveRequirements,
    })
    : [];

  if (remainingFillCount && !chosen) {
    ElMessage.warning('当前规则下没有找到可用补全结果');
    return;
  }

  const nextItems = [...(chosen || [])].sort(compareMagicTreasureItems);

  emptyIndexes.forEach((slotIndex, index) => {
    card.slots[slotIndex].itemId = nextItems[index]?.id ?? null;
  });

  const finalState = getCardState(card);
  ElMessage.success(`已补入 ${nextItems.length} 个法宝，触发 ${finalState.triggeredCount}/${finalState.activeRequirementCount} 条条件`);
}

function buildSearchCandidatePool(
  candidates: FanxiuInventoryItem[],
  requirements: FormationRequirement[],
  cardRank: number,
  pickCount: number,
) {
  if (pickCount <= 0) return [];
  const sorted = [...candidates].sort(compareMagicTreasureItems);
  if (sorted.length <= 18) return sorted;

  const relevant = sorted.filter(item =>
    requirements.some(requirement => couldItemHelpRequirement(item, requirement, cardRank, requirements)),
  );
  const chosen: FanxiuInventoryItem[] = [];
  const added = new Set<string>();
  for (const item of relevant) {
    if (added.has(item.id)) continue;
    chosen.push(item);
    added.add(item.id);
    if (chosen.length >= 12) break;
  }
  for (const item of sorted) {
    if (added.has(item.id)) continue;
    chosen.push(item);
    added.add(item.id);
    if (chosen.length >= Math.max(12, pickCount * 3)) break;
  }
  return chosen.sort(compareMagicTreasureItems);
}

function couldItemHelpRequirement(
  item: FanxiuInventoryItem,
  requirement: FormationRequirement,
  cardRank = 1,
  allRequirements: FormationRequirement[] = [requirement],
) {
  if (requirement.kind === 'formation_rank_and') {
    const requiredRank = Math.max(1, Number(requirement.formationRankThreshold || 1) || 1);
    if (cardRank < requiredRank || !requirement.nestedRequirement) return false;
    const nestedRequirement = normalizeRequirement({
      text: stringifyParsedRequirementDraft(requirement.nestedRequirement),
      effectText: requirement.effectText,
    });
    return couldItemHelpRequirement(item, nestedRequirement, cardRank, allRequirements);
  }
  if (requirement.kind === 'tags_each') {
    return requirement.tags.some(tag => itemHasTag(item, tag));
  }
  if (requirement.kind === 'formation_rank') {
    return false;
  }
  if (requirement.kind === 'effect_count') {
    return allRequirements.some(entry =>
      entry.id !== requirement.id
      && isEffectBearingRequirementKind(entry.kind)
      && entry.effectText.trim()
      && couldItemHelpRequirement(item, entry, cardRank, allRequirements),
    );
  }
  if (requirement.kind === 'effect_ref') {
    const targetMarker = normalizeEffectMarkerToken(requirement.effectMarker);
    if (!targetMarker) return false;
    return allRequirements.some(entry =>
      entry.id !== requirement.id
      && isEffectBearingRequirementKind(entry.kind)
      && extractEffectMarkers(entry.effectText).includes(targetMarker)
      && couldItemHelpRequirement(item, entry, cardRank, allRequirements),
    );
  }
  return matchesRequirementMatcher(item, requirement.matcher);
}

function searchBestCandidates(params: {
  baseItems: FanxiuInventoryItem[];
  candidates: FanxiuInventoryItem[];
  cardRank: number;
  pickCount: number;
  requirements: FormationRequirement[];
}) {
  const { baseItems, candidates, cardRank, pickCount, requirements } = params;
  let best: FanxiuInventoryItem[] | null = null;

  const choose = (startIndex: number, selected: FanxiuInventoryItem[]) => {
    if (selected.length === pickCount) {
      const current = [...baseItems, ...selected];
      if (!best || compareSolutionSets(current, [...baseItems, ...best], cardRank, requirements) > 0) {
        best = [...selected];
      }
      return;
    }

    const remainingNeeded = pickCount - selected.length;
    for (let index = startIndex; index <= candidates.length - remainingNeeded; index += 1) {
      selected.push(candidates[index]);
      choose(index + 1, selected);
      selected.pop();
    }
  };

  choose(0, []);
  return best;
}

function compareSolutionSets(
  leftItems: FanxiuInventoryItem[],
  rightItems: FanxiuInventoryItem[],
  cardRank: number,
  requirements: FormationRequirement[],
) {
  const leftTriggered = requirements.filter(requirement => getRequirementState(requirement, leftItems, cardRank, requirements).triggered).length;
  const rightTriggered = requirements.filter(requirement => getRequirementState(requirement, rightItems, cardRank, requirements).triggered).length;
  if (leftTriggered !== rightTriggered) return leftTriggered - rightTriggered;

  const leftSorted = [...leftItems].sort(compareMagicTreasureItems);
  const rightSorted = [...rightItems].sort(compareMagicTreasureItems);
  for (let index = 0; index < Math.min(leftSorted.length, rightSorted.length); index += 1) {
    const diff = compareMagicTreasureItems(leftSorted[index], rightSorted[index]);
    if (diff !== 0) return -diff;
  }

  const leftRankSum = leftItems.reduce((sum, item) => sum + item.rank, 0);
  const rightRankSum = rightItems.reduce((sum, item) => sum + item.rank, 0);
  return leftRankSum - rightRankSum;
}

function buildRequirementsFromRawConditions(rawConditionsText: string) {
  return rawConditionsText
    .split(/\r?\n/)
    .map(text => text.trim())
    .filter(Boolean)
    .map(label => normalizeRequirement({ text: label }));
}

function parseRequirementText(label: string): ParsedRequirementDraft | null {
  const normalized = normalizeRequirementRawText(label).replace(/\s+/g, '');
  if (!normalized) return null;

  const formationRankAndRequirement = parseFormationRankAndRequirement(normalized);
  if (formationRankAndRequirement) return formationRankAndRequirement;

  const formationRankRequirement = parseFormationRankRequirement(normalized);
  if (formationRankRequirement) return formationRankRequirement;

  const effectCountRequirement = parseEffectCountRequirement(normalized);
  if (effectCountRequirement) return effectCountRequirement;

  const effectReferenceRequirement = parseEffectReferenceRequirement(normalized);
  if (effectReferenceRequirement) return effectReferenceRequirement;

  if (!/^(入阵|上阵)/.test(normalized)) return null;
  if (normalized.includes('词缀的法宝各1个')) {
    const body = normalized.replace(/^(入阵|上阵)带/, '').replace(/词缀的法宝各1个$/, '');
    const tags = body.split('和').map(text => text.trim()).filter(Boolean);
    if (!tags.length) return null;
    return {
      kind: 'tags_each',
      tags,
      threshold: 1,
    };
  }

  if (normalized.includes('合计') || normalized.includes('阶数达到') || normalized.includes('阶数合计')) {
    return parseLegacyRankSumRequirement(normalized);
  }

  return parseLegacyCountRequirement(normalized);
}

function parseFormationRankRequirement(text: string): ParsedRequirementDraft | null {
  const match = text.match(/^阵法神通达到([零一二三四五六七八九十百两0-9]+)阶$/);
  if (!match?.[1]) return null;
  return {
    kind: 'formation_rank',
    formationRankThreshold: parseFlexibleNumber(match[1]),
    threshold: 1,
  };
}

function parseFormationRankAndRequirement(text: string): ParsedRequirementDraft | null {
  const match = text.match(/^阵法神通达到([零一二三四五六七八九十百两0-9]+)阶并且(.+)$/);
  if (!match?.[1] || !match?.[2]) return null;
  const nestedText = normalizeFormationNestedRequirementText(match[2]);
  const nestedRequirement = parseRequirementText(nestedText) ?? parseFormationRankNestedFallback(nestedText);
  if (!nestedRequirement || !isBaseRequirementKind(nestedRequirement.kind)) return null;
  return {
    kind: 'formation_rank_and',
    formationRankThreshold: parseFlexibleNumber(match[1]),
    nestedRequirement,
    threshold: 1,
  };
}

function normalizeFormationNestedRequirementText(text: string) {
  return text.replace(/^(?:并且|且)+/, '').trim();
}

function parseFormationRankNestedFallback(text: string): ParsedRequirementDraft | null {
  const normalized = normalizeFormationNestedRequirementText(text);

  const countMatch = normalized.match(/^(入阵|上阵)([零一二三四五六七八九十百两0-9]+)个(.+)$/);
  if (countMatch?.[2] && countMatch?.[3]) {
    const matcher = parseLegacyMatcherDescriptor(countMatch[3].trim());
    if (matcher) {
      return {
        kind: 'count',
        threshold: parseFlexibleNumber(countMatch[2]),
        matcher,
      };
    }
  }

  const rankSumMatch = normalized.match(/^(入阵|上阵)(.+?)合计([零一二三四五六七八九十百两0-9]+)阶$/);
  if (rankSumMatch?.[2] && rankSumMatch?.[3]) {
    const matcherText = rankSumMatch[2].trim();
    const matcher = matcherText ? parseLegacyMatcherDescriptor(matcherText) : createDefaultMatcher();
    if (matcher) {
      return {
        kind: 'rank_sum',
        threshold: parseFlexibleNumber(rankSumMatch[3]),
        matcher,
      };
    }
  }

  return null;
}

function parseEffectCountRequirement(text: string): ParsedRequirementDraft | null {
  const match = text.match(/^点亮([零一二三四五六七八九十百两0-9]+)(?:条|个|项)?(?:条件法效果|阵法效果)激活技能$/);
  if (!match?.[1]) return null;
  return {
    kind: 'effect_count',
    threshold: parseFlexibleNumber(match[1]),
  };
}

function parseEffectReferenceRequirement(text: string): ParsedRequirementDraft | null {
  const markerMatch = text.match(/^点亮阵法效果(.+?)激活技能$/);
  if (!markerMatch?.[1]) return null;
  const effectMarker = normalizeEffectMarkerToken(markerMatch[1]);
  if (!effectMarker) return null;
  return {
    kind: 'effect_ref',
    effectMarker,
    threshold: 1,
  };
}

function parseLegacyCountRequirement(text: string) {
  let body = text.replace(/^(入阵|上阵)/, '');
  let minCount = 1;

  const countMatch = body.match(/^([零一二三四五六七八九十百两0-9]+)个(.+)$/);
  if (countMatch) {
    minCount = parseFlexibleNumber(countMatch[1]);
    body = countMatch[2];
  }

  const matcher = parseLegacyMatcherDescriptor(body);
  if (!matcher) return null;
  return {
    kind: 'count',
    threshold: minCount,
    matcher,
  };
}

function parseLegacyRankSumRequirement(text: string) {
  const targetMatch = text.match(/([零一二三四五六七八九十百两0-9]+)阶$/);
  if (!targetMatch) return null;
  const threshold = parseFlexibleNumber(targetMatch?.[1] || '0');
  let body = text.replace(/^(入阵|上阵)/, '').replace(/([零一二三四五六七八九十百两0-9]+)阶$/, '');
  body = body
    .replace(/^法宝的?合计阶数达到/, '')
    .replace(/^法宝的?合计阶数/, '')
    .replace(/^法宝的?阶数合计/, '')
    .replace(/法宝合计$/, '')
    .replace(/法宝的?$/, '')
    .trim();

  const matcher = body ? parseLegacyMatcherDescriptor(body) : createDefaultMatcher();
  if (!matcher) return null;
  return {
    kind: 'rank_sum',
    threshold,
    matcher,
  };
}

function parseLegacyMatcherDescriptor(input: string) {
  let text = input
    .replace(/法宝/g, '')
    .replace(/型/g, '')
    .replace(/的/g, '')
    .trim();

  const matcher = createDefaultMatcher();
  const qualityConstraint = extractLegacyQualityConstraint(text);
  text = qualityConstraint.text;
  Object.assign(matcher, qualityConstraint.matcher);

  const rankMinMatch = text.match(/([零一二三四五六七八九十百两0-9]+)阶以上/);
  if (rankMinMatch) {
    matcher.rankMode = 'gte';
    matcher.rankValue = parseFlexibleNumber(rankMinMatch[1]);
    text = text.replace(rankMinMatch[0], '');
  } else {
    const rankExactMatch = text.match(/([零一二三四五六七八九十百两0-9]+)阶/);
    if (rankExactMatch) {
      matcher.rankMode = 'gte';
      matcher.rankValue = parseFlexibleNumber(rankExactMatch[1]);
      text = text.replace(rankExactMatch[0], '');
    }
  }

  const typeMatch = text.match(/(攻击|防御|灵力|辅助)/);
  if (typeMatch) {
    matcher.type = typeMatch[1] as FanxiuInventoryType;
    text = text.replace(typeMatch[0], '');
  }

  text = text.replace(/^以上/, '').trim();
  if (/[带需达各加增触发阵词缀条件]/.test(text)) return null;
  if (text) {
    matcher.namesAny = text.split('或').map(part => part.trim()).filter(Boolean);
  }

  return matcher;
}

function extractLegacyQualityConstraint(text: string): {
  text: string;
  matcher: Partial<FormationRequirementMatcher>;
} {
  const qualityTokens = [
    ...QUALITY_LABELS.map(label => `${label}以上`),
    '神品以上',
    '仙品以上',
    '绝品以上',
    '珍品以上',
    ...QUALITY_LABELS,
    '神品',
    '仙品',
    '绝品',
    '珍品',
  ].sort((left, right) => right.length - left.length);

  for (const token of qualityTokens) {
    if (!text.includes(token)) continue;
    const nextText = text.replace(token, '').trim();
    const base = token.endsWith('以上') ? token.slice(0, -2) : token;
    const exact = getQualityValueByText(base);
    return {
      text: nextText,
      matcher: exact == null ? {} : { qualityMode: 'gte', qualityValue: exact },
    };
  }

  return { text, matcher: {} };
}

function parseFlexibleNumber(input: string) {
  const normalized = String(input || '').trim();
  if (/^\d+$/.test(normalized)) return Number(normalized);

  const digits: Record<string, number> = {
    零: 0,
    一: 1,
    二: 2,
    两: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9,
  };
  const units: Record<string, number> = {
    十: 10,
    百: 100,
  };

  let result = 0;
  let section = 0;
  let current = 0;

  for (const char of normalized) {
    if (digits[char] != null) {
      current = digits[char];
      continue;
    }
    if (units[char] != null) {
      const unit = units[char];
      if (current === 0) current = 1;
      section += current * unit;
      current = 0;
    }
  }

  result += section + current;
  return result;
}
</script>

<template>
  <div class="fanxiu-magic-treasure-formations-page">
    <el-card class="toolbar-card" shadow="never">
      <div class="toolbar-row">
        <div class="toolbar-main">
          <el-button type="primary" size="small" @click="addBlankCard">新增空白卡片</el-button>
          <el-button size="small" @click="loadInventory">刷新法宝</el-button>
        </div>
        <div class="toolbar-summary">
          阵图 {{ cards.length }} 张，法宝 {{ inventoryItems.length }} 个，未使用 {{ unusedCount }} 个
        </div>
      </div>
    </el-card>

    <el-alert
      v-if="loadError"
      class="page-alert"
      type="warning"
      :closable="false"
      :title="loadError"
    />

    <div v-if="loading" class="page-loading">法宝仓库加载中...</div>

    <el-empty
      v-else-if="!cards.length"
      class="page-empty"
      description="先新增一个阵图卡片，再配置触发条件"
      :image-size="68"
    />

    <div v-else class="formation-card-list">
      <el-card
        v-for="card in cards"
        :key="card.id"
        class="formation-card"
        shadow="never"
      >
        <template #header>
          <div class="formation-card-header">
            <div class="formation-card-title-block">
              <div class="formation-card-title-row">
                <el-input v-model="card.name" class="card-name-input" />
                <div class="card-rank-field">
                  <el-input-number
                    v-model="card.rank"
                    class="card-rank-input"
                    size="small"
                    :min="0"
                    controls-position="right"
                  />
                  <span class="card-rank-suffix">阶</span>
                </div>
                <el-input
                  v-model="card.remark"
                  size="small"
                  clearable
                  class="card-remark-input"
                  placeholder="备注"
                />
              </div>
              <div class="formation-card-subtitle">{{ getCardSummary(card) }}</div>
            </div>
            <div class="formation-card-actions">
              <el-button size="small" @click="smartPlace(card.id)">智能放置</el-button>
              <el-button size="small" @click="clearUnlocked(card.id)">清空未锁定</el-button>
              <el-button size="small" type="danger" plain @click="removeCard(card.id)">删除</el-button>
            </div>
          </div>
        </template>

        <div class="formation-card-body">
          <section class="slot-panel">
            <FormationSlotList
              :slots="card.slots"
              :list-style="getSlotListStyle(card)"
              :get-options="slotIndex => getAvailableItemOptionsForSlot(card.id, slotIndex)"
              @update-item="(slotIndex, itemId) => updateSlotItem(card.id, slotIndex, itemId)"
              @toggle-lock="slotIndex => toggleSlotLock(card.id, slotIndex)"
              @reorder="payload => moveSlot(card.id, payload.oldIndex, payload.newIndex)"
            />
          </section>

          <section class="rule-panel">
            <div class="rule-panel-header">
              <div class="rule-panel-title">触发条件</div>
              <div class="rule-panel-actions">
                <el-button
                  type="primary"
                  plain
                  size="small"
                  :loading="importingRequirementCardId === card.id"
                  @click="toggleRequirementImport(card.id)"
                >
                  {{
                    importingRequirementCardId === card.id
                      ? '识别中...'
                      : pendingRequirementImportCardId === card.id
                        ? '关闭粘贴导入'
                        : '粘贴截图导入'
                  }}
                </el-button>
                <el-button type="primary" plain size="small" @click="addRequirement(card.id)">添加条件</el-button>
              </div>
            </div>

            <FormationRequirementList
              :states="getRequirementStates(card)"
              @update-text="(requirementId, text) => updateRequirementText(card.id, requirementId, text)"
              @update-effect-text="(requirementId, text) => updateRequirementEffectText(card.id, requirementId, text)"
              @update-effect-detail="(requirementId, text) => updateRequirementEffectDetail(card.id, requirementId, text)"
              @remove="requirementId => removeRequirement(card.id, requirementId)"
              @reorder="payload => moveRequirement(card.id, payload.oldIndex, payload.newIndex)"
            />
          </section>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.fanxiu-magic-treasure-formations-page {
  padding: 0 12px 24px;
}

.toolbar-card,
.formation-card {
  border-radius: 18px;
}

.toolbar-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.toolbar-main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.toolbar-summary,
.formation-card-subtitle,
.mini-label,
.req-progress,
.page-loading,
.rule-empty {
  font-size: 13px;
  color: #7a879d;
}

.page-alert,
.page-loading,
.page-empty,
.formation-card-list {
  margin-top: 12px;
}

.page-loading {
  padding: 32px 12px;
}

.formation-card-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.formation-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.formation-card-title-block {
  min-width: 0;
}

.formation-card-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.card-name-input {
  width: clamp(240px, 30vw, 360px);
}

.card-name-input :deep(.el-input__wrapper) {
  min-height: 42px;
  padding: 4px 12px;
}

.card-name-input :deep(.el-input__inner) {
  font-size: 28px;
  line-height: 1.2;
  font-weight: 700;
  color: #22314a;
}

.card-remark-input {
  width: clamp(220px, 24vw, 320px);
}

.card-rank-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.card-rank-input {
  width: 94px;
}

.card-rank-input :deep(.el-input-number__decrease),
.card-rank-input :deep(.el-input-number__increase) {
  width: 24px;
}

.card-rank-input :deep(.el-input__wrapper) {
  min-height: 32px;
}

.card-rank-input :deep(.el-input__inner) {
  font-size: 16px;
  font-weight: 600;
}

.card-rank-suffix {
  font-size: 16px;
  font-weight: 600;
  color: #34445d;
  line-height: 1;
}

.card-remark-input :deep(.el-input__wrapper) {
  min-height: 32px;
  padding: 2px 10px;
}

.card-remark-input :deep(.el-input__inner) {
  font-size: 14px;
  line-height: 1.4;
  color: #3a4a63;
}

.formation-card-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.formation-card-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.slot-panel,
.rule-panel {
  min-width: 0;
}

.slot-panel {
  overflow-x: auto;
}

.requirement-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  overflow-x: auto;
}

.rule-panel-title {
  font-size: 13px;
  font-weight: 600;
  color: #5b6b85;
}

.rule-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.rule-panel-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.requirement-row {
  grid-template-columns:
    var(--req-text-width, 320px)
    var(--req-progress-width, 72px)
    72px;
  width: max-content;
  max-width: 100%;
  padding: 6px 8px;
  border: 1px solid #e7edf7;
  border-radius: 10px;
  background: #f8fbff;
}

.requirement-row.triggered {
  border-color: #c9defd;
  background: #edf5ff;
}

.requirement-row.invalid {
  border-color: #f3b3b3;
  background: #fff2f2;
}

.requirement-row.pending {
  background: #fbfcfe;
}

.requirement-row :deep(.el-input) {
  width: auto;
}

.requirement-row :deep(.el-input__wrapper) {
  min-height: 30px;
}

.req-text {
  width: var(--req-text-width, 320px);
}

.req-progress {
  text-align: right;
  white-space: nowrap;
}

.requirement-row > .el-button {
  justify-self: end;
  width: 72px;
}

.rule-empty {
  padding: 12px 10px;
  border-radius: 12px;
  border: 1px dashed #d7e2f2;
}

@media (max-width: 760px) {
  .requirement-row {
    width: 100%;
    grid-template-columns: 1fr 1fr;
  }

  .req-progress,
  .requirement-row > .el-button {
    grid-column: 1 / -1;
  }
}
</style>

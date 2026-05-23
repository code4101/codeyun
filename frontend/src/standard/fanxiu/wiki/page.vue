<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, ArrowRight, Refresh, Search } from '@element-plus/icons-vue'

import {
  getFanxiuGongfaCard,
  getFanxiuItemCard,
  getFanxiuLingjieFeatureCard,
  getFanxiuResourceIconUrl,
  searchFanxiuGongfaCards,
  searchFanxiuItemCards,
  searchFanxiuLingjieFeatureCards,
  updateFanxiuWikiUserFields,
  type FanxiuFacetIndex,
  type FanxiuGongfaCard,
  type FanxiuGongfaLinkedItem,
  type FanxiuGongfaProgressionRow,
  type FanxiuGongfaProgressionSection,
  type FanxiuGongfaQualityPartOption,
  type FanxiuGongfaSearchItem,
  type FanxiuGongfaSkill,
  type FanxiuGongfaSkillTypeOption,
  type FanxiuGongfaStats,
  type FanxiuItemCard,
  type FanxiuItemQualityOption,
  type FanxiuItemSearchItem,
  type FanxiuItemStats,
  type FanxiuItemTypeOption,
  type FanxiuLingjieCompactRow,
  type FanxiuLingjieFeatureCard,
  type FanxiuLingjieFeatureGroupLink,
  type FanxiuLingjieFeatureItem,
  type FanxiuLingjieFeatureSearchItem,
  type FanxiuLingjieFeatureStats,
  type FanxiuLingjieMainFeature,
  type FanxiuLingjieRuntimeDamageFamily,
  type FanxiuLingjieRuntimeSummary,
  type FanxiuLingjieRuntimeTimelineSample,
  type FanxiuTimelineHint,
  type FanxiuWikiUserFields,
} from '@/api/fanxiu'

const PAGE_CONFIG_STORAGE_KEY = 'fanxiu:wiki:object-page-config'
const PAGE_SIZE_OPTIONS = [30, 50, 80, 120]
const WIKI_TABS = [
  { key: 'item', label: '道具' },
  { key: 'gongfa', label: '功法' },
  { key: 'lingjie', label: '灵界词条' },
] as const
type SortMode = 'default' | 'time_asc' | 'time_desc'
const SORT_MODE_ORDER: SortMode[] = ['default', 'time_asc', 'time_desc']
const SORT_MODE_LABELS: Record<SortMode, string> = {
  default: '默认',
  time_asc: '时间↑',
  time_desc: '时间↓',
}
const PROGRESSION_ORDER = ['special_jie', 'renjie_jie', 'star', 'upgrade', 'gongfa_jie', 'lingjie_jie']
const GONGFA_QUALITY_GRADE_ORDER = ['上品', '珍品', '绝品', '仙品', '神品', '圣品']
const PROGRESSION_LABELS: Record<string, string> = {
  gongfa_jie: '功法进阶',
  lingjie_jie: '灵界进阶',
  renjie_jie: '人界重数',
  special_jie: '特殊进阶',
  star: '升星',
  upgrade: '升级',
}
const ATTR_LABELS: Record<string, string> = {
  MAXHP: '气血',
  MAXMP: '法力',
  ATTACK: '攻击',
  DEFENSE: '防御',
  ENDURANCE: '体魄',
  INTELLIGENCE: '神识',
  STRENGTH: '力道',
  AGILITY: '身法',
  CELESTIAL_POWER: '仙灵',
  EVIL_POWER: '邪灵',
  SWORD_POWER: '剑道',
  DAMON_POWER: '魔道',
}

const richColorMap: Record<string, string> = {
  '#017077': '#18e5e3',
  '#193970': '#9dc8ff',
  '#2a4b10': '#b9f08f',
  '#3e147d': '#caa7ff',
  '#73123a': '#ff8ac7',
  '#864c00': '#ffd45f',
  '#9e1e09': '#ff9f8b',
}

const lightRichColorMap: Record<string, string> = {
  '#017077': '#007f86',
  '#193970': '#245da8',
  '#2a4b10': '#2f8f1d',
  '#3e147d': '#6a3eb1',
  '#73123a': '#b22666',
  '#864c00': '#b16a00',
  '#9e1e09': '#c83b22',
}

type PageConfig = {
  activeTab?: WikiTab
  query?: string
  gongfaQualityFilter?: string
  gongfaQualityGradeFilter?: string
  gongfaQualityFamilyFilter?: string
  gongfaSkillTypeFilter?: string
  itemQualityFilter?: string
  itemTypeFilter?: string
  itemSubTypeFilter?: string
  qualityFilter?: string
  sortMode?: SortMode
  page?: number
  pageSize?: number
  selectedId?: string
}

type WikiTab = typeof WIKI_TABS[number]['key']
type WikiUserFieldsTarget = {
  objectType: string
  objectId: string
  userFields?: FanxiuWikiUserFields | null
}
type WikiLinkedItem = FanxiuGongfaLinkedItem | FanxiuLingjieFeatureItem

const activeTab = ref<WikiTab>('item')
const query = ref('')
const gongfaQualityGradeFilter = ref('')
const gongfaQualityFamilyFilter = ref('')
const gongfaSkillTypeFilter = ref('')
const itemQualityFilter = ref('')
const itemTypeFilter = ref('')
const itemSubTypeFilter = ref('')
const sortMode = ref<SortMode>('default')
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)
const stats = ref<FanxiuGongfaStats>({})
const itemStats = ref<FanxiuItemStats>({})
const lingjieStats = ref<FanxiuLingjieFeatureStats>({})
const catalogPath = ref('')
const gongfaQualityGradeOptions = ref<FanxiuGongfaQualityPartOption[]>([])
const gongfaQualityFamilyOptions = ref<FanxiuGongfaQualityPartOption[]>([])
const gongfaSkillTypeOptions = ref<FanxiuGongfaSkillTypeOption[]>([])
const itemQualityOptions = ref<FanxiuItemQualityOption[]>([])
const itemTypeOptions = ref<FanxiuItemTypeOption[]>([])
const itemSubTypeOptions = ref<FanxiuItemTypeOption[]>([])
const gongfaFacetIndex = ref<FanxiuFacetIndex | null>(null)
const itemFacetIndex = ref<FanxiuFacetIndex | null>(null)
const gongfaItems = ref<FanxiuGongfaSearchItem[]>([])
const itemItems = ref<FanxiuItemSearchItem[]>([])
const lingjieItems = ref<FanxiuLingjieFeatureSearchItem[]>([])
const selectedId = ref('')
const selectedCard = ref<FanxiuGongfaCard | null>(null)
const selectedItem = ref<FanxiuItemCard | null>(null)
const selectedLingjieCard = ref<FanxiuLingjieFeatureCard | null>(null)
const selectedProgressionType = ref('')
const wikiUserNoteDraft = ref('')
const wikiUserSourceDraft = ref('')
const wikiUserFieldsSaveState = ref<'idle' | 'saving' | 'saved' | 'error'>('idle')
const loadingList = ref(false)
const loadingDetail = ref(false)
const gongfaDetailCache = new Map<string, FanxiuGongfaCard>()
const itemDetailCache = new Map<string, FanxiuItemCard>()
const lingjieDetailCache = new Map<string, FanxiuLingjieFeatureCard>()
const route = useRoute()
const router = useRouter()
let listRequestSeq = 0
let detailRequestSeq = 0
let wikiUserFieldsSaveSeq = 0
let wikiUserFieldsSaveTimer: ReturnType<typeof setTimeout> | null = null
let applyingRouteState = false
let internalTabNavigation = false

function normalizeWikiTab(value: unknown): WikiTab | null {
  const text = Array.isArray(value) ? String(value[0] ?? '') : String(value ?? '')
  return WIKI_TABS.some(tab => tab.key === text) ? text as WikiTab : null
}

function queryValue(value: unknown) {
  if (Array.isArray(value)) return String(value[0] ?? '').trim()
  return String(value ?? '').trim()
}

function applyRouteState() {
  const routeTab = normalizeWikiTab(route.query.tab)
  const routeId = queryValue(route.query.id)
  let changed = false
  applyingRouteState = true
  try {
    if (routeTab && activeTab.value !== routeTab) {
      activeTab.value = routeTab
      changed = true
    }
    if (routeId && selectedId.value !== routeId) {
      selectedId.value = routeId
      changed = true
    }
  } finally {
    applyingRouteState = false
  }
  return changed
}

function syncRouteState() {
  if (applyingRouteState) return
  const nextQuery = { ...route.query }
  nextQuery.tab = activeTab.value
  if (selectedId.value) {
    nextQuery.id = selectedId.value
  } else {
    delete nextQuery.id
  }
  if (queryValue(route.query.tab) === activeTab.value && queryValue(route.query.id) === selectedId.value) return
  void router.replace({ query: nextQuery }).catch(() => {})
}

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function normalizePage(value: unknown, fallback = 1) {
  const numeric = Math.floor(Number(value))
  return Number.isFinite(numeric) && numeric > 0 ? numeric : fallback
}

function normalizePageSize(value: unknown, fallback = 50) {
  const numeric = Math.floor(Number(value))
  return PAGE_SIZE_OPTIONS.includes(numeric) ? numeric : fallback
}

function normalizeSortMode(value: unknown): SortMode {
  const text = String(value ?? '').trim()
  return text === 'time_asc' || text === 'time_desc' ? text : 'default'
}

function loadPageConfig() {
  if (!canUseLocalStorage()) return
  try {
    const raw = window.localStorage.getItem(PAGE_CONFIG_STORAGE_KEY)
    if (!raw) return
    const config = JSON.parse(raw) as PageConfig
    if (WIKI_TABS.some(tab => tab.key === config.activeTab)) {
      activeTab.value = config.activeTab
    }
    query.value = String(config.query ?? '')
    gongfaQualityGradeFilter.value = String(config.gongfaQualityGradeFilter ?? '')
    gongfaQualityFamilyFilter.value = String(config.gongfaQualityFamilyFilter ?? '')
    gongfaSkillTypeFilter.value = String(config.gongfaSkillTypeFilter ?? '')
    itemQualityFilter.value = String(config.itemQualityFilter ?? '')
    itemTypeFilter.value = String(config.itemTypeFilter ?? '')
    itemSubTypeFilter.value = String(config.itemSubTypeFilter ?? '')
    sortMode.value = normalizeSortMode(config.sortMode)
    page.value = normalizePage(config.page, 1)
    pageSize.value = normalizePageSize(config.pageSize, 50)
    selectedId.value = String(config.selectedId ?? '')
  } catch (error) {
    console.warn('Failed to load persisted Fanxiu wiki object page config:', error)
    window.localStorage.removeItem(PAGE_CONFIG_STORAGE_KEY)
  }
}

function persistPageConfig() {
  if (!canUseLocalStorage()) return
  try {
    window.localStorage.setItem(PAGE_CONFIG_STORAGE_KEY, JSON.stringify({
      activeTab: activeTab.value,
      query: query.value,
      gongfaQualityGradeFilter: gongfaQualityGradeFilter.value,
      gongfaQualityFamilyFilter: gongfaQualityFamilyFilter.value,
      gongfaSkillTypeFilter: gongfaSkillTypeFilter.value,
      itemQualityFilter: itemQualityFilter.value,
      itemTypeFilter: itemTypeFilter.value,
      itemSubTypeFilter: itemSubTypeFilter.value,
      sortMode: sortMode.value,
      page: page.value,
      pageSize: pageSize.value,
      selectedId: selectedId.value,
    }))
  } catch (error) {
    console.warn('Failed to persist Fanxiu wiki object page config:', error)
  }
}

const pageCount = computed(() => {
  return Math.max(1, Math.ceil(Math.max(total.value, 0) / Math.max(pageSize.value, 1)))
})

type FacetFilterMap = Record<string, string>
type FacetCountOption = { value: string; count: number }

function getFacetCandidateIds(index: FanxiuFacetIndex | null, filters: FacetFilterMap, excludedRow: string) {
  if (!index) return null
  let candidate: Set<string> | null = null
  for (const [rowKey, value] of Object.entries(filters)) {
    if (!value || rowKey === excludedRow) continue
    const ids = index.rows?.[rowKey]?.[value] ?? []
    const idSet = new Set(ids.map(String))
    if (candidate === null) {
      candidate = idSet
      continue
    }
    candidate = new Set([...candidate].filter(id => idSet.has(id)))
  }
  return candidate ?? new Set((index.object_ids ?? []).map(String))
}

function getFacetOptionCount(index: FanxiuFacetIndex | null, rowKey: string, value: string, filters: FacetFilterMap, fallback: number) {
  const candidate = getFacetCandidateIds(index, filters, rowKey)
  if (!candidate) return fallback
  const optionIds = index?.rows?.[rowKey]?.[value] ?? []
  let count = 0
  for (const id of optionIds) {
    if (candidate.has(String(id))) count += 1
  }
  return count
}

function withDynamicFacetCounts<T extends FacetCountOption>(
  options: T[],
  index: FanxiuFacetIndex | null,
  rowKey: string,
  filters: FacetFilterMap,
) {
  return options.map(option => ({
    ...option,
    count: getFacetOptionCount(index, rowKey, option.value, filters, option.count),
  }))
}

const gongfaFacetFilters = computed<FacetFilterMap>(() => ({
  quality_grade_name: gongfaQualityGradeFilter.value,
  quality_family_name: gongfaQualityFamilyFilter.value,
  skill_type_name: gongfaSkillTypeFilter.value,
}))

const itemFacetFilters = computed<FacetFilterMap>(() => ({
  quality_name: itemQualityFilter.value,
  type_key: itemTypeFilter.value,
  sub_type_key: itemSubTypeFilter.value,
}))

const selectedListItem = computed(() => {
  if (activeTab.value === 'item') {
    return itemItems.value.find(item => String(item.id) === selectedId.value) ?? null
  }
  if (activeTab.value === 'lingjie') {
    return lingjieItems.value.find(item => String(item.gongfa_id) === selectedId.value) ?? null
  }
  return gongfaItems.value.find(item => String(item.id) === selectedId.value) ?? null
})

const selectedTerms = computed(() => {
  if (activeTab.value === 'item') {
    return selectedItem.value?.terms?.slice(0, 12) ?? selectedListItem.value?.terms?.slice(0, 8) ?? []
  }
  if (activeTab.value === 'lingjie') {
    return uniqueLabels([
      selectedLingjieCard.value?.main_feature_names,
      selectedLingjieCard.value?.side_feature_names,
      ...(selectedLingjieCard.value?.items ?? []).map(item => item.name),
    ]).slice(0, 12)
  }
  return selectedCard.value?.terms?.slice(0, 12) ?? selectedListItem.value?.terms?.slice(0, 8) ?? []
})

const selectedWikiUserFieldsTarget = computed<WikiUserFieldsTarget | null>(() => {
  if (activeTab.value === 'item' && selectedItem.value) {
    return { objectType: 'item', objectId: String(selectedItem.value.id), userFields: selectedItem.value.user_fields }
  }
  if (activeTab.value === 'lingjie' && selectedLingjieCard.value) {
    return {
      objectType: 'lingjie',
      objectId: String(selectedLingjieCard.value.gongfa_id),
      userFields: selectedLingjieCard.value.user_fields,
    }
  }
  if (activeTab.value === 'gongfa' && selectedCard.value) {
    return { objectType: 'gongfa', objectId: String(selectedCard.value.id), userFields: selectedCard.value.user_fields }
  }
  return null
})

const wikiUserFieldsSaveLabel = computed(() => {
  const target = selectedWikiUserFieldsTarget.value
  if (!target) return ''
  if (wikiUserFieldsSaveState.value === 'saving') return '保存中'
  if (wikiUserFieldsSaveState.value === 'saved') return '已保存'
  if (wikiUserFieldsSaveState.value === 'error') return '保存失败'
  return target.userFields?.updated_at ? '已保存' : ''
})

const objectStats = computed(() => {
  if (activeTab.value === 'item') {
    return [
      { label: '道具', value: itemStats.value.item_count },
      { label: '品质', value: itemStats.value.quality_count },
      { label: '类型', value: itemStats.value.type_count },
      { label: '子类', value: itemStats.value.sub_type_count },
      { label: '时间线索', value: itemStats.value.item_with_time_hint_count },
    ].filter(item => Number.isFinite(Number(item.value)))
  }
  if (activeTab.value === 'lingjie') {
    return [
      { label: '功法', value: lingjieStats.value.gongfa_count },
      { label: '词条组', value: lingjieStats.value.linked_feature_group_count },
      { label: '道具', value: lingjieStats.value.linked_item_count },
    ].filter(item => Number.isFinite(Number(item.value)))
  }
  const values = [
    { label: '功法', value: stats.value.gongfa_count },
    { label: '技能', value: stats.value.skill_count },
    { label: '已关联', value: stats.value.linked_skill_count },
    { label: '时间线索', value: stats.value.gongfa_with_time_hint_count },
  ]
  return values.filter(item => Number.isFinite(Number(item.value)))
})

const searchPlaceholder = computed(() => {
  if (activeTab.value === 'item') return '搜索道具 / 效果 / 描述 / ID'
  if (activeTab.value === 'lingjie') return '搜索灵界功法 / 道具 / 主词条 / 侧词条 / Feature'
  return '搜索功法 / 技能 / 效果 / 条件'
})

const objectSortParams = computed<{ sort_by?: string; sort_order?: string }>(() => {
  if (activeTab.value === 'lingjie' || sortMode.value === 'default') return {}
  if (sortMode.value === 'time_desc') return { sort_by: 'time', sort_order: 'desc' }
  return { sort_by: 'time', sort_order: 'asc' }
})

const activeSortModeLabel = computed(() => SORT_MODE_LABELS[sortMode.value])

const nextSortModeLabel = computed(() => {
  const index = SORT_MODE_ORDER.indexOf(sortMode.value)
  const next = SORT_MODE_ORDER[(index + 1) % SORT_MODE_ORDER.length] ?? 'default'
  return SORT_MODE_LABELS[next]
})

const activeObjectLabel = computed(() => {
  if (activeTab.value === 'item') return '道具'
  if (activeTab.value === 'lingjie') return '灵界词条'
  return '功法'
})

const selectedProgressionSource = computed(() => {
  if (activeTab.value === 'lingjie') return {}
  return activeTab.value === 'item'
    ? selectedItem.value?.progression ?? {}
    : selectedCard.value?.progression ?? {}
})

const progressionTabs = computed(() => {
  const progression = selectedProgressionSource.value
  return PROGRESSION_ORDER
    .filter(key => Array.isArray(progression[key]) && progression[key].length)
    .map(key => ({
      key,
      label: PROGRESSION_LABELS[key] ?? key,
      count: progression[key].length,
    }))
})

const selectedProgressionRows = computed(() => {
  const key = selectedProgressionType.value || progressionTabs.value[0]?.key || ''
  return selectedProgressionSource.value?.[key] ?? []
})

const progressionDisplayGroups = computed(() => buildProgressionDisplayGroups(selectedProgressionRows.value))
const progressionViewGroups = computed(() => buildProgressionViewGroups(progressionDisplayGroups.value))
const lingjieMainPinViewGroups = computed(() => buildLingjieProgressionViewGroups(selectedLingjieCard.value?.main_pin_rows))
const lingjieJieViewGroups = computed(() => buildLingjieProgressionViewGroups(selectedLingjieCard.value?.jie_rows))
const lingjieStarViewGroups = computed(() => buildLingjieProgressionViewGroups(selectedLingjieCard.value?.star_rows))

const gongfaQualityGradeFacetOptions = computed(() => {
  const order = new Map(GONGFA_QUALITY_GRADE_ORDER.map((name, index) => [name, index]))
  return withDynamicFacetCounts(
    gongfaQualityGradeOptions.value,
    gongfaFacetIndex.value,
    'quality_grade_name',
    gongfaFacetFilters.value,
  ).sort((left, right) => {
    const leftIndex = order.get(String(left.label || left.value || '')) ?? Number.MAX_SAFE_INTEGER
    const rightIndex = order.get(String(right.label || right.value || '')) ?? Number.MAX_SAFE_INTEGER
    if (leftIndex !== rightIndex) return leftIndex - rightIndex
    return String(left.label || left.value || '').localeCompare(String(right.label || right.value || ''), 'zh-Hans-CN')
  })
})

const gongfaQualityFamilyFacetOptions = computed(() => {
  return withDynamicFacetCounts(
    gongfaQualityFamilyOptions.value,
    gongfaFacetIndex.value,
    'quality_family_name',
    gongfaFacetFilters.value,
  )
})

const gongfaSkillTypeFacetOptions = computed(() => {
  return withDynamicFacetCounts(
    gongfaSkillTypeOptions.value,
    gongfaFacetIndex.value,
    'skill_type_name',
    gongfaFacetFilters.value,
  )
})

const itemQualityFacetOptions = computed(() => {
  return withDynamicFacetCounts(
    itemQualityOptions.value,
    itemFacetIndex.value,
    'quality_name',
    itemFacetFilters.value,
  )
})

const itemTypeFacetOptions = computed(() => {
  return withDynamicFacetCounts(
    itemTypeOptions.value,
    itemFacetIndex.value,
    'type_key',
    itemFacetFilters.value,
  )
})

const itemSubTypeFacetOptions = computed(() => {
  return withDynamicFacetCounts(
    itemSubTypeOptions.value,
    itemFacetIndex.value,
    'sub_type_key',
    itemFacetFilters.value,
  ).filter(option => option.count > 0 || itemSubTypeFilter.value === option.value)
})

const primarySkill = computed(() => {
  return selectedCard.value?.skills?.find(skill => getSkillText(skill)) ?? null
})

const secondarySkills = computed(() => {
  const skills = selectedCard.value?.skills ?? []
  if (!primarySkill.value) return skills
  return skills.filter(skill => skill !== primarySkill.value)
})

type WikiObjectItem =
  FanxiuGongfaSearchItem |
  FanxiuGongfaCard |
  FanxiuItemSearchItem |
  FanxiuItemCard |
  FanxiuLingjieFeatureSearchItem |
  FanxiuLingjieFeatureCard

type ProgressionAttrEntry = {
  key: string;
  label: string;
  value: string;
}

type ProgressionDimensionKey = 'jie' | 'pin' | 'star' | 'grade'

type ProgressionVariableEntry = {
  key: ProgressionDimensionKey;
  symbol: string;
  label: string;
  range: string;
}

type ProgressionFazeTip = {
  code?: string;
  reason?: string;
  text?: string;
}

type ProgressionFazeSummary = {
  title: string;
  meta: string;
  tips: ProgressionFazeTip[];
}

type ProgressionInheritedBadge = {
  key: string;
  label: string;
  title: string;
  html?: string;
}

type ProgressionStepVariable = {
  symbol: string;
  value: number;
  title: string;
  unit?: string;
  kind?: 'stage' | 'block';
}

type ProgressionParagraphStepPlan = {
  replacement?: string;
  hidden?: boolean;
  badgeLabel?: string;
  badgeTitle?: string;
  stepVariable?: ProgressionStepVariable;
}

type ProgressionStepOccurrence = {
  groupIndex: number;
  paragraphIndex: number;
  part: string;
  tokens: ReturnType<typeof getNumericTokens>;
  bounds: ProgressionGroupStageBounds;
}

type ProgressionStepRunPlan = {
  run: ProgressionStepOccurrence[];
  variableValues: number[];
  varyingIndexes: number[];
  variableSymbol: string;
  variableUnit: string;
  variableKind: ProgressionStepVariable['kind'];
}

type ProgressionDisplayGroup = {
  key: string;
  rows: FanxiuGongfaProgressionRow[];
  first: FanxiuGongfaProgressionRow;
  startIndex: number;
  merged: boolean;
  title: string;
  meta: string;
  variables: ProgressionVariableEntry[];
  attrEntries: ProgressionAttrEntry[];
  text: string;
  richText: string;
  fazeSummary: ProgressionFazeSummary | null;
}

type ProgressionViewGroup = ProgressionDisplayGroup & {
  inheritedBadges: ProgressionInheritedBadge[];
  stepVariables: ProgressionStepVariable[];
  displayAttrEntries: ProgressionAttrEntry[];
  displayText: string;
  displayRichText: string;
  displayFazeSummary: ProgressionFazeSummary | null;
}

type ProgressionBuildOptions = {
  allowStaticMerge?: boolean;
}

const PROGRESSION_VARIABLES: Record<ProgressionDimensionKey, { symbol: string; label: string; unit: string }> = {
  jie: { symbol: 'x', label: '重数', unit: '重' },
  pin: { symbol: 'y', label: '阶数', unit: '阶' },
  star: { symbol: 'z', label: '星级', unit: '星' },
  grade: { symbol: 'n', label: '等级', unit: '级' },
}

const PROGRESSION_STAGE_ORDER: ProgressionDimensionKey[] = ['jie', 'star', 'grade', 'pin']

function getObjectIconText(item: WikiObjectItem | null) {
  const name = String(item?.name || '').replace(/[【】\s]/g, '')
  return Array.from(name || '凡')[0] ?? '凡'
}

function getObjectIconUrl(item: WikiObjectItem | null) {
  return getFanxiuResourceIconUrl(item?.icon)
}

function getLinkedItemIconUrl(item: WikiLinkedItem | null | undefined) {
  return getFanxiuResourceIconUrl(item?.icon || (item as FanxiuGongfaLinkedItem | null | undefined)?.small_icon)
}

function getLinkedItemText(item: WikiLinkedItem) {
  const count = (item as FanxiuGongfaLinkedItem).count
  const countText = count === null || count === undefined || count === '' ? '' : ` x${count}`
  return `${item.name || item.id || '道具'}${countText}`
}

function getLinkedItemId(item: WikiLinkedItem | null | undefined) {
  const id = String(item?.id ?? '').trim()
  return id
}

function canOpenLinkedItem(item: WikiLinkedItem | null | undefined) {
  return Boolean(getLinkedItemId(item))
}

function getLinkedItemDescription(item: WikiLinkedItem | null | undefined) {
  return String(item?.description || '').trim()
}

function normalizeIconName(value: unknown) {
  return String(value ?? '').trim().toLowerCase()
}

function getLinkedItemIconNames(item: FanxiuGongfaLinkedItem | null | undefined) {
  return uniqueLabels([item?.icon, item?.small_icon]).map(normalizeIconName).filter(Boolean)
}

function isSameIconAsSelectedCard(item: FanxiuGongfaLinkedItem | null | undefined) {
  const cardIcons = new Set(uniqueLabels([selectedCard.value?.icon, selectedCard.value?.small_icon]).map(normalizeIconName).filter(Boolean))
  if (!cardIcons.size) return false
  return getLinkedItemIconNames(item).some(icon => cardIcons.has(icon))
}

function getDisplayLinkedItems(items: FanxiuGongfaLinkedItem[] | null | undefined) {
  return (items ?? []).filter(item => !isSameIconAsSelectedCard(item))
}

function hideBrokenIcon(event: Event) {
  const target = event.currentTarget
  if (target instanceof HTMLImageElement) {
    target.style.display = 'none'
  }
}

function getQualityLabel(item: WikiObjectItem | null | undefined) {
  const qualityItem = item as Partial<FanxiuGongfaCard & FanxiuItemCard> | null | undefined
  const richName = String(qualityItem?.quality_rich_name || '').trim()
  if (richName) return richName
  const name = String((item as { quality_name?: unknown } | null | undefined)?.quality_name || '').trim()
  if (name) return name
  const value = item?.quality
  if (value === null || value === undefined || value === '') return '品质未知'
  return `品质 ${value}`
}

function getQualityTitle(item: WikiObjectItem | null | undefined) {
  const qualityItem = item as Partial<FanxiuGongfaCard & FanxiuItemCard> | null | undefined
  const values = [
    item?.quality ? `品质ID ${item.quality}` : '',
    qualityItem?.quality_rank ? `阶级 ${qualityItem.quality_rank}` : '',
    qualityItem?.quality_type_name ? `类型 ${qualityItem.quality_type_name}` : '',
    qualityItem?.quality_tab ? `品质 ${qualityItem.quality_tab}` : '',
  ]
  return values.filter(Boolean).join(' / ')
}

function getItemMeta(item: FanxiuItemSearchItem | FanxiuItemCard | null | undefined) {
  const values = [
    getQualityLabel(item),
    getItemCategoryLabel(item),
    getProgressionSummary(item),
  ]
  return values.filter(Boolean).join(' · ')
}

function getItemCategoryLabel(item: FanxiuItemSearchItem | FanxiuItemCard | null | undefined) {
  const typeName = String(item?.type_name || '').trim()
  const subTypeName = String(item?.sub_type_name || '').trim()
  if (typeName && subTypeName && !subTypeName.includes(typeName)) return `${typeName} · ${subTypeName}`
  return subTypeName || typeName
}

function getItemTypeDisplay(item: FanxiuItemCard | null | undefined) {
  const name = String(item?.type_name || '').trim()
  const raw = String(item?.type ?? '').trim()
  if (name && raw) return `${name} (${raw})`
  return name || raw
}

function getItemSubTypeDisplay(item: FanxiuItemCard | null | undefined) {
  const name = String(item?.sub_type_name || '').trim()
  const raw = String(item?.sub_type ?? '').trim()
  if (name && raw) return `${name} (${raw})`
  return name || raw
}

type TimelineCarrier = {
  time_hints?: FanxiuTimelineHint[];
  first_time_hint?: FanxiuTimelineHint | null;
} | null | undefined

function getTimelineHints(item: TimelineCarrier) {
  const hints = item?.time_hints?.filter(Boolean) ?? []
  if (hints.length) return hints
  return item?.first_time_hint ? [item.first_time_hint] : []
}

function getFirstTimelineHint(item: TimelineCarrier) {
  return item?.first_time_hint ?? getTimelineHints(item)[0] ?? null
}

function getTimelineValueHints(item: TimelineCarrier) {
  const hints = getTimelineHints(item)
  const values: FanxiuTimelineHint[] = []
  const seen = new Set<string>()
  for (const hint of hints) {
    const key = [hint.date || '', hint.time || '', hint.time_code || ''].join('|')
    if (key === '||' || seen.has(key)) continue
    seen.add(key)
    values.push(hint)
  }
  return values.length ? values : hints
}

function getTimelineDateText(hint: FanxiuTimelineHint | null | undefined) {
  if (!hint) return ''
  if (hint.date && hint.time) return `${hint.date} ${hint.time}`
  if (hint.date) return hint.date
  if (hint.time_code) return `相对时程 ${hint.time_code}`
  return ''
}

function getTimelineHintName(hint: FanxiuTimelineHint | null | undefined) {
  if (!hint) return ''
  return String(hint.activity_name || hint.activity_little_name || hint.activity_id || '').trim()
}

function getTimelineHintLabel(hint: FanxiuTimelineHint) {
  const mergedCount = Number(hint.merged_count || 0)
  return [
    getTimelineDateText(hint),
    getTimelineHintName(hint),
    hint.label,
    mergedCount > 1 ? `${mergedCount}条证据` : '',
    hint.via_item_name ? `由 ${hint.via_item_name} 关联` : '',
  ].filter(Boolean).join(' · ')
}

function getTimelineHintTitle(hint: FanxiuTimelineHint) {
  return [
    hint.sources?.length ? `来源 ${hint.sources.join('、')}` : hint.source,
    hint.activity_ids?.length ? `活动ID ${hint.activity_ids.join('、')}` : '',
    hint.evidences?.length ? `证据 ${hint.evidences.join('、')}` : hint.evidence,
    hint.confidence ? `置信度 ${hint.confidence}` : '',
  ].filter(Boolean).join(' / ')
}

function getFirstTimelineLabel(item: TimelineCarrier) {
  const hint = getFirstTimelineHint(item)
  const text = getTimelineDateText(hint)
  return text ? `最早线索 ${text}` : ''
}

function getFirstTimelineShortLabel(item: TimelineCarrier) {
  const hint = getFirstTimelineHint(item)
  return hint?.date || ''
}

function uniqueLabels(values: Array<unknown>) {
  return Array.from(new Set(
    values
      .map(value => String(value ?? '').trim())
      .filter(Boolean),
  ))
}

function formatCountLabel(value: unknown, label: string) {
  const count = Number(value)
  return Number.isFinite(count) && count > 0 ? `${label} ${count}` : ''
}

function getGongfaSkillTypeNames(item: FanxiuGongfaSearchItem | FanxiuGongfaCard | null | undefined) {
  const searchItem = item as FanxiuGongfaSearchItem | null | undefined
  const card = item as FanxiuGongfaCard | null | undefined
  return uniqueLabels([
    ...(searchItem?.skill_type_names ?? []),
    card?.skill_type_name,
    ...(card?.skills ?? []).map(skill => skill.type_name),
  ]).slice(0, 3)
}

function getGongfaMetaTail(item: FanxiuGongfaSearchItem | FanxiuGongfaCard | null | undefined) {
  return [
    ...getGongfaSkillTypeNames(item),
    formatCountLabel(item?.skill_count, '技能'),
    getProgressionSummary(item),
  ].filter(Boolean).join(' · ')
}

function getLingjieMeta(item: FanxiuLingjieFeatureSearchItem | FanxiuLingjieFeatureCard | null | undefined) {
  return [
    getQualityLabel(item),
    formatCountLabel(item?.main_pin_count, '主词条'),
    formatCountLabel(item?.side_jie_group_count, '侧词条组'),
    formatCountLabel(item?.jie_count, '进阶'),
    formatCountLabel(item?.star_count, '升星'),
  ].filter(Boolean).join(' · ')
}

function getLingjieItemIconUrl(item: FanxiuLingjieFeatureItem | null | undefined) {
  return getFanxiuResourceIconUrl(item?.icon)
}

function getLingjieItemText(item: FanxiuLingjieFeatureItem) {
  return String(item.name || item.id || '道具')
}

function getLingjieFeatureTypeName(feature: FanxiuLingjieMainFeature | null | undefined) {
  const type = String(feature?.feature_type ?? '')
  if (type === '1') return '主词条'
  if (type === '2') return '侧词条池'
  return type ? `类型 ${type}` : '词条'
}

function getLingjieMainFeatureTitle(feature: FanxiuLingjieMainFeature, index: number) {
  return `${getLingjieFeatureTypeName(feature)} ${feature.id ?? index + 1}`
}

function getLingjieTargetLabel(link: FanxiuLingjieFeatureGroupLink | null | undefined) {
  const kinds = link?.target_kinds ?? []
  const labels = kinds.map(kind => {
    if (kind === 'main_pin') return '主词条'
    if (kind === 'side_jie') return '进阶侧词条'
    if (kind === 'side_pin') return '品阶侧词条'
    return kind
  })
  return labels.join(' / ')
}

function getLingjieGroupMeta(link: FanxiuLingjieFeatureGroupLink | null | undefined) {
  return [
    link?.feature_group ? `Group ${link.feature_group}` : '',
    getLingjieTargetLabel(link),
    formatCountLabel(link?.main_pin_count, '主词条'),
    formatCountLabel(link?.side_jie_count, '进阶'),
    formatCountLabel(link?.side_pin_count, '品阶'),
  ].filter(Boolean).join(' · ')
}

function getLingjieRowTitle(row: FanxiuLingjieCompactRow, index: number) {
  const stageTitle = row.jie && `${row.jie}重` || row.star && `${row.star}星` || row.pin && `${row.pin}阶`
  return String(row.name || stageTitle || row.feature || row.skill || row.id || `条目 ${index + 1}`)
}

function getLingjieRowMeta(row: FanxiuLingjieCompactRow) {
  return [
    row.pin ? `${row.pin}阶` : '',
    row.jie ? `${row.jie}重` : '',
    row.star ? `${row.star}星` : '',
    row.quality ? `品质 ${row.quality}` : '',
    row.featureGroup ? `Group ${row.featureGroup}` : '',
    row.feature ? `Feature ${row.feature}` : '',
    row.skill ? `Skill ${row.skill}` : '',
    row.cd ? `CD ${row.cd}` : '',
  ].filter(Boolean).join(' / ')
}

function getLingjieParamText(row: FanxiuLingjieCompactRow) {
  return formatRawValue(row.param)
}

function asLingjieProgressionRows(rows: FanxiuLingjieCompactRow[] | null | undefined) {
  return (rows ?? []).map(row => ({
    ...row,
    title: row.name || '',
    describe: row.describe || '',
  } as unknown as FanxiuGongfaProgressionRow))
}

function buildLingjieProgressionViewGroups(rows: FanxiuLingjieCompactRow[] | null | undefined) {
  const displayGroups = buildProgressionDisplayGroups(asLingjieProgressionRows(rows), { allowStaticMerge: true })
  return buildProgressionViewGroups(displayGroups)
}

function getLingjieProgressionName(group: ProgressionDisplayGroup | ProgressionViewGroup) {
  return String((group.first as unknown as FanxiuLingjieCompactRow).name || '').trim()
}

function getLingjieProgressionDisplayTitle(group: ProgressionDisplayGroup | ProgressionViewGroup) {
  const name = getLingjieProgressionName(group)
  const progressionTitle = getProgressionDisplayTitle(group)
  if (!name || name === progressionTitle) return progressionTitle || name
  return `${name} · ${progressionTitle}`
}

function getLingjieProgressionTitleHint(group: ProgressionDisplayGroup | ProgressionViewGroup) {
  return [
    getLingjieProgressionName(group),
    getProgressionTitleHint(group),
  ].filter(Boolean).join('；')
}

function getLingjieProgressionMeta(group: ProgressionDisplayGroup | ProgressionViewGroup) {
  const first = group.first as unknown as FanxiuLingjieCompactRow
  return [
    group.rows.length > 1 ? `${group.rows.length}条` : '',
    getLingjieRowMeta(first),
  ].filter(Boolean).join(' / ')
}

function getLingjieProgressionParamText(group: ProgressionDisplayGroup | ProgressionViewGroup) {
  const values = uniqueLabels(group.rows.map(row => getLingjieParamText(row as unknown as FanxiuLingjieCompactRow)).filter(Boolean))
  if (!values.length) return ''
  if (values.length === 1) return values[0]
  return `${values[0]} -> ${values[values.length - 1]}`
}

function getLingjieProgressionSectionCount(groups: ProgressionViewGroup[], rows: FanxiuLingjieCompactRow[] | undefined) {
  const rowCount = rows?.length ?? 0
  if (!rowCount || groups.length === rowCount) return String(rowCount)
  return `${groups.length}组 / ${rowCount}条`
}

const LINGJIE_CAREER_LABELS: Record<string, string> = {
  jian: '剑',
  mo: '魔',
  sha: '煞',
  xian: '仙',
}

function formatLingjieRuntimeCareers(value: unknown) {
  const values = Array.isArray(value) ? value : splitFanxiuList(value, 8)
  return uniqueLabels(values.map(item => LINGJIE_CAREER_LABELS[String(item)] ?? item)).join(' / ')
}

function formatRuntimeMs(value: unknown) {
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return String(value || '')
  if (numberValue >= 1000) return `${Number((numberValue / 1000).toFixed(2))}s`
  return `${Math.round(numberValue)}ms`
}

function formatRuntimePercent(value: unknown) {
  const text = String(value ?? '').trim()
  if (!text) return ''
  return text.includes('%') ? text : `${text}%`
}

function formatRuntimeMsList(value: unknown, limit = 8) {
  return splitFanxiuList(value, limit).map(formatRuntimeMs).filter(Boolean).join('、')
}

function formatRuntimePercentList(value: unknown, hitCount: unknown) {
  const values = splitFanxiuList(value, 12).map(formatRuntimePercent).filter(Boolean)
  if (!values.length) return ''
  const uniqueValues = uniqueLabels(values)
  const count = Number(hitCount)
  if (uniqueValues.length === 1 && Number.isFinite(count) && count > 1) {
    return `${uniqueValues[0]} × ${count}`
  }
  return values.join('、')
}

function getRuntimeTimelineBadges(value: unknown) {
  return splitFanxiuList(value, 6).map((item) => {
    const parts = item.split(':')
    if (parts.length >= 3) {
      const career = LINGJIE_CAREER_LABELS[parts[0]] ?? parts[0]
      return `${career} · ${parts.slice(2).join(':')}`
    }
    return item
  })
}

function getRuntimeListCount(value: unknown) {
  return splitFanxiuList(value, 999).length
}

function getRuntimeSummaryStats(summary: FanxiuLingjieRuntimeSummary | null | undefined) {
  if (!summary) return []
  return [
    formatCountLabel(summary.projected_skill_count, '投影技能'),
    formatCountLabel(summary.profile_count, '职业画像'),
    formatCountLabel(summary.timeline_count, 'Timeline'),
    summary.careers?.length ? `职业 ${formatLingjieRuntimeCareers(summary.careers)}` : '',
  ].filter(Boolean)
}

function getRuntimeFamilyTitle(family: FanxiuLingjieRuntimeDamageFamily, index: number) {
  const hitCount = Number(family.hit_count)
  const total = formatRuntimePercent(family.total_hurt_percent)
  if (Number.isFinite(hitCount) && hitCount > 0) {
    return `${hitCount}段伤害${total ? ` · 总${total}` : ''}`
  }
  return `伤害模式 ${index + 1}`
}

function getRuntimeFamilyMeta(family: FanxiuLingjieRuntimeDamageFamily) {
  return [
    formatLingjieRuntimeCareers(family.careers),
    formatCountLabel(family.hit_count, '命中'),
    family.target_max ? `目标 ${family.target_max}` : '',
  ].filter(Boolean).join(' · ')
}

function getRuntimeFamilyBadges(family: FanxiuLingjieRuntimeDamageFamily) {
  return [
    family.skill_count ? `技能 ${family.skill_count}` : '',
    family.timeline_count ? `Timeline ${family.timeline_count}` : '',
    family.first_hit_ms && family.last_hit_ms ? `时段 ${formatRuntimeMs(family.first_hit_ms)}-${formatRuntimeMs(family.last_hit_ms)}` : '',
    family.cd_times ? `CD ${family.cd_times}` : '',
  ].filter(Boolean)
}

function getRuntimeTimelineTitle(timeline: FanxiuLingjieRuntimeTimelineSample, index: number) {
  return String(timeline.q_desc || timeline.timeline_id || `Timeline ${index + 1}`)
}

function getRuntimeTimelineMeta(timeline: FanxiuLingjieRuntimeTimelineSample) {
  return [
    timeline.timeline_id ? `ID ${timeline.timeline_id}` : '',
    formatLingjieRuntimeCareers(timeline.careers),
    timeline.q_track_time ? `时长 ${formatRuntimeMs(timeline.q_track_time)}` : '',
    formatCountLabel(timeline.hurt_event_count, '伤害帧'),
    formatCountLabel(getRuntimeListCount(timeline.effect_resources), '特效'),
    formatCountLabel(getRuntimeListCount(timeline.sound_ids), '音效'),
  ].filter(Boolean).join(' · ')
}

function getSkillQualityLabel(skill: FanxiuGongfaSkill | null | undefined) {
  const qualityName = String(skill?.quality_name || skill?.quality_tab || '').trim()
  if (qualityName) return qualityName
  if (skill?.quality === null || skill?.quality === undefined || skill?.quality === '') return ''
  return `品质 ${skill.quality}`
}

function getSkillMeta(skill: FanxiuGongfaSkill | null | undefined) {
  return uniqueLabels([
    skill?.pin ? `${skill.pin}阶` : '',
    skill?.type_name,
    skill?.sub_type_name,
    getSkillQualityLabel(skill),
  ]).join(' / ')
}

function getCardDescriptionText(card: FanxiuGongfaCard | null | undefined) {
  return String(card?.description_rich || card?.description || '').trim()
}

function getSkillText(skill: FanxiuGongfaSkill | null | undefined) {
  if (!skill) return ''
  return String(
    skill.describe_rich
    || skill.describe
    || skill.effect_describe_rich
    || skill.effect_describe
    || skill.additional_describe_rich
    || skill.additional_describe
    || '',
  ).trim()
}

function getSkillSections(skill: FanxiuGongfaSkill | null | undefined) {
  if (!skill) return []
  if ((skill.describe_rich || skill.describe) && skill.describe_sections?.length) {
    return skill.describe_sections
  }
  if ((skill.effect_describe_rich || skill.effect_describe) && skill.effect_describe_sections?.length) {
    return skill.effect_describe_sections
  }
  if ((skill.additional_describe_rich || skill.additional_describe) && skill.additional_describe_sections?.length) {
    return skill.additional_describe_sections
  }
  return []
}

function getSkillTitle(skill: FanxiuGongfaSkill, index: number) {
  return String(skill.skill_name || skill.name || skill.id || `技能 ${index + 1}`)
}

function getProgressionTitle(row: FanxiuGongfaProgressionRow, index: number) {
  const stageTitle = row.jie && `${row.jie}重` || row.star && `${row.star}星` || row.grade && `${row.grade}级`
  return String(row.title || stageTitle || row.name || row.id || `阶段 ${index + 1}`)
}

function getProgressionText(row: FanxiuGongfaProgressionRow) {
  return String(row.describe || row.upgrade_desc || row.top_describe || row.down_describe || '').trim()
}

function getProgressionRichText(row: FanxiuGongfaProgressionRow) {
  return String(
    row.describe_rich
    || row.describe
    || row.upgrade_desc_rich
    || row.upgrade_desc
    || row.top_describe_rich
    || row.top_describe
    || row.down_describe_rich
    || row.down_describe
    || '',
  ).trim()
}

function normalizeProgressionTextList(values: string[] | undefined) {
  return Array.isArray(values)
    ? values.map(value => String(value || '').trim()).filter(Boolean)
    : []
}

function getProgressionSectionTitle(section: FanxiuGongfaProgressionSection) {
  return String(section.title_rich || section.title || '').trim()
}

function getProgressionSectionLines(section: FanxiuGongfaProgressionSection) {
  const richLines = normalizeProgressionTextList(section.rich_lines)
  return richLines.length ? richLines : normalizeProgressionTextList(section.lines)
}

function getProgressionSections(row: FanxiuGongfaProgressionRow | null | undefined) {
  return (row?.describe_sections ?? []).filter(section => getProgressionSectionTitle(section) || getProgressionSectionLines(section).length)
}

function getProgressionMeta(row: FanxiuGongfaProgressionRow) {
  const values = [
    row.pin ? `${row.pin}阶` : '',
    row.jie ? `${row.jie}重` : '',
    row.star ? `${row.star}星` : '',
    row.grade ? `${row.grade}级` : '',
    row.id ? `ID ${row.id}` : '',
  ]
  return values.filter(Boolean).join(' / ')
}

function getProgressionConsumeSignature(row: FanxiuGongfaProgressionRow | null | undefined) {
  return getDisplayLinkedItems(row?.consume_items)
    .map(item => [
      item.id ?? '',
      item.count ?? '',
      item.name ?? '',
      item.icon ?? item.small_icon ?? '',
    ].join(':'))
    .join('|')
}

function getProgressionFazeSignature(row: FanxiuGongfaProgressionRow | null | undefined) {
  const resource = row?.faze_resource
  if (!resource) return 'none'
  const effect = resource.effect_resource
  return [
    resource.name ?? '',
    resource.head_name ?? '',
    resource.show_condition ?? '',
    resource.source ?? '',
    resource.tip_str ?? '',
    effect?.type ?? '',
    effect?.params ?? '',
  ].join('|')
}

function getProgressionDisplayItems(row: FanxiuGongfaProgressionRow | null | undefined) {
  return getDisplayLinkedItems(row?.consume_items)
}

function shouldShowProgressionItems(row: FanxiuGongfaProgressionRow, index: number) {
  const signature = getProgressionConsumeSignature(row)
  if (!signature) return false
  const previous = selectedProgressionRows.value[index - 1]
  return signature !== getProgressionConsumeSignature(previous)
}

function getProgressionAttrEntries(row: FanxiuGongfaProgressionRow) {
  const attr = row.attr ?? row.attributes
  if (!attr || typeof attr !== 'object' || Array.isArray(attr)) return []
  return Object.entries(attr as Record<string, unknown>)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => ({
      key,
      label: ATTR_LABELS[key] ?? key,
      value: String(value),
    }))
}

function getProgressionStage(row: FanxiuGongfaProgressionRow) {
  for (const key of PROGRESSION_STAGE_ORDER) {
    const value = row[key]
    const numberValue = Number(value)
    if (Number.isFinite(numberValue)) {
      const variable = PROGRESSION_VARIABLES[key]
      return { key, value: numberValue, unit: variable.unit, label: variable.label, symbol: variable.symbol }
    }
  }
  return null
}

function getProgressionTemplateSignature(row: FanxiuGongfaProgressionRow) {
  const text = getProgressionText(row)
  return text.replace(/(?<![#\w])([+-]?\d+(?:\.\d+)?)(%?)/g, '{n}')
}

function getNumericTokens(text: string) {
  const tokens: Array<{ raw: string; value: number; suffix: string; start: number; end: number }> = []
  const pattern = /(?<![#\w])([+-]?\d+(?:\.\d+)?)(%?)/g
  for (const match of text.matchAll(pattern)) {
    const raw = match[0]
    const value = Number(match[1])
    if (!Number.isFinite(value) || match.index === undefined) continue
    tokens.push({
      raw,
      value,
      suffix: match[2] || '',
      start: match.index,
      end: match.index + raw.length,
    })
  }
  return tokens
}

function nearlyEqual(left: number, right: number) {
  return Math.abs(left - right) < 1e-8
}

function formatFormulaNumber(value: number) {
  if (nearlyEqual(value, Math.round(value))) return String(Math.round(value))
  return String(Number(value.toFixed(4)))
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function parseSignedCoefficient(value: string) {
  if (!value || value === '+') return 1
  if (value === '-') return -1
  return Number(value)
}

function formatLinearFormulaParts(intercept: number, slope: number, symbol: string) {
  if (nearlyEqual(slope, 0)) return formatFormulaNumber(intercept)
  const slopeText = nearlyEqual(Math.abs(slope), 1) ? '' : formatFormulaNumber(Math.abs(slope))
  const variablePart = `${slope < 0 ? '-' : ''}${slopeText}${symbol}`
  if (nearlyEqual(intercept, 0)) return variablePart
  if (intercept < 0 && slope > 0) return `${variablePart}${formatFormulaNumber(intercept)}`
  const joiner = slope > 0 ? '+' : ''
  return `${formatFormulaNumber(intercept)}${joiner}${variablePart}`
}

function parseLinearFormulaText(value: string, symbol: string) {
  const text = value.trim()
  if (!text.includes(symbol)) {
    const numeric = Number(text)
    return Number.isFinite(numeric) ? { intercept: numeric, slope: 0 } : null
  }

  const escapedSymbol = escapeRegExp(symbol)
  const withIntercept = text.match(new RegExp(`^([+-]?\\d+(?:\\.\\d+)?)([+-]\\d*(?:\\.\\d+)?)${escapedSymbol}$`))
  if (withIntercept) {
    const intercept = Number(withIntercept[1])
    const slope = parseSignedCoefficient(withIntercept[2])
    if (Number.isFinite(intercept) && Number.isFinite(slope)) return { intercept, slope }
  }

  const onlyVariable = text.match(new RegExp(`^([+-]?\\d*(?:\\.\\d+)?)${escapedSymbol}$`))
  if (onlyVariable) {
    const slope = parseSignedCoefficient(onlyVariable[1])
    if (Number.isFinite(slope)) return { intercept: 0, slope }
  }

  return null
}

function formatFormulaWithSuffix(intercept: number, slope: number, symbol: string, suffix: string) {
  const formula = formatLinearFormulaParts(intercept, slope, symbol)
  return nearlyEqual(slope, 0) ? `${formula}${suffix}` : `(${formula})${suffix}`
}

function blocksAdditiveFormulaMerge(value: string | undefined) {
  return value === '*' || value === '×' || value === '/'
}

function normalizeMergedFormulaText(text: string, symbol: string) {
  const escapedSymbol = escapeRegExp(symbol)
  const formulaThenConstant = new RegExp(`\\(([^()]*${escapedSymbol}[^()]*)\\)%([+-])(\\d+(?:\\.\\d+)?)%`, 'g')
  const constantThenFormula = new RegExp(`(\\d+(?:\\.\\d+)?)%([+-])\\(([^()]*${escapedSymbol}[^()]*)\\)%`, 'g')

  return text
    .replace(formulaThenConstant, (match: string, formulaText: string, operator: string, constantText: string, offset: number, source: string) => {
      if (blocksAdditiveFormulaMerge(source[offset - 1]) || blocksAdditiveFormulaMerge(source[offset + match.length])) return match
      const formula = parseLinearFormulaText(formulaText, symbol)
      const constant = Number(constantText)
      if (!formula || !Number.isFinite(constant)) return match
      const delta = operator === '-' ? -constant : constant
      return formatFormulaWithSuffix(formula.intercept + delta, formula.slope, symbol, '%')
    })
    .replace(constantThenFormula, (match: string, constantText: string, operator: string, formulaText: string, offset: number, source: string) => {
      if (blocksAdditiveFormulaMerge(source[offset - 1]) || blocksAdditiveFormulaMerge(source[offset + match.length])) return match
      const formula = parseLinearFormulaText(formulaText, symbol)
      const constant = Number(constantText)
      if (!formula || !Number.isFinite(constant)) return match
      if (operator === '-') {
        return formatFormulaWithSuffix(constant - formula.intercept, -formula.slope, symbol, '%')
      }
      return formatFormulaWithSuffix(formula.intercept + constant, formula.slope, symbol, '%')
    })
}

function buildLinearFormula(xs: number[], ys: number[], symbol = 'x') {
  if (xs.length !== ys.length || xs.length < 2) return null
  const denominator = xs[xs.length - 1] - xs[0]
  if (nearlyEqual(denominator, 0)) return null
  const slope = (ys[ys.length - 1] - ys[0]) / denominator
  const intercept = ys[0] - slope * xs[0]
  if (!ys.every((value, index) => nearlyEqual(value, intercept + slope * xs[index]))) return null
  return formatLinearFormulaParts(intercept, slope, symbol)
}

function mergeNumericTemplate(text: string, rows: FanxiuGongfaProgressionRow[], stages: number[], symbol: string) {
  const tokenRows = rows.map(row => getNumericTokens(getProgressionText(row)))
  const firstTokens = tokenRows[0] ?? []
  if (!firstTokens.length || tokenRows.some(tokens => tokens.length !== firstTokens.length)) return null
  const replacements = new Map<number, string>()
  let variableCount = 0
  for (let index = 0; index < firstTokens.length; index += 1) {
    const suffix = firstTokens[index].suffix
    if (tokenRows.some(tokens => tokens[index].suffix !== suffix)) return null
    const values = tokenRows.map(tokens => tokens[index].value)
    if (values.every(value => nearlyEqual(value, values[0]))) continue
    const formula = buildLinearFormula(stages, values, symbol)
    if (!formula) return null
    replacements.set(index, suffix ? `(${formula})${suffix}` : formula)
    variableCount += 1
  }
  if (!variableCount) return { text, variableCount }
  let merged = ''
  let cursor = 0
  firstTokens.forEach((token, index) => {
    merged += text.slice(cursor, token.start)
    merged += replacements.get(index) ?? token.raw
    cursor = token.end
  })
  merged += text.slice(cursor)
  return { text: normalizeMergedFormulaText(merged, symbol), variableCount }
}

function getProgressionAttrSignature(row: FanxiuGongfaProgressionRow) {
  return getProgressionAttrEntries(row).map(attr => attr.key).join('|')
}

function getProgressionIdRange(rows: FanxiuGongfaProgressionRow[]) {
  const ids = rows.map(row => Number(row.id)).filter(Number.isFinite)
  if (ids.length !== rows.length) return ''
  return `ID ${ids[0]}-${ids[ids.length - 1]}`
}

function buildProgressionVariableEntries(stages: Array<ReturnType<typeof getProgressionStage>>) {
  const first = stages[0]
  const last = stages[stages.length - 1]
  if (!first || !last) return []
  return [{
    key: first.key,
    symbol: first.symbol,
    label: first.label,
    range: `${formatFormulaNumber(first.value)}-${formatFormulaNumber(last.value)}${first.unit}`,
  }]
}

function getProgressionStepVariableLabel(variable: ProgressionStepVariable) {
  return `${variable.symbol}=${formatFormulaNumber(variable.value)}${variable.unit || ''}`
}

function getGroupStepVariables(group: ProgressionDisplayGroup | ProgressionViewGroup) {
  return 'stepVariables' in group ? group.stepVariables : []
}

function getProgressionDisplayTitle(group: ProgressionDisplayGroup | ProgressionViewGroup) {
  const stepVariables = getGroupStepVariables(group)
  if (!group.variables.length && stepVariables.length === 1 && stepVariables[0].kind === 'stage') {
    return getProgressionStepVariableLabel(stepVariables[0])
  }
  const title = group.variables.length
    ? group.variables.map(variable => `${variable.symbol}=${variable.range}`).join('，')
    : group.title
  const stepLabels = stepVariables.map(getProgressionStepVariableLabel)
  return [title, ...stepLabels].filter(Boolean).join('，')
}

function getProgressionTitleHint(group: ProgressionDisplayGroup | ProgressionViewGroup) {
  const variableHints = group.variables.map(variable => `${variable.symbol} 表示${variable.label}`)
  const stepHints = getGroupStepVariables(group).map(variable => variable.title)
  return [...variableHints, ...stepHints].filter(Boolean).join('；')
}

function formatProgressionNumberRange(values: unknown[], label: string) {
  const numericValues = values.map(value => Number(value)).filter(Number.isFinite)
  if (!numericValues.length) return ''
  if (numericValues.every(value => nearlyEqual(value, numericValues[0]))) {
    return `${label} ${formatFormulaNumber(numericValues[0])}`
  }
  if (numericValues.every((value, index) => index === 0 || nearlyEqual(value, numericValues[index - 1] + 1))) {
    return `${label} ${formatFormulaNumber(numericValues[0])}-${formatFormulaNumber(numericValues[numericValues.length - 1])}`
  }
  return `${label} ${uniqueLabels(numericValues.map(formatFormulaNumber)).slice(0, 4).join('、')}`
}

function buildProgressionFazeSummary(rows: FanxiuGongfaProgressionRow[]): ProgressionFazeSummary | null {
  const resources = rows.map(row => row.faze_resource).filter(Boolean)
  if (!resources.length) return null
  const first = resources[0]
  const title = String(first?.name || first?.head_name || first?.id || '规则资源')
  const typeValues = uniqueLabels(resources.map(resource => resource?.effect_resource?.type).filter(value => value !== null && value !== undefined && value !== ''))
  const meta = [
    formatProgressionNumberRange(resources.map(resource => resource?.id), 'Faze'),
    formatProgressionNumberRange(resources.map(resource => resource?.last_grade), '上阶 Faze'),
    formatProgressionNumberRange(resources.map(resource => resource?.effect_resource?.id), 'Effect'),
    typeValues.length === 1 ? `Type ${typeValues[0]}` : '',
  ].filter(Boolean).join(' / ')
  const seen = new Set<string>()
  const tips: ProgressionFazeTip[] = []
  for (const resource of resources) {
    for (const tip of resource?.tips ?? []) {
      const text = String(tip?.text || '').trim()
      if (!text) continue
      const key = `${tip?.code ?? ''}|${text}`
      if (seen.has(key)) continue
      seen.add(key)
      tips.push({ code: tip?.code, reason: tip?.reason, text })
    }
  }
  return { title, meta, tips }
}

function buildMergedAttrEntries(rows: FanxiuGongfaProgressionRow[], stages: number[], symbol: string) {
  const attrRows = rows.map(row => getProgressionAttrEntries(row))
  const firstAttrs = attrRows[0] ?? []
  const entries: ProgressionAttrEntry[] = []
  let variableCount = 0
  for (let index = 0; index < firstAttrs.length; index += 1) {
    const key = firstAttrs[index].key
    const label = firstAttrs[index].label
    const rawValues = attrRows.map(attrs => attrs[index]?.value)
    const numericValues = rawValues.map(value => Number(value))
    if (numericValues.every(Number.isFinite)) {
      if (numericValues.every(value => nearlyEqual(value, numericValues[0]))) {
        entries.push({ key, label, value: formatFormulaNumber(numericValues[0]) })
        continue
      }
      const formula = buildLinearFormula(stages, numericValues, symbol)
      if (!formula) return null
      variableCount += 1
      entries.push({ key, label, value: formula })
      continue
    }
    const uniqueValues = uniqueLabels(rawValues)
    if (uniqueValues.length !== 1) return null
    entries.push({ key, label, value: uniqueValues[0] })
  }
  return { entries, variableCount }
}

function areProgressionRowsCompatible(rows: FanxiuGongfaProgressionRow[]) {
  if (!rows.length) return false
  const firstSignature = getProgressionTemplateSignature(rows[0])
  const firstAttrSignature = getProgressionAttrSignature(rows[0])
  const firstConsumeSignature = getProgressionConsumeSignature(rows[0])
  const firstFazeSignature = getProgressionFazeSignature(rows[0])
  return rows.every(row => {
    return !hasFeatureLink(row)
      && getProgressionTemplateSignature(row) === firstSignature
      && getProgressionAttrSignature(row) === firstAttrSignature
      && getProgressionConsumeSignature(row) === firstConsumeSignature
      && getProgressionFazeSignature(row) === firstFazeSignature
  })
}

function canMergeProgressionRows(rows: FanxiuGongfaProgressionRow[]) {
  return rows.length >= 3 && areProgressionRowsCompatible(rows)
}

function buildMergedProgressionGroup(
  rows: FanxiuGongfaProgressionRow[],
  startIndex: number,
  options: ProgressionBuildOptions = {},
): ProgressionDisplayGroup | null {
  if (!canMergeProgressionRows(rows)) return null
  const stages = rows.map(row => getProgressionStage(row))
  if (stages.some(stage => !stage)) return null
  const stageValues = stages.map(stage => stage?.value ?? 0)
  if (!stageValues.every((value, index) => index === 0 || nearlyEqual(value, stageValues[index - 1] + 1))) return null
  const unit = stages[0]?.unit ?? ''
  if (stages.some(stage => stage?.unit !== unit)) return null
  const variableSymbol = stages[0]?.symbol ?? 'x'
  const baseText = getProgressionText(rows[0])
  const mergedText = baseText
    ? mergeNumericTemplate(baseText, rows, stageValues, variableSymbol)
    : { text: '', variableCount: 0 }
  if (!mergedText) return null
  const mergedAttrs = buildMergedAttrEntries(rows, stageValues, variableSymbol)
  if (!mergedAttrs) return null
  if (!options.allowStaticMerge && mergedText.variableCount + mergedAttrs.variableCount <= 0) return null
  const startStage = stageValues[0]
  const endStage = stageValues[stageValues.length - 1]
  const idRange = getProgressionIdRange(rows)
  return {
    key: `merged-${rows[0].row_key ?? rows[0].id ?? startIndex}-${rows.length}`,
    rows,
    first: rows[0],
    startIndex,
    merged: true,
    title: `${startStage}-${endStage}${unit}`,
    meta: [`${rows.length}条`, idRange].filter(Boolean).join(' / '),
    variables: buildProgressionVariableEntries(stages),
    attrEntries: mergedAttrs.entries,
    text: mergedText.text,
    richText: mergedText.text,
    fazeSummary: buildProgressionFazeSummary(rows),
  }
}

function buildSingleProgressionGroup(row: FanxiuGongfaProgressionRow, index: number): ProgressionDisplayGroup {
  return {
    key: String(row.row_key ?? row.id ?? index),
    rows: [row],
    first: row,
    startIndex: index,
    merged: false,
    title: getProgressionTitle(row, index),
    meta: getProgressionMeta(row),
    variables: [],
    attrEntries: getProgressionAttrEntries(row),
    text: getProgressionText(row),
    richText: getProgressionRichText(row),
    fazeSummary: buildProgressionFazeSummary([row]),
  }
}

function buildProgressionDisplayGroups(rows: FanxiuGongfaProgressionRow[], options: ProgressionBuildOptions = {}) {
  const groups: ProgressionDisplayGroup[] = []
  let index = 0
  while (index < rows.length) {
    let end = index + 1
    while (end < rows.length) {
      const candidate = rows.slice(index, end + 1)
      const stages = candidate.map(row => getProgressionStage(row))
      const continuous = stages.every(Boolean)
        && stages.every((stage, stageIndex) => stageIndex === 0 || (
          stage?.unit === stages[0]?.unit
          && nearlyEqual((stage?.value ?? 0), (stages[stageIndex - 1]?.value ?? 0) + 1)
        ))
      if (!continuous || !areProgressionRowsCompatible(candidate)) break
      end += 1
    }
    let merged: ProgressionDisplayGroup | null = null
    for (let candidateEnd = end; candidateEnd > index + 1; candidateEnd -= 1) {
      merged = buildMergedProgressionGroup(rows.slice(index, candidateEnd), index, options)
      if (merged) break
    }
    if (merged) {
      groups.push(merged)
      index += merged.rows.length
      continue
    }
    groups.push(buildSingleProgressionGroup(rows[index], index))
    index += 1
  }
  return groups
}

function normalizeProgressionDisplayText(value: string) {
  return value.replace(/\s+/g, ' ').trim()
}

function splitProgressionParagraphs(value: string) {
  return String(value || '')
    .replace(/\s+(?=(?:<color=#[0-9a-fA-F]{3,8}>)?每(?:周|日)首次)/g, '\n\n')
    .replace(/\s+(?=(?:<color=#[0-9a-fA-F]{3,8}>)?【[^】]{1,30}】(?:<\/color>)?活动持续期间)/g, '\n\n')
    .split(/\n\s*\n+/)
    .map(part => part.trim())
    .filter(Boolean)
}

function getProgressionParagraphPlanKey(groupIndex: number, paragraphIndex: number) {
  return `${groupIndex}:${paragraphIndex}`
}

function getProgressionParagraphTemplateSignature(value: string) {
  return value.replace(/(?<![#\w])([+-]?\d+(?:\.\d+)?)(%?)/g, '{n}')
}

function getProgressionGroupStageBounds(group: ProgressionDisplayGroup) {
  const stages = group.rows.map(row => getProgressionStage(row)).filter(Boolean)
  if (!stages.length || stages.length !== group.rows.length) return null
  const first = stages[0]
  const last = stages[stages.length - 1]
  if (!first || !last) return null
  if (stages.some(stage => stage?.key !== first.key || stage?.unit !== first.unit || stage?.symbol !== first.symbol)) return null
  return {
    key: first.key,
    symbol: first.symbol,
    label: first.label,
    unit: first.unit,
    start: first.value,
    end: last.value,
  }
}

type ProgressionGroupStageBounds = NonNullable<ReturnType<typeof getProgressionGroupStageBounds>>

function getProgressionGroupStageSpan(bounds: ProgressionGroupStageBounds) {
  return bounds.end - bounds.start + 1
}

function getProgressionStepIndexKey(bounds: ProgressionGroupStageBounds) {
  return [
    bounds.key,
    bounds.symbol,
    bounds.unit,
    formatFormulaNumber(getProgressionGroupStageSpan(bounds)),
  ].join('|')
}

function isProgressionBlockStepBounds(bounds: ProgressionGroupStageBounds) {
  return bounds.key === 'jie'
    && bounds.unit === '重'
    && bounds.start >= 5
    && getProgressionGroupStageSpan(bounds) >= 5
}

function getAbsoluteProgressionStepIndex(bounds: ProgressionGroupStageBounds) {
  if (isProgressionBlockStepBounds(bounds)) {
    return Math.floor(bounds.start / 5)
  }
  return null
}

function buildProgressionGroupStepIndexes(boundsList: Array<ProgressionGroupStageBounds | null>) {
  const grouped = new Map<string, Array<{ index: number; bounds: ProgressionGroupStageBounds; span: number }>>()
  boundsList.forEach((bounds, index) => {
    if (!bounds) return
    const absoluteIndex = getAbsoluteProgressionStepIndex(bounds)
    if (absoluteIndex !== null && Number.isFinite(absoluteIndex)) {
      return
    }
    const span = getProgressionGroupStageSpan(bounds)
    if (!Number.isFinite(span) || span <= 0) return
    const key = getProgressionStepIndexKey(bounds)
    const items = grouped.get(key) ?? []
    items.push({ index, bounds, span })
    grouped.set(key, items)
  })

  const indexes = new Map<number, number>()
  boundsList.forEach((bounds, index) => {
    if (!bounds) return
    const absoluteIndex = getAbsoluteProgressionStepIndex(bounds)
    if (absoluteIndex !== null && Number.isFinite(absoluteIndex)) {
      indexes.set(index, absoluteIndex)
    }
  })
  Array.from(grouped.values()).forEach(items => {
    const sorted = [...items].sort((left, right) => left.bounds.start - right.bounds.start)
    if (!sorted.length) return
    const positiveDiffs = sorted
      .slice(1)
      .map((item, index) => item.bounds.start - sorted[index].bounds.start)
      .filter(diff => Number.isFinite(diff) && diff > 0)
    const stageStep = sorted[0].span > 1 ? sorted[0].span : positiveDiffs[0] ?? 1
    if (!Number.isFinite(stageStep) || nearlyEqual(stageStep, 0)) return
    const baseStart = sorted[0].bounds.start

    sorted.forEach(item => {
      const value = ((item.bounds.start - baseStart) / stageStep) + 1
      if (Number.isFinite(value) && nearlyEqual(value, Math.round(value))) {
        indexes.set(item.index, Math.round(value))
      }
    })
  })

  return indexes
}

function getProgressionStepVariableSymbol(primarySymbol: string) {
  const symbol = primarySymbol.trim()
  if (/^[a-z]$/.test(symbol)) return symbol.toUpperCase()
  return ['X', 'Y', 'Z', 'N', 'M', 'K'].find(candidate => candidate !== symbol) ?? 'T'
}

function isPrimaryStageProgressionRun(run: ProgressionStepOccurrence[]) {
  if (run.length < 2) return false
  const first = run[0]
  return run.every(item => {
    if (
      item.bounds.key !== first.bounds.key
      || item.bounds.unit !== first.bounds.unit
      || item.bounds.symbol !== first.bounds.symbol
      || getProgressionGroupStageSpan(item.bounds) !== 1
    ) return false
    return true
  })
}

function buildProgressionRunVariableSpec(
  run: ProgressionStepOccurrence[],
  groupStepIndexes: Map<number, number>,
) {
  const first = run[0]
  if (!first) return null
  if (isPrimaryStageProgressionRun(run)) {
    return {
      symbol: first.bounds.symbol,
      unit: first.bounds.unit,
      kind: 'stage' as const,
      values: run.map(item => item.bounds.start),
    }
  }
  const values = run
    .map(item => groupStepIndexes.get(item.groupIndex))
    .filter((value): value is number => typeof value === 'number')
  if (values.length !== run.length) return null
  return {
    symbol: getProgressionStepVariableSymbol(first.bounds.symbol),
    unit: '',
    kind: 'block' as const,
    values,
  }
}

function formatStepVariableFormula(firstValue: number, delta: number, variableSymbol: string) {
  if (nearlyEqual(delta, 0)) return formatFormulaNumber(firstValue)
  const firstText = formatFormulaNumber(firstValue)
  const deltaText = formatFormulaNumber(Math.abs(delta))
  const deltaTerm = nearlyEqual(Math.abs(delta), 1)
    ? `(${variableSymbol}-1)`
    : `${deltaText}(${variableSymbol}-1)`
  if (nearlyEqual(firstValue, 0)) return delta > 0 ? deltaTerm : `-${deltaTerm}`
  return `${firstText}${delta > 0 ? '+' : '-'}${deltaTerm}`
}

function buildProgressionStepFormulaText(
  text: string,
  tokenRows: Array<ReturnType<typeof getNumericTokens>>,
  varyingIndexes: number[],
  variableSymbol: string,
  variableValues: number[],
) {
  const firstTokens = tokenRows[0] ?? []
  const replacements = new Map<number, string>()

  for (const tokenIndex of varyingIndexes) {
    const token = firstTokens[tokenIndex]
    if (!token) return null
    const values = tokenRows.map(tokens => tokens[tokenIndex]?.value).filter((value): value is number => typeof value === 'number')
    if (values.length !== tokenRows.length) return null
    const formula = buildLinearFormula(variableValues, values, variableSymbol)
    if (!formula) return null
    replacements.set(tokenIndex, token.suffix ? `(${formula})${token.suffix}` : formula)
  }

  let merged = ''
  let cursor = 0
  firstTokens.forEach((token, index) => {
    merged += text.slice(cursor, token.start)
    merged += replacements.get(index) ?? token.raw
    cursor = token.end
  })
  merged += text.slice(cursor)
  return normalizeMergedFormulaText(merged, variableSymbol)
}

function buildProgressionStepRunPlan(
  run: ProgressionStepOccurrence[],
  groupStepIndexes: Map<number, number>,
): ProgressionStepRunPlan | null {
  if (run.length < 2) return null
  const stageStarts = run.map(item => item.bounds.start)
  const stageStep = stageStarts[1] - stageStarts[0]
  if (!Number.isFinite(stageStep) || nearlyEqual(stageStep, 0)) return null
  if (!stageStarts.every((value, index) => index === 0 || nearlyEqual(value - stageStarts[index - 1], stageStep))) return null

  const variableSpec = buildProgressionRunVariableSpec(run, groupStepIndexes)
  if (!variableSpec) return null
  const variableValues = variableSpec.values
  const variableStep = variableValues[1] - variableValues[0]
  if (!Number.isFinite(variableStep) || nearlyEqual(variableStep, 0)) return null
  if (!variableValues.every((value, index) => index === 0 || nearlyEqual(value - variableValues[index - 1], variableStep))) return null

  const firstTokens = run[0].tokens
  const varyingIndexes: number[] = []
  for (let tokenIndex = 0; tokenIndex < firstTokens.length; tokenIndex += 1) {
    const values = run.map(item => item.tokens[tokenIndex].value)
    if (values.every(value => nearlyEqual(value, values[0]))) continue
    const delta = (values[1] - values[0]) / variableStep
    if (!Number.isFinite(delta) || !values.every((value, index) => nearlyEqual(value, values[0] + delta * (variableValues[index] - variableValues[0])))) {
      return null
    }
    varyingIndexes.push(tokenIndex)
  }
  if (!varyingIndexes.length) return null
  if (run.length === 2) {
    const span = getProgressionGroupStageSpan(run[0].bounds)
    const sameSpan = run.every(item => nearlyEqual(getProgressionGroupStageSpan(item.bounds), span))
    const adjacentSpan = nearlyEqual(stageStep, span)
    const adjacentVariable = nearlyEqual(variableStep, 1)
    if (varyingIndexes.length !== 1 || !sameSpan || !adjacentSpan || !adjacentVariable || span <= 1) return null
  }
  return {
    run,
    variableValues,
    varyingIndexes,
    variableSymbol: variableSpec.symbol,
    variableUnit: variableSpec.unit,
    variableKind: variableSpec.kind,
  }
}

function buildProgressionStepParagraphPlans(groups: ProgressionDisplayGroup[]) {
  const groupParts = groups.map(group => splitProgressionParagraphs(group.text))
  const groupBounds = groups.map(group => getProgressionGroupStageBounds(group))
  const groupStepIndexes = buildProgressionGroupStepIndexes(groupBounds)
  const plans = new Map<string, ProgressionParagraphStepPlan>()

  for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
    const parts = groupParts[groupIndex] ?? []
    const bounds = groupBounds[groupIndex]
    if (!bounds) continue

    for (let paragraphIndex = 0; paragraphIndex < parts.length; paragraphIndex += 1) {
      const planKey = getProgressionParagraphPlanKey(groupIndex, paragraphIndex)
      if (plans.has(planKey)) continue

      const part = parts[paragraphIndex]
      const tokens = getNumericTokens(part)
      if (!tokens.length) continue
      const signature = getProgressionParagraphTemplateSignature(part)
      const run: ProgressionStepOccurrence[] = [{ groupIndex, paragraphIndex, part, tokens, bounds }]

      for (let nextGroupIndex = groupIndex + 1; nextGroupIndex < groups.length; nextGroupIndex += 1) {
        const nextBounds = groupBounds[nextGroupIndex]
        if (!nextBounds || nextBounds.key !== bounds.key || nextBounds.unit !== bounds.unit) break
        const nextParts = groupParts[nextGroupIndex] ?? []
        const matchedParagraphIndex = nextParts.findIndex(nextPart => getProgressionParagraphTemplateSignature(nextPart) === signature)
        if (matchedParagraphIndex < 0) break
        const nextPart = nextParts[matchedParagraphIndex]
        const nextTokens = getNumericTokens(nextPart)
        if (
          nextTokens.length !== tokens.length
          || nextTokens.some((token, tokenIndex) => token.suffix !== tokens[tokenIndex].suffix)
        ) break
        run.push({
          groupIndex: nextGroupIndex,
          paragraphIndex: matchedParagraphIndex,
          part: nextPart,
          tokens: nextTokens,
          bounds: nextBounds,
        })
      }

      let runPlan: ProgressionStepRunPlan | null = null
      for (let candidateLength = run.length; candidateLength >= 2; candidateLength -= 1) {
        runPlan = buildProgressionStepRunPlan(run.slice(0, candidateLength), groupStepIndexes)
        if (runPlan) break
      }
      if (!runPlan) continue

      const { variableValues, varyingIndexes, variableSymbol, variableUnit, variableKind } = runPlan
      const first = runPlan.run[0]
      const formulaText = buildProgressionStepFormulaText(
        first.part,
        runPlan.run.map(item => item.tokens),
        varyingIndexes,
        variableSymbol,
        variableValues,
      )
      if (!formulaText) continue
      const variableRange = `${variableSymbol}=${formatFormulaNumber(variableValues[0])}-${formatFormulaNumber(variableValues[variableValues.length - 1])}${variableUnit}`
      const summary = `${variableRange}\n${formulaText}`
      plans.set(planKey, {
        replacement: formulaText,
        stepVariable: {
          symbol: variableSymbol,
          value: variableValues[0],
          unit: variableUnit,
          kind: variableKind,
          title: summary,
        },
      })
      runPlan.run.slice(1).forEach((occurrence, occurrenceIndex) => {
        const variableValue = variableValues[occurrenceIndex + 1]
        plans.set(getProgressionParagraphPlanKey(occurrence.groupIndex, occurrence.paragraphIndex), {
          hidden: true,
          badgeLabel: '公式同上',
          badgeTitle: summary,
          stepVariable: {
            symbol: variableSymbol,
            value: variableValue,
            unit: variableUnit,
            kind: variableKind,
            title: summary,
          },
        })
      })
    }
  }

  return plans
}

function getProgressionAttrDisplaySignature(entries: ProgressionAttrEntry[]) {
  return entries.map(entry => `${entry.key}:${entry.value}`).join('|')
}

function getProgressionAttrSummary(entries: ProgressionAttrEntry[]) {
  return entries.map(entry => `${entry.label} ${entry.value}`).join('，')
}

function renderProgressionAttrTooltip(entries: ProgressionAttrEntry[]) {
  if (!entries.length) return ''
  return entries.map(entry => [
    '<div class="inherit-tooltip-entry">',
    `<b>${escapeHtml(entry.label)}</b>`,
    `<span>${renderFanxiuText(entry.value)}</span>`,
    '</div>',
  ].join('')).join('')
}

function getProgressionFazeBehaviorSignature(summary: ProgressionFazeSummary | null) {
  if (!summary) return ''
  const stableMeta = summary.meta
    .split('/')
    .map(part => part.trim())
    .filter(part => part && !/^(Faze|上阶 Faze|Effect)\b/.test(part))
    .join('/')
  const tips = summary.tips.map(tip => `${tip.code ?? ''}:${tip.text ?? ''}`).join('|')
  return [summary.title, stableMeta, tips].join('|')
}

function getProgressionFazeSummaryText(summary: ProgressionFazeSummary | null) {
  if (!summary) return ''
  const tips = summary.tips.map(tip => `${tip.code ? `${tip.code} ` : ''}${tip.text ?? ''}`)
  return [summary.title, ...tips].filter(Boolean).join('\n')
}

function renderProgressionFazeSummaryTooltip(summary: ProgressionFazeSummary | null) {
  if (!summary) return ''
  const title = summary.title
    ? `<div class="inherit-tooltip-title">${renderFanxiuText(summary.title)}</div>`
    : ''
  const tips = summary.tips.map(tip => [
    '<div class="inherit-tooltip-entry">',
    tip.code ? `<b>${escapeHtml(String(tip.code))}</b>` : '',
    `<span>${renderFanxiuText(tip.text ?? '')}</span>`,
    '</div>',
  ].join('')).join('')
  return [title, tips].filter(Boolean).join('')
}

function renderInheritedBadgeContent(badge: ProgressionInheritedBadge) {
  return badge.html || renderFanxiuText(badge.title)
}

function addProgressionStepVariable(target: ProgressionStepVariable[], variable: ProgressionStepVariable | undefined) {
  if (!variable) return
  const exists = target.some(item => {
    return item.symbol === variable.symbol
      && item.unit === variable.unit
      && item.kind === variable.kind
      && nearlyEqual(item.value, variable.value)
  })
  if (!exists) target.push(variable)
}

function buildProgressionViewGroups(groups: ProgressionDisplayGroup[]) {
  const paragraphStepPlans = buildProgressionStepParagraphPlans(groups)

  return groups.map((group, index): ProgressionViewGroup => {
    const previous = index > 0 ? groups[index - 1] : null
    const inheritedBadges: ProgressionInheritedBadge[] = []
    const stepVariables: ProgressionStepVariable[] = []
    let displayAttrEntries = group.attrEntries
    let displayText = group.text
    let displayRichText = group.richText
    let displayFazeSummary = group.fazeSummary

    if (previous && group.attrEntries.length && getProgressionAttrDisplaySignature(group.attrEntries) === getProgressionAttrDisplaySignature(previous.attrEntries)) {
      displayAttrEntries = []
      inheritedBadges.push({
        key: 'attr',
        label: '属性同上',
        title: getProgressionAttrSummary(group.attrEntries),
        html: renderProgressionAttrTooltip(group.attrEntries),
      })
    }

    if (group.text) {
      const previousTextKeys = previous
        ? new Set(splitProgressionParagraphs(previous.text).map(normalizeProgressionDisplayText))
        : new Set<string>()
      const parts = splitProgressionParagraphs(group.text)
      const displayParts: string[] = []
      const hiddenRepeatedParts: string[] = []
      let hasStepHiddenPart = false

      for (let partIndex = 0; partIndex < parts.length; partIndex += 1) {
        const part = parts[partIndex]
        const plan = paragraphStepPlans.get(getProgressionParagraphPlanKey(index, partIndex))
        addProgressionStepVariable(stepVariables, plan?.stepVariable)
        if (plan?.hidden) {
          hasStepHiddenPart = true
          continue
        }
        if (previousTextKeys.has(normalizeProgressionDisplayText(part))) {
          hiddenRepeatedParts.push(part)
          continue
        }
        displayParts.push(plan?.replacement ?? part)
      }

      if (hiddenRepeatedParts.length) {
        inheritedBadges.push({
          key: 'text',
          label: displayParts.length ? '基础效果同上' : '效果同上',
          title: hiddenRepeatedParts.join('\n\n'),
        })
      }

      if (hasStepHiddenPart) {
        const hiddenStepPlans = parts
          .map((_, partIndex) => paragraphStepPlans.get(getProgressionParagraphPlanKey(index, partIndex)))
          .filter((plan): plan is ProgressionParagraphStepPlan => Boolean(plan?.hidden))
        const badgeGroups = new Map<string, string[]>()
        hiddenStepPlans.forEach(plan => {
          const label = plan.badgeLabel || '等差变化'
          const titles = badgeGroups.get(label) ?? []
          if (plan.badgeTitle) titles.push(plan.badgeTitle)
          badgeGroups.set(label, titles)
        })
        Array.from(badgeGroups.entries()).forEach(([label, titles], badgeIndex) => {
          inheritedBadges.push({
            key: `step-text-${badgeIndex}`,
            label,
            title: uniqueLabels(titles).filter(Boolean).join('\n\n'),
          })
        })
      }

      if (displayParts.length < parts.length || displayParts.some((part, partIndex) => part !== parts[partIndex])) {
        displayText = displayParts.join('\n\n')
        displayRichText = displayText
      }
    }

    if (previous && group.fazeSummary && getProgressionFazeBehaviorSignature(group.fazeSummary) === getProgressionFazeBehaviorSignature(previous.fazeSummary)) {
      displayFazeSummary = null
      inheritedBadges.push({
        key: 'faze',
        label: '规则奖励同上',
        title: getProgressionFazeSummaryText(group.fazeSummary),
        html: renderProgressionFazeSummaryTooltip(group.fazeSummary),
      })
    }

    return {
      ...group,
      inheritedBadges,
      stepVariables,
      displayAttrEntries,
      displayText,
      displayRichText,
      displayFazeSummary,
    }
  })
}

function shouldUseProgressionRichText(group: ProgressionViewGroup) {
  return !group.merged
    && Boolean(group.displayText)
    && normalizeProgressionDisplayText(group.displayText) === normalizeProgressionDisplayText(group.text)
}

function getProgressionRenderedText(group: ProgressionViewGroup) {
  return shouldUseProgressionRichText(group)
    ? String(group.displayRichText || group.displayText || '').trim()
    : String(group.displayText || '').trim()
}

function shouldRenderProgressionSections(group: ProgressionViewGroup) {
  return shouldUseProgressionRichText(group) && getProgressionSections(group.first).length > 0
}

function splitFanxiuList(value: unknown, limit = 4) {
  return String(value || '')
    .split('、')
    .map(item => item.trim())
    .filter(Boolean)
    .slice(0, limit)
}

function hasFeatureLink(row: FanxiuGongfaProgressionRow) {
  const link = row.feature_link
  return Boolean(link?.feature || link?.source_describe || link?.config_descriptions || link?.timelines || link?.effect_paths || link?.sound_ids)
}

function getFeatureLinkTitle(row: FanxiuGongfaProgressionRow) {
  const link = row.feature_link
  if (!link) return ''
  const configTitle = [link.config_descriptions, link.timelines].filter(Boolean).join(' / ')
  return configTitle || `Feature ${link.feature || row.feature || ''}`.trim()
}

function getFeatureLinkStatus(row: FanxiuGongfaProgressionRow) {
  const link = row.feature_link
  if (!link) return ''
  if (link.config_descriptions || link.timelines || link.effect_paths) {
    return '客户端动作配置'
  }
  return '未找到客户端动作配置'
}

function getFeatureLinkEffects(row: FanxiuGongfaProgressionRow) {
  return splitFanxiuList(row.feature_link?.effect_paths, 5)
}

function getProgressionSummary(item: { progression_counts?: Record<string, number> } | null | undefined) {
  const counts = item?.progression_counts ?? {}
  return PROGRESSION_ORDER
    .filter(key => counts[key])
    .slice(0, 3)
    .map(key => `${PROGRESSION_LABELS[key] ?? key} ${counts[key]}`)
    .join(' · ')
}

function escapeHtml(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function highlightFormulaVariables(value: string) {
  return value.replace(/[xXyYzZ]/g, (match, offset, source) => {
    const before = source[offset - 1] ?? ''
    const after = source[offset + 1] ?? ''
    const isFormulaEdge = /[0-9=+\-*/×()（）]/.test(before) || /[0-9=+\-*/×()（）]/.test(after)
    return isFormulaEdge ? `<span class="wiki-variable">${match}</span>` : match
  })
}

function renderFanxiuText(value: string, options: { mapColors?: boolean; tone?: 'dark' | 'light' } = {}) {
  const colorMap = options.tone === 'light' ? lightRichColorMap : richColorMap
  return escapeHtml(value || '')
    .replace(/&lt;color=(#[0-9a-fA-F]{3,8})&gt;/g, (_match, color) => {
      const mapped = options.mapColors === false ? color : colorMap[String(color).toLowerCase()] ?? color
      return `<span style="color:${mapped}">`
    })
    .replace(/&lt;\/color&gt;/g, '</span>')
    .replace(/&lt;size=([0-9]{1,3})&gt;/g, '<span>')
    .replace(/&lt;\/size&gt;/g, '</span>')
    .replace(/【([^】]{1,30})】/g, '<span class="wiki-term">【$1】</span>')
    .replace(/(^|>)([^<]+)/g, (_match, prefix, text) => `${prefix}${highlightFormulaVariables(text)}`)
    .replace(/([+＋]\s*\d+(?:\.\d+)?%?)/g, '<span class="wiki-number">$1</span>')
    .replace(/\n/g, '<br>')
}

function compactText(value: string | undefined, limit = 120) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text.length <= limit ? text : `${text.slice(0, limit).trimEnd()}...`
}

type FacetOption = (
  FanxiuGongfaQualityPartOption |
  FanxiuGongfaSkillTypeOption |
  FanxiuItemQualityOption
) & {
  rich_label?: string;
  color?: string;
  quality_color?: string;
}

function normalizeCssColor(value: unknown) {
  const text = String(value ?? '').trim()
  if (!text) return ''
  if (text.startsWith('#')) return text
  return /^[0-9a-fA-F]{3,8}$/.test(text) ? `#${text}` : text
}

function getFacetOptionLabel(option: FacetOption) {
  return String(option.rich_label || option.label || option.value || '')
}

function getFacetOptionStyle(option: FacetOption) {
  const color = normalizeCssColor(option.color || option.quality_color)
  return color ? { '--facet-option-color': color } : undefined
}

function renderFacetOptionLabel(option: FacetOption) {
  return renderFanxiuText(getFacetOptionLabel(option), { mapColors: false })
}

function isFacetOptionDisabled(option: FacetCountOption, activeValue: string) {
  return option.count <= 0 && activeValue !== option.value
}

function formatRawValue(value: unknown) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function getWikiUserFieldsTargetKey(target = selectedWikiUserFieldsTarget.value) {
  return target ? `${target.objectType}:${target.objectId}` : ''
}

function isCurrentWikiUserFieldsTarget(target: WikiUserFieldsTarget) {
  const current = selectedWikiUserFieldsTarget.value
  return Boolean(current && current.objectType === target.objectType && current.objectId === target.objectId)
}

function normalizeWikiUserFields(target: WikiUserFieldsTarget | null | undefined): FanxiuWikiUserFields {
  return {
    object_type: target?.objectType ?? '',
    object_id: target?.objectId ?? '',
    note: String(target?.userFields?.note ?? ''),
    source: String(target?.userFields?.source ?? ''),
    updated_at: String(target?.userFields?.updated_at ?? ''),
  }
}

function syncWikiUserFieldDrafts(target: WikiUserFieldsTarget | null | undefined) {
  const fields = normalizeWikiUserFields(target)
  wikiUserNoteDraft.value = fields.note
  wikiUserSourceDraft.value = fields.source
  wikiUserFieldsSaveState.value = 'idle'
}

function hasWikiUserFieldDraftChanged(target: WikiUserFieldsTarget | null | undefined) {
  if (!target) return false
  const fields = normalizeWikiUserFields(target)
  return wikiUserNoteDraft.value !== fields.note || wikiUserSourceDraft.value !== fields.source
}

function applySavedWikiUserFields(target: WikiUserFieldsTarget, fields: FanxiuWikiUserFields) {
  if (target.objectType === 'gongfa') {
    const cached = gongfaDetailCache.get(target.objectId)
    if (cached) {
      gongfaDetailCache.set(target.objectId, { ...cached, user_fields: fields })
    }
    if (selectedCard.value && String(selectedCard.value.id) === target.objectId) {
      selectedCard.value = { ...selectedCard.value, user_fields: fields }
    }
    return
  }

  if (target.objectType === 'item') {
    const cached = itemDetailCache.get(target.objectId)
    if (cached) {
      itemDetailCache.set(target.objectId, { ...cached, user_fields: fields })
    }
    if (selectedItem.value && String(selectedItem.value.id) === target.objectId) {
      selectedItem.value = { ...selectedItem.value, user_fields: fields }
    }
    return
  }

  if (target.objectType === 'lingjie') {
    const cached = lingjieDetailCache.get(target.objectId)
    if (cached) {
      lingjieDetailCache.set(target.objectId, { ...cached, user_fields: fields })
    }
    if (selectedLingjieCard.value && String(selectedLingjieCard.value.gongfa_id) === target.objectId) {
      selectedLingjieCard.value = { ...selectedLingjieCard.value, user_fields: fields }
    }
  }
}

async function saveWikiUserFieldsForTarget(target: WikiUserFieldsTarget, note: string, source: string) {
  const requestSeq = ++wikiUserFieldsSaveSeq
  if (isCurrentWikiUserFieldsTarget(target)) {
    wikiUserFieldsSaveState.value = 'saving'
  }
  try {
    const fields = await updateFanxiuWikiUserFields(target.objectType, target.objectId, { note, source })
    if (requestSeq !== wikiUserFieldsSaveSeq) return
    applySavedWikiUserFields(target, fields)
    if (isCurrentWikiUserFieldsTarget(target)) {
      wikiUserFieldsSaveState.value = 'saved'
    }
  } catch (error: any) {
    if (requestSeq === wikiUserFieldsSaveSeq && isCurrentWikiUserFieldsTarget(target)) {
      wikiUserFieldsSaveState.value = 'error'
      ElMessage.error(error?.response?.data?.detail || error?.message || '保存图鉴备注失败')
    }
  }
}

function scheduleWikiUserFieldsSave() {
  const target = selectedWikiUserFieldsTarget.value
  if (!target || !hasWikiUserFieldDraftChanged(target)) return
  const note = wikiUserNoteDraft.value
  const source = wikiUserSourceDraft.value
  if (wikiUserFieldsSaveTimer) {
    clearTimeout(wikiUserFieldsSaveTimer)
  }
  wikiUserFieldsSaveTimer = setTimeout(() => {
    wikiUserFieldsSaveTimer = null
    void saveWikiUserFieldsForTarget(target, note, source)
  }, 700)
}

function flushWikiUserFieldsSave() {
  const target = selectedWikiUserFieldsTarget.value
  if (!target || !hasWikiUserFieldDraftChanged(target)) return
  if (wikiUserFieldsSaveTimer) {
    clearTimeout(wikiUserFieldsSaveTimer)
    wikiUserFieldsSaveTimer = null
  }
  void saveWikiUserFieldsForTarget(target, wikiUserNoteDraft.value, wikiUserSourceDraft.value)
}

async function loadGongfaCards(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuGongfaCards({
      query: query.value,
      quality_grade_name: gongfaQualityGradeFilter.value,
      quality_family_name: gongfaQualityFamilyFilter.value,
      skill_type_name: gongfaSkillTypeFilter.value,
      ...objectSortParams.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    stats.value = response.stats
    catalogPath.value = response.catalog_path
    gongfaQualityGradeOptions.value = response.quality_grade_options ?? []
    gongfaQualityFamilyOptions.value = response.quality_family_options ?? []
    gongfaSkillTypeOptions.value = response.skill_type_options ?? []
    gongfaFacetIndex.value = response.facet_index ?? null
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadGongfaCards(options)
      return
    }

    gongfaItems.value = response.items
    itemItems.value = []
    lingjieItems.value = []
    selectedItem.value = null
    selectedLingjieCard.value = null
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (!selectedCard.value || String(selectedCard.value.id) !== selectedId.value) {
        void selectGongfa(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    if (first) {
      selectedId.value = String(first.id)
      selectedCard.value = null
      void selectGongfa(first.id)
    } else {
      selectedId.value = ''
      selectedCard.value = null
      selectedItem.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取功法图鉴失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadItemCards(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuItemCards({
      query: query.value,
      quality_name: itemQualityFilter.value,
      type_key: itemTypeFilter.value,
      sub_type_key: itemSubTypeFilter.value,
      ...objectSortParams.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    itemStats.value = response.stats
    catalogPath.value = response.catalog_path
    itemQualityOptions.value = response.quality_options ?? []
    itemTypeOptions.value = response.type_options ?? []
    itemSubTypeOptions.value = response.sub_type_options ?? []
    itemFacetIndex.value = response.facet_index ?? null
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadItemCards(options)
      return
    }

    itemItems.value = response.items
    gongfaItems.value = []
    lingjieItems.value = []
    selectedCard.value = null
    selectedLingjieCard.value = null
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (!selectedItem.value || String(selectedItem.value.id) !== selectedId.value) {
        void selectItem(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    if (first) {
      selectedId.value = String(first.id)
      selectedItem.value = null
      void selectItem(first.id)
    } else {
      selectedId.value = ''
      selectedItem.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取道具图鉴失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadLingjieFeatureCards(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuLingjieFeatureCards({
      query: query.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    lingjieStats.value = response.stats
    catalogPath.value = response.catalog_path ?? ''
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadLingjieFeatureCards(options)
      return
    }

    lingjieItems.value = response.items
    gongfaItems.value = []
    itemItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (!selectedLingjieCard.value || String(selectedLingjieCard.value.gongfa_id) !== selectedId.value) {
        void selectLingjieFeature(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    if (first) {
      selectedId.value = String(first.gongfa_id)
      selectedLingjieCard.value = null
      void selectLingjieFeature(first.gongfa_id)
    } else {
      selectedId.value = ''
      selectedLingjieCard.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取灵界词条失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

function loadCurrentCards(options: { keepSelection?: boolean } = {}) {
  if (activeTab.value === 'item') {
    return loadItemCards(options)
  }
  if (activeTab.value === 'lingjie') {
    return loadLingjieFeatureCards(options)
  }
  return loadGongfaCards(options)
}

async function selectGongfa(gongfaId: string | number) {
  const nextId = String(gongfaId)
  selectedId.value = nextId
  const requestSeq = ++detailRequestSeq
  const cached = gongfaDetailCache.get(nextId)
    if (cached) {
      selectedCard.value = cached
      selectedItem.value = null
      selectedLingjieCard.value = null
      const tabs = progressionTabs.value
      if (!tabs.some(tab => tab.key === selectedProgressionType.value)) {
        selectedProgressionType.value = tabs[0]?.key ?? ''
    }
    loadingDetail.value = false
    return
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuGongfaCard(nextId)
    if (requestSeq !== detailRequestSeq) return
    gongfaDetailCache.set(nextId, response.card)
    selectedCard.value = response.card
    selectedItem.value = null
    selectedLingjieCard.value = null
    const tabs = progressionTabs.value
    if (!tabs.some(tab => tab.key === selectedProgressionType.value)) {
      selectedProgressionType.value = tabs[0]?.key ?? ''
    }
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取功法详情失败')
    }
  } finally {
    if (requestSeq === detailRequestSeq) {
      loadingDetail.value = false
    }
  }
}

async function selectItem(itemId: string | number) {
  const nextId = String(itemId)
  selectedId.value = nextId
  const requestSeq = ++detailRequestSeq
  const cached = itemDetailCache.get(nextId)
    if (cached) {
      selectedItem.value = cached
      selectedCard.value = null
      selectedLingjieCard.value = null
      loadingDetail.value = false
      return
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuItemCard(nextId)
    if (requestSeq !== detailRequestSeq) return
    itemDetailCache.set(nextId, response.card)
    selectedItem.value = response.card
    selectedCard.value = null
    selectedLingjieCard.value = null
    const tabs = progressionTabs.value
    if (!tabs.some(tab => tab.key === selectedProgressionType.value)) {
      selectedProgressionType.value = tabs[0]?.key ?? ''
    }
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取道具详情失败')
    }
  } finally {
    if (requestSeq === detailRequestSeq) {
      loadingDetail.value = false
    }
  }
}

async function selectLingjieFeature(gongfaId: string | number) {
  const nextId = String(gongfaId)
  selectedId.value = nextId
  const requestSeq = ++detailRequestSeq
  const cached = lingjieDetailCache.get(nextId)
  if (cached) {
    selectedLingjieCard.value = cached
    selectedCard.value = null
    selectedItem.value = null
    loadingDetail.value = false
    return
  }
  loadingDetail.value = true
  try {
    const card = await getFanxiuLingjieFeatureCard(nextId)
    if (requestSeq !== detailRequestSeq) return
    lingjieDetailCache.set(nextId, card)
    selectedLingjieCard.value = card
    selectedCard.value = null
    selectedItem.value = null
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取灵界词条详情失败')
    }
  } finally {
    if (requestSeq === detailRequestSeq) {
      loadingDetail.value = false
    }
  }
}

function openWikiObject(tab: WikiTab, objectId: string | number, options: { resetListContext?: boolean } = {}) {
  const nextId = String(objectId ?? '').trim()
  if (!nextId) return
  internalTabNavigation = true
  try {
    activeTab.value = tab
    selectedId.value = nextId
    page.value = 1
    if (options.resetListContext) {
      query.value = ''
      sortMode.value = 'default'
      if (tab === 'item') {
        itemQualityFilter.value = ''
        itemTypeFilter.value = ''
        itemSubTypeFilter.value = ''
      } else if (tab === 'gongfa') {
        gongfaQualityGradeFilter.value = ''
        gongfaQualityFamilyFilter.value = ''
        gongfaSkillTypeFilter.value = ''
      }
    }
    void loadCurrentCards({ keepSelection: true })
    syncRouteState()
  } finally {
    window.setTimeout(() => {
      internalTabNavigation = false
    }, 0)
  }
}

function openLinkedItem(item: WikiLinkedItem | null | undefined) {
  const id = getLinkedItemId(item)
  if (!id) return
  openWikiObject('item', id, { resetListContext: true })
}

function selectObject(objectId: string | number) {
  if (activeTab.value === 'item') {
    return selectItem(objectId)
  }
  if (activeTab.value === 'lingjie') {
    return selectLingjieFeature(objectId)
  }
  return selectGongfa(objectId)
}

function reloadFromFirstPage() {
  page.value = 1
  loadCurrentCards()
}

function handlePageChange(nextPage: number) {
  page.value = normalizePage(nextPage, 1)
  loadCurrentCards()
}

function handlePageStep(delta: number) {
  const nextPage = Math.min(pageCount.value, Math.max(1, page.value + delta))
  if (nextPage === page.value) return
  handlePageChange(nextPage)
}

function handlePageSizeChange(nextPageSize: number) {
  pageSize.value = normalizePageSize(nextPageSize, 50)
  page.value = 1
  loadCurrentCards()
}

function cycleSortMode() {
  const index = SORT_MODE_ORDER.indexOf(sortMode.value)
  sortMode.value = SORT_MODE_ORDER[(index + 1) % SORT_MODE_ORDER.length] ?? 'default'
  reloadFromFirstPage()
}

function handleTabChange() {
  if (internalTabNavigation) return
  page.value = 1
  total.value = 0
  selectedId.value = ''
  selectedCard.value = null
  selectedItem.value = null
  selectedLingjieCard.value = null
  loadCurrentCards()
}

function applyGongfaQualityGradeFilter(value: string) {
  gongfaQualityGradeFilter.value = value
  reloadFromFirstPage()
}

function applyGongfaQualityFamilyFilter(value: string) {
  gongfaQualityFamilyFilter.value = value
  reloadFromFirstPage()
}

function applyGongfaSkillTypeFilter(value: string) {
  gongfaSkillTypeFilter.value = value
  reloadFromFirstPage()
}

function applyItemQualityFilter(value: string) {
  itemQualityFilter.value = value
  reloadFromFirstPage()
}

function applyItemTypeFilter(value: string) {
  itemTypeFilter.value = value
  itemSubTypeFilter.value = ''
  reloadFromFirstPage()
}

function applyItemSubTypeFilter(value: string) {
  itemSubTypeFilter.value = value
  reloadFromFirstPage()
}

watch([
  activeTab,
  query,
  gongfaQualityGradeFilter,
  gongfaQualityFamilyFilter,
  gongfaSkillTypeFilter,
  itemQualityFilter,
  itemTypeFilter,
  itemSubTypeFilter,
  sortMode,
  page,
  pageSize,
  selectedId,
], persistPageConfig)
watch([activeTab, selectedId], syncRouteState)
watch(
  () => [route.query.tab, route.query.id],
  () => {
    if (applyRouteState()) {
      void loadCurrentCards({ keepSelection: Boolean(selectedId.value) })
    }
  },
)
watch(() => getWikiUserFieldsTargetKey(), () => syncWikiUserFieldDrafts(selectedWikiUserFieldsTarget.value), { immediate: true })
watch([wikiUserNoteDraft, wikiUserSourceDraft], scheduleWikiUserFieldsSave)

onMounted(() => {
  loadPageConfig()
  applyRouteState()
  loadCurrentCards({ keepSelection: Boolean(selectedId.value) })
})
</script>

<template>
  <div class="fanxiu-wiki-page">
    <header class="page-header">
      <div>
        <h2>凡修图鉴</h2>
        <div class="page-subline">
          <span v-for="item in objectStats" :key="item.label">{{ item.label }} {{ item.value }}</span>
          <span v-if="catalogPath">{{ catalogPath }}</span>
        </div>
      </div>
      <el-button :icon="Refresh" :loading="loadingList" @click="loadCurrentCards({ keepSelection: true })">刷新</el-button>
    </header>

    <el-tabs v-model="activeTab" class="wiki-tabs" @tab-change="handleTabChange">
      <el-tab-pane v-for="tab in WIKI_TABS" :key="tab.key" :label="tab.label" :name="tab.key" />
    </el-tabs>

    <div class="toolbar">
      <el-input
        v-model="query"
        class="query-input"
        clearable
        :placeholder="searchPlaceholder"
        :prefix-icon="Search"
        @keyup.enter="reloadFromFirstPage"
        @clear="reloadFromFirstPage"
      />
      <el-button type="primary" :icon="Search" :loading="loadingList" @click="reloadFromFirstPage">搜索</el-button>
      <el-button
        v-if="activeTab !== 'lingjie'"
        class="sort-mode-button"
        :class="{ active: sortMode !== 'default' }"
        :title="`点击切换到 ${nextSortModeLabel}`"
        @click="cycleSortMode"
      >{{ activeSortModeLabel }}</el-button>
      <span class="result-count">{{ total }} 个对象</span>
    </div>

    <div v-if="activeTab !== 'lingjie'" class="facet-panel">
      <template v-if="activeTab === 'gongfa'">
        <div class="facet-row">
          <span class="facet-label">品阶</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !gongfaQualityGradeFilter }"
              type="button"
              @click="applyGongfaQualityGradeFilter('')"
            >全部</button>
            <button
              v-for="option in gongfaQualityGradeFacetOptions"
              :key="option.value"
              class="facet-option"
              :class="{ active: gongfaQualityGradeFilter === option.value }"
              :style="getFacetOptionStyle(option)"
              :disabled="isFacetOptionDisabled(option, gongfaQualityGradeFilter)"
              type="button"
              @click="applyGongfaQualityGradeFilter(option.value)"
            >
              <span class="facet-option-label" v-html="renderFacetOptionLabel(option)"></span>
              <small>{{ option.count }}</small>
            </button>
          </span>
        </div>
        <div class="facet-row">
          <span class="facet-label">体系</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !gongfaQualityFamilyFilter }"
              type="button"
              @click="applyGongfaQualityFamilyFilter('')"
            >全部</button>
            <button
              v-for="option in gongfaQualityFamilyFacetOptions"
              :key="option.value"
              class="facet-option"
              :class="{ active: gongfaQualityFamilyFilter === option.value }"
              :style="getFacetOptionStyle(option)"
              :disabled="isFacetOptionDisabled(option, gongfaQualityFamilyFilter)"
              type="button"
              @click="applyGongfaQualityFamilyFilter(option.value)"
            >
              <span class="facet-option-label" v-html="renderFacetOptionLabel(option)"></span>
              <small>{{ option.count }}</small>
            </button>
          </span>
        </div>
        <div class="facet-row">
          <span class="facet-label">技能类型</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !gongfaSkillTypeFilter }"
              type="button"
              @click="applyGongfaSkillTypeFilter('')"
            >全部</button>
            <button
              v-for="option in gongfaSkillTypeFacetOptions"
              :key="option.value"
              class="facet-option"
              :class="{ active: gongfaSkillTypeFilter === option.value }"
              :style="getFacetOptionStyle(option)"
              :disabled="isFacetOptionDisabled(option, gongfaSkillTypeFilter)"
              type="button"
              @click="applyGongfaSkillTypeFilter(option.value)"
            >
              <span class="facet-option-label" v-html="renderFacetOptionLabel(option)"></span>
              <small>{{ option.count }}</small>
            </button>
          </span>
        </div>
      </template>
      <template v-else-if="activeTab === 'item'">
        <div class="facet-row">
          <span class="facet-label">品质</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !itemQualityFilter }"
              type="button"
              @click="applyItemQualityFilter('')"
            >全部</button>
            <button
              v-for="option in itemQualityFacetOptions"
              :key="option.value"
              class="facet-option"
              :class="{ active: itemQualityFilter === option.value }"
              :style="getFacetOptionStyle(option)"
              :disabled="isFacetOptionDisabled(option, itemQualityFilter)"
              type="button"
              @click="applyItemQualityFilter(option.value)"
            >
              <span class="facet-option-label" v-html="renderFacetOptionLabel(option)"></span>
              <small>{{ option.count }}</small>
            </button>
          </span>
        </div>
        <div class="facet-row">
          <span class="facet-label">类型</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !itemTypeFilter }"
              type="button"
              @click="applyItemTypeFilter('')"
            >全部</button>
            <button
              v-for="option in itemTypeFacetOptions"
              :key="option.value"
              class="facet-option"
              :class="{ active: itemTypeFilter === option.value }"
              :disabled="isFacetOptionDisabled(option, itemTypeFilter)"
              type="button"
              @click="applyItemTypeFilter(option.value)"
            >
              <span class="facet-option-label">{{ option.label }}</span>
              <small>{{ option.count }}</small>
            </button>
          </span>
        </div>
        <div class="facet-row">
          <span class="facet-label">子类</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !itemSubTypeFilter }"
              type="button"
              @click="applyItemSubTypeFilter('')"
            >全部</button>
            <button
              v-for="option in itemSubTypeFacetOptions"
              :key="option.value"
              class="facet-option"
              :class="{ active: itemSubTypeFilter === option.value }"
              :disabled="isFacetOptionDisabled(option, itemSubTypeFilter)"
              type="button"
              @click="applyItemSubTypeFilter(option.value)"
            >
              <span class="facet-option-label">{{ option.label }}</span>
              <small>{{ option.count }}</small>
            </button>
          </span>
        </div>
      </template>
    </div>

    <div class="object-workspace">
      <aside class="object-list" v-loading="loadingList">
        <div class="object-list-scroll">
          <template v-if="activeTab === 'gongfa'">
            <button
              v-for="item in gongfaItems"
              :key="item.id"
              class="object-row"
              :class="{ selected: String(item.id) === selectedId }"
              type="button"
              @click="selectObject(item.id)"
            >
              <span class="object-row-icon">
                <span class="icon-fallback">{{ getObjectIconText(item) }}</span>
                <img
                  v-if="getObjectIconUrl(item)"
                  :src="getObjectIconUrl(item)"
                  :alt="item.name"
                  loading="lazy"
                  @error="hideBrokenIcon"
                >
              </span>
              <span class="object-row-main">
                <span class="object-row-title">{{ item.name }}</span>
                <span class="object-row-meta">
                  <span
                    class="quality-label"
                    :title="getQualityTitle(item)"
                    v-html="renderFanxiuText(getQualityLabel(item))"
                  ></span>
                  <template v-if="getGongfaMetaTail(item)"> · {{ getGongfaMetaTail(item) }}</template>
                  <template v-if="getFirstTimelineShortLabel(item)"> · {{ getFirstTimelineShortLabel(item) }}</template>
                </span>
                <span class="object-row-preview">{{ compactText(item.effect_preview || item.description_preview, 96) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !gongfaItems.length" class="empty-state">没有匹配功法</div>
          </template>
          <template v-else-if="activeTab === 'lingjie'">
            <button
              v-for="item in lingjieItems"
              :key="item.gongfa_id"
              class="object-row"
              :class="{ selected: String(item.gongfa_id) === selectedId }"
              type="button"
              @click="selectObject(item.gongfa_id)"
            >
              <span class="object-row-icon">
                <span class="icon-fallback">{{ getObjectIconText(item) }}</span>
                <img
                  v-if="getObjectIconUrl(item)"
                  :src="getObjectIconUrl(item)"
                  :alt="item.name"
                  loading="lazy"
                  @error="hideBrokenIcon"
                >
              </span>
              <span class="object-row-main">
                <span class="object-row-title">{{ item.name }}</span>
                <span class="object-row-meta">{{ getLingjieMeta(item) }}</span>
                <span class="object-row-preview">
                  {{ compactText(item.main_feature_names || item.side_feature_names || item.description_preview, 96) }}
                </span>
              </span>
            </button>
            <div v-if="!loadingList && !lingjieItems.length" class="empty-state">没有匹配灵界词条</div>
          </template>
          <template v-else>
            <button
              v-for="item in itemItems"
              :key="item.id"
              class="object-row"
              :class="{ selected: String(item.id) === selectedId }"
              type="button"
              @click="selectObject(item.id)"
            >
              <span class="object-row-icon">
                <span class="icon-fallback">{{ getObjectIconText(item) }}</span>
                <img
                  v-if="getObjectIconUrl(item)"
                  :src="getObjectIconUrl(item)"
                  :alt="item.name"
                  loading="lazy"
                  @error="hideBrokenIcon"
                >
              </span>
              <span class="object-row-main">
                <span class="object-row-title">{{ item.name }}</span>
                <span class="object-row-meta">
                  {{ getItemMeta(item) }}
                  <template v-if="getFirstTimelineShortLabel(item)"> · {{ getFirstTimelineShortLabel(item) }}</template>
                </span>
                <span class="object-row-preview">{{ compactText(item.effect_preview || item.description_preview, 96) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !itemItems.length" class="empty-state">没有匹配道具</div>
          </template>
        </div>

        <div v-if="total > 0" class="object-pagination">
          <el-select
            v-model="pageSize"
            class="page-size-select"
            size="small"
            @change="value => handlePageSizeChange(Number(value))"
          >
            <el-option v-for="size in PAGE_SIZE_OPTIONS" :key="size" :value="size" :label="`${size}条/页`" />
          </el-select>
          <div class="pager-nav">
            <button
              class="pager-arrow"
              type="button"
              :disabled="page <= 1"
              title="上一页"
              aria-label="上一页"
              @click="handlePageStep(-1)"
            >
              <ArrowLeft />
            </button>
            <span class="pager-status">
              <b>{{ page }}</b>
              <span>/</span>
              {{ pageCount }}
            </span>
            <button
              class="pager-arrow"
              type="button"
              :disabled="page >= pageCount"
              title="下一页"
              aria-label="下一页"
              @click="handlePageStep(1)"
            >
              <ArrowRight />
            </button>
          </div>
        </div>
      </aside>

      <main class="object-detail" v-loading="loadingDetail">
        <template v-if="selectedCard">
          <section class="detail-head">
            <div class="object-icon">
              <span class="icon-fallback">{{ getObjectIconText(selectedCard) }}</span>
              <img
                v-if="getObjectIconUrl(selectedCard)"
                :src="getObjectIconUrl(selectedCard)"
                :alt="selectedCard.name"
                @error="hideBrokenIcon"
              >
            </div>
            <div class="detail-title">
              <h3>{{ selectedCard.name }}</h3>
              <div class="detail-meta">
                <span>ID {{ selectedCard.id }}</span>
                <span
                  class="quality-label"
                  :title="getQualityTitle(selectedCard)"
                  v-html="renderFanxiuText(getQualityLabel(selectedCard))"
                ></span>
                <span v-if="getGongfaMetaTail(selectedCard)">{{ getGongfaMetaTail(selectedCard) }}</span>
                <span v-if="getFirstTimelineLabel(selectedCard)">{{ getFirstTimelineLabel(selectedCard) }}</span>
              </div>
            </div>
          </section>

          <div
            v-if="getDisplayLinkedItems(selectedCard.consume_items).length || getDisplayLinkedItems(selectedCard.show_condition_items).length"
            class="linked-item-strip detail-items"
          >
            <el-popover
              v-for="item in getDisplayLinkedItems(selectedCard.consume_items)"
              :key="`consume-${item.id}-${item.count}`"
              trigger="hover"
              placement="top-start"
              :width="320"
              popper-class="fanxiu-linked-item-popover"
            >
              <template #reference>
                <button class="linked-item clickable" type="button" @click="openLinkedItem(item)">
                  <span class="linked-item-icon">
                    <img
                      v-if="getLinkedItemIconUrl(item)"
                      :src="getLinkedItemIconUrl(item)"
                      :alt="item.name"
                      loading="lazy"
                      @error="hideBrokenIcon"
                    >
                  </span>
                  <span>{{ getLinkedItemText(item) }}</span>
                </button>
              </template>
              <div class="linked-item-popover-card">
                <strong>{{ getLinkedItemText(item) }}</strong>
                <span v-if="getLinkedItemId(item)">ID {{ getLinkedItemId(item) }}</span>
                <div v-if="getLinkedItemDescription(item)" v-html="renderFanxiuText(getLinkedItemDescription(item), { tone: 'light' })" />
              </div>
            </el-popover>
            <el-popover
              v-for="item in getDisplayLinkedItems(selectedCard.show_condition_items)"
              :key="`show-${item.id}-${item.count}`"
              trigger="hover"
              placement="top-start"
              :width="320"
              popper-class="fanxiu-linked-item-popover"
            >
              <template #reference>
                <button class="linked-item muted clickable" type="button" @click="openLinkedItem(item)">
                  <span class="linked-item-icon">
                    <img
                      v-if="getLinkedItemIconUrl(item)"
                      :src="getLinkedItemIconUrl(item)"
                      :alt="item.name"
                      loading="lazy"
                      @error="hideBrokenIcon"
                    >
                  </span>
                  <span>{{ getLinkedItemText(item) }}</span>
                </button>
              </template>
              <div class="linked-item-popover-card">
                <strong>{{ getLinkedItemText(item) }}</strong>
                <span v-if="getLinkedItemId(item)">ID {{ getLinkedItemId(item) }}</span>
                <div v-if="getLinkedItemDescription(item)" v-html="renderFanxiuText(getLinkedItemDescription(item), { tone: 'light' })" />
              </div>
            </el-popover>
          </div>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section v-if="selectedWikiUserFieldsTarget" class="object-section user-fields-section">
            <div v-if="wikiUserFieldsSaveLabel" class="user-fields-status-row">
              <span
                class="user-fields-save-state"
                :class="`state-${wikiUserFieldsSaveState}`"
              >
                {{ wikiUserFieldsSaveLabel }}
              </span>
            </div>
            <div class="user-fields-grid">
              <label class="user-field">
                <span>来源</span>
                <el-input
                  v-model="wikiUserSourceDraft"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  resize="none"
                  placeholder="获取渠道"
                  @blur="flushWikiUserFieldsSave"
                />
              </label>
              <label class="user-field">
                <span>备注</span>
                <el-input
                  v-model="wikiUserNoteDraft"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 6 }"
                  resize="none"
                  placeholder="备注"
                  @blur="flushWikiUserFieldsSave"
                />
              </label>
            </div>
          </section>

          <div v-if="getTimelineValueHints(selectedCard).length" class="time-hint-strip">
            <strong>时间线索</strong>
            <span
              v-for="hint in getTimelineValueHints(selectedCard)"
              :key="`${hint.date}-${hint.time}-${hint.time_code}-${hint.source}-${hint.activity_id}-${hint.relation}-${hint.via_item_id}`"
              :title="getTimelineHintTitle(hint)"
            >
              {{ getTimelineHintLabel(hint) }}
            </span>
          </div>

          <section v-if="getCardDescriptionText(selectedCard)" class="object-section intro-section">
            <h4>简介</h4>
            <div class="plain-rich-text" v-html="renderFanxiuText(getCardDescriptionText(selectedCard), { tone: 'light' })" />
          </section>

          <section v-if="primarySkill" class="object-section">
            <h4>{{ getSkillTitle(primarySkill, 0) }}</h4>
            <div v-if="getSkillMeta(primarySkill)" class="skill-meta">{{ getSkillMeta(primarySkill) }}</div>
            <div v-if="getSkillSections(primarySkill).length" class="rich-section-list skill-section-list">
              <div
                v-for="(section, sectionIndex) in getSkillSections(primarySkill)"
                :key="`primary-skill-section-${sectionIndex}`"
                class="rich-section-block"
              >
                <div
                  v-if="getProgressionSectionTitle(section)"
                  class="rich-section-title"
                  v-html="renderFanxiuText(getProgressionSectionTitle(section))"
                />
                <div v-if="getProgressionSectionLines(section).length" class="rich-section-body">
                  <p
                    v-for="(line, lineIndex) in getProgressionSectionLines(section)"
                    :key="`primary-skill-section-${sectionIndex}-line-${lineIndex}`"
                    v-html="renderFanxiuText(line)"
                  />
                </div>
              </div>
            </div>
            <div v-else class="game-rich-text" v-html="renderFanxiuText(getSkillText(primarySkill))" />
          </section>

          <section v-if="secondarySkills.length" class="object-section">
            <h4>其他技能 / 效果</h4>
            <div class="skill-list">
              <article v-for="(skill, index) in secondarySkills" :key="String(skill.row_key ?? skill.id ?? index)" class="skill-item">
                <div class="skill-item-head">
                  <strong>{{ getSkillTitle(skill, index + 1) }}</strong>
                  <span>{{ getSkillMeta(skill) }}</span>
                </div>
                <div v-if="getSkillSections(skill).length" class="rich-section-list compact">
                  <div
                    v-for="(section, sectionIndex) in getSkillSections(skill)"
                    :key="`${skill.row_key ?? skill.id ?? index}-section-${sectionIndex}`"
                    class="rich-section-block"
                  >
                    <div
                      v-if="getProgressionSectionTitle(section)"
                      class="rich-section-title"
                      v-html="renderFanxiuText(getProgressionSectionTitle(section))"
                    />
                    <div v-if="getProgressionSectionLines(section).length" class="rich-section-body">
                      <p
                        v-for="(line, lineIndex) in getProgressionSectionLines(section)"
                        :key="`${skill.row_key ?? skill.id ?? index}-section-${sectionIndex}-line-${lineIndex}`"
                        v-html="renderFanxiuText(line)"
                      />
                    </div>
                  </div>
                </div>
                <div v-else class="plain-rich-text compact" v-html="renderFanxiuText(getSkillText(skill))" />
              </article>
            </div>
          </section>

          <section v-if="progressionTabs.length" class="object-section">
            <div class="section-row">
              <h4>进阶链</h4>
              <el-segmented
                v-model="selectedProgressionType"
                :options="progressionTabs.map(tab => ({ label: `${tab.label} ${tab.count}`, value: tab.key }))"
              />
            </div>
            <div class="progression-list">
              <article
                v-for="group in progressionViewGroups"
                :key="group.key"
                class="progression-item"
                :class="{ merged: group.merged }"
              >
                <div class="progression-title">
                  <div class="progression-title-main">
                    <strong :title="getProgressionTitleHint(group)">{{ getProgressionDisplayTitle(group) }}</strong>
                    <span v-if="group.inheritedBadges.length" class="inherit-strip">
                      <el-tooltip
                        v-for="badge in group.inheritedBadges"
                        :key="`${group.key}-${badge.key}`"
                        effect="dark"
                        placement="top-start"
                        popper-class="fanxiu-inherit-tooltip"
                        :show-after="120"
                      >
                        <template #content>
                          <div class="inherit-tooltip-content" v-html="renderInheritedBadgeContent(badge)" />
                        </template>
                        <span class="inherit-badge">
                          {{ badge.label }}
                        </span>
                      </el-tooltip>
                    </span>
                  </div>
                </div>
                <div v-if="group.displayAttrEntries.length" class="attr-strip">
                  <span v-for="attr in group.displayAttrEntries" :key="`${group.key}-${attr.key}`">
                    <b>{{ attr.label }}</b>
                    {{ attr.value }}
                  </span>
                </div>
                <div v-if="shouldShowProgressionItems(group.first, group.startIndex)" class="linked-item-strip progression-items">
                    <el-popover
                      v-for="item in getProgressionDisplayItems(group.first)"
                      :key="`${group.key}-${item.id}-${item.count}`"
                      trigger="hover"
                      placement="top-start"
                      :width="320"
                      popper-class="fanxiu-linked-item-popover"
                    >
                      <template #reference>
                        <button class="linked-item compact clickable" type="button" @click="openLinkedItem(item)">
                          <span class="linked-item-icon">
                            <img
                              v-if="getLinkedItemIconUrl(item)"
                              :src="getLinkedItemIconUrl(item)"
                              :alt="item.name"
                              loading="lazy"
                              @error="hideBrokenIcon"
                            >
                          </span>
                          <span>{{ getLinkedItemText(item) }}</span>
                        </button>
                      </template>
                      <div class="linked-item-popover-card">
                        <strong>{{ getLinkedItemText(item) }}</strong>
                        <span v-if="getLinkedItemId(item)">ID {{ getLinkedItemId(item) }}</span>
                        <div v-if="getLinkedItemDescription(item)" v-html="renderFanxiuText(getLinkedItemDescription(item), { tone: 'light' })" />
                      </div>
                    </el-popover>
                  </div>
                <div v-if="shouldRenderProgressionSections(group)" class="progression-section-list">
                  <div
                    v-for="(section, sectionIndex) in getProgressionSections(group.first)"
                    :key="`${group.key}-section-${sectionIndex}`"
                    class="progression-section-block"
                  >
                    <div
                      v-if="getProgressionSectionTitle(section)"
                      class="progression-section-title"
                      v-html="renderFanxiuText(getProgressionSectionTitle(section))"
                    />
                    <div v-if="getProgressionSectionLines(section).length" class="progression-section-body">
                      <p
                        v-for="(line, lineIndex) in getProgressionSectionLines(section)"
                        :key="`${group.key}-section-${sectionIndex}-line-${lineIndex}`"
                        v-html="renderFanxiuText(line)"
                      />
                    </div>
                  </div>
                </div>
                <div v-else-if="getProgressionRenderedText(group)" class="plain-rich-text compact" v-html="renderFanxiuText(getProgressionRenderedText(group))" />
                <div v-if="hasFeatureLink(group.first)" class="feature-link">
                  <div class="feature-link-title">
                    <span>{{ getFeatureLinkStatus(group.first) }}</span>
                    <strong>{{ getFeatureLinkTitle(group.first) }}</strong>
                  </div>
                  <div v-if="getFeatureLinkEffects(group.first).length" class="feature-effects">
                    <span v-for="effect in getFeatureLinkEffects(group.first)" :key="`${group.key}-${effect}`">{{ effect }}</span>
                  </div>
                  <div
                    v-else-if="group.first.feature_link?.source_describe"
                    class="feature-static-text"
                    v-html="renderFanxiuText(group.first.feature_link.source_describe)"
                  />
                </div>
                <div v-if="group.displayFazeSummary" class="faze-resource">
                  <div class="faze-title">
                    <span>{{ group.displayFazeSummary.title }}</span>
                  </div>
                  <div v-if="group.displayFazeSummary.tips.length" class="faze-tips">
                    <span v-for="tip in group.displayFazeSummary.tips" :key="`${group.key}-${tip.code}-${tip.text}`">
                      <b v-if="tip.code">{{ tip.code }}</b>
                      {{ tip.text }}
                    </span>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <details class="source-details">
            <summary>配置来源</summary>
            <dl>
              <dt>功法 ID</dt>
              <dd>{{ selectedCard.id }}</dd>
              <dt>配置行</dt>
              <dd>{{ selectedCard.source_row_key || '-' }}</dd>
              <dt>目录</dt>
              <dd>{{ catalogPath }}</dd>
            </dl>
          </details>
        </template>
        <template v-else-if="selectedLingjieCard">
          <section class="detail-head">
            <div class="object-icon">
              <span class="icon-fallback">{{ getObjectIconText(selectedLingjieCard) }}</span>
              <img
                v-if="getObjectIconUrl(selectedLingjieCard)"
                :src="getObjectIconUrl(selectedLingjieCard)"
                :alt="selectedLingjieCard.name"
                @error="hideBrokenIcon"
              >
            </div>
            <div class="detail-title">
              <h3>{{ selectedLingjieCard.name }}</h3>
              <div class="detail-meta">
                <span>ID {{ selectedLingjieCard.gongfa_id }}</span>
                <span>{{ getLingjieMeta(selectedLingjieCard) }}</span>
              </div>
            </div>
          </section>

          <div v-if="selectedLingjieCard.items?.length" class="linked-item-strip detail-items">
            <el-popover
              v-for="item in selectedLingjieCard.items"
              :key="String(item.id ?? item.row_key ?? item.name)"
              trigger="hover"
              placement="top-start"
              :width="320"
              popper-class="fanxiu-linked-item-popover"
            >
              <template #reference>
                <button class="linked-item clickable" type="button" @click="openLinkedItem(item)">
                  <span class="linked-item-icon">
                    <img
                      v-if="getLingjieItemIconUrl(item)"
                      :src="getLingjieItemIconUrl(item)"
                      :alt="item.name"
                      loading="lazy"
                      @error="hideBrokenIcon"
                    >
                  </span>
                  <span>{{ getLingjieItemText(item) }}</span>
                </button>
              </template>
              <div class="linked-item-popover-card">
                <strong>{{ getLingjieItemText(item) }}</strong>
                <span v-if="getLinkedItemId(item)">ID {{ getLinkedItemId(item) }}</span>
                <div v-if="getLinkedItemDescription(item)" v-html="renderFanxiuText(getLinkedItemDescription(item), { tone: 'light' })" />
              </div>
            </el-popover>
          </div>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section v-if="selectedWikiUserFieldsTarget" class="object-section user-fields-section">
            <div v-if="wikiUserFieldsSaveLabel" class="user-fields-status-row">
              <span
                class="user-fields-save-state"
                :class="`state-${wikiUserFieldsSaveState}`"
              >
                {{ wikiUserFieldsSaveLabel }}
              </span>
            </div>
            <div class="user-fields-grid">
              <label class="user-field">
                <span>来源</span>
                <el-input
                  v-model="wikiUserSourceDraft"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  resize="none"
                  placeholder="获取渠道"
                  @blur="flushWikiUserFieldsSave"
                />
              </label>
              <label class="user-field">
                <span>备注</span>
                <el-input
                  v-model="wikiUserNoteDraft"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 6 }"
                  resize="none"
                  placeholder="备注"
                  @blur="flushWikiUserFieldsSave"
                />
              </label>
            </div>
          </section>

          <section v-if="selectedLingjieCard.description" class="object-section intro-section">
            <h4>简介</h4>
            <div class="plain-rich-text" v-html="renderFanxiuText(selectedLingjieCard.description, { tone: 'light' })" />
          </section>

          <section v-if="selectedLingjieCard.main_features?.length" class="object-section">
            <h4>词条结构</h4>
            <div class="lingjie-feature-list">
              <article
                v-for="(feature, index) in selectedLingjieCard.main_features"
                :key="String(feature.row_key ?? feature.id ?? index)"
                class="lingjie-feature-item"
              >
                <div class="skill-item-head">
                  <strong>{{ getLingjieMainFeatureTitle(feature, index) }}</strong>
                  <span>{{ feature.groups?.length ? `Groups ${feature.groups.join(' / ')}` : '' }}</span>
                </div>
                <div v-if="feature.describe || feature.condition" class="plain-rich-text compact">
                  <span v-if="feature.describe">{{ feature.describe }}</span>
                  <span v-if="feature.condition"> {{ feature.condition }}</span>
                </div>
                <div v-if="feature.expanded_groups?.length" class="lingjie-group-list">
                  <div
                    v-for="link in feature.expanded_groups"
                    :key="`${feature.id}-${link.feature_group}`"
                    class="lingjie-group"
                  >
                    <div class="lingjie-group-head">
                      <strong>{{ link.sample_names || `FeatureGroup ${link.feature_group}` }}</strong>
                      <span>{{ getLingjieGroupMeta(link) }}</span>
                    </div>
                    <div v-if="link.sample_features" class="feature-effects">
                      <span v-for="item in splitFanxiuList(link.sample_features, 8)" :key="`${link.feature_group}-${item}`">{{ item }}</span>
                    </div>
                    <div v-if="link.sample_describes" class="feature-static-text" v-html="renderFanxiuText(link.sample_describes)" />
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section v-if="lingjieMainPinViewGroups.length" class="object-section">
            <div class="section-row">
              <h4>主词条品阶</h4>
              <span class="section-count">{{ getLingjieProgressionSectionCount(lingjieMainPinViewGroups, selectedLingjieCard.main_pin_rows) }}</span>
            </div>
            <div class="progression-list">
              <article
                v-for="group in lingjieMainPinViewGroups"
                :key="group.key"
                class="progression-item"
                :class="{ merged: group.merged }"
              >
                <div class="progression-title">
                  <div class="progression-title-main">
                    <strong :title="getLingjieProgressionTitleHint(group)">{{ getLingjieProgressionDisplayTitle(group) }}</strong>
                    <span v-if="group.inheritedBadges.length" class="inherit-strip">
                      <el-tooltip
                        v-for="badge in group.inheritedBadges"
                        :key="`${group.key}-${badge.key}`"
                        effect="dark"
                        placement="top-start"
                        popper-class="fanxiu-inherit-tooltip"
                        :show-after="120"
                      >
                        <template #content>
                          <div class="inherit-tooltip-content" v-html="renderInheritedBadgeContent(badge)" />
                        </template>
                        <span class="inherit-badge">{{ badge.label }}</span>
                      </el-tooltip>
                    </span>
                  </div>
                  <span>{{ getLingjieProgressionMeta(group) }}</span>
                </div>
                <div v-if="shouldRenderProgressionSections(group)" class="progression-section-list">
                  <div
                    v-for="(section, sectionIndex) in getProgressionSections(group.first)"
                    :key="`${group.key}-section-${sectionIndex}`"
                    class="progression-section-block"
                  >
                    <div
                      v-if="getProgressionSectionTitle(section)"
                      class="progression-section-title"
                      v-html="renderFanxiuText(getProgressionSectionTitle(section))"
                    />
                    <div v-if="getProgressionSectionLines(section).length" class="progression-section-body">
                      <p
                        v-for="(line, lineIndex) in getProgressionSectionLines(section)"
                        :key="`${group.key}-section-${sectionIndex}-line-${lineIndex}`"
                        v-html="renderFanxiuText(line)"
                      />
                    </div>
                  </div>
                </div>
                <div v-else-if="getProgressionRenderedText(group)" class="plain-rich-text compact" v-html="renderFanxiuText(getProgressionRenderedText(group))" />
              </article>
            </div>
          </section>

          <section v-if="lingjieJieViewGroups.length" class="object-section">
            <div class="section-row">
              <h4>进阶链</h4>
              <span class="section-count">{{ getLingjieProgressionSectionCount(lingjieJieViewGroups, selectedLingjieCard.jie_rows) }}</span>
            </div>
            <div class="progression-list">
              <article
                v-for="group in lingjieJieViewGroups"
                :key="group.key"
                class="progression-item"
                :class="{ merged: group.merged }"
              >
                <div class="progression-title">
                  <div class="progression-title-main">
                    <strong :title="getLingjieProgressionTitleHint(group)">{{ getLingjieProgressionDisplayTitle(group) }}</strong>
                    <span v-if="group.inheritedBadges.length" class="inherit-strip">
                      <el-tooltip
                        v-for="badge in group.inheritedBadges"
                        :key="`${group.key}-${badge.key}`"
                        effect="dark"
                        placement="top-start"
                        popper-class="fanxiu-inherit-tooltip"
                        :show-after="120"
                      >
                        <template #content>
                          <div class="inherit-tooltip-content" v-html="renderInheritedBadgeContent(badge)" />
                        </template>
                        <span class="inherit-badge">{{ badge.label }}</span>
                      </el-tooltip>
                    </span>
                  </div>
                  <span>{{ getLingjieProgressionMeta(group) }}</span>
                </div>
                <div v-if="getLingjieProgressionParamText(group)" class="attr-strip">
                  <span><b>param</b>{{ getLingjieProgressionParamText(group) }}</span>
                </div>
                <div v-if="group.displayText" class="plain-rich-text compact" v-html="renderFanxiuText(group.displayText)" />
              </article>
            </div>
          </section>

          <section v-if="lingjieStarViewGroups.length" class="object-section">
            <div class="section-row">
              <h4>升星</h4>
              <span class="section-count">{{ getLingjieProgressionSectionCount(lingjieStarViewGroups, selectedLingjieCard.star_rows) }}</span>
            </div>
            <div class="progression-list">
              <article
                v-for="group in lingjieStarViewGroups"
                :key="group.key"
                class="progression-item"
                :class="{ merged: group.merged }"
              >
                <div class="progression-title">
                  <div class="progression-title-main">
                    <strong :title="getLingjieProgressionTitleHint(group)">{{ getLingjieProgressionDisplayTitle(group) }}</strong>
                    <span v-if="group.inheritedBadges.length" class="inherit-strip">
                      <el-tooltip
                        v-for="badge in group.inheritedBadges"
                        :key="`${group.key}-${badge.key}`"
                        effect="dark"
                        placement="top-start"
                        popper-class="fanxiu-inherit-tooltip"
                        :show-after="120"
                      >
                        <template #content>
                          <div class="inherit-tooltip-content" v-html="renderInheritedBadgeContent(badge)" />
                        </template>
                        <span class="inherit-badge">{{ badge.label }}</span>
                      </el-tooltip>
                    </span>
                  </div>
                  <span>{{ getLingjieProgressionMeta(group) }}</span>
                </div>
                <div v-if="getLingjieProgressionParamText(group)" class="attr-strip">
                  <span><b>param</b>{{ getLingjieProgressionParamText(group) }}</span>
                </div>
                <div v-if="group.displayText" class="plain-rich-text compact" v-html="renderFanxiuText(group.displayText)" />
              </article>
            </div>
          </section>

          <section v-if="selectedLingjieCard.runtime_summary" class="object-section">
            <div class="section-row">
              <h4>战斗画像</h4>
              <span class="section-count">{{ selectedLingjieCard.runtime_summary.timeline_count || 0 }} Timeline</span>
            </div>
            <div class="runtime-stat-grid">
              <span v-for="item in getRuntimeSummaryStats(selectedLingjieCard.runtime_summary)" :key="item">{{ item }}</span>
            </div>
            <div v-if="selectedLingjieCard.runtime_summary.damage_families?.length" class="runtime-card-list">
              <article
                v-for="(family, index) in selectedLingjieCard.runtime_summary.damage_families"
                :key="String(family.family_id || index)"
                class="runtime-card"
              >
                <div class="skill-item-head">
                  <strong>{{ getRuntimeFamilyTitle(family, index) }}</strong>
                  <span>{{ getRuntimeFamilyMeta(family) }}</span>
                </div>
                <div class="feature-effects">
                  <span v-for="badge in getRuntimeFamilyBadges(family)" :key="badge">{{ badge }}</span>
                </div>
                <div v-if="family.hit_times_ms" class="runtime-line">
                  <b>命中时点</b>
                  <span>{{ formatRuntimeMsList(family.hit_times_ms) }}</span>
                </div>
                <div v-if="family.hurt_percents" class="runtime-line">
                  <b>伤害段</b>
                  <span>{{ formatRuntimePercentList(family.hurt_percents, family.hit_count) }}</span>
                </div>
                <div v-if="family.sample_timelines" class="feature-effects">
                  <span v-for="item in getRuntimeTimelineBadges(family.sample_timelines)" :key="item">{{ item }}</span>
                </div>
              </article>
            </div>
            <div v-if="selectedLingjieCard.runtime_summary.timeline_samples?.length" class="runtime-timeline-list">
              <article
                v-for="(timeline, index) in selectedLingjieCard.runtime_summary.timeline_samples"
                :key="String(timeline.timeline_id || index)"
                class="runtime-timeline"
              >
                <strong>{{ getRuntimeTimelineTitle(timeline, index) }}</strong>
                <span>{{ getRuntimeTimelineMeta(timeline) }}</span>
              </article>
            </div>
          </section>

          <details class="source-details">
            <summary>来源</summary>
            <dl>
              <dt>功法 ID</dt>
              <dd>{{ selectedLingjieCard.gongfa_id }}</dd>
              <dt>目录</dt>
              <dd>{{ catalogPath }}</dd>
            </dl>
          </details>
        </template>
        <template v-else-if="selectedItem">
          <section class="detail-head">
            <div class="object-icon">
              <span class="icon-fallback">{{ getObjectIconText(selectedItem) }}</span>
              <img
                v-if="getObjectIconUrl(selectedItem)"
                :src="getObjectIconUrl(selectedItem)"
                :alt="selectedItem.name"
                @error="hideBrokenIcon"
              >
            </div>
            <div class="detail-title">
              <h3>{{ selectedItem.name }}</h3>
              <div class="detail-meta">
                <span>ID {{ selectedItem.id }}</span>
                <span
                  class="quality-label"
                  :title="getQualityTitle(selectedItem)"
                  v-html="renderFanxiuText(getQualityLabel(selectedItem))"
                ></span>
                <span v-if="getItemCategoryLabel(selectedItem)">{{ getItemCategoryLabel(selectedItem) }}</span>
                <span v-if="selectedItem.overlay">堆叠 {{ selectedItem.overlay }}</span>
                <span v-if="getFirstTimelineLabel(selectedItem)">{{ getFirstTimelineLabel(selectedItem) }}</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section v-if="selectedWikiUserFieldsTarget" class="object-section user-fields-section">
            <div v-if="wikiUserFieldsSaveLabel" class="user-fields-status-row">
              <span
                class="user-fields-save-state"
                :class="`state-${wikiUserFieldsSaveState}`"
              >
                {{ wikiUserFieldsSaveLabel }}
              </span>
            </div>
            <div class="user-fields-grid">
              <label class="user-field">
                <span>来源</span>
                <el-input
                  v-model="wikiUserSourceDraft"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  resize="none"
                  placeholder="获取渠道"
                  @blur="flushWikiUserFieldsSave"
                />
              </label>
              <label class="user-field">
                <span>备注</span>
                <el-input
                  v-model="wikiUserNoteDraft"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 6 }"
                  resize="none"
                  placeholder="备注"
                  @blur="flushWikiUserFieldsSave"
                />
              </label>
            </div>
          </section>

          <div v-if="getTimelineValueHints(selectedItem).length" class="time-hint-strip">
            <strong>时间线索</strong>
            <span
              v-for="hint in getTimelineValueHints(selectedItem)"
              :key="`${hint.date}-${hint.time}-${hint.time_code}-${hint.source}-${hint.activity_id}-${hint.relation}-${hint.reward_row_id}`"
              :title="getTimelineHintTitle(hint)"
            >
              {{ getTimelineHintLabel(hint) }}
            </span>
          </div>

          <section v-if="selectedItem.description" class="object-section intro-section">
            <h4>简介</h4>
            <div class="plain-rich-text" v-html="renderFanxiuText(selectedItem.description, { tone: 'light' })" />
          </section>

          <section v-if="selectedItem.effect_description || selectedItem.optional_gift_rewards?.length" class="object-section">
            <h4>效果</h4>
            <div v-if="selectedItem.effect_description" class="game-rich-text" v-html="renderFanxiuText(selectedItem.effect_description)" />
            <div v-if="selectedItem.optional_gift_rewards?.length" class="linked-item-strip detail-items optional-gift-items">
              <el-popover
                v-for="item in getDisplayLinkedItems(selectedItem.optional_gift_rewards)"
                :key="`optional-gift-${item.id}-${item.count}`"
                trigger="hover"
                placement="top-start"
                :width="320"
                popper-class="fanxiu-linked-item-popover"
              >
                <template #reference>
                  <button class="linked-item clickable" type="button" @click="openLinkedItem(item)">
                    <span class="linked-item-icon">
                      <img
                        v-if="getLinkedItemIconUrl(item)"
                        :src="getLinkedItemIconUrl(item)"
                        :alt="item.name"
                        loading="lazy"
                        @error="hideBrokenIcon"
                      >
                    </span>
                    <span>{{ getLinkedItemText(item) }}</span>
                  </button>
                </template>
                <div class="linked-item-popover-card">
                  <strong>{{ getLinkedItemText(item) }}</strong>
                  <span v-if="getLinkedItemId(item)">ID {{ getLinkedItemId(item) }}</span>
                  <div v-if="getLinkedItemDescription(item)" v-html="renderFanxiuText(getLinkedItemDescription(item), { tone: 'light' })" />
                </div>
              </el-popover>
            </div>
          </section>

          <section v-if="progressionTabs.length" class="object-section">
            <div class="section-row">
              <h4>进阶链</h4>
              <el-segmented
                v-model="selectedProgressionType"
                :options="progressionTabs.map(tab => ({ label: `${tab.label} ${tab.count}`, value: tab.key }))"
              />
            </div>
            <div class="progression-list">
              <article
                v-for="group in progressionViewGroups"
                :key="group.key"
                class="progression-item"
                :class="{ merged: group.merged }"
              >
                <div class="progression-title">
                  <div class="progression-title-main">
                    <strong :title="getProgressionTitleHint(group)">{{ getProgressionDisplayTitle(group) }}</strong>
                    <span v-if="group.inheritedBadges.length" class="inherit-strip">
                      <el-tooltip
                        v-for="badge in group.inheritedBadges"
                        :key="`${group.key}-${badge.key}`"
                        effect="dark"
                        placement="top-start"
                        popper-class="fanxiu-inherit-tooltip"
                        :show-after="120"
                      >
                        <template #content>
                          <div class="inherit-tooltip-content" v-html="renderInheritedBadgeContent(badge)" />
                        </template>
                        <span class="inherit-badge">
                          {{ badge.label }}
                        </span>
                      </el-tooltip>
                    </span>
                  </div>
                </div>
                <div v-if="group.displayAttrEntries.length" class="attr-strip">
                  <span v-for="attr in group.displayAttrEntries" :key="`${group.key}-${attr.key}`">
                    <b>{{ attr.label }}</b>
                    {{ attr.value }}
                  </span>
                </div>
                <div v-if="shouldShowProgressionItems(group.first, group.startIndex)" class="linked-item-strip progression-items">
                  <el-popover
                    v-for="item in getProgressionDisplayItems(group.first)"
                    :key="`${group.key}-${item.id}-${item.count}`"
                    trigger="hover"
                    placement="top-start"
                    :width="320"
                    popper-class="fanxiu-linked-item-popover"
                  >
                    <template #reference>
                      <button class="linked-item compact clickable" type="button" @click="openLinkedItem(item)">
                        <span class="linked-item-icon">
                          <img
                            v-if="getLinkedItemIconUrl(item)"
                            :src="getLinkedItemIconUrl(item)"
                            :alt="item.name"
                            loading="lazy"
                            @error="hideBrokenIcon"
                          >
                        </span>
                        <span>{{ getLinkedItemText(item) }}</span>
                      </button>
                    </template>
                    <div class="linked-item-popover-card">
                      <strong>{{ getLinkedItemText(item) }}</strong>
                      <span v-if="getLinkedItemId(item)">ID {{ getLinkedItemId(item) }}</span>
                      <div v-if="getLinkedItemDescription(item)" v-html="renderFanxiuText(getLinkedItemDescription(item), { tone: 'light' })" />
                    </div>
                  </el-popover>
                </div>
                <div v-if="group.displayText" class="plain-rich-text compact" v-html="renderFanxiuText(group.displayText)" />
                <div v-if="hasFeatureLink(group.first)" class="feature-link">
                  <div class="feature-link-title">
                    <span>{{ getFeatureLinkStatus(group.first) }}</span>
                    <strong>{{ getFeatureLinkTitle(group.first) }}</strong>
                  </div>
                  <div v-if="getFeatureLinkEffects(group.first).length" class="feature-effects">
                    <span v-for="effect in getFeatureLinkEffects(group.first)" :key="`${group.key}-${effect}`">{{ effect }}</span>
                  </div>
                  <div
                    v-else-if="group.first.feature_link?.source_describe"
                    class="feature-static-text"
                    v-html="renderFanxiuText(group.first.feature_link.source_describe)"
                  />
                </div>
                <div v-if="group.displayFazeSummary" class="faze-resource">
                  <div class="faze-title">
                    <span>{{ group.displayFazeSummary.title }}</span>
                  </div>
                  <div v-if="group.displayFazeSummary.tips.length" class="faze-tips">
                    <span v-for="tip in group.displayFazeSummary.tips" :key="`${group.key}-${tip.code}-${tip.text}`">
                      <b v-if="tip.code">{{ tip.code }}</b>
                      {{ tip.text }}
                    </span>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section
            v-if="getItemTypeDisplay(selectedItem) || getItemSubTypeDisplay(selectedItem) || formatRawValue(selectedItem.effect_value) || selectedItem.can_use || selectedItem.backpack"
            class="object-section item-field-section"
          >
            <h4>字段</h4>
            <dl class="object-field-list">
              <template v-if="getItemTypeDisplay(selectedItem)">
                <dt>类型</dt>
                <dd>{{ getItemTypeDisplay(selectedItem) }}</dd>
              </template>
              <template v-if="getItemSubTypeDisplay(selectedItem)">
                <dt>子类</dt>
                <dd>{{ getItemSubTypeDisplay(selectedItem) }}</dd>
              </template>
              <template v-if="formatRawValue(selectedItem.effect_value)">
                <dt>effectValue</dt>
                <dd>{{ formatRawValue(selectedItem.effect_value) }}</dd>
              </template>
              <template v-if="selectedItem.can_use">
                <dt>canUse</dt>
                <dd>{{ selectedItem.can_use }}</dd>
              </template>
              <template v-if="selectedItem.backpack">
                <dt>backpack</dt>
                <dd>{{ selectedItem.backpack }}</dd>
              </template>
            </dl>
          </section>

          <details class="source-details">
            <summary>来源</summary>
            <dl>
              <dt>道具 ID</dt>
              <dd>{{ selectedItem.id }}</dd>
              <dt>配置行</dt>
              <dd>{{ selectedItem.source_row_key || '-' }}</dd>
              <dt>目录</dt>
              <dd>{{ catalogPath }}</dd>
            </dl>
          </details>
        </template>
        <div v-else class="empty-state">未选择{{ activeObjectLabel }}</div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.fanxiu-wiki-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 18px 22px;
  box-sizing: border-box;
  color: #172033;
  background: #fff;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid #dfe4ec;
}

.page-header h2 {
  margin: 0 0 7px;
  color: #0f1f35;
  font-size: 24px;
  line-height: 1.2;
}

.page-subline {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  color: #667085;
  font-size: 13px;
}

.wiki-tabs {
  flex: 0 0 auto;
}

.wiki-tabs :deep(.el-tabs__header) {
  margin: 0;
}

.wiki-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
  background: #dfe4ec;
}

.toolbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 0 8px;
}

.query-input {
  width: min(420px, 42vw);
}

.sort-mode-button {
  flex: 0 0 auto;
  width: 72px;
}

.sort-mode-button.active {
  color: #1677ff;
  border-color: rgba(22, 119, 255, 0.45);
  background: #f0f7ff;
}

.facet-panel {
  flex: 0 0 auto;
  display: grid;
  gap: 4px;
  padding: 2px 0 12px;
  border-bottom: 1px solid #eef1f5;
}

.facet-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: start;
  min-height: 30px;
  column-gap: 12px;
}

.facet-label {
  padding-top: 5px;
  color: #172033;
  font-size: 14px;
  font-weight: 700;
}

.facet-options {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  min-width: 0;
}

.facet-option {
  min-height: 28px;
  padding: 0 6px;
  border: 0;
  border-radius: 3px;
  color: #344054;
  background: transparent;
  font: inherit;
  font-size: 14px;
  line-height: 28px;
  white-space: nowrap;
  cursor: pointer;
}

.facet-option-label {
  color: var(--facet-option-color, currentColor);
}

.facet-option:hover {
  background: rgba(150, 123, 63, 0.08);
}

.facet-option:disabled {
  opacity: 0.38;
  cursor: default;
}

.facet-option:disabled:hover {
  background: transparent;
}

.facet-option.active {
  font-weight: 700;
  background: rgba(255, 246, 220, 0.82);
  box-shadow: inset 0 0 0 1px rgba(174, 128, 38, 0.38);
}

.facet-option small {
  margin-left: 3px;
  color: #98a2b3;
  font-size: 12px;
  font-weight: 500;
}

.result-count {
  color: #667085;
  font-size: 13px;
}

.object-workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: clamp(320px, 25%, 420px) minmax(0, 1fr);
  border: 1px solid #dfe4ec;
  background: #f7f1dc;
}

.object-list {
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #fbfbfb;
  border-right: 1px solid #dfe4ec;
}

.object-list-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.object-row {
  width: 100%;
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr);
  gap: 10px;
  padding: 11px 13px;
  border: 0;
  border-bottom: 1px solid #e5e7eb;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.object-row:hover {
  background: #f3f4f6;
}

.object-row.selected {
  background: #fff8e7;
  box-shadow: inset 3px 0 0 #c28b2c;
}

.object-row-icon,
.object-icon {
  position: relative;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #fff9d4;
  font-weight: 800;
  text-shadow: 0 2px 8px rgba(0, 31, 62, 0.9);
  border: 1px solid rgba(244, 230, 170, 0.95);
  background:
    radial-gradient(circle at 36% 28%, rgba(255, 255, 255, 0.9), transparent 18%),
    radial-gradient(circle at 70% 72%, rgba(0, 234, 255, 0.72), transparent 24%),
    conic-gradient(from 220deg, #183b89, #1fb6d0, #f8f1b0, #2262a0, #183b89);
}

.object-row-icon img,
.object-icon img {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.icon-fallback {
  position: relative;
  z-index: 0;
}

.object-row-icon {
  width: 44px;
  height: 44px;
  font-size: 22px;
}

.object-row-main {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.object-row-title {
  min-width: 0;
  color: #101828;
  font-size: 15px;
  font-weight: 750;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.object-row-meta {
  color: #8a6b33;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.object-row-preview {
  color: #4b5563;
  font-size: 13px;
  line-height: 1.42;
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.object-pagination {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-top: 1px solid #e5e7eb;
  background: #fffdfa;
}

.pager-nav {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.pager-arrow {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(166, 128, 54, 0.26);
  border-radius: 4px;
  color: #7d6a46;
  background: #fff;
  cursor: pointer;
}

.pager-arrow:hover:not(:disabled) {
  color: #0f62c9;
  border-color: rgba(22, 119, 255, 0.36);
  background: #f7fbff;
}

.pager-arrow:disabled {
  color: #c4c9d2;
  background: #f4f5f7;
  cursor: default;
}

.pager-arrow svg {
  width: 14px;
  height: 14px;
}

.pager-status {
  min-width: 54px;
  color: #667085;
  font-size: 13px;
  text-align: center;
}

.pager-status b {
  color: #172033;
  font-weight: 750;
}

.page-size-select {
  width: 92px;
}

.object-detail {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 28px 32px 40px;
  background:
    linear-gradient(90deg, rgba(145, 122, 70, 0.13), transparent 14%, transparent 86%, rgba(145, 122, 70, 0.12)),
    radial-gradient(circle at 82% 8%, rgba(255, 255, 255, 0.82), transparent 32%),
    #f7f1dc;
}

.detail-head {
  width: min(100%, 1080px);
  display: flex;
  align-items: center;
  gap: 16px;
  margin: 0 auto 14px;
}

.object-icon {
  width: 86px;
  height: 86px;
  flex: 0 0 auto;
  font-size: 34px;
  box-shadow: 0 2px 12px rgba(24, 55, 98, 0.28), inset 0 0 22px rgba(255, 255, 255, 0.42);
}

.detail-title {
  min-width: 0;
  display: grid;
  gap: 7px;
}

.detail-title h3 {
  margin: 0;
  color: #17bfc8;
  font-size: 34px;
  line-height: 1.15;
  font-weight: 800;
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.78);
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  color: #8a7b61;
  font-size: 13px;
}

.term-strip {
  width: min(100%, 1080px);
  margin: 0 auto 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.term-strip span {
  padding: 3px 8px;
  color: #7c5b28;
  font-size: 12px;
  background: rgba(255, 255, 255, 0.52);
  border: 1px solid rgba(165, 132, 69, 0.34);
}

.time-hint-strip {
  width: min(100%, 1080px);
  margin: 0 auto 14px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  color: #7c5b28;
  font-size: 12px;
}

.time-hint-strip strong {
  padding: 3px 7px;
  color: #8a5e12;
  background: rgba(255, 246, 220, 0.82);
  border: 1px solid rgba(174, 128, 38, 0.34);
}

.time-hint-strip span {
  padding: 3px 8px;
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid rgba(165, 132, 69, 0.26);
}

.linked-item-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail-items {
  width: min(100%, 1080px);
  margin: 0 auto 14px;
}

.progression-items {
  margin-top: 8px;
}

.linked-item {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 3px 9px 3px 4px;
  color: #6f4d17;
  font: inherit;
  font-size: 13px;
  font-weight: 650;
  text-align: left;
  background: rgba(255, 251, 236, 0.82);
  border: 1px solid rgba(191, 151, 70, 0.48);
  cursor: default;
}

.linked-item.clickable {
  cursor: pointer;
}

.linked-item.clickable:hover {
  color: #4d340e;
  background: rgba(255, 246, 205, 0.96);
  border-color: rgba(194, 130, 24, 0.78);
}

.linked-item.muted {
  opacity: 0.72;
}

.linked-item.compact {
  min-height: 28px;
  color: #ead7a5;
  background: rgba(255, 248, 220, 0.08);
  border-color: rgba(255, 212, 95, 0.34);
}

.linked-item-icon {
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  display: inline-grid;
  place-items: center;
  overflow: hidden;
  background:
    radial-gradient(circle at 35% 25%, rgba(255, 255, 255, 0.74), transparent 22%),
    linear-gradient(135deg, #284d8d, #20b6cc 52%, #efe9ac);
  border: 1px solid rgba(244, 230, 170, 0.86);
}

.linked-item-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

:global(.fanxiu-linked-item-popover) {
  padding: 0 !important;
  border-color: rgba(180, 136, 54, 0.48) !important;
  box-shadow: 0 16px 34px rgba(42, 31, 16, 0.22) !important;
}

:global(.fanxiu-linked-item-popover .linked-item-popover-card) {
  display: grid;
  gap: 7px;
  padding: 12px 14px;
  color: #4c3b24;
  line-height: 1.62;
  background: #fffaf0;
}

:global(.fanxiu-linked-item-popover .linked-item-popover-card strong) {
  color: #0f7480;
  font-size: 15px;
}

:global(.fanxiu-linked-item-popover .linked-item-popover-card span) {
  color: #8a6a35;
  font-size: 12px;
}

.object-section {
  --wiki-term-color: #ffd45f;
  --wiki-number-color: #b9f08f;
  --wiki-variable-color: #44d6df;
  width: min(100%, 1080px);
  margin: 0 auto 14px;
  padding: 18px 26px 22px;
  box-sizing: border-box;
  color: #f7f0df;
  background: rgba(55, 56, 64, 0.95);
  border: 2px solid rgba(211, 190, 132, 0.95);
  box-shadow: 0 14px 34px rgba(50, 36, 18, 0.24);
}

.intro-section {
  --wiki-term-color: #b16a00;
  --wiki-number-color: #2f8f1d;
  --wiki-variable-color: #007f86;
  color: #554733;
  background: rgba(255, 252, 242, 0.74);
  border-color: rgba(193, 164, 92, 0.48);
  box-shadow: none;
}

.object-section h4 {
  width: max-content;
  min-width: 148px;
  margin: 0 0 12px;
  padding-bottom: 5px;
  color: #efe2ad;
  font-size: 20px;
  font-weight: 760;
  border-bottom: 2px solid rgba(214, 196, 136, 0.56);
}

.intro-section h4 {
  color: #8a6b33;
  border-bottom-color: rgba(138, 107, 51, 0.36);
}

.section-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.section-row h4 {
  margin-bottom: 0;
}

.section-count {
  color: rgba(247, 240, 223, 0.62);
  font-size: 13px;
}

.user-fields-section {
  padding-top: 14px;
  padding-bottom: 16px;
}

.user-fields-status-row {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 6px;
}

.user-fields-grid {
  display: grid;
  grid-template-columns: minmax(220px, 0.9fr) minmax(300px, 1.2fr);
  gap: 12px;
}

.user-field {
  display: grid;
  gap: 6px;
}

.user-field > span {
  color: #efd98f;
  font-size: 13px;
  font-weight: 700;
}

.user-field :deep(.el-textarea__inner) {
  color: #f7f0df;
  line-height: 1.5;
  background: rgba(255, 255, 255, 0.055);
  border-color: rgba(239, 217, 143, 0.28);
  border-radius: 2px;
  box-shadow: none;
}

.user-field :deep(.el-textarea__inner:focus) {
  border-color: rgba(68, 214, 223, 0.68);
  box-shadow: 0 0 0 1px rgba(68, 214, 223, 0.22);
}

.user-field :deep(.el-textarea__inner::placeholder) {
  color: rgba(247, 240, 223, 0.36);
}

.user-fields-save-state {
  color: rgba(247, 240, 223, 0.62);
  font-size: 12px;
}

.user-fields-save-state.state-saving {
  color: #44d6df;
}

.user-fields-save-state.state-error {
  color: #ff8f8f;
}

.skill-meta,
.skill-item-head,
.progression-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px 14px;
  color: rgba(247, 240, 223, 0.72);
  font-size: 13px;
}

.skill-item-head strong,
.progression-title strong {
  color: #f7f0df;
  font-size: 16px;
}

.progression-title-main {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 7px;
}

.game-rich-text,
.plain-rich-text {
  color: #f7f0df;
  font-size: 17px;
  line-height: 1.55;
  word-break: break-word;
}

.intro-section .plain-rich-text {
  color: #554733;
}

.game-rich-text {
  padding-top: 12px;
}

.plain-rich-text.compact {
  padding-top: 8px;
  font-size: 15px;
  line-height: 1.5;
}

.rich-section-list,
.progression-section-list {
  display: grid;
  gap: 9px;
  margin-top: 10px;
}

.rich-section-block,
.progression-section-block {
  display: grid;
  gap: 5px;
  padding: 2px 0 2px 10px;
  border-left: 2px solid rgba(239, 217, 143, 0.34);
}

.rich-section-title,
.progression-section-title {
  color: #ffd45f;
  font-size: 15px;
  font-weight: 760;
  line-height: 1.45;
}

.rich-section-body,
.progression-section-body {
  display: grid;
  gap: 2px;
}

.rich-section-body p,
.progression-section-body p {
  margin: 0;
  color: rgba(247, 240, 223, 0.9);
  font-size: 15px;
  line-height: 1.55;
  word-break: break-word;
}

.skill-section-list {
  padding-top: 12px;
}

.rich-section-list.compact {
  margin-top: 8px;
}

.rich-section-list.compact .rich-section-title,
.rich-section-list.compact .rich-section-body p {
  font-size: 14px;
}

.skill-list,
.progression-list,
.lingjie-feature-list,
.lingjie-group-list {
  display: grid;
  gap: 10px;
}

.item-field-section {
  color: #f7f0df;
}

.object-field-list {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 8px 14px;
  margin: 0;
  color: rgba(247, 240, 223, 0.82);
  font-size: 14px;
}

.object-field-list dt {
  color: #efd98f;
}

.object-field-list dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.skill-item,
.progression-item,
.lingjie-feature-item,
.lingjie-group {
  padding: 10px 12px;
  background: rgba(20, 22, 28, 0.22);
  border-left: 3px solid rgba(255, 212, 95, 0.72);
}

.progression-item.merged {
  background: rgba(255, 212, 95, 0.06);
  border-left-color: rgba(68, 214, 223, 0.78);
}

.progression-item.merged .progression-title strong {
  color: #44d6df;
}

.lingjie-group-list {
  margin-top: 10px;
}

.lingjie-group {
  border-left-color: rgba(68, 214, 223, 0.72);
}

.lingjie-group-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px 14px;
  color: rgba(247, 240, 223, 0.62);
  font-size: 13px;
}

.lingjie-group-head strong {
  color: #44d6df;
  font-size: 15px;
}

.inherit-strip {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.inherit-badge {
  display: inline-flex;
  padding: 2px 7px;
  color: rgba(247, 240, 223, 0.72);
  font-size: 12px;
  background: rgba(255, 244, 208, 0.06);
  border: 1px solid rgba(239, 217, 143, 0.18);
  cursor: default;
}

:global(.fanxiu-inherit-tooltip) {
  max-width: min(760px, calc(100vw - 48px));
  border: 1px solid rgba(239, 217, 143, 0.28) !important;
  background: #3f4149 !important;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.28);
}

:global(.fanxiu-inherit-tooltip .el-popper__arrow::before) {
  border-color: rgba(239, 217, 143, 0.28) !important;
  background: #3f4149 !important;
}

:global(.fanxiu-inherit-tooltip .inherit-tooltip-content) {
  max-width: min(720px, calc(100vw - 72px));
  max-height: min(520px, 62vh);
  overflow: auto;
  color: #f7f0df;
  font-size: 14px;
  line-height: 1.55;
  white-space: normal;
  word-break: break-word;
}

:global(.fanxiu-inherit-tooltip .inherit-tooltip-title) {
  margin-bottom: 6px;
  color: #44d6df;
  font-weight: 700;
}

:global(.fanxiu-inherit-tooltip .inherit-tooltip-entry) {
  display: flex;
  gap: 7px;
  align-items: baseline;
}

:global(.fanxiu-inherit-tooltip .inherit-tooltip-entry + .inherit-tooltip-entry) {
  margin-top: 4px;
}

:global(.fanxiu-inherit-tooltip .inherit-tooltip-entry b) {
  flex: 0 0 auto;
  color: #ffd45f;
  font-weight: 700;
}

:global(.fanxiu-inherit-tooltip .wiki-term) {
  color: #ffd45f;
  font-weight: 700;
}

:global(.fanxiu-inherit-tooltip .wiki-number) {
  color: #b9f08f;
  font-weight: 700;
}

:global(.fanxiu-inherit-tooltip .wiki-variable) {
  color: #44d6df;
  font-weight: 800;
}

.attr-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.attr-strip span {
  display: inline-flex;
  gap: 4px;
  padding: 2px 8px;
  background: rgba(255, 244, 208, 0.08);
  border: 1px solid rgba(239, 217, 143, 0.22);
  color: rgba(247, 240, 223, 0.88);
  font-size: 13px;
}

.attr-strip b {
  color: #efd98f;
}

.feature-link {
  display: grid;
  gap: 7px;
  margin-top: 10px;
  padding: 9px 10px;
  background: rgba(68, 214, 223, 0.08);
  border: 1px solid rgba(68, 214, 223, 0.24);
}

.feature-link-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  color: rgba(247, 240, 223, 0.62);
  font-size: 13px;
}

.feature-link-title strong {
  color: #44d6df;
  font-size: 14px;
}

.feature-effects {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.feature-effects span {
  padding: 2px 6px;
  color: rgba(247, 240, 223, 0.82);
  font-size: 12px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.feature-static-text {
  color: rgba(247, 240, 223, 0.84);
  font-size: 13px;
  line-height: 1.45;
}

.runtime-stat-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 10px;
}

.runtime-stat-grid span,
.runtime-line b {
  padding: 2px 7px;
  color: #efd98f;
  font-size: 12px;
  font-weight: 700;
  background: rgba(255, 244, 208, 0.08);
  border: 1px solid rgba(239, 217, 143, 0.2);
}

.runtime-card-list {
  display: grid;
  gap: 10px;
}

.runtime-card {
  display: grid;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.035);
  border-left: 3px solid #44d6df;
}

.runtime-line {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  align-items: baseline;
  color: rgba(247, 240, 223, 0.88);
  font-size: 13px;
  line-height: 1.45;
}

.runtime-timeline-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.runtime-timeline {
  display: grid;
  gap: 4px;
  padding: 8px 10px;
  background: rgba(68, 214, 223, 0.07);
  border: 1px solid rgba(68, 214, 223, 0.2);
}

.runtime-timeline strong {
  color: #44d6df;
  font-size: 13px;
}

.runtime-timeline span {
  color: rgba(247, 240, 223, 0.7);
  font-size: 12px;
}

.faze-resource {
  display: grid;
  gap: 8px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid rgba(239, 217, 143, 0.2);
}

.faze-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  color: #44d6df;
  font-weight: 700;
}

.faze-title small {
  color: rgba(247, 240, 223, 0.5);
  font-size: 12px;
  font-weight: 500;
}

.faze-tips {
  display: grid;
  gap: 5px;
  color: rgba(247, 240, 223, 0.86);
  font-size: 13px;
  line-height: 1.45;
}

.faze-tips span {
  overflow-wrap: anywhere;
}

.faze-tips b {
  margin-right: 6px;
  color: #ffd45f;
}

:deep(.wiki-term) {
  color: var(--wiki-term-color, #ffd45f);
  font-weight: 700;
}

:deep(.wiki-number) {
  color: var(--wiki-number-color, #b9f08f);
  font-weight: 700;
}

:deep(.wiki-variable) {
  color: var(--wiki-variable-color, #44d6df);
  font-weight: 800;
}

.source-details {
  width: min(100%, 1080px);
  margin: 8px auto 0;
  color: #806f50;
  font-size: 12px;
}

.source-details summary {
  width: max-content;
  cursor: pointer;
}

.source-details dl {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 5px 12px;
  margin: 8px 0 0;
}

.source-details dt {
  color: #9a7a45;
}

.source-details dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.empty-state {
  padding: 24px;
  color: #98a2b3;
  font-size: 14px;
}

@media (max-width: 1100px) {
  .object-workspace {
    grid-template-columns: clamp(300px, 32%, 380px) minmax(0, 1fr);
  }

  .object-detail {
    padding: 22px 24px 34px;
  }

  .detail-title h3 {
    font-size: 29px;
  }
}

@media (max-width: 900px) {
  .fanxiu-wiki-page {
    padding: 14px;
  }

  .toolbar {
    flex-wrap: wrap;
  }

  .query-input {
    width: min(100%, 320px);
  }

  .facet-row {
    grid-template-columns: 58px minmax(0, 1fr);
    column-gap: 8px;
  }

  .facet-options {
    gap: 2px 12px;
  }

  .object-workspace {
    grid-template-columns: 1fr;
  }

  .object-list {
    max-height: 42vh;
    border-right: 0;
    border-bottom: 1px solid #dfe4ec;
  }

  .detail-head {
    align-items: flex-start;
  }

  .object-icon {
    width: 66px;
    height: 66px;
    font-size: 27px;
  }

  .detail-title h3 {
    font-size: 25px;
  }

  .object-section {
    padding: 16px 18px 20px;
  }

  .user-fields-grid {
    grid-template-columns: 1fr;
  }
}
</style>

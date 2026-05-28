<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, ArrowRight, Close, Refresh, Search, TopRight } from '@element-plus/icons-vue'

import {
  buildFanxiuResourceHref,
  buildFanxiuLinkTargetGroups,
  buildFanxiuRewardPreview,
  cleanFanxiuDisplayText,
  cleanFanxiuPreview,
  escapeFanxiuHtml,
  renderFanxiuRichText,
  sameFanxiuPreview,
  type FanxiuResourceType,
  type FanxiuResourceLinkTarget,
} from '../resourceRenderer'
import FanxiuResourceHoverScope from '../FanxiuResourceHoverScope.vue'
import FanxiuLinkedItemChip from '../FanxiuLinkedItemChip.vue'
import {
  getHiddenFanxiuPacketProtocols,
  isFanxiuPacketProtocolVisible,
  setFanxiuPacketProtocolVisible,
} from '../packetProtocolVisibility'

import {
  getFanxiuActivityCard,
  getFanxiuDigitDoorCharacterCard,
  getFanxiuDigitDoorEnhanceGroup,
  getFanxiuDigitDoorLevelConfig,
  getFanxiuDoupoTDPartnerCard,
  getFanxiuDoupoTDRewardConfig,
  getFanxiuGongfaCard,
  getFanxiuGongfaHomeMakeBuffParameterSemantics,
  getFanxiuGongfaHomeMakeStaticDetail,
  getFanxiuGongfaHomeMakeXianShuFormulaCatalog,
  getFanxiuGongfaSpecialFazeCatalog,
  getFanxiuItemCard,
  getFanxiuLingjieFeatureCard,
  getFanxiuProtocolSemantics,
  getFanxiuStaticAssetManifest,
  getFanxiuStaticAssetPreviewManifest,
  getFanxiuResourceIconUrl,
  getFanxiuStaticVisualManifest,
  getFanxiuWikiLinkIndex,
  getFanxiuWwiseMp3Manifest,
  listFanxiuTcpBusinessEntries,
  searchFanxiuStaticVisualByImage,
  searchFanxiuActivityCards,
  searchFanxiuDigitDoorCharacterCards,
  searchFanxiuDigitDoorEnhanceGroups,
  searchFanxiuDigitDoorLevelConfigs,
  searchFanxiuDoupoTDPartnerCards,
  searchFanxiuDoupoTDRewardConfigs,
  searchFanxiuGongfaCards,
  searchFanxiuItemCards,
  searchFanxiuLingjieFeatureCards,
  type FanxiuActivityCard,
  type FanxiuActivityOption,
  type FanxiuActivityRewardRow,
  type FanxiuActivityRewardSection,
  type FanxiuActivitySearchItem,
  type FanxiuActivityStats,
  type FanxiuDigitDoorBuffRuntime,
  type FanxiuDigitDoorCharacterCard,
  type FanxiuDigitDoorCharacterSearchItem,
  type FanxiuDigitDoorDoorEffect,
  type FanxiuDigitDoorDoorEffectOption,
  type FanxiuDigitDoorDoorEffectPool,
  type FanxiuDigitDoorDoorRefreshPoint,
  type FanxiuDigitDoorEnhance,
  type FanxiuDigitDoorEnhanceGroup,
  type FanxiuDigitDoorEnhanceGroupSearchItem,
  type FanxiuDigitDoorEnhanceRef,
  type FanxiuDigitDoorLevelMilestone,
  type FanxiuDigitDoorLevelConfig,
  type FanxiuDigitDoorLevelSearchItem,
  type FanxiuDigitDoorMonsterRefreshMonster,
  type FanxiuDigitDoorMonsterRefreshPoint,
  type FanxiuDigitDoorMonsterSkill,
  type FanxiuDigitDoorRewardItem,
  type FanxiuDigitDoorStageOption,
  type FanxiuDigitDoorStageReward,
  type FanxiuDigitDoorLogicSkill,
  type FanxiuDigitDoorSkill,
  type FanxiuDigitDoorSkillEnhanceEffect,
  type FanxiuDigitDoorStats,
  type FanxiuDoupoTDAttrEntry,
  type FanxiuDoupoTDComposeCard,
  type FanxiuDoupoTDComposeProgressReward,
  type FanxiuDoupoTDComposeQualitySource,
  type FanxiuDoupoTDDrawSource,
  type FanxiuDoupoTDPartnerCard,
  type FanxiuDoupoTDPartnerSearchItem,
  type FanxiuDoupoTDRewardConfigRewardItem,
  type FanxiuDoupoTDRewardConfigSearchItem,
  type FanxiuDoupoTDRewardConfigStats,
  type FanxiuDoupoTDRewardItem,
  type FanxiuDoupoTDBuffFlowFunction,
  type FanxiuDoupoTDBuffRuntime,
  type FanxiuDoupoTDLogicSkill,
  type FanxiuDoupoTDSkill,
  type FanxiuDoupoTDSkillStrength,
  type FanxiuDoupoTDStats,
  type FanxiuFacetIndex,
  type FanxiuGongfaCard,
  type FanxiuGongfaHomeMakeBuffParameterGroup,
  type FanxiuGongfaHomeMakeBuffParameterLink,
  type FanxiuGongfaHomeMakeBuffParameterSemanticsResponse,
  type FanxiuGongfaHomeMakeStaticDetailResponse,
  type FanxiuGongfaHomeMakeStaticDetailRow,
  type FanxiuGongfaHomeMakeXianShuFormulaCatalogResponse,
  type FanxiuGongfaHomeMakeXianShuFormulaGroup,
  type FanxiuGongfaLinkedItem,
  type FanxiuGongfaProgressionRow,
  type FanxiuGongfaProgressionSection,
  type FanxiuGongfaQualityPartOption,
  type FanxiuGongfaSearchItem,
  type FanxiuGongfaSkill,
  type FanxiuGongfaSkillTypeOption,
  type FanxiuGongfaStats,
  type FanxiuGongfaSpecialFazeCatalogResponse,
  type FanxiuGongfaSpecialFazeEffectType,
  type FanxiuGongfaSpecialFazeReason,
  type FanxiuGongfaSpecialFazeStage,
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
  type FanxiuProtocolSemanticEdge,
  type FanxiuProtocolSemanticFeature,
  type FanxiuProtocolSemanticResponse,
  type FanxiuProtocolSemanticRow,
  type FanxiuStaticAssetManifestResponse,
  type FanxiuStaticAssetManifestRow,
  type FanxiuStaticAssetPreviewManifestResponse,
  type FanxiuStaticAssetPreviewItem,
  type FanxiuStaticVisualManifestResponse,
  type FanxiuStaticVisualManifestRow,
  type FanxiuTcpBusinessEntry,
  type FanxiuTcpBusinessCategorySummary,
  type FanxiuTcpBusinessProtocolSample,
  type FanxiuTcpBusinessProtocolSummary,
  type FanxiuTimelineHint,
  type FanxiuWwiseMp3ManifestResponse,
  type FanxiuWwiseMp3ManifestRow,
} from '@/api/fanxiu'

const PAGE_CONFIG_STORAGE_KEY = 'fanxiu:wiki:object-page-config'
const SEARCH_HISTORY_STORAGE_KEY = 'fanxiu:wiki:search-history'
const FACET_OPTION_DISPLAY_LIMIT = 100
const SEARCH_HISTORY_LIMIT = 12
const PAGE_SIZE_OPTIONS = [30, 50, 80, 120]
const WIKI_TABS = [
  { key: 'item', label: '道具' },
  { key: 'visual', label: '图片' },
  { key: 'asset', label: '素材' },
  { key: 'audio', label: '音乐' },
  { key: 'activity', label: '活动' },
  { key: 'gongfa', label: '功法' },
  { key: 'lingjie', label: '灵界词条' },
  { key: 'digitdoor', label: '数字门角色' },
  { key: 'digitdoor_level', label: '数字门关卡' },
  { key: 'digitdoor_enhance', label: '数字门强化' },
  { key: 'doupotd', label: '斗破角色' },
  { key: 'doupotd_reward', label: '斗破奖励' },
  { key: 'packet', label: '抓包' },
  { key: 'protocol', label: '协议' },
] as const

type WikiTab = typeof WIKI_TABS[number]['key']

const AUXILIARY_TOP_TAB_KEY = 'reverse'
const AUXILIARY_WIKI_TABS: Array<{ key: WikiTab; label: string }> = [
  { key: 'visual', label: '图片' },
  { key: 'asset', label: '素材' },
  { key: 'audio', label: '音乐' },
  { key: 'digitdoor', label: '数字门角色' },
  { key: 'digitdoor_level', label: '数字门关卡' },
  { key: 'digitdoor_enhance', label: '数字门强化' },
  { key: 'doupotd', label: '斗破角色' },
  { key: 'doupotd_reward', label: '斗破奖励' },
  { key: 'protocol', label: '协议' },
]
const AUXILIARY_WIKI_TAB_KEYS = new Set<WikiTab>(AUXILIARY_WIKI_TABS.map(tab => tab.key))
const TOP_WIKI_TABS = [
  { key: 'item', label: '道具' },
  { key: 'activity', label: '活动' },
  { key: 'gongfa', label: '功法' },
  { key: 'lingjie', label: '灵界词条' },
  { key: 'packet', label: '抓包' },
  { key: AUXILIARY_TOP_TAB_KEY, label: '逆向资料' },
] as const
type TopWikiTab = typeof TOP_WIKI_TABS[number]['key']

const DEFAULT_PROTOCOL_FEATURES: FanxiuProtocolSemanticFeature[] = [
  { key: 'bluestarsea', title: 'BlueStarSea' },
  { key: 'blld', title: 'BLLD' },
  { key: 'faze', title: 'Faze' },
  { key: 'gongfa', title: 'Gongfa' },
]
type SortMode = 'default' | 'time_asc' | 'time_desc'
type StaticAssetCatalogView = 'semantic'
const SORT_MODE_ORDER: SortMode[] = ['default', 'time_asc', 'time_desc']
const SORT_MODE_LABELS: Record<SortMode, string> = {
  default: '默认',
  time_asc: '时间↑',
  time_desc: '时间↓',
}
const PROGRESSION_ORDER = ['special_jie', 'renjie_jie', 'star', 'upgrade', 'gongfa_jie', 'lingjie_jie']
const GONGFA_QUALITY_GRADE_ORDER = ['上品', '珍品', '绝品', '仙品', '神品', '圣品']
const AUDIO_KIND_FILTER_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'bgm', label: 'BGM' },
  { value: 'ambient', label: 'AMB' },
  { value: 'ui', label: 'UI' },
  { value: 'audio', label: '音效' },
] as const
const VISUAL_ASSET_GROUP_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'icon', label: '图标' },
  { value: 'text', label: '标题' },
  { value: 'image', label: '大图' },
  { value: 'sprite', label: '切片' },
  { value: 'apk', label: 'APK' },
] as const
const STATIC_ASSET_SEMANTIC_GROUP_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'function', label: '功能' },
  { value: 'item', label: '道具' },
  { value: 'activity', label: '活动' },
  { value: 'activity_gift', label: '礼包' },
  { value: 'model', label: '模型' },
  { value: 'monster', label: '怪物' },
  { value: 'skill', label: '技能' },
  { value: 'buff', label: 'Buff' },
  { value: 'gongfa_skill', label: '功法技能' },
] as const
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

const WIKI_LINK_ALIAS_BLACKLIST = new Set(['攻击'])

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
  activityKindFilter?: string
  activityTimeFilter?: string
  activityTypeFilter?: string
  visualAssetGroupFilter?: string
  staticAssetCatalogView?: StaticAssetCatalogView
  staticAssetGroupFilter?: string
  audioKindFilter?: string
  digitDoorStageFilter?: string
  protocolFeature?: string
  protocolRoleFilter?: string
  protocolOperationFilter?: string
  qualityFilter?: string
  sortMode?: SortMode
  page?: number
  pageSize?: number
  selectedId?: string
  expandedFacetRows?: Record<string, boolean>
}

type WikiLinkedItem = FanxiuGongfaLinkedItem | FanxiuLingjieFeatureItem
type WikiLinkTarget = FanxiuResourceLinkTarget
type PacketSampleTable = {
  title: string
  columns: Array<{ key: string; label: string }>
  rows: Array<Record<string, string>>
  fieldLabels?: Record<string, Record<string, string>>
}

const activeTab = ref<WikiTab>('item')
const selectedAuxiliaryTab = ref<WikiTab>('visual')
const activeTopTab = computed<TopWikiTab>({
  get() {
    return isAuxiliaryWikiTab(activeTab.value) ? AUXILIARY_TOP_TAB_KEY : activeTab.value as TopWikiTab
  },
  set(value) {
    activeTab.value = value === AUXILIARY_TOP_TAB_KEY ? selectedAuxiliaryTab.value : value as WikiTab
  },
})
const showAuxiliaryTabs = computed(() => isAuxiliaryWikiTab(activeTab.value))
const query = ref('')
const searchHistory = ref<Record<WikiTab, string[]>>({
  item: [],
  visual: [],
  asset: [],
  audio: [],
  activity: [],
  gongfa: [],
  lingjie: [],
  digitdoor: [],
  digitdoor_level: [],
  digitdoor_enhance: [],
  doupotd: [],
  doupotd_reward: [],
  protocol: [],
  packet: [],
})
const searchHistoryVisible = ref(false)
const gongfaQualityGradeFilter = ref('')
const gongfaQualityFamilyFilter = ref('')
const gongfaSkillTypeFilter = ref('')
const itemQualityFilter = ref('')
const itemTypeFilter = ref('')
const itemSubTypeFilter = ref('')
const activityKindFilter = ref('')
const activityTimeFilter = ref('')
const activityTypeFilter = ref('')
const visualAssetGroupFilter = ref('')
const staticAssetCatalogView = ref<StaticAssetCatalogView>('semantic')
const staticAssetGroupFilter = ref('')
const audioKindFilter = ref('')
const digitDoorStageFilter = ref('')
const protocolFeature = ref('bluestarsea')
const protocolRoleFilter = ref('')
const protocolOperationFilter = ref('')
const expandedFacetRows = ref<Record<string, boolean>>({})
const sortMode = ref<SortMode>('default')
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)
const stats = ref<FanxiuGongfaStats>({})
const itemStats = ref<FanxiuItemStats>({})
const visualManifest = ref<FanxiuStaticVisualManifestResponse | null>(null)
const visualSimilarityFile = ref<File | null>(null)
const visualSimilarityPreviewUrl = ref('')
const visualSimilaritySourceName = ref('')
const visualSimilarityPreviewList = computed(() => visualSimilarityPreviewUrl.value ? [visualSimilarityPreviewUrl.value] : [])
const staticAssetManifest = ref<FanxiuStaticAssetManifestResponse | null>(null)
const staticAssetPreviewManifest = ref<FanxiuStaticAssetPreviewManifestResponse | null>(null)
const audioManifest = ref<FanxiuWwiseMp3ManifestResponse | null>(null)
const activityStats = ref<FanxiuActivityStats>({})
const lingjieStats = ref<FanxiuLingjieFeatureStats>({})
const digitDoorStats = ref<FanxiuDigitDoorStats>({})
const digitDoorLevelStats = ref<FanxiuDigitDoorStats>({})
const digitDoorEnhanceStats = ref<FanxiuDigitDoorStats>({})
const doupoTDStats = ref<FanxiuDoupoTDStats>({})
const doupoTDRewardStats = ref<FanxiuDoupoTDRewardConfigStats>({})
const protocolResponse = ref<FanxiuProtocolSemanticResponse | null>(null)
const protocolBusinessCategories = ref<FanxiuTcpBusinessCategorySummary[]>([])
const packetProtocolDetails = ref<FanxiuTcpBusinessProtocolSummary[]>([])
const hiddenPacketProtocols = ref<string[]>(getHiddenFanxiuPacketProtocols())
const expandedPacketProtocol = ref('')
const packetProtocolSamples = ref<FanxiuTcpBusinessEntry[]>([])
const packetProtocolSamplesLoading = ref(false)
const selectedPacketCategory = ref('')
const catalogPath = ref('')
const gongfaQualityGradeOptions = ref<FanxiuGongfaQualityPartOption[]>([])
const gongfaQualityFamilyOptions = ref<FanxiuGongfaQualityPartOption[]>([])
const gongfaSkillTypeOptions = ref<FanxiuGongfaSkillTypeOption[]>([])
const itemQualityOptions = ref<FanxiuItemQualityOption[]>([])
const itemTypeOptions = ref<FanxiuItemTypeOption[]>([])
const itemSubTypeOptions = ref<FanxiuItemTypeOption[]>([])
const activityKindOptions = ref<FanxiuActivityOption[]>([])
const activityTimeOptions = ref<FanxiuActivityOption[]>([])
const activityTypeOptions = ref<FanxiuActivityOption[]>([])
const digitDoorStageOptions = ref<FanxiuDigitDoorStageOption[]>([])
const gongfaFacetIndex = ref<FanxiuFacetIndex | null>(null)
const itemFacetIndex = ref<FanxiuFacetIndex | null>(null)
const activityFacetIndex = ref<FanxiuFacetIndex | null>(null)
const gongfaItems = ref<FanxiuGongfaSearchItem[]>([])
const itemItems = ref<FanxiuItemSearchItem[]>([])
const visualItems = ref<FanxiuStaticVisualManifestRow[]>([])
const staticAssetItems = ref<FanxiuStaticAssetManifestRow[]>([])
const audioItems = ref<FanxiuWwiseMp3ManifestRow[]>([])
const activityItems = ref<FanxiuActivitySearchItem[]>([])
const lingjieItems = ref<FanxiuLingjieFeatureSearchItem[]>([])
const digitDoorItems = ref<FanxiuDigitDoorCharacterSearchItem[]>([])
const digitDoorLevelItems = ref<FanxiuDigitDoorLevelSearchItem[]>([])
const digitDoorEnhanceItems = ref<FanxiuDigitDoorEnhanceGroupSearchItem[]>([])
const doupoTDItems = ref<FanxiuDoupoTDPartnerSearchItem[]>([])
const doupoTDRewardItems = ref<FanxiuDoupoTDRewardConfigSearchItem[]>([])
const selectedId = ref('')
const selectedCard = ref<FanxiuGongfaCard | null>(null)
const selectedItem = ref<FanxiuItemCard | null>(null)
const selectedActivity = ref<FanxiuActivityCard | null>(null)
const selectedLingjieCard = ref<FanxiuLingjieFeatureCard | null>(null)
const selectedDigitDoorCharacter = ref<FanxiuDigitDoorCharacterCard | null>(null)
const selectedDigitDoorLevel = ref<FanxiuDigitDoorLevelConfig | null>(null)
const selectedDigitDoorStage = ref<FanxiuDigitDoorStageReward | null>(null)
const selectedDigitDoorEnhanceGroup = ref<FanxiuDigitDoorEnhanceGroup | null>(null)
const selectedDoupoTDPartner = ref<FanxiuDoupoTDPartnerCard | null>(null)
const selectedDoupoTDReward = ref<FanxiuDoupoTDRewardConfigSearchItem | null>(null)
const selectedHomeMakeStaticDetail = ref<FanxiuGongfaHomeMakeStaticDetailResponse | null>(null)
const selectedHomeMakeBuffParameterSemantics = ref<FanxiuGongfaHomeMakeBuffParameterSemanticsResponse | null>(null)
const selectedHomeMakeXianShuFormulaCatalog = ref<FanxiuGongfaHomeMakeXianShuFormulaCatalogResponse | null>(null)
const selectedSpecialFazeCatalog = ref<FanxiuGongfaSpecialFazeCatalogResponse | null>(null)
const homeMakeBuffOverview = ref<FanxiuGongfaHomeMakeBuffParameterSemanticsResponse | null>(null)
const homeMakeBuffParameterQuery = ref('')
const homeMakeFormulaQuery = ref('')
const homeMakeBuffOverviewQuery = ref('')
const selectedProgressionType = ref('')
const wikiLinkIndexItems = ref<WikiLinkTarget[]>([])
const contextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
  href: '',
})
const loadingList = ref(false)
const loadingDetail = ref(false)
const loadingStaticAssetPreview = ref(false)
const loadingHomeMakeStaticDetail = ref(false)
const loadingHomeMakeBuffParameterSemantics = ref(false)
const loadingHomeMakeFormulaCatalog = ref(false)
const loadingSpecialFazeCatalog = ref(false)
const loadingHomeMakeBuffOverview = ref(false)
const gongfaDetailCache = new Map<string, FanxiuGongfaCard>()
const gongfaHomeMakeStaticDetailCache = new Map<string, FanxiuGongfaHomeMakeStaticDetailResponse | null>()
const gongfaHomeMakeBuffParameterSemanticsCache = new Map<string, FanxiuGongfaHomeMakeBuffParameterSemanticsResponse | null>()
const gongfaHomeMakeFormulaCatalogCache = new Map<string, FanxiuGongfaHomeMakeXianShuFormulaCatalogResponse | null>()
const gongfaSpecialFazeCatalogCache = new Map<string, FanxiuGongfaSpecialFazeCatalogResponse | null>()
const itemDetailCache = new Map<string, FanxiuItemCard>()
const itemDetailRefreshAttempts = new Map<string, string>()
const activityDetailCache = new Map<string, FanxiuActivityCard>()
const lingjieDetailCache = new Map<string, FanxiuLingjieFeatureCard>()
const digitDoorDetailCache = new Map<string, FanxiuDigitDoorCharacterCard>()
const digitDoorLevelDetailCache = new Map<string, { item: FanxiuDigitDoorLevelConfig; stage?: FanxiuDigitDoorStageReward | null }>()
const digitDoorEnhanceDetailCache = new Map<string, FanxiuDigitDoorEnhanceGroup>()
const doupoTDDetailCache = new Map<string, FanxiuDoupoTDPartnerCard>()
const doupoTDRewardDetailCache = new Map<string, FanxiuDoupoTDRewardConfigSearchItem>()
const route = useRoute()
const router = useRouter()
let listRequestSeq = 0
let detailRequestSeq = 0
let staticAssetPreviewRequestSeq = 0
let homeMakeStaticDetailRequestSeq = 0
let homeMakeBuffParameterSemanticsRequestSeq = 0
let homeMakeFormulaCatalogRequestSeq = 0
let specialFazeCatalogRequestSeq = 0
let homeMakeBuffOverviewRequestSeq = 0
let applyingRouteState = false
let internalTabNavigation = false
let searchHistoryHideTimer: ReturnType<typeof setTimeout> | null = null

function isAuxiliaryWikiTab(tab: WikiTab) {
  return AUXILIARY_WIKI_TAB_KEYS.has(tab)
}

function normalizeWikiTab(value: unknown): WikiTab | null {
  const text = Array.isArray(value) ? String(value[0] ?? '') : String(value ?? '')
  if (text === AUXILIARY_TOP_TAB_KEY) return selectedAuxiliaryTab.value
  return WIKI_TABS.some(tab => tab.key === text) ? text as WikiTab : null
}

function queryValue(value: unknown) {
  if (Array.isArray(value)) return String(value[0] ?? '').trim()
  return String(value ?? '').trim()
}

function applyRouteState() {
  const routeTab = normalizeWikiTab(route.query.tab)
  const hasRouteId = Object.prototype.hasOwnProperty.call(route.query, 'id')
  const routeId = queryValue(route.query.id)
  const routeSearch = queryValue(route.query.q)
  let changed = false
  applyingRouteState = true
  try {
    if (routeTab && isAuxiliaryWikiTab(routeTab)) {
      selectedAuxiliaryTab.value = routeTab
    }
    if (routeTab && activeTab.value !== routeTab) {
      activeTab.value = routeTab
      changed = true
    }
    if (routeSearch && query.value !== routeSearch) {
      query.value = routeSearch
      changed = true
    }
    if (hasRouteId && selectedId.value !== routeId) {
      selectedId.value = routeId
      changed = true
    } else if (routeTab && !hasRouteId && selectedId.value) {
      selectedId.value = ''
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
  if (queryValue(route.query.q) && query.value) {
    nextQuery.q = query.value
  } else {
    delete nextQuery.q
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

function normalizeAudioKindFilter(value: unknown) {
  const text = String(value ?? '').trim().toLowerCase()
  if (text === 'amb') return 'ambient'
  return AUDIO_KIND_FILTER_OPTIONS.some(option => option.value === text) ? text : ''
}

function normalizeVisualAssetGroupFilter(value: unknown) {
  const text = String(value ?? '').trim().toLowerCase()
  return VISUAL_ASSET_GROUP_OPTIONS.some(option => option.value === text) ? text : ''
}

function normalizeStaticAssetGroupFilter(value: unknown) {
  const text = String(value ?? '').trim().toLowerCase()
  return STATIC_ASSET_SEMANTIC_GROUP_OPTIONS.some(option => option.value === text) ? text : ''
}

function normalizeStaticAssetCatalogView(value: unknown): StaticAssetCatalogView {
  return 'semantic'
}

function createEmptySearchHistory(): Record<WikiTab, string[]> {
  return Object.fromEntries(WIKI_TABS.map(tab => [tab.key, []])) as Record<WikiTab, string[]>
}

function normalizeSearchQuery(value: unknown) {
  return String(value ?? '').replace(/\s+/g, ' ').trim()
}

function normalizeSearchHistoryList(value: unknown) {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  const list: string[] = []
  for (const item of value) {
    const text = normalizeSearchQuery(item)
    if (!text || seen.has(text)) continue
    seen.add(text)
    list.push(text)
    if (list.length >= SEARCH_HISTORY_LIMIT) break
  }
  return list
}

function loadSearchHistory() {
  if (!canUseLocalStorage()) return
  try {
    const raw = window.localStorage.getItem(SEARCH_HISTORY_STORAGE_KEY)
    if (!raw) return
    const data = JSON.parse(raw) as Partial<Record<WikiTab, unknown>>
    const next = createEmptySearchHistory()
    for (const tab of WIKI_TABS) {
      next[tab.key] = normalizeSearchHistoryList(data?.[tab.key])
    }
    searchHistory.value = next
  } catch (error) {
    console.warn('Failed to load Fanxiu wiki search history:', error)
    window.localStorage.removeItem(SEARCH_HISTORY_STORAGE_KEY)
  }
}

function persistSearchHistory() {
  if (!canUseLocalStorage()) return
  try {
    window.localStorage.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify(searchHistory.value))
  } catch (error) {
    console.warn('Failed to persist Fanxiu wiki search history:', error)
  }
}

function recordSearchHistory(value = query.value) {
  const text = normalizeSearchQuery(value)
  if (!text) return
  const current = searchHistory.value[activeTab.value] ?? []
  searchHistory.value = {
    ...searchHistory.value,
    [activeTab.value]: [
      text,
      ...current.filter(item => item !== text),
    ].slice(0, SEARCH_HISTORY_LIMIT),
  }
  persistSearchHistory()
}

function openSearchHistory() {
  if (searchHistoryHideTimer) {
    clearTimeout(searchHistoryHideTimer)
    searchHistoryHideTimer = null
  }
  searchHistoryVisible.value = visibleSearchHistory.value.length > 0
}

function scheduleCloseSearchHistory() {
  if (searchHistoryHideTimer) clearTimeout(searchHistoryHideTimer)
  searchHistoryHideTimer = setTimeout(() => {
    searchHistoryHideTimer = null
    searchHistoryVisible.value = false
  }, 120)
}

function chooseSearchHistory(text: string) {
  query.value = text
  searchHistoryVisible.value = false
  executeSearchFromFirstPage()
}

function clearCurrentSearchHistory() {
  searchHistory.value = {
    ...searchHistory.value,
    [activeTab.value]: [],
  }
  searchHistoryVisible.value = false
  persistSearchHistory()
}

function loadPageConfig() {
  if (!canUseLocalStorage()) return
  try {
    const raw = window.localStorage.getItem(PAGE_CONFIG_STORAGE_KEY)
    if (!raw) return
    const config = JSON.parse(raw) as PageConfig
    const configTab = normalizeWikiTab(config.activeTab)
    if (configTab) {
      activeTab.value = configTab
      if (isAuxiliaryWikiTab(configTab)) selectedAuxiliaryTab.value = configTab
    }
    query.value = String(config.query ?? '')
    gongfaQualityGradeFilter.value = String(config.gongfaQualityGradeFilter ?? '')
    gongfaQualityFamilyFilter.value = String(config.gongfaQualityFamilyFilter ?? '')
    gongfaSkillTypeFilter.value = String(config.gongfaSkillTypeFilter ?? '')
    itemQualityFilter.value = String(config.itemQualityFilter ?? '')
    itemTypeFilter.value = String(config.itemTypeFilter ?? '')
    itemSubTypeFilter.value = String(config.itemSubTypeFilter ?? '')
    activityKindFilter.value = String(config.activityKindFilter ?? '')
    activityTimeFilter.value = String(config.activityTimeFilter ?? '')
    activityTypeFilter.value = String(config.activityTypeFilter ?? '')
    visualAssetGroupFilter.value = normalizeVisualAssetGroupFilter(config.visualAssetGroupFilter)
    staticAssetCatalogView.value = normalizeStaticAssetCatalogView(config.staticAssetCatalogView)
    staticAssetGroupFilter.value = normalizeStaticAssetGroupFilter(config.staticAssetGroupFilter)
    audioKindFilter.value = normalizeAudioKindFilter(config.audioKindFilter)
    digitDoorStageFilter.value = String(config.digitDoorStageFilter ?? '')
    protocolFeature.value = String(config.protocolFeature ?? 'bluestarsea') || 'bluestarsea'
    protocolRoleFilter.value = String(config.protocolRoleFilter ?? '')
    protocolOperationFilter.value = String(config.protocolOperationFilter ?? '')
    sortMode.value = normalizeSortMode(config.sortMode)
    page.value = normalizePage(config.page, 1)
    pageSize.value = normalizePageSize(config.pageSize, 50)
    selectedId.value = String(config.selectedId ?? '')
    expandedFacetRows.value = config.expandedFacetRows && typeof config.expandedFacetRows === 'object'
      ? { ...config.expandedFacetRows }
      : {}
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
      activityKindFilter: activityKindFilter.value,
      activityTimeFilter: activityTimeFilter.value,
      activityTypeFilter: activityTypeFilter.value,
      visualAssetGroupFilter: visualAssetGroupFilter.value,
      staticAssetCatalogView: staticAssetCatalogView.value,
      staticAssetGroupFilter: staticAssetGroupFilter.value,
      audioKindFilter: audioKindFilter.value,
      digitDoorStageFilter: digitDoorStageFilter.value,
      protocolFeature: protocolFeature.value,
      protocolRoleFilter: protocolRoleFilter.value,
      protocolOperationFilter: protocolOperationFilter.value,
      sortMode: sortMode.value,
      page: page.value,
      pageSize: pageSize.value,
      selectedId: selectedId.value,
      expandedFacetRows: expandedFacetRows.value,
    }))
  } catch (error) {
    console.warn('Failed to persist Fanxiu wiki object page config:', error)
  }
}

const pageCount = computed(() => {
  return Math.max(1, Math.ceil(Math.max(total.value, 0) / Math.max(pageSize.value, 1)))
})

const activeSearchHistory = computed(() => searchHistory.value[activeTab.value] ?? [])

const visibleSearchHistory = computed(() => {
  const needle = normalizeSearchQuery(query.value)
  const list = activeSearchHistory.value
  return needle ? list.filter(item => item.includes(needle)) : list
})

const visualAssetGroupQueryCounts = computed<Record<string, number>>(() => {
  return visualManifest.value?.stats?.query_asset_groups ?? visualManifest.value?.stats?.asset_groups ?? {}
})

const visualAssetGroupFilterOptions = computed(() => {
  const counts = visualAssetGroupQueryCounts.value
  const allCount = visualManifest.value?.stats?.query_total ?? visualManifest.value?.filtered ?? visualManifest.value?.total ?? 0
  return VISUAL_ASSET_GROUP_OPTIONS.map(option => ({
    ...option,
    count: option.value ? Number(counts[option.value] ?? 0) : Number(allCount),
  }))
})

const staticAssetGroupQueryCounts = computed<Record<string, number>>(() => {
  return staticAssetManifest.value?.stats?.query_asset_groups ?? staticAssetManifest.value?.stats?.asset_groups ?? {}
})

const staticAssetGroupFilterOptions = computed(() => {
  const counts = staticAssetGroupQueryCounts.value
  const allCount = staticAssetManifest.value?.stats?.query_total ?? staticAssetManifest.value?.filtered ?? staticAssetManifest.value?.total ?? 0
  return STATIC_ASSET_SEMANTIC_GROUP_OPTIONS.map(option => ({
    ...option,
    count: option.value ? Number(counts[option.value] ?? 0) : Number(allCount),
  }))
})

const audioKindQueryCounts = computed<Record<string, number>>(() => {
  return audioManifest.value?.stats?.query_kinds ?? audioManifest.value?.stats?.kinds ?? {}
})

const audioKindFilterOptions = computed(() => {
  const counts = audioKindQueryCounts.value
  const allCount = audioManifest.value?.stats?.query_total ?? audioManifest.value?.total ?? 0
  return AUDIO_KIND_FILTER_OPTIONS.map(option => ({
    ...option,
    count: option.value ? Number(counts[option.value] ?? 0) : Number(allCount),
  }))
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

const activityFacetFilters = computed<FacetFilterMap>(() => ({
  kind_key: activityKindFilter.value,
  time_kind: activityTimeFilter.value,
  activity_type: activityTypeFilter.value,
}))

const selectedListItem = computed(() => {
  if (activeTab.value === 'item') {
    return itemItems.value.find(item => String(item.id) === selectedId.value) ?? null
  }
  if (activeTab.value === 'visual') {
    return visualItems.value.find(item => getVisualAssetKey(item) === selectedId.value) ?? null
  }
  if (activeTab.value === 'asset') {
    return staticAssetItems.value.find(item => getStaticAssetKey(item) === selectedId.value) ?? null
  }
  if (activeTab.value === 'audio') {
    return audioItems.value.find(item => getAudioAssetKey(item) === selectedId.value) ?? null
  }
  if (activeTab.value === 'activity') {
    return activityItems.value.find(item => String(item.id) === selectedId.value) ?? null
  }
  if (activeTab.value === 'lingjie') {
    return lingjieItems.value.find(item => String(item.gongfa_id) === selectedId.value) ?? null
  }
  if (activeTab.value === 'digitdoor') {
    return digitDoorItems.value.find(item => String(item.id) === selectedId.value) ?? null
  }
  if (activeTab.value === 'digitdoor_level') {
    return digitDoorLevelItems.value.find(item => String(item.id) === selectedId.value) ?? null
  }
  if (activeTab.value === 'digitdoor_enhance') {
    return digitDoorEnhanceItems.value.find(item => String(item.id ?? item.char_id) === selectedId.value) ?? null
  }
  if (activeTab.value === 'doupotd') {
    return doupoTDItems.value.find(item => String(item.id) === selectedId.value) ?? null
  }
  if (activeTab.value === 'doupotd_reward') {
    return doupoTDRewardItems.value.find(item => getDoupoTDRewardConfigKey(item) === selectedId.value) ?? null
  }
  if (activeTab.value === 'protocol') {
    return null
  }
  return gongfaItems.value.find(item => String(item.id) === selectedId.value) ?? null
})

function shouldRefetchItemDetail(itemId: string | number, cached: FanxiuItemCard | null | undefined) {
  if (!cached) return true
  const summary = itemItems.value.find(item => String(item.id) === String(itemId))
  return Boolean((summary?.effect_detail_preview || cached.effect_detail_preview) && !cached.effect_details?.length)
}

function getItemDetailRefreshSignature(itemId: string | number, cached: FanxiuItemCard | null | undefined) {
  const summary = itemItems.value.find(item => String(item.id) === String(itemId))
  return [
    summary?.effect_detail_preview || cached?.effect_detail_preview || '',
    itemStats.value?.item_with_effect_detail_count ?? '',
    itemStats.value?.item_with_talisman_detail_count ?? '',
  ].join('|')
}

function ensureSelectedItemDetailFresh() {
  if (activeTab.value !== 'item' || !selectedId.value || loadingDetail.value) return
  const current = selectedItem.value
  if (!current || String(current.id) !== selectedId.value || !shouldRefetchItemDetail(selectedId.value, current)) return
  const signature = getItemDetailRefreshSignature(selectedId.value, current)
  if (itemDetailRefreshAttempts.get(selectedId.value) === signature) return
  itemDetailRefreshAttempts.set(selectedId.value, signature)
  itemDetailCache.delete(selectedId.value)
  void selectItem(selectedId.value)
}

const selectedVisualAsset = computed(() => visualItems.value.find(item => getVisualAssetKey(item) === selectedId.value) ?? null)
const selectedStaticAsset = computed(() => staticAssetItems.value.find(item => getStaticAssetKey(item) === selectedId.value) ?? null)
const selectedAudioAsset = computed(() => audioItems.value.find(item => getAudioAssetKey(item) === selectedId.value) ?? null)
const selectedStaticAssetPreviewItems = computed(() => staticAssetPreviewManifest.value?.items ?? [])
const selectedStaticAssetBusinessImages = computed<FanxiuStaticAssetPreviewItem[]>(() => {
  const item = selectedStaticAsset.value
  const urls = item?.semantic_visual_media_urls ?? []
  const names = splitPipeText(item?.semantic_visual_names)
  const paths = splitPipeText(item?.semantic_visual_media_paths)
  return urls.map((url, index) => ({
    name: names[index] || paths[index] || `业务图片 ${index + 1}`,
    kind: 'business_image',
    media_path: paths[index] || url,
    media_url: url,
    object_type: '业务图片',
    is_original_image: true,
  }))
})
const selectedStaticAssetOriginalImages = computed(() =>
  selectedStaticAssetBusinessImages.value.length
    ? selectedStaticAssetBusinessImages.value
    : selectedStaticAssetPreviewItems.value.filter(item => item.is_original_image && item.media_url),
)
const selectedStaticAssetDerivedPreviews = computed(() =>
  selectedStaticAssetPreviewItems.value.filter(item => !item.is_original_image && item.media_url),
)

const contextMenuStyle = computed(() => ({
  left: `${contextMenu.value.x}px`,
  top: `${contextMenu.value.y}px`,
}))

const selectedTerms = computed(() => {
  if (activeTab.value === 'item') {
    return selectedItem.value?.terms?.slice(0, 12) ?? selectedListItem.value?.terms?.slice(0, 8) ?? []
  }
  if (activeTab.value === 'visual') {
    const item = selectedVisualAsset.value
    return uniqueLabels([item?.asset_group, item?.category, item?.source_kind, item?.atlas_key].filter(Boolean)).slice(0, 8)
  }
  if (activeTab.value === 'asset') {
    const item = selectedStaticAsset.value
    if (item?.semantic_id) {
      return uniqueLabels([item.semantic_group, item.semantic_type, item.semantic_visual_categories, item.linked_asset_groups].filter(Boolean)).slice(0, 8)
    }
    return uniqueLabels([item?.asset_group, item?.source_kind, item?.category, item?.suffix].filter(Boolean)).slice(0, 8)
  }
  if (activeTab.value === 'audio') {
    const item = selectedAudioAsset.value
    return uniqueLabels([item?.kind, item?.encoding, item?.sample_rate ? `${item.sample_rate}Hz` : '', item?.channels ? `${item.channels}ch` : ''].filter(Boolean)).slice(0, 8)
  }
  if (activeTab.value === 'activity') {
    return selectedActivity.value?.terms?.slice(0, 12) ?? selectedListItem.value?.terms?.slice(0, 8) ?? []
  }
  if (activeTab.value === 'lingjie') {
    return uniqueLabels([
      selectedLingjieCard.value?.main_feature_names,
      selectedLingjieCard.value?.side_feature_names,
      ...(selectedLingjieCard.value?.items ?? []).map(item => item.name),
    ]).slice(0, 12)
  }
  if (activeTab.value === 'digitdoor') {
    return selectedDigitDoorCharacter.value?.terms?.slice(0, 12) ?? selectedListItem.value?.terms?.slice(0, 8) ?? []
  }
  if (activeTab.value === 'digitdoor_level') {
    const rewards = (selectedDigitDoorLevel.value?.reward_items ?? [])
      .map(item => item.item?.name || item.id)
      .filter(Boolean)
    const monsters = (selectedDigitDoorLevel.value?.monster_refresh?.monsters ?? [])
      .map(item => item.name || item.monster_id)
      .filter(Boolean)
    return uniqueLabels([
      getDigitDoorStageName(selectedDigitDoorStage.value),
      getDigitDoorLevelRewardTitlePlain(selectedDigitDoorLevel.value),
      ...rewards,
      ...monsters,
    ]).slice(0, 12)
  }
  if (activeTab.value === 'digitdoor_enhance') {
    const group = selectedDigitDoorEnhanceGroup.value
    return uniqueLabels([
      group?.name,
      ...(group?.enhances ?? []).map(item => item.name),
      ...(group?.enhances ?? []).flatMap(item => (item.prereqs ?? []).map(ref => ref.name)),
    ].filter(Boolean)).slice(0, 12)
  }
  if (activeTab.value === 'doupotd') {
    return selectedDoupoTDPartner.value?.terms?.slice(0, 12) ?? selectedListItem.value?.terms?.slice(0, 8) ?? []
  }
  if (activeTab.value === 'doupotd_reward') {
    const itemNames = (selectedDoupoTDReward.value?.items ?? [])
      .map(item => item.item_name || item.item_id)
      .filter(Boolean)
    return uniqueLabels([selectedDoupoTDReward.value?.reward_title, ...itemNames]).slice(0, 12)
  }
  if (activeTab.value === 'protocol') {
    return []
  }
  return selectedCard.value?.terms?.slice(0, 12) ?? selectedListItem.value?.terms?.slice(0, 8) ?? []
})

const searchPlaceholder = computed(() => {
  if (activeTab.value === 'item') return '搜索道具 / 效果 / 描述 / ID'
  if (activeTab.value === 'visual') return '搜索图片 / 图标 / 背景 / atlas / sprite / 文件名'
  if (activeTab.value === 'asset') return '搜索语义素材 / 小绿瓶 / 道具 / 活动 / 技能 / 模型'
  if (activeTab.value === 'audio') return '搜索音乐 / BGM / WEM / bank / 文件名'
  if (activeTab.value === 'activity') return '搜索活动 / 奖励 / 条件 / ID'
  if (activeTab.value === 'lingjie') return '搜索灵界功法 / 道具 / 主词条 / 侧词条 / Feature'
  if (activeTab.value === 'digitdoor') return '搜索数字门角色 / 技能 / 门效果 / Buff'
  if (activeTab.value === 'digitdoor_level') return '搜索数字门关卡 / 奖励 / 推荐 / 怪物 / ID'
  if (activeTab.value === 'digitdoor_enhance') return '搜索数字门强化 / 条件 / 前置 / 互斥 / ID'
  if (activeTab.value === 'doupotd') return '搜索斗破角色 / 技能 / 卡牌 / 强化'
  if (activeTab.value === 'doupotd_reward') return '搜索斗破奖励 / 关卡 / 物品 / ID'
  if (activeTab.value === 'protocol') return '搜索 packet / handler / 字段 / 语义'
  if (activeTab.value === 'packet') return '搜索大类 / 业务包 / 含义'
  return '搜索功法 / 技能 / 效果 / 条件'
})

const objectSortParams = computed<{ sort_by?: string; sort_order?: string }>(() => {
  if (
    activeTab.value === 'lingjie'
    || activeTab.value === 'visual'
    || activeTab.value === 'asset'
    || activeTab.value === 'audio'
    || activeTab.value === 'digitdoor'
    || activeTab.value === 'digitdoor_level'
    || activeTab.value === 'digitdoor_enhance'
    || activeTab.value === 'doupotd'
    || activeTab.value === 'doupotd_reward'
    || activeTab.value === 'protocol'
    || activeTab.value === 'packet'
    || sortMode.value === 'default'
  ) return {}
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
  if (activeTab.value === 'visual') return '图片'
  if (activeTab.value === 'asset') return '素材'
  if (activeTab.value === 'audio') return '音乐'
  if (activeTab.value === 'activity') return '活动'
  if (activeTab.value === 'lingjie') return '灵界词条'
  if (activeTab.value === 'digitdoor') return '数字门角色'
  if (activeTab.value === 'digitdoor_level') return '数字门关卡'
  if (activeTab.value === 'digitdoor_enhance') return '数字门强化'
  if (activeTab.value === 'doupotd') return '斗破角色'
  if (activeTab.value === 'doupotd_reward') return '斗破奖励'
  if (activeTab.value === 'protocol') return '协议'
  if (activeTab.value === 'packet') return '抓包'
  return '功法'
})

const selectedProgressionSource = computed(() => {
  if (
    activeTab.value === 'lingjie'
    || activeTab.value === 'asset'
    || activeTab.value === 'digitdoor'
    || activeTab.value === 'digitdoor_level'
    || activeTab.value === 'digitdoor_enhance'
    || activeTab.value === 'doupotd'
    || activeTab.value === 'doupotd_reward'
    || activeTab.value === 'protocol'
    || activeTab.value === 'packet'
  ) return {}
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

const homeMakeStaticRows = computed(() => selectedHomeMakeStaticDetail.value?.rows ?? [])

const homeMakeStaticWarnings = computed(() => selectedHomeMakeStaticDetail.value?.warnings ?? [])

const homeMakeBuffParameterRawGroups = computed(() => selectedHomeMakeBuffParameterSemantics.value?.items ?? [])

const homeMakeBuffParameterGroups = computed(() => {
  const text = normalizeSearchQuery(homeMakeBuffParameterQuery.value).toLowerCase()
  if (!text) return homeMakeBuffParameterRawGroups.value
  const tokens = text.split(' ').filter(Boolean)
  return homeMakeBuffParameterRawGroups.value.filter(group => {
    const haystack = getHomeMakeBuffSearchText(group).toLowerCase()
    return tokens.every(token => haystack.includes(token))
  })
})

const homeMakeBuffParameterCounts = computed(() => selectedHomeMakeBuffParameterSemantics.value?.counts ?? null)

const homeMakeBuffParameterCountText = computed(() => {
  if (homeMakeBuffParameterQuery.value.trim()) {
    return `${homeMakeBuffParameterGroups.value.length}/${homeMakeBuffParameterRawGroups.value.length} 组`
  }
  const counts = homeMakeBuffParameterCounts.value
  return counts ? `${counts.groups} 组 · ${counts.candidate_rows} 条` : ''
})

const homeMakeFormulaRawGroups = computed(() => selectedHomeMakeXianShuFormulaCatalog.value?.groups ?? [])

const homeMakeFormulaGroups = computed(() => {
  const text = normalizeSearchQuery(homeMakeFormulaQuery.value).toLowerCase()
  if (!text) return homeMakeFormulaRawGroups.value
  const tokens = text.split(' ').filter(Boolean)
  return homeMakeFormulaRawGroups.value.filter(group => {
    const haystack = getHomeMakeFormulaSearchText(group).toLowerCase()
    return tokens.every(token => haystack.includes(token))
  })
})

const homeMakeFormulaCountText = computed(() => {
  const counts = selectedHomeMakeXianShuFormulaCatalog.value?.counts
  if (homeMakeFormulaQuery.value.trim()) {
    return `${homeMakeFormulaGroups.value.length}/${homeMakeFormulaRawGroups.value.length} 组`
  }
  return counts ? `${counts.feature_groups} 组 · ${counts.rows} 阶` : ''
})

const specialFazeGroup = computed(() => selectedSpecialFazeCatalog.value?.selected.group ?? null)
const specialFazeStages = computed(() => selectedSpecialFazeCatalog.value?.selected.stages ?? [])
const specialFazeEffectTypes = computed(() => selectedSpecialFazeCatalog.value?.selected.effect_types ?? [])
const specialFazeReasons = computed(() => selectedSpecialFazeCatalog.value?.selected.reasons ?? [])
const specialFazeCountText = computed(() => {
  const group = specialFazeGroup.value
  if (!group) return ''
  return `${group.stage_count} 阶 · ${group.faze_count} 法则 · ${group.effect_types || '-'}`
})

const homeMakeBuffOverviewRawGroups = computed(() => homeMakeBuffOverview.value?.items ?? [])

const homeMakeBuffOverviewGroups = computed(() => {
  const text = normalizeSearchQuery(homeMakeBuffOverviewQuery.value).toLowerCase()
  if (!text) return homeMakeBuffOverviewRawGroups.value
  const tokens = text.split(' ').filter(Boolean)
  return homeMakeBuffOverviewRawGroups.value.filter(group => {
    const haystack = getHomeMakeBuffSearchText(group).toLowerCase()
    return tokens.every(token => haystack.includes(token))
  })
})

const homeMakeBuffOverviewCountText = computed(() => {
  const counts = homeMakeBuffOverview.value?.counts
  if (!counts) return ''
  if (homeMakeBuffOverviewQuery.value.trim()) {
    return `${homeMakeBuffOverviewGroups.value.length}/${homeMakeBuffOverviewRawGroups.value.length} 组`
  }
  return `${counts.groups} 组 · ${counts.candidate_rows} 条 · ${counts.unique_buff_ids} Buff`
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

const activityKindFacetOptions = computed(() => {
  return withDynamicFacetCounts(
    activityKindOptions.value,
    activityFacetIndex.value,
    'kind_key',
    activityFacetFilters.value,
  )
})

const activityTimeFacetOptions = computed(() => {
  return withDynamicFacetCounts(
    activityTimeOptions.value,
    activityFacetIndex.value,
    'time_kind',
    activityFacetFilters.value,
  )
})

const activityTypeFacetOptions = computed(() => {
  return withDynamicFacetCounts(
    activityTypeOptions.value,
    activityFacetIndex.value,
    'activity_type',
    activityFacetFilters.value,
  )
})

const protocolFeatures = computed(() => {
  return protocolResponse.value?.available_features?.length
    ? protocolResponse.value.available_features
    : DEFAULT_PROTOCOL_FEATURES
})

const protocolRows = computed(() => protocolResponse.value?.items ?? [])
const protocolEdges = computed(() => protocolResponse.value?.edges ?? [])
const protocolCounts = computed(() => protocolResponse.value?.counts ?? null)
const protocolBusinessCategoryCount = computed(() => protocolBusinessCategories.value.reduce((sum, item) => sum + item.count, 0))
const isPacketWikiInitialLoading = computed(() => (
  activeTab.value === 'packet'
  && loadingList.value
  && !protocolBusinessCategories.value.length
))
const selectedPacketCategoryRow = computed(() => (
  protocolBusinessCategories.value.find(item => item.category === selectedPacketCategory.value)
    ?? protocolBusinessCategories.value[0]
    ?? null
))

const selectedProtocolRow = computed(() => {
  return protocolRows.value.find(item => item.packet === selectedId.value) ?? protocolRows.value[0] ?? null
})

const selectedProtocolEdges = computed(() => {
  const row = selectedProtocolRow.value
  if (!row) return protocolEdges.value
  const packet = row.packet
  const operation = row.operation
  return protocolEdges.value.filter(edge => {
    return edge.source === packet
      || edge.target === packet
      || (!!operation && (edge.source === operation || edge.evidence === operation))
  })
})

const protocolRoleFacetOptions = computed(() => {
  const stats = protocolCounts.value?.by_role ?? {}
  return Object.entries(stats).map(([value, count]) => ({ value, label: value, count }))
})

const protocolOperationFacetOptions = computed(() => {
  const stats = protocolCounts.value?.by_operation ?? {}
  return Object.entries(stats).map(([value, count]) => ({ value, label: value, count }))
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
  FanxiuActivitySearchItem |
  FanxiuActivityCard |
  FanxiuLingjieFeatureSearchItem |
  FanxiuLingjieFeatureCard |
  FanxiuDigitDoorCharacterSearchItem |
  FanxiuDigitDoorCharacterCard |
  FanxiuDigitDoorLevelSearchItem |
  FanxiuDigitDoorLevelConfig |
  FanxiuDoupoTDPartnerSearchItem |
  FanxiuDoupoTDPartnerCard |
  FanxiuDoupoTDRewardConfigSearchItem

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

function getVisualAssetKey(item: FanxiuStaticVisualManifestRow | null | undefined) {
  return String(item?.media_path || item?.absolute_media_path || item?.name || '')
}

function getStaticAssetKey(item: FanxiuStaticAssetManifestRow | null | undefined) {
  return String(item?.asset_id || item?.relative_path || item?.name || '')
}

function getAudioAssetKey(item: FanxiuWwiseMp3ManifestRow | null | undefined) {
  return String(item?.relative_mp3_path || `${item?.source_bank || ''}:${item?.wem_id || ''}:${item?.entry_index || ''}`)
}

function getVisualCategoryLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    ability_icon: '能力',
    apk_image: 'APK图片',
    apk_icon: 'APK图标',
    apk_logo: 'APK Logo',
    apk_splash: 'APK启动图',
    buff_icon: 'Buff',
    fashion_icon: '外观',
    head_portrait: '头像',
    item_or_ui_icon: '图标',
    logo: 'Logo',
    sdk_ui: 'SDK',
    skill_icon: '技能',
    sprite: '切片',
    taptap_ui: 'TapTap',
    title_label: '标题',
  }
  const key = String(value || '').trim()
  return labels[key] || key || '图像'
}

function getVisualAssetGroupLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    apk: 'APK',
    icon: '图标',
    image: '大图',
    sprite: '切片',
    text: '标题',
  }
  const key = String(value || '').trim()
  return labels[key] || key || '图片'
}

function getVisualSourceKindLabel(value: string | null | undefined) {
  const key = String(value || '').trim()
  if (key === 'atlas_sprite') return 'Atlas'
  if (key === 'apk_image') return 'APK'
  return key || '资源'
}

function getVisualAssetMeta(item: FanxiuStaticVisualManifestRow | null | undefined) {
  if (!item) return ''
  return [
    getVisualAssetGroupLabel(item.asset_group),
    getVisualCategoryLabel(item.category),
    getVisualSourceKindLabel(item.source_kind),
    item.atlas_key,
    item.width && item.height ? `${item.width}x${item.height}` : '',
  ].filter(Boolean).join(' · ')
}

function getVisualAssetPreview(item: FanxiuStaticVisualManifestRow | null | undefined) {
  return compactText(item?.source_path || item?.media_path || '', 108)
}

function getVisualSimilarityLabel(item: FanxiuStaticVisualManifestRow | null | undefined) {
  const value = Number(item?.similarity_percent)
  return Number.isFinite(value) ? `${value.toFixed(value >= 99.95 ? 0 : 1)}%` : ''
}

function getVisualSimilarityMeta(item: FanxiuStaticVisualManifestRow | null | undefined) {
  if (!item || item.similarity_percent === undefined) return getVisualAssetMeta(item)
  const distances = [
    item.phash_distance !== undefined ? `p${item.phash_distance}` : '',
    item.dhash_distance !== undefined && item.dhash_distance !== '' ? `d${item.dhash_distance}` : '',
  ].filter(Boolean).join('/')
  return [getVisualAssetMeta(item), getVisualSimilarityLabel(item), distances].filter(Boolean).join(' · ')
}

function getStaticAssetGroupLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    animation: '动画',
    activity: '活动',
    activity_gift: '活动礼包',
    buff: 'Buff',
    effect: '特效',
    function: '功能',
    gongfa_skill: '功法技能',
    item: '道具',
    model: '模型',
    monster: '怪物',
    scene: '场景',
    skill: '技能',
    ui: 'UI',
  }
  const key = String(value || '').trim()
  return labels[key] || key || '素材'
}

function getStaticAssetSourceLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    animationclip: 'AnimationClip',
    animatorcontroller: 'AnimatorController',
    effect: 'Effect',
    model: 'Model',
    playable: 'Playable',
    scenepart: 'ScenePart',
    uieffect: 'UIEffect',
    ui: 'UI',
    wholescene: 'WholeScene',
  }
  const key = String(value || '').trim()
  return labels[key] || key || '资源'
}

function getStaticAssetVisibleTypeLabel(item: FanxiuStaticAssetManifestRow | null | undefined) {
  if (!item) return ''
  const labels: Record<string, string> = {
    animation_clip: 'AnimationClip',
    animator_controller: 'AnimatorController',
    asset_bundle: 'AssetBundle',
    mesh_model: 'Mesh模型',
    particle_effect: '粒子特效',
    scene_prefab: '场景Prefab',
    script_config: '脚本配置',
    semantic_activity: '活动语义',
    semantic_activity_gift: '礼包语义',
    semantic_buff: 'Buff语义',
    semantic_function: '功能语义',
    semantic_gongfa_skill: '功法技能语义',
    semantic_item: '道具语义',
    semantic_model: '模型语义',
    semantic_monster: '怪物语义',
    semantic_skill: '技能语义',
    skinned_mesh: '骨骼模型',
    timeline_config: 'Timeline配置',
    ui_prefab: 'UI Prefab',
    unity_asset: 'Unity资源',
  }
  const key = String(item.visible_data_type || '').trim()
  return labels[key] || item.unity_primary_type || key
}

function getStaticAssetMeta(item: FanxiuStaticAssetManifestRow | null | undefined) {
  if (!item) return ''
  if (item.semantic_id) {
    return [
      getStaticAssetGroupLabel(item.semantic_group || item.asset_group),
      item.semantic_visual_count ? `${item.semantic_visual_count} 张图` : '',
      item.semantic_variant_count && item.semantic_variant_count > 1 ? `${item.semantic_variant_count} 档` : '',
      item.linked_asset_count ? `${item.linked_asset_count} 个资源` : '',
      item.linked_asset_groups,
    ].filter(Boolean).join(' · ')
  }
  return [
    getStaticAssetVisibleTypeLabel(item),
    getStaticAssetGroupLabel(item.asset_group),
    getStaticAssetSourceLabel(item.source_kind),
    item.category,
    formatByteSize(item.bytes),
  ].filter(Boolean).join(' · ')
}

function getStaticAssetPreview(item: FanxiuStaticAssetManifestRow | null | undefined) {
  if (!item) return ''
  if (item.semantic_id) {
    return compactText(item.semantic_summary || item.semantic_visual_names || item.semantic_refs || item.linked_asset_names || '', 116)
  }
  const details = [
    item.unity_object_types ? `对象 ${item.unity_object_types}` : '',
    item.unity_named_objects ? item.unity_named_objects : '',
    item.mesh_count ? `Mesh ${item.mesh_count}` : '',
    item.material_count ? `材质 ${item.material_count}` : '',
    item.texture_count ? `贴图 ${item.texture_count}` : '',
    item.animation_count ? `动画 ${item.animation_count}` : '',
    item.ui_gameobject_count ? `UI节点 ${item.ui_gameobject_count}` : '',
  ].filter(Boolean).join(' · ')
  return compactText(details || item.relative_path || '', 116)
}

function getStaticAssetPreviewItemTitle(item: FanxiuStaticAssetPreviewItem) {
  return String(item.name || item.media_path || 'image')
}

function getStaticAssetPreviewItemMeta(item: FanxiuStaticAssetPreviewItem) {
  return [
    item.object_type,
    item.width && item.height ? `${item.width}x${item.height}` : '',
    item.path_id ? `PathID ${item.path_id}` : '',
  ].filter(Boolean).join(' · ')
}

function getStaticAssetDerivedPreviewNote(item: FanxiuStaticAssetPreviewItem) {
  if (item.kind === 'layout_svg') return '未提取到 Texture2D/Sprite 原图；这里是基于 RectTransform 的静态结构示意，不是原始截图。'
  if (item.kind === 'summary_svg') return '未提取到可直接显示的原始图片；这里是 Unity 对象摘要示意。'
  if (item.kind === 'error_svg') return '预览解析失败；这里显示错误摘要。'
  return '该预览不是原始图片。'
}

function formatByteSize(value: string | number | null | undefined) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function formatAudioDuration(value: string | number | null | undefined) {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds < 0) return ''
  const minutes = Math.floor(seconds / 60)
  const rest = seconds - minutes * 60
  if (minutes <= 0) return `${rest.toFixed(rest >= 10 ? 1 : 2)}s`
  return `${minutes}:${String(Math.floor(rest)).padStart(2, '0')}`
}

function getAudioAssetTitle(item: FanxiuWwiseMp3ManifestRow | null | undefined) {
  if (!item) return ''
  const bank = String(item.source_bank || '').split(/[\\/]/).pop()?.replace(/\.bnk$/i, '') || 'audio'
  return `${bank} / ${item.wem_id || item.entry_index || 'wem'}`
}

function getAudioAssetMeta(item: FanxiuWwiseMp3ManifestRow | null | undefined) {
  if (!item) return ''
  return [
    item.kind || 'audio',
    formatAudioDuration(item.duration_seconds),
    item.sample_rate ? `${item.sample_rate}Hz` : '',
    item.channels ? `${item.channels}ch` : '',
  ].filter(Boolean).join(' · ')
}

function getAudioAssetPreview(item: FanxiuWwiseMp3ManifestRow | null | undefined) {
  if (!item) return ''
  const size = formatByteSize(item.wem_size)
  return [item.source_bank, item.encoding, size].filter(Boolean).join(' · ')
}

function getAudioIndependentPlayerUrl(item: FanxiuWwiseMp3ManifestRow | null | undefined) {
  const rawUrl = item?.player_url
  if (!rawUrl || typeof window === 'undefined') return ''
  try {
    const url = new URL(rawUrl, window.location.origin)
    if (url.pathname.startsWith('/api/') && url.port === '5173') {
      url.port = '8000'
    }
    return url.href
  } catch {
    return String(rawUrl || '')
  }
}

function getAudioIndependentMediaUrl(item: FanxiuWwiseMp3ManifestRow | null | undefined) {
  const rawUrl = item?.media_url
  if (!rawUrl || typeof window === 'undefined') return ''
  try {
    const url = new URL(rawUrl, window.location.origin)
    if (url.pathname.startsWith('/api/') && url.port === '5173') {
      url.port = '8000'
    }
    return url.href
  } catch {
    return String(rawUrl || '')
  }
}

function escapeStandalonePlayerText(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char] || char))
}

function buildStandaloneAudioPlayerHtml(item: FanxiuWwiseMp3ManifestRow, mediaUrl: string) {
  const title = getAudioAssetTitle(item)
  const subtitle = getAudioAssetMeta(item)
  const path = item.relative_mp3_path || item.source_bank || ''
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeStandalonePlayerText(title)}</title>
  <style>
    :root { color-scheme: dark; --fg: #f6f7fb; --muted: #9aa4b2; --line: #2a3240; --accent: #27c2d4; }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh; margin: 0; display: grid; place-items: center; padding: 36px;
      color: var(--fg); font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #050608;
    }
    main { width: min(1040px, 92vw); display: grid; gap: 16px; }
    h1 { margin: 0; overflow-wrap: anywhere; font-size: clamp(22px, 4vw, 42px); line-height: 1.15; letter-spacing: 0; }
    .meta, .path { overflow-wrap: anywhere; color: var(--muted); font-size: 14px; line-height: 1.5; }
    .player {
      display: grid; grid-template-columns: auto 1fr auto; gap: 18px; align-items: center;
      padding: 24px 28px; background: #111722; border: 1px solid var(--line); border-radius: 8px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.34);
    }
    button {
      width: 72px; height: 72px; border: 0; border-radius: 50%; color: #06262c;
      font-size: 18px; font-weight: 800; background: var(--accent); cursor: pointer;
    }
    .timeline { min-width: 0; display: grid; gap: 12px; }
    .time-row { display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-variant-numeric: tabular-nums; font-size: 18px; }
    input[type="range"] { width: 100%; height: 34px; margin: 0; accent-color: var(--accent); cursor: pointer; }
    .volume { width: 128px; display: grid; gap: 8px; color: var(--muted); font-size: 13px; }
    .volume input[type="range"] { height: 24px; }
    @media (max-width: 720px) { body { padding: 18px; } .player { grid-template-columns: 1fr; } button { width: 64px; height: 64px; } .volume { width: 100%; } }
  </style>
</head>
<body>
  <main>
    <h1>${escapeStandalonePlayerText(title)}</h1>
    <div class="meta">${escapeStandalonePlayerText(subtitle)}</div>
    <div class="path">${escapeStandalonePlayerText(path)}</div>
    <section class="player">
      <button id="toggle" type="button">播放</button>
      <div class="timeline">
        <div class="time-row"><span id="current">0:00</span><span id="duration">0:00</span></div>
        <input id="progress" type="range" min="0" max="1000" value="0" step="1" aria-label="播放进度">
      </div>
      <label class="volume">音量<input id="volume" type="range" min="0" max="1" value="1" step="0.01" aria-label="音量"></label>
      <audio id="audio" preload="metadata" src="${escapeStandalonePlayerText(mediaUrl)}"></audio>
    </section>
  </main>
  <script>
    const audio = document.getElementById('audio');
    const toggle = document.getElementById('toggle');
    const progress = document.getElementById('progress');
    const current = document.getElementById('current');
    const duration = document.getElementById('duration');
    const volume = document.getElementById('volume');
    function fmt(value) {
      if (!Number.isFinite(value) || value < 0) return '0:00';
      const minutes = Math.floor(value / 60);
      const seconds = Math.floor(value % 60);
      return minutes + ':' + String(seconds).padStart(2, '0');
    }
    function sync() {
      current.textContent = fmt(audio.currentTime);
      duration.textContent = fmt(audio.duration);
      progress.value = Number.isFinite(audio.duration) && audio.duration > 0 ? String(Math.round(audio.currentTime / audio.duration * 1000)) : '0';
      toggle.textContent = audio.paused ? '播放' : '暂停';
    }
    toggle.addEventListener('click', () => { if (audio.paused) audio.play(); else audio.pause(); });
    progress.addEventListener('input', () => { if (Number.isFinite(audio.duration) && audio.duration > 0) audio.currentTime = Number(progress.value) / 1000 * audio.duration; });
    volume.addEventListener('input', () => { audio.volume = Number(volume.value); });
    window.addEventListener('keydown', event => { if (event.code !== 'Space') return; event.preventDefault(); toggle.click(); });
    audio.addEventListener('loadedmetadata', sync);
    audio.addEventListener('timeupdate', sync);
    audio.addEventListener('play', sync);
    audio.addEventListener('pause', sync);
    audio.addEventListener('ended', sync);
    sync();
  <\/script>
</body>
</html>`
}

function openAudioIndependentPlayer(item: FanxiuWwiseMp3ManifestRow | null | undefined) {
  if (!item || typeof window === 'undefined') return
  const playerHref = getAudioIndependentPlayerUrl(item)
  if (playerHref) {
    window.open(playerHref, '_blank', 'noopener')
    return
  }
  const mediaUrl = getAudioIndependentMediaUrl(item)
  if (!mediaUrl) return
  const html = buildStandaloneAudioPlayerHtml(item, mediaUrl)
  const blobUrl = URL.createObjectURL(new Blob([html], { type: 'text/html' }))
  window.open(blobUrl, '_blank', 'noopener')
}

function getLinkedItemId(item: WikiLinkedItem | null | undefined) {
  const id = String(item?.id ?? '').trim()
  return id
}

function cleanWikiLinkAlias(value: unknown) {
  return String(value ?? '').replace(/\s+/g, ' ').trim()
}

const cleanWikiLinkPreview = cleanFanxiuPreview
const cleanWikiLinkDisplayText = cleanFanxiuDisplayText
const sameWikiLinkPreview = sameFanxiuPreview
const buildWikiLinkRewardPreview = buildFanxiuRewardPreview

function getGongfaCardLinkPreview(card: FanxiuGongfaCard | null | undefined) {
  if (!card) return ''
  const description = cleanWikiLinkDisplayText(card.description_rich || card.description)
  if (description) return description
  const skill = card.skills?.find(item => item.describe_rich || item.describe || item.effect_describe_rich || item.effect_describe || item.additional_describe_rich || item.additional_describe)
  return cleanWikiLinkDisplayText(skill?.describe_rich || skill?.describe || skill?.effect_describe_rich || skill?.effect_describe || skill?.additional_describe_rich || skill?.additional_describe)
}

function getGongfaCardLinkEffectTextPreview(card: FanxiuGongfaCard | null | undefined) {
  if (!card) return ''
  const skill = card.skills?.find(item => item.describe_rich || item.describe || item.effect_describe_rich || item.effect_describe || item.additional_describe_rich || item.additional_describe)
  const effect = cleanWikiLinkDisplayText(skill?.describe_rich || skill?.describe || skill?.effect_describe_rich || skill?.effect_describe || skill?.additional_describe_rich || skill?.additional_describe)
  return sameWikiLinkPreview(effect, card.description_rich || card.description) ? '' : effect
}

function getItemCardLinkPreview(card: FanxiuItemCard | null | undefined) {
  return cleanWikiLinkDisplayText(card?.description || card?.effect_description)
}

function getItemCardLinkEffectTextPreview(card: FanxiuItemCard | null | undefined) {
  const effect = cleanWikiLinkDisplayText(card?.effect_description)
  return sameWikiLinkPreview(effect, card?.description) ? '' : effect
}

function getItemCardLinkEffectPreview(card: FanxiuItemCard | null | undefined) {
  return cleanWikiLinkPreview(card?.show_effect)
}

function getLinkedItemLinkPreview(item: WikiLinkedItem | null | undefined) {
  return cleanWikiLinkDisplayText(item?.description)
}

function addWikiLinkTarget(
  targets: WikiLinkTarget[],
  seen: Set<string>,
  target: Omit<WikiLinkTarget, 'alias'> & { alias: unknown },
) {
  const alias = cleanWikiLinkAlias(target.alias)
  const id = String(target.id ?? '').trim()
  if (alias.length < 2 || WIKI_LINK_ALIAS_BLACKLIST.has(alias) || !id) return
  const key = `${alias}|${target.tab}|${id}`
  if (seen.has(key)) return
  seen.add(key)
  targets.push({ ...target, alias, id })
}

function addLinkedItemTarget(targets: WikiLinkTarget[], seen: Set<string>, item: WikiLinkedItem | null | undefined) {
  const id = getLinkedItemId(item)
  if (!id) return
  addWikiLinkTarget(targets, seen, {
    alias: item?.name,
    tab: 'item',
    id,
    title: item?.name,
    preview: getLinkedItemLinkPreview(item),
    kind: 'linked_item',
    priority: 120,
  })
}

const wikiLinkTargets = computed(() => {
  const targets: WikiLinkTarget[] = []
  const seen = new Set<string>()
  for (const item of wikiLinkIndexItems.value) {
    addWikiLinkTarget(targets, seen, {
      alias: item.alias,
      tab: item.tab,
      id: item.id,
      title: item.title,
      preview: item.preview,
      effect_text_preview: item.effect_text_preview,
      effect_preview: item.effect_preview,
      reward_preview: item.reward_preview,
      kind: item.kind,
      priority: item.priority ?? 0,
    })
  }

  if (selectedCard.value) {
    addWikiLinkTarget(targets, seen, {
      alias: selectedCard.value.name,
      tab: 'gongfa',
      id: selectedCard.value.id,
      title: selectedCard.value.name,
      preview: getGongfaCardLinkPreview(selectedCard.value),
      effect_text_preview: getGongfaCardLinkEffectTextPreview(selectedCard.value),
      kind: 'current_gongfa',
      priority: 130,
    })
    for (const prefix of [selectedCard.value.quality_family_name, selectedCard.value.quality_grade_name]) {
      const prefixText = cleanWikiLinkAlias(prefix)
      const name = cleanWikiLinkAlias(selectedCard.value.name)
      if (prefixText && name && !name.startsWith(`${prefixText}·`)) {
        addWikiLinkTarget(targets, seen, {
          alias: `${prefixText}·${name}`,
          tab: 'gongfa',
          id: selectedCard.value.id,
          title: selectedCard.value.name,
          preview: getGongfaCardLinkPreview(selectedCard.value),
          effect_text_preview: getGongfaCardLinkEffectTextPreview(selectedCard.value),
          kind: 'current_gongfa_alias',
          priority: 140,
        })
      }
    }
    for (const item of [...(selectedCard.value.consume_items ?? []), ...(selectedCard.value.show_condition_items ?? [])]) {
      addLinkedItemTarget(targets, seen, item)
    }
    for (const rows of Object.values(selectedCard.value.progression ?? {})) {
      for (const row of rows ?? []) {
        for (const item of row.consume_items ?? []) addLinkedItemTarget(targets, seen, item)
        const fazeResource = row.faze_resource
        if (fazeResource) {
          for (const alias of [fazeResource.name, fazeResource.head_name]) {
            addWikiLinkTarget(targets, seen, {
              alias,
              tab: 'gongfa',
              id: selectedCard.value.id,
              title: selectedCard.value.name,
              preview: cleanWikiLinkDisplayText(fazeResource.tip_str) || getGongfaCardLinkPreview(selectedCard.value),
              kind: 'current_faze_resource',
              priority: 150,
            })
          }
        }
      }
    }
  }

  if (selectedItem.value) {
    addWikiLinkTarget(targets, seen, {
      alias: selectedItem.value.name,
      tab: 'item',
      id: selectedItem.value.id,
      title: selectedItem.value.name,
      preview: getItemCardLinkPreview(selectedItem.value),
      effect_text_preview: getItemCardLinkEffectTextPreview(selectedItem.value),
      effect_preview: getItemCardLinkEffectPreview(selectedItem.value),
      reward_preview: buildWikiLinkRewardPreview(selectedItem.value.optional_gift_rewards),
      kind: 'current_item',
      priority: 130,
    })
    for (const item of selectedItem.value.optional_gift_rewards ?? []) addLinkedItemTarget(targets, seen, item)
    for (const rows of Object.values(selectedItem.value.progression ?? {})) {
      for (const row of rows ?? []) {
        for (const item of row.consume_items ?? []) addLinkedItemTarget(targets, seen, item)
      }
    }
  }

  if (selectedActivity.value) {
    for (const item of getActivityLinkedItems(selectedActivity.value)) addLinkedItemTarget(targets, seen, item)
  }

  if (selectedLingjieCard.value) {
    addWikiLinkTarget(targets, seen, {
      alias: selectedLingjieCard.value.name,
      tab: 'lingjie',
      id: selectedLingjieCard.value.gongfa_id,
      title: selectedLingjieCard.value.name,
      preview: cleanWikiLinkDisplayText(selectedLingjieCard.value.description),
      kind: 'current_lingjie',
      priority: 120,
    })
    for (const item of selectedLingjieCard.value.items ?? []) addLinkedItemTarget(targets, seen, item)
  }

  return targets.sort((left, right) => {
    const lengthDelta = String(right.alias).length - String(left.alias).length
    if (lengthDelta) return lengthDelta
    const priorityDelta = Number(right.priority ?? 0) - Number(left.priority ?? 0)
    if (priorityDelta) return priorityDelta
    return String(left.alias).localeCompare(String(right.alias), 'zh-Hans-CN')
  })
})

const wikiLinkTargetsByFirstChar = computed(() => {
  return buildFanxiuLinkTargetGroups(wikiLinkTargets.value)
})

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

function getActivityKindText(item: FanxiuActivitySearchItem | FanxiuActivityCard | null | undefined) {
  return uniqueLabels(item?.kind_names ?? []).slice(0, 3).join(' · ')
}

function getActivityMeta(item: FanxiuActivitySearchItem | FanxiuActivityCard | null | undefined) {
  return [
    getActivityKindText(item),
    item?.activity_type ? `玩法 ${item.activity_type}` : '',
    item?.base_id ? `Base ${item.base_id}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorMeta(item: FanxiuDigitDoorCharacterSearchItem | FanxiuDigitDoorCharacterCard | null | undefined) {
  return [
    item?.quality_label ? `${item.quality_label}品` : '',
    item?.positioning,
    item?.skill_name ? `神通 ${item.skill_name}` : '',
    item?.door_effect_count ? `${item.door_effect_count}门` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorSkillMeta(skill: FanxiuDigitDoorSkill | null | undefined) {
  return [
    skill?.level_show ? `${skill.level_show}级` : '',
    skill?.skill_patch,
    skill?.runtime?.damage_text ? `伤害 ${skill.runtime.damage_text}` : '',
    skill?.runtime?.cd_ms ? `CD ${Number(skill.runtime.cd_ms) / 1000}s` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorLogicSkillTitle(skill: FanxiuDigitDoorLogicSkill | null | undefined, index: number) {
  return skill?.id ? `逻辑技能 ${skill.id}` : `逻辑技能 ${index + 1}`
}

function getDigitDoorLogicSkillMeta(skill: FanxiuDigitDoorLogicSkill | null | undefined) {
  return [
    skill?.skill_type ? `类型 ${skill.skill_type}` : '',
    skill?.level ? `${skill.level}级` : '',
    skill?.damage_text ? `伤害 ${skill.damage_text}` : '',
    skill?.cd_ms ? `CD ${Number(skill.cd_ms) / 1000}s` : '',
    skill?.timeline_id ? `TL ${skill.timeline_id}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorBuffTitle(buff: FanxiuDigitDoorBuffRuntime | null | undefined) {
  if (!buff) return ''
  return buff.id ? `Buff ${buff.id}` : 'Buff'
}

function getDigitDoorBuffMeta(buff: FanxiuDigitDoorBuffRuntime | null | undefined) {
  return [
    buff?.type ? `type ${buff.type}` : '',
    buff?.target_type ? `target ${buff.target_type}` : '',
    buff?.trigger_type,
    buff?.eff_type ? `eff ${buff.eff_type}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorBuffLines(buff: FanxiuDigitDoorBuffRuntime | null | undefined) {
  if (!buff) return []
  return [
    buff.damage_text ? `伤害 ${buff.damage_text}` : '',
    buff.add_attr ? `属性 ${buff.add_attr}` : '',
    buff.shield ? `护盾 ${buff.shield}` : '',
    buff.slow_down ? `减速 ${buff.slow_down}` : '',
    buff.duration !== null && buff.duration !== undefined ? `时长 ${buff.duration}` : '',
    buff.timeline_id ? `TL ${buff.timeline_id}` : '',
  ].filter(Boolean)
}

function getDigitDoorEnhanceMeta(item: FanxiuDigitDoorSkillEnhanceEffect | null | undefined) {
  return [
    item?.skill ? `技能组 ${item.skill}` : '',
    item?.skill_type ? `类型 ${item.skill_type}` : '',
    item?.buff_id ? `Buff ${item.buff_id}` : '',
    item?.mutex_timeline ? `替换TL ${item.mutex_timeline}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorEnhanceLines(item: FanxiuDigitDoorSkillEnhanceEffect | null | undefined) {
  if (!item) return []
  return [
    item.ext_release_count ? `追加释放 ${item.ext_release_count}` : '',
    item.ext_hit_num ? `追加命中 ${item.ext_hit_num}` : '',
    item.ext_penetrate ? `穿透 +${item.ext_penetrate}` : '',
    item.ext_atk_distance ? `距离 +${item.ext_atk_distance}` : '',
  ].filter(Boolean)
}

function getDigitDoorEnhanceGroupMeta(item: FanxiuDigitDoorEnhanceGroupSearchItem | FanxiuDigitDoorEnhanceGroup | null | undefined) {
  return [
    item?.char_id ? `Group ${item.char_id}` : '',
    item?.enhance_count ? `${item.enhance_count} 强化` : '',
    (item as FanxiuDigitDoorEnhanceGroupSearchItem | null | undefined)?.condition_count ? `${(item as FanxiuDigitDoorEnhanceGroupSearchItem).condition_count} 条条件` : '',
    (item as FanxiuDigitDoorEnhanceGroupSearchItem | null | undefined)?.mutex_count ? `${(item as FanxiuDigitDoorEnhanceGroupSearchItem).mutex_count} 互斥` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorEnhanceRefLabel(ref: FanxiuDigitDoorEnhanceRef | null | undefined) {
  if (!ref) return ''
  const name = String(ref.name || '').trim()
  return name ? `${name}(${ref.id ?? ''})` : String(ref.id ?? '')
}

function getDigitDoorEnhanceLevelRangeText(item: FanxiuDigitDoorEnhance | null | undefined) {
  return (item?.level_ranges ?? [])
    .map(row => `Lv${row.min_level ?? '?'}-${row.max_level ?? '?'}`)
    .filter(Boolean)
    .join(' / ')
}

function getDigitDoorEnhanceConditionText(item: FanxiuDigitDoorEnhance | null | undefined) {
  if (!item) return ''
  const parts = [
    item.prereqs?.length ? `前置 ${item.prereqs.map(getDigitDoorEnhanceRefLabel).join(' / ')}` : '',
    getDigitDoorEnhanceLevelRangeText(item) ? `等级 ${getDigitDoorEnhanceLevelRangeText(item)}` : '',
    item.mutexes?.length ? `互斥 ${item.mutexes.map(getDigitDoorEnhanceRefLabel).join(' / ')}` : '',
  ].filter(Boolean)
  return parts.join(' · ') || '无条件'
}

function getDigitDoorEnhanceTreeMeta(item: FanxiuDigitDoorEnhance | null | undefined) {
  return [
    item?.quality_label ? `${item.quality_label}品` : '',
    item?.type_label,
    item?.limit ? `上限 ${item.limit}` : '',
    item?.weight ? `权重 ${item.weight}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorEnhanceBadges(item: FanxiuDigitDoorEnhance | null | undefined) {
  if (!item) return []
  return [
    ...(item.prereqs ?? []).map(ref => `前置 ${getDigitDoorEnhanceRefLabel(ref)}`),
    ...(item.mutexes ?? []).map(ref => `互斥 ${getDigitDoorEnhanceRefLabel(ref)}`),
    getDigitDoorEnhanceLevelRangeText(item) ? `等级 ${getDigitDoorEnhanceLevelRangeText(item)}` : '',
    ...(item.unlock_show ?? []).map(ref => `后续 ${getDigitDoorEnhanceRefLabel(ref)}`),
  ].filter(Boolean)
}

function getDigitDoorDoorMeta(item: FanxiuDigitDoorDoorEffect | null | undefined) {
  return [
    item?.door_type_label,
    item?.refresh_weights ? `权重 ${item.refresh_weights}` : '',
    item?.skill_ids?.length ? `${item.skill_ids.length}技能效果` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorDoorSkillMeta(skill: FanxiuDigitDoorSkill | null | undefined) {
  return [
    skill?.skill_title_plain,
    getDigitDoorSkillMeta(skill),
  ].filter(Boolean).join(' · ')
}

function getDigitDoorLevelMilestoneTitle(item: FanxiuDigitDoorLevelMilestone | null | undefined) {
  return item?.level ? `${item.level}级` : '等级节点'
}

function getDigitDoorLevelMilestoneMeta(item: FanxiuDigitDoorLevelMilestone | null | undefined) {
  const attrs = item?.attrs ?? {}
  const attrText = Object.entries(attrs)
    .slice(0, 4)
    .map(([key, value]) => `${ATTR_LABELS[key] ?? key} ${value}`)
    .join(' / ')
  const skills = item?.default_skill?.length ? `技能 ${item.default_skill.join('/')}` : ''
  const enhances = item?.default_skill_enhance?.length ? `强化 ${item.default_skill_enhance.join('/')}` : ''
  return [attrText, skills, enhances].filter(Boolean).join(' · ')
}

function getDigitDoorLevelTitle(item: FanxiuDigitDoorLevelSearchItem | FanxiuDigitDoorLevelConfig | null | undefined) {
  if (!item) return ''
  const name = String((item as FanxiuDigitDoorLevelConfig).name_plain || item.name || '').trim()
  return name || `关卡 ${item.id ?? ''}`.trim()
}

function getDigitDoorLevelMeta(item: FanxiuDigitDoorLevelSearchItem | FanxiuDigitDoorLevelConfig | null | undefined) {
  return [
    item?.stage ? `章节 ${item.stage}` : '',
    item?.layer ? `第 ${item.layer} 关` : '',
    item?.sub_layer ? `小关 ${item.sub_layer}` : '',
    item?.door_count ? `${item.door_count} 门` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorLevelRewardTitlePlain(item: FanxiuDigitDoorLevelConfig | FanxiuDigitDoorLevelSearchItem | null | undefined) {
  return String((item as FanxiuDigitDoorLevelConfig | null | undefined)?.reward_show_title_plain || stripFanxiuRichTags(item?.reward_show_title || '')).trim()
}

function getDigitDoorLevelRewardTitleRich(item: FanxiuDigitDoorLevelConfig | null | undefined) {
  return String(item?.reward_show_title || item?.reward_show_title_plain || '').trim()
}

function getDigitDoorStageName(stage: FanxiuDigitDoorStageReward | FanxiuDigitDoorStageOption | null | undefined) {
  return String((stage as FanxiuDigitDoorStageReward | null | undefined)?.title_plain || (stage as FanxiuDigitDoorStageReward | null | undefined)?.title || stage?.name || '').trim()
}

function getDigitDoorLevelRewardText(item: FanxiuDigitDoorRewardItem | null | undefined) {
  if (!item) return ''
  if (item.text) return String(item.text)
  const name = String(item.item?.name || item.id || item.raw || '').trim()
  return item.count ? `${name}x${item.count}` : name
}

function getDigitDoorLevelRewardMeta(item: FanxiuDigitDoorRewardItem | null | undefined) {
  const extraMarkName = item?.reward_result?.extra_mark_name
  return [
    item?.item?.quality_name,
    item?.id ? `ID ${item.id}` : '',
    extraMarkName && extraMarkName !== 'RewardType.ExtraMark.Common' ? extraMarkName : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorRewardResultBadges(item: FanxiuDigitDoorRewardItem | null | undefined) {
  const result = item?.reward_result
  if (!result) return []
  const typeName = String(result.runtime_reward_type_name || '').trim()
  const typeValue = String(result.runtime_reward_type ?? '').trim()
  const code = String(result.code ?? item?.id ?? '').trim()
  const amount = String(result.amount ?? item?.count ?? '').trim()
  const extraMark = String(result.extra_mark ?? item?.extra_mark ?? '0').trim()
  return [
    typeName ? `${typeName}${typeValue ? `(${typeValue})` : ''}` : '',
    code ? `code ${code}` : '',
    amount ? `amount ${amount}` : '',
    extraMark ? `extraMark ${extraMark}` : '',
  ].filter(Boolean)
}

function getDigitDoorRewardResultNote(item: FanxiuDigitDoorRewardItem | null | undefined) {
  const note = String(item?.reward_result?.note || '').trim()
  if (!note) return ''
  if (note.includes('omits extraMark')) return ''
  if (note.includes('negative amount')) return '静态预览使用负数占位，最终数量看回包'
  return note
}

function getDigitDoorLevelRawRows(item: FanxiuDigitDoorLevelConfig | null | undefined) {
  if (!item) return []
  return [
    ['配置 ID', item.id],
    ['章节', item.stage],
    ['Group', item.group],
    ['Layer', item.layer],
    ['SubLayer', item.sub_layer],
    ['InitChar', item.init_char],
    ['Scene', item.scene_id],
    ['Monster', (item.monster ?? []).join(' / ')],
    ['DoorTypes', item.door_type_counts ? Object.entries(item.door_type_counts).map(([key, count]) => `${key}:${count}`).join(' / ') : ''],
    ['FirstDoorTimes', (item.first_door_times ?? []).join(' / ')],
  ].map(([label, value]) => ({ label: String(label), value: formatRawValue(value) })).filter(row => row.value)
}

function getDigitDoorDoorRefreshChips(item: FanxiuDigitDoorLevelConfig | null | undefined) {
  const summary = item?.door_refresh?.summary
  if (!summary) return []
  const timeRange = summary.first_refresh_time && summary.last_refresh_time
    ? `${summary.first_refresh_time}-${summary.last_refresh_time} 秒`
    : ''
  return uniqueLabels([
    summary.point_count ? `${summary.point_count} 个刷门点` : '',
    timeRange,
    summary.side_counts || '',
    summary.special_rule_count ? `${summary.special_rule_count} 个特殊池字段` : '',
    summary.max_hp ? `最高血量 ${summary.max_hp}` : '',
    summary.pool_semantic_preview ? compactText(summary.pool_semantic_preview, 96) : '',
    summary.replacement_pool_preview ? compactText(summary.replacement_pool_preview, 96) : '',
    summary.effect_pool_preview ? compactText(summary.effect_pool_preview, 80) : '',
  ])
}

function getDigitDoorDoorRefreshEffectText(item: FanxiuDigitDoorDoorRefreshPoint | null | undefined) {
  if (!item) return ''
  if (item.pool_semantic_text) return item.pool_semantic_text
  if (item.effect_pool_preview) return item.effect_pool_preview
  const types = item.customized_type_values?.filter(Boolean).join(' / ')
  return types ? `customizedType ${types}` : ''
}

function getDigitDoorDoorEffectOptionLabel(item: FanxiuDigitDoorDoorEffectOption | null | undefined) {
  if (!item) return ''
  if (item.display_text) return item.display_text
  const skillCount = Number(item.skill_count || item.skill_ids?.length || 0)
  return [
    item.char_name,
    item.effect_show,
    skillCount ? `${skillCount} 技能` : '',
  ].filter(Boolean).join(' · ')
}

function hasDigitDoorEffectOptionValue(value: unknown) {
  return value !== undefined && value !== null && value !== ''
}

function getDigitDoorDoorEffectOptionWeightText(item: FanxiuDigitDoorDoorEffectOption | null | undefined) {
  if (!item) return ''
  return hasDigitDoorEffectOptionValue(item.refresh_weights)
    ? `权重 ${item.refresh_weights}`
    : '权重 未填'
}

function getDigitDoorDoorEffectOptionPutBackText(item: FanxiuDigitDoorDoorEffectOption | null | undefined) {
  if (!item || !hasDigitDoorEffectOptionValue(item.put_back)) return ''
  return `放回 ${item.put_back}`
}

function getDigitDoorDoorEffectOptionChipHint(item: FanxiuDigitDoorDoorEffectOption | null | undefined) {
  if (!item) return ''
  return [
    item.effect_hint_preview,
    getDigitDoorDoorEffectOptionWeightText(item),
    getDigitDoorDoorEffectOptionPutBackText(item),
  ].filter(Boolean).join(' · ')
}

function getDigitDoorDoorEffectOptionTitle(item: FanxiuDigitDoorDoorEffectOption | null | undefined) {
  if (!item) return ''
  return [
    getDigitDoorDoorEffectOptionLabel(item),
    item.show_tips,
    item.effect_hints?.length ? `效果：${item.effect_hints.join(' / ')}` : '',
    `权重：${hasDigitDoorEffectOptionValue(item.refresh_weights) ? item.refresh_weights : '未填'}`,
    hasDigitDoorEffectOptionValue(item.put_back) ? `放回：${item.put_back}` : '',
    item.skill_names?.length ? `技能：${item.skill_names.join(' / ')}` : '',
    item.effect_id ? `SkillRefreshEffect ${item.effect_id}` : '',
  ].filter(Boolean).join('\n')
}

function getDigitDoorDoorEffectOptionChips(item: FanxiuDigitDoorDoorRefreshPoint | null | undefined) {
  const options = item?.effect_options ?? []
  const chips = options.slice(0, 6).map(option => ({
    key: String(option.effect_id ?? option.display_text ?? option.effect_show ?? ''),
    label: compactText(getDigitDoorDoorEffectOptionLabel(option), 34),
    title: getDigitDoorDoorEffectOptionTitle(option),
    more: false,
  }))
  if (options.length > 6) {
    chips.push({
      key: `more-${item?.point_id ?? ''}`,
      label: `另 ${options.length - 6} 个候选`,
      title: (item?.effect_option_preview || '').replaceAll(' / ', '\n'),
      more: true,
    })
  }
  return chips.filter(chip => chip.label)
}

function getDigitDoorDoorSpecialEffectOptionChips(item: FanxiuDigitDoorDoorRefreshPoint | null | undefined) {
  const chips: Array<{ key: string; label: string; title: string; more: boolean }> = []
  let hiddenCount = 0
  const pushGroup = (prefix: string, options: FanxiuDigitDoorDoorEffectOption[] | undefined) => {
    const rows = options ?? []
    if (!rows.length || chips.length >= 6) {
      hiddenCount += rows.length
      return
    }
    const visibleCount = Math.min(rows.length, 2, 6 - chips.length)
    for (const option of rows.slice(0, visibleCount)) {
      const label = [prefix, getDigitDoorDoorEffectOptionLabel(option)].filter(Boolean).join(' · ')
      chips.push({
        key: `special-${prefix}-${option.effect_id ?? option.display_text ?? chips.length}`,
        label: compactText(label, 42),
        title: getDigitDoorDoorEffectOptionTitle(option),
        more: false,
      })
    }
    hiddenCount += Math.max(0, rows.length - visibleCount)
  }
  for (const rule of item?.special_rules ?? []) {
    if (rule.kind === 'debuff_pool') {
      pushGroup(rule.semantic_label || '负面门池', rule.effect_options)
    }
    for (const option of rule.options ?? []) {
      const prefix = option.rate_text
        ? `${option.semantic_label || '特殊池'} ${option.rate_text}`
        : option.semantic_label || '特殊池'
      pushGroup(prefix, option.effect_options)
    }
  }
  if (hiddenCount > 0) {
    chips.push({
      key: `special-more-${item?.point_id ?? ''}`,
      label: `另 ${hiddenCount} 个特殊候选`,
      title: '',
      more: true,
    })
  }
  return chips.filter(chip => chip.label)
}

function getDigitDoorDoorEffectPoolTitle(pool: FanxiuDigitDoorDoorEffectPool | null | undefined) {
  if (!pool) return ''
  return pool.semantic_label || (pool.customized_type ? `customizedType ${pool.customized_type}` : '门效果池')
}

function getDigitDoorDoorEffectPoolMeta(pool: FanxiuDigitDoorDoorEffectPool | null | undefined) {
  if (!pool) return []
  const sourceText = pool.source_labels?.length ? pool.source_labels.join(' / ') : ''
  const rateText = pool.rate_texts?.length ? `特殊权重 ${pool.rate_texts.join(' / ')}` : ''
  return uniqueLabels([
    pool.customized_type ? `customizedType ${pool.customized_type}` : '',
    sourceText,
    pool.point_count ? `${pool.point_count} 个刷门点` : '',
    pool.point_time_preview ? `时间 ${pool.point_time_preview}` : '',
    pool.effect_count ? `${pool.effect_count} 个候选效果` : '',
    pool.refresh_weight_summary ? `池内权重 ${pool.refresh_weight_summary}` : '',
    pool.put_back_summary ? `放回 ${pool.put_back_summary}` : '',
    rateText,
  ])
}

function getDigitDoorDoorEffectPoolChips(pool: FanxiuDigitDoorDoorEffectPool | null | undefined) {
  return (pool?.effect_options ?? []).map(option => ({
    key: String(option.effect_id ?? option.display_text ?? option.effect_show ?? ''),
    label: compactText(getDigitDoorDoorEffectOptionLabel(option), 42),
    hint: compactText(getDigitDoorDoorEffectOptionChipHint(option), 48),
    title: getDigitDoorDoorEffectOptionTitle(option),
  })).filter(chip => chip.label)
}

function getDigitDoorDoorRefreshStatsText(item: FanxiuDigitDoorDoorRefreshPoint | null | undefined) {
  if (!item) return ''
  return [
    item.hp ? `血 ${item.hp}` : '',
    item.attack ? `攻 ${item.attack}` : '',
    item.door_damage ? `门伤 ${item.door_damage}` : '',
    item.volume ? `体积 ${item.volume}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorDoorRefreshSpecialText(item: FanxiuDigitDoorDoorRefreshPoint | null | undefined) {
  if (!item) return ''
  if (item.special_rule_text) return item.special_rule_text
  if (item.replacement_pool_semantic_text && item.special_rule_projection) {
    return `${item.replacement_pool_semantic_text}；${item.special_rule_projection}`
  }
  if (item.replacement_pool_semantic_text) return item.replacement_pool_semantic_text
  if (item.special_rule_projection) return item.special_rule_projection
  if (item.debuff_door_type) return `负面门池 ${item.debuff_door_type}`
  return item.server_boundary || ''
}

function getDigitDoorMonsterRefreshChips(item: FanxiuDigitDoorLevelConfig | null | undefined) {
  const summary = item?.monster_refresh?.summary
  if (!summary) return []
  const waveRange = summary.first_wave && summary.last_wave
    ? `${summary.first_wave}-${summary.last_wave} 波`
    : summary.wave_count ? `${summary.wave_count} 波` : ''
  const declaredNames = (summary.declared_monster_names ?? []).join(' / ')
  return uniqueLabels([
    waveRange,
    summary.refresh_point_count ? `${summary.refresh_point_count} 刷新点` : '',
    summary.refresh_monster_count ? `${summary.refresh_monster_count} 种刷新怪` : '',
    summary.max_attack ? `最高攻击 ${summary.max_attack}` : '',
    summary.max_hp ? `最高血量 ${summary.max_hp}` : '',
    declaredNames ? `配置怪 ${declaredNames}` : '',
  ])
}

function getDigitDoorMonsterName(item: FanxiuDigitDoorMonsterRefreshPoint | null | undefined) {
  if (!item) return ''
  const name = String(item.monster_name || '').trim()
  if (name) return name
  return item.monster_id ? `怪物 ${item.monster_id}` : '刷新点'
}

function getDigitDoorMonsterRefreshProjectionText(item: FanxiuDigitDoorMonsterRefreshPoint | null | undefined) {
  const fieldOrder = ['refreshTotalNum', 'refreshNum', 'refreshTime', 'waveTime', 'nextWaveCondition', 'refreshType', 'refreshPos']
  const projections = item?.value_projections ?? []
  return fieldOrder
    .flatMap(field => projections.filter(row => row.field === field))
    .map(row => row.projection)
    .filter((text): text is string => Boolean(text))
    .join(' · ')
}

function getDigitDoorMonsterTimingText(item: FanxiuDigitDoorMonsterRefreshPoint | null | undefined) {
  if (!item) return ''
  const projected = getDigitDoorMonsterRefreshProjectionText(item)
  if (projected) return projected
  return [
    item.refresh_total_num ? `总数 ${item.refresh_total_num}` : '',
    item.refresh_num ? `每批 ${item.refresh_num}` : '',
    item.refresh_time ? `间隔 ${item.refresh_time}` : '',
    item.wave_time ? `波长 ${item.wave_time}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorMonsterStatsText(item: FanxiuDigitDoorMonsterRefreshPoint | null | undefined) {
  if (!item) return ''
  const projected = item.attribute_projections
    ?.map(row => row.projection)
    .filter((text): text is string => Boolean(text))
    .join(' · ')
  if (projected) return projected
  return [
    item.attack ? `攻 ${item.attack}` : '',
    item.hp ? `血 ${item.hp}` : '',
    item.atk_speed ? `速 ${item.atk_speed}` : '',
    item.critical ? `暴 ${item.critical}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorMonsterSkillText(item: FanxiuDigitDoorMonsterRefreshPoint | null | undefined) {
  if (!item) return ''
  return item.default_skill_ids ? `技能 ${item.default_skill_ids}` : ''
}

function getDigitDoorMonsterCardMeta(item: FanxiuDigitDoorMonsterRefreshMonster | null | undefined) {
  if (!item) return ''
  return [
    item.monster_id ? `ID ${item.monster_id}` : '',
    item.type ? `类型 ${item.type}` : '',
    item.speed ? `速度 ${item.speed}` : '',
    item.default_skill_count ? `${item.default_skill_count} 技能` : item.default_skill_ids ? `技能 ${item.default_skill_ids}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorMonsterSkillTitle(item: FanxiuDigitDoorMonsterSkill | null | undefined) {
  return item?.id ? `技能 ${item.id}` : '技能'
}

function getDigitDoorMonsterSkillMeta(item: FanxiuDigitDoorMonsterSkill | null | undefined) {
  if (!item) return ''
  const effectClasses = item.timeline_effect?.effect_classes?.filter(Boolean) ?? []
  const flowLabels = item.timeline_effect?.class_flows
    ?.flatMap(flow => flow.flow_labels?.filter(Boolean) ?? [])
    .filter(Boolean) ?? []
  const configFields = item.timeline_effect?.skill_data_accessors
    ?.map(accessor => accessor.config_field)
    .filter(Boolean) ?? []
  const buffLabels = item.buff_effects
    ?.map(buff => buff.buff_type_name || buff.buff_id)
    .filter(Boolean) ?? []
  const sections = item.timeline_effect?.sections?.filter(Boolean) ?? []
  return [
    item.type_name ? `${item.type_name}(${item.type ?? ''})` : item.type ? `类型 ${item.type}` : '',
    item.trigger_name ? `${item.trigger_name}(${item.trigger ?? ''})` : item.trigger ? `触发 ${item.trigger}` : '',
    item.timeline_id ? `timeline ${item.timeline_id}` : '',
    effectClasses.length ? `效果 ${effectClasses.slice(0, 4).join('/')}` : '',
    flowLabels.length ? `流程 ${Array.from(new Set(flowLabels)).slice(0, 4).join('/')}` : '',
    configFields.length ? `参数 ${Array.from(new Set(configFields)).slice(0, 5).join('/')}` : '',
    sections.length ? `阶段 ${sections.join('/')}` : '',
    item.cd ? `CD ${item.cd}` : '',
    item.damage ? `伤害 ${item.damage}` : '',
    item.distance ? `距离 ${item.distance}` : '',
    item.hp_limit ? `血线 ${item.hp_limit}` : '',
    buffLabels.length ? `Buff ${Array.from(new Set(buffLabels.map(String))).slice(0, 4).join('/')}` : item.buff_id ? `Buff ${item.buff_id}` : '',
  ].filter(Boolean).join(' · ')
}

function getDigitDoorMonsterSkillFlowHints(item: FanxiuDigitDoorMonsterSkill | null | undefined) {
  return item?.timeline_effect?.class_flows
    ?.map(flow => flow.flow_hint)
    .filter((hint): hint is string => Boolean(hint)) ?? []
}

function getDigitDoorMonsterSkillAccessorHints(item: FanxiuDigitDoorMonsterSkill | null | undefined) {
  return item?.timeline_effect?.skill_data_accessors
    ?.map(accessor => {
      if (!accessor.config_field || !accessor.accessor) return ''
      return `${accessor.accessor} -> ${accessor.config_field}${accessor.transform ? `：${accessor.transform}` : ''}`
    })
    .filter((hint): hint is string => Boolean(hint)) ?? []
}

function getDigitDoorMonsterSkillValueProjectionHints(item: FanxiuDigitDoorMonsterSkill | null | undefined) {
  return item?.value_projections
    ?.map(row => row.projection ? `${row.field || 'value'}：${row.projection}` : '')
    .filter((hint): hint is string => Boolean(hint)) ?? []
}

function getDigitDoorMonsterSkillBuffHints(item: FanxiuDigitDoorMonsterSkill | null | undefined) {
  return item?.buff_effects
    ?.map(buff => {
      if (buff.runtime_hint) return buff.runtime_hint
      const label = buff.buff_type_name || buff.buff_id
      if (!label) return ''
      return `Buff ${label}`
    })
    .filter((hint): hint is string => Boolean(hint)) ?? []
}

function getDigitDoorMonsterSkillBuffFormulaHints(item: FanxiuDigitDoorMonsterSkill | null | undefined) {
  return item?.buff_effects
    ?.flatMap(buff => buff.formula_projections
      ?.map(row => row.projection ? `${row.field || 'formula'}：${row.projection}` : '')
      .filter(Boolean) ?? [])
    .filter((hint): hint is string => Boolean(hint)) ?? []
}

function getDoupoTDMeta(item: FanxiuDoupoTDPartnerSearchItem | FanxiuDoupoTDPartnerCard | null | undefined) {
  return [
    item?.positioning,
    item?.skill_name ? `绝技 ${item.skill_name}` : '',
    item?.compose_card_count ? `${item.compose_card_count} 卡` : '',
  ].filter(Boolean).join(' · ')
}

function getDoupoTDRewardConfigKey(item: FanxiuDoupoTDRewardConfigSearchItem | null | undefined) {
  if (!item) return ''
  return `${item.source_table}:${item.config_id}`
}

function parseDoupoTDRewardConfigKey(key: string) {
  const index = key.indexOf(':')
  if (index <= 0) return { sourceTable: 'Level', configId: key }
  return { sourceTable: key.slice(0, index), configId: key.slice(index + 1) }
}

function getDoupoTDRewardSourceLabel(source: unknown) {
  const text = String(source ?? '').trim()
  if (text === 'Level') return '关卡奖励'
  if (text === 'DoupoPreLevelReward') return '章节预览'
  return text || '奖励'
}

function getDoupoTDRewardSourceShort(source: unknown) {
  const text = String(source ?? '').trim()
  if (text === 'Level') return '关'
  if (text === 'DoupoPreLevelReward') return '章'
  return Array.from(text || '奖')[0] ?? '奖'
}

function getDoupoTDRewardConfigTitle(item: FanxiuDoupoTDRewardConfigSearchItem | null | undefined) {
  if (!item) return ''
  const name = String(item.name || '').trim()
  if (name) return name
  const layer = String(item.layer ?? '').trim()
  const source = getDoupoTDRewardSourceLabel(item.source_table)
  return layer ? `${source} ${layer}` : `${source} ${item.config_id ?? ''}`.trim()
}

function getDoupoTDRewardConfigMeta(item: FanxiuDoupoTDRewardConfigSearchItem | null | undefined) {
  if (!item) return ''
  const stage = item.stage ? `章节 ${item.stage}` : ''
  const layer = item.layer ? `关卡 ${item.layer}` : ''
  const subLayer = item.sub_layer ? `小关 ${item.sub_layer}` : ''
  return [
    getDoupoTDRewardSourceLabel(item.source_table),
    stage,
    layer,
    subLayer,
    item.reward_count ? `${item.reward_count}项` : '',
  ].filter(Boolean).join(' · ')
}

function getDoupoTDRewardConfigPreview(item: FanxiuDoupoTDRewardConfigSearchItem | null | undefined) {
  return compactText(item?.reward_items || item?.reward_title || item?.raw_rewards, 118)
}

function getDoupoTDRewardItemText(item: FanxiuDoupoTDRewardConfigRewardItem | null | undefined) {
  if (!item) return ''
  if (item.text) return String(item.text)
  const name = String(item.item_name || item.item_id || item.raw || '').trim()
  return item.count ? `${name}x${item.count}` : name
}

function getDoupoTDRewardItemMeta(item: FanxiuDoupoTDRewardConfigRewardItem | null | undefined) {
  if (!item) return ''
  const extraMarkName = item.reward_result?.extra_mark_name
  return [
    item.quality_name,
    item.item_id ? `ID ${item.item_id}` : '',
    extraMarkName && extraMarkName !== 'RewardType.ExtraMark.Common' ? extraMarkName : '',
  ].filter(Boolean).join(' · ')
}

function getDoupoTDRewardResultBadges(item: FanxiuDoupoTDRewardConfigRewardItem | null | undefined) {
  const result = item?.reward_result
  if (!result) return []
  const typeName = String(result.runtime_reward_type_name || '').trim()
  const typeValue = String(result.runtime_reward_type ?? '').trim()
  const code = String(result.code ?? item?.item_id ?? '').trim()
  const amount = String(result.amount ?? item?.count ?? '').trim()
  const extraMark = String(result.extra_mark ?? item?.extra_mark ?? '0').trim()
  return [
    typeName ? `${typeName}${typeValue ? `(${typeValue})` : ''}` : '',
    code ? `code ${code}` : '',
    amount ? `amount ${amount}` : '',
    extraMark ? `extraMark ${extraMark}` : '',
  ].filter(Boolean)
}

function getDoupoTDRewardResultNote(item: FanxiuDoupoTDRewardConfigRewardItem | null | undefined) {
  const note = String(item?.reward_result?.note || '').trim()
  if (!note) return ''
  if (note.includes('omits extraMark')) return '未写 extraMark，客户端按 0 处理'
  if (note.includes('negative amount')) return '静态预览使用负数占位，最终数量看回包'
  return note
}

function getDoupoTDRewardConfigRawRows(item: FanxiuDoupoTDRewardConfigSearchItem | null | undefined) {
  if (!item) return []
  return [
    ['来源', getDoupoTDRewardSourceLabel(item.source_table)],
    ['配置 ID', item.config_id],
    ['Different', item.different],
    ['Stage', item.stage],
    ['Layer', item.layer],
    ['SubLayer', item.sub_layer],
    ['ShowPos', item.show_pos_id],
    ['标题', item.reward_title],
    ['奖励串', item.raw_rewards],
  ].map(([label, value]) => ({ label: String(label), value: formatRawValue(value) })).filter(row => row.value)
}

function getDoupoTDComposeIconUrl(card: FanxiuDoupoTDComposeCard | null | undefined) {
  return getFanxiuResourceIconUrl(card?.show_item?.icon)
}

function getDoupoTDComposeMeta(card: FanxiuDoupoTDComposeCard | null | undefined) {
  return [
    card?.quality_name,
    Number(card?.star || 0) > 0 ? `${card?.star}星` : '',
    card?.show_item?.quality_name,
  ].filter(Boolean).join(' · ')
}

function getDoupoTDAttrText(entries: FanxiuDoupoTDAttrEntry[] | null | undefined) {
  return (entries ?? []).map(item => item.text).filter(Boolean).join('\n')
}

function getDoupoTDSkillMeta(skill: FanxiuDoupoTDSkill | null | undefined) {
  return [
    skill?.skill_type ? `类型 ${skill.skill_type}` : '',
    skill?.id ? `ID ${skill.id}` : '',
  ].filter(Boolean).join(' · ')
}

function getDoupoTDLogicSkillTitle(skill: FanxiuDoupoTDLogicSkill | null | undefined, index: number) {
  return skill?.id ? `技能 ${skill.id}` : `技能 ${index + 1}`
}

function getDoupoTDLogicSkillMeta(skill: FanxiuDoupoTDLogicSkill | null | undefined) {
  return [
    skill?.skillType ? `类型 ${skill.skillType}` : '',
    skill?.level ? `${skill.level}级` : '',
    skill?.damage ? `伤害 ${skill.damage}` : '',
    skill?.cd ? `CD ${skill.cd}` : '',
  ].filter(Boolean).join(' · ')
}

function getDoupoTDRuntimeTimelineChips(skill: FanxiuDoupoTDLogicSkill | null | undefined) {
  return (skill?.runtime?.timeline_ids ?? []).map(item => `TL ${item}`)
}

const doupoTDBuffFlagLabels: Record<string, string> = {
  starts_buff: '启动',
  updates_timer: '计时',
  custom_do_buff_logic: '逻辑',
  layer_logic: '层数',
  uses_trigger_buff: '派生',
  uses_timeline: 'Timeline',
  uses_add_attr: '属性',
  uses_damage: '伤害',
  adds_runtime_buff: '加Buff',
  removes_runtime_buff: '移除',
  has_percent_trigger: '概率',
  controls_release_skill: '放技',
  controls_status: '状态',
}

const doupoTDFlowCategoryLabels: Record<string, string> = {
  add_buff: '加Buff',
  remove_buff: '移除',
  random_gate: '概率',
  skill_filter: '技能过滤',
  target_buff_check: '目标检查',
  target_selection: '目标选择',
  trigger_buff_ids: '派生',
  buff_config_lookup: '配置读取',
  layer: '叠层',
  lifetime: '生命周期',
  dispatch: '分发',
  timeline: 'Timeline',
}

function getDoupoTDBuffTitle(buff: FanxiuDoupoTDBuffRuntime | null | undefined) {
  if (!buff) return ''
  const typeName = String(buff.type_name || '').trim()
  return typeName && typeName !== 'None' ? typeName : `Buff ${buff.id ?? ''}`.trim()
}

function getDoupoTDBuffMeta(buff: FanxiuDoupoTDBuffRuntime | null | undefined) {
  return [
    buff?.buff_class,
    buff?.trigger_type,
    buff?.target_type_name,
    buff?.layer_type_name,
  ].filter(Boolean).join(' · ')
}

function getDoupoTDBuffFlagLabels(buff: FanxiuDoupoTDBuffRuntime | null | undefined) {
  return (buff?.semantic_flags ?? [])
    .map(flag => doupoTDBuffFlagLabels[flag] || flag)
    .slice(0, 4)
}

function getDoupoTDBuffExtraLines(buff: FanxiuDoupoTDBuffRuntime | null | undefined) {
  if (!buff) return []
  return [
    buff.add_attr ? `属性 ${buff.add_attr}` : '',
    buff.damage ? `伤害 ${buff.damage}` : '',
    buff.timeline_id ? `Timeline ${buff.timeline_id}` : '',
    buff.trigger_buff_ids?.length ? `派生 ${buff.trigger_buff_ids.join(' / ')}` : '',
  ].filter(Boolean)
}

function getDoupoTDBuffFlowHint(buff: FanxiuDoupoTDBuffRuntime | null | undefined) {
  return String(buff?.flow?.hint || '').trim()
}

function getDoupoTDBuffFlowChips(buff: FanxiuDoupoTDBuffRuntime | null | undefined) {
  const categories = (buff?.flow?.categories ?? [])
    .map(category => doupoTDFlowCategoryLabels[category] || category)
    .filter(Boolean)
  const stats = [
    buff?.flow?.function_count ? `${buff.flow.function_count}函数` : '',
    buff?.flow?.flow_step_count ? `${buff.flow.flow_step_count}步` : '',
  ].filter(Boolean)
  return uniqueLabels([...categories, ...stats]).slice(0, 7)
}

function getDoupoTDBuffFlowFunctions(buff: FanxiuDoupoTDBuffRuntime | null | undefined) {
  return (buff?.flow?.key_functions ?? []).filter(item => item?.name).slice(0, 4)
}

function getDoupoTDBuffFlowFunctionLabel(item: FanxiuDoupoTDBuffFlowFunction) {
  const name = String(item.name || '').trim()
  const categories = (item.categories ?? [])
    .filter(category => category !== 'entry' && category !== 'super_call' && category !== 'guard')
    .map(category => doupoTDFlowCategoryLabels[category] || category)
    .slice(0, 3)
  return categories.length ? `${name} · ${categories.join('/')}` : name
}

function getDoupoTDStrengthMeta(item: FanxiuDoupoTDSkillStrength | null | undefined) {
  return [
    item?.quality_name,
    item?.level ? `${item.level}级` : '',
    item?.unlock_description,
  ].filter(Boolean).join(' · ')
}

function getDoupoTDRewardText(reward: FanxiuDoupoTDRewardItem | null | undefined) {
  if (!reward) return ''
  if (reward.text) return reward.text
  const name = String(reward.item?.name || reward.id || '').trim()
  return reward.count ? `${name}x${reward.count}` : name
}

function getDoupoTDRewardsText(rewards: FanxiuDoupoTDRewardItem[] | null | undefined) {
  return (rewards ?? []).map(getDoupoTDRewardText).filter(Boolean).join(' / ')
}

function getDoupoTDEntryText(entry: { title?: string; chance_text?: string; weight?: string | number } | null | undefined) {
  if (!entry) return ''
  const chance = entry.chance_text ? ` ${entry.chance_text}` : ''
  return `${entry.title || '卡牌'}${chance}`.trim()
}

function getDoupoTDDrawSourceTitle(source: FanxiuDoupoTDDrawSource | null | undefined) {
  return source?.item?.name || (source?.item_id ? `抽卡道具 ${source.item_id}` : `抽卡池 ${source?.id ?? ''}`)
}

function getDoupoTDDrawSourceMeta(source: FanxiuDoupoTDDrawSource | null | undefined) {
  return [
    source?.id ? `池 ${source.id}` : '',
    source?.total_weight ? `总权重 ${source.total_weight}` : '',
    source?.rewards?.length ? `附带 ${getDoupoTDRewardsText(source.rewards)}` : '',
  ].filter(Boolean).join(' · ')
}

function getDoupoTDComposeSourceTitle(source: FanxiuDoupoTDComposeQualitySource | null | undefined) {
  return source?.quality_name || (source?.quality ? `品质 ${source.quality}` : `合成池 ${source?.id ?? ''}`)
}

function getDoupoTDComposeSourceMeta(source: FanxiuDoupoTDComposeQualitySource | null | undefined) {
  return [
    source?.id ? `池 ${source.id}` : '',
    source?.total_weight ? `总权重 ${source.total_weight}` : '',
  ].filter(Boolean).join(' · ')
}

function getDoupoTDProgressRewardTitle(item: FanxiuDoupoTDComposeProgressReward | null | undefined) {
  return item?.progress ? `${item.progress} 抽` : `进度 ${item?.id ?? ''}`
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

function getActivityTimeRows(activity: FanxiuActivityCard | null | undefined) {
  if (!activity) return []
  const parsedRows = (activity.time_fields ?? [])
    .map(field => ({
      label: String(field.label || field.field || ''),
      value: String(field.summary || field.raw || '').trim(),
      raw: String(field.raw || '').trim(),
    }))
    .filter(item => item.label && item.value)
  if (parsedRows.length) return parsedRows
  return [
    ['准备', activity.prepare_time],
    ['开始', activity.start_time],
    ['结束', activity.end_time],
    ['领奖', activity.reward_time],
    ['关闭面板', activity.close_panel_time],
  ].map(([label, value]) => ({ label: String(label), value: formatRawValue(value), raw: formatRawValue(value) })).filter(item => item.value)
}

function getActivityConditionRows(activity: FanxiuActivityCard | null | undefined) {
  if (!activity) return []
  const parsedRows = (activity.condition_fields ?? [])
    .map(field => ({
      label: String(field.label || field.field || ''),
      value: String(field.summary || field.raw || '').trim(),
      raw: String(field.raw || '').trim(),
    }))
    .filter(item => item.label && item.value)
  if (parsedRows.length) return parsedRows
  return [
    ['开启条件', activity.open_condition],
    ['参与条件', activity.join_condition],
    ['显示条件', activity.show_condition],
    ['强制隐藏', activity.force_hide_condition],
  ].map(([label, value]) => ({ label: String(label), value: formatRawValue(value), raw: formatRawValue(value) })).filter(item => item.value)
}

function getActivityDescriptionRows(activity: FanxiuActivityCard | null | undefined) {
  if (!activity) return []
  const description = String(activity.description || '').trim()
  const join = String(activity.join_condition_description || '').trim()
  return [
    { label: '简介', value: description },
    { label: '参与说明', value: join && join !== description ? join : '' },
  ].filter(item => item.value)
}

function getActivityLoopRows(activity: FanxiuActivityCard | null | undefined) {
  return (activity?.loop_entries ?? [])
    .map(entry => ({
      key: `${entry.loop_id ?? ''}-${entry.day ?? ''}-${entry.activity_id ?? ''}`,
      label: `轮换 ${entry.loop_id ?? '-'}`,
      value: `第 ${entry.day ?? '-'} 天`,
    }))
}

function getActivityJumpTargetRows(activity: FanxiuActivityCard | null | undefined) {
  const target = activity?.jump_target
  if (!target) return []
  return [
    ['入口功能', target.name],
    ['功能 ID', target.id],
    ['解锁', target.unlock],
    ['条件', target.condition],
    ['窗口', target.window_id],
    ['Lua', target.lua_path],
  ].map(([label, value]) => ({ label: String(label), value: formatRawValue(value) })).filter(item => item.value)
}

function getActivityFieldRows(activity: FanxiuActivityCard | null | undefined) {
  if (!activity) return []
  return [
    ['来源表', activity.source_table],
    ['活动 ID', activity.id],
    ['玩法', activity.activity_type],
    ['Base', activity.base_id],
    ['奖励组', activity.reward_group],
    ['父活动', activity.parent_activity_id],
    ['子类型', activity.sub_type],
    ['入口', activity.jump],
  ].map(([label, value]) => ({ label: String(label), value: formatRawValue(value) })).filter(item => item.value)
}

function displayProtocolText(value: unknown, fallback = '-') {
  const text = String(value ?? '').trim()
  return text || fallback
}

function compactProtocolFields(row: FanxiuProtocolSemanticRow | null | undefined) {
  if (!row) return ''
  return row.write_fields || row.read_fields || row.assigned_fields || row.msg_fields || ''
}

function getProtocolEdgeLabel(edge: FanxiuProtocolSemanticEdge) {
  return `${edge.source_type}:${edge.source} -> ${edge.target_type}:${edge.target}`
}

function getProtocolRowMeta(row: FanxiuProtocolSemanticRow) {
  return [row.operation, row.role, row.authority_class].filter(Boolean).join(' · ')
}

function getProtocolRowPreview(row: FanxiuProtocolSemanticRow) {
  return compactText(row.semantic_note || row.state_sinks || compactProtocolFields(row), 110)
}

function getProtocolBusinessNames(item: FanxiuTcpBusinessCategorySummary) {
  return item.protocols.slice(0, 10).join('、')
}

function packetBusinessDisplaySegments(sample: FanxiuTcpBusinessProtocolSample) {
  if (packetSampleTables(sample).length) {
    const text = String(sample.display_text || '').split('：')[0] || '解析结果'
    return [{ text, kind: 'text' }]
  }
  if (sample.display_segments?.length) return sample.display_segments
  return [{ text: sample.display_text || JSON.stringify(sample.content), kind: 'text' }]
}

function packetEntryDisplaySegments(entry: FanxiuTcpBusinessEntry) {
  if (entry.display_segments?.length) return entry.display_segments
  return [{ text: entry.display_text || JSON.stringify(entry.content), kind: 'text' }]
}

function packetEntryJson(entry: FanxiuTcpBusinessEntry) {
  return JSON.stringify(entry.content ?? {}, null, 2)
}

function formatTcpBusinessTime(value: string) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/)
  if (!match) return value || '-'
  const now = new Date()
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const time = `${match[4]}:${match[5]}:${match[6]}`
  if (year === now.getFullYear() && month === now.getMonth() + 1 && day === now.getDate()) return time
  if (year === now.getFullYear()) return `${match[2]}-${match[3]} ${time}`
  return `${match[1]}-${match[2]}-${match[3]} ${time}`
}

async function togglePacketProtocolSamples(item: FanxiuTcpBusinessProtocolSummary) {
  if (expandedPacketProtocol.value === item.name) {
    expandedPacketProtocol.value = ''
    packetProtocolSamples.value = []
    return
  }
  expandedPacketProtocol.value = item.name
  packetProtocolSamples.value = []
  packetProtocolSamplesLoading.value = true
  try {
    const response = await listFanxiuTcpBusinessEntries({
      page: 1,
      page_size: 200,
      category: item.category,
      protocol: item.name,
    })
    if (expandedPacketProtocol.value === item.name) {
      packetProtocolSamples.value = response.items ?? []
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取协议样本失败')
  } finally {
    if (expandedPacketProtocol.value === item.name) {
      packetProtocolSamplesLoading.value = false
    }
  }
}

const PACKET_TABLE_FIELD_LABELS: Record<string, string> = {
  id: 'ID',
  serverId: '区服',
  name: '名称',
  level: '等级',
  memberNum: '成员',
  leaderName: '盟主',
  leaderSex: '性别',
  leaderVipLevel: 'VIP',
  roleId: '角色ID',
  roleName: '角色名',
  clubId: '宗门ID',
  clubName: '宗门',
  score: '积分',
  rank: '排行',
  value: '值',
  amount: '数量',
  code: '编号',
  type: '类型',
  avatar: '头像',
  headFrame: '头像框',
  post: '职位',
  sex: '性别',
  lastOnlineTime: '最后在线',
  vipLevel: 'VIP',
}

const PACKET_TABLE_FIELD_ORDER = [
  'name',
  'roleName',
  'id',
  'roleId',
  'serverId',
  'clubName',
  'clubId',
  'level',
  'memberNum',
  'leaderName',
  'leaderSex',
  'leaderVipLevel',
  'score',
  'rank',
  'amount',
  'code',
  'type',
  'value',
  'avatar',
  'headFrame',
  'post',
  'sex',
  'lastOnlineTime',
  'vipLevel',
]

function isRecordValue(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function formatPacketTableValue(value: unknown, key?: string) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    if (key && /time/i.test(key) && value > 1_000_000_000_000) {
      const date = new Date(value)
      if (!isNaN(date.getTime())) {
        return date.toLocaleString('zh-CN', { hour12: false })
      }
    }
    return String(value)
  }
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return `${value.length} 项`
  if (isRecordValue(value) && typeof value.name === 'string') return value.name
  return JSON.stringify(value)
}

function translatePacketCellValue(rawText: string, fieldKey: string, fieldLabels?: Record<string, Record<string, string>>) {
  if (!fieldLabels) return null
  const labels = fieldLabels[fieldKey]
  if (!labels) return null
  const label = labels[rawText]
  if (label) return label
  if (rawText === '是') return labels['1'] || labels['true'] || null
  if (rawText === '否') return labels['0'] || labels['false'] || null
  return null
}

function packetTableTitle(path: string) {
  const parts = path.split('.').filter(part => part !== 'items')
  const key = parts[parts.length - 1] || '列表'
  const labels: Record<string, string> = {
    members: '成员列表',
    items: '列表',
    rewards: '奖励列表',
    costs: '消耗列表',
  }
  return labels[key] || key
}

function flattenPacketTableRow(row: Record<string, unknown>) {
  const output: Record<string, string> = {}
  if (isRecordValue(row._super)) {
    for (const [key, value] of Object.entries(row._super)) {
      if (key !== '_class') output[key] = formatPacketTableValue(value, key)
    }
  }
  for (const [key, value] of Object.entries(row)) {
    if (key === '_class' || key === '_super') continue
    output[key] = formatPacketTableValue(value, key)
  }
  return output
}

function findPacketTableSource(value: unknown, path = ''): { path: string; rows: Record<string, unknown>[] } | null {
  if (!isRecordValue(value)) return null
  const items = value.items
  if (Array.isArray(items) && items.some(isRecordValue)) {
    return { path: `${path}.items`.replace(/^\./, ''), rows: items.filter(isRecordValue) }
  }
  let best: { path: string; rows: Record<string, unknown>[] } | null = null
  for (const [key, child] of Object.entries(value)) {
    if (key === '_super') continue
    if (Array.isArray(child) && child.some(isRecordValue)) {
      const childPath = path ? `${path}.${key}` : key
      const rows = child.filter(isRecordValue)
      if (!best || rows.length > best.rows.length) best = { path: childPath, rows }
      continue
    }
    const found = findPacketTableSource(child, path ? `${path}.${key}` : key)
    if (found && (!best || found.rows.length > best.rows.length)) best = found
  }
  return best
}

function packetSampleTables(sample: FanxiuTcpBusinessProtocolSample | null | undefined): PacketSampleTable[] {
  const found = findPacketTableSource(sample?.content)
  if (!found) return []
  const rows = found.rows.map(flattenPacketTableRow)
  const fieldSet = new Set(rows.flatMap(row => Object.keys(row).filter(key => row[key])))
  const keys = [...fieldSet].sort((left, right) => {
    const leftIndex = PACKET_TABLE_FIELD_ORDER.indexOf(left)
    const rightIndex = PACKET_TABLE_FIELD_ORDER.indexOf(right)
    const leftOrder = leftIndex >= 0 ? leftIndex : 999
    const rightOrder = rightIndex >= 0 ? rightIndex : 999
    return leftOrder - rightOrder || left.localeCompare(right)
  }).slice(0, 10)
  if (!keys.length) return []
  return [{
    title: packetTableTitle(found.path),
    columns: keys.map(key => ({ key, label: PACKET_TABLE_FIELD_LABELS[key] || key })),
    rows,
    fieldLabels: sample?.field_labels,
  }]
}

function isPacketProtocolChecked(name: string) {
  return !hiddenPacketProtocols.value.includes(name) && isFanxiuPacketProtocolVisible(name)
}

function togglePacketProtocolVisibility(name: string, visible: boolean) {
  setFanxiuPacketProtocolVisible(name, visible)
  hiddenPacketProtocols.value = getHiddenFanxiuPacketProtocols()
}

function sortPacketProtocolsByVisibility(items: FanxiuTcpBusinessProtocolSummary[]) {
  const hidden = new Set(getHiddenFanxiuPacketProtocols())
  return [...items].sort((left, right) => Number(hidden.has(left.name)) - Number(hidden.has(right.name)))
}

function packetDirectionLabel(value: string) {
  return value === 'c2s' ? '上行' : '下行'
}

function packetProtocolSampleJson(sample: FanxiuTcpBusinessProtocolSample | null | undefined) {
  if (!sample) return ''
  return JSON.stringify(sample.content ?? {}, null, 2)
}

function getActivityRewardRowKey(section: FanxiuActivityRewardSection, row: FanxiuActivityRewardRow, index: number) {
  return `${section.key}-${row.source || ''}-${row.row_key || ''}-${index}`
}

function getActivityRewardRowTitle(row: FanxiuActivityRewardRow, index: number) {
  return String(row.title || row.source || `奖励 ${index + 1}`)
}

function getActivityRewardRowMeta(row: FanxiuActivityRewardRow) {
  return [row.meta, row.condition ? `条件 ${row.condition}` : ''].filter(Boolean).join(' / ')
}

function getActivityRawRewardText(row: FanxiuActivityRewardRow) {
  return (row.raw_rewards ?? []).filter(Boolean).join('；')
}

function getActivityLinkedItems(activity: FanxiuActivityCard | null | undefined) {
  return (activity?.reward_sections ?? []).flatMap(section => (
    section.rows ?? []
  ).flatMap(row => row.reward_items ?? []))
}

function uniqueLabels(values: Array<unknown>) {
  return Array.from(new Set(
    values
      .map(value => String(value ?? '').trim())
      .filter(Boolean),
  ))
}

function splitPipeText(value: unknown) {
  return String(value ?? '')
    .split('|')
    .map(item => item.trim())
    .filter(Boolean)
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

function stripFanxiuRichTags(value: string) {
  return String(value || '')
    .replace(/<color=#[0-9a-fA-F]{3,8}>/gi, '')
    .replace(/<\/color>/gi, '')
    .replace(/<size=[0-9]{1,3}>/gi, '')
    .replace(/<\/size>/gi, '')
    .trim()
}

function getSectionTitleKey(value: string) {
  return stripFanxiuRichTags(value).replace(/\s+/g, '')
}

function isProgressionSectionTitleLine(value: string, fallbackTitleKeys: Set<string>) {
  const text = stripFanxiuRichTags(value)
  if (!text) return false
  const key = getSectionTitleKey(text)
  if (fallbackTitleKeys.has(key)) return true
  return /^[一二三四五六七八九十百千万0-9]+[阶重星级]效果[:：]/.test(text)
    || /^【[^】]{1,30}】$/.test(text)
}

function trimTrailingBlankSectionLines(section: FanxiuGongfaProgressionSection) {
  while (section.lines?.length && !section.lines[section.lines.length - 1]?.trim()) {
    section.lines.pop()
  }
  while (section.rich_lines?.length && !section.rich_lines[section.rich_lines.length - 1]?.trim()) {
    section.rich_lines.pop()
  }
}

function splitProgressionSectionsFromText(
  plainValue: string | undefined,
  richValue: string | undefined,
  fallbackSections: FanxiuGongfaProgressionSection[] | undefined,
) {
  const richText = String(richValue || plainValue || '').trim()
  const plainText = String(plainValue || richValue || '').trim()
  if (!richText && !plainText) return []

  const fallbackTitleKeys = new Set(
    (fallbackSections ?? [])
      .map(section => getProgressionSectionTitle(section))
      .map(getSectionTitleKey)
      .filter(Boolean),
  )
  const richLines = (richText || plainText).replace(/\r\n/g, '\n').split('\n')
  const plainLines = (plainText || richText).replace(/\r\n/g, '\n').split('\n')
  const sections: FanxiuGongfaProgressionSection[] = []
  let current: FanxiuGongfaProgressionSection | null = null

  const flush = () => {
    if (!current) return
    trimTrailingBlankSectionLines(current)
    if (getProgressionSectionTitle(current) || getProgressionSectionLines(current).length) {
      sections.push(current)
    }
    current = null
  }

  richLines.forEach((rawRichLine, index) => {
    const rawPlainLine = plainLines[index] ?? stripFanxiuRichTags(rawRichLine)
    const richLine = String(rawRichLine || '').trim()
    const plainLine = String(rawPlainLine || '').trim()
    const titleCandidate = richLine || plainLine

    if (isProgressionSectionTitleLine(titleCandidate, fallbackTitleKeys)) {
      flush()
      current = {
        title: plainLine,
        title_rich: richLine,
        lines: [],
        rich_lines: [],
      }
      return
    }

    if (!current) {
      current = { title: '', title_rich: '', lines: [], rich_lines: [] }
    }

    if (!plainLine && !richLine) {
      if (current.lines?.length || current.rich_lines?.length) {
        const previousLine = current.rich_lines?.[current.rich_lines.length - 1] ?? current.lines?.[current.lines.length - 1] ?? ''
        if (previousLine.trim()) {
          current.lines?.push('')
          current.rich_lines?.push('')
        }
      }
      return
    }

    current.lines?.push(plainLine)
    current.rich_lines?.push(richLine)
  })
  flush()

  const hasStructuredShape = Boolean(fallbackSections?.length) || sections.some(section => getProgressionSectionTitle(section))
  return hasStructuredShape ? sections : []
}

function getSkillSections(skill: FanxiuGongfaSkill | null | undefined) {
  if (!skill) return []
  if ((skill.describe_rich || skill.describe) && skill.describe_sections?.length) {
    const sections = splitProgressionSectionsFromText(skill.describe, skill.describe_rich, skill.describe_sections)
    return sections.length ? sections : skill.describe_sections
  }
  if ((skill.effect_describe_rich || skill.effect_describe) && skill.effect_describe_sections?.length) {
    const sections = splitProgressionSectionsFromText(skill.effect_describe, skill.effect_describe_rich, skill.effect_describe_sections)
    return sections.length ? sections : skill.effect_describe_sections
  }
  if ((skill.additional_describe_rich || skill.additional_describe) && skill.additional_describe_sections?.length) {
    const sections = splitProgressionSectionsFromText(skill.additional_describe, skill.additional_describe_rich, skill.additional_describe_sections)
    return sections.length ? sections : skill.additional_describe_sections
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
  if (!Array.isArray(values)) return []
  const lines = values.map(value => String(value || '').trim())
  while (lines.length && !lines[0]) lines.shift()
  while (lines.length && !lines[lines.length - 1]) lines.pop()
  return lines
}

function getProgressionSectionTitle(section: FanxiuGongfaProgressionSection) {
  return String(section.title_rich || section.title || '').trim()
}

function getProgressionSectionLines(section: FanxiuGongfaProgressionSection) {
  const richLines = normalizeProgressionTextList(section.rich_lines)
  return richLines.length ? richLines : normalizeProgressionTextList(section.lines)
}

function getProgressionSections(row: FanxiuGongfaProgressionRow | null | undefined) {
  if (!row) return []
  const sections = splitProgressionSectionsFromText(getProgressionText(row), getProgressionRichText(row), row.describe_sections)
  const source = sections.length ? sections : row.describe_sections ?? []
  return source.filter(section => getProgressionSectionTitle(section) || getProgressionSectionLines(section).length)
}

function isBlankProgressionLine(line: string) {
  return !String(line || '').trim()
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

const escapeHtml = escapeFanxiuHtml

function renderFanxiuTextSegment(value: string) {
  return renderFanxiuRichText(value, wikiLinkTargetsByFirstChar.value)
}

function renderFanxiuText(value: string, options: { mapColors?: boolean; tone?: 'dark' | 'light' } = {}) {
  const colorMap = options.tone === 'light' ? lightRichColorMap : richColorMap
  const raw = String(value || '')
  const tagRe = /<color=(#[0-9a-fA-F]{3,8})>|<\/color>|<size=([0-9]{1,3})>|<\/size>/g
  let output = ''
  let lastIndex = 0
  for (const match of raw.matchAll(tagRe)) {
    output += renderFanxiuTextSegment(raw.slice(lastIndex, match.index))
    if (match[1]) {
      const color = match[1]
      const mapped = options.mapColors === false ? color : colorMap[String(color).toLowerCase()] ?? color
      output += `<span class="wiki-rich-color" style="color:${escapeHtml(mapped)}">`
    } else if (match[0] === '</color>') {
      output += '</span>'
    } else if (match[2]) {
      output += '<span>'
    } else {
      output += '</span>'
    }
    lastIndex = (match.index ?? 0) + match[0].length
  }
  output += renderFanxiuTextSegment(raw.slice(lastIndex))
  return output.replace(/\n/g, '<br>')
}

function compactText(value: string | undefined, limit = 120) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text.length <= limit ? text : `${text.slice(0, limit).trimEnd()}...`
}

type FacetOption = (
  FanxiuGongfaQualityPartOption |
  FanxiuGongfaSkillTypeOption |
  FanxiuItemQualityOption |
  FanxiuActivityOption
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

function isFacetRowExpanded(rowKey: string) {
  return Boolean(expandedFacetRows.value[rowKey])
}

function getDisplayableFacetOptions<T extends FacetCountOption>(options: T[], activeValue = '') {
  return options.filter(option => option.count > 0 || option.value === activeValue)
}

function getVisibleFacetOptions<T extends FacetCountOption>(rowKey: string, options: T[], activeValue = '') {
  const displayableOptions = getDisplayableFacetOptions(options, activeValue)
  if (displayableOptions.length <= FACET_OPTION_DISPLAY_LIMIT || isFacetRowExpanded(rowKey)) return displayableOptions
  const visible = displayableOptions.slice(0, FACET_OPTION_DISPLAY_LIMIT)
  if (activeValue && !visible.some(option => option.value === activeValue)) {
    const activeOption = displayableOptions.find(option => option.value === activeValue)
    if (activeOption) return [...visible, activeOption]
  }
  return visible
}

function getFacetHiddenCount(options: FacetCountOption[], activeValue = '') {
  return Math.max(0, getDisplayableFacetOptions(options, activeValue).length - FACET_OPTION_DISPLAY_LIMIT)
}

function shouldShowFacetToggle(rowKey: string, options: FacetCountOption[], activeValue = '') {
  return getDisplayableFacetOptions(options, activeValue).length > FACET_OPTION_DISPLAY_LIMIT || isFacetRowExpanded(rowKey)
}

function getFacetToggleLabel(rowKey: string, options: FacetCountOption[], activeValue = '') {
  return isFacetRowExpanded(rowKey) ? '收起' : `更多 ${getFacetHiddenCount(options, activeValue)}`
}

function toggleFacetRow(rowKey: string) {
  expandedFacetRows.value = {
    ...expandedFacetRows.value,
    [rowKey]: !isFacetRowExpanded(rowKey),
  }
  persistPageConfig()
}

function formatRawValue(value: unknown) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function getHomeMakeStaticSectionTitle(row: FanxiuGongfaHomeMakeStaticDetailRow) {
  if (row.section === 'main_description') return '描述'
  if (row.section === 'main_effect') return '主词条'
  if (row.section === 'side_effect') return '副词条'
  return row.section || '效果'
}

function getHomeMakeStaticRowMeta(row: FanxiuGongfaHomeMakeStaticDetailRow) {
  const values = [
    row.active_state === 'base' ? '基础' : row.active_state === 'active' ? '已激活' : row.active_state,
    row.effect_id ? `Effect ${row.effect_id}` : '',
  ]
  return values.filter(Boolean).join(' / ')
}

function getHomeMakeBuffTiming(group: FanxiuGongfaHomeMakeBuffParameterGroup) {
  return [group.duration_seconds, group.periodic_seconds].filter(Boolean).join(' / ')
}

function getHomeMakeBuffCategoryLabel(value: string) {
  const labels: Record<string, string> = {
    damage: '伤害',
    recovery: '恢复',
    attribute_gain: '增益',
    attribute_debuff: '削弱',
    summon_or_auxiliary: '召唤',
    periodic_or_triggered: '周期',
    control_or_state: '状态',
    display_or_unknown: '展示',
  }
  return String(value || '')
    .split(',')
    .map(part => labels[part] ?? part)
    .filter(Boolean)
    .join(' / ')
}

function getHomeMakeBuffTags(group: FanxiuGongfaHomeMakeBuffParameterGroup) {
  const tags = [
    getHomeMakeBuffCategoryLabel(group.desc_category),
    group.buff_type && group.buff_type !== 'empty' ? group.buff_type : '',
    getHomeMakeBuffTiming(group),
    group.layer ? `层 ${group.layer}` : '',
    group.populated_parameter_fields ? '关联技能' : '',
  ]
  return tags.filter(Boolean).slice(0, 6)
}

function getHomeMakeBuffSearchText(group: FanxiuGongfaHomeMakeBuffParameterGroup) {
  const linkText = (group.links ?? [])
    .flatMap(link => [
      getHomeMakeBuffLinkLabel(link),
      getHomeMakeBuffLinkMeta(link),
      link.target_description,
      link.target_id,
      link.token,
      link.source_file,
    ])
    .join(' ')
  return [
    group.buff_name,
    group.gongfa_names,
    group.side_jie_names,
    cleanFanxiuDisplayText(group.buff_desc),
    getHomeMakeBuffTags(group).join(' '),
    group.matching_buff_ids,
    group.populated_parameter_fields,
    linkText,
  ]
    .filter(Boolean)
    .join(' ')
}

function getHomeMakeFormulaTags(group: FanxiuGongfaHomeMakeXianShuFormulaGroup) {
  return [
    group.rows ? `${group.rows} 阶` : '',
    group.star_rows ? `${group.star_rows} 星级行` : '',
    group.feature_group ? `FG ${group.feature_group}` : '',
    group.gongfa_names,
  ]
    .filter(Boolean)
    .slice(0, 6)
}

function getHomeMakeFormulaSearchText(group: FanxiuGongfaHomeMakeXianShuFormulaGroup) {
  return [
    group.feature_group,
    group.side_feature_names,
    group.buff_names,
    group.gongfa_ids,
    group.gongfa_names,
    cleanFanxiuDisplayText(group.sample_rendered_plain),
    getHomeMakeFormulaTags(group).join(' '),
  ]
    .filter(Boolean)
    .join(' ')
}

function splitSpecialFazeTokens(value: string | undefined) {
  return String(value || '')
    .split(/[、;,]/)
    .map(part => part.trim())
    .filter(Boolean)
}

function getSpecialFazeEffectTags(effect: FanxiuGongfaSpecialFazeEffectType) {
  return [
    effect.effect_type ? `Type ${effect.effect_type}` : '',
    effect.stage_count ? `${effect.stage_count} 阶` : '',
    effect.effect_id_count ? `${effect.effect_id_count} 效果` : '',
  ].filter(Boolean)
}

function getSpecialFazeReasonTags(reason: FanxiuGongfaSpecialFazeReason) {
  return [
    reason.reason ? `Reason ${reason.reason}` : '',
    reason.stage_count ? `${reason.stage_count} 阶` : '',
    reason.effect_types ? `Type ${reason.effect_types}` : '',
  ].filter(Boolean)
}

function getSpecialFazeStageTags(stage: FanxiuGongfaSpecialFazeStage) {
  return [
    stage.stage,
    stage.faze_id ? `Faze ${stage.faze_id}` : '',
    stage.effect_type ? `Type ${stage.effect_type}` : '',
    stage.tip_codes ? `Reason ${stage.tip_codes}` : '',
  ].filter(Boolean)
}

function getHomeMakeBuffLinkLabel(link: FanxiuGongfaHomeMakeBuffParameterLink) {
  return link.target_name || link.target_id || link.token || link.target_table
}

function getHomeMakeBuffLinkMeta(link: FanxiuGongfaHomeMakeBuffParameterLink) {
  const tableLabels: Record<string, string> = {
    'Renjie-GongfaJie': '重数',
    GongfaSkill: '技能',
    lua_file: '表现文件',
  }
  return tableLabels[link.target_table] ?? link.target_table
}

function getHomeMakeBuffLinkGongfaId(link: FanxiuGongfaHomeMakeBuffParameterLink) {
  return String(link.target_gongfa_id || '').trim()
}

function canNavigateHomeMakeBuffLink(link: FanxiuGongfaHomeMakeBuffParameterLink) {
  return Boolean(getHomeMakeBuffLinkGongfaId(link))
}

async function navigateHomeMakeBuffLink(link: FanxiuGongfaHomeMakeBuffParameterLink) {
  const gongfaId = getHomeMakeBuffLinkGongfaId(link)
  if (!gongfaId) return
  homeMakeBuffParameterQuery.value = ''
  homeMakeFormulaQuery.value = ''
  activeTab.value = 'gongfa'
  selectedId.value = gongfaId
  selectedCard.value = null
  query.value = gongfaId
  gongfaQualityGradeFilter.value = ''
  gongfaQualityFamilyFilter.value = ''
  gongfaSkillTypeFilter.value = ''
  page.value = 1
  await loadGongfaCards({ keepSelection: true })
}

function getHomeMakeBuffLinkTitle(link: FanxiuGongfaHomeMakeBuffParameterLink) {
  return [link.target_description, link.source_file].filter(Boolean).join('\n')
}

function clearHomeMakeStaticDetail() {
  homeMakeStaticDetailRequestSeq += 1
  homeMakeBuffParameterSemanticsRequestSeq += 1
  homeMakeFormulaCatalogRequestSeq += 1
  specialFazeCatalogRequestSeq += 1
  selectedHomeMakeStaticDetail.value = null
  selectedHomeMakeBuffParameterSemantics.value = null
  selectedHomeMakeXianShuFormulaCatalog.value = null
  selectedSpecialFazeCatalog.value = null
  loadingHomeMakeStaticDetail.value = false
  loadingHomeMakeBuffParameterSemantics.value = false
  loadingHomeMakeFormulaCatalog.value = false
  loadingSpecialFazeCatalog.value = false
}

async function loadHomeMakeStaticDetail(gongfaId: string | number) {
  const nextId = String(gongfaId)
  const requestSeq = ++homeMakeStaticDetailRequestSeq
  selectedHomeMakeStaticDetail.value = null
  const cached = gongfaHomeMakeStaticDetailCache.get(nextId)
  if (cached !== undefined) {
    selectedHomeMakeStaticDetail.value = cached
    loadingHomeMakeStaticDetail.value = false
    return
  }

  loadingHomeMakeStaticDetail.value = true
  try {
    const response = await getFanxiuGongfaHomeMakeStaticDetail(nextId, { include_inactive: false })
    if (requestSeq !== homeMakeStaticDetailRequestSeq) return
    gongfaHomeMakeStaticDetailCache.set(nextId, response)
    selectedHomeMakeStaticDetail.value = response
  } catch (error) {
    if (requestSeq !== homeMakeStaticDetailRequestSeq) return
    gongfaHomeMakeStaticDetailCache.set(nextId, null)
    selectedHomeMakeStaticDetail.value = null
    console.warn('Failed to load Fanxiu GongFaHomeMake static detail:', error)
  } finally {
    if (requestSeq === homeMakeStaticDetailRequestSeq) {
      loadingHomeMakeStaticDetail.value = false
    }
  }
}

async function loadHomeMakeBuffParameterSemantics(gongfaId: string | number) {
  const nextId = String(gongfaId)
  const requestSeq = ++homeMakeBuffParameterSemanticsRequestSeq
  selectedHomeMakeBuffParameterSemantics.value = null
  const cached = gongfaHomeMakeBuffParameterSemanticsCache.get(nextId)
  if (cached !== undefined) {
    selectedHomeMakeBuffParameterSemantics.value = cached
    loadingHomeMakeBuffParameterSemantics.value = false
    return
  }

  loadingHomeMakeBuffParameterSemantics.value = true
  try {
    const response = await getFanxiuGongfaHomeMakeBuffParameterSemantics(nextId, { limit: 80 })
    if (requestSeq !== homeMakeBuffParameterSemanticsRequestSeq) return
    gongfaHomeMakeBuffParameterSemanticsCache.set(nextId, response)
    selectedHomeMakeBuffParameterSemantics.value = response
  } catch (error) {
    if (requestSeq !== homeMakeBuffParameterSemanticsRequestSeq) return
    gongfaHomeMakeBuffParameterSemanticsCache.set(nextId, null)
    selectedHomeMakeBuffParameterSemantics.value = null
    console.warn('Failed to load Fanxiu GongFaHomeMake buff parameter semantics:', error)
  } finally {
    if (requestSeq === homeMakeBuffParameterSemanticsRequestSeq) {
      loadingHomeMakeBuffParameterSemantics.value = false
    }
  }
}

async function loadHomeMakeFormulaCatalog(gongfaId: string | number) {
  const nextId = String(gongfaId)
  const requestSeq = ++homeMakeFormulaCatalogRequestSeq
  selectedHomeMakeXianShuFormulaCatalog.value = null
  const cached = gongfaHomeMakeFormulaCatalogCache.get(nextId)
  if (cached !== undefined) {
    selectedHomeMakeXianShuFormulaCatalog.value = cached
    loadingHomeMakeFormulaCatalog.value = false
    return
  }

  loadingHomeMakeFormulaCatalog.value = true
  try {
    const response = await getFanxiuGongfaHomeMakeXianShuFormulaCatalog(nextId, { limit: 200, star: 1 })
    if (requestSeq !== homeMakeFormulaCatalogRequestSeq) return
    gongfaHomeMakeFormulaCatalogCache.set(nextId, response)
    selectedHomeMakeXianShuFormulaCatalog.value = response
  } catch (error) {
    if (requestSeq !== homeMakeFormulaCatalogRequestSeq) return
    gongfaHomeMakeFormulaCatalogCache.set(nextId, null)
    selectedHomeMakeXianShuFormulaCatalog.value = null
    console.warn('Failed to load Fanxiu GongFaHomeMake xianshu formula catalog:', error)
  } finally {
    if (requestSeq === homeMakeFormulaCatalogRequestSeq) {
      loadingHomeMakeFormulaCatalog.value = false
    }
  }
}

async function loadSpecialFazeCatalog(gongfaId: string | number) {
  const nextId = String(gongfaId)
  const requestSeq = ++specialFazeCatalogRequestSeq
  selectedSpecialFazeCatalog.value = null
  const cached = gongfaSpecialFazeCatalogCache.get(nextId)
  if (cached !== undefined) {
    selectedSpecialFazeCatalog.value = cached
    loadingSpecialFazeCatalog.value = false
    return
  }

  loadingSpecialFazeCatalog.value = true
  try {
    const response = await getFanxiuGongfaSpecialFazeCatalog({ gid: nextId, limit: 1 })
    if (requestSeq !== specialFazeCatalogRequestSeq) return
    const value = response.selected.group ? response : null
    gongfaSpecialFazeCatalogCache.set(nextId, value)
    selectedSpecialFazeCatalog.value = value
  } catch (error) {
    if (requestSeq !== specialFazeCatalogRequestSeq) return
    gongfaSpecialFazeCatalogCache.set(nextId, null)
    selectedSpecialFazeCatalog.value = null
    console.warn('Failed to load Fanxiu Special-GongfaJie/Faze catalog:', error)
  } finally {
    if (requestSeq === specialFazeCatalogRequestSeq) {
      loadingSpecialFazeCatalog.value = false
    }
  }
}

async function loadHomeMakeBuffOverview(options: { force?: boolean } = {}) {
  if (homeMakeBuffOverview.value && !options.force) return
  const requestSeq = ++homeMakeBuffOverviewRequestSeq
  loadingHomeMakeBuffOverview.value = true
  try {
    const response = await getFanxiuGongfaHomeMakeBuffParameterSemantics(null, { limit: 200 })
    if (requestSeq !== homeMakeBuffOverviewRequestSeq) return
    homeMakeBuffOverview.value = response
  } catch (error) {
    if (requestSeq !== homeMakeBuffOverviewRequestSeq) return
    console.warn('Failed to load Fanxiu GongFaHomeMake buff overview:', error)
  } finally {
    if (requestSeq === homeMakeBuffOverviewRequestSeq) {
      loadingHomeMakeBuffOverview.value = false
    }
  }
}

async function loadGongfaCards(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  void loadHomeMakeBuffOverview()
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
    activityItems.value = []
    lingjieItems.value = []
    digitDoorEnhanceItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
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
      clearHomeMakeStaticDetail()
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
    activityItems.value = []
    lingjieItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (
        !selectedItem.value ||
        String(selectedItem.value.id) !== selectedId.value ||
        shouldRefetchItemDetail(selectedId.value, selectedItem.value)
      ) {
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
      clearHomeMakeStaticDetail()
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

async function loadVisualManifest(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  loadingDetail.value = false
  try {
    const requestParams = {
      query: visualSimilarityFile.value ? undefined : query.value,
      asset_group: visualAssetGroupFilter.value || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    }
    const response = visualSimilarityFile.value
      ? await searchFanxiuStaticVisualByImage(visualSimilarityFile.value, { ...requestParams, max_prefilter: 800 })
      : await getFanxiuStaticVisualManifest(requestParams)
    if (requestSeq !== listRequestSeq) return
    visualManifest.value = response
    catalogPath.value = response.manifest_root
    total.value = response.filtered

    const maxPage = Math.max(1, Math.ceil(Math.max(response.filtered, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadVisualManifest(options)
      return
    }

    visualItems.value = response.rows
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorLevel.value = null
    selectedDigitDoorStage.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    protocolResponse.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && response.rows.some(item => getVisualAssetKey(item) === selectedId.value)
    if (keepSelected) return
    selectedId.value = response.rows[0] ? getVisualAssetKey(response.rows[0]) : ''
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取图标图鉴失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadStaticAssetManifest(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  loadingDetail.value = false
  staticAssetPreviewManifest.value = null
  try {
    const response = await getFanxiuStaticAssetManifest({
      query: query.value,
      catalog_view: staticAssetCatalogView.value,
      asset_group: staticAssetGroupFilter.value || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    staticAssetManifest.value = response
    catalogPath.value = response.manifest_root
    total.value = response.filtered

    const maxPage = Math.max(1, Math.ceil(Math.max(response.filtered, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadStaticAssetManifest(options)
      return
    }

    staticAssetItems.value = response.rows
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorLevel.value = null
    selectedDigitDoorStage.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    protocolResponse.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && response.rows.some(item => getStaticAssetKey(item) === selectedId.value)
    if (keepSelected) return
    selectedId.value = response.rows[0] ? getStaticAssetKey(response.rows[0]) : ''
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取素材图鉴失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadStaticAssetPreviewManifest(item: FanxiuStaticAssetManifestRow | null | undefined) {
  const requestSeq = ++staticAssetPreviewRequestSeq
  staticAssetPreviewManifest.value = null
  if (activeTab.value !== 'asset' || !item?.relative_path || item.semantic_id) {
    loadingStaticAssetPreview.value = false
    return
  }
  loadingStaticAssetPreview.value = true
  try {
    const response = await getFanxiuStaticAssetPreviewManifest({ path: item.relative_path })
    if (requestSeq !== staticAssetPreviewRequestSeq) return
    staticAssetPreviewManifest.value = response
  } catch (error) {
    if (requestSeq === staticAssetPreviewRequestSeq) {
      console.warn('Failed to load Fanxiu static asset preview manifest:', error)
    }
  } finally {
    if (requestSeq === staticAssetPreviewRequestSeq) {
      loadingStaticAssetPreview.value = false
    }
  }
}

async function loadAudioManifest(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  loadingDetail.value = false
  try {
    const response = await getFanxiuWwiseMp3Manifest({
      query: query.value,
      kind: audioKindFilter.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    audioManifest.value = response
    catalogPath.value = response.manifest
    total.value = response.filtered

    const maxPage = Math.max(1, Math.ceil(Math.max(response.filtered, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadAudioManifest(options)
      return
    }

    audioItems.value = response.rows
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorLevel.value = null
    selectedDigitDoorStage.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    protocolResponse.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && response.rows.some(item => getAudioAssetKey(item) === selectedId.value)
    if (keepSelected) return
    selectedId.value = response.rows[0] ? getAudioAssetKey(response.rows[0]) : ''
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取音乐图鉴失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadActivityCards(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuActivityCards({
      query: query.value,
      kind_key: activityKindFilter.value,
      time_kind: activityTimeFilter.value,
      activity_type: activityTypeFilter.value,
      ...objectSortParams.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    activityStats.value = response.stats
    catalogPath.value = response.catalog_path
    activityKindOptions.value = response.kind_options ?? []
    activityTimeOptions.value = response.time_options ?? []
    activityTypeOptions.value = response.activity_type_options ?? []
    activityFacetIndex.value = response.facet_index ?? null
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadActivityCards(options)
      return
    }

    activityItems.value = response.items
    gongfaItems.value = []
    itemItems.value = []
    lingjieItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (!selectedActivity.value || String(selectedActivity.value.id) !== selectedId.value) {
        void selectActivity(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    if (first) {
      selectedId.value = String(first.id)
      selectedActivity.value = null
      void selectActivity(first.id)
    } else {
      selectedId.value = ''
      selectedActivity.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取活动表失败')
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
    activityItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
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
      clearHomeMakeStaticDetail()
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

async function loadDigitDoorCharacterCards(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuDigitDoorCharacterCards({
      query: query.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    digitDoorStats.value = response.stats
    catalogPath.value = response.catalog_path ?? ''
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadDigitDoorCharacterCards(options)
      return
    }

    digitDoorItems.value = response.items
    gongfaItems.value = []
    itemItems.value = []
    activityItems.value = []
    lingjieItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (!selectedDigitDoorCharacter.value || String(selectedDigitDoorCharacter.value.id) !== selectedId.value) {
        void selectDigitDoorCharacter(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    if (first) {
      selectedId.value = String(first.id)
      selectedDigitDoorCharacter.value = null
      void selectDigitDoorCharacter(first.id)
    } else {
      selectedId.value = ''
      selectedDigitDoorCharacter.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取数字门角色失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadDigitDoorLevelConfigs(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuDigitDoorLevelConfigs({
      query: query.value,
      stage: digitDoorStageFilter.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    digitDoorLevelStats.value = response.stats
    digitDoorStageOptions.value = response.stage_options ?? []
    catalogPath.value = response.catalog_path ?? ''
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadDigitDoorLevelConfigs(options)
      return
    }

    digitDoorLevelItems.value = response.items
    gongfaItems.value = []
    itemItems.value = []
    activityItems.value = []
    lingjieItems.value = []
    digitDoorItems.value = []
    digitDoorEnhanceItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (!selectedDigitDoorLevel.value || String(selectedDigitDoorLevel.value.id) !== selectedId.value) {
        void selectDigitDoorLevel(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    if (first) {
      selectedId.value = String(first.id)
      selectedDigitDoorLevel.value = null
      selectedDigitDoorStage.value = null
      void selectDigitDoorLevel(first.id)
    } else {
      selectedId.value = ''
      selectedDigitDoorLevel.value = null
      selectedDigitDoorStage.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取数字门关卡失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadDoupoTDPartnerCards(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuDoupoTDPartnerCards({
      query: query.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    doupoTDStats.value = response.stats
    catalogPath.value = response.catalog_path ?? ''
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadDoupoTDPartnerCards(options)
      return
    }

    doupoTDItems.value = response.items
    gongfaItems.value = []
    itemItems.value = []
    activityItems.value = []
    lingjieItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (!selectedDoupoTDPartner.value || String(selectedDoupoTDPartner.value.id) !== selectedId.value) {
        void selectDoupoTDPartner(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    if (first) {
      selectedId.value = String(first.id)
      selectedDoupoTDPartner.value = null
      void selectDoupoTDPartner(first.id)
    } else {
      selectedId.value = ''
      selectedDoupoTDPartner.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取斗破角色失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadDoupoTDRewardConfigs(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuDoupoTDRewardConfigs({
      query: query.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    doupoTDRewardStats.value = response.stats
    catalogPath.value = response.source?.reward_items ?? response.source?.levels ?? response.source?.prelevel_rewards ?? ''
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadDoupoTDRewardConfigs(options)
      return
    }

    doupoTDRewardItems.value = response.items
    gongfaItems.value = []
    itemItems.value = []
    activityItems.value = []
    lingjieItems.value = []
    doupoTDItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      const current = doupoTDRewardItems.value.find(item => getDoupoTDRewardConfigKey(item) === selectedId.value)
      if (current) {
        await selectDoupoTDRewardConfig(current)
      } else if (!selectedDoupoTDReward.value || getDoupoTDRewardConfigKey(selectedDoupoTDReward.value) !== selectedId.value) {
        await selectDoupoTDRewardConfig(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    if (first) {
      selectedId.value = getDoupoTDRewardConfigKey(first)
      selectedDoupoTDReward.value = first
      doupoTDRewardDetailCache.set(selectedId.value, first)
    } else {
      selectedId.value = ''
      selectedDoupoTDReward.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取斗破奖励失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadProtocolSemantics(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  loadingDetail.value = false
  try {
    const response = await getFanxiuProtocolSemantics({
      feature: protocolFeature.value,
      query: query.value.trim(),
      role: protocolRoleFilter.value,
      operation: protocolOperationFilter.value,
      limit: 2000,
      edge_limit: 3000,
    })
    if (requestSeq !== listRequestSeq) return
    protocolResponse.value = response
    catalogPath.value = response.outputs?.semantics ?? ''
    total.value = response.counts?.filtered_rows ?? response.items.length
    gongfaItems.value = []
    itemItems.value = []
    activityItems.value = []
    lingjieItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()

    const current = options.keepSelection ? selectedId.value : ''
    selectedId.value = response.items.some(item => item.packet === current)
      ? current
      : response.items[0]?.packet ?? ''
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取协议语义失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadPacketProtocolWiki() {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  loadingDetail.value = false
  try {
    const response = await listFanxiuTcpBusinessEntries({ page: 1, page_size: 1 })
    if (requestSeq !== listRequestSeq) return
    const needle = normalizeSearchQuery(query.value).toLowerCase()
    const categories = response.category_summary ?? []
    protocolBusinessCategories.value = needle
      ? categories.filter(item => {
          const haystack = `${item.category} ${item.meaning} ${item.protocols.join(' ')}`.toLowerCase()
          return haystack.includes(needle)
        })
      : categories
    selectedPacketCategory.value = protocolBusinessCategories.value.some(item => item.category === selectedPacketCategory.value)
      ? selectedPacketCategory.value
      : protocolBusinessCategories.value[0]?.category ?? ''
    packetProtocolDetails.value = []
    expandedPacketProtocol.value = ''
    packetProtocolSamples.value = []
    protocolResponse.value = null
    catalogPath.value = ''
    total.value = protocolBusinessCategories.value.length
    gongfaItems.value = []
    itemItems.value = []
    activityItems.value = []
    lingjieItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    selectedId.value = ''
    clearHomeMakeStaticDetail()
    if (selectedPacketCategory.value) {
      await loadPacketProtocolCategoryDetail(selectedPacketCategory.value, requestSeq)
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取抓包协议图鉴失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function loadPacketProtocolCategoryDetail(category: string, requestSeq = listRequestSeq) {
  const target = String(category || '').trim()
  if (!target) {
    packetProtocolDetails.value = []
    expandedPacketProtocol.value = ''
    packetProtocolSamples.value = []
    return
  }
  loadingDetail.value = true
  try {
    const response = await listFanxiuTcpBusinessEntries({ page: 1, page_size: 50, category: target })
    if (requestSeq !== listRequestSeq || selectedPacketCategory.value !== target) return
    packetProtocolDetails.value = sortPacketProtocolsByVisibility(response.protocol_summary ?? [])
    expandedPacketProtocol.value = ''
    packetProtocolSamples.value = []
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取抓包大类详情失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingDetail.value = false
    }
  }
}

async function loadWikiLinkIndex() {
  try {
    const response = await getFanxiuWikiLinkIndex()
    wikiLinkIndexItems.value = response.items ?? []
  } catch (error) {
    console.warn('Failed to load Fanxiu wiki link index:', error)
  }
}

function loadCurrentCards(options: { keepSelection?: boolean } = {}) {
  if (activeTab.value === 'item') {
    return loadItemCards(options)
  }
  if (activeTab.value === 'visual') {
    return loadVisualManifest(options)
  }
  if (activeTab.value === 'asset') {
    return loadStaticAssetManifest(options)
  }
  if (activeTab.value === 'audio') {
    return loadAudioManifest(options)
  }
  if (activeTab.value === 'activity') {
    return loadActivityCards(options)
  }
  if (activeTab.value === 'lingjie') {
    return loadLingjieFeatureCards(options)
  }
  if (activeTab.value === 'digitdoor') {
    return loadDigitDoorCharacterCards(options)
  }
  if (activeTab.value === 'digitdoor_level') {
    return loadDigitDoorLevelConfigs(options)
  }
  if (activeTab.value === 'digitdoor_enhance') {
    return loadDigitDoorEnhanceGroups(options)
  }
  if (activeTab.value === 'doupotd') {
    return loadDoupoTDPartnerCards(options)
  }
  if (activeTab.value === 'doupotd_reward') {
    return loadDoupoTDRewardConfigs(options)
  }
  if (activeTab.value === 'protocol') {
    return loadProtocolSemantics(options)
  }
  if (activeTab.value === 'packet') {
    return loadPacketProtocolWiki()
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
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    const tabs = progressionTabs.value
    if (!tabs.some(tab => tab.key === selectedProgressionType.value)) {
      selectedProgressionType.value = tabs[0]?.key ?? ''
    }
    loadingDetail.value = false
    void loadHomeMakeStaticDetail(nextId)
    void loadHomeMakeBuffParameterSemantics(nextId)
    void loadHomeMakeFormulaCatalog(nextId)
    void loadSpecialFazeCatalog(nextId)
    return
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuGongfaCard(nextId)
    if (requestSeq !== detailRequestSeq) return
    gongfaDetailCache.set(nextId, response.card)
    selectedCard.value = response.card
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    const tabs = progressionTabs.value
    if (!tabs.some(tab => tab.key === selectedProgressionType.value)) {
      selectedProgressionType.value = tabs[0]?.key ?? ''
    }
    void loadHomeMakeStaticDetail(nextId)
    void loadHomeMakeBuffParameterSemantics(nextId)
    void loadHomeMakeFormulaCatalog(nextId)
    void loadSpecialFazeCatalog(nextId)
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
  if (cached && !shouldRefetchItemDetail(nextId, cached)) {
    selectedItem.value = cached
    selectedCard.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
    loadingDetail.value = false
    return
  }
  if (cached) {
    itemDetailCache.delete(nextId)
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuItemCard(nextId)
    if (requestSeq !== detailRequestSeq) return
    itemDetailCache.set(nextId, response.card)
    if (response.card.effect_details?.length) {
      itemDetailRefreshAttempts.delete(nextId)
    }
    selectedItem.value = response.card
    selectedCard.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
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

async function selectActivity(activityId: string | number) {
  const nextId = String(activityId)
  selectedId.value = nextId
  const requestSeq = ++detailRequestSeq
  const cached = activityDetailCache.get(nextId)
  if (cached) {
    selectedActivity.value = cached
    selectedCard.value = null
    selectedItem.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
    loadingDetail.value = false
    return
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuActivityCard(nextId)
    if (requestSeq !== detailRequestSeq) return
    activityDetailCache.set(nextId, response.card)
    selectedActivity.value = response.card
    selectedCard.value = null
    selectedItem.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取活动详情失败')
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
    selectedActivity.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
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
    selectedActivity.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
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

async function selectDigitDoorCharacter(characterId: string | number) {
  const nextId = String(characterId)
  selectedId.value = nextId
  const requestSeq = ++detailRequestSeq
  const cached = digitDoorDetailCache.get(nextId)
  if (cached) {
    selectedDigitDoorCharacter.value = cached
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    loadingDetail.value = false
    return
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuDigitDoorCharacterCard(nextId)
    if (requestSeq !== detailRequestSeq) return
    digitDoorDetailCache.set(nextId, response.card)
    selectedDigitDoorCharacter.value = response.card
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取数字门角色详情失败')
    }
  } finally {
    if (requestSeq === detailRequestSeq) {
      loadingDetail.value = false
    }
  }
}

async function selectDigitDoorLevel(levelId: string | number) {
  const nextId = String(levelId)
  selectedId.value = nextId
  const requestSeq = ++detailRequestSeq
  const cached = digitDoorLevelDetailCache.get(nextId)
  if (cached) {
    selectedDigitDoorLevel.value = cached.item
    selectedDigitDoorStage.value = cached.stage ?? null
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    loadingDetail.value = false
    return
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuDigitDoorLevelConfig(nextId)
    if (requestSeq !== detailRequestSeq) return
    digitDoorLevelDetailCache.set(nextId, { item: response.item, stage: response.stage })
    selectedDigitDoorLevel.value = response.item
    selectedDigitDoorStage.value = response.stage ?? null
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorEnhanceGroup.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取数字门关卡详情失败')
    }
  } finally {
    if (requestSeq === detailRequestSeq) {
      loadingDetail.value = false
    }
  }
}

async function loadDigitDoorEnhanceGroups(options: { keepSelection?: boolean } = {}) {
  const requestSeq = ++listRequestSeq
  loadingList.value = true
  try {
    const response = await searchFanxiuDigitDoorEnhanceGroups({
      query: query.value,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    if (requestSeq !== listRequestSeq) return
    digitDoorEnhanceStats.value = response.stats
    catalogPath.value = response.catalog_path ?? ''
    total.value = response.total

    const maxPage = Math.max(1, Math.ceil(Math.max(response.total, 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      await loadDigitDoorEnhanceGroups(options)
      return
    }

    digitDoorEnhanceItems.value = response.items
    gongfaItems.value = []
    itemItems.value = []
    activityItems.value = []
    lingjieItems.value = []
    digitDoorItems.value = []
    digitDoorLevelItems.value = []
    doupoTDItems.value = []
    doupoTDRewardItems.value = []
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorLevel.value = null
    selectedDigitDoorStage.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    const keepSelected = options.keepSelection && Boolean(selectedId.value)
    if (keepSelected) {
      if (!selectedDigitDoorEnhanceGroup.value || String(selectedDigitDoorEnhanceGroup.value.char_id) !== selectedId.value) {
        void selectDigitDoorEnhanceGroup(selectedId.value)
      }
      return
    }

    const first = response.items[0]
    const firstId = first?.id ?? first?.char_id
    if (firstId) {
      selectedId.value = String(firstId)
      selectedDigitDoorEnhanceGroup.value = null
      void selectDigitDoorEnhanceGroup(firstId)
    } else {
      selectedId.value = ''
      selectedDigitDoorEnhanceGroup.value = null
    }
  } catch (error: any) {
    if (requestSeq === listRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取数字门强化失败')
    }
  } finally {
    if (requestSeq === listRequestSeq) {
      loadingList.value = false
    }
  }
}

async function selectDigitDoorEnhanceGroup(groupId: string | number) {
  const nextId = String(groupId)
  selectedId.value = nextId
  const requestSeq = ++detailRequestSeq
  const cached = digitDoorEnhanceDetailCache.get(nextId)
  if (cached) {
    selectedDigitDoorEnhanceGroup.value = cached
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorLevel.value = null
    selectedDigitDoorStage.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    loadingDetail.value = false
    return
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuDigitDoorEnhanceGroup(nextId)
    if (requestSeq !== detailRequestSeq) return
    digitDoorEnhanceDetailCache.set(nextId, response.group)
    selectedDigitDoorEnhanceGroup.value = response.group
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDigitDoorCharacter.value = null
    selectedDigitDoorLevel.value = null
    selectedDigitDoorStage.value = null
    selectedDoupoTDPartner.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取数字门强化详情失败')
    }
  } finally {
    if (requestSeq === detailRequestSeq) {
      loadingDetail.value = false
    }
  }
}

async function selectDoupoTDPartner(partnerId: string | number) {
  const nextId = String(partnerId)
  selectedId.value = nextId
  const requestSeq = ++detailRequestSeq
  const cached = doupoTDDetailCache.get(nextId)
  if (cached) {
    selectedDoupoTDPartner.value = cached
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
    loadingDetail.value = false
    return
  }
  loadingDetail.value = true
  try {
    const response = await getFanxiuDoupoTDPartnerCard(nextId)
    if (requestSeq !== detailRequestSeq) return
    doupoTDDetailCache.set(nextId, response.card)
    selectedDoupoTDPartner.value = response.card
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDReward.value = null
    clearHomeMakeStaticDetail()
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取斗破角色详情失败')
    }
  } finally {
    if (requestSeq === detailRequestSeq) {
      loadingDetail.value = false
    }
  }
}

async function selectDoupoTDRewardConfig(value: string | FanxiuDoupoTDRewardConfigSearchItem) {
  const key = typeof value === 'string' ? value : getDoupoTDRewardConfigKey(value)
  if (!key) return
  selectedId.value = key
  const current = typeof value === 'string'
    ? doupoTDRewardItems.value.find(item => getDoupoTDRewardConfigKey(item) === value)
    : value
  if (current) {
    selectedDoupoTDReward.value = current
    doupoTDRewardDetailCache.set(key, current)
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
    loadingDetail.value = false
    return
  }

  const cached = doupoTDRewardDetailCache.get(key)
  if (cached) {
    selectedDoupoTDReward.value = cached
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
    loadingDetail.value = false
    return
  }

  const requestSeq = ++detailRequestSeq
  loadingDetail.value = true
  const { sourceTable, configId } = parseDoupoTDRewardConfigKey(key)
  try {
    const response = await getFanxiuDoupoTDRewardConfig(sourceTable, configId)
    if (requestSeq !== detailRequestSeq) return
    selectedDoupoTDReward.value = response.item
    doupoTDRewardDetailCache.set(key, response.item)
    selectedCard.value = null
    selectedItem.value = null
    selectedActivity.value = null
    selectedLingjieCard.value = null
    selectedDoupoTDPartner.value = null
    clearHomeMakeStaticDetail()
  } catch (error: any) {
    if (requestSeq === detailRequestSeq) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '读取斗破奖励详情失败')
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
    if (isAuxiliaryWikiTab(tab)) selectedAuxiliaryTab.value = tab
    selectedId.value = nextId
    page.value = 1
    if (options.resetListContext) {
      query.value = ''
      sortMode.value = 'default'
      if (tab === 'item') {
        itemQualityFilter.value = ''
        itemTypeFilter.value = ''
        itemSubTypeFilter.value = ''
      } else if (tab === 'activity') {
        activityKindFilter.value = ''
        activityTimeFilter.value = ''
        activityTypeFilter.value = ''
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

function searchWikiObject(tab: WikiTab, text: string) {
  const nextQuery = cleanWikiLinkAlias(text)
  if (!nextQuery) return
  internalTabNavigation = true
  try {
    activeTab.value = tab
    if (isAuxiliaryWikiTab(tab)) selectedAuxiliaryTab.value = tab
    selectedId.value = ''
    query.value = nextQuery
    page.value = 1
    sortMode.value = 'default'
    void loadCurrentCards()
    void router.replace({ query: { ...route.query, tab, q: nextQuery } }).catch(() => {})
  } finally {
    window.setTimeout(() => {
      internalTabNavigation = false
    }, 0)
  }
}

function getWikiTabFromTabElement(element: HTMLElement | null) {
  if (!element) return null
  const idValue = element.id.startsWith('tab-') ? element.id.slice(4) : ''
  const controlsValue = element.getAttribute('aria-controls') ?? ''
  const paneValue = controlsValue.startsWith('pane-') ? controlsValue.slice(5) : ''
  const rawValue = idValue || paneValue
  if (rawValue === AUXILIARY_TOP_TAB_KEY) return selectedAuxiliaryTab.value
  return normalizeWikiTab(rawValue)
}

function buildWikiTabHref(tab: WikiTab) {
  const nextQuery = { ...route.query, tab }
  delete nextQuery.id
  return router.resolve({ path: route.path, query: nextQuery }).href
}

function buildStandaloneWikiTabHref(tab: WikiTab) {
  const nextQuery = { ...route.query, tab }
  delete nextQuery.id
  return router.resolve({ path: '/standalone/fanxiu/wiki', query: nextQuery }).href
}

function getIndependentResourceType(tab: WikiTab): FanxiuResourceType | null {
  if (tab === 'gongfa' || tab === 'item' || tab === 'lingjie') return tab
  return null
}

function showContextMenu(event: MouseEvent, href: string) {
  if (!href) return
  event.preventDefault()
  event.stopPropagation()
  const menuWidth = 154
  const menuHeight = 38
  contextMenu.value = {
    visible: true,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8)),
    href,
  }
}

function closeContextMenu() {
  if (!contextMenu.value.visible) return
  contextMenu.value = { ...contextMenu.value, visible: false }
}

function openContextMenuTarget() {
  const href = contextMenu.value.href
  closeContextMenu()
  if (href) window.open(href, '_blank', 'noopener')
}

function handleGlobalContextMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeContextMenu()
}

function handleWikiTabHeaderClick(event: MouseEvent) {
  if (event.button !== 0 || (!event.ctrlKey && !event.metaKey)) return
  const tabElement = event.target instanceof Element
    ? event.target.closest<HTMLElement>('.el-tabs__item')
    : null
  const tab = getWikiTabFromTabElement(tabElement)
  if (!tab) return
  event.preventDefault()
  event.stopPropagation()
  window.open(buildWikiTabHref(tab), '_blank', 'noopener')
}

function handleWikiTabHeaderContextMenu(event: MouseEvent) {
  const tabElement = event.target instanceof Element
    ? event.target.closest<HTMLElement>('.el-tabs__item')
    : null
  const tab = getWikiTabFromTabElement(tabElement)
  if (!tab) return
  showContextMenu(event, buildStandaloneWikiTabHref(tab))
}

function handleObjectContextMenu(event: MouseEvent, objectId: string | number) {
  const resourceType = getIndependentResourceType(activeTab.value)
  if (!resourceType) return
  selectObject(objectId)
  showContextMenu(event, buildFanxiuResourceHref(resourceType, objectId))
}

function selectObject(objectId: string | number) {
  closeContextMenu()
  if (activeTab.value === 'item') {
    return selectItem(objectId)
  }
  if (activeTab.value === 'visual' || activeTab.value === 'asset' || activeTab.value === 'audio') {
    selectedId.value = String(objectId)
    return
  }
  if (activeTab.value === 'activity') {
    return selectActivity(objectId)
  }
  if (activeTab.value === 'lingjie') {
    return selectLingjieFeature(objectId)
  }
  if (activeTab.value === 'digitdoor') {
    return selectDigitDoorCharacter(objectId)
  }
  if (activeTab.value === 'digitdoor_level') {
    return selectDigitDoorLevel(objectId)
  }
  if (activeTab.value === 'digitdoor_enhance') {
    return selectDigitDoorEnhanceGroup(objectId)
  }
  if (activeTab.value === 'doupotd') {
    return selectDoupoTDPartner(objectId)
  }
  if (activeTab.value === 'doupotd_reward') {
    return selectDoupoTDRewardConfig(String(objectId))
  }
  return selectGongfa(objectId)
}

function reloadFromFirstPage() {
  page.value = 1
  clearDetailCaches()
  loadCurrentCards()
}

function clearDetailCaches() {
  gongfaDetailCache.clear()
  gongfaHomeMakeStaticDetailCache.clear()
  gongfaHomeMakeBuffParameterSemanticsCache.clear()
  gongfaHomeMakeFormulaCatalogCache.clear()
  gongfaSpecialFazeCatalogCache.clear()
  itemDetailCache.clear()
  itemDetailRefreshAttempts.clear()
  activityDetailCache.clear()
  lingjieDetailCache.clear()
  digitDoorDetailCache.clear()
  digitDoorLevelDetailCache.clear()
  digitDoorEnhanceDetailCache.clear()
  doupoTDDetailCache.clear()
  doupoTDRewardDetailCache.clear()
}

function clearSelectedDetailsForReload() {
  selectedCard.value = null
  selectedItem.value = null
  selectedActivity.value = null
  selectedLingjieCard.value = null
  selectedDigitDoorCharacter.value = null
  selectedDigitDoorLevel.value = null
  selectedDigitDoorStage.value = null
  selectedDigitDoorEnhanceGroup.value = null
  selectedDoupoTDPartner.value = null
  selectedDoupoTDReward.value = null
  clearHomeMakeStaticDetail()
}

function revokeVisualSimilarityPreview() {
  if (!visualSimilarityPreviewUrl.value) return
  URL.revokeObjectURL(visualSimilarityPreviewUrl.value)
  visualSimilarityPreviewUrl.value = ''
}

function clearVisualSimilaritySearch(options: { reload?: boolean } = {}) {
  const hadImage = Boolean(visualSimilarityFile.value)
  visualSimilarityFile.value = null
  visualSimilaritySourceName.value = ''
  revokeVisualSimilarityPreview()
  if (options.reload !== false && hadImage && activeTab.value === 'visual') {
    reloadFromFirstPage()
  }
}

function getClipboardImageExtension(type: string) {
  const subtype = String(type || '').split('/')[1] || 'png'
  return subtype === 'jpeg' ? 'jpg' : subtype
}

function applyVisualSimilarityImage(file: File) {
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('剪贴板里不是图片')
    return
  }
  revokeVisualSimilarityPreview()
  visualSimilarityFile.value = file
  visualSimilaritySourceName.value = file.name
  visualSimilarityPreviewUrl.value = URL.createObjectURL(file)
  searchHistoryVisible.value = false
  page.value = 1
  loadCurrentCards()
}

function handleVisualSimilarityPaste(event: ClipboardEvent) {
  if (activeTab.value !== 'visual') return
  const items = event.clipboardData?.items
  if (!items) return
  for (const item of Array.from(items)) {
    if (!item.type.startsWith('image/')) continue
    const blob = item.getAsFile()
    if (!blob) continue
    event.preventDefault()
    const file = new File([blob], `剪贴板截图.${getClipboardImageExtension(blob.type || item.type)}`, { type: blob.type || item.type })
    applyVisualSimilarityImage(file)
    return
  }
}

function refreshCurrentCards() {
  clearDetailCaches()
  clearSelectedDetailsForReload()
  if (activeTab.value === 'gongfa') {
    void loadHomeMakeBuffOverview({ force: true })
  }
  loadCurrentCards({ keepSelection: true })
}

function executeSearchFromFirstPage() {
  if (activeTab.value === 'visual' && visualSimilarityFile.value) {
    clearVisualSimilaritySearch({ reload: false })
  }
  recordSearchHistory()
  reloadFromFirstPage()
  searchHistoryVisible.value = false
}

function handleQueryClear() {
  if (activeTab.value === 'visual' && visualSimilarityFile.value) {
    clearVisualSimilaritySearch({ reload: false })
  }
  reloadFromFirstPage()
  void nextTick(() => openSearchHistory())
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
  closeContextMenu()
  if (isAuxiliaryWikiTab(activeTab.value)) {
    selectedAuxiliaryTab.value = activeTab.value
  }
  if (internalTabNavigation) return
  page.value = 1
  sortMode.value = activeTab.value === 'visual' || activeTab.value === 'asset' || activeTab.value === 'audio' || activeTab.value === 'digitdoor' || activeTab.value === 'digitdoor_level' || activeTab.value === 'digitdoor_enhance' || activeTab.value === 'doupotd' || activeTab.value === 'doupotd_reward' || activeTab.value === 'protocol' || activeTab.value === 'packet' ? 'default' : sortMode.value
  total.value = 0
  selectedId.value = ''
  selectedCard.value = null
  selectedItem.value = null
  selectedActivity.value = null
  selectedLingjieCard.value = null
  selectedDigitDoorCharacter.value = null
  selectedDigitDoorLevel.value = null
  selectedDigitDoorStage.value = null
  selectedDigitDoorEnhanceGroup.value = null
  selectedDoupoTDPartner.value = null
  selectedDoupoTDReward.value = null
  loadCurrentCards()
}

function handleTopTabChange() {
  handleTabChange()
}

function selectProtocolRow(row: FanxiuProtocolSemanticRow) {
  selectedId.value = row.packet
}

function selectPacketCategory(row: FanxiuTcpBusinessCategorySummary) {
  if (selectedPacketCategory.value === row.category) return
  selectedPacketCategory.value = row.category
  packetProtocolDetails.value = []
  void loadPacketProtocolCategoryDetail(row.category)
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

function applyActivityKindFilter(value: string) {
  activityKindFilter.value = value
  reloadFromFirstPage()
}

function applyActivityTimeFilter(value: string) {
  activityTimeFilter.value = value
  reloadFromFirstPage()
}

function applyActivityTypeFilter(value: string) {
  activityTypeFilter.value = value
  reloadFromFirstPage()
}

function applyAudioKindFilter(value: string) {
  audioKindFilter.value = normalizeAudioKindFilter(value)
  reloadFromFirstPage()
}

function applyVisualAssetGroupFilter(value: string) {
  visualAssetGroupFilter.value = normalizeVisualAssetGroupFilter(value)
  reloadFromFirstPage()
}

function applyStaticAssetGroupFilter(value: string) {
  staticAssetGroupFilter.value = normalizeStaticAssetGroupFilter(value)
  reloadFromFirstPage()
}

function applyProtocolFeature(value: string) {
  protocolFeature.value = value || 'bluestarsea'
  protocolRoleFilter.value = ''
  protocolOperationFilter.value = ''
  reloadFromFirstPage()
}

function applyProtocolRoleFilter(value: string) {
  protocolRoleFilter.value = value
  reloadFromFirstPage()
}

function applyProtocolOperationFilter(value: string) {
  protocolOperationFilter.value = value
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
  activityKindFilter,
  activityTimeFilter,
  activityTypeFilter,
  visualAssetGroupFilter,
  staticAssetCatalogView,
  staticAssetGroupFilter,
  audioKindFilter,
  protocolFeature,
  protocolRoleFilter,
  protocolOperationFilter,
  sortMode,
  page,
  pageSize,
  selectedId,
], persistPageConfig)
watch(activeTab, tab => {
  if (isAuxiliaryWikiTab(tab)) {
    selectedAuxiliaryTab.value = tab
  }
  if (tab !== 'asset') {
    staticAssetPreviewManifest.value = null
    loadingStaticAssetPreview.value = false
  }
})
watch([activeTab, selectedId], syncRouteState)
watch(
  () => [
    activeTab.value,
    selectedId.value,
    itemItems.value.find(item => String(item.id) === selectedId.value)?.effect_detail_preview ?? '',
    selectedItem.value?.id ?? '',
    selectedItem.value?.effect_details?.length ?? 0,
    loadingDetail.value,
  ],
  ensureSelectedItemDetailFresh,
  { flush: 'post' },
)
watch(selectedStaticAsset, item => {
  void loadStaticAssetPreviewManifest(item)
})
watch(
  () => [route.query.tab, route.query.id, route.query.q],
  () => {
    if (applyRouteState()) {
      void loadCurrentCards({ keepSelection: Boolean(selectedId.value) })
    }
  },
)
onMounted(() => {
  window.addEventListener('keydown', handleGlobalContextMenuKeydown)
  window.addEventListener('paste', handleVisualSimilarityPaste)
  window.addEventListener('scroll', closeContextMenu, true)
  window.addEventListener('resize', closeContextMenu)
  loadPageConfig()
  loadSearchHistory()
  applyRouteState()
  void loadWikiLinkIndex()
  loadCurrentCards({ keepSelection: Boolean(selectedId.value) })
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalContextMenuKeydown)
  window.removeEventListener('paste', handleVisualSimilarityPaste)
  window.removeEventListener('scroll', closeContextMenu, true)
  window.removeEventListener('resize', closeContextMenu)
  revokeVisualSimilarityPreview()
})
</script>

<template>
  <FanxiuResourceHoverScope>
  <div class="fanxiu-wiki-page" @click="closeContextMenu">
    <header class="page-header">
      <div>
        <h2>凡修图鉴</h2>
      </div>
      <el-button :icon="Refresh" :loading="loadingList || loadingHomeMakeBuffOverview" @click="refreshCurrentCards">刷新</el-button>
    </header>

    <div @click.capture="handleWikiTabHeaderClick" @contextmenu.capture="handleWikiTabHeaderContextMenu">
      <el-tabs v-model="activeTopTab" class="wiki-tabs" @tab-change="handleTopTabChange">
        <el-tab-pane v-for="tab in TOP_WIKI_TABS" :key="tab.key" :label="tab.label" :name="tab.key" />
      </el-tabs>
    </div>

    <div
      v-if="showAuxiliaryTabs"
      @click.capture="handleWikiTabHeaderClick"
      @contextmenu.capture="handleWikiTabHeaderContextMenu"
    >
      <el-tabs v-model="activeTab" class="wiki-tabs wiki-secondary-tabs" @tab-change="handleTabChange">
        <el-tab-pane v-for="tab in AUXILIARY_WIKI_TABS" :key="tab.key" :label="tab.label" :name="tab.key" />
      </el-tabs>
    </div>

    <div class="toolbar">
      <el-popover
        v-model:visible="searchHistoryVisible"
        trigger="manual"
        placement="bottom-start"
        :width="420"
        popper-class="fanxiu-search-history-popover"
      >
        <template #reference>
          <el-input
            v-model="query"
            class="query-input"
            clearable
            :placeholder="searchPlaceholder"
            :prefix-icon="Search"
            @focus="openSearchHistory"
            @click="openSearchHistory"
            @blur="scheduleCloseSearchHistory"
            @input="openSearchHistory"
            @keyup.enter="executeSearchFromFirstPage"
            @clear="handleQueryClear"
          />
        </template>
        <div class="search-history-panel" @mousedown.prevent>
          <div class="search-history-header">
            <span>最近搜索</span>
            <button type="button" @click="clearCurrentSearchHistory">清空</button>
          </div>
          <button
            v-for="text in visibleSearchHistory"
            :key="text"
            class="search-history-item"
            type="button"
            @click="chooseSearchHistory(text)"
          >{{ text }}</button>
        </div>
      </el-popover>
      <el-button type="primary" :icon="Search" :loading="loadingList" @click="executeSearchFromFirstPage">搜索</el-button>
      <el-select
        v-if="activeTab === 'digitdoor_level'"
        v-model="digitDoorStageFilter"
        class="stage-filter-select"
        clearable
        placeholder="全部章节"
        @change="reloadFromFirstPage"
        @clear="reloadFromFirstPage"
      >
        <el-option
          v-for="option in digitDoorStageOptions"
          :key="String(option.id)"
          :label="`${option.name || option.id} ${option.level_count ? `(${option.level_count})` : ''}`"
          :value="String(option.id)"
        />
      </el-select>
      <el-button
        v-if="activeTab !== 'visual' && activeTab !== 'asset' && activeTab !== 'audio' && activeTab !== 'lingjie' && activeTab !== 'digitdoor' && activeTab !== 'digitdoor_level' && activeTab !== 'digitdoor_enhance' && activeTab !== 'doupotd' && activeTab !== 'doupotd_reward' && activeTab !== 'protocol' && activeTab !== 'packet'"
        class="sort-mode-button"
        :class="{ active: sortMode !== 'default' }"
        :title="`点击切换到 ${nextSortModeLabel}`"
        @click="cycleSortMode"
      >{{ activeSortModeLabel }}</el-button>
      <span class="result-count">{{ total }} 个对象</span>
    </div>

    <div v-if="activeTab === 'visual' && visualSimilarityFile" class="visual-similarity-strip">
      <el-image
        v-if="visualSimilarityPreviewUrl"
        class="visual-similarity-thumb"
        :src="visualSimilarityPreviewUrl"
        :preview-src-list="visualSimilarityPreviewList"
        fit="contain"
        preview-teleported
        hide-on-click-modal
        title="点击放大"
      />
      <span>相似图：{{ visualSimilaritySourceName }}</span>
      <small v-if="visualManifest?.stats?.prefiltered">候选 {{ visualManifest.stats.prefiltered }}</small>
      <button type="button" title="恢复文本搜索" @click="clearVisualSimilaritySearch()">
        <Close />
      </button>
    </div>

    <div v-if="activeTab === 'visual'" class="facet-panel visual-asset-panel">
      <div class="facet-row">
        <span class="facet-label">类型</span>
        <span class="facet-options">
          <button
            v-for="option in visualAssetGroupFilterOptions"
            :key="option.value || 'all'"
            class="facet-option"
            :class="{ active: visualAssetGroupFilter === option.value }"
            :disabled="option.value !== visualAssetGroupFilter && option.count <= 0"
            type="button"
            @click="applyVisualAssetGroupFilter(option.value)"
          >
            <span class="facet-option-label">{{ option.label }}</span>
            <small>{{ option.count }}</small>
          </button>
        </span>
      </div>
    </div>

    <div v-if="activeTab === 'asset'" class="facet-panel static-asset-panel">
      <div class="facet-row">
        <span class="facet-label">类型</span>
        <span class="facet-options">
          <button
            v-for="option in staticAssetGroupFilterOptions"
            :key="option.value || 'all'"
            class="facet-option"
            :class="{ active: staticAssetGroupFilter === option.value }"
            :disabled="option.value !== staticAssetGroupFilter && option.count <= 0"
            type="button"
            @click="applyStaticAssetGroupFilter(option.value)"
          >
            <span class="facet-option-label">{{ option.label }}</span>
            <small>{{ option.count }}</small>
          </button>
        </span>
      </div>
    </div>

    <div v-if="activeTab === 'audio'" class="facet-panel audio-kind-panel">
      <div class="facet-row">
        <span class="facet-label">类型</span>
        <span class="facet-options">
          <button
            v-for="option in audioKindFilterOptions"
            :key="option.value || 'all'"
            class="facet-option"
            :class="{ active: audioKindFilter === option.value }"
            :disabled="option.value !== audioKindFilter && option.count <= 0"
            type="button"
            @click="applyAudioKindFilter(option.value)"
          >
            <span class="facet-option-label">{{ option.label }}</span>
            <small>{{ option.count }}</small>
          </button>
        </span>
      </div>
    </div>

    <div v-if="activeTab !== 'visual' && activeTab !== 'asset' && activeTab !== 'audio' && activeTab !== 'lingjie' && activeTab !== 'digitdoor' && activeTab !== 'digitdoor_level' && activeTab !== 'digitdoor_enhance' && activeTab !== 'doupotd' && activeTab !== 'doupotd_reward' && activeTab !== 'packet'" class="facet-panel">
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
              v-for="option in getVisibleFacetOptions('gongfa:quality-grade', gongfaQualityGradeFacetOptions, gongfaQualityGradeFilter)"
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
            <button
              v-if="shouldShowFacetToggle('gongfa:quality-grade', gongfaQualityGradeFacetOptions, gongfaQualityGradeFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('gongfa:quality-grade')"
            >{{ getFacetToggleLabel('gongfa:quality-grade', gongfaQualityGradeFacetOptions, gongfaQualityGradeFilter) }}</button>
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
              v-for="option in getVisibleFacetOptions('gongfa:quality-family', gongfaQualityFamilyFacetOptions, gongfaQualityFamilyFilter)"
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
            <button
              v-if="shouldShowFacetToggle('gongfa:quality-family', gongfaQualityFamilyFacetOptions, gongfaQualityFamilyFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('gongfa:quality-family')"
            >{{ getFacetToggleLabel('gongfa:quality-family', gongfaQualityFamilyFacetOptions, gongfaQualityFamilyFilter) }}</button>
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
              v-for="option in getVisibleFacetOptions('gongfa:skill-type', gongfaSkillTypeFacetOptions, gongfaSkillTypeFilter)"
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
            <button
              v-if="shouldShowFacetToggle('gongfa:skill-type', gongfaSkillTypeFacetOptions, gongfaSkillTypeFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('gongfa:skill-type')"
            >{{ getFacetToggleLabel('gongfa:skill-type', gongfaSkillTypeFacetOptions, gongfaSkillTypeFilter) }}</button>
          </span>
        </div>
      </template>
      <template v-else-if="activeTab === 'activity'">
        <div class="facet-row">
          <span class="facet-label">形态</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !activityKindFilter }"
              type="button"
              @click="applyActivityKindFilter('')"
            >全部</button>
            <button
              v-for="option in getVisibleFacetOptions('activity:kind', activityKindFacetOptions, activityKindFilter)"
              :key="option.value"
              class="facet-option"
              :class="{ active: activityKindFilter === option.value }"
              :disabled="isFacetOptionDisabled(option, activityKindFilter)"
              type="button"
              @click="applyActivityKindFilter(option.value)"
            >
              <span class="facet-option-label">{{ option.label }}</span>
              <small>{{ option.count }}</small>
            </button>
            <button
              v-if="shouldShowFacetToggle('activity:kind', activityKindFacetOptions, activityKindFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('activity:kind')"
            >{{ getFacetToggleLabel('activity:kind', activityKindFacetOptions, activityKindFilter) }}</button>
          </span>
        </div>
        <div class="facet-row">
          <span class="facet-label">时间</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !activityTimeFilter }"
              type="button"
              @click="applyActivityTimeFilter('')"
            >全部</button>
            <button
              v-for="option in getVisibleFacetOptions('activity:time', activityTimeFacetOptions, activityTimeFilter)"
              :key="option.value"
              class="facet-option"
              :class="{ active: activityTimeFilter === option.value }"
              :disabled="isFacetOptionDisabled(option, activityTimeFilter)"
              type="button"
              @click="applyActivityTimeFilter(option.value)"
            >
              <span class="facet-option-label">{{ option.label }}</span>
              <small>{{ option.count }}</small>
            </button>
            <button
              v-if="shouldShowFacetToggle('activity:time', activityTimeFacetOptions, activityTimeFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('activity:time')"
            >{{ getFacetToggleLabel('activity:time', activityTimeFacetOptions, activityTimeFilter) }}</button>
          </span>
        </div>
        <div class="facet-row">
          <span class="facet-label">玩法</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !activityTypeFilter }"
              type="button"
              @click="applyActivityTypeFilter('')"
            >全部</button>
            <button
              v-for="option in getVisibleFacetOptions('activity:type', activityTypeFacetOptions, activityTypeFilter)"
              :key="option.value"
              class="facet-option"
              :class="{ active: activityTypeFilter === option.value }"
              :disabled="isFacetOptionDisabled(option, activityTypeFilter)"
              type="button"
              @click="applyActivityTypeFilter(option.value)"
            >
              <span class="facet-option-label">{{ option.label }}</span>
              <small>{{ option.count }}</small>
            </button>
            <button
              v-if="shouldShowFacetToggle('activity:type', activityTypeFacetOptions, activityTypeFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('activity:type')"
            >{{ getFacetToggleLabel('activity:type', activityTypeFacetOptions, activityTypeFilter) }}</button>
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
              v-for="option in getVisibleFacetOptions('item:quality', itemQualityFacetOptions, itemQualityFilter)"
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
            <button
              v-if="shouldShowFacetToggle('item:quality', itemQualityFacetOptions, itemQualityFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('item:quality')"
            >{{ getFacetToggleLabel('item:quality', itemQualityFacetOptions, itemQualityFilter) }}</button>
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
              v-for="option in getVisibleFacetOptions('item:type', itemTypeFacetOptions, itemTypeFilter)"
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
            <button
              v-if="shouldShowFacetToggle('item:type', itemTypeFacetOptions, itemTypeFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('item:type')"
            >{{ getFacetToggleLabel('item:type', itemTypeFacetOptions, itemTypeFilter) }}</button>
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
              v-for="option in getVisibleFacetOptions('item:sub-type', itemSubTypeFacetOptions, itemSubTypeFilter)"
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
            <button
              v-if="shouldShowFacetToggle('item:sub-type', itemSubTypeFacetOptions, itemSubTypeFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('item:sub-type')"
            >{{ getFacetToggleLabel('item:sub-type', itemSubTypeFacetOptions, itemSubTypeFilter) }}</button>
          </span>
        </div>
      </template>
      <template v-else-if="activeTab === 'protocol'">
        <div class="facet-row">
          <span class="facet-label">协议</span>
          <span class="facet-options">
            <button
              v-for="option in protocolFeatures"
              :key="option.key"
              class="facet-option"
              :class="{ active: protocolFeature === option.key }"
              type="button"
              @click="applyProtocolFeature(option.key)"
            >{{ option.title }}</button>
          </span>
        </div>
        <div class="facet-row">
          <span class="facet-label">角色</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !protocolRoleFilter }"
              type="button"
              @click="applyProtocolRoleFilter('')"
            >全部</button>
            <button
              v-for="option in getVisibleFacetOptions('protocol:role', protocolRoleFacetOptions, protocolRoleFilter)"
              :key="option.value"
              class="facet-option"
              :class="{ active: protocolRoleFilter === option.value }"
              :disabled="isFacetOptionDisabled(option, protocolRoleFilter)"
              type="button"
              @click="applyProtocolRoleFilter(option.value)"
            >
              <span class="facet-option-label">{{ option.label }}</span>
              <small>{{ option.count }}</small>
            </button>
            <button
              v-if="shouldShowFacetToggle('protocol:role', protocolRoleFacetOptions, protocolRoleFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('protocol:role')"
            >{{ getFacetToggleLabel('protocol:role', protocolRoleFacetOptions, protocolRoleFilter) }}</button>
          </span>
        </div>
        <div class="facet-row">
          <span class="facet-label">操作</span>
          <span class="facet-options">
            <button
              class="facet-option"
              :class="{ active: !protocolOperationFilter }"
              type="button"
              @click="applyProtocolOperationFilter('')"
            >全部</button>
            <button
              v-for="option in getVisibleFacetOptions('protocol:operation', protocolOperationFacetOptions, protocolOperationFilter)"
              :key="option.value"
              class="facet-option"
              :class="{ active: protocolOperationFilter === option.value }"
              :disabled="isFacetOptionDisabled(option, protocolOperationFilter)"
              type="button"
              @click="applyProtocolOperationFilter(option.value)"
            >
              <span class="facet-option-label">{{ option.label }}</span>
              <small>{{ option.count }}</small>
            </button>
            <button
              v-if="shouldShowFacetToggle('protocol:operation', protocolOperationFacetOptions, protocolOperationFilter)"
              class="facet-option facet-more-option"
              type="button"
              @click="toggleFacetRow('protocol:operation')"
            >{{ getFacetToggleLabel('protocol:operation', protocolOperationFacetOptions, protocolOperationFilter) }}</button>
          </span>
        </div>
      </template>
    </div>

    <section
      v-if="activeTab === 'gongfa' && (loadingHomeMakeBuffOverview || homeMakeBuffOverviewRawGroups.length)"
      class="homemake-overview"
    >
      <div class="homemake-overview-head">
        <h3>仙书机制总览</h3>
        <span v-if="homeMakeBuffOverviewCountText">{{ homeMakeBuffOverviewCountText }}</span>
        <span v-else-if="loadingHomeMakeBuffOverview">解析中</span>
        <el-input
          v-model="homeMakeBuffOverviewQuery"
          :prefix-icon="Search"
          clearable
          class="homemake-overview-filter"
          placeholder="筛机制 / 功法 / 技能"
        />
      </div>
      <div v-if="loadingHomeMakeBuffOverview" class="homemake-overview-loading">正在读取机制分组...</div>
      <div v-else-if="homeMakeBuffOverviewGroups.length" class="homemake-overview-list">
        <article
          v-for="group in homeMakeBuffOverviewGroups"
          :key="`overview-${group.group_key}`"
          class="homemake-overview-row"
        >
          <div class="homemake-overview-main">
            <div class="homemake-overview-title">
              <strong>{{ group.buff_name }}</strong>
              <span v-if="group.side_jie_names">{{ group.side_jie_names }}</span>
            </div>
            <div class="homemake-overview-desc" v-html="renderFanxiuText(group.buff_desc)" />
            <div class="homemake-overview-meta">
              <span v-if="group.gongfa_names">{{ group.gongfa_names }}</span>
              <span v-for="tag in getHomeMakeBuffTags(group)" :key="`${group.group_key}-${tag}`">{{ tag }}</span>
            </div>
          </div>
          <div v-if="group.links.length" class="homemake-overview-links">
            <button
              v-for="link in group.links.slice(0, 4)"
              :key="`${group.group_key}-overview-${link.field}-${link.target_table}-${link.target_id}`"
              type="button"
              class="homemake-buff-link-chip"
              :class="{ actionable: canNavigateHomeMakeBuffLink(link) }"
              :disabled="!canNavigateHomeMakeBuffLink(link)"
              :title="getHomeMakeBuffLinkTitle(link)"
              @click.stop="void navigateHomeMakeBuffLink(link)"
            >
              <b>{{ getHomeMakeBuffLinkLabel(link) }}</b>
              <small>{{ getHomeMakeBuffLinkMeta(link) }}</small>
            </button>
            <em v-if="group.link_count > 4">+{{ group.link_count - 4 }}</em>
          </div>
        </article>
      </div>
      <div v-else class="homemake-overview-empty">没有匹配机制</div>
    </section>

    <div v-if="activeTab === 'packet'" class="object-workspace packet-wiki-workspace">
      <aside class="object-list" v-loading="loadingList">
        <div class="object-list-scroll">
          <button
            v-for="item in protocolBusinessCategories"
            :key="item.category"
            class="protocol-row"
            :class="{ selected: item.category === selectedPacketCategory }"
            type="button"
            @click="selectPacketCategory(item)"
          >
            <span class="protocol-row-title">{{ item.category }}</span>
            <span class="protocol-row-meta">{{ item.count }} 条样本</span>
            <span class="protocol-row-preview">{{ item.meaning }}</span>
          </button>
          <div v-if="isPacketWikiInitialLoading" class="empty-state">加载中</div>
          <div v-else-if="!protocolBusinessCategories.length" class="empty-state">还没有抓包样本</div>
        </div>
      </aside>

      <main class="object-detail packet-wiki-detail" v-loading="loadingDetail">
        <template v-if="selectedPacketCategoryRow">
          <section class="packet-doc-head">
            <h3>
              {{ selectedPacketCategoryRow.category }}
              <span class="packet-doc-sample-count">{{ selectedPacketCategoryRow.count }} 条样本 · {{ selectedPacketCategoryRow.protocols.length }} 个业务包</span>
            </h3>
            <p>{{ selectedPacketCategoryRow.meaning }}</p>
          </section>
          <section class="packet-doc-section">
            <h4>业务包</h4>
            <article
              v-for="item in packetProtocolDetails"
              :key="item.name"
              class="packet-protocol-doc"
            >
              <header>
                <el-checkbox
                  :model-value="isPacketProtocolChecked(item.name)"
                  @change="value => togglePacketProtocolVisibility(item.name, Boolean(value))"
                />
                <strong>{{ item.name }}</strong>
                <span>{{ item.meaning }}</span>
                <button class="packet-count-link" type="button" @click="togglePacketProtocolSamples(item)">
                  {{ item.count }} 条
                </button>
              </header>
              <div v-if="item.samples[0]" class="packet-protocol-example">
                <div
                  class="packet-translation-example"
                  :class="{ upstream: item.samples[0].direction === 'c2s' }"
                >
                  <span class="packet-example-label">翻译结果</span>
                  <span class="packet-sample-text">
                    <template
                      v-for="(segment, index) in packetBusinessDisplaySegments(item.samples[0])"
                      :key="`${item.samples[0].id}-translation-${index}`"
                    >
                      <span :class="{ 'packet-business-param': segment.kind === 'param' }">{{ segment.text }}</span>
                    </template>
                  </span>
                  <span class="packet-sample-meta">{{ packetDirectionLabel(item.samples[0].direction) }}</span>
                </div>
                <div
                  v-for="table in packetSampleTables(item.samples[0])"
                  :key="`${item.samples[0].id}-${table.title}`"
                  class="packet-table-example"
                >
                  <span class="packet-example-label">{{ table.title }}</span>
                  <div class="packet-sample-table-wrap">
                    <table class="packet-sample-table">
                      <thead>
                        <tr>
                          <th v-for="column in table.columns" :key="column.key">{{ column.label }}</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(row, rowIndex) in table.rows" :key="rowIndex">
                          <td v-for="column in table.columns" :key="column.key">
                            <template v-if="row[column.key]">
                              {{ row[column.key] }}<span
                                v-if="translatePacketCellValue(row[column.key], column.key, table.fieldLabels)"
                                class="packet-cell-meaning"
                              >（{{ translatePacketCellValue(row[column.key], column.key, table.fieldLabels) }}）</span>
                            </template>
                            <template v-else>-</template>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
                <div class="packet-json-example">
                  <span class="packet-example-label">JSON 示例</span>
                  <pre>{{ packetProtocolSampleJson(item.samples[0]) }}</pre>
                </div>
                <div
                  v-if="expandedPacketProtocol === item.name"
                  class="packet-all-samples"
                  v-loading="packetProtocolSamplesLoading"
                >
                  <div
                    v-for="entry in packetProtocolSamples"
                    :key="entry.id"
                    class="packet-sample-detail"
                  >
                    <div class="packet-sample-detail-head">
                      <strong>{{ formatTcpBusinessTime(entry.decoded_at) }}</strong>
                      <span>{{ packetDirectionLabel(entry.direction) }}</span>
                    </div>
                    <div class="packet-sample-detail-text">
                      <template
                        v-for="(segment, index) in packetEntryDisplaySegments(entry)"
                        :key="`${entry.id}-all-${index}`"
                      >
                        <span :class="{ 'packet-business-param': segment.kind === 'param' }">{{ segment.text }}</span>
                      </template>
                    </div>
                    <details>
                      <summary>JSON</summary>
                      <pre>{{ packetEntryJson(entry) }}</pre>
                    </details>
                  </div>
                  <div v-if="!packetProtocolSamplesLoading && !packetProtocolSamples.length" class="empty-state">没有样本</div>
                </div>
              </div>
            </article>
          </section>
        </template>
        <div v-else-if="isPacketWikiInitialLoading" class="empty-state">加载中</div>
        <div v-else class="empty-state">还没有抓包样本</div>
      </main>
    </div>

    <div v-else class="object-workspace" :class="{ 'protocol-workspace': activeTab === 'protocol' }">
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
              @contextmenu="handleObjectContextMenu($event, item.id)"
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
          <template v-else-if="activeTab === 'visual'">
            <button
              v-for="item in visualItems"
              :key="getVisualAssetKey(item)"
              class="object-row visual-asset-row"
              :class="{ selected: getVisualAssetKey(item) === selectedId }"
              type="button"
              @click="selectObject(getVisualAssetKey(item))"
            >
              <span class="object-row-icon visual-thumb">
                <img
                  v-if="item.media_url"
                  :src="item.media_url"
                  :alt="item.name"
                  loading="lazy"
                  @error="hideBrokenIcon"
                >
              </span>
              <span class="object-row-main">
                <span class="object-row-title">
                  {{ item.name }}
                  <small v-if="item.similarity_rank" class="similarity-rank">#{{ item.similarity_rank }}</small>
                </span>
                <span class="object-row-meta">{{ getVisualSimilarityMeta(item) }}</span>
                <span class="object-row-preview">{{ getVisualAssetPreview(item) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !visualItems.length" class="empty-state">没有匹配图片</div>
          </template>
          <template v-else-if="activeTab === 'asset'">
            <button
              v-for="item in staticAssetItems"
              :key="getStaticAssetKey(item)"
              class="object-row static-asset-row"
              :class="{ selected: getStaticAssetKey(item) === selectedId }"
              type="button"
              @click="selectObject(getStaticAssetKey(item))"
            >
              <span class="object-row-icon static-asset-thumb">
                <img
                  v-if="item.preview_url"
                  :src="item.preview_url"
                  :alt="item.name || item.stem"
                  loading="lazy"
                  @error="hideBrokenIcon"
                >
                <span v-else>{{ getStaticAssetGroupLabel(item.asset_group) }}</span>
              </span>
              <span class="object-row-main">
                <span class="object-row-title">{{ item.name || item.stem }}</span>
                <span class="object-row-meta">{{ getStaticAssetMeta(item) }}</span>
                <span class="object-row-preview">{{ getStaticAssetPreview(item) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !staticAssetItems.length" class="empty-state">没有匹配素材</div>
          </template>
          <template v-else-if="activeTab === 'audio'">
            <button
              v-for="item in audioItems"
              :key="getAudioAssetKey(item)"
              class="object-row audio-asset-row"
              :class="{ selected: getAudioAssetKey(item) === selectedId }"
              type="button"
              @click="selectObject(getAudioAssetKey(item))"
            >
              <span class="audio-row-badge">{{ item.kind || 'audio' }}</span>
              <span class="object-row-main">
                <span class="object-row-title">{{ getAudioAssetTitle(item) }}</span>
                <span class="object-row-meta">{{ getAudioAssetMeta(item) }}</span>
                <span class="object-row-preview">{{ getAudioAssetPreview(item) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !audioItems.length" class="empty-state">没有匹配音乐</div>
          </template>
          <template v-else-if="activeTab === 'activity'">
            <button
              v-for="item in activityItems"
              :key="item.id"
              class="object-row"
              :class="{ selected: String(item.id) === selectedId, stale: item.is_stale }"
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
                  {{ getActivityMeta(item) }}
                  <template v-if="getFirstTimelineShortLabel(item)"> · {{ getFirstTimelineShortLabel(item) }}</template>
                  <template v-if="item.is_stale"> · 旧版保留</template>
                </span>
                <span class="object-row-preview">{{ compactText(item.reward_preview || item.description_preview, 96) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !activityItems.length" class="empty-state">没有匹配活动</div>
          </template>
          <template v-else-if="activeTab === 'lingjie'">
            <button
              v-for="item in lingjieItems"
              :key="item.gongfa_id"
              class="object-row"
              :class="{ selected: String(item.gongfa_id) === selectedId }"
              type="button"
              @click="selectObject(item.gongfa_id)"
              @contextmenu="handleObjectContextMenu($event, item.gongfa_id)"
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
          <template v-else-if="activeTab === 'digitdoor'">
            <button
              v-for="item in digitDoorItems"
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
                <span class="object-row-meta">{{ getDigitDoorMeta(item) }}</span>
                <span class="object-row-preview">{{ compactText(item.skill_description_preview, 96) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !digitDoorItems.length" class="empty-state">没有匹配数字门角色</div>
          </template>
          <template v-else-if="activeTab === 'digitdoor_level'">
            <button
              v-for="item in digitDoorLevelItems"
              :key="item.id"
              class="object-row reward-config-row"
              :class="{ selected: String(item.id) === selectedId }"
              type="button"
              @click="selectObject(item.id)"
            >
              <span class="reward-config-badge">{{ item.stage || '关' }}</span>
              <span class="object-row-main">
                <span class="object-row-title">{{ getDigitDoorLevelTitle(item) }}</span>
                <span class="object-row-meta">{{ getDigitDoorLevelMeta(item) }}</span>
                <span class="object-row-preview">{{ compactText(item.reward_preview || item.recommend_tips, 108) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !digitDoorLevelItems.length" class="empty-state">没有匹配数字门关卡</div>
          </template>
          <template v-else-if="activeTab === 'digitdoor_enhance'">
            <button
              v-for="item in digitDoorEnhanceItems"
              :key="String(item.id ?? item.char_id)"
              class="object-row reward-config-row"
              :class="{ selected: String(item.id ?? item.char_id) === selectedId }"
              type="button"
              @click="selectObject(item.id ?? item.char_id ?? '')"
            >
              <span class="reward-config-badge">{{ item.char_id || '强' }}</span>
              <span class="object-row-main">
                <span class="object-row-title">{{ item.name }}</span>
                <span class="object-row-meta">{{ getDigitDoorEnhanceGroupMeta(item) }}</span>
                <span class="object-row-preview">{{ compactText(item.enhance_preview || item.description_preview, 108) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !digitDoorEnhanceItems.length" class="empty-state">没有匹配数字门强化</div>
          </template>
          <template v-else-if="activeTab === 'doupotd'">
            <button
              v-for="item in doupoTDItems"
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
                <span class="object-row-meta">{{ getDoupoTDMeta(item) }}</span>
                <span class="object-row-preview">{{ compactText(item.skill_description_preview, 96) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !doupoTDItems.length" class="empty-state">没有匹配斗破角色</div>
          </template>
          <template v-else-if="activeTab === 'doupotd_reward'">
            <button
              v-for="item in doupoTDRewardItems"
              :key="getDoupoTDRewardConfigKey(item)"
              class="object-row reward-config-row"
              :class="{ selected: getDoupoTDRewardConfigKey(item) === selectedId }"
              type="button"
              @click="selectObject(getDoupoTDRewardConfigKey(item))"
            >
              <span class="reward-config-badge">{{ getDoupoTDRewardSourceShort(item.source_table) }}</span>
              <span class="object-row-main">
                <span class="object-row-title">{{ getDoupoTDRewardConfigTitle(item) }}</span>
                <span class="object-row-meta">{{ getDoupoTDRewardConfigMeta(item) }}</span>
                <span class="object-row-preview">{{ getDoupoTDRewardConfigPreview(item) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !doupoTDRewardItems.length" class="empty-state">没有匹配斗破奖励</div>
          </template>
          <template v-else-if="activeTab === 'item'">
            <button
              v-for="item in itemItems"
              :key="item.id"
              class="object-row"
              :class="{ selected: String(item.id) === selectedId }"
              type="button"
              @click="selectObject(item.id)"
              @contextmenu="handleObjectContextMenu($event, item.id)"
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
                <span class="object-row-preview">{{ compactText(item.effect_detail_preview || item.effect_preview || item.description_preview, 96) }}</span>
              </span>
            </button>
            <div v-if="!loadingList && !itemItems.length" class="empty-state">没有匹配道具</div>
          </template>
          <template v-else-if="activeTab === 'protocol'">
            <button
              v-for="item in protocolRows"
              :key="item.packet"
              class="protocol-row"
              :class="{ selected: item.packet === selectedId }"
              type="button"
              @click="selectProtocolRow(item)"
            >
              <span class="protocol-row-title">{{ item.packet }}</span>
              <span class="protocol-row-meta">{{ getProtocolRowMeta(item) }}</span>
              <span class="protocol-row-preview">{{ getProtocolRowPreview(item) }}</span>
            </button>
            <div v-if="!loadingList && !protocolRows.length" class="empty-state">没有匹配协议</div>
          </template>
        </div>

        <div v-if="total > 0 && activeTab !== 'protocol'" class="object-pagination">
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
        <template v-if="activeTab === 'visual' && selectedVisualAsset">
          <section class="detail-head visual-detail-head">
            <div class="visual-detail-preview">
              <img
                v-if="selectedVisualAsset.media_url"
                :src="selectedVisualAsset.media_url"
                :alt="selectedVisualAsset.name"
              >
            </div>
            <div class="detail-title">
              <h3>{{ selectedVisualAsset.name }}</h3>
              <div class="detail-meta">
                <span>{{ getVisualAssetMeta(selectedVisualAsset) }}</span>
                <span v-if="selectedVisualAsset.path_id">PathID {{ selectedVisualAsset.path_id }}</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section class="object-section intro-section">
            <h4>资源信息</h4>
            <div class="asset-info-grid">
              <div v-if="selectedVisualAsset.similarity_percent !== undefined">
                <span>相似度</span>
                <strong>{{ getVisualSimilarityLabel(selectedVisualAsset) }}</strong>
              </div>
              <div v-if="selectedVisualAsset.phash_distance !== undefined">
                <span>哈希距离</span>
                <strong>p{{ selectedVisualAsset.phash_distance }} / d{{ selectedVisualAsset.dhash_distance }}</strong>
              </div>
              <div>
                <span>分类</span>
                <strong>{{ getVisualCategoryLabel(selectedVisualAsset.category) }}</strong>
              </div>
              <div>
                <span>来源</span>
                <strong>{{ getVisualSourceKindLabel(selectedVisualAsset.source_kind) }}</strong>
              </div>
              <div>
                <span>尺寸</span>
                <strong>{{ selectedVisualAsset.width }} x {{ selectedVisualAsset.height }}</strong>
              </div>
              <div v-if="selectedVisualAsset.bytes">
                <span>大小</span>
                <strong>{{ formatByteSize(selectedVisualAsset.bytes) }}</strong>
              </div>
            </div>
            <dl class="asset-path-list">
              <div v-if="selectedVisualAsset.atlas_key">
                <dt>Atlas</dt>
                <dd>{{ selectedVisualAsset.atlas_key }}</dd>
              </div>
              <div v-if="selectedVisualAsset.source_path">
                <dt>源路径</dt>
                <dd>{{ selectedVisualAsset.source_path }}</dd>
              </div>
              <div>
                <dt>图鉴路径</dt>
                <dd>{{ selectedVisualAsset.media_path }}</dd>
              </div>
            </dl>
          </section>
        </template>

        <template v-else-if="activeTab === 'asset' && selectedStaticAsset">
          <section class="detail-head static-asset-detail-head">
            <div :class="selectedStaticAsset.semantic_id ? 'static-asset-detail-badge' : 'static-asset-detail-preview'">
              <img
                v-if="!selectedStaticAsset.semantic_id && selectedStaticAsset.preview_url"
                :src="selectedStaticAsset.preview_url"
                :alt="selectedStaticAsset.name || selectedStaticAsset.stem"
                loading="lazy"
                @error="hideBrokenIcon"
              >
              <span v-else>{{ getStaticAssetGroupLabel(selectedStaticAsset.asset_group) }}</span>
            </div>
            <div class="detail-title">
              <h3>{{ selectedStaticAsset.name || selectedStaticAsset.stem }}</h3>
              <div class="detail-meta">
                <span>{{ getStaticAssetMeta(selectedStaticAsset) }}</span>
                <span v-if="selectedStaticAsset.detail_status">已有细节索引</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section
            v-if="loadingStaticAssetPreview || selectedStaticAssetBusinessImages.length || selectedStaticAssetPreviewItems.length"
            class="object-section intro-section static-asset-full-preview-section"
          >
            <h4>{{ selectedStaticAssetBusinessImages.length ? '业务图片' : (selectedStaticAssetOriginalImages.length ? '原始图片' : '预览') }}</h4>
            <div v-if="loadingStaticAssetPreview" class="static-asset-preview-loading">加载预览...</div>
            <div v-else-if="selectedStaticAssetOriginalImages.length" class="static-asset-original-list">
              <figure
                v-for="item in selectedStaticAssetOriginalImages"
                :key="item.media_path"
                class="static-asset-original-figure"
              >
                <a
                  class="static-asset-original-image"
                  :href="item.media_url"
                  target="_blank"
                  rel="noreferrer"
                  :aria-label="`打开图片 ${getStaticAssetPreviewItemTitle(item)}`"
                >
                  <img
                    :src="item.media_url"
                    :alt="getStaticAssetPreviewItemTitle(item)"
                    loading="lazy"
                    @error="hideBrokenIcon"
                  >
                </a>
                <figcaption class="static-asset-preview-caption">
                  <strong>{{ getStaticAssetPreviewItemTitle(item) }}</strong>
                  <span>{{ getStaticAssetPreviewItemMeta(item) }}</span>
                </figcaption>
              </figure>
            </div>
            <div v-else class="static-asset-derived-list">
              <figure
                v-for="item in selectedStaticAssetDerivedPreviews"
                :key="item.media_path"
                class="static-asset-derived-figure"
              >
                <p class="static-asset-derived-note">{{ getStaticAssetDerivedPreviewNote(item) }}</p>
                <a
                  class="static-asset-derived-preview"
                  :href="item.media_url"
                  target="_blank"
                  rel="noreferrer"
                  :aria-label="`打开预览 ${getStaticAssetPreviewItemTitle(item)}`"
                >
                  <img
                    :src="item.media_url"
                    :alt="getStaticAssetPreviewItemTitle(item)"
                    loading="lazy"
                    @error="hideBrokenIcon"
                  >
                </a>
              </figure>
            </div>
          </section>

          <section v-if="selectedStaticAsset.semantic_id" class="object-section intro-section">
            <h4>语义归属</h4>
            <div class="asset-info-grid">
              <div>
                <span>业务类型</span>
                <strong>{{ getStaticAssetGroupLabel(selectedStaticAsset.semantic_group || selectedStaticAsset.asset_group) }}</strong>
              </div>
              <div v-if="selectedStaticAsset.linked_asset_count">
                <span>关联资源</span>
                <strong>{{ selectedStaticAsset.linked_asset_count }}</strong>
              </div>
              <div v-if="selectedStaticAsset.semantic_visual_count">
                <span>业务图片</span>
                <strong>{{ selectedStaticAsset.semantic_visual_count }}</strong>
              </div>
              <div v-if="selectedStaticAsset.semantic_variant_count && selectedStaticAsset.semantic_variant_count > 1">
                <span>配置档位</span>
                <strong>{{ selectedStaticAsset.semantic_variant_count }}</strong>
              </div>
              <div v-if="selectedStaticAsset.linked_asset_groups">
                <span>资源分布</span>
                <strong>{{ selectedStaticAsset.linked_asset_groups }}</strong>
              </div>
            </div>
            <dl class="asset-path-list">
              <div v-if="selectedStaticAsset.semantic_summary">
                <dt>说明</dt>
                <dd>{{ selectedStaticAsset.semantic_summary }}</dd>
              </div>
              <div v-if="selectedStaticAsset.semantic_refs">
                <dt>配置引用</dt>
                <dd>{{ selectedStaticAsset.semantic_refs }}</dd>
              </div>
              <div v-if="selectedStaticAsset.semantic_visual_names">
                <dt>业务图片名</dt>
                <dd>{{ selectedStaticAsset.semantic_visual_names }}</dd>
              </div>
              <div v-if="selectedStaticAsset.semantic_variant_refs">
                <dt>配置档位</dt>
                <dd>{{ selectedStaticAsset.semantic_variant_refs }}</dd>
              </div>
            </dl>
          </section>

          <section v-if="!selectedStaticAsset.semantic_id" class="object-section intro-section">
            <h4>资源信息</h4>
            <div class="asset-info-grid">
              <div>
                <span>类型</span>
                <strong>{{ getStaticAssetGroupLabel(selectedStaticAsset.asset_group) }}</strong>
              </div>
              <div v-if="getStaticAssetVisibleTypeLabel(selectedStaticAsset)">
                <span>对象类型</span>
                <strong>{{ getStaticAssetVisibleTypeLabel(selectedStaticAsset) }}</strong>
              </div>
              <div>
                <span>来源</span>
                <strong>{{ getStaticAssetSourceLabel(selectedStaticAsset.source_kind) }}</strong>
              </div>
              <div>
                <span>目录</span>
                <strong>{{ selectedStaticAsset.category || '-' }}</strong>
              </div>
              <div>
                <span>大小</span>
                <strong>{{ formatByteSize(selectedStaticAsset.bytes) || '-' }}</strong>
              </div>
              <div v-if="selectedStaticAsset.mesh_count">
                <span>Mesh</span>
                <strong>{{ selectedStaticAsset.mesh_count }}</strong>
              </div>
              <div v-if="selectedStaticAsset.material_count">
                <span>材质</span>
                <strong>{{ selectedStaticAsset.material_count }}</strong>
              </div>
              <div v-if="selectedStaticAsset.texture_count">
                <span>贴图</span>
                <strong>{{ selectedStaticAsset.texture_count }}</strong>
              </div>
              <div v-if="selectedStaticAsset.animation_count">
                <span>动画</span>
                <strong>{{ selectedStaticAsset.animation_count }}</strong>
              </div>
              <div v-if="selectedStaticAsset.ui_gameobject_count">
                <span>UI节点</span>
                <strong>{{ selectedStaticAsset.ui_gameobject_count }}</strong>
              </div>
              <div v-if="selectedStaticAsset.unity_object_count">
                <span>Unity对象</span>
                <strong>{{ selectedStaticAsset.unity_object_count }}</strong>
              </div>
            </div>
            <dl class="asset-path-list">
              <div v-if="selectedStaticAsset.unity_object_types">
                <dt>对象类型统计</dt>
                <dd>{{ selectedStaticAsset.unity_object_types }}</dd>
              </div>
              <div v-if="selectedStaticAsset.unity_named_objects">
                <dt>对象名称样本</dt>
                <dd>{{ selectedStaticAsset.unity_named_objects }}</dd>
              </div>
              <div v-if="selectedStaticAsset.unity_script_names">
                <dt>脚本样本</dt>
                <dd>{{ selectedStaticAsset.unity_script_names }}</dd>
              </div>
              <div>
                <dt>资源路径</dt>
                <dd>{{ selectedStaticAsset.relative_path }}</dd>
              </div>
              <div v-if="selectedStaticAsset.unity_parse_status && selectedStaticAsset.unity_parse_status !== 'parsed'">
                <dt>解析状态</dt>
                <dd>{{ selectedStaticAsset.unity_parse_status }} {{ selectedStaticAsset.unity_parse_error || '' }}</dd>
              </div>
              <div v-if="selectedStaticAsset.hash_suffix">
                <dt>Hash</dt>
                <dd>{{ selectedStaticAsset.hash_suffix }}</dd>
              </div>
              <div v-if="selectedStaticAsset.mesh_vertices || selectedStaticAsset.mesh_faces">
                <dt>Mesh统计</dt>
                <dd>顶点 {{ selectedStaticAsset.mesh_vertices || 0 }} / 面 {{ selectedStaticAsset.mesh_faces || 0 }}</dd>
              </div>
            </dl>
          </section>
        </template>

        <template v-else-if="activeTab === 'audio' && selectedAudioAsset">
          <section class="detail-head audio-detail-head">
            <div class="audio-detail-badge">MP3</div>
            <div class="detail-title">
              <h3>{{ getAudioAssetTitle(selectedAudioAsset) }}</h3>
              <div class="detail-meta">
                <span>{{ getAudioAssetMeta(selectedAudioAsset) }}</span>
                <span v-if="selectedAudioAsset.wem_id">WEM {{ selectedAudioAsset.wem_id }}</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section class="object-section intro-section audio-player-section">
            <div class="audio-player-toolbar">
              <el-button
                size="small"
                :icon="TopRight"
                :disabled="!selectedAudioAsset.media_url"
                title="打开独立播放器"
                @click="openAudioIndependentPlayer(selectedAudioAsset)"
              >独立播放</el-button>
            </div>
            <audio
              v-if="selectedAudioAsset.media_url"
              :key="selectedAudioAsset.media_url"
              controls
              preload="metadata"
              :src="selectedAudioAsset.media_url"
            ></audio>
            <div class="asset-info-grid">
              <div>
                <span>时长</span>
                <strong>{{ formatAudioDuration(selectedAudioAsset.duration_seconds) || '-' }}</strong>
              </div>
              <div>
                <span>采样率</span>
                <strong>{{ selectedAudioAsset.sample_rate || '-' }}</strong>
              </div>
              <div>
                <span>声道</span>
                <strong>{{ selectedAudioAsset.channels || '-' }}</strong>
              </div>
              <div>
                <span>WEM大小</span>
                <strong>{{ formatByteSize(selectedAudioAsset.wem_size) || '-' }}</strong>
              </div>
            </div>
            <dl class="asset-path-list">
              <div>
                <dt>Bank</dt>
                <dd>{{ selectedAudioAsset.source_bank }}</dd>
              </div>
              <div>
                <dt>MP3</dt>
                <dd>{{ selectedAudioAsset.relative_mp3_path }}</dd>
              </div>
              <div v-if="selectedAudioAsset.encoding">
                <dt>编码</dt>
                <dd>{{ selectedAudioAsset.encoding }}</dd>
              </div>
            </dl>
          </section>
        </template>

        <template v-else-if="activeTab === 'doupotd_reward' && selectedDoupoTDReward">
          <section class="detail-head reward-config-head">
            <div class="reward-config-detail-badge">
              {{ getDoupoTDRewardSourceShort(selectedDoupoTDReward.source_table) }}
            </div>
            <div class="detail-title">
              <h3>{{ getDoupoTDRewardConfigTitle(selectedDoupoTDReward) }}</h3>
              <div class="detail-meta">
                <span>{{ getDoupoTDRewardSourceLabel(selectedDoupoTDReward.source_table) }}</span>
                <span>ID {{ selectedDoupoTDReward.config_id }}</span>
                <span v-if="selectedDoupoTDReward.reward_count">{{ selectedDoupoTDReward.reward_count }} 项奖励</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section class="object-section reward-boundary-section">
            <div class="section-row">
              <h4>结算边界</h4>
              <span class="section-count">静态配置 / 服务端结果</span>
            </div>
            <div class="reward-boundary-grid">
              <div>
                <strong>配置展示</strong>
                <span>Level.reward / rewardShow</span>
                <small>用于预览和图标渲染</small>
              </div>
              <div>
                <strong>字段解析</strong>
                <span>ITEM -> Item_Item</span>
                <small>code / amount / extraMark 已映射</small>
              </div>
              <div>
                <strong>到账依据</strong>
                <span>rewardResults 回包</span>
                <small>SM_DoupoTDGamePlayer</small>
              </div>
            </div>
          </section>

          <section v-if="selectedDoupoTDReward.items?.length" class="object-section">
            <div class="section-row">
              <h4>奖励列表</h4>
              <span class="section-count">{{ selectedDoupoTDReward.items.length }} 项</span>
            </div>
            <div class="reward-config-item-grid">
              <article
                v-for="(item, index) in selectedDoupoTDReward.items"
                :key="`${selectedId}-${item.item_id}-${index}`"
                class="reward-config-item"
              >
                <strong>{{ getDoupoTDRewardItemText(item) }}</strong>
                <div v-if="getDoupoTDRewardResultBadges(item).length" class="reward-result-badges">
                  <span v-for="badge in getDoupoTDRewardResultBadges(item)" :key="badge">{{ badge }}</span>
                </div>
                <small>{{ getDoupoTDRewardItemMeta(item) }}</small>
                <small v-if="getDoupoTDRewardResultNote(item)" class="reward-result-note">
                  {{ getDoupoTDRewardResultNote(item) }}
                </small>
              </article>
            </div>
          </section>

          <section v-if="getDoupoTDRewardConfigRawRows(selectedDoupoTDReward).length" class="object-section item-field-section">
            <h4>配置字段</h4>
            <dl>
              <template
                v-for="row in getDoupoTDRewardConfigRawRows(selectedDoupoTDReward)"
                :key="row.label"
              >
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </template>
            </dl>
          </section>
        </template>
        <template v-else-if="activeTab === 'digitdoor_level' && selectedDigitDoorLevel">
          <section class="detail-head reward-config-head">
            <div class="reward-config-detail-badge">
              {{ selectedDigitDoorLevel.stage || '关' }}
            </div>
            <div class="detail-title">
              <h3>{{ getDigitDoorLevelTitle(selectedDigitDoorLevel) }}</h3>
              <div class="detail-meta">
                <span v-if="getDigitDoorStageName(selectedDigitDoorStage)">{{ getDigitDoorStageName(selectedDigitDoorStage) }}</span>
                <span>ID {{ selectedDigitDoorLevel.id }}</span>
                <span v-if="selectedDigitDoorLevel.layer">第 {{ selectedDigitDoorLevel.layer }} 关</span>
                <span v-if="selectedDigitDoorLevel.door_count">{{ selectedDigitDoorLevel.door_count }} 个刷门点</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section v-if="selectedDigitDoorLevel.recommend_tips || selectedDigitDoorLevel.reward_show_title" class="object-section intro-section">
            <h4 v-html="renderFanxiuText(getDigitDoorLevelRewardTitleRich(selectedDigitDoorLevel) || '关卡概览', { tone: 'light' })" />
            <div v-if="selectedDigitDoorLevel.recommend_tips" class="plain-rich-text" v-html="renderFanxiuText(selectedDigitDoorLevel.recommend_tips, { tone: 'light' })" />
          </section>

          <section v-if="selectedDigitDoorLevel.reward_items?.length" class="object-section">
            <div class="section-row">
              <h4>通关奖励</h4>
              <span class="section-count">{{ selectedDigitDoorLevel.reward_items.length }} 项</span>
            </div>
            <div class="reward-config-item-grid">
              <article
                v-for="(item, index) in selectedDigitDoorLevel.reward_items"
                :key="`${selectedDigitDoorLevel.id}-reward-${item.id}-${index}`"
                class="reward-config-item"
              >
                <strong>{{ getDigitDoorLevelRewardText(item) }}</strong>
                <small>{{ getDigitDoorLevelRewardMeta(item) }}</small>
                <div v-if="getDigitDoorRewardResultBadges(item).length" class="reward-result-badges">
                  <span v-for="badge in getDigitDoorRewardResultBadges(item)" :key="badge">{{ badge }}</span>
                </div>
                <small v-if="getDigitDoorRewardResultNote(item)" class="reward-result-note">
                  {{ getDigitDoorRewardResultNote(item) }}
                </small>
                <small v-if="item.item?.description">{{ compactText(item.item.description, 120) }}</small>
              </article>
            </div>
          </section>

          <section v-if="selectedDigitDoorStage?.reward_items?.length" class="object-section">
            <div class="section-row">
              <h4>章节预览奖励</h4>
              <span class="section-count">{{ selectedDigitDoorStage.reward_items.length }} 项</span>
            </div>
            <div class="reward-config-item-grid">
              <article
                v-for="(item, index) in selectedDigitDoorStage.reward_items"
                :key="`${selectedDigitDoorStage.id}-stage-reward-${item.id}-${index}`"
                class="reward-config-item"
              >
                <strong>{{ getDigitDoorLevelRewardText(item) }}</strong>
                <small>{{ getDigitDoorLevelRewardMeta(item) }}</small>
                <div v-if="getDigitDoorRewardResultBadges(item).length" class="reward-result-badges">
                  <span v-for="badge in getDigitDoorRewardResultBadges(item)" :key="badge">{{ badge }}</span>
                </div>
                <small v-if="getDigitDoorRewardResultNote(item)" class="reward-result-note">
                  {{ getDigitDoorRewardResultNote(item) }}
                </small>
              </article>
            </div>
          </section>

          <section v-if="selectedDigitDoorLevel.customized_types?.length || selectedDigitDoorLevel.door_type_counts" class="object-section reward-boundary-section">
            <div class="section-row">
              <h4>刷门配置</h4>
              <span class="section-count">Level -> DoorRefreshPoint</span>
            </div>
            <div class="reward-boundary-grid">
              <div>
                <strong>{{ selectedDigitDoorLevel.door_count || 0 }}</strong>
                <span>刷门点</span>
                <small>由 DoorRefreshPoint.level 汇总</small>
              </div>
              <div>
                <strong>{{ (selectedDigitDoorLevel.customized_types || []).join(' / ') || '-' }}</strong>
                <span>customizedType</span>
                <small>{{ selectedDigitDoorLevel.door_refresh?.summary?.pool_semantic_preview || '对应 SkillRefreshEffect 门效果池' }}</small>
              </div>
              <div>
                <strong>{{ selectedDigitDoorLevel.monster?.length || 0 }}</strong>
                <span>怪物配置</span>
                <small>{{ (selectedDigitDoorLevel.monster || []).join(' / ') || '-' }}</small>
              </div>
            </div>
          </section>

          <section v-if="selectedDigitDoorLevel.door_refresh?.effect_pools?.length" class="object-section digitdoor-door-pool-section">
            <div class="section-row">
              <h4>门效果池</h4>
              <span class="section-count">{{ selectedDigitDoorLevel.door_refresh.effect_pools.length }} 池</span>
            </div>
            <div class="digitdoor-door-pool-grid">
              <article
                v-for="pool in selectedDigitDoorLevel.door_refresh.effect_pools"
                :key="String(pool.customized_type || pool.semantic_label)"
                class="digitdoor-door-pool-card"
              >
                <header>
                  <strong>{{ getDigitDoorDoorEffectPoolTitle(pool) }}</strong>
                  <small>{{ pool.effect_option_preview || '-' }}</small>
                </header>
                <div v-if="getDigitDoorDoorEffectPoolMeta(pool).length" class="digitdoor-door-pool-meta">
                  <span v-for="meta in getDigitDoorDoorEffectPoolMeta(pool)" :key="meta">{{ meta }}</span>
                </div>
                <div v-if="getDigitDoorDoorEffectPoolChips(pool).length" class="digitdoor-door-pool-effect-list">
                  <span
                    v-for="chip in getDigitDoorDoorEffectPoolChips(pool)"
                    :key="chip.key"
                    :title="chip.title"
                  >
                    <b>{{ chip.label }}</b>
                    <em v-if="chip.hint">{{ chip.hint }}</em>
                  </span>
                </div>
              </article>
            </div>
          </section>

          <section v-if="selectedDigitDoorLevel.door_refresh?.points?.length" class="object-section digitdoor-monster-section">
            <div class="section-row">
              <h4>刷门时间轴</h4>
              <span class="section-count">DoorRefreshPoint -> SkillRefreshEffect</span>
            </div>
            <div v-if="getDigitDoorDoorRefreshChips(selectedDigitDoorLevel).length" class="digitdoor-monster-chip-row">
              <span v-for="chip in getDigitDoorDoorRefreshChips(selectedDigitDoorLevel)" :key="chip">{{ chip }}</span>
            </div>
            <div class="digitdoor-wave-table">
              <div class="digitdoor-wave-row head">
                <span>时间</span>
                <span>位置</span>
                <span>候选门池</span>
                <span>实体</span>
                <span>特殊字段</span>
              </div>
              <template
                v-for="point in selectedDigitDoorLevel.door_refresh.points"
                :key="`${point.level}-${point.start_refresh_time}-${point.point_id}`"
              >
                <div class="digitdoor-wave-row">
                  <strong>{{ point.start_refresh_time || '-' }}s</strong>
                  <span>{{ point.position_projection || point.side_label || '-' }}</span>
                  <span>{{ getDigitDoorDoorRefreshEffectText(point) || '-' }}</span>
                  <span>{{ getDigitDoorDoorRefreshStatsText(point) || '-' }}</span>
                  <span>{{ getDigitDoorDoorRefreshSpecialText(point) || '-' }}</span>
                </div>
                <div
                  v-if="getDigitDoorDoorEffectOptionChips(point).length || getDigitDoorDoorSpecialEffectOptionChips(point).length"
                  class="digitdoor-door-option-row"
                >
                  <span
                    v-for="chip in getDigitDoorDoorEffectOptionChips(point)"
                    :key="chip.key"
                    :class="{ more: chip.more }"
                    :title="chip.title"
                  >
                    {{ chip.label }}
                  </span>
                  <span
                    v-for="chip in getDigitDoorDoorSpecialEffectOptionChips(point)"
                    :key="chip.key"
                    class="special"
                    :class="{ more: chip.more }"
                    :title="chip.title"
                  >
                    {{ chip.label }}
                  </span>
                </div>
              </template>
            </div>
          </section>

          <section v-if="selectedDigitDoorLevel.monster_refresh?.points?.length" class="object-section digitdoor-monster-section">
            <div class="section-row">
              <h4>怪物波次</h4>
              <span class="section-count">MonsterRefreshPoint -> MonsterGroup</span>
            </div>
            <div v-if="getDigitDoorMonsterRefreshChips(selectedDigitDoorLevel).length" class="digitdoor-monster-chip-row">
              <span v-for="chip in getDigitDoorMonsterRefreshChips(selectedDigitDoorLevel)" :key="chip">{{ chip }}</span>
            </div>
            <div v-if="selectedDigitDoorLevel.monster_refresh.monsters?.length" class="digitdoor-monster-card-grid">
              <article
                v-for="monster in selectedDigitDoorLevel.monster_refresh.monsters"
                :key="String(monster.monster_id || monster.name)"
                class="digitdoor-monster-card"
              >
                <strong>{{ monster.name || monster.text_name || `怪物 ${monster.monster_id}` }}</strong>
                <small>{{ getDigitDoorMonsterCardMeta(monster) }}</small>
                <p v-if="monster.description">{{ compactText(monster.description, 96) }}</p>
                <div v-if="monster.default_skills?.length" class="digitdoor-monster-skill-list">
                  <span
                    v-for="skill in monster.default_skills"
                    :key="`${monster.monster_id}-skill-${skill.id}`"
                  >
                    <b>{{ getDigitDoorMonsterSkillTitle(skill) }}</b>
                    {{ getDigitDoorMonsterSkillMeta(skill) }}
                    <em
                      v-for="hint in getDigitDoorMonsterSkillFlowHints(skill)"
                      :key="`${monster.monster_id}-skill-${skill.id}-${hint}`"
                    >
                      {{ hint }}
                    </em>
                    <em
                      v-for="hint in getDigitDoorMonsterSkillAccessorHints(skill)"
                      :key="`${monster.monster_id}-skill-${skill.id}-accessor-${hint}`"
                      class="param"
                    >
                      {{ hint }}
                    </em>
                    <em
                      v-for="hint in getDigitDoorMonsterSkillValueProjectionHints(skill)"
                      :key="`${monster.monster_id}-skill-${skill.id}-value-${hint}`"
                      class="projection"
                    >
                      {{ hint }}
                    </em>
                    <em
                      v-for="hint in getDigitDoorMonsterSkillBuffHints(skill)"
                      :key="`${monster.monster_id}-skill-${skill.id}-buff-${hint}`"
                      class="buff"
                    >
                      {{ hint }}
                    </em>
                    <em
                      v-for="hint in getDigitDoorMonsterSkillBuffFormulaHints(skill)"
                      :key="`${monster.monster_id}-skill-${skill.id}-buff-formula-${hint}`"
                      class="formula"
                    >
                      {{ hint }}
                    </em>
                  </span>
                </div>
              </article>
            </div>
            <div class="digitdoor-wave-table">
              <div class="digitdoor-wave-row head">
                <span>波次</span>
                <span>怪物</span>
                <span>刷新</span>
                <span>属性</span>
                <span>技能</span>
              </div>
              <div
                v-for="point in selectedDigitDoorLevel.monster_refresh.points"
                :key="`${point.level}-${point.refresh_wave}-${point.id}`"
                class="digitdoor-wave-row"
              >
                <strong>{{ point.refresh_wave || '-' }}</strong>
                <span>{{ getDigitDoorMonsterName(point) }}</span>
                <span>{{ getDigitDoorMonsterTimingText(point) || '-' }}</span>
                <span>{{ getDigitDoorMonsterStatsText(point) || '-' }}</span>
                <span>{{ getDigitDoorMonsterSkillText(point) || '-' }}</span>
              </div>
            </div>
          </section>

          <section v-if="getDigitDoorLevelRawRows(selectedDigitDoorLevel).length" class="object-section item-field-section">
            <h4>配置字段</h4>
            <dl>
              <template
                v-for="row in getDigitDoorLevelRawRows(selectedDigitDoorLevel)"
                :key="row.label"
              >
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </template>
            </dl>
          </section>
        </template>
        <template v-else-if="activeTab === 'digitdoor_enhance' && selectedDigitDoorEnhanceGroup">
          <section class="detail-head reward-config-head">
            <div class="reward-config-detail-badge">
              {{ selectedDigitDoorEnhanceGroup.char_id || '强' }}
            </div>
            <div class="detail-title">
              <h3>{{ selectedDigitDoorEnhanceGroup.name }}</h3>
              <div class="detail-meta">
                <span v-if="selectedDigitDoorEnhanceGroup.char_id">Group {{ selectedDigitDoorEnhanceGroup.char_id }}</span>
                <span>{{ selectedDigitDoorEnhanceGroup.enhance_count || selectedDigitDoorEnhanceGroup.enhances?.length || 0 }} 个强化</span>
                <span>{{ (selectedDigitDoorEnhanceGroup.enhances || []).filter(item => item.condition_raw).length }} 条条件</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section v-if="selectedDigitDoorEnhanceGroup.description" class="object-section intro-section">
            <h4>基础效果</h4>
            <div class="plain-rich-text" v-html="renderFanxiuText(selectedDigitDoorEnhanceGroup.description, { tone: 'light' })" />
          </section>

          <section v-if="selectedDigitDoorEnhanceGroup.enhances?.length" class="object-section">
            <div class="section-row">
              <h4>强化条件树</h4>
              <span class="section-count">{{ selectedDigitDoorEnhanceGroup.enhances.length }} 节点</span>
            </div>
            <div class="doupo-strength-list">
              <article
                v-for="item in selectedDigitDoorEnhanceGroup.enhances"
                :key="String(item.id)"
                class="doupo-strength-item"
              >
                <div class="skill-item-head">
                  <strong>{{ item.name || item.id }}</strong>
                  <span>{{ getDigitDoorEnhanceTreeMeta(item) }}</span>
                </div>
                <div class="plain-rich-text compact" v-html="renderFanxiuText(item.description || '', { tone: 'light' })" />
                <div class="doupo-logic-chip-row compact">
                  <span>{{ getDigitDoorEnhanceConditionText(item) }}</span>
                </div>
                <div v-if="getDigitDoorEnhanceBadges(item).length" class="doupo-logic-chip-row compact">
                  <span v-for="badge in getDigitDoorEnhanceBadges(item)" :key="`${item.id}-${badge}`">{{ badge }}</span>
                </div>
              </article>
            </div>
          </section>

          <section class="object-section item-field-section">
            <h4>配置边界</h4>
            <dl>
              <dt>组 ID</dt>
              <dd>{{ selectedDigitDoorEnhanceGroup.char_id || '-' }}</dd>
              <dt>条件说明</dt>
              <dd>PR 为前置强化，MU 为互斥，TCLV 为等级区间；该 group key 不直接等同数字门角色 id。</dd>
            </dl>
          </section>
        </template>
        <template v-else-if="activeTab === 'digitdoor' && selectedDigitDoorCharacter">
          <section class="detail-head">
            <div class="object-icon">
              <span class="icon-fallback">{{ getObjectIconText(selectedDigitDoorCharacter) }}</span>
              <img
                v-if="getObjectIconUrl(selectedDigitDoorCharacter)"
                :src="getObjectIconUrl(selectedDigitDoorCharacter)"
                :alt="selectedDigitDoorCharacter.name"
                @error="hideBrokenIcon"
              >
            </div>
            <div class="detail-title">
              <h3>{{ selectedDigitDoorCharacter.name }}</h3>
              <div class="detail-meta">
                <span>ID {{ selectedDigitDoorCharacter.id }}</span>
                <span v-if="selectedDigitDoorCharacter.quality_label">{{ selectedDigitDoorCharacter.quality_label }}品</span>
                <span v-if="selectedDigitDoorCharacter.positioning">{{ selectedDigitDoorCharacter.positioning }}</span>
                <span v-if="selectedDigitDoorCharacter.skill_name">神通 {{ selectedDigitDoorCharacter.skill_name }}</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section v-if="selectedDigitDoorCharacter.skill_description" class="object-section intro-section">
            <h4>{{ selectedDigitDoorCharacter.skill_name || '技能概览' }}</h4>
            <div class="plain-rich-text" v-html="renderFanxiuText(selectedDigitDoorCharacter.skill_description, { tone: 'light' })" />
          </section>

          <section v-if="selectedDigitDoorCharacter.skills?.length" class="object-section">
            <div class="section-row">
              <h4>局内技能</h4>
              <span class="section-count">{{ selectedDigitDoorCharacter.skills.length }} 条</span>
            </div>
            <div class="doupo-skill-list">
              <article v-for="skill in selectedDigitDoorCharacter.skills" :key="String(skill.id)" class="doupo-skill-item">
                <div class="doupo-source-head">
                  <span class="doupo-source-icon">
                    <span class="icon-fallback">{{ getObjectIconText(selectedDigitDoorCharacter) }}</span>
                    <img
                      v-if="getFanxiuResourceIconUrl(skill.skill_icon)"
                      :src="getFanxiuResourceIconUrl(skill.skill_icon)"
                      :alt="skill.skill_name || selectedDigitDoorCharacter.name"
                      loading="lazy"
                      @error="hideBrokenIcon"
                    >
                  </span>
                  <span>
                    <strong>{{ skill.skill_name || skill.skill_title_plain || skill.id }}</strong>
                    <small>{{ getDigitDoorSkillMeta(skill) }}</small>
                  </span>
                </div>
                <div class="plain-rich-text compact" v-html="renderFanxiuText(skill.skill_description || '', { tone: 'light' })" />
                <div v-if="skill.show_condition" class="doupo-logic-chip-row compact">
                  <span v-html="renderFanxiuText(skill.show_condition, { tone: 'light' })"></span>
                </div>
                <div v-if="skill.runtime?.buffs?.length" class="doupo-buff-list">
                  <div
                    v-for="buff in skill.runtime.buffs"
                    :key="`${skill.id}-runtime-buff-${buff.id}`"
                    class="doupo-buff-row"
                  >
                    <div class="doupo-buff-main">
                      <strong>{{ getDigitDoorBuffTitle(buff) }}</strong>
                      <small>{{ getDigitDoorBuffMeta(buff) }}</small>
                    </div>
                    <div v-if="getDigitDoorBuffLines(buff).length" class="doupo-buff-extra">
                      <span v-for="line in getDigitDoorBuffLines(buff)" :key="`${skill.id}-${buff.id}-${line}`">{{ line }}</span>
                    </div>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section v-if="selectedDigitDoorCharacter.door_effects?.length" class="object-section">
            <div class="section-row">
              <h4>门效果</h4>
              <span class="section-count">{{ selectedDigitDoorCharacter.door_effects.length }} 条</span>
            </div>
            <div class="doupo-source-grid">
              <article
                v-for="door in selectedDigitDoorCharacter.door_effects"
                :key="String(door.id)"
                class="doupo-source-card"
              >
                <div class="skill-item-head">
                  <strong>{{ door.effect_show_plain || door.effect_show || door.id }}</strong>
                  <span>{{ getDigitDoorDoorMeta(door) }}</span>
                </div>
                <div v-if="door.show_tips" class="plain-rich-text compact" v-html="renderFanxiuText(door.show_tips, { tone: 'light' })" />
                <div v-if="door.skills?.length" class="doupo-source-lines">
                  <span v-for="skill in door.skills" :key="`${door.id}-${skill.id}`">
                    {{ skill.skill_name || skill.skill_title_plain || skill.id }}
                    <template v-if="getDigitDoorDoorSkillMeta(skill)"> · {{ getDigitDoorDoorSkillMeta(skill) }}</template>
                  </span>
                </div>
              </article>
            </div>
          </section>

          <section v-if="selectedDigitDoorCharacter.logic_skills?.length" class="object-section">
            <div class="section-row">
              <h4>技能逻辑</h4>
              <span class="section-count">{{ selectedDigitDoorCharacter.logic_skills.length }} 行</span>
            </div>
            <div class="doupo-logic-list">
              <article
                v-for="(skill, index) in selectedDigitDoorCharacter.logic_skills"
                :key="String(skill.id ?? index)"
                class="doupo-logic-item"
              >
                <div class="skill-item-head">
                  <strong>{{ getDigitDoorLogicSkillTitle(skill, index) }}</strong>
                  <span>{{ getDigitDoorLogicSkillMeta(skill) }}</span>
                </div>
                <div v-if="skill.buff_ids?.length" class="doupo-logic-chip-row">
                  <span v-for="buffId in skill.buff_ids" :key="`${skill.id}-buff-id-${buffId}`">Buff {{ buffId }}</span>
                </div>
                <div v-if="skill.buffs?.length" class="doupo-buff-list">
                  <div
                    v-for="buff in skill.buffs"
                    :key="`${skill.id}-logic-buff-${buff.id}`"
                    class="doupo-buff-row"
                  >
                    <div class="doupo-buff-main">
                      <strong>{{ getDigitDoorBuffTitle(buff) }}</strong>
                      <small>{{ getDigitDoorBuffMeta(buff) }}</small>
                    </div>
                    <div v-if="getDigitDoorBuffLines(buff).length" class="doupo-buff-extra">
                      <span v-for="line in getDigitDoorBuffLines(buff)" :key="`${skill.id}-${buff.id}-${line}`">{{ line }}</span>
                    </div>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section v-if="selectedDigitDoorCharacter.skill_enhance_effects?.length" class="object-section">
            <div class="section-row">
              <h4>强化效果</h4>
              <span class="section-count">{{ selectedDigitDoorCharacter.skill_enhance_effects.length }} 条</span>
            </div>
            <div class="doupo-strength-list">
              <article
                v-for="item in selectedDigitDoorCharacter.skill_enhance_effects"
                :key="String(item.id)"
                class="doupo-strength-item"
              >
                <div class="skill-item-head">
                  <strong>强化 {{ item.id }}</strong>
                  <span>{{ getDigitDoorEnhanceMeta(item) }}</span>
                </div>
                <div v-if="getDigitDoorEnhanceLines(item).length" class="doupo-logic-chip-row compact">
                  <span v-for="line in getDigitDoorEnhanceLines(item)" :key="`${item.id}-${line}`">{{ line }}</span>
                </div>
                <div v-if="item.buff" class="doupo-buff-row">
                  <div class="doupo-buff-main">
                    <strong>{{ getDigitDoorBuffTitle(item.buff) }}</strong>
                    <small>{{ getDigitDoorBuffMeta(item.buff) }}</small>
                  </div>
                  <div v-if="getDigitDoorBuffLines(item.buff).length" class="doupo-buff-extra">
                    <span v-for="line in getDigitDoorBuffLines(item.buff)" :key="`${item.id}-buff-${line}`">{{ line }}</span>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section v-if="selectedDigitDoorCharacter.level_milestones?.length" class="object-section">
            <div class="section-row">
              <h4>等级节点</h4>
              <span class="section-count">{{ selectedDigitDoorCharacter.level_milestones.length }} 个</span>
            </div>
            <div class="doupo-progress-strip">
              <span
                v-for="item in selectedDigitDoorCharacter.level_milestones"
                :key="String(item.level)"
              >
                <strong>{{ getDigitDoorLevelMilestoneTitle(item) }}</strong>
                {{ getDigitDoorLevelMilestoneMeta(item) }}
              </span>
            </div>
          </section>

          <section class="object-section item-field-section">
            <h4>配置摘要</h4>
            <dl>
              <dt>等级</dt>
              <dd>{{ selectedDigitDoorCharacter.min_level || '-' }} - {{ selectedDigitDoorCharacter.max_level || '-' }} · {{ selectedDigitDoorCharacter.level_count || 0 }} 行</dd>
              <dt>技能</dt>
              <dd>{{ selectedDigitDoorCharacter.skill_count || 0 }} 展示 / {{ selectedDigitDoorCharacter.logic_skill_count || 0 }} 逻辑 / {{ selectedDigitDoorCharacter.skill_enhance_effect_count || 0 }} 强化</dd>
              <dt>门效果</dt>
              <dd>{{ selectedDigitDoorCharacter.door_effect_count || 0 }} 条</dd>
              <dt>模型</dt>
              <dd>{{ selectedDigitDoorCharacter.model || '-' }}</dd>
            </dl>
          </section>
        </template>
        <template v-else-if="activeTab === 'doupotd' && selectedDoupoTDPartner">
          <section class="detail-head">
            <div class="object-icon">
              <span class="icon-fallback">{{ getObjectIconText(selectedDoupoTDPartner) }}</span>
              <img
                v-if="getObjectIconUrl(selectedDoupoTDPartner)"
                :src="getObjectIconUrl(selectedDoupoTDPartner)"
                :alt="selectedDoupoTDPartner.name"
                @error="hideBrokenIcon"
              >
            </div>
            <div class="detail-title">
              <h3>{{ selectedDoupoTDPartner.name }}</h3>
              <div class="detail-meta">
                <span>ID {{ selectedDoupoTDPartner.id }}</span>
                <span v-if="selectedDoupoTDPartner.positioning">{{ selectedDoupoTDPartner.positioning }}</span>
                <span v-if="selectedDoupoTDPartner.skill_name">绝技 {{ selectedDoupoTDPartner.skill_name }}</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <section v-if="selectedDoupoTDPartner.skill_description_rich" class="object-section intro-section">
            <h4>{{ selectedDoupoTDPartner.skill_name || '技能概览' }}</h4>
            <div class="plain-rich-text" v-html="renderFanxiuText(selectedDoupoTDPartner.skill_description_rich, { tone: 'light' })" />
          </section>

          <section v-if="selectedDoupoTDPartner.skills?.length" class="object-section">
            <div class="section-row">
              <h4>局内技能</h4>
              <span class="section-count">{{ selectedDoupoTDPartner.skills.length }} 条</span>
            </div>
            <div class="doupo-skill-list">
              <article v-for="skill in selectedDoupoTDPartner.skills" :key="String(skill.id)" class="doupo-skill-item">
                <div class="skill-item-head">
                  <strong>{{ skill.skill_name || skill.skill_title || skill.id }}</strong>
                  <span>{{ getDoupoTDSkillMeta(skill) }}</span>
                </div>
                <div class="plain-rich-text compact" v-html="renderFanxiuText(skill.skill_description_rich || skill.skill_description || '', { tone: 'light' })" />
              </article>
            </div>
          </section>

          <section v-if="selectedDoupoTDPartner.logic_skills?.length" class="object-section">
            <div class="section-row">
              <h4>技能逻辑</h4>
              <span class="section-count">{{ selectedDoupoTDPartner.logic_skills.length }} 行</span>
            </div>
            <div class="doupo-logic-list">
              <article
                v-for="(skill, index) in selectedDoupoTDPartner.logic_skills"
                :key="String(skill.id ?? index)"
                class="doupo-logic-item"
              >
                <div class="skill-item-head">
                  <strong>{{ getDoupoTDLogicSkillTitle(skill, index) }}</strong>
                  <span>{{ getDoupoTDLogicSkillMeta(skill) }}</span>
                </div>
                <div v-if="getDoupoTDRuntimeTimelineChips(skill).length" class="doupo-logic-chip-row">
                  <span v-for="chip in getDoupoTDRuntimeTimelineChips(skill)" :key="`${skill.id}-${chip}`">{{ chip }}</span>
                </div>
                <div v-if="skill.runtime?.buffs?.length" class="doupo-buff-list">
                  <div
                    v-for="buff in skill.runtime.buffs"
                    :key="`${skill.id}-${buff.source_kind}-${buff.id}`"
                    class="doupo-buff-row"
                  >
                    <div class="doupo-buff-main">
                      <strong>{{ getDoupoTDBuffTitle(buff) }}</strong>
                      <small>{{ getDoupoTDBuffMeta(buff) }}</small>
                    </div>
                    <div v-if="getDoupoTDBuffFlagLabels(buff).length" class="doupo-logic-chip-row compact">
                      <span v-for="label in getDoupoTDBuffFlagLabels(buff)" :key="`${skill.id}-${buff.id}-${label}`">{{ label }}</span>
                    </div>
                    <div v-if="getDoupoTDBuffExtraLines(buff).length" class="doupo-buff-extra">
                      <span v-for="line in getDoupoTDBuffExtraLines(buff)" :key="`${skill.id}-${buff.id}-${line}`">{{ line }}</span>
                    </div>
                    <div v-if="getDoupoTDBuffFlowHint(buff) || getDoupoTDBuffFlowFunctions(buff).length" class="doupo-buff-flow">
                      <p v-if="getDoupoTDBuffFlowHint(buff)">{{ getDoupoTDBuffFlowHint(buff) }}</p>
                      <div v-if="getDoupoTDBuffFlowChips(buff).length" class="doupo-logic-chip-row compact flow">
                        <span v-for="chip in getDoupoTDBuffFlowChips(buff)" :key="`${skill.id}-${buff.id}-flow-${chip}`">{{ chip }}</span>
                      </div>
                      <div v-if="getDoupoTDBuffFlowFunctions(buff).length" class="doupo-buff-flow-functions">
                        <span
                          v-for="item in getDoupoTDBuffFlowFunctions(buff)"
                          :key="`${skill.id}-${buff.id}-fn-${item.name}`"
                        >
                          {{ getDoupoTDBuffFlowFunctionLabel(item) }}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section
            v-if="selectedDoupoTDPartner.draw_sources?.length || selectedDoupoTDPartner.compose_quality_sources?.length || selectedDoupoTDPartner.compose_progress_rewards?.length"
            class="object-section"
          >
            <div class="section-row">
              <h4>来源与概率</h4>
              <span class="section-count">
                {{ (selectedDoupoTDPartner.draw_sources?.length || 0) + (selectedDoupoTDPartner.compose_quality_sources?.length || 0) }} 池
              </span>
            </div>
            <div class="doupo-source-grid">
              <article
                v-for="source in selectedDoupoTDPartner.draw_sources"
                :key="`draw-${source.id}`"
                class="doupo-source-card"
              >
                <div class="doupo-source-head">
                  <span class="doupo-source-icon">
                    <span class="icon-fallback">{{ getObjectIconText(source.item || selectedDoupoTDPartner) }}</span>
                    <img
                      v-if="getFanxiuResourceIconUrl(source.item?.icon)"
                      :src="getFanxiuResourceIconUrl(source.item?.icon)"
                      :alt="getDoupoTDDrawSourceTitle(source)"
                      loading="lazy"
                      @error="hideBrokenIcon"
                    >
                  </span>
                  <span>
                    <strong>{{ getDoupoTDDrawSourceTitle(source) }}</strong>
                    <small>{{ getDoupoTDDrawSourceMeta(source) }}</small>
                  </span>
                </div>
                <div class="doupo-source-lines">
                  <span v-for="entry in source.entries" :key="`draw-${source.id}-${entry.card_id}`">
                    {{ getDoupoTDEntryText(entry) }}
                  </span>
                </div>
              </article>

              <article
                v-for="source in selectedDoupoTDPartner.compose_quality_sources"
                :key="`compose-${source.id}`"
                class="doupo-source-card compact-source"
              >
                <div class="doupo-source-head">
                  <span class="doupo-source-badge">合</span>
                  <span>
                    <strong>{{ getDoupoTDComposeSourceTitle(source) }}</strong>
                    <small>{{ getDoupoTDComposeSourceMeta(source) }}</small>
                  </span>
                </div>
                <div class="doupo-source-lines">
                  <span v-for="entry in source.entries" :key="`compose-${source.id}-${entry.card_id}`">
                    {{ getDoupoTDEntryText(entry) }}
                  </span>
                </div>
              </article>
            </div>
            <div v-if="selectedDoupoTDPartner.compose_progress_rewards?.length" class="doupo-progress-strip">
              <span
                v-for="item in selectedDoupoTDPartner.compose_progress_rewards"
                :key="`progress-${item.id}`"
              >
                <strong>{{ getDoupoTDProgressRewardTitle(item) }}</strong>
                {{ getDoupoTDRewardsText(item.rewards) }}
              </span>
            </div>
          </section>

          <section v-if="selectedDoupoTDPartner.compose_cards?.length" class="object-section">
            <div class="section-row">
              <h4>卡牌档位</h4>
              <span class="section-count">{{ selectedDoupoTDPartner.compose_cards.length }} 张</span>
            </div>
            <div class="doupo-compose-grid">
              <article
                v-for="card in selectedDoupoTDPartner.compose_cards"
                :key="String(card.id)"
                class="doupo-compose-card"
              >
                <div class="doupo-compose-head">
                  <span class="doupo-compose-icon">
                    <span class="icon-fallback">{{ getObjectIconText(selectedDoupoTDPartner) }}</span>
                    <img
                      v-if="getDoupoTDComposeIconUrl(card)"
                      :src="getDoupoTDComposeIconUrl(card)"
                      :alt="card.title"
                      loading="lazy"
                      @error="hideBrokenIcon"
                    >
                  </span>
                  <span>
                    <strong>{{ card.title }}</strong>
                    <small>{{ getDoupoTDComposeMeta(card) }}</small>
                  </span>
                </div>
                <div v-if="getDoupoTDAttrText(card.attrs)" class="doupo-attr-text">
                  <span v-for="line in getDoupoTDAttrText(card.attrs).split('\n')" :key="`${card.id}-${line}`">{{ line }}</span>
                </div>
              </article>
            </div>
          </section>

          <section v-if="selectedDoupoTDPartner.strengths?.length" class="object-section">
            <div class="section-row">
              <h4>角色强化</h4>
              <span class="section-count">{{ selectedDoupoTDPartner.strengths.length }} 条</span>
            </div>
            <div class="doupo-strength-list">
              <article
                v-for="item in selectedDoupoTDPartner.strengths"
                :key="String(item.id)"
                class="doupo-strength-item"
              >
                <div class="skill-item-head">
                  <strong>{{ item.skill_name || item.id }}</strong>
                  <span>{{ getDoupoTDStrengthMeta(item) }}</span>
                </div>
                <div class="plain-rich-text compact" v-html="renderFanxiuText(item.skill_description_rich || item.skill_description || '', { tone: 'light' })" />
              </article>
            </div>
          </section>

          <section v-if="selectedDoupoTDPartner.level_summary" class="object-section item-field-section">
            <h4>配置摘要</h4>
            <dl>
              <dt>等级</dt>
              <dd>
                {{ selectedDoupoTDPartner.level_summary.min_level || '-' }}
                -
                {{ selectedDoupoTDPartner.level_summary.max_level || '-' }}
                <template v-if="selectedDoupoTDPartner.level_summary.level_count">
                  · {{ selectedDoupoTDPartner.level_summary.level_count }} 行
                </template>
              </dd>
              <dt>默认技能</dt>
              <dd>{{ (selectedDoupoTDPartner.level_summary.default_skill || []).join(' / ') || '-' }}</dd>
              <dt>模型</dt>
              <dd>{{ selectedDoupoTDPartner.model || '-' }}</dd>
              <dt>解锁</dt>
              <dd>{{ selectedDoupoTDPartner.unlock_description || selectedDoupoTDPartner.unlock_description1 || selectedDoupoTDPartner.unlock_condition || '-' }}</dd>
            </dl>
          </section>
        </template>
        <template v-else-if="selectedCard">
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
            <FanxiuLinkedItemChip
              v-for="item in getDisplayLinkedItems(selectedCard.consume_items)"
              :key="`consume-${item.id}-${item.count}`"
              :item="item"
            />
            <FanxiuLinkedItemChip
              v-for="item in getDisplayLinkedItems(selectedCard.show_condition_items)"
              :key="`show-${item.id}-${item.count}`"
              :item="item"
              muted
            />
          </div>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

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

          <section
            v-if="loadingSpecialFazeCatalog || specialFazeGroup"
            class="object-section special-faze-section"
          >
            <div class="section-row">
              <h4>特殊法则</h4>
              <span v-if="specialFazeCountText" class="section-count">{{ specialFazeCountText }}</span>
              <span v-else-if="loadingSpecialFazeCatalog" class="section-count">解析中</span>
            </div>
            <div v-if="loadingSpecialFazeCatalog" class="homemake-static-loading">正在读取法则目录...</div>
            <template v-if="specialFazeGroup">
              <div class="special-faze-summary">
                <span v-for="type in splitSpecialFazeTokens(specialFazeGroup.effect_types)" :key="`special-type-${type}`">Type {{ type }}</span>
                <span v-for="reason in splitSpecialFazeTokens(specialFazeGroup.reason_codes)" :key="`special-reason-${reason}`">Reason {{ reason }}</span>
                <span v-if="specialFazeGroup.consume_items">{{ specialFazeGroup.consume_items }}</span>
              </div>

              <div v-if="specialFazeEffectTypes.length" class="special-faze-panel-list">
                <article
                  v-for="effect in specialFazeEffectTypes"
                  :key="`special-effect-${effect.effect_type}`"
                  class="special-faze-panel"
                >
                  <div class="special-faze-panel-head">
                    <strong>Type {{ effect.effect_type }}</strong>
                    <span v-for="tag in getSpecialFazeEffectTags(effect)" :key="`${effect.effect_type}-${tag}`">{{ tag }}</span>
                  </div>
                  <div v-if="effect.tip_texts" class="special-faze-text" v-html="renderFanxiuText(effect.tip_texts, { tone: 'light' })" />
                </article>
              </div>

              <div v-if="specialFazeReasons.length" class="special-faze-reason-list">
                <article
                  v-for="reason in specialFazeReasons"
                  :key="`special-reason-row-${reason.reason}`"
                  class="special-faze-reason"
                >
                  <div class="special-faze-reason-head">
                    <strong>{{ reason.reason }}</strong>
                    <span v-for="tag in getSpecialFazeReasonTags(reason)" :key="`${reason.reason}-${tag}`">{{ tag }}</span>
                  </div>
                  <div class="special-faze-text" v-html="renderFanxiuText(reason.tip_texts, { tone: 'light' })" />
                </article>
              </div>

              <div v-if="specialFazeStages.length" class="special-faze-stage-list">
                <article
                  v-for="stage in specialFazeStages"
                  :key="`special-stage-${stage.source_id}-${stage.faze_id}`"
                  class="special-faze-stage"
                >
                  <div class="special-faze-stage-head">
                    <strong>{{ stage.source_name || stage.stage || stage.source_id }}</strong>
                    <span v-for="tag in getSpecialFazeStageTags(stage)" :key="`${stage.source_id}-${tag}`">{{ tag }}</span>
                  </div>
                  <div v-if="stage.tip_texts" class="special-faze-text" v-html="renderFanxiuText(stage.tip_texts, { tone: 'light' })" />
                </article>
              </div>
            </template>
          </section>

          <section
            v-if="loadingHomeMakeStaticDetail || homeMakeStaticRows.length || homeMakeStaticWarnings.length"
            class="object-section homemake-static-section"
          >
            <div class="section-row">
              <h4>功法效果</h4>
              <span v-if="selectedHomeMakeStaticDetail" class="section-count">
                {{ selectedHomeMakeStaticDetail.counts.rows }} 条 · {{ selectedHomeMakeStaticDetail.source }}
              </span>
              <span v-else-if="loadingHomeMakeStaticDetail" class="section-count">解析中</span>
            </div>
            <div v-if="loadingHomeMakeStaticDetail" class="homemake-static-loading">正在读取静态配置...</div>
            <div v-if="homeMakeStaticWarnings.length" class="homemake-static-warnings">
              <span v-for="warning in homeMakeStaticWarnings" :key="warning">{{ warning }}</span>
            </div>
            <div v-if="homeMakeStaticRows.length" class="homemake-static-list">
              <article
                v-for="(row, rowIndex) in homeMakeStaticRows"
                :key="`${row.section}-${row.active_state}-${row.effect_id || rowIndex}`"
                class="homemake-static-row"
                :class="`state-${row.active_state || 'unknown'}`"
              >
                <div class="homemake-static-label">
                  <strong>{{ getHomeMakeStaticSectionTitle(row) }}</strong>
                  <span v-if="getHomeMakeStaticRowMeta(row)">{{ getHomeMakeStaticRowMeta(row) }}</span>
                </div>
                <div class="homemake-static-text" v-html="renderFanxiuText(row.rich_text)" />
              </article>
            </div>
          </section>

          <section
            v-if="loadingHomeMakeFormulaCatalog || homeMakeFormulaRawGroups.length"
            class="object-section homemake-formula-section"
          >
            <div class="section-row">
              <h4>仙书公式</h4>
              <span v-if="homeMakeFormulaCountText" class="section-count">{{ homeMakeFormulaCountText }}</span>
              <span v-else-if="loadingHomeMakeFormulaCatalog" class="section-count">解析中</span>
            </div>
            <el-input
              v-if="homeMakeFormulaRawGroups.length > 8"
              v-model="homeMakeFormulaQuery"
              :prefix-icon="Search"
              clearable
              class="homemake-formula-filter"
              placeholder="筛公式 / 机制 / Buff"
            />
            <div v-if="loadingHomeMakeFormulaCatalog" class="homemake-static-loading">正在读取公式目录...</div>
            <div v-if="homeMakeFormulaGroups.length" class="homemake-formula-list">
              <article
                v-for="group in homeMakeFormulaGroups"
                :key="`formula-${group.feature_group}`"
                class="homemake-formula-row"
              >
                <div class="homemake-formula-head">
                  <strong>{{ group.side_feature_names || `特性组 ${group.feature_group}` }}</strong>
                  <span v-if="group.buff_names">{{ group.buff_names }}</span>
                </div>
                <div class="homemake-formula-text" v-html="renderFanxiuText(group.sample_rendered_plain)" />
                <div v-if="getHomeMakeFormulaTags(group).length" class="homemake-formula-tags">
                  <span v-for="tag in getHomeMakeFormulaTags(group)" :key="`${group.feature_group}-${tag}`">{{ tag }}</span>
                </div>
              </article>
            </div>
            <div
              v-else-if="homeMakeFormulaRawGroups.length && homeMakeFormulaQuery"
              class="homemake-buff-empty"
            >
              没有匹配公式
            </div>
          </section>

          <section
            v-if="loadingHomeMakeBuffParameterSemantics || homeMakeBuffParameterRawGroups.length"
            class="object-section homemake-buff-section"
          >
            <div class="section-row">
              <h4>仙书机制</h4>
              <span v-if="homeMakeBuffParameterCountText" class="section-count">{{ homeMakeBuffParameterCountText }}</span>
              <span v-else-if="loadingHomeMakeBuffParameterSemantics" class="section-count">解析中</span>
            </div>
            <el-input
              v-if="homeMakeBuffParameterRawGroups.length > 8"
              v-model="homeMakeBuffParameterQuery"
              :prefix-icon="Search"
              clearable
              class="homemake-buff-filter"
              placeholder="筛机制 / 技能 / 标签"
            />
            <div v-if="loadingHomeMakeBuffParameterSemantics" class="homemake-static-loading">正在读取机制分组...</div>
            <div v-if="homeMakeBuffParameterGroups.length" class="homemake-buff-list">
              <article
                v-for="group in homeMakeBuffParameterGroups"
                :key="group.group_key"
                class="homemake-buff-row"
              >
                <div class="homemake-buff-main">
                  <div class="homemake-buff-head">
                    <strong>{{ group.buff_name }}</strong>
                    <span v-if="group.side_jie_names">{{ group.side_jie_names }}</span>
                  </div>
                  <div class="homemake-buff-desc" v-html="renderFanxiuText(group.buff_desc)" />
                  <div v-if="getHomeMakeBuffTags(group).length" class="homemake-buff-tags">
                    <span v-for="tag in getHomeMakeBuffTags(group)" :key="tag">{{ tag }}</span>
                  </div>
                </div>
                <div v-if="group.links.length" class="homemake-buff-links">
                  <button
                    v-for="link in group.links.slice(0, 5)"
                    :key="`${group.group_key}-${link.field}-${link.target_table}-${link.target_id}`"
                    type="button"
                    class="homemake-buff-link-chip"
                    :class="{ actionable: canNavigateHomeMakeBuffLink(link) }"
                    :disabled="!canNavigateHomeMakeBuffLink(link)"
                    :title="getHomeMakeBuffLinkTitle(link)"
                    @click.stop="void navigateHomeMakeBuffLink(link)"
                  >
                    <b>{{ getHomeMakeBuffLinkLabel(link) }}</b>
                    <small>{{ getHomeMakeBuffLinkMeta(link) }}</small>
                  </button>
                  <em v-if="group.link_count > 5">+{{ group.link_count - 5 }}</em>
                </div>
              </article>
            </div>
            <div
              v-else-if="homeMakeBuffParameterRawGroups.length && homeMakeBuffParameterQuery"
              class="homemake-buff-empty"
            >
              没有匹配机制
            </div>
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
                    :class="{ empty: isBlankProgressionLine(line) }"
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
                        :class="{ empty: isBlankProgressionLine(line) }"
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
                    <FanxiuLinkedItemChip
                      v-for="item in getProgressionDisplayItems(group.first)"
                      :key="`${group.key}-${item.id}-${item.count}`"
                      :item="item"
                      compact
                    />
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
                        :class="{ empty: isBlankProgressionLine(line) }"
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
        <template v-else-if="selectedActivity">
          <section class="detail-head" :class="{ stale: selectedActivity.is_stale }">
            <div class="object-icon">
              <span class="icon-fallback">{{ getObjectIconText(selectedActivity) }}</span>
              <img
                v-if="getObjectIconUrl(selectedActivity)"
                :src="getObjectIconUrl(selectedActivity)"
                :alt="selectedActivity.name"
                @error="hideBrokenIcon"
              >
            </div>
            <div class="detail-title">
              <h3>{{ selectedActivity.name }}</h3>
              <div class="detail-meta">
                <span>ID {{ selectedActivity.id }}</span>
                <span v-if="getActivityMeta(selectedActivity)">{{ getActivityMeta(selectedActivity) }}</span>
                <span v-if="selectedActivity.time_kind_name">{{ selectedActivity.time_kind_name }}</span>
                <span v-if="getFirstTimelineLabel(selectedActivity)">{{ getFirstTimelineLabel(selectedActivity) }}</span>
                <span v-if="selectedActivity.is_stale" class="stale-badge">旧版保留</span>
              </div>
            </div>
          </section>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

          <div v-if="getTimelineValueHints(selectedActivity).length" class="time-hint-strip">
            <strong>时间线索</strong>
            <span
              v-for="hint in getTimelineValueHints(selectedActivity)"
              :key="`${hint.date}-${hint.time}-${hint.time_code}-${hint.source}-${hint.activity_id}-${hint.relation}`"
              :title="getTimelineHintTitle(hint)"
            >
              {{ getTimelineHintLabel(hint) }}
            </span>
          </div>

          <div v-if="getActivityLoopRows(selectedActivity).length" class="time-hint-strip">
            <strong>轮换日程</strong>
            <span
              v-for="row in getActivityLoopRows(selectedActivity)"
              :key="row.key"
            >
              {{ row.label }} · {{ row.value }}
            </span>
          </div>

          <section v-if="getActivityDescriptionRows(selectedActivity).length" class="object-section intro-section">
            <div class="activity-text-list">
              <article v-for="row in getActivityDescriptionRows(selectedActivity)" :key="row.label">
                <h4>{{ row.label }}</h4>
                <div class="plain-rich-text" v-html="renderFanxiuText(row.value, { tone: 'light' })" />
              </article>
            </div>
          </section>

          <section
            v-if="getActivityTimeRows(selectedActivity).length || getActivityConditionRows(selectedActivity).length"
            class="object-section"
          >
            <h4>时程 / 条件</h4>
            <dl class="object-field-list">
              <template v-for="row in getActivityTimeRows(selectedActivity)" :key="`time-${row.label}`">
                <dt>{{ row.label }}</dt>
                <dd :title="row.raw || row.value">{{ row.value }}</dd>
              </template>
              <template v-for="row in getActivityConditionRows(selectedActivity)" :key="`condition-${row.label}`">
                <dt>{{ row.label }}</dt>
                <dd :title="row.raw || row.value">{{ row.value }}</dd>
              </template>
            </dl>
          </section>

          <section v-if="getActivityJumpTargetRows(selectedActivity).length" class="object-section item-field-section">
            <h4>入口功能</h4>
            <dl class="object-field-list">
              <template v-for="row in getActivityJumpTargetRows(selectedActivity)" :key="row.label">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </template>
            </dl>
          </section>

          <section
            v-for="section in selectedActivity.reward_sections ?? []"
            :key="section.key"
            class="object-section"
          >
            <div class="section-row">
              <h4>{{ section.title }}</h4>
              <span class="section-count">{{ section.count }} 条</span>
            </div>
            <div class="skill-list activity-reward-list">
              <article
                v-for="(row, index) in section.rows"
                :key="getActivityRewardRowKey(section, row, index)"
                class="skill-item activity-reward-row"
              >
                <div class="skill-item-head">
                  <strong>{{ getActivityRewardRowTitle(row, index) }}</strong>
                  <span>{{ getActivityRewardRowMeta(row) }}</span>
                </div>
                <div v-if="row.costs?.length" class="feature-effects">
                  <span v-for="cost in row.costs" :key="cost">消耗 {{ cost }}</span>
                </div>
                <div v-if="row.reward_items?.length" class="linked-item-strip progression-items">
                  <FanxiuLinkedItemChip
                    v-for="item in row.reward_items"
                    :key="`${section.key}-${row.row_key}-${item.id}-${item.count}`"
                    :item="item"
                    compact
                  />
                </div>
                <div
                  v-if="getActivityRawRewardText(row)"
                  class="feature-static-text activity-raw-reward"
                >
                  {{ getActivityRawRewardText(row) }}
                </div>
              </article>
            </div>
          </section>

          <section v-if="getActivityFieldRows(selectedActivity).length" class="object-section item-field-section">
            <h4>字段</h4>
            <dl class="object-field-list">
              <template v-for="row in getActivityFieldRows(selectedActivity)" :key="row.label">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </template>
            </dl>
          </section>

          <details class="source-details">
            <summary>来源</summary>
            <dl>
              <dt>活动 ID</dt>
              <dd>{{ selectedActivity.id }}</dd>
              <dt>配置行</dt>
              <dd>{{ selectedActivity.source_row_key || '-' }}</dd>
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
            <FanxiuLinkedItemChip
              v-for="item in selectedLingjieCard.items"
              :key="String(item.id ?? item.row_key ?? item.name)"
              :item="item"
            />
          </div>

          <div v-if="selectedTerms.length" class="term-strip">
            <span v-for="term in selectedTerms" :key="term">{{ term }}</span>
          </div>

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
                        :class="{ empty: isBlankProgressionLine(line) }"
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
        <template v-else-if="activeTab === 'protocol' && selectedProtocolRow">
          <section class="protocol-detail-head">
            <div class="detail-title">
              <h3>{{ selectedProtocolRow.packet }}</h3>
              <div class="detail-meta">
                <span>ID {{ selectedProtocolRow.id || '-' }}</span>
                <span v-if="selectedProtocolRow.operation">操作 {{ selectedProtocolRow.operation }}</span>
                <span v-if="selectedProtocolRow.role">角色 {{ selectedProtocolRow.role }}</span>
                <span v-if="selectedProtocolRow.authority_class">边界 {{ selectedProtocolRow.authority_class }}</span>
              </div>
            </div>
          </section>

          <section class="protocol-detail-grid">
            <div>
              <span>方向</span>
              <strong>{{ displayProtocolText(selectedProtocolRow.direction) }}</strong>
            </div>
            <div>
              <span>操作</span>
              <strong>{{ displayProtocolText(selectedProtocolRow.operation) }}</strong>
            </div>
            <div>
              <span>角色</span>
              <strong>{{ displayProtocolText(selectedProtocolRow.role) }}</strong>
            </div>
            <div>
              <span>边界</span>
              <strong>{{ displayProtocolText(selectedProtocolRow.authority_class) }}</strong>
            </div>
            <div>
              <span>handler</span>
              <strong>{{ displayProtocolText(selectedProtocolRow.handler_names || selectedProtocolRow.net_function) }}</strong>
            </div>
            <div>
              <span>flow</span>
              <strong>{{ displayProtocolText(selectedProtocolRow.flow_kind) }}</strong>
            </div>
          </section>

          <section class="object-section protocol-section">
            <h4>字段</h4>
            <p>{{ displayProtocolText(compactProtocolFields(selectedProtocolRow)) }}</p>
          </section>

          <section
            v-if="selectedProtocolRow.state_sinks || selectedProtocolRow.semantic_note"
            class="object-section protocol-section"
          >
            <h4>状态与语义</h4>
            <p>{{ displayProtocolText(selectedProtocolRow.state_sinks || selectedProtocolRow.semantic_note) }}</p>
            <p v-if="selectedProtocolRow.state_sinks && selectedProtocolRow.semantic_note" class="protocol-note">
              {{ selectedProtocolRow.semantic_note }}
            </p>
          </section>

          <section class="object-section protocol-section">
            <h4>相关边 {{ selectedProtocolEdges.length }}</h4>
            <div v-if="selectedProtocolEdges.length" class="protocol-edge-list">
              <article v-for="edge in selectedProtocolEdges" :key="`${edge.source}-${edge.edge}-${edge.target}-${edge.evidence}`">
                <strong>{{ edge.edge }}</strong>
                <span>{{ getProtocolEdgeLabel(edge) }}</span>
                <em v-if="edge.evidence">{{ edge.evidence }}</em>
              </article>
            </div>
            <p v-else>没有相关边</p>
          </section>

          <details class="source-details">
            <summary>来源</summary>
            <dl>
              <dt>协议</dt>
              <dd>{{ protocolResponse?.title || protocolFeature }}</dd>
              <dt>Packet</dt>
              <dd>{{ selectedProtocolRow.packet }}</dd>
              <dt>语义表</dt>
              <dd>{{ protocolResponse?.outputs?.semantics || '-' }}</dd>
              <dt>边表</dt>
              <dd>{{ protocolResponse?.outputs?.edges || '-' }}</dd>
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

          <section v-if="selectedItem.effect_description || selectedItem.effect_details?.length || selectedItem.optional_gift_rewards?.length" class="object-section">
            <h4>效果</h4>
            <div v-if="selectedItem.effect_description" class="game-rich-text" v-html="renderFanxiuText(selectedItem.effect_description)" />
            <div v-for="detail in selectedItem.effect_details || []" :key="`${detail.kind || 'effect'}-${detail.source_id || detail.title}`" class="item-effect-detail">
              <div class="item-effect-detail-title">
                <strong>{{ detail.title || '详细效果' }}</strong>
                <span v-if="detail.subtitle">{{ detail.subtitle }}</span>
              </div>
              <div class="game-rich-text" v-html="renderFanxiuText(detail.description || detail.plain_description || '')" />
            </div>
            <div v-if="selectedItem.optional_gift_rewards?.length" class="linked-item-strip detail-items optional-gift-items">
              <FanxiuLinkedItemChip
                v-for="item in getDisplayLinkedItems(selectedItem.optional_gift_rewards)"
                :key="`optional-gift-${item.id}-${item.count}`"
                :item="item"
              />
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
                  <FanxiuLinkedItemChip
                    v-for="item in getProgressionDisplayItems(group.first)"
                    :key="`${group.key}-${item.id}-${item.count}`"
                    :item="item"
                    compact
                  />
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

    <div
      v-if="contextMenu.visible"
      class="wiki-context-menu"
      :style="contextMenuStyle"
      @click.stop
      @contextmenu.prevent
    >
      <button type="button" @click="openContextMenuTarget">在独立页面打开</button>
    </div>
  </div>
  </FanxiuResourceHoverScope>
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
  margin: 0;
  color: #0f1f35;
  font-size: 24px;
  line-height: 1.2;
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

.wiki-secondary-tabs {
  padding-top: 5px;
}

.wiki-secondary-tabs :deep(.el-tabs__item) {
  height: 34px;
  line-height: 34px;
  font-size: 13px;
}

.wiki-secondary-tabs :deep(.el-tabs__nav-wrap::after) {
  background: #edf0f5;
}

.wiki-context-menu {
  position: fixed;
  z-index: 3200;
  min-width: 146px;
  padding: 4px;
  background: #ffffff;
  border: 1px solid #d0d5dd;
  box-shadow: 0 10px 24px rgba(16, 24, 40, 0.16);
}

.wiki-context-menu button {
  width: 100%;
  height: 30px;
  padding: 0 10px;
  color: #172033;
  font: inherit;
  font-size: 13px;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.wiki-context-menu button:hover {
  color: #0b63ce;
  background: #eef5ff;
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

.visual-similarity-strip {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  margin: 0 0 8px;
  padding: 5px 7px;
  color: #344054;
  background: #f6f9fc;
  border: 1px solid #d8e2ef;
}

.visual-similarity-thumb {
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  object-fit: contain;
  background: #fff;
  border: 1px solid #e4e7ec;
  cursor: zoom-in;
}

.visual-similarity-thumb :deep(.el-image__inner) {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.visual-similarity-strip span {
  min-width: 0;
  max-width: min(360px, 50vw);
  overflow: hidden;
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.visual-similarity-strip small {
  color: #667085;
  font-size: 12px;
  white-space: nowrap;
}

.visual-similarity-strip button {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  padding: 0;
  color: #667085;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.stage-filter-select {
  width: 168px;
}

:global(.fanxiu-search-history-popover) {
  padding: 6px !important;
}

.search-history-panel {
  display: grid;
  gap: 2px;
}

.search-history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px 6px;
  color: #667085;
  font-size: 12px;
}

.search-history-header button {
  padding: 0;
  border: 0;
  color: #8a6728;
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.search-history-header button:hover {
  color: #5e461b;
}

.search-history-item {
  width: 100%;
  min-height: 28px;
  padding: 4px 8px;
  border: 0;
  border-radius: 4px;
  color: #344054;
  background: transparent;
  font: inherit;
  font-size: 13px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
}

.search-history-item:hover {
  background: #fff6dc;
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
  display: flex;
  flex-direction: column;
  gap: 4px;
  box-sizing: border-box;
  max-height: clamp(198px, 36vh, 390px);
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: 2px 6px 12px 0;
  border-bottom: 1px solid #eef1f5;
}

.facet-row {
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: start;
  min-height: auto;
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

.facet-more-option {
  color: #8a6728;
  background: rgba(255, 246, 220, 0.62);
  box-shadow: inset 0 0 0 1px rgba(174, 128, 38, 0.24);
}

.facet-more-option:hover {
  background: rgba(255, 238, 190, 0.92);
}

.result-count {
  color: #667085;
  font-size: 13px;
}

.homemake-overview {
  flex: 0 0 auto;
  margin: 0 0 10px;
  padding: 12px 14px;
  background:
    linear-gradient(90deg, rgba(255, 251, 238, 0.96), rgba(247, 240, 223, 0.9)),
    #f7f0df;
  border: 1px solid rgba(176, 132, 44, 0.32);
}

.homemake-overview-head {
  display: grid;
  grid-template-columns: auto auto minmax(220px, 320px);
  align-items: center;
  gap: 10px 14px;
  margin-bottom: 10px;
}

.homemake-overview-head h3 {
  margin: 0;
  color: #6b480d;
  font-size: 16px;
  line-height: 1.35;
}

.homemake-overview-head > span {
  color: #8d6b2c;
  font-size: 13px;
}

.homemake-overview-filter {
  justify-self: end;
  width: 100%;
}

.homemake-overview-loading,
.homemake-overview-empty {
  color: rgba(79, 60, 22, 0.72);
  font-size: 14px;
  line-height: 1.6;
}

.homemake-overview-list {
  max-height: clamp(190px, 24vh, 300px);
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 8px;
  overflow: auto;
  padding-right: 4px;
  scrollbar-gutter: stable;
}

.homemake-overview-row {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  padding: 9px 10px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(176, 132, 44, 0.22);
}

.homemake-overview-main {
  min-width: 0;
  display: grid;
  gap: 5px;
}

.homemake-overview-title {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 7px;
}

.homemake-overview-title strong {
  color: #202532;
  font-size: 15px;
  font-weight: 780;
}

.homemake-overview-title span {
  color: #a36a00;
  font-size: 12px;
}

.homemake-overview-desc {
  overflow: hidden;
  color: #463b29;
  font-size: 14px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.homemake-overview-meta {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.homemake-overview-meta span,
.homemake-overview-links em {
  max-width: 100%;
  padding: 2px 6px;
  overflow: hidden;
  color: #8a5a00;
  background: rgba(255, 251, 238, 0.82);
  border: 1px solid rgba(176, 132, 44, 0.28);
  font-size: 12px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.homemake-overview-links {
  max-width: 210px;
  display: flex;
  flex-wrap: wrap;
  align-content: start;
  justify-content: flex-end;
  gap: 5px;
}

.homemake-overview-links em {
  font-style: normal;
}

.object-workspace {
  flex: 1 1 420px;
  min-height: 0;
  display: grid;
  grid-template-columns: clamp(320px, 25%, 420px) minmax(0, 1fr);
  overflow: hidden;
  border: 1px solid #dfe4ec;
  background: #f7f1dc;
}

.object-workspace.protocol-workspace {
  grid-template-columns: clamp(460px, 42%, 660px) minmax(0, 1fr);
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

.object-row.stale {
  background: #f6f6f4;
}

.object-row.stale:hover {
  background: #eeeeeb;
}

.object-row.stale.selected {
  background: #f3eee2;
}

.object-row.stale .object-row-icon,
.detail-head.stale .object-icon {
  filter: grayscale(1);
}

.object-row.stale .object-row-title,
.object-row.stale .object-row-meta,
.object-row.stale .object-row-preview {
  color: #8a8f98;
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

.visual-thumb {
  background: #ffffff;
  border-color: #d0d5dd;
  text-shadow: none;
}

.visual-thumb img {
  object-fit: contain;
  padding: 3px;
  box-sizing: border-box;
}

.static-asset-thumb {
  background: #fffdf4;
  border-color: #d8c48c;
  color: #344054;
  text-shadow: none;
  font-size: 11px;
}

.static-asset-thumb img {
  object-fit: cover;
  padding: 2px;
  box-sizing: border-box;
  background: #fffdf4;
}

.audio-asset-row,
.static-asset-row,
.reward-config-row {
  grid-template-columns: 46px minmax(0, 1fr);
}

.audio-row-badge,
.static-asset-badge {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #32615f;
  background: #e8f3f1;
  border: 1px solid #abcac5;
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
}

.static-asset-badge {
  color: #344054;
  background: #f2f4f7;
  border-color: #cfd4dc;
  text-transform: none;
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

.similarity-rank {
  margin-left: 6px;
  color: #2f9eaa;
  font-size: 11px;
  font-weight: 800;
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

.protocol-row {
  width: 100%;
  display: grid;
  gap: 5px;
  padding: 11px 14px;
  border: 0;
  border-bottom: 1px solid #e5e7eb;
  background: transparent;
  color: #28384f;
  text-align: left;
  cursor: pointer;
}

.protocol-row:hover {
  background: #f3f4f6;
}

.protocol-row.selected {
  background: #eaf2ff;
  box-shadow: inset 3px 0 0 #3f92f5;
}

.protocol-row-title {
  min-width: 0;
  overflow: hidden;
  color: #152238;
  font-size: 14px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.protocol-row-meta {
  overflow: hidden;
  color: #6a4f2a;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.protocol-row-preview {
  color: #5d6b80;
  font-size: 12px;
  line-height: 1.45;
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.packet-wiki-workspace {
  grid-template-columns: clamp(280px, 24%, 380px) minmax(0, 1fr);
}

.packet-wiki-detail {
  background: #fff;
}

.packet-doc-head {
  max-width: 1120px;
  margin: 0 auto 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid #e5e7eb;
}

.packet-doc-head h3 {
  margin: 0 0 8px;
  color: #101828;
  font-size: 26px;
  line-height: 1.2;
}

.packet-doc-sample-count {
  font-size: 13px;
  font-weight: 400;
  color: #667085;
  margin-left: 12px;
}

.packet-doc-head p {
  margin: 0;
  color: #344054;
  line-height: 1.7;
}

.packet-doc-section {
  max-width: 1120px;
  margin: 0 auto;
}

.packet-doc-section h4 {
  margin: 0 0 10px;
  color: #1f2937;
  font-size: 16px;
}

.packet-protocol-doc {
  margin-bottom: 10px;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.packet-protocol-doc header {
  display: grid;
  grid-template-columns: 18px max-content minmax(0, 1fr) max-content;
  gap: 8px;
  align-items: baseline;
  padding: 8px 10px;
  border-bottom: 1px solid #edf0f5;
}

.packet-protocol-doc header :deep(.el-checkbox) {
  height: 18px;
}

.packet-protocol-doc header strong {
  color: #0f172a;
  font-size: 15px;
}

.packet-protocol-doc header span {
  overflow: hidden;
  color: #475569;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.packet-count-link {
  padding: 0;
  border: 0;
  background: transparent;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
}

.packet-count-link:hover {
  color: #2563eb;
  text-decoration: underline;
}

.packet-protocol-example {
  display: grid;
  gap: 0;
}

.packet-translation-example {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 10px;
  align-items: start;
  padding: 8px 10px;
  border-bottom: 1px solid #f1f5f9;
  font-size: 13px;
  line-height: 1.7;
}

.packet-translation-example.upstream {
  background: #f5f6f8;
}

.packet-example-label {
  color: #64748b;
  font-size: 12px;
  font-weight: 650;
  white-space: nowrap;
}

.packet-sample-meta {
  color: #64748b;
  text-align: right;
  line-height: 1.4;
}

.packet-sample-text {
  grid-column: 1 / -1;
  white-space: normal;
  word-break: break-word;
}

.packet-table-example {
  display: grid;
  gap: 6px;
  align-items: start;
  padding: 8px 10px;
  border-bottom: 1px solid #f1f5f9;
}

.packet-sample-table-wrap {
  overflow: auto;
}

.packet-sample-table {
  min-width: 520px;
  border-collapse: collapse;
  background: #fff;
  font-size: 12px;
}

.packet-sample-table th,
.packet-sample-table td {
  padding: 5px 8px;
  border: 1px solid #e5e7eb;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}

.packet-sample-table th {
  background: #f8fafc;
  color: #64748b;
  font-weight: 650;
}

.packet-cell-meaning {
  color: #059669;
  font-weight: 500;
}

.packet-json-example {
  display: grid;
  gap: 6px;
  align-items: start;
  padding: 8px 10px 10px;
}

.packet-json-example pre {
  max-height: 360px;
  min-width: 0;
  overflow: auto;
  margin: 0;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  background: #f8fafc;
  color: #0f172a;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre;
}

.packet-all-samples {
  display: grid;
  gap: 8px;
  padding: 10px;
  border-top: 1px solid #edf0f5;
  background: #fcfcfd;
}

.packet-sample-detail {
  border: 1px solid #e5e7eb;
  background: #fff;
}

.packet-sample-detail-head {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 6px 9px;
  border-bottom: 1px solid #f1f5f9;
  color: #64748b;
  font-size: 12px;
}

.packet-sample-detail-head strong {
  color: #334155;
  font-size: 12px;
}

.packet-sample-detail-text {
  padding: 8px 9px;
  color: #1f2937;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.packet-sample-detail details {
  border-top: 1px solid #f1f5f9;
}

.packet-sample-detail summary {
  padding: 6px 9px;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
}

.packet-sample-detail pre {
  max-height: 280px;
  overflow: auto;
  margin: 0;
  padding: 10px 12px;
  background: #f8fafc;
  color: #0f172a;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre;
}

.packet-business-param {
  display: inline-block;
  margin: 0 1px;
  padding: 0 3px;
  border-radius: 3px;
  background: #fff7ed;
  color: #b45309;
  font-weight: 600;
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

.visual-detail-head {
  align-items: flex-start;
}

.visual-detail-preview {
  width: 128px;
  height: 128px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  padding: 10px;
  box-sizing: border-box;
  background: #ffffff;
  border: 1px solid #d0d5dd;
  box-shadow: 0 8px 22px rgba(16, 24, 40, 0.08);
}

.visual-detail-preview img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.audio-detail-badge,
.static-asset-detail-badge {
  width: 86px;
  height: 86px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  color: #32615f;
  background: #e8f3f1;
  border: 1px solid #abcac5;
  font-size: 22px;
  font-weight: 850;
}

.static-asset-detail-badge {
  color: #344054;
  background: #f2f4f7;
  border-color: #cfd4dc;
  font-size: 18px;
}

.static-asset-detail-preview {
  width: 176px;
  height: 128px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #344054;
  background: #fffdf4;
  border: 1px solid #d8c48c;
  font-size: 18px;
  font-weight: 850;
  box-shadow: 0 8px 22px rgba(92, 67, 11, 0.1);
}

.static-asset-detail-preview img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.static-asset-full-preview-section {
  padding: 16px 18px 18px;
}

.static-asset-preview-loading {
  color: #8a7b61;
  font-size: 13px;
}

.static-asset-original-list,
.static-asset-derived-list {
  display: grid;
  gap: 16px;
}

.static-asset-original-figure,
.static-asset-derived-figure {
  min-width: 0;
  margin: 0;
  display: grid;
  gap: 8px;
}

.static-asset-original-image,
.static-asset-derived-preview {
  display: grid;
  place-items: center;
  overflow: hidden;
  min-height: 180px;
  box-sizing: border-box;
  background: #fff;
  border: 1px solid rgba(193, 164, 92, 0.42);
}

.static-asset-original-image img {
  display: block;
  width: auto;
  max-width: 100%;
  max-height: min(72vh, 760px);
  height: auto;
  object-fit: contain;
}

.static-asset-derived-preview {
  padding: 12px;
  background:
    linear-gradient(45deg, rgba(0, 0, 0, 0.035) 25%, transparent 25%) 0 0 / 18px 18px,
    linear-gradient(45deg, transparent 75%, rgba(0, 0, 0, 0.035) 75%) 0 0 / 18px 18px,
    #fffef8;
}

.static-asset-derived-preview img {
  display: block;
  width: auto;
  max-width: 100%;
  max-height: min(72vh, 760px);
  height: auto;
  object-fit: contain;
}

.static-asset-preview-caption {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  color: #8a7b61;
  font-size: 12px;
  line-height: 1.45;
}

.static-asset-preview-caption strong {
  color: #344054;
}

.static-asset-derived-note {
  margin: 0;
  color: #8a7b61;
  font-size: 13px;
  line-height: 1.55;
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

.stale-badge {
  color: #737985;
}

.protocol-detail-head {
  width: min(100%, 1080px);
  margin: 0 auto 14px;
}

.protocol-detail-grid {
  width: min(100%, 1080px);
  margin: 0 auto 14px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 14px;
  padding: 16px 20px;
  box-sizing: border-box;
  color: #243044;
  background: rgba(255, 252, 242, 0.74);
  border: 1px solid rgba(193, 164, 92, 0.48);
}

.protocol-detail-grid div {
  min-width: 0;
}

.protocol-detail-grid span {
  display: block;
  color: #7d8491;
  font-size: 12px;
  line-height: 1.5;
}

.protocol-detail-grid strong {
  display: block;
  overflow: hidden;
  color: #101828;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.protocol-section p {
  margin: 0;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.protocol-note {
  margin-top: 10px !important;
  color: #d8d0bd;
}

.protocol-edge-list {
  display: grid;
  gap: 8px;
}

.protocol-edge-list article {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr) minmax(120px, 220px);
  gap: 12px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.05);
}

.protocol-edge-list strong,
.protocol-edge-list span,
.protocol-edge-list em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.protocol-edge-list strong {
  color: #f4dc8a;
}

.protocol-edge-list em {
  color: #b8c4d6;
  font-style: normal;
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

.asset-info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.asset-info-grid div {
  min-width: 0;
  padding: 9px 10px;
  background: rgba(255, 255, 255, 0.56);
  border: 1px solid rgba(193, 164, 92, 0.28);
}

.asset-info-grid span,
.asset-path-list dt {
  display: block;
  color: #8a7b61;
  font-size: 12px;
  line-height: 1.45;
}

.asset-info-grid strong {
  display: block;
  overflow: hidden;
  color: #263244;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.asset-path-list {
  display: grid;
  gap: 8px;
  margin: 0;
}

.asset-path-list div {
  min-width: 0;
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  gap: 10px;
}

.asset-path-list dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: #344054;
  font-size: 13px;
  line-height: 1.45;
}

.audio-player-section audio {
  width: 100%;
  margin-bottom: 14px;
}

.audio-player-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}

.homemake-static-section {
  background: rgba(57, 58, 66, 0.97);
}

.homemake-buff-section {
  background: #f7f0df;
  border-color: rgba(176, 132, 44, 0.28);
}

.homemake-formula-section {
  background: #fbf7eb;
  border-color: rgba(176, 132, 44, 0.28);
}

.special-faze-section {
  line-height: 1.35;
}

.special-faze-summary,
.special-faze-panel-head,
.special-faze-reason-head,
.special-faze-stage-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 7px;
}

.special-faze-summary {
  margin-bottom: 12px;
}

.special-faze-summary span,
.special-faze-panel-head span,
.special-faze-reason-head span,
.special-faze-stage-head span {
  padding: 2px 7px;
  color: #f3d37a;
  font-size: 12px;
  background: rgba(255, 212, 95, 0.1);
  border: 1px solid rgba(255, 212, 95, 0.26);
}

.special-faze-panel-list,
.special-faze-reason-list,
.special-faze-stage-list {
  display: grid;
  gap: 10px;
}

.special-faze-panel-list,
.special-faze-reason-list {
  margin-bottom: 12px;
}

.special-faze-panel,
.special-faze-reason,
.special-faze-stage {
  display: grid;
  gap: 6px;
  padding: 10px 0 11px;
  border-bottom: 1px solid rgba(214, 196, 136, 0.22);
}

.special-faze-panel:last-child,
.special-faze-reason:last-child,
.special-faze-stage:last-child {
  border-bottom: 0;
}

.special-faze-panel-head strong,
.special-faze-reason-head strong,
.special-faze-stage-head strong {
  color: #fff5cf;
  font-size: 17px;
  font-weight: 760;
}

.special-faze-text {
  color: #f7f0df;
  font-size: 15px;
  line-height: 1.62;
}

.homemake-buff-section h4,
.homemake-formula-section h4 {
  color: #8a6b33;
  border-bottom-color: rgba(138, 107, 51, 0.36);
}

.homemake-buff-section .section-count,
.homemake-formula-section .section-count {
  color: rgba(79, 60, 22, 0.68);
}

.homemake-buff-filter,
.homemake-formula-filter {
  max-width: 320px;
  margin: -2px 0 12px;
}

.homemake-static-list {
  display: grid;
  gap: 14px;
}

.homemake-static-row {
  display: grid;
  grid-template-columns: 118px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  padding: 8px 0 10px;
  border-bottom: 1px solid rgba(226, 204, 138, 0.18);
}

.homemake-static-row:last-child {
  border-bottom: 0;
}

.homemake-static-row.state-base .homemake-static-label strong {
  color: #efe2ad;
}

.homemake-static-row.state-active .homemake-static-label strong {
  color: #35e0e0;
}

.homemake-static-label {
  display: grid;
  gap: 4px;
  color: rgba(247, 240, 223, 0.58);
  font-size: 12px;
  line-height: 1.35;
}

.homemake-static-label strong {
  color: #efe2ad;
  font-size: 17px;
  font-weight: 780;
}

.homemake-static-label span {
  overflow-wrap: anywhere;
}

.homemake-static-text {
  color: #f7f0df;
  font-size: 19px;
  line-height: 1.62;
  word-break: break-word;
}

.homemake-static-loading,
.homemake-static-warnings {
  color: rgba(247, 240, 223, 0.72);
  font-size: 14px;
  line-height: 1.6;
}

.homemake-static-warnings {
  display: grid;
  gap: 4px;
  margin-bottom: 12px;
  color: #ffd45f;
}

.homemake-buff-section .homemake-static-loading,
.homemake-formula-section .homemake-static-loading {
  color: rgba(79, 60, 22, 0.72);
}

.homemake-buff-list {
  display: grid;
  gap: 10px;
}

.homemake-buff-empty {
  color: rgba(79, 60, 22, 0.72);
  font-size: 14px;
  line-height: 1.6;
}

.homemake-buff-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(160px, 28%);
  gap: 14px;
  align-items: start;
  padding: 10px 0 12px;
  border-bottom: 1px solid rgba(176, 132, 44, 0.22);
}

.homemake-buff-row:last-child {
  border-bottom: 0;
}

.homemake-buff-main {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.homemake-buff-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

.homemake-buff-head strong {
  color: #262633;
  font-size: 18px;
  font-weight: 780;
}

.homemake-buff-head span {
  color: #a36a00;
  font-size: 13px;
}

.homemake-buff-desc {
  color: #3f3a30;
  font-size: 16px;
  line-height: 1.55;
  word-break: break-word;
}

.homemake-buff-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.homemake-buff-tags span,
.homemake-buff-link-chip,
.homemake-buff-links em {
  border: 1px solid rgba(176, 132, 44, 0.32);
  background: rgba(255, 251, 238, 0.82);
  color: #8a5a00;
  font-size: 12px;
  line-height: 1.3;
}

.homemake-buff-tags span {
  padding: 3px 7px;
}

.homemake-buff-links {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.homemake-buff-link-chip {
  appearance: none;
  display: grid;
  gap: 2px;
  max-width: 190px;
  padding: 5px 7px;
  text-align: left;
}

.homemake-buff-link-chip.actionable {
  cursor: pointer;
  border-color: rgba(176, 132, 44, 0.52);
}

.homemake-buff-link-chip.actionable:hover {
  background: #fff4ca;
}

.homemake-buff-link-chip:disabled {
  cursor: default;
}

.homemake-buff-link-chip b {
  overflow: hidden;
  color: #654000;
  font-size: 12px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.homemake-buff-link-chip small {
  overflow: hidden;
  color: rgba(101, 64, 0, 0.66);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.homemake-buff-links em {
  align-self: start;
  padding: 5px 7px;
  font-style: normal;
}

.homemake-formula-list {
  display: grid;
  gap: 10px;
}

.homemake-formula-row {
  display: grid;
  gap: 7px;
  padding: 10px 0 12px;
  border-bottom: 1px solid rgba(176, 132, 44, 0.2);
}

.homemake-formula-row:last-child {
  border-bottom: 0;
}

.homemake-formula-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

.homemake-formula-head strong {
  color: #262633;
  font-size: 18px;
  font-weight: 780;
}

.homemake-formula-head span {
  color: #9b5a00;
  font-size: 13px;
}

.homemake-formula-text {
  color: #3f3a30;
  font-size: 16px;
  line-height: 1.58;
  white-space: pre-wrap;
  word-break: break-word;
}

.homemake-formula-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.homemake-formula-tags span {
  border: 1px solid rgba(176, 132, 44, 0.32);
  background: rgba(255, 251, 238, 0.82);
  color: #8a5a00;
  padding: 3px 7px;
  font-size: 12px;
  line-height: 1.3;
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

.item-effect-detail {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid rgba(239, 217, 143, 0.3);
}

.item-effect-detail-title {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  color: #f7f0df;
}

.item-effect-detail-title strong {
  font-size: 16px;
}

.item-effect-detail-title span {
  color: rgba(247, 240, 223, 0.68);
  font-size: 13px;
}

.plain-rich-text.compact {
  padding-top: 8px;
  font-size: 15px;
  line-height: 1.5;
}

.rich-section-list,
.progression-section-list {
  display: grid;
  gap: 14px;
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

.rich-section-body p.empty,
.progression-section-body p.empty {
  min-height: 1.35em;
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

.activity-text-list {
  display: grid;
  gap: 18px;
}

.activity-text-list article {
  display: grid;
  gap: 4px;
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

:global(.fanxiu-inherit-tooltip .wiki-term),
:global(.fanxiu-inherit-tooltip .fanxiu-rich-term) {
  color: #ffd45f;
  font-weight: 700;
}

:global(.fanxiu-inherit-tooltip .wiki-number),
:global(.fanxiu-inherit-tooltip .fanxiu-rich-number) {
  color: #b9f08f;
  font-weight: 700;
}

:global(.fanxiu-inherit-tooltip .wiki-variable),
:global(.fanxiu-inherit-tooltip .fanxiu-rich-variable) {
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

.activity-reward-row {
  display: grid;
  gap: 8px;
}

.activity-raw-reward {
  padding-top: 4px;
  color: rgba(247, 240, 223, 0.64);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow-wrap: anywhere;
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

:deep(.wiki-term),
:deep(.fanxiu-rich-term) {
  color: var(--wiki-term-color, #ffd45f);
  font-weight: 700;
}

:deep(.wiki-number),
:deep(.fanxiu-rich-number) {
  color: var(--wiki-number-color, #b9f08f);
  font-weight: 700;
}

:deep(.wiki-variable),
:deep(.fanxiu-rich-variable) {
  color: var(--wiki-variable-color, #44d6df);
  font-weight: 800;
}

:deep(.wiki-resource-link),
:deep(.fanxiu-resource-link) {
  color: inherit;
  font-weight: 800;
  text-decoration: underline;
  text-decoration-color: rgba(68, 214, 223, 0.65);
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
  cursor: pointer;
}

:deep(.wiki-resource-link:hover),
:deep(.fanxiu-resource-link:hover) {
  color: #44d6df;
  text-decoration-color: currentColor;
}

:deep(.wiki-rich-color .wiki-term),
:deep(.wiki-rich-color .wiki-number),
:deep(.wiki-rich-color .wiki-variable),
:deep(.wiki-rich-color .fanxiu-rich-term),
:deep(.wiki-rich-color .fanxiu-rich-number),
:deep(.wiki-rich-color .fanxiu-rich-variable) {
  color: inherit;
  font-weight: inherit;
}

.reward-config-row {
  grid-template-columns: 46px minmax(0, 1fr);
}

.reward-config-badge,
.reward-config-detail-badge {
  display: grid;
  place-items: center;
  color: #7b4f0a;
  font-weight: 800;
  background: linear-gradient(135deg, #fff7d8, #e8c067);
  border: 1px solid rgba(174, 125, 32, 0.42);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.45);
}

.reward-config-badge {
  width: 46px;
  height: 46px;
  font-size: 18px;
}

.reward-config-detail-badge {
  width: 74px;
  height: 74px;
  font-size: 30px;
}

.reward-config-head {
  align-items: center;
}

.reward-config-item-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px;
}

.reward-config-item {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 9px 10px;
  color: #f7f0df;
  background: rgba(28, 29, 36, 0.22);
  border: 1px solid rgba(214, 196, 136, 0.22);
}

.reward-config-item strong,
.reward-config-item small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reward-config-item strong {
  color: #fff5cf;
  font-size: 14px;
}

.reward-config-item small {
  color: rgba(247, 240, 223, 0.64);
  font-size: 12px;
}

.reward-result-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
}

.reward-result-badges span {
  max-width: 100%;
  padding: 2px 5px;
  color: #f5d889;
  font-size: 11px;
  line-height: 1.25;
  background: rgba(255, 212, 95, 0.08);
  border: 1px solid rgba(255, 212, 95, 0.22);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reward-config-item .reward-result-note {
  line-height: 1.35;
  white-space: normal;
}

.reward-boundary-section {
  background: rgba(54, 45, 33, 0.82);
}

.reward-boundary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.reward-boundary-grid div {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 9px 10px;
  background: rgba(255, 244, 208, 0.07);
  border: 1px solid rgba(214, 196, 136, 0.22);
}

.reward-boundary-grid strong,
.reward-boundary-grid span,
.reward-boundary-grid small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reward-boundary-grid strong {
  color: #ffd45f;
  font-size: 13px;
}

.reward-boundary-grid span {
  color: #fff5cf;
  font-size: 13px;
}

.reward-boundary-grid small {
  color: rgba(247, 240, 223, 0.62);
  font-size: 12px;
}

.digitdoor-monster-section {
  background: rgba(34, 37, 42, 0.86);
}

.digitdoor-monster-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.digitdoor-monster-chip-row span {
  max-width: 100%;
  padding: 3px 7px;
  color: #f5d889;
  font-size: 12px;
  line-height: 1.35;
  background: rgba(255, 212, 95, 0.08);
  border: 1px solid rgba(255, 212, 95, 0.24);
  overflow-wrap: anywhere;
}

.digitdoor-monster-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}

.digitdoor-monster-card {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 9px 10px;
  background: rgba(255, 244, 208, 0.06);
  border: 1px solid rgba(214, 196, 136, 0.2);
}

.digitdoor-monster-card strong {
  color: #fff5cf;
  font-size: 14px;
}

.digitdoor-monster-card small,
.digitdoor-monster-card p {
  margin: 0;
  color: rgba(247, 240, 223, 0.64);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.digitdoor-door-pool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}

.digitdoor-door-pool-card {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 10px 11px;
  border: 1px solid rgba(214, 196, 136, 0.24);
  background: rgba(255, 244, 208, 0.055);
}

.digitdoor-door-pool-card header {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.digitdoor-door-pool-card strong {
  color: #fff5cf;
  font-size: 14px;
  line-height: 1.35;
}

.digitdoor-door-pool-card small {
  color: rgba(247, 240, 223, 0.58);
  font-size: 12px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.digitdoor-door-pool-meta,
.digitdoor-door-pool-effect-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.digitdoor-door-pool-meta span,
.digitdoor-door-pool-effect-list span {
  max-width: 100%;
  padding: 3px 7px;
  font-size: 12px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.digitdoor-door-pool-meta span {
  color: rgba(247, 240, 223, 0.68);
  border: 1px solid rgba(247, 240, 223, 0.16);
  background: rgba(247, 240, 223, 0.05);
}

.digitdoor-door-pool-effect-list span {
  display: grid;
  gap: 2px;
  color: #ffe6a4;
  border: 1px solid rgba(255, 212, 95, 0.3);
  background: rgba(255, 212, 95, 0.08);
}

.digitdoor-door-pool-effect-list b {
  color: inherit;
  font-weight: 700;
}

.digitdoor-door-pool-effect-list em {
  color: #b8f7a1;
  font-style: normal;
  line-height: 1.25;
}

.digitdoor-monster-skill-list {
  display: grid;
  gap: 3px;
  margin-top: 2px;
}

.digitdoor-monster-skill-list span {
  color: rgba(247, 240, 223, 0.7);
  font-size: 12px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.digitdoor-monster-skill-list em {
  display: block;
  margin-top: 2px;
  color: rgba(247, 240, 223, 0.58);
  font-style: normal;
}

.digitdoor-monster-skill-list em.param {
  color: rgba(178, 218, 255, 0.74);
}

.digitdoor-monster-skill-list em.projection {
  color: rgba(255, 178, 120, 0.78);
}

.digitdoor-monster-skill-list em.buff {
  color: rgba(181, 232, 159, 0.78);
}

.digitdoor-monster-skill-list em.formula {
  color: rgba(255, 212, 95, 0.76);
}

.digitdoor-monster-skill-list b {
  margin-right: 4px;
  color: #ffd45f;
  font-weight: 700;
}

.digitdoor-wave-table {
  display: grid;
  border-top: 1px solid rgba(214, 196, 136, 0.2);
}

.digitdoor-wave-row {
  display: grid;
  grid-template-columns: 48px minmax(100px, 1.1fr) minmax(120px, 1.4fr) minmax(110px, 1.1fr) minmax(90px, 1fr);
  gap: 8px;
  align-items: start;
  padding: 7px 0;
  border-bottom: 1px solid rgba(214, 196, 136, 0.16);
}

.digitdoor-wave-row.head {
  padding-top: 9px;
  color: rgba(247, 240, 223, 0.52);
  font-size: 12px;
}

.digitdoor-wave-row strong {
  color: #ffd45f;
}

.digitdoor-wave-row span {
  min-width: 0;
  color: #f7f0df;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.digitdoor-door-option-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 0 8px 56px;
  border-bottom: 1px solid rgba(214, 196, 136, 0.16);
}

.digitdoor-door-option-row span {
  max-width: 100%;
  padding: 3px 7px;
  border: 1px solid rgba(255, 212, 95, 0.34);
  background: rgba(255, 212, 95, 0.08);
  color: #ffe6a4;
  font-size: 12px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.digitdoor-door-option-row span.more {
  border-color: rgba(247, 240, 223, 0.18);
  background: rgba(247, 240, 223, 0.06);
  color: rgba(247, 240, 223, 0.68);
}

.digitdoor-door-option-row span.special {
  border-color: rgba(102, 224, 255, 0.34);
  background: rgba(102, 224, 255, 0.08);
  color: #b8edff;
}

.digitdoor-door-option-row span.special.more {
  border-color: rgba(102, 224, 255, 0.18);
  color: rgba(184, 237, 255, 0.7);
}

.doupo-skill-list,
.doupo-logic-list,
.doupo-strength-list {
  display: grid;
  gap: 12px;
}

.doupo-skill-item,
.doupo-logic-item,
.doupo-strength-item {
  padding: 10px 0 12px;
  border-bottom: 1px solid rgba(214, 196, 136, 0.22);
}

.doupo-skill-item:last-child,
.doupo-logic-item:last-child,
.doupo-strength-item:last-child {
  border-bottom: 0;
}

.doupo-logic-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.doupo-logic-chip-row span {
  padding: 2px 7px;
  color: #8b5a0a;
  font-size: 12px;
  background: rgba(255, 245, 213, 0.78);
  border: 1px solid rgba(214, 196, 136, 0.42);
}

.doupo-logic-chip-row.compact {
  margin-top: 0;
}

.doupo-logic-chip-row.compact span {
  color: rgba(255, 245, 213, 0.88);
  background: rgba(255, 245, 213, 0.08);
  border-color: rgba(214, 196, 136, 0.24);
}

.doupo-logic-chip-row.compact.flow {
  margin-top: 7px;
}

.doupo-logic-chip-row.compact.flow span {
  color: #c7e3ff;
  background: rgba(119, 173, 232, 0.1);
  border-color: rgba(119, 173, 232, 0.24);
}

.doupo-buff-list {
  display: grid;
  gap: 8px;
  margin-top: 9px;
}

.doupo-buff-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.9fr) minmax(130px, 0.7fr) minmax(180px, 1fr);
  gap: 10px;
  align-items: start;
  min-width: 0;
  padding: 8px 10px;
  background: rgba(28, 29, 36, 0.18);
  border: 1px solid rgba(214, 196, 136, 0.18);
}

.doupo-buff-main {
  min-width: 0;
}

.doupo-buff-main strong,
.doupo-buff-main small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doupo-buff-main strong {
  color: #fff5cf;
  font-size: 13px;
}

.doupo-buff-main small,
.doupo-buff-extra {
  color: rgba(247, 240, 223, 0.66);
  font-size: 12px;
}

.doupo-buff-extra {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.doupo-buff-flow {
  grid-column: 1 / -1;
  min-width: 0;
  padding-top: 7px;
  border-top: 1px solid rgba(214, 196, 136, 0.14);
}

.doupo-buff-flow p {
  margin: 0;
  color: rgba(247, 240, 223, 0.82);
  font-size: 12px;
  line-height: 1.65;
}

.doupo-buff-flow-functions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}

.doupo-buff-flow-functions span {
  padding: 2px 7px;
  color: rgba(247, 240, 223, 0.72);
  font-size: 12px;
  background: rgba(28, 29, 36, 0.28);
  border: 1px solid rgba(214, 196, 136, 0.16);
}

.doupo-source-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px;
}

.doupo-source-card {
  display: grid;
  gap: 9px;
  min-width: 0;
  padding: 11px 12px;
  color: #f7f0df;
  background: rgba(28, 29, 36, 0.22);
  border: 1px solid rgba(214, 196, 136, 0.24);
}

.doupo-source-card.compact-source {
  background: rgba(255, 244, 208, 0.055);
}

.doupo-source-head {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  gap: 9px;
  align-items: center;
  min-width: 0;
}

.doupo-source-head strong,
.doupo-source-head small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doupo-source-head strong {
  color: #fff5cf;
  font-size: 14px;
  font-weight: 760;
}

.doupo-source-head small {
  margin-top: 2px;
  color: rgba(247, 240, 223, 0.62);
  font-size: 12px;
}

.doupo-source-icon,
.doupo-source-badge {
  position: relative;
  display: grid;
  width: 38px;
  height: 38px;
  overflow: hidden;
  place-items: center;
  color: #efe2ad;
  background: rgba(246, 231, 184, 0.12);
  border: 1px solid rgba(214, 196, 136, 0.34);
}

.doupo-source-icon img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.doupo-source-badge {
  color: #ffd45f;
  font-size: 17px;
  font-weight: 800;
}

.doupo-source-lines,
.doupo-progress-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.doupo-source-lines span,
.doupo-progress-strip span {
  padding: 3px 7px;
  color: #fff5cf;
  font-size: 12px;
  line-height: 1.35;
  background: rgba(255, 244, 208, 0.07);
  border: 1px solid rgba(214, 196, 136, 0.22);
}

.doupo-progress-strip {
  margin-top: 10px;
}

.doupo-progress-strip strong {
  margin-right: 5px;
  color: #ffd45f;
}

.doupo-compose-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}

.doupo-compose-card {
  display: grid;
  gap: 10px;
  min-width: 0;
  padding: 12px;
  color: #f7f0df;
  background: rgba(28, 29, 36, 0.28);
  border: 1px solid rgba(214, 196, 136, 0.24);
}

.doupo-compose-head {
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.doupo-compose-head strong,
.doupo-compose-head small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doupo-compose-head strong {
  color: #fff5cf;
  font-size: 15px;
  font-weight: 760;
}

.doupo-compose-head small {
  margin-top: 2px;
  color: rgba(247, 240, 223, 0.62);
  font-size: 12px;
}

.doupo-compose-icon {
  position: relative;
  display: grid;
  width: 46px;
  height: 46px;
  overflow: hidden;
  place-items: center;
  color: #efe2ad;
  background: rgba(246, 231, 184, 0.12);
  border: 1px solid rgba(214, 196, 136, 0.34);
}

.doupo-compose-icon img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.doupo-attr-text {
  display: grid;
  gap: 4px;
  color: #f7f0df;
  font-size: 13px;
  line-height: 1.42;
}

.doupo-attr-text span {
  overflow-wrap: anywhere;
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

  .facet-panel {
    max-height: clamp(168px, 33vh, 300px);
  }

  .homemake-overview-head {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .homemake-overview-filter {
    justify-self: stretch;
  }

  .homemake-overview-list {
    grid-template-columns: 1fr;
  }

  .homemake-overview-row {
    grid-template-columns: 1fr;
  }

  .homemake-overview-links {
    max-width: none;
    justify-content: flex-start;
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

  .homemake-static-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .homemake-buff-row {
    grid-template-columns: 1fr;
  }

  .doupo-buff-row {
    grid-template-columns: 1fr;
  }

  .reward-boundary-grid {
    grid-template-columns: 1fr;
  }

  .digitdoor-wave-row {
    grid-template-columns: 42px minmax(0, 1fr);
  }

  .digitdoor-wave-row.head {
    display: none;
  }

  .digitdoor-door-option-row {
    padding-left: 0;
  }

  .homemake-buff-links {
    justify-content: flex-start;
  }

  .homemake-static-text {
    font-size: 17px;
  }

}
</style>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { FolderOpened, QuestionFilled, Refresh, Upload } from '@element-plus/icons-vue'
import { BarChart, type BarSeriesOption } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  type GridComponentOption,
  type TooltipComponentOption,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import type { ComposeOption, ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import StandardPagination from '@/components/StandardPagination.vue'

import {
  cloneFreebillProgramChannel,
  createFreebillDateRangeRule,
  createFreebillIncludeAllProgram,
  fetchFreebillCategoryBranchRecords,
  fetchFreebillDashboardByProgram,
  fetchFreebillFilterOptions,
  fetchFreebillInterpretRules,
  fetchFreebillSheetWorkbook,
  fetchFreebillStatus,
  importFreebillFiles,
  normalizeFreebillProgramChannel,
  recomputeFreebillInterpretRules,
  saveFreebillInterpretRules,
  saveFreebillCategoryBranchManualOverrides,
  saveFreebillRecordManualOverrides,
  clearFreebillRecordOverrides,
  refreshFreebillSheetWorkbook,
  upsertFreebillDateRangeRule,
  type FreebillBuiltInInterpretRule,
  type FreebillCategoryDimension,
  type FreebillCategoryPathItem,
  type FreebillCategoryStat,
  type FreebillCategoryBranchSortBy,
  type FreebillDashboard,
  type FreebillFilterOptions,
  type FreebillInterpretRule,
  type FreebillInterpretRuleField,
  type FreebillInterpretRuleOperator,
  type FreebillInterpretRuleSettings,
  type FreebillImportSource,
  type FreebillProgramChannel,
  type FreebillRecord,
  type FreebillSheetWorkbookSheet,
  type FreebillSheetWorkbook,
  type FreebillSortOrder,
  type FreebillStandardDirection,
  type FreebillStandardNature,
  type FreebillStatus,
  type FreebillTrendGranularity,
} from '@/api/freebill'
import SortableOrderHandle from '@/components/SortableOrderHandle.vue'
import { useSortableList } from '@/utils/useSortableList'
import FreebillProgramBar from './FreebillProgramBar.vue'
import NoteSheetWorkspace from '../components/NoteSheetWorkspace.vue'

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

type TrendChartOption = ComposeOption<BarSeriesOption | GridComponentOption | TooltipComponentOption>

const emptySummary = {
  total_income: 0,
  total_expense: 0,
  total_ignore: 0,
  total_other: 0,
  total_count: 0,
  balance: 0,
}

type DateRange = {
  start: Date
  end: Date
}

type BuiltInInterpretRuleGroup = {
  nature: FreebillStandardNature
  rules: FreebillBuiltInInterpretRule[]
}
type ConceptDocSection = {
  title: string
  paragraphs?: readonly string[]
  items?: readonly string[]
}

const FREEBILL_SHEET_TABS = [
  { key: 'records', label: '账单明细', emptyText: '暂无账单明细' },
  { key: 'monthly', label: '月度汇总', emptyText: '暂无月度汇总' },
  { key: 'categories', label: '分类汇总', emptyText: '暂无分类汇总' },
  { key: 'raw-files', label: '原始文件', emptyText: '暂无原始文件' },
] as const
type FreebillSheetTabKey = (typeof FREEBILL_SHEET_TABS)[number]['key']
type NoteSheetWorkspaceInstance = InstanceType<typeof NoteSheetWorkspace>
type CategoryDetailSortState = {
  field: FreebillCategoryBranchSortBy
  order: FreebillSortOrder
}
type CategoryBranchDetailState = {
  loading: boolean
  loaded: boolean
  page: number
  pageSize: number
  sortBy: FreebillCategoryBranchSortBy
  sortOrder: FreebillSortOrder
  total: number
  items: FreebillRecord[]
  error: string
}
type CategoryBranchRef = {
  path: FreebillCategoryPathItem[]
  label: string
  count: number
  value: number
}
type RecordManualOverrideField = {
  key: string
  label: string
  mode: 'text' | 'number' | 'direction' | 'nature'
}
type CategoryTreeRow = {
  key: string
  depth: number
  item: FreebillCategoryStat
  path: FreebillCategoryPathItem[]
}
type CategoryMatrixCellEntry = {
  key: string
  direction: string
  item: FreebillCategoryStat | null
  path: FreebillCategoryPathItem[]
  value: number
  count: number
  rows: CategoryTreeRow[]
}
type CategoryMatrixCell = {
  key: string
  direction: string
  entries: CategoryMatrixCellEntry[]
}
type CategoryMatrixRow = {
  key: string
  nature: string
  item: FreebillCategoryStat
  path: FreebillCategoryPathItem[]
  value: number
  count: number
  cells: CategoryMatrixCell[]
}
const SHEET_TAB_CONTEXT_MENU_WIDTH = 140
const SHEET_TAB_CONTEXT_MENU_HEIGHT = 90
const RECORD_SHEET_FILTER_FIELDS = [
  { value: '__all', label: '全部记录', mode: 'all' },
  { value: '交易时间', label: '交易时间', field: '交易时间', mode: 'date' },
  { value: '来源', label: '来源', field: '来源', mode: 'enum', enumKey: 'sources' },
  { value: '收支', label: '收支', field: '收支', mode: 'enum', enumKey: 'directions' },
  { value: '类型', label: '类型', field: '类型', mode: 'enum', enumKey: 'types' },
  { value: '分类', label: '分类', field: '分类', mode: 'enum', enumKey: 'categories' },
  { value: '__full_text', label: '全文搜索', mode: 'full_text' },
  { value: '交易对方', label: '交易对方', field: '交易对方', mode: 'text' },
  { value: '商品', label: '商品', field: '商品', mode: 'text' },
  { value: '金额', label: '金额', field: '金额', mode: 'number' },
  { value: '状态', label: '状态', field: '状态', mode: 'text' },
] as const
const RECORD_SHEET_BACKEND_FIELD_MAP: Record<string, string> = {
  create_time: '交易时间',
  source: '来源',
  direction: '收支',
  standard_nature: '类型',
  type: '分类',
  counterparty: '交易对方',
  product_name: '商品',
  amount: '金额',
  status: '状态',
}

const DAY_MS = 24 * 60 * 60 * 1000
const MIN_TREND_ZOOM_DAYS = 1
const FREEBILL_FILTER_STATE_STORAGE_KEY = 'codeyun.freebill.filterState.v1'
const CATEGORY_MATRIX_DIMENSIONS: FreebillCategoryDimension[] = ['standard_nature', 'standard_direction', 'type', 'counterparty']
const CATEGORY_MATRIX_DIRECTION_ORDER: FreebillStandardDirection[] = ['支出', '收入']
const CATEGORY_MATRIX_DIRECTION_SORT_ORDER: string[] = ['支出', '收支', '收入']
const CATEGORY_MATRIX_MERGED_DIRECTIONS: Record<string, string[]> = {
  支出: ['支出', '收支'],
}
const DEFAULT_CATEGORY_DIMENSIONS: FreebillCategoryDimension[] = CATEGORY_MATRIX_DIMENSIONS
const CATEGORY_DIMENSION_LABELS: Record<FreebillCategoryDimension, string> = {
  standard_direction: '收支',
  standard_nature: '类型',
  type: '分类',
  counterparty: '交易对方',
}
const CATEGORY_MATRIX_SUMMARY_TEXT = '类型 × 收支 / 分类 / 交易对方'
const CATEGORY_DETAIL_PAGE_SIZE = 10
const DEFAULT_TREND_STANDARD_NATURE: FreebillStandardNature = '常规'
const TREND_STANDARD_NATURE_OPTIONS: FreebillStandardNature[] = ['常规', '借贷', '理财', '转账', '流水']
const CATEGORY_DETAIL_DEFAULT_SORT: CategoryDetailSortState = { field: 'amount', order: 'desc' }
const CATEGORY_DETAIL_SORT_DEFAULT_ORDERS: Record<FreebillCategoryBranchSortBy, FreebillSortOrder> = {
  create_time: 'asc',
  source: 'asc',
  amount: 'desc',
  product_name: 'asc',
  remark: 'asc',
}
const FREEBILL_DATA_LAYER_DOC = [
  {
    title: '外部原始层',
    description: '用户手动导出的压缩包、邮件附件、验证码、解压密码和原始 xls/csv/xlsx 文件。',
    note: '只归档和记录来源元信息，不改文件内容。',
  },
  {
    title: '文件整理层',
    description: '整理到 m2402账单计算 这类目录中的标准化文件集合。',
    note: '可以解压、改目录、统一命名、转换文件格式，但不改业务含义。',
  },
  {
    title: '规范导入层',
    description: '程序把微信、支付宝、建行等来源对齐字段后写入数据库的结构化原始账单。',
    note: '保留导入时的收支、分类、商品、交易对方、来源文件和去重 key。',
  },
  {
    title: '解释规则层',
    description: '按当前前端和用户口径，把结构化账单解释成收支、类型等派生结果。',
    note: '例如不计收支重算成收入/支出或中立收支，余额宝内部流转归流水，信用借还归借贷，这类规则应可调整并可按版本重算。',
  },
] as const
const FREEBILL_CONCEPT_DOC: readonly ConceptDocSection[] = [
  {
    title: '定位',
    paragraphs: [
      'Freebill 不是手动记账工具，而是把支付宝、微信、银行卡等平台导出的真实流水导入系统，自动做分类、统计和查询。',
      '它不追求绝对精确，但要足够还原主要资金流向，让人能看清消费、借贷、理财和账户流转的真实规模。',
    ],
  },
  {
    title: '特色优势',
    items: [
      '解放双手：机器已经记录了绝大多数交易，用户不应该再逐笔手动记账。',
      '数据自由：把分散在微信、支付宝、银行卡等平台的数据导回本地，打破支付平台的数据孤岛。',
      '本地优先：数据存储在本机 SQLite 中，不需要上传到云端。',
      '统一视角：微信、支付宝、银行卡等资金流可以在同一个视图里分析。',
      '可编程性：底层是结构化数据库，后续可以用 SQL、脚本或规则继续深挖。',
    ],
  },
  {
    title: '处理流水线',
    paragraphs: [
      'Freebill 更像一条本地账单 ETL 流水线：原始账单 -> 字段标准化 -> SQLite 存储 -> 解释规则 -> 可视化分析。',
      '它关注的不是重复记录，而是把已经存在的交易事实整理成能复盘、能调整、能继续加工的数据资产。',
    ],
  },
  {
    title: '导入与去重',
    items: [
      '导出是最大的现实门槛：不同平台流程差异大，有的只能手机操作，有的要等邮件、验证码和解压密码。',
      '系统按交易号、来源、时间、金额、原始序号等信息做去重，同一批数据重复导入也不会重复统计。',
      '京东等缺少稳定批量导出的来源，后续更适合单独通过爬虫或自动化补充。',
    ],
  },
  {
    title: '五大类型',
    items: [
      '常规：日常消费支出、工资收入、普通经营性收支。',
      '借贷：信用卡、借呗、花呗、京东白条、借款和还款；人情往来确认后也可归入。',
      '理财：股票、基金、黄金、余额宝收益、余利宝等投资行为；买入是支出，赎回和收益是收入。',
      '转账：自己不同 App、银行卡、支付平台之间的资金移动，理论上成对出现并整体对冲。',
      '流水：App 内部更细粒度的账户流转，例如支付宝余额和余额宝之间的内部转移。',
    ],
  },
  {
    title: '分类层级',
    paragraphs: [
      '主要分析层级是：类型 -> 收支 -> 分类 -> 交易对方。',
      '支付宝账单本身提供了较好的分类和交易对方信息，叠加五大类型后，能更清楚地看到资金结构。',
    ],
  },
  {
    title: '人工修改',
    items: [
      '任意条目和分组都可以人工修改，适合把装修、借贷、人情往来等场景整理成自己的口径。',
      '人工修改不覆盖原始数据，也不影响判重；系统只在独立 JSON 字典里记录被改过的字段。',
      '查询和统计时，解释规则层会用人工字段覆盖原始字段，从而兼顾原始可追溯和分类可调整。',
    ],
  },
  {
    title: '当前边界',
    items: [
      '自动分类很难完全精确，尤其是平台内部转账、借贷、理财和退款混在一起时。',
      '数据时效性受导出流程限制，现阶段更适合年度或阶段性复盘。',
      '通用化还需要继续打磨导入流程、解释规则、手动批量调整和更多平台采集能力。',
    ],
  },
] as const
const DEFAULT_INTERPRET_RULE_FIELDS: FreebillInterpretRuleField[] = [
  { value: 'product_name', label: '商品', mode: 'text' },
  { value: 'remark', label: '备注', mode: 'text' },
  { value: 'counterparty', label: '交易对方', mode: 'text' },
  { value: 'type', label: '分类', mode: 'text' },
  { value: 'direction', label: '导入收支', mode: 'text' },
  { value: 'standard_direction', label: '当前收支', mode: 'text' },
  { value: 'standard_nature', label: '当前类型', mode: 'text' },
  { value: 'source', label: '来源', mode: 'text' },
  { value: 'status', label: '状态', mode: 'text' },
  { value: 'amount', label: '金额', mode: 'number' },
]
const DEFAULT_INTERPRET_RULE_OPERATORS: Array<{ value: FreebillInterpretRuleOperator; label: string }> = [
  { value: 'eq', label: '=' },
  { value: 'neq', label: '≠' },
  { value: 'contains', label: '包含' },
  { value: 'not_contains', label: '不包含' },
  { value: 'gt', label: '>' },
  { value: 'gte', label: '≥' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '≤' },
]
const INTERPRET_RULE_DIRECTIONS: FreebillStandardDirection[] = ['支出', '收支', '收入']
const INTERPRET_RULE_NATURES = TREND_STANDARD_NATURE_OPTIONS
const RECORD_MANUAL_OVERRIDE_FIELDS: RecordManualOverrideField[] = [
  { key: 'standard_nature', label: '类型', mode: 'nature' },
  { key: 'create_time', label: '交易时间', mode: 'text' },
  { key: 'standard_direction', label: '收支', mode: 'direction' },
  { key: 'source', label: '来源', mode: 'text' },
  { key: 'type', label: '分类', mode: 'text' },
  { key: 'amount', label: '金额', mode: 'number' },
  { key: 'counterparty', label: '交易对方', mode: 'text' },
  { key: 'status', label: '状态', mode: 'text' },
  { key: 'product_name', label: '商品', mode: 'text' },
  { key: 'pay_time', label: '付款时间', mode: 'text' },
  { key: 'remark', label: '备注', mode: 'text' },
  { key: 'modify_time', label: '修改时间', mode: 'text' },
  { key: 'location', label: '交易来源地', mode: 'text' },
  { key: 'fund_status', label: '资金状态', mode: 'text' },
  { key: 'service_fee', label: '服务费', mode: 'number' },
  { key: 'refund_amount', label: '退款金额', mode: 'number' },
  { key: 'account_no', label: '账号', mode: 'text' },
  { key: 'currency', label: '币种', mode: 'text' },
  { key: 'cash_type', label: '钞汇', mode: 'text' },
  { key: 'account_balance', label: '账户余额', mode: 'number' },
  { key: 'raw_sequence', label: '原始序号', mode: 'text' },
]
const CATEGORY_NATURE_DIRECTION_COLORS: Record<FreebillStandardNature, Record<FreebillStandardDirection, string>> = {
  常规: { 支出: '#f8cbc5', 收支: '#cbd5e1', 收入: '#df8a82' },
  借贷: { 支出: '#f4dba5', 收支: '#cbd5e1', 收入: '#d49a48' },
  理财: { 支出: '#d7cbf2', 收支: '#cbd5e1', 收入: '#9b85cf' },
  转账: { 支出: '#a9ded8', 收支: '#cbd5e1', 收入: '#62aaa2' },
  流水: { 支出: '#d7dee8', 收支: '#c5ceda', 收入: '#d7dee8' },
}
const CATEGORY_FALLBACK_DIRECTION_COLORS: Record<FreebillStandardDirection, string> = {
  支出: '#f8cbc5',
  收支: '#cbd5e1',
  收入: '#df8a82',
}

const loading = ref(false)
const sheetWorkbookLoading = ref(false)
const importingSource = ref<FreebillImportSource | ''>('')
const status = ref<FreebillStatus | null>(null)
const dashboard = ref<FreebillDashboard | null>(null)
const sheetWorkbook = ref<FreebillSheetWorkbook | null>(null)
const sheetReloadToken = ref(0)
const restoredFilterState = readFreebillFilterState()
const backendProgram = ref<FreebillProgramChannel>(restoredFilterState?.backendProgram ?? createFreebillIncludeAllProgram())
const frontendProgram = ref<FreebillProgramChannel>(restoredFilterState?.frontendProgram ?? createDefaultPassThroughProgram())
const sheetViewProgram = ref<FreebillProgramChannel>(restoredFilterState?.sheetViewProgram ?? createDefaultPassThroughProgram())
const categoryDimensions = ref<FreebillCategoryDimension[]>(restoredFilterState?.categoryDimensions ?? [...DEFAULT_CATEGORY_DIMENSIONS])
const filterOptions = ref<FreebillFilterOptions>({
  sources: [],
  directions: [],
  types: [],
  categories: [],
})
const trendGranularity = ref<FreebillTrendGranularity>(restoredFilterState?.trendGranularity ?? 'month')
const trendStandardNature = ref<FreebillStandardNature>(restoredFilterState?.trendStandardNature ?? DEFAULT_TREND_STANDARD_NATURE)
const alipayFileInput = ref<HTMLInputElement | null>(null)
const wechatFileInput = ref<HTMLInputElement | null>(null)
const ccbFileInput = ref<HTMLInputElement | null>(null)
const trendChartRef = ref<HTMLDivElement | null>(null)
const categoryDimensionListRef = ref<HTMLElement | null>(null)
const expandedCategoryKeys = ref<Set<string>>(new Set())
const sheetWorkspaceRefs = new Map<FreebillSheetTabKey, NoteSheetWorkspaceInstance>()
const autoExpandedCategoryKeys = new Set<string>()
const sheetTabContextMenu = ref({
  visible: false,
  key: null as FreebillSheetTabKey | null,
  left: 0,
  top: 0,
})
const selectedCategoryBranch = ref<CategoryBranchRef | null>(null)
const categoryDetailSort = ref<CategoryDetailSortState>({ ...CATEGORY_DETAIL_DEFAULT_SORT })
const categoryOrderEditorVisible = ref(false)
const conceptDocDialogVisible = ref(false)
const dataLayerDialogVisible = ref(false)
const interpretRulesDialogVisible = ref(false)
const interpretRulesLoading = ref(false)
const interpretRulesSaving = ref(false)
const interpretRulesApplying = ref(false)
const builtInInterpretRules = ref<FreebillBuiltInInterpretRule[]>([])
const interpretRuleSettings = ref<FreebillInterpretRuleSettings>({
  signed_category_values: false,
  built_in_rules: {},
})
const interpretRuleFields = ref<FreebillInterpretRuleField[]>([...DEFAULT_INTERPRET_RULE_FIELDS])
const interpretRuleOperators = ref<Array<{ value: FreebillInterpretRuleOperator; label: string }>>([...DEFAULT_INTERPRET_RULE_OPERATORS])
const interpretRules = ref<FreebillInterpretRule[]>([])
const interpretRuleListRef = ref<HTMLElement | null>(null)
const recordEditDialogVisible = ref(false)
const recordEditSaving = ref(false)
const recordEditTarget = ref<FreebillRecord | null>(null)
const recordEditForm = reactive<Record<string, string | number | null>>({})
const recordEditTouchedFields = reactive<Record<string, boolean>>({})
const branchBatchEditDialogVisible = ref(false)
const branchBatchEditSaving = ref(false)
const branchBatchEditTarget = ref<CategoryBranchRef | null>(null)
const branchBatchEditForm = reactive<Record<string, string | number | null>>({})
const branchBatchEditEnabled = reactive<Record<string, boolean>>({})
let trendZoomTimer: ReturnType<typeof window.setTimeout> | undefined
let trendChart: ECharts | null = null
let trendResizeObserver: ResizeObserver | undefined
let lastAppliedDefaultFrontendRangeKey = restoredFilterState?.lastAppliedDefaultFrontendRangeKey ?? ''
let persistedBackendProgram = cloneFreebillProgramChannel(backendProgram.value)
let suppressNextFrontendProgramQuery = false
const categoryBranchDetailCache = reactive<Record<string, CategoryBranchDetailState>>({})

const summary = computed(() => dashboard.value?.summary ?? emptySummary)
const selectedCategoryDetailState = computed(() => {
  const branch = selectedCategoryBranch.value
  if (!branch) return null
  return getCategoryDetailState(branch.path)
})
const builtInInterpretRuleGroups = computed<BuiltInInterpretRuleGroup[]>(() => {
  return TREND_STANDARD_NATURE_OPTIONS
    .map((nature) => ({
      nature,
      rules: builtInInterpretRules.value.filter((rule) => getBuiltInRuleTargetNature(rule) === nature),
    }))
    .filter((group) => group.rules.length > 0)
})
const trendGranularityOptions: Array<{ label: string; value: FreebillTrendGranularity }> = [
  { label: '日', value: 'day' },
  { label: '周', value: 'week' },
  { label: '月', value: 'month' },
  { label: '年', value: 'year' },
]
const trendUnitLabels: Record<FreebillTrendGranularity, string> = {
  day: '天',
  week: '周',
  month: '月',
  year: '年',
}
const trendUnitLabel = computed(() => {
  return trendUnitLabels[trendGranularity.value] ?? '期'
})
const trendItems = computed(() => {
  const items = dashboard.value?.monthly_trend ?? []
  return items.map((item, index) => {
    const fullPeriod = formatTrendPeriod(item.month)
    const previousFullPeriod = index > 0 ? formatTrendPeriod(items[index - 1]?.month) : ''
    const axisAnchor = isTrendAxisAnchor(fullPeriod, previousFullPeriod, index)
    return {
      ...item,
      fullPeriod,
      axisAnchor,
      axisLabel: buildTrendAxisLabel(fullPeriod, previousFullPeriod, index),
    }
  })
})
const trendChartStyle = computed(() => ({
  width: `${Math.max(360, 66 + trendItems.value.length * 44)}px`,
}))
const categoryTreeItems = computed(() => {
  const tree = dashboard.value?.category_tree ?? []
  if (tree.length) return tree
  return buildLegacyCategoryTree()
})
const categoryMatrixDirections = computed(() => {
  const directions: string[] = [...CATEGORY_MATRIX_DIRECTION_ORDER]
  const seen = new Set<string>(directions)
  const mergedDirections = new Set(Object.values(CATEGORY_MATRIX_MERGED_DIRECTIONS).flat())
  categoryTreeItems.value.forEach((natureItem) => {
    getCategoryChildren(natureItem).forEach((directionItem) => {
      const direction = String(directionItem.name || '').trim()
      if (mergedDirections.has(direction)) return
      if (!direction || seen.has(direction)) return
      directions.push(direction)
      seen.add(direction)
    })
  })
  const priority = new Map(CATEGORY_MATRIX_DIRECTION_SORT_ORDER.map((direction, index) => [direction, index]))
  return directions.sort((left, right) => (
    (priority.get(left) ?? CATEGORY_MATRIX_DIRECTION_SORT_ORDER.length) -
    (priority.get(right) ?? CATEGORY_MATRIX_DIRECTION_SORT_ORDER.length) ||
    left.localeCompare(right, 'zh-Hans-CN')
  ))
})
const categoryMatrixRows = computed<CategoryMatrixRow[]>(() => (
  categoryTreeItems.value.map((natureItem) => {
    const naturePath = resolveCategoryItemPath(natureItem, [], 0)
    const directionItems = getCategoryChildren(natureItem)
    const directionItemMap = new Map(directionItems.map((item) => [String(item.name || '').trim(), item]))
    const cells = categoryMatrixDirections.value.map((direction): CategoryMatrixCell => {
      const mergedDirections = CATEGORY_MATRIX_MERGED_DIRECTIONS[direction] ?? [direction]
      const entries = mergedDirections
        .map((entryDirection): CategoryMatrixCellEntry | null => {
          const directionItem = directionItemMap.get(entryDirection) ?? null
          if (!directionItem) return null
          const path = resolveCategoryItemPath(directionItem, naturePath, 1)
          return {
            key: `${getCategoryPathKey(naturePath)}/${entryDirection}`,
            direction: entryDirection,
            item: directionItem,
            path,
            value: Number(directionItem.value || 0),
            count: Number(directionItem.count || 0),
            rows: buildCategoryTreeRows(getCategoryChildren(directionItem), path),
          }
        })
        .filter((entry): entry is CategoryMatrixCellEntry => Boolean(entry))
      return {
        key: `${getCategoryPathKey(naturePath)}/${direction}`,
        direction,
        entries,
      }
    })
    return {
      key: getCategoryPathKey(naturePath),
      nature: natureItem.name,
      item: natureItem,
      path: naturePath,
      value: Number(natureItem.value || 0),
      count: Number(natureItem.count || 0),
      cells,
    }
  })
))
const maxCategoryMatrixReferenceValue = computed(() => {
  const primaryValues = categoryMatrixRows.value
    .filter((row) => !isFlowNature(row.nature))
    .map((row) => Math.abs(row.value))
  const values = primaryValues.length
    ? primaryValues
    : categoryMatrixRows.value.map((row) => Math.abs(row.value))
  return Math.max(0, ...values)
})
const selectedCategoryPathDimensions = computed(() => (
  new Set((selectedCategoryBranch.value?.path ?? []).map((item) => item.dimension))
))
const categoryDetailContextColumns = computed(() => {
  const hiddenDimensions = selectedCategoryPathDimensions.value
  return [
    { key: 'standard_direction', dimension: 'standard_direction', label: '收支', value: (record: FreebillRecord) => getRecordStandardDirectionLabel(record) } as const,
    { key: 'standard_nature', dimension: 'standard_nature', label: '类型', value: (record: FreebillRecord) => record.standard_nature } as const,
    { key: 'type', dimension: 'type', label: '分类', value: (record: FreebillRecord) => record.type } as const,
    { key: 'counterparty', dimension: 'counterparty', label: '交易对方', value: (record: FreebillRecord) => record.counterparty } as const,
  ].filter((column) => !hiddenDimensions.has(column.dimension))
})
const workbookId = computed(() => sheetWorkbook.value?.workbook.id ?? null)
const sheetTabs = computed(() => FREEBILL_SHEET_TABS.map((tab) => ({
  ...tab,
  sheet: getSheetItem(tab.key),
})))
const activeSheetKey = ref<(typeof FREEBILL_SHEET_TABS)[number]['key']>('records')
const recordsBaseRowFilterPrograms = computed(() => [
  mapProgramFields(backendProgram.value, RECORD_SHEET_BACKEND_FIELD_MAP),
  mapProgramFields(frontendProgram.value, RECORD_SHEET_BACKEND_FIELD_MAP),
])
const sheetTabContextMenuTab = computed(() => (
  sheetTabs.value.find((tab) => tab.key === sheetTabContextMenu.value.key) ?? null
))

useSortableList({
  listRef: categoryDimensionListRef,
  getDeps: () => [categoryOrderEditorVisible.value, categoryDimensions.value.join('|')],
  isEnabled: () => categoryOrderEditorVisible.value,
  onReorder: reorderCategoryDimensions,
})

useSortableList({
  listRef: interpretRuleListRef,
  getDeps: () => [interpretRulesDialogVisible.value, interpretRules.value.length],
  isEnabled: () => interpretRulesDialogVisible.value,
  onReorder: reorderInterpretRules,
})

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '读取失败'
}

async function loadStatus() {
  status.value = await fetchFreebillStatus()
}

async function loadDashboard() {
  clearCategoryDetailCache()
  dashboard.value = await fetchFreebillDashboardByProgram({
    program: backendProgram.value,
    programs: [backendProgram.value, frontendProgram.value],
    trend_granularity: trendGranularity.value,
    trend_standard_nature: trendStandardNature.value,
    category_dimensions: CATEGORY_MATRIX_DIMENSIONS,
  })
  syncSelectedCategoryBranchWithDashboard()
  await reloadSelectedCategoryBranchDetail()
}

async function loadFilterOptions() {
  filterOptions.value = await fetchFreebillFilterOptions()
}

async function openInterpretRulesDialog() {
  interpretRulesDialogVisible.value = true
  await loadInterpretRules()
}

async function loadInterpretRules() {
  interpretRulesLoading.value = true
  try {
    const payload = await fetchFreebillInterpretRules()
    builtInInterpretRules.value = (payload.built_in_rules ?? []).map((rule) => ({
      ...rule,
      enabled: rule.enabled !== false,
    }))
    interpretRuleSettings.value = {
      signed_category_values: payload.settings?.signed_category_values ?? false,
      built_in_rules: payload.settings?.built_in_rules ?? {},
    }
    interpretRuleFields.value = payload.fields?.length ? payload.fields : [...DEFAULT_INTERPRET_RULE_FIELDS]
    interpretRuleOperators.value = payload.operators?.length ? payload.operators : [...DEFAULT_INTERPRET_RULE_OPERATORS]
    interpretRules.value = (payload.rules ?? []).map((rule, index) => normalizeInterpretRuleDraft(rule, index))
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    interpretRulesLoading.value = false
  }
}

function createInterpretRuleDraft(): FreebillInterpretRule {
  return {
    name: '',
    enabled: true,
    order_index: interpretRules.value.length,
    matcher: {
      kind: 'field',
      field: 'product_name',
      op: 'contains',
      value: '',
      values: [],
      ignore_case: true,
    },
    set_direction: null,
    set_nature: null,
    note: null,
    match_count: 0,
  }
}

function normalizeInterpretRuleDraft(rule: Partial<FreebillInterpretRule>, index: number): FreebillInterpretRule {
  return {
    id: rule.id ?? null,
    name: rule.name ?? '',
    enabled: rule.enabled ?? true,
    order_index: index,
    matcher: {
      kind: rule.matcher?.kind ?? 'field',
      field: rule.matcher?.field ?? 'product_name',
      op: rule.matcher?.op ?? 'contains',
      value: rule.matcher?.value ?? '',
      values: rule.matcher?.values ?? [],
      ignore_case: rule.matcher?.ignore_case ?? true,
    },
    set_direction: rule.set_direction ?? null,
    set_nature: rule.set_nature ?? null,
    note: rule.note ?? null,
    match_count: rule.match_count ?? 0,
  }
}

function getBuiltInRuleTargetNature(rule: FreebillBuiltInInterpretRule): FreebillStandardNature {
  const targetNature = rule.target_nature
  if (targetNature && TREND_STANDARD_NATURE_OPTIONS.includes(targetNature)) {
    return targetNature
  }
  const match = (rule.result_text || '').match(/类型=([^，,]+)/)
  const inferred = (match?.[1] || '').trim() as FreebillStandardNature
  return TREND_STANDARD_NATURE_OPTIONS.includes(inferred) ? inferred : DEFAULT_TREND_STANDARD_NATURE
}

function getBuiltInRuleResultText(rule: FreebillBuiltInInterpretRule) {
  const resultText = (rule.result_text || '')
    .replace(/类型=[^，,]+[，,]?\s*/, '')
    .replace(/[，,]\s*$/, '')
    .trim()
  return resultText || rule.result_text
}

function addInterpretRule() {
  interpretRules.value.push(createInterpretRuleDraft())
}

function removeInterpretRule(index: number) {
  interpretRules.value.splice(index, 1)
  normalizeInterpretRuleOrder()
}

function reorderInterpretRules(oldIndex: number, newIndex: number) {
  interpretRules.value = moveArrayItem(interpretRules.value, oldIndex, newIndex)
  normalizeInterpretRuleOrder()
}

function normalizeInterpretRuleOrder() {
  interpretRules.value.forEach((rule, index) => {
    rule.order_index = index
  })
}

function getInterpretRuleField(field?: string | null) {
  return interpretRuleFields.value.find((item) => item.value === field) ?? interpretRuleFields.value[0]
}

function getInterpretRuleFieldMode(rule: FreebillInterpretRule) {
  return getInterpretRuleField(rule.matcher.field).mode
}

function getInterpretRuleOperators(rule: FreebillInterpretRule) {
  const mode = getInterpretRuleFieldMode(rule)
  return interpretRuleOperators.value.filter((operator) => {
    if (mode === 'number') {
      return ['eq', 'neq', 'gt', 'gte', 'lt', 'lte'].includes(operator.value)
    }
    return ['eq', 'neq', 'contains', 'not_contains'].includes(operator.value)
  })
}

function updateInterpretRuleKind(rule: FreebillInterpretRule) {
  if (rule.matcher.kind === 'field') {
    rule.matcher.field = rule.matcher.field || 'product_name'
    updateInterpretRuleField(rule)
  } else if (rule.matcher.kind === 'full_text_contains') {
    rule.matcher.value = typeof rule.matcher.value === 'string' ? rule.matcher.value : ''
    rule.matcher.field = null
    rule.matcher.op = null
  } else {
    rule.matcher.field = null
    rule.matcher.op = null
    rule.matcher.value = ''
  }
}

function updateInterpretRuleField(rule: FreebillInterpretRule) {
  const mode = getInterpretRuleFieldMode(rule)
  rule.matcher.op = mode === 'number' ? 'gte' : 'contains'
  rule.matcher.value = mode === 'number' ? 0 : ''
}

function normalizeInterpretRulesForSave() {
  normalizeInterpretRuleOrder()
  return interpretRules.value.map((rule, index) => ({
    ...rule,
    name: (rule.name || '').trim(),
    order_index: index,
    matcher: {
      ...rule.matcher,
      value: rule.matcher.value ?? '',
      values: rule.matcher.values ?? [],
      ignore_case: rule.matcher.ignore_case ?? true,
    },
    set_direction: rule.set_direction || null,
    set_nature: rule.set_nature || null,
    note: rule.note || null,
  }))
}

function normalizeInterpretSettingsForSave(): FreebillInterpretRuleSettings {
  return {
    signed_category_values: interpretRuleSettings.value.signed_category_values,
    built_in_rules: Object.fromEntries(
      builtInInterpretRules.value.map((rule) => [rule.key, rule.enabled !== false]),
    ),
  }
}

async function saveInterpretRulesOnly() {
  interpretRulesSaving.value = true
  try {
    const payload = await saveFreebillInterpretRules(normalizeInterpretRulesForSave(), normalizeInterpretSettingsForSave())
    builtInInterpretRules.value = (payload.built_in_rules ?? []).map((rule) => ({
      ...rule,
      enabled: rule.enabled !== false,
    }))
    interpretRuleSettings.value = {
      signed_category_values: payload.settings?.signed_category_values ?? false,
      built_in_rules: payload.settings?.built_in_rules ?? {},
    }
    interpretRules.value = (payload.rules ?? []).map((rule, index) => normalizeInterpretRuleDraft(rule, index))
    ElMessage.success('解释规则已保存')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    interpretRulesSaving.value = false
  }
}

async function applyInterpretRules() {
  interpretRulesApplying.value = true
  try {
    await saveFreebillInterpretRules(normalizeInterpretRulesForSave(), normalizeInterpretSettingsForSave())
    const result = await recomputeFreebillInterpretRules()
    interpretRules.value = (result.rules ?? []).map((rule, index) => normalizeInterpretRuleDraft(rule, index))
    await loadStatus()
    await Promise.all([
      loadDashboard(),
      loadFilterOptions(),
      refreshFreebillSheetWorkbook().then((payload) => applySheetWorkbook(payload)),
    ])
    ElMessage.success(`解释规则已应用，更新 ${formatNumber(result.updated)} 条`)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    interpretRulesApplying.value = false
  }
}

async function loadSheetWorkbook() {
  const payload = await fetchFreebillSheetWorkbook()
  if (isCompleteSheetWorkbook(payload)) {
    applySheetWorkbook(payload)
    return
  }

  applySheetWorkbook(await refreshFreebillSheetWorkbook())
}

async function refreshSheetWorkbook() {
  sheetWorkbookLoading.value = true
  try {
    applySheetWorkbook(await refreshFreebillSheetWorkbook())
    ElMessage.success('星云表格已刷新')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    sheetWorkbookLoading.value = false
  }
}

function openWorkbookFile() {
  if (!workbookId.value) return
  const activeSheet = getSheetItem(activeSheetKey.value)
  const query = activeSheet ? `?sheet=${activeSheet.sheet_id}` : ''
  window.open(`/workbook/${workbookId.value}${query}`, '_blank', 'noopener')
}

function openWorkbookSheet(sheet: FreebillSheetWorkbookSheet | null | undefined) {
  if (!workbookId.value || !sheet) return
  window.open(`/workbook/${workbookId.value}?sheet=${sheet.sheet_id}`, '_blank', 'noopener')
}

async function refreshAll() {
  loading.value = true
  try {
    await loadStatus()
    suppressNextFrontendProgramQuery = ensureFrontendDefaultProgram()
    await Promise.all([
      loadDashboard(),
      loadFilterOptions(),
      loadSheetWorkbook(),
    ])
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function executeQuery() {
  try {
    await loadDashboard()
    return true
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
    return false
  }
}

async function applyBackendProgram() {
  syncTrendGranularityToProgramRange()
  if (await executeQuery()) {
    persistedBackendProgram = cloneFreebillProgramChannel(backendProgram.value)
    persistFreebillFilterState()
  }
}

async function changeTrendGranularity() {
  try {
    await loadDashboard()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

async function changeTrendStandardNature() {
  try {
    await loadDashboard()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

function resetSheetViewProgram() {
  sheetViewProgram.value = createDefaultPassThroughProgram()
}

function resetFrontendProgram() {
  ensureFrontendDefaultProgram(true)
  syncTrendGranularityToProgramRange()
}

function applySheetWorkbook(payload: FreebillSheetWorkbook | null | undefined) {
  sheetWorkbook.value = payload ?? null
  if (payload) {
    sheetReloadToken.value += 1
  }
}

function isCompleteSheetWorkbook(payload: FreebillSheetWorkbook | null | undefined) {
  if (!payload?.workbook?.id) return false
  const sheetKeys = new Set(payload.sheets.map((sheet) => sheet.key))
  return FREEBILL_SHEET_TABS.every((tab) => sheetKeys.has(tab.key))
}

function getSheetItem(key: string): FreebillSheetWorkbookSheet | null {
  return sheetWorkbook.value?.sheets.find((sheet) => sheet.key === key) ?? null
}

function getSheetWorkspaceKey(key: string) {
  const sheet = getSheetItem(key)
  return `${workbookId.value ?? 'none'}:${sheet?.sheet_id ?? 'none'}:${sheet?.updated_at ?? 0}:${sheetReloadToken.value}`
}

function mapProgramFields(program: FreebillProgramChannel, fieldMap: Record<string, string>) {
  const draft = cloneFreebillProgramChannel(program)
  draft.rules.forEach((rule) => {
    if (rule.matcher.kind !== 'field' || !rule.matcher.field) return
    rule.matcher.field = fieldMap[rule.matcher.field] ?? rule.matcher.field
  })
  return draft
}

function ensureFrontendDefaultProgram(force = false) {
  const range = getLatestDataYearRange()
  if (!range) {
    if (force) {
      frontendProgram.value = createDefaultPassThroughProgram()
      lastAppliedDefaultFrontendRangeKey = ''
      return true
    }
    return false
  }

  const nextProgram = createFrontendDateRangeProgram(range)
  const nextRangeKey = getProgramDateRangeKey(nextProgram, 'create_time')
  const currentRangeKey = getProgramDateRangeKey(frontendProgram.value, 'create_time')
  const shouldApply = force
    || isIncludeAllProgram(frontendProgram.value)
    || !frontendProgram.value.rules.length
    || (
      isOnlyDateRangeProgram(frontendProgram.value, 'create_time')
      && (!lastAppliedDefaultFrontendRangeKey || currentRangeKey === lastAppliedDefaultFrontendRangeKey)
    )

  if (!shouldApply) return false
  frontendProgram.value = nextProgram
  lastAppliedDefaultFrontendRangeKey = nextRangeKey
  syncTrendGranularityToProgramRange()
  return true
}

function createDefaultPassThroughProgram(): FreebillProgramChannel {
  return {
    default: true,
    rules: [],
  }
}

type FreebillFilterState = {
  backendProgram: FreebillProgramChannel
  frontendProgram: FreebillProgramChannel
  sheetViewProgram: FreebillProgramChannel
  trendGranularity: FreebillTrendGranularity
  trendStandardNature: FreebillStandardNature
  categoryDimensions: FreebillCategoryDimension[]
  lastAppliedDefaultFrontendRangeKey: string
}

function canUseLocalStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function normalizeTrendGranularityValue(value: unknown): FreebillTrendGranularity {
  return value === 'day' || value === 'week' || value === 'year' ? value : 'month'
}

function normalizeTrendStandardNatureValue(value: unknown): FreebillStandardNature {
  return TREND_STANDARD_NATURE_OPTIONS.includes(value as FreebillStandardNature)
    ? value as FreebillStandardNature
    : DEFAULT_TREND_STANDARD_NATURE
}

function normalizeStoredProgram(value: unknown, fallback: FreebillProgramChannel) {
  return isRecord(value)
    ? normalizeFreebillProgramChannel(value)
    : cloneFreebillProgramChannel(fallback)
}

function normalizeStoredCategoryDimensions(value: unknown): FreebillCategoryDimension[] {
  const values = Array.isArray(value) ? value : []
  const normalized: FreebillCategoryDimension[] = []
  const seen = new Set<FreebillCategoryDimension>()
  values.forEach((item) => {
    const candidate = String(item || '').trim() as FreebillCategoryDimension
    if (!(candidate in CATEGORY_DIMENSION_LABELS) || seen.has(candidate)) return
    normalized.push(candidate)
    seen.add(candidate)
  })
  return normalized.length ? normalized : [...DEFAULT_CATEGORY_DIMENSIONS]
}

function readFreebillFilterState(): FreebillFilterState | null {
  if (!canUseLocalStorage()) return null

  try {
    const raw = window.localStorage.getItem(FREEBILL_FILTER_STATE_STORAGE_KEY)
    if (!raw) return null
    const payload = JSON.parse(raw) as Record<string, unknown>
    return {
      backendProgram: normalizeStoredProgram(payload.backendProgram, createFreebillIncludeAllProgram()),
      frontendProgram: normalizeStoredProgram(payload.frontendProgram, createDefaultPassThroughProgram()),
      sheetViewProgram: normalizeStoredProgram(payload.sheetViewProgram, createDefaultPassThroughProgram()),
      trendGranularity: normalizeTrendGranularityValue(payload.trendGranularity),
      trendStandardNature: normalizeTrendStandardNatureValue(payload.trendStandardNature),
      categoryDimensions: normalizeStoredCategoryDimensions(payload.categoryDimensions),
      lastAppliedDefaultFrontendRangeKey: typeof payload.lastAppliedDefaultFrontendRangeKey === 'string'
        ? payload.lastAppliedDefaultFrontendRangeKey
        : '',
    }
  } catch (error) {
    console.warn('Failed to restore freebill filter state', error)
    window.localStorage.removeItem(FREEBILL_FILTER_STATE_STORAGE_KEY)
    return null
  }
}

function persistFreebillFilterState() {
  if (!canUseLocalStorage()) return

  window.localStorage.setItem(FREEBILL_FILTER_STATE_STORAGE_KEY, JSON.stringify({
    version: 1,
    updatedAt: Date.now(),
    backendProgram: cloneFreebillProgramChannel(persistedBackendProgram),
    frontendProgram: cloneFreebillProgramChannel(frontendProgram.value),
    sheetViewProgram: cloneFreebillProgramChannel(sheetViewProgram.value),
    trendGranularity: normalizeTrendGranularityValue(trendGranularity.value),
    trendStandardNature: normalizeTrendStandardNatureValue(trendStandardNature.value),
    categoryDimensions: [...categoryDimensions.value],
    lastAppliedDefaultFrontendRangeKey,
  }))
}

function createFrontendDateRangeProgram(range: DateRange): FreebillProgramChannel {
  const normalized = normalizeDateRange(range)
  return {
    default: true,
    rules: [
      createFreebillDateRangeRule(
        'create_time',
        formatLocalDate(normalized.start),
        formatLocalDate(normalized.end),
      ),
    ],
  }
}

function isIncludeAllProgram(program: FreebillProgramChannel) {
  return program.default === false
    && program.rules.length === 1
    && program.rules[0]?.action === 'include'
    && program.rules[0]?.matcher.kind === 'all'
}

function isOnlyDateRangeProgram(program: FreebillProgramChannel, field: string) {
  const rule = program.rules[0]
  return program.default === true
    && program.rules.length === 1
    && rule?.action === 'filter'
    && rule.matcher.kind === 'field'
    && rule.matcher.field === field
    && (rule.matcher.op === 'between' || rule.matcher.op === 'year')
}

function getProgramDateRangeKey(program: FreebillProgramChannel, field: string) {
  const range = getProgramDateRange(program, field)
  if (!range) return ''
  return `${formatLocalDate(range.start)}:${formatLocalDate(range.end)}`
}

function setSheetWorkspaceRef(key: FreebillSheetTabKey, instance: unknown) {
  if (instance) {
    sheetWorkspaceRefs.set(key, instance as NoteSheetWorkspaceInstance)
  } else {
    sheetWorkspaceRefs.delete(key)
  }
}

function closeSheetTabContextMenu() {
  sheetTabContextMenu.value.visible = false
}

function positionSheetTabContextMenu(event: MouseEvent) {
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight
  sheetTabContextMenu.value.left = Math.max(8, Math.min(event.clientX, viewportWidth - SHEET_TAB_CONTEXT_MENU_WIDTH - 8))
  sheetTabContextMenu.value.top = Math.max(8, Math.min(event.clientY, viewportHeight - SHEET_TAB_CONTEXT_MENU_HEIGHT - 8))
}

function openSheetTabContextMenu(
  event: MouseEvent,
  tab: { key: FreebillSheetTabKey; sheet: FreebillSheetWorkbookSheet | null },
) {
  if (!tab.sheet) return
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()

  activeSheetKey.value = tab.key
  positionSheetTabContextMenu(event)
  sheetTabContextMenu.value.key = tab.key
  sheetTabContextMenu.value.visible = true
}

async function waitForSheetWorkspaceRef(key: FreebillSheetTabKey) {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await nextTick()
    const workspace = sheetWorkspaceRefs.get(key)
    if (workspace) {
      return workspace
    }
    await new Promise((resolve) => window.requestAnimationFrame(resolve))
  }
  return sheetWorkspaceRefs.get(key) ?? null
}

async function configureSheetFromTabContextMenu() {
  const key = sheetTabContextMenu.value.key
  closeSheetTabContextMenu()
  if (!key) return

  activeSheetKey.value = key
  const workspace = await waitForSheetWorkspaceRef(key)
  if (!workspace) {
    ElMessage.warning('工作表还在加载')
    return
  }
  workspace.openSheetSettings?.()
}

function openWorkbookFromTabContextMenu() {
  const tab = sheetTabContextMenuTab.value
  closeSheetTabContextMenu()
  openWorkbookSheet(tab?.sheet)
}

function handleGlobalMouseDown(event: MouseEvent) {
  const target = event.target
  if (sheetTabContextMenu.value.visible) {
    if (!(target instanceof HTMLElement && target.closest('.freebill-sheet-tab-context-menu'))) {
      closeSheetTabContextMenu()
    }
  }
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeSheetTabContextMenu()
  }
}

function handleTrendWheel(event: WheelEvent) {
  if (!event.ctrlKey) return
  event.preventDefault()

  const bounds = getDataDateBounds()
  const currentRange = getActiveTrendDateRange()
  if (!bounds || !currentRange) return

  const target = event.currentTarget as HTMLElement | null
  const rect = target?.getBoundingClientRect()
  const rawRatio = rect && rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0.5
  const anchorRatio = clampNumber(rawRatio, 0.02, 0.98)
  const zoomFactor = event.deltaY < 0 ? 0.72 : 1.38
  const nextRange = zoomDateRange(currentRange, anchorRatio, zoomFactor, bounds)
  const nextStart = formatLocalDate(nextRange.start)
  const nextEnd = formatLocalDate(nextRange.end)
  const currentProgramRange = getProgramDateRange(frontendProgram.value, 'create_time')
  if (
    currentProgramRange
    && nextStart === formatLocalDate(currentProgramRange.start)
    && nextEnd === formatLocalDate(currentProgramRange.end)
  ) return

  frontendProgram.value = ensureDateRangeRuleFirst(
    upsertFreebillDateRangeRule(frontendProgram.value, 'create_time', nextStart, nextEnd),
    'create_time',
  )
  trendGranularity.value = pickTrendGranularity(nextRange)
}

function ensureDateRangeRuleFirst(program: FreebillProgramChannel, field: string) {
  const draft = cloneFreebillProgramChannel(program)
  const index = draft.rules.findIndex((rule) => (
    rule.matcher.kind === 'field'
    && rule.matcher.field === field
    && rule.matcher.op === 'between'
  ))
  if (index > 0) {
    const [rule] = draft.rules.splice(index, 1)
    if (rule) draft.rules.unshift(rule)
  }
  return draft
}

function scheduleTrendZoomQuery() {
  if (trendZoomTimer !== undefined) {
    window.clearTimeout(trendZoomTimer)
  }
  trendZoomTimer = window.setTimeout(() => {
    trendZoomTimer = undefined
    void executeQuery()
  }, 160)
}

function openFilePicker(source: FreebillImportSource) {
  if (source === 'alipay') {
    alipayFileInput.value?.click()
  } else if (source === 'wechat') {
    wechatFileInput.value?.click()
  } else {
    ccbFileInput.value?.click()
  }
}

function getImportSourceLabel(source: FreebillImportSource) {
  if (source === 'alipay') return '支付宝 CSV'
  if (source === 'wechat') return '微信 Excel'
  return '建行 Excel'
}

async function handleFileInput(source: FreebillImportSource, event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (!files.length) return

  importingSource.value = source
  try {
    const result = await importFreebillFiles(source, files)
    const sourceLabel = getImportSourceLabel(source)
    const message = `${sourceLabel}导入完成：新增 ${result.inserted} 条，跳过 ${result.skipped} 条`
    if (result.error_count) {
      const firstError = result.results.find((item) => item.status === 'error')?.error
      ElMessage.warning(firstError ? `${message}，失败 ${result.error_count} 个：${firstError}` : `${message}，失败 ${result.error_count} 个`)
    } else {
      ElMessage.success(message)
    }
    await refreshAll()
    if (sheetWorkbook.value) {
      await refreshSheetWorkbook()
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    importingSource.value = ''
    input.value = ''
  }
}

function formatNumber(value: number | null | undefined) {
  return Number(value || 0).toLocaleString()
}

function formatMoney(value: number | null | undefined) {
  const numberValue = Number(value || 0)
  const normalized = Object.is(numberValue, -0) ? 0 : numberValue
  return formatSignificantUnitNumber(normalized)
}

function formatCompactMoney(value: number | null | undefined) {
  const numberValue = Number(value || 0)
  return formatSignificantUnitNumber(numberValue)
}

function formatSignificantUnitNumber(value: number, significantDigits = 4) {
  if (!Number.isFinite(value)) return '0'
  const normalized = Object.is(value, -0) ? 0 : value
  const sign = normalized < 0 ? '-' : ''
  const absValue = Math.abs(normalized)
  if (absValue >= 100000000) {
    return `${sign}${formatSignificantDigits(absValue / 100000000, significantDigits)}亿`
  }
  if (absValue >= 10000) {
    return `${sign}${formatSignificantDigits(absValue / 10000, significantDigits)}万`
  }
  return `${sign}${formatSignificantDigits(absValue, significantDigits, false)}`
}

function formatSignificantDigits(value: number, significantDigits: number, useGrouping = true) {
  if (!Number.isFinite(value) || value === 0) return '0'
  const decimalDigits = Math.max(0, significantDigits - Math.floor(Math.log10(Math.abs(value))) - 1)
  return value.toLocaleString(undefined, {
    useGrouping,
    minimumFractionDigits: 0,
    maximumFractionDigits: decimalDigits,
  })
}

function formatCategoryBarLabel(value: number | null | undefined) {
  return formatCompactMoney(value)
}

function buildLegacyCategoryTree(): FreebillCategoryStat[] {
  const tree: FreebillCategoryStat[] = []
  if (dashboard.value?.expense_categories.length) {
    tree.push({
      name: '支出',
      value: dashboard.value.summary.total_expense,
      count: dashboard.value.expense_categories.reduce((sum, item) => sum + Number(item.count || 0), 0),
      children: dashboard.value.expense_categories,
    })
  }
  if (dashboard.value?.income_categories.length) {
    tree.push({
      name: '收入',
      value: dashboard.value.summary.total_income,
      count: dashboard.value.income_categories.reduce((sum, item) => sum + Number(item.count || 0), 0),
      children: dashboard.value.income_categories,
    })
  }
  return tree
}

function flattenCategoryValues(items: FreebillCategoryStat[]): number[] {
  const values: number[] = []
  items.forEach((item) => {
    values.push(Math.abs(Number(item.value || 0)))
    values.push(...flattenCategoryValues(getCategoryChildren(item)))
  })
  return values
}

function buildCategoryTreeRows(
  items: FreebillCategoryStat[],
  parentPath: FreebillCategoryPathItem[],
  depth = 0,
): CategoryTreeRow[] {
  const rows: CategoryTreeRow[] = []
  const visit = (item: FreebillCategoryStat, rowDepth: number, currentParentPath: FreebillCategoryPathItem[]) => {
    const path = resolveCategoryItemPath(item, currentParentPath, rowDepth + parentPath.length)
    const key = getCategoryPathKey(path)
    rows.push({ key, depth: rowDepth, item, path })
    if (!hasCategoryChildren(item) || !isCategoryExpanded(path)) return
    getCategoryChildren(item).forEach((child) => visit(child, rowDepth + 1, path))
  }
  items.forEach((item) => visit(item, depth, parentPath))
  return rows
}

function getCategoryChildren(item: FreebillCategoryStat) {
  return Array.isArray(item.children) ? item.children : []
}

function hasCategoryChildren(item: FreebillCategoryStat) {
  return getCategoryChildren(item).length > 0
}

function resolveCategoryItemPath(
  item: FreebillCategoryStat,
  parentPath: FreebillCategoryPathItem[] = [],
  depth = 0,
) {
  const explicitPath = Array.isArray(item.path) ? item.path : []
  if (explicitPath.length) return explicitPath
  const dimension = item.dimension || CATEGORY_MATRIX_DIMENSIONS[depth]
  if (!dimension) return parentPath
  return [...parentPath, { dimension, value: item.name }]
}

function getCategoryPathKey(path: FreebillCategoryPathItem[]) {
  return path.map((item) => `${item.dimension}:${encodeURIComponent(item.value)}`).join('/')
}

function isCategoryExpanded(path: FreebillCategoryPathItem[]) {
  return expandedCategoryKeys.value.has(getCategoryPathKey(path))
}

function toggleCategoryExpanded(path: FreebillCategoryPathItem[]) {
  const key = getCategoryPathKey(path)
  const next = new Set(expandedCategoryKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedCategoryKeys.value = next
}

function createCategoryBranchRef(
  path: FreebillCategoryPathItem[],
  item?: FreebillCategoryStat,
): CategoryBranchRef {
  const normalizedPath = path.map((segment) => ({ ...segment }))
  const label = normalizedPath.map((segment) => segment.value).join(' / ')
  return {
    path: normalizedPath,
    label,
    count: Number(item?.count || 0),
    value: Number(item?.value || 0),
  }
}

function findCategoryBranchInCurrentTree(path: FreebillCategoryPathItem[]) {
  const targetKey = getCategoryPathKey(path)
  const visit = (
    items: FreebillCategoryStat[],
    parentPath: FreebillCategoryPathItem[] = [],
    depth = 0,
  ): { item: FreebillCategoryStat; path: FreebillCategoryPathItem[] } | null => {
    for (const item of items) {
      const itemPath = resolveCategoryItemPath(item, parentPath, depth)
      if (getCategoryPathKey(itemPath) === targetKey) {
        return { item, path: itemPath }
      }
      const childMatch = visit(getCategoryChildren(item), itemPath, depth + 1)
      if (childMatch) return childMatch
    }
    return null
  }
  return visit(categoryTreeItems.value)
}

function syncSelectedCategoryBranchWithDashboard() {
  const branch = selectedCategoryBranch.value
  if (!branch) return
  const currentBranch = findCategoryBranchInCurrentTree(branch.path)
  selectedCategoryBranch.value = currentBranch
    ? createCategoryBranchRef(currentBranch.path, currentBranch.item)
    : null
}

function isSelectedCategoryBranch(path: FreebillCategoryPathItem[]) {
  const selected = selectedCategoryBranch.value
  return Boolean(selected) && getCategoryPathKey(selected?.path ?? []) === getCategoryPathKey(path)
}

async function selectCategoryBranch(path: FreebillCategoryPathItem[], item?: FreebillCategoryStat) {
  selectedCategoryBranch.value = createCategoryBranchRef(path, item)
  await loadCategoryBranchRecords(path, 1)
}

async function reloadSelectedCategoryBranchDetail() {
  const branch = selectedCategoryBranch.value
  if (!branch) return
  const state = getCategoryDetailState(branch.path)
  await loadCategoryBranchRecords(branch.path, state.page, true)
}

function getCategoryDetailKey(path: FreebillCategoryPathItem[]) {
  return `detail/${getCategoryPathKey(path)}`
}

function createCategoryDetailState(): CategoryBranchDetailState {
  return {
    loading: false,
    loaded: false,
    page: 1,
    pageSize: CATEGORY_DETAIL_PAGE_SIZE,
    sortBy: CATEGORY_DETAIL_DEFAULT_SORT.field,
    sortOrder: CATEGORY_DETAIL_DEFAULT_SORT.order,
    total: 0,
    items: [],
    error: '',
  }
}

function getCategoryDetailState(path: FreebillCategoryPathItem[]) {
  const key = getCategoryDetailKey(path)
  if (!categoryBranchDetailCache[key]) {
    categoryBranchDetailCache[key] = createCategoryDetailState()
  }
  return categoryBranchDetailCache[key]
}

function clearCategoryDetailCache() {
  Object.keys(categoryBranchDetailCache).forEach((key) => {
    delete categoryBranchDetailCache[key]
  })
}

async function loadCategoryBranchRecords(
  path: FreebillCategoryPathItem[],
  page = 1,
  force = false,
) {
  const state = getCategoryDetailState(path)
  const nextPage = Math.max(1, Math.floor(page))
  const sort = categoryDetailSort.value
  if (state.loading) return
  if (!force && state.loaded && state.page === nextPage && state.sortBy === sort.field && state.sortOrder === sort.order) return
  state.loading = true
  state.error = ''
  try {
    const offset = (nextPage - 1) * CATEGORY_DETAIL_PAGE_SIZE
    const result = await fetchFreebillCategoryBranchRecords({
      program: backendProgram.value,
      programs: [backendProgram.value, frontendProgram.value],
      path,
      limit: CATEGORY_DETAIL_PAGE_SIZE,
      offset,
      sort_by: sort.field,
      sort_order: sort.order,
    })
    state.items = result.items
    state.total = result.total
    state.page = nextPage
    state.pageSize = CATEGORY_DETAIL_PAGE_SIZE
    state.sortBy = sort.field
    state.sortOrder = sort.order
    state.loaded = true
  } catch (error) {
    state.error = getErrorMessage(error)
  } finally {
    state.loading = false
  }
}

async function changeCategoryDetailPage(page: number) {
  const branch = selectedCategoryBranch.value
  if (!branch) return
  await loadCategoryBranchRecords(branch.path, page)
}

async function setCategoryDetailSort(field: FreebillCategoryBranchSortBy) {
  const current = categoryDetailSort.value
  const order = current.field === field
    ? (current.order === 'asc' ? 'desc' : 'asc')
    : CATEGORY_DETAIL_SORT_DEFAULT_ORDERS[field]
  categoryDetailSort.value = { field, order }
  const branch = selectedCategoryBranch.value
  if (!branch) return
  await loadCategoryBranchRecords(branch.path, 1, true)
}

function getCategoryDetailSortMark(field: FreebillCategoryBranchSortBy) {
  if (categoryDetailSort.value.field !== field) return ''
  return categoryDetailSort.value.order === 'asc' ? '↑' : '↓'
}

function formatCategoryDetailTime(value: string | null | undefined) {
  const text = (value || '').trim()
  if (!text) return '-'
  return text.slice(0, 16).replaceAll('-', '/')
}

function formatCategoryDetailText(value: string | number | null | undefined) {
  const text = String(value ?? '').trim()
  return text || '-'
}

function getRecordFieldValue(record: FreebillRecord | null, field: string) {
  if (!record) return ''
  return (record as unknown as Record<string, unknown>)[field]
}

function getRecordRawFieldValue(record: FreebillRecord | null, field: string) {
  if (!record) return ''
  if (record.raw_values && Object.prototype.hasOwnProperty.call(record.raw_values, field)) {
    return record.raw_values[field]
  }
  return getRecordFieldValue(record, field)
}

function normalizeRecordEditValue(value: unknown, mode: RecordManualOverrideField['mode']) {
  if (mode === 'number') {
    if (value === null || value === undefined || String(value).trim() === '') return null
    const number = Number(value)
    return Number.isFinite(number) ? number : null
  }
  return String(value ?? '').trim()
}

function isSameRecordEditValue(
  left: unknown,
  right: unknown,
  mode: RecordManualOverrideField['mode'],
) {
  if (mode === 'number') {
    const leftNumber = normalizeRecordEditValue(left, mode)
    const rightNumber = normalizeRecordEditValue(right, mode)
    return leftNumber === rightNumber
  }
  return String(left ?? '').trim() === String(right ?? '').trim()
}

function isPinnedManualOverrideField(field: string) {
  return field === 'standard_direction' || field === 'standard_nature'
}

function markRecordEditFieldTouched(field: string) {
  recordEditTouchedFields[field] = true
}

function openRecordEditForRecord(record: FreebillRecord) {
  recordEditTarget.value = record
  RECORD_MANUAL_OVERRIDE_FIELDS.forEach((field) => {
    recordEditTouchedFields[field.key] = false
    const value = getRecordFieldValue(record, field.key)
    recordEditForm[field.key] = field.mode === 'number'
      ? normalizeRecordEditValue(value, field.mode) as number | null
      : String(value ?? '').trim()
  })
  recordEditDialogVisible.value = true
}

function openRecordEditDialog(event: MouseEvent, record: FreebillRecord) {
  event.preventDefault()
  openRecordEditForRecord(record)
}

function buildRecordManualOverrides() {
  const record = recordEditTarget.value
  const overrides: Record<string, unknown> = {}
  if (!record) return overrides
  RECORD_MANUAL_OVERRIDE_FIELDS.forEach((field) => {
    const currentValue = normalizeRecordEditValue(recordEditForm[field.key], field.mode)
    const rawValue = getRecordRawFieldValue(record, field.key)
    const hasExistingManualOverride = Boolean(
      record.manual_overrides
        && Object.prototype.hasOwnProperty.call(record.manual_overrides, field.key),
    )
    if (isPinnedManualOverrideField(field.key)) {
      if (
        recordEditTouchedFields[field.key]
        || hasExistingManualOverride
        || !isSameRecordEditValue(currentValue, rawValue, field.mode)
      ) {
        overrides[field.key] = currentValue
      }
      return
    }
    if (!isSameRecordEditValue(currentValue, rawValue, field.mode)) {
      overrides[field.key] = currentValue
    }
  })
  return overrides
}

function getCategoryPathFieldDefaults(path: FreebillCategoryPathItem[]) {
  const defaults: Record<string, string> = {}
  path.forEach((item) => {
    if (item.dimension === 'standard_direction') defaults.standard_direction = item.value
    if (item.dimension === 'standard_nature') defaults.standard_nature = item.value
    if (item.dimension === 'type') defaults.type = item.value
    if (item.dimension === 'counterparty') defaults.counterparty = item.value
  })
  return defaults
}

function openCategoryBranchBatchEditDialog(
  path: FreebillCategoryPathItem[],
  item?: FreebillCategoryStat,
) {
  const target = createCategoryBranchRef(path, item)
  const defaults = getCategoryPathFieldDefaults(path)
  branchBatchEditTarget.value = target
  RECORD_MANUAL_OVERRIDE_FIELDS.forEach((field) => {
    branchBatchEditEnabled[field.key] = false
    branchBatchEditForm[field.key] = field.mode === 'number'
      ? null
      : defaults[field.key] ?? ''
  })
  branchBatchEditDialogVisible.value = true
}

async function openCategoryBranchBatchEdit(
  event: MouseEvent,
  path: FreebillCategoryPathItem[],
  item?: FreebillCategoryStat,
) {
  event.preventDefault()
  event.stopPropagation()
  try {
    const result = await fetchFreebillCategoryBranchRecords({
      program: backendProgram.value,
      programs: [backendProgram.value, frontendProgram.value],
      path,
      limit: 2,
      offset: 0,
      sort_by: 'amount',
      sort_order: 'desc',
    })
    if (result.total === 0) {
      ElMessage.warning('该分支没有可修改记录')
      return
    }
    if (result.total === 1 && result.items[0]) {
      openRecordEditForRecord(result.items[0])
      return
    }
    openCategoryBranchBatchEditDialog(path, item ? { ...item, count: result.total } : item)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

function buildBranchBatchManualOverrides() {
  const overrides: Record<string, unknown> = {}
  RECORD_MANUAL_OVERRIDE_FIELDS.forEach((field) => {
    if (!branchBatchEditEnabled[field.key]) return
    const value = normalizeRecordEditValue(branchBatchEditForm[field.key], field.mode)
    if (field.mode === 'number' && value === null) return
    overrides[field.key] = value
  })
  return overrides
}

async function saveCategoryBranchBatchEdit() {
  const target = branchBatchEditTarget.value
  if (!target) return
  const overrides = buildBranchBatchManualOverrides()
  if (Object.keys(overrides).length === 0) {
    ElMessage.error('请选择至少一个要批量修改的字段')
    return
  }
  branchBatchEditSaving.value = true
  try {
    const result = await saveFreebillCategoryBranchManualOverrides({
      program: backendProgram.value,
      programs: [backendProgram.value, frontendProgram.value],
      path: target.path,
      overrides,
    })
    branchBatchEditDialogVisible.value = false
    await loadDashboard()
    await loadFilterOptions()
    ElMessage.success(`已批量修改 ${formatNumber(result.updated)} 条`)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    branchBatchEditSaving.value = false
  }
}

async function saveRecordManualEdit() {
  const record = recordEditTarget.value
  const tradeNo = String(record?.trade_no || '').trim()
  if (!tradeNo || tradeNo === '/') {
    ElMessage.error('这条记录没有稳定交易号，不能保存人工修改')
    return
  }
  recordEditSaving.value = true
  try {
    const overrides = buildRecordManualOverrides()
    if (Object.keys(overrides).length === 0) {
      await clearFreebillRecordOverrides([tradeNo])
      recordEditDialogVisible.value = false
      await loadDashboard()
      await loadFilterOptions()
      ElMessage.success('人工修改已清除')
      return
    }
    await saveFreebillRecordManualOverrides(tradeNo, overrides)
    recordEditDialogVisible.value = false
    await loadDashboard()
    await loadFilterOptions()
    ElMessage.success('人工修改已保存')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    recordEditSaving.value = false
  }
}

async function clearRecordManualEdit() {
  const record = recordEditTarget.value
  const tradeNo = String(record?.trade_no || '').trim()
  if (!tradeNo || tradeNo === '/') return
  recordEditSaving.value = true
  try {
    await clearFreebillRecordOverrides([tradeNo])
    recordEditDialogVisible.value = false
    await loadDashboard()
    await loadFilterOptions()
    ElMessage.success('人工修改已清除')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    recordEditSaving.value = false
  }
}

function getRecordStandardDirectionLabel(record: FreebillRecord) {
  const direction = String(record.standard_direction || '').trim()
  return direction
}

function moveArrayItem<T>(items: T[], oldIndex: number, newIndex: number) {
  if (oldIndex === newIndex) return [...items]
  const next = [...items]
  const [moved] = next.splice(oldIndex, 1)
  next.splice(newIndex, 0, moved)
  return next
}

async function reorderCategoryDimensions(oldIndex: number, newIndex: number) {
  categoryDimensions.value = moveArrayItem(categoryDimensions.value, oldIndex, newIndex)
  expandedCategoryKeys.value = new Set()
  autoExpandedCategoryKeys.clear()
  selectedCategoryBranch.value = null
  clearCategoryDetailCache()
  try {
    await loadDashboard()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

function normalizeCategoryNature(value: string | null | undefined): FreebillStandardNature {
  const normalized = String(value || '').trim() as FreebillStandardNature
  return TREND_STANDARD_NATURE_OPTIONS.includes(normalized) ? normalized : DEFAULT_TREND_STANDARD_NATURE
}

function normalizeCategoryDirection(value: string | null | undefined): FreebillStandardDirection | null {
  const normalized = String(value || '').trim()
  if (normalized === '支出' || normalized === '收支' || normalized === '收入') return normalized
  return null
}

function getNatureDirectionColors(nature: string | null | undefined) {
  return CATEGORY_NATURE_DIRECTION_COLORS[normalizeCategoryNature(nature)]
}

function getNatureDirectionColor(nature: string | null | undefined, direction: string | null | undefined) {
  const normalizedDirection = normalizeCategoryDirection(direction)
  if (!normalizedDirection) return '#94a3b8'
  return getNatureDirectionColors(nature)[normalizedDirection] ?? CATEGORY_FALLBACK_DIRECTION_COLORS[normalizedDirection]
}

function isFlowNature(nature: string | null | undefined) {
  return String(nature || '').trim() === '流水'
}

function getNatureNetColor(nature: string | null | undefined, value: number | null | undefined) {
  return getNatureDirectionColor(nature, getNatureNetDirection(nature, value))
}

function getNatureNetDirection(nature: string | null | undefined, value: number | null | undefined): FreebillStandardDirection {
  if (isFlowNature(nature)) return '收支'
  return Number(value || 0) < 0 ? '支出' : '收入'
}

function getCategoryTrackStyle(color: string, value: number | null | undefined, maxValue: number) {
  return {
    '--category-bar-width': barWidth(value, maxValue),
    '--category-bar-color': color,
  }
}

function formatTrendPeriod(value: string | null | undefined) {
  const text = (value || '').trim()
  if (!text) return '-'
  if (trendGranularity.value === 'year') return text.slice(0, 4)
  if (trendGranularity.value === 'month') return text.slice(0, 7)
  return text.slice(0, 10).replaceAll('-', '/')
}

function buildTrendAxisLabel(current: string, previous: string, index: number) {
  if (current === '-') return current
  if (trendGranularity.value === 'year') return current
  if (isTrendAxisAnchor(current, previous, index)) return current
  if (trendGranularity.value === 'month') {
    return current.slice(5, 7)
  }
  if (trendGranularity.value === 'day' && current.slice(0, 7) === previous.slice(0, 7)) {
    return current.slice(8, 10)
  }
  return current.slice(5, 10)
}

function isTrendAxisAnchor(current: string, previous: string, index: number) {
  if (current === '-') return false
  if (index === 0 || !previous || previous === '-') return true
  if (trendGranularity.value === 'month') {
    return current.slice(0, 4) !== previous.slice(0, 4)
  }
  if (trendGranularity.value === 'day' || trendGranularity.value === 'week') {
    return current.slice(0, 7) !== previous.slice(0, 7)
  }
  return false
}

function shouldShowTrendAxisLabel(
  index: number,
  items: Array<{ axisAnchor: boolean }>,
  labelStep: number,
) {
  if (items.length <= 18) return true
  if (items[index]?.axisAnchor) return true
  if (items[index - 1]?.axisAnchor || items[index + 1]?.axisAnchor) return false
  return index % labelStep === 0
}

async function updateTrendChart() {
  await nextTick()
  const el = trendChartRef.value
  if (!el || !trendItems.value.length) {
    disposeTrendChart()
    return
  }
  if (!trendChart) {
    trendChart = echarts.init(el)
    trendResizeObserver = new ResizeObserver(() => {
      trendChart?.resize()
    })
    trendResizeObserver.observe(el)
  }
  trendChart.setOption(buildTrendChartOption(), true)
  trendChart.resize()
}

function disposeTrendChart() {
  trendResizeObserver?.disconnect()
  trendResizeObserver = undefined
  trendChart?.dispose()
  trendChart = null
}

function buildTrendChartOption(): TrendChartOption {
  const items = trendItems.value
  const labelStep = Math.max(1, Math.ceil(items.length / 18))
  const trendColors = getNatureDirectionColors(trendStandardNature.value)
  const series: BarSeriesOption[] = isFlowNature(trendStandardNature.value)
    ? [
        {
          name: '收支',
          type: 'bar',
          barWidth: 18,
          data: items.map((item) => Number(item.other || 0)),
          itemStyle: {
            borderRadius: [3, 3, 0, 0],
            color: trendColors.收支,
          },
          emphasis: {
            focus: 'series',
          },
        },
      ]
    : [
        {
          name: '支出',
          type: 'bar',
          barWidth: 18,
          barGap: '-100%',
          data: items.map((item) => Number(item.expense || 0)),
          itemStyle: {
            borderRadius: [3, 3, 0, 0],
            color: trendColors.支出,
          },
          emphasis: {
            focus: 'series',
          },
        },
        {
          name: '收入',
          type: 'bar',
          barWidth: 18,
          data: items.map((item) => -Number(item.income || 0)),
          itemStyle: {
            borderRadius: [0, 0, 3, 3],
            color: trendColors.收入,
          },
          emphasis: {
            focus: 'series',
          },
        },
      ]
  return {
    animationDuration: 180,
    color: [trendColors.支出, trendColors.收支, trendColors.收入],
    grid: {
      top: 12,
      right: 12,
      bottom: 26,
      left: 54,
      containLabel: false,
    },
    tooltip: {
      trigger: 'item',
      borderColor: '#dfe5ee',
      confine: true,
      formatter: buildTrendTooltip,
      padding: [7, 9],
      textStyle: {
        color: '#1f2937',
        fontSize: 12,
      },
    },
    xAxis: {
      type: 'category',
      data: items.map((item) => item.axisLabel),
      axisLine: {
        onZero: true,
        lineStyle: {
          color: '#cbd5e1',
        },
      },
      axisTick: {
        show: false,
      },
      axisLabel: {
        color: '#334155',
        fontSize: 11,
        interval: (index: number) => shouldShowTrendAxisLabel(index, items, labelStep),
      },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#64748b',
        fontSize: 11,
        formatter: (value: number) => (value === 0 ? '0' : formatCompactMoney(Math.abs(value))),
      },
      axisLine: {
        show: false,
      },
      axisTick: {
        show: false,
      },
      splitLine: {
        lineStyle: {
          color: '#edf1f6',
        },
      },
    },
    series,
  }
}

function buildTrendTooltip(params: unknown) {
  const points = Array.isArray(params) ? params : [params]
  const firstPoint = points[0] as { dataIndex?: number } | undefined
  const item = trendItems.value[Number(firstPoint?.dataIndex ?? 0)]
  if (!item) return ''
  const trendColors = getNatureDirectionColors(trendStandardNature.value)
  const lines = isFlowNature(trendStandardNature.value)
    ? [buildTrendTooltipLine('收支', trendColors.收支, item.other, item.other_count)]
    : [
        buildTrendTooltipLine('支出', trendColors.支出, item.expense, item.expense_count),
        buildTrendTooltipLine('收入', trendColors.收入, item.income, item.income_count),
      ]
  return [
    `<div style="font-weight:650;margin-bottom:5px;">${escapeHtml(item.fullPeriod)}</div>`,
    ...lines,
  ].join('')
}

function buildTrendTooltipLine(label: string, color: string, value: number | null | undefined, count: number | null | undefined) {
  return `<div><span style="display:inline-block;width:7px;height:7px;background:${color};margin-right:6px;"></span>${label} ${formatMoney(value)} · ${formatNumber(Number(count || 0))} 条</div>`
}

function escapeHtml(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function syncTrendGranularityToProgramRange() {
  const range = getProgramDateRange(frontendProgram.value, 'create_time')
    ?? getProgramDateRange(backendProgram.value, 'create_time')
  if (!range) return
  trendGranularity.value = pickTrendGranularity(range)
}

function pickTrendGranularity(range: DateRange): FreebillTrendGranularity {
  const days = getDateSpanDays(range)
  if (days <= 62) return 'day'
  if (days <= 183) return 'week'
  if (days <= 6 * 366) return 'month'
  return 'year'
}

function getActiveTrendDateRange(): DateRange | null {
  const bounds = getDataDateBounds()
  const chartRange = getTrendDateRange()
  const frontendRange = getProgramDateRange(frontendProgram.value, 'create_time')
  const backendRange = getProgramDateRange(backendProgram.value, 'create_time')
  const programRange = frontendRange ?? backendRange
  const start = programRange?.start ?? chartRange?.start ?? bounds?.start
  const end = programRange?.end ?? chartRange?.end ?? bounds?.end
  if (!start || !end) return null
  const range = normalizeDateRange({ start, end })
  return bounds ? clampDateRange(range, bounds) : range
}

function getProgramDateRange(program: FreebillProgramChannel, field: string): DateRange | null {
  for (let index = program.rules.length - 1; index >= 0; index -= 1) {
    const rule = program.rules[index]
    if (rule?.matcher.kind !== 'field' || rule.matcher.field !== field) continue
    if (rule.matcher.op === 'year') {
      const year = Number(rule.matcher.value)
      if (Number.isInteger(year) && year >= 1 && year <= 9999) {
        return normalizeDateRange({
          start: new Date(year, 0, 1),
          end: new Date(year, 11, 31),
        })
      }
    }
    if (rule.matcher.op === 'between') {
      const values = Array.isArray(rule.matcher.values) ? rule.matcher.values : []
      const start = parseLocalDate(String(values[0] || ''))
      const end = parseLocalDate(String(values[1] || ''))
      if (start && end) return normalizeDateRange({ start, end })
    }
  }
  return null
}

function getTrendDateRange(): DateRange | null {
  const rows = dashboard.value?.monthly_trend ?? []
  if (!rows.length) return null
  const start = parseTrendPeriodStart(rows[0]?.month)
  const end = parseTrendPeriodEnd(rows[rows.length - 1]?.month)
  if (!start || !end) return null
  return normalizeDateRange({ start, end })
}

function getDataDateBounds(): DateRange | null {
  const start = parseLocalDate(status.value?.min_date)
  const end = parseLocalDate(status.value?.max_date)
  if (!start || !end) return null
  return normalizeDateRange({ start, end })
}

function getLatestDataYearRange(): DateRange | null {
  const bounds = getDataDateBounds()
  if (!bounds) return null
  const year = bounds.end.getFullYear()
  return clampDateRange({
    start: new Date(year, 0, 1),
    end: new Date(year, 11, 31),
  }, bounds)
}

function parseTrendPeriodStart(value: string | null | undefined) {
  const text = (value || '').trim()
  if (!text) return null
  if (trendGranularity.value === 'year') {
    const year = Number(text.slice(0, 4))
    return Number.isFinite(year) ? new Date(year, 0, 1) : null
  }
  if (trendGranularity.value === 'month') {
    const match = text.match(/^(\d{4})-(\d{2})/)
    if (!match) return null
    return new Date(Number(match[1]), Number(match[2]) - 1, 1)
  }
  return parseLocalDate(text.slice(0, 10))
}

function parseTrendPeriodEnd(value: string | null | undefined) {
  const start = parseTrendPeriodStart(value)
  if (!start) return null
  if (trendGranularity.value === 'year') return new Date(start.getFullYear(), 11, 31)
  if (trendGranularity.value === 'month') return new Date(start.getFullYear(), start.getMonth() + 1, 0)
  if (trendGranularity.value === 'week') return addDays(start, 6)
  return start
}

function zoomDateRange(range: DateRange, anchorRatio: number, factor: number, bounds: DateRange): DateRange {
  const boundsStart = dateToDayIndex(bounds.start)
  const boundsEnd = dateToDayIndex(bounds.end)
  const boundsSpan = Math.max(MIN_TREND_ZOOM_DAYS, boundsEnd - boundsStart + 1)
  const startIndex = dateToDayIndex(range.start)
  const endIndex = dateToDayIndex(range.end)
  const span = Math.max(MIN_TREND_ZOOM_DAYS, endIndex - startIndex + 1)
  const nextSpan = clampNumber(Math.round(span * factor), MIN_TREND_ZOOM_DAYS, boundsSpan)
  const anchorIndex = startIndex + (span - 1) * anchorRatio
  let nextStart = Math.round(anchorIndex - (nextSpan - 1) * anchorRatio)
  let nextEnd = nextStart + nextSpan - 1

  if (nextSpan >= boundsSpan) {
    nextStart = boundsStart
    nextEnd = boundsEnd
  } else if (nextStart < boundsStart) {
    nextStart = boundsStart
    nextEnd = nextStart + nextSpan - 1
  } else if (nextEnd > boundsEnd) {
    nextEnd = boundsEnd
    nextStart = nextEnd - nextSpan + 1
  }

  return {
    start: dayIndexToDate(nextStart),
    end: dayIndexToDate(nextEnd),
  }
}

function clampDateRange(range: DateRange, bounds: DateRange): DateRange {
  const boundsStart = dateToDayIndex(bounds.start)
  const boundsEnd = dateToDayIndex(bounds.end)
  const startIndex = clampNumber(dateToDayIndex(range.start), boundsStart, boundsEnd)
  const endIndex = clampNumber(dateToDayIndex(range.end), boundsStart, boundsEnd)
  return normalizeDateRange({
    start: dayIndexToDate(startIndex),
    end: dayIndexToDate(endIndex),
  })
}

function normalizeDateRange(range: DateRange): DateRange {
  const start = startOfLocalDay(range.start)
  const end = startOfLocalDay(range.end)
  return dateToDayIndex(start) <= dateToDayIndex(end)
    ? { start, end }
    : { start: end, end: start }
}

function getDateSpanDays(range: DateRange) {
  const normalized = normalizeDateRange(range)
  return dateToDayIndex(normalized.end) - dateToDayIndex(normalized.start) + 1
}

function parseLocalDate(value: string | null | undefined) {
  const match = (value || '').trim().match(/^(\d{4})[-/](\d{2})[-/](\d{2})/)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const date = new Date(year, month - 1, day)
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null
  return date
}

function formatLocalDate(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function addDays(date: Date, days: number) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days)
}

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function dateToDayIndex(date: Date) {
  return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY_MS)
}

function dayIndexToDate(dayIndex: number) {
  const date = new Date(dayIndex * DAY_MS)
  return new Date(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())
}

function clampNumber(value: number, min: number, max: number) {
  if (max < min) return min
  return Math.min(max, Math.max(min, value))
}

function barWidth(value: number | null | undefined, maxValue: number) {
  const numberValue = Math.abs(Number(value || 0))
  if (!maxValue || numberValue <= 0) return '0%'
  return `${Math.max(4, Math.min(100, Math.round((numberValue / maxValue) * 100)))}%`
}

watch(trendItems, () => {
  void updateTrendChart()
})

watch(categoryTreeItems, (items) => {
  const next = new Set(expandedCategoryKeys.value)
  let changed = false
  items.forEach((item) => {
    const key = getCategoryPathKey(resolveCategoryItemPath(item, [], 0))
    if (autoExpandedCategoryKeys.has(key)) return
    autoExpandedCategoryKeys.add(key)
    next.add(key)
    changed = true
  })
  if (changed) {
    expandedCategoryKeys.value = next
  }
}, { immediate: true })

watch(frontendProgram, () => {
  if (suppressNextFrontendProgramQuery) {
    suppressNextFrontendProgramQuery = false
    return
  }
  syncTrendGranularityToProgramRange()
  scheduleTrendZoomQuery()
}, { deep: true })

watch([
  frontendProgram,
  sheetViewProgram,
  trendGranularity,
  trendStandardNature,
  categoryDimensions,
], () => {
  persistFreebillFilterState()
}, { deep: true })

onMounted(() => {
  document.addEventListener('mousedown', handleGlobalMouseDown)
  document.addEventListener('keydown', handleGlobalKeydown)
  void refreshAll()
})

onUnmounted(() => {
  document.removeEventListener('mousedown', handleGlobalMouseDown)
  document.removeEventListener('keydown', handleGlobalKeydown)
  if (trendZoomTimer !== undefined) {
    window.clearTimeout(trendZoomTimer)
  }
  disposeTrendChart()
})
</script>

<template>
  <div class="freebill-page" v-loading="loading">
    <header class="page-toolbar">
      <div class="title-line">
        <h1>Freebill</h1>
        <el-tooltip placement="bottom-start">
          <template #content>
            <div class="tooltip-content">
              导入支付宝 CSV、微信支付 Excel 和建行 Excel 后，本页把账单标准化写入本地 SQLite，只在本机做汇总和明细查询。
            </div>
          </template>
          <el-icon class="help-icon"><QuestionFilled /></el-icon>
        </el-tooltip>
        <el-button
          class="data-layer-help-button"
          :icon="QuestionFilled"
          size="small"
          text
          @click="conceptDocDialogVisible = true"
        >
          核心概念
        </el-button>
        <el-button
          class="data-layer-help-button"
          size="small"
          text
          @click="dataLayerDialogVisible = true"
        >
          数据层级
        </el-button>
        <el-button
          class="data-layer-help-button"
          size="small"
          text
          @click="openInterpretRulesDialog"
        >
          解释规则
        </el-button>
      </div>
      <div class="toolbar-actions">
        <input
          ref="alipayFileInput"
          class="hidden-file-input"
          type="file"
          accept=".csv,text/csv"
          multiple
          @change="handleFileInput('alipay', $event)"
        >
        <input
          ref="wechatFileInput"
          class="hidden-file-input"
          type="file"
          accept=".xlsx,.xlsm,.xls,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          multiple
          @change="handleFileInput('wechat', $event)"
        >
        <input
          ref="ccbFileInput"
          class="hidden-file-input"
          type="file"
          accept=".xlsx,.xlsm,.xls,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          multiple
          @change="handleFileInput('ccb', $event)"
        >
        <el-button
          :icon="Upload"
          :loading="importingSource === 'alipay'"
          size="small"
          plain
          @click="openFilePicker('alipay')"
        >
          支付宝 CSV
        </el-button>
        <el-button
          :icon="Upload"
          :loading="importingSource === 'wechat'"
          size="small"
          plain
          @click="openFilePicker('wechat')"
        >
          微信 Excel
        </el-button>
        <el-button
          :icon="Upload"
          :loading="importingSource === 'ccb'"
          size="small"
          plain
          @click="openFilePicker('ccb')"
        >
          建行 Excel
        </el-button>
        <el-button
          :icon="FolderOpened"
          :disabled="!workbookId"
          size="small"
          @click="openWorkbookFile"
        >
          打开星云表格
        </el-button>
        <el-button
          :icon="Refresh"
          :loading="sheetWorkbookLoading"
          size="small"
          plain
          @click="refreshSheetWorkbook"
        >
          刷新表格文件
        </el-button>
        <el-button :icon="Refresh" :loading="loading" size="small" text @click="refreshAll">
          刷新
        </el-button>
      </div>
    </header>

    <el-dialog
      v-model="conceptDocDialogVisible"
      title="Freebill 核心概念"
      width="860px"
      class="freebill-concept-dialog"
    >
      <div class="concept-doc">
        <section
          v-for="section in FREEBILL_CONCEPT_DOC"
          :key="section.title"
          class="concept-doc-section"
        >
          <h3>{{ section.title }}</h3>
          <p
            v-for="paragraph in section.paragraphs ?? []"
            :key="paragraph"
          >
            {{ paragraph }}
          </p>
          <ul v-if="section.items?.length">
            <li
              v-for="item in section.items"
              :key="item"
            >
              {{ item }}
            </li>
          </ul>
        </section>
      </div>
    </el-dialog>

    <el-dialog
      v-model="dataLayerDialogVisible"
      title="Freebill 数据层级"
      width="720px"
      class="freebill-data-layer-dialog"
    >
      <ol class="data-layer-list">
        <li
          v-for="(item, index) in FREEBILL_DATA_LAYER_DOC"
          :key="item.title"
          class="data-layer-item"
        >
          <span class="data-layer-index">{{ index + 1 }}</span>
          <div class="data-layer-copy">
            <strong>{{ item.title }}</strong>
            <p>{{ item.description }}</p>
            <p>{{ item.note }}</p>
          </div>
        </li>
      </ol>
      <p class="data-layer-principle">
        原则：导入事实层尽量不被解释规则污染；收支、类型这类解释结果可以物化缓存，但应能按规则版本重算。
      </p>
    </el-dialog>

    <el-dialog
      v-model="interpretRulesDialogVisible"
      title="Freebill 解释规则"
      width="960px"
      class="freebill-interpret-rule-dialog"
    >
      <div class="interpret-rule-dialog-body" v-loading="interpretRulesLoading">
        <section class="interpret-rule-section">
          <div class="interpret-section-title">
            <strong>统计口径</strong>
            <span>只影响分类树的汇总值，原始金额仍按导入事实保存。</span>
          </div>
          <el-checkbox v-model="interpretRuleSettings.signed_category_values">
            支出按负值参与分类树净值汇总
          </el-checkbox>
        </section>

        <section class="interpret-rule-section">
          <div class="interpret-section-title">
            <strong>内置规则</strong>
            <span>先把导入数据解释成当前收支和类型，自定义规则会在内置规则之后继续覆盖。</span>
          </div>
          <div class="built-in-rule-list">
            <div
              v-for="group in builtInInterpretRuleGroups"
              :key="group.nature"
              class="built-in-rule-group"
            >
              <div class="built-in-rule-group-title">{{ group.nature }}</div>
              <div
                v-for="rule in group.rules"
                :key="rule.key"
                class="built-in-rule-row"
              >
                <el-checkbox v-model="rule.enabled" />
                <strong>{{ rule.name }}</strong>
                <span class="built-in-rule-matcher">{{ rule.matcher_text }}</span>
                <span class="built-in-rule-result">{{ getBuiltInRuleResultText(rule) }}</span>
              </div>
            </div>
          </div>
        </section>

        <section class="interpret-rule-section">
          <div class="interpret-section-title">
            <strong>自定义规则链</strong>
            <el-button size="small" text @click="addInterpretRule">+ 添加</el-button>
          </div>
          <div v-if="!interpretRules.length" class="empty-inline">当前没有自定义规则。</div>
          <div v-else ref="interpretRuleListRef" class="interpret-rule-list">
            <div
              v-for="(rule, index) in interpretRules"
              :key="rule.id ?? `new-${index}`"
              class="interpret-rule-row"
            >
              <SortableOrderHandle :index="index" :total="interpretRules.length" size="sm" />
              <el-checkbox v-model="rule.enabled" />
              <el-input
                v-model="rule.name"
                class="interpret-rule-name"
                placeholder="规则名"
                size="small"
              />
              <el-select
                v-model="rule.matcher.kind"
                class="interpret-rule-kind"
                size="small"
                @change="updateInterpretRuleKind(rule)"
              >
                <el-option label="字段" value="field" />
                <el-option label="全文" value="full_text_contains" />
                <el-option label="全部" value="all" />
                <el-option label="无" value="none" />
              </el-select>
              <template v-if="rule.matcher.kind === 'field'">
                <el-select
                  v-model="rule.matcher.field"
                  class="interpret-rule-field"
                  size="small"
                  @change="updateInterpretRuleField(rule)"
                >
                  <el-option
                    v-for="field in interpretRuleFields"
                    :key="field.value"
                    :label="field.label"
                    :value="field.value"
                  />
                </el-select>
                <el-select
                  v-model="rule.matcher.op"
                  class="interpret-rule-op"
                  size="small"
                >
                  <el-option
                    v-for="operator in getInterpretRuleOperators(rule)"
                    :key="operator.value"
                    :label="operator.label"
                    :value="operator.value"
                  />
                </el-select>
                <el-input-number
                  v-if="getInterpretRuleFieldMode(rule) === 'number'"
                  v-model="rule.matcher.value"
                  class="interpret-rule-value"
                  size="small"
                  :controls="false"
                />
                <el-input
                  v-else
                  v-model="rule.matcher.value"
                  class="interpret-rule-value"
                  placeholder="匹配值"
                  size="small"
                />
              </template>
              <el-input
                v-else-if="rule.matcher.kind === 'full_text_contains'"
                v-model="rule.matcher.value"
                class="interpret-rule-value is-wide"
                placeholder="全文包含"
                size="small"
              />
              <span v-else class="interpret-rule-static">直接匹配</span>
              <el-select
                v-model="rule.set_direction"
                class="interpret-rule-result"
                clearable
                placeholder="收支"
                size="small"
              >
                <el-option
                  v-for="direction in INTERPRET_RULE_DIRECTIONS"
                  :key="direction"
                  :label="direction"
                  :value="direction"
                />
              </el-select>
              <el-select
                v-model="rule.set_nature"
                class="interpret-rule-result"
                clearable
                placeholder="类型"
                size="small"
              >
                <el-option
                  v-for="nature in INTERPRET_RULE_NATURES"
                  :key="nature"
                  :label="nature"
                  :value="nature"
                />
              </el-select>
              <span class="interpret-rule-count">{{ formatNumber(rule.match_count ?? 0) }} 条</span>
              <el-button size="small" text type="danger" @click="removeInterpretRule(index)">删</el-button>
            </div>
          </div>
        </section>
      </div>
      <template #footer>
        <div class="interpret-dialog-footer">
          <el-button size="small" @click="interpretRulesDialogVisible = false">关闭</el-button>
          <el-button
            size="small"
            :loading="interpretRulesSaving"
            @click="saveInterpretRulesOnly"
          >
            保存
          </el-button>
          <el-button
            type="primary"
            size="small"
            :loading="interpretRulesApplying"
            @click="applyInterpretRules"
          >
            应用并重算
          </el-button>
        </div>
      </template>
    </el-dialog>

    <FreebillProgramBar
      v-model="backendProgram"
      class="backend-program"
      title="后端筛选"
      help-text="第一层筛选，决定从后端账单库里取哪些候选账单；点击执行后刷新下方统计。"
      :filter-options="filterOptions"
      :show-reset="false"
      :loading="loading"
      @apply="applyBackendProgram"
    />

    <FreebillProgramBar
      v-model="frontendProgram"
      class="frontend-program"
      title="前端筛选"
      help-text="第二层筛选，基于后端筛选结果继续收窄；摘要、趋势、分类和账单明细都按它统计。图表 Ctrl+滚轮缩放会自动写入这里的交易时间范围。"
      :filter-options="filterOptions"
      :loading="loading"
      :show-apply="false"
      :show-reset="false"
    >
      <template #title-actions>
        <el-button
          class="program-title-action"
          size="small"
          text
          @click="resetFrontendProgram"
        >
          最近年份
        </el-button>
      </template>
    </FreebillProgramBar>

    <main class="analytics-layout">
      <section class="trend-panel">
        <div class="panel-title trend-title">
          <div class="panel-heading">
            <h2>收支趋势</h2>
            <span>{{ trendItems.length }} {{ trendUnitLabel }}</span>
          </div>
          <div class="trend-actions">
            <el-select
              v-model="trendStandardNature"
              class="trend-nature-select"
              size="small"
              @change="changeTrendStandardNature"
            >
              <el-option
                v-for="option in TREND_STANDARD_NATURE_OPTIONS"
                :key="option"
                :label="option"
                :value="option"
              />
            </el-select>
            <el-radio-group
              v-model="trendGranularity"
              class="trend-granularity"
              size="small"
              @change="changeTrendGranularity"
            >
              <el-radio-button
                v-for="option in trendGranularityOptions"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </el-radio-button>
            </el-radio-group>
          </div>
        </div>
        <div
          v-if="trendItems.length"
          class="trend-chart-scroll"
        >
          <div
            ref="trendChartRef"
            class="trend-chart"
            :style="trendChartStyle"
            title="Ctrl+滚轮缩放时间范围"
            @wheel="handleTrendWheel"
          ></div>
        </div>
        <el-empty v-else description="暂无趋势数据" />
      </section>

      <section class="category-panel">
        <div class="panel-title category-title">
          <div class="panel-heading">
            <h2>分类</h2>
          </div>
          <div class="category-title-actions">
            <span>{{ CATEGORY_MATRIX_SUMMARY_TEXT }}</span>
          </div>
        </div>
        <div v-if="categoryMatrixRows.length" class="category-matrix-scroll">
          <table class="category-matrix-table">
            <thead>
              <tr>
                <th class="category-matrix-type-header">类型</th>
                <th
                  v-for="direction in categoryMatrixDirections"
                  :key="direction"
                  class="category-matrix-direction-header"
                >
                  {{ direction }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="matrixRow in categoryMatrixRows" :key="matrixRow.key">
                <th class="category-matrix-type-cell">
                  <div
                    class="category-track category-matrix-type-track"
                    :class="{ 'is-selected': isSelectedCategoryBranch(matrixRow.path) }"
                    :style="getCategoryTrackStyle(getNatureNetColor(matrixRow.nature, matrixRow.value), matrixRow.value, maxCategoryMatrixReferenceValue)"
                    @click="selectCategoryBranch(matrixRow.path, matrixRow.item)"
                    @contextmenu.prevent.stop="openCategoryBranchBatchEdit($event, matrixRow.path, matrixRow.item)"
                  >
                    <i class="category-bar" />
                    <span class="category-name-label">
                      <span
                        class="category-direction-swatch"
                        :style="{ backgroundColor: getNatureNetColor(matrixRow.nature, matrixRow.value) }"
                      ></span>
                      <span class="category-name-text">{{ matrixRow.nature }}</span>
                      <span class="category-value-text">{{ formatCategoryBarLabel(matrixRow.value) }}</span>
                    </span>
                  </div>
                </th>
                <td
                  v-for="cell in matrixRow.cells"
                  :key="cell.key"
                  class="category-matrix-cell"
                >
                  <template v-if="cell.entries.length">
                    <div
                      v-for="entry in cell.entries"
                      :key="entry.key"
                      class="category-cell-entry"
                    >
                      <div
                        class="category-track category-matrix-cell-summary"
                        :class="{ 'is-selected': isSelectedCategoryBranch(entry.path) }"
                        :style="getCategoryTrackStyle(getNatureDirectionColor(matrixRow.nature, entry.direction), entry.value, maxCategoryMatrixReferenceValue)"
                        @click="selectCategoryBranch(entry.path, entry.item)"
                        @contextmenu.prevent.stop="openCategoryBranchBatchEdit($event, entry.path, entry.item)"
                      >
                        <i class="category-bar" />
                        <span class="category-name-label">
                          <span class="category-name-text">{{ entry.direction }}</span>
                          <span class="category-value-text">{{ formatCategoryBarLabel(entry.value) }}</span>
                        </span>
                      </div>
                      <div v-if="entry.rows.length" class="category-list category-cell-tree">
                        <div
                          v-for="row in entry.rows"
                          :key="row.key"
                          class="category-row category-tree-row"
                          :class="{ 'is-selected': isSelectedCategoryBranch(row.path) }"
                          :style="{ paddingLeft: `${row.depth * 18}px` }"
                        >
                          <button
                            v-if="hasCategoryChildren(row.item)"
                            type="button"
                            class="category-toggle"
                            :title="isCategoryExpanded(row.path) ? '收起' : '展开'"
                            @click="toggleCategoryExpanded(row.path)"
                          >
                            {{ isCategoryExpanded(row.path) ? '-' : '+' }}
                          </button>
                          <span v-else class="category-toggle-placeholder"></span>
                          <div
                            class="category-track"
                            :style="getCategoryTrackStyle(getNatureDirectionColor(matrixRow.nature, entry.direction), row.item.value, maxCategoryMatrixReferenceValue)"
                            @click="selectCategoryBranch(row.path, row.item)"
                            @contextmenu.prevent.stop="openCategoryBranchBatchEdit($event, row.path, row.item)"
                          >
                            <i class="category-bar" />
                            <span class="category-name-label">
                              <span class="category-name-text">{{ row.item.name }}</span>
                              <span class="category-value-text">{{ formatCategoryBarLabel(row.item.value) }}</span>
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </template>
                  <div v-else class="category-matrix-empty">无</div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-inline">暂无分类</div>
        <div v-if="selectedCategoryBranch" class="category-detail-panel">
          <div class="category-detail-panel-title">
            <div>
              <strong>{{ selectedCategoryBranch.label }}</strong>
              <span v-if="selectedCategoryDetailState">
                共 {{ formatNumber(selectedCategoryDetailState.total) }} 条，每页 {{ selectedCategoryDetailState.pageSize }} 条
              </span>
            </div>
            <button
              type="button"
              class="category-detail-close"
              title="关闭明细"
              @click="selectedCategoryBranch = null"
            >
              ×
            </button>
          </div>
          <div v-if="!selectedCategoryDetailState || selectedCategoryDetailState.loading" class="category-detail-status">
            加载中...
          </div>
          <div v-else-if="selectedCategoryDetailState.error" class="category-detail-status is-error">
            {{ selectedCategoryDetailState.error }}
          </div>
          <div v-else-if="!selectedCategoryDetailState.loaded" class="category-detail-status">
            加载中...
          </div>
          <div v-else-if="!selectedCategoryDetailState.items.length" class="category-detail-status">
            暂无明细
          </div>
          <table v-else class="category-detail-table category-detail-fixed-table">
            <thead>
              <tr>
                <th>
                  <button
                    type="button"
                    class="category-detail-sort-button"
                    :class="{ 'is-active': categoryDetailSort.field === 'create_time' }"
                    :disabled="selectedCategoryDetailState?.loading"
                    @click="setCategoryDetailSort('create_time')"
                  >
                    交易时间<span>{{ getCategoryDetailSortMark('create_time') }}</span>
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="category-detail-sort-button"
                    :class="{ 'is-active': categoryDetailSort.field === 'source' }"
                    :disabled="selectedCategoryDetailState?.loading"
                    @click="setCategoryDetailSort('source')"
                  >
                    来源<span>{{ getCategoryDetailSortMark('source') }}</span>
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="category-detail-sort-button"
                    :class="{ 'is-active': categoryDetailSort.field === 'amount' }"
                    :disabled="selectedCategoryDetailState?.loading"
                    @click="setCategoryDetailSort('amount')"
                  >
                    金额<span>{{ getCategoryDetailSortMark('amount') }}</span>
                  </button>
                </th>
                <th
                  v-for="column in categoryDetailContextColumns"
                  :key="column.key"
                >
                  {{ column.label }}
                </th>
                <th>
                  <button
                    type="button"
                    class="category-detail-sort-button"
                    :class="{ 'is-active': categoryDetailSort.field === 'product_name' }"
                    :disabled="selectedCategoryDetailState?.loading"
                    @click="setCategoryDetailSort('product_name')"
                  >
                    商品<span>{{ getCategoryDetailSortMark('product_name') }}</span>
                  </button>
                </th>
                <th>
                  <button
                    type="button"
                    class="category-detail-sort-button"
                    :class="{ 'is-active': categoryDetailSort.field === 'remark' }"
                    :disabled="selectedCategoryDetailState?.loading"
                    @click="setCategoryDetailSort('remark')"
                  >
                    备注<span>{{ getCategoryDetailSortMark('remark') }}</span>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="record in selectedCategoryDetailState.items"
                :key="record.id"
                class="category-detail-row"
                :class="{ 'has-manual-override': Boolean(record.has_record_override) }"
                title="右键修改"
                @contextmenu.prevent="openRecordEditDialog($event, record)"
              >
                <td>{{ formatCategoryDetailTime(record.create_time) }}</td>
                <td>{{ formatCategoryDetailText(record.source) }}</td>
                <td class="amount-cell">{{ formatMoney(record.amount) }}</td>
                <td
                  v-for="column in categoryDetailContextColumns"
                  :key="column.key"
                >
                  {{ formatCategoryDetailText(column.value(record)) }}
                </td>
                <td>{{ formatCategoryDetailText(record.product_name) }}</td>
                <td>{{ formatCategoryDetailText(record.remark) }}</td>
              </tr>
            </tbody>
          </table>
          <div
            v-if="selectedCategoryDetailState && selectedCategoryDetailState.total > selectedCategoryDetailState.pageSize"
            class="category-detail-pagination"
          >
            <StandardPagination
              :page="selectedCategoryDetailState.page"
              :page-size="selectedCategoryDetailState.pageSize"
              :total="selectedCategoryDetailState.total"
              :show-page-size="false"
              @page-change="changeCategoryDetailPage"
            />
          </div>
        </div>
      </section>
    </main>

    <section class="sheet-panel" v-loading="sheetWorkbookLoading">
      <div class="panel-title sheet-title">
        <div class="panel-heading">
          <h2>星云表格</h2>
        </div>
        <span>{{ status?.db_path }}</span>
      </div>
      <el-tabs v-model="activeSheetKey" class="freebill-sheet-tabs">
        <el-tab-pane
          v-for="tab in sheetTabs"
          :key="tab.key"
          :name="tab.key"
        >
          <template #label>
            <span
              class="freebill-sheet-tab-label"
              @contextmenu.capture="event => openSheetTabContextMenu(event, tab)"
            >
              {{ tab.label }}
            </span>
          </template>
          <FreebillProgramBar
            v-if="tab.key === 'records'"
            v-model="sheetViewProgram"
            class="sheet-view-program"
            title="表格筛选"
            help-text="账单明细表自用筛选。它基于上方前端筛选结果继续收窄，只影响表格查看，不影响上方统计。"
            apply-text="即时生效"
            reset-text="清空"
            :show-apply="false"
            :show-reset="sheetViewProgram.rules.length > 0"
            :filter-options="filterOptions"
            :field-options="RECORD_SHEET_FILTER_FIELDS"
            @reset="resetSheetViewProgram"
          />
          <NoteSheetWorkspace
            v-if="workbookId && tab.sheet"
            :ref="instance => setSheetWorkspaceRef(tab.key, instance)"
            class="freebill-sheet-workspace"
            :key="getSheetWorkspaceKey(tab.key)"
            :workbook-id="workbookId"
            :sheet-id="tab.sheet.sheet_id"
            :show-title-input="false"
            :empty-text="tab.emptyText"
            :base-row-filter-programs="tab.key === 'records' ? recordsBaseRowFilterPrograms : null"
            :row-filter-program="tab.key === 'records' ? sheetViewProgram : null"
            default-height-mode="content"
          />
          <el-empty
            v-else
            :description="sheetWorkbookLoading ? '正在刷新星云表格' : tab.emptyText"
          />
        </el-tab-pane>
      </el-tabs>
      <div
        v-if="sheetTabContextMenu.visible"
        class="freebill-sheet-tab-context-menu"
        :style="{ left: `${sheetTabContextMenu.left}px`, top: `${sheetTabContextMenu.top}px` }"
        @contextmenu.prevent.stop
        @mousedown.stop
      >
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          @click="configureSheetFromTabContextMenu"
        >
          设置表格
        </button>
        <button
          type="button"
          class="sheet-tab-context-menu-item"
          @click="openWorkbookFromTabContextMenu"
        >
          打开完整工作簿
        </button>
      </div>
    </section>

    <el-dialog
      v-model="recordEditDialogVisible"
      title="人工修改账单明细"
      width="820px"
      class="record-edit-dialog"
    >
      <div v-if="recordEditTarget" class="record-edit-meta">
        <span>{{ recordEditTarget.trade_no }}</span>
        <span>{{ formatCategoryDetailTime(recordEditTarget.create_time) }}</span>
      </div>
      <div class="record-edit-grid">
        <label
          v-for="field in RECORD_MANUAL_OVERRIDE_FIELDS"
          :key="field.key"
          class="record-edit-field"
        >
          <span>{{ field.label }}</span>
          <el-select
            v-if="field.mode === 'direction'"
            v-model="recordEditForm[field.key]"
            size="small"
            @change="markRecordEditFieldTouched(field.key)"
          >
            <el-option
              v-for="item in INTERPRET_RULE_DIRECTIONS"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
          <el-select
            v-else-if="field.mode === 'nature'"
            v-model="recordEditForm[field.key]"
            size="small"
            @change="markRecordEditFieldTouched(field.key)"
          >
            <el-option
              v-for="item in INTERPRET_RULE_NATURES"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
          <el-input-number
            v-else-if="field.mode === 'number'"
            v-model="recordEditForm[field.key]"
            size="small"
            controls-position="right"
            :precision="field.key === 'amount' || field.key === 'account_balance' ? 2 : undefined"
            @change="markRecordEditFieldTouched(field.key)"
          />
          <el-input
            v-else
            v-model="recordEditForm[field.key]"
            size="small"
            @change="markRecordEditFieldTouched(field.key)"
          />
        </label>
      </div>
      <template #footer>
        <div class="record-edit-footer">
          <el-button
            :disabled="recordEditSaving || !recordEditTarget?.has_record_override"
            @click="clearRecordManualEdit"
          >
            清除人工修改
          </el-button>
          <span />
          <el-button @click="recordEditDialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="recordEditSaving" @click="saveRecordManualEdit">保存</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="branchBatchEditDialogVisible"
      title="批量修改分支明细"
      width="820px"
      class="record-edit-dialog"
    >
      <div v-if="branchBatchEditTarget" class="record-edit-meta">
        <span>{{ branchBatchEditTarget.label }}</span>
        <span>约 {{ formatNumber(branchBatchEditTarget.count) }} 条</span>
      </div>
      <div class="record-edit-grid branch-batch-edit-grid">
        <label
          v-for="field in RECORD_MANUAL_OVERRIDE_FIELDS"
          :key="field.key"
          class="record-edit-field branch-batch-edit-field"
        >
          <el-checkbox v-model="branchBatchEditEnabled[field.key]">
            {{ field.label }}
          </el-checkbox>
          <el-select
            v-if="field.mode === 'direction'"
            v-model="branchBatchEditForm[field.key]"
            size="small"
            :disabled="!branchBatchEditEnabled[field.key]"
          >
            <el-option
              v-for="item in INTERPRET_RULE_DIRECTIONS"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
          <el-select
            v-else-if="field.mode === 'nature'"
            v-model="branchBatchEditForm[field.key]"
            size="small"
            :disabled="!branchBatchEditEnabled[field.key]"
          >
            <el-option
              v-for="item in INTERPRET_RULE_NATURES"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
          <el-input-number
            v-else-if="field.mode === 'number'"
            v-model="branchBatchEditForm[field.key]"
            size="small"
            controls-position="right"
            :disabled="!branchBatchEditEnabled[field.key]"
            :precision="field.key === 'amount' || field.key === 'account_balance' ? 2 : undefined"
          />
          <el-input
            v-else
            v-model="branchBatchEditForm[field.key]"
            size="small"
            :disabled="!branchBatchEditEnabled[field.key]"
          />
        </label>
      </div>
      <template #footer>
        <div class="record-edit-footer">
          <span></span>
          <span />
          <el-button @click="branchBatchEditDialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="branchBatchEditSaving" @click="saveCategoryBranchBatchEdit">批量保存</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.freebill-page {
  min-height: 100%;
  padding: 18px 20px 14px;
  background: #f6f8fb;
  color: #1f2937;
}

.page-toolbar,
.title-line,
.toolbar-actions,
.panel-title,
.panel-heading,
.category-row {
  display: flex;
  align-items: center;
}

.page-toolbar {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.title-line {
  gap: 8px;
  min-width: 0;
}

h1,
h2 {
  margin: 0;
  line-height: 1.2;
}

h1 {
  font-size: 22px;
  font-weight: 650;
  letter-spacing: 0;
}

h2 {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0;
}

.help-icon {
  color: #64748b;
  cursor: help;
}

.tooltip-content {
  max-width: 320px;
  line-height: 1.6;
}

.toolbar-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.data-layer-help-button {
  height: 24px;
  padding: 0 4px;
}

.concept-doc {
  display: grid;
  gap: 14px;
  max-height: 64vh;
  overflow: auto;
  padding-right: 4px;
}

.concept-doc-section {
  min-width: 0;
  padding-bottom: 12px;
  border-bottom: 1px solid #edf1f6;
}

.concept-doc-section:last-child {
  padding-bottom: 0;
  border-bottom: none;
}

.concept-doc-section h3 {
  margin: 0 0 6px;
  color: #0f172a;
  font-size: 14px;
  font-weight: 650;
  line-height: 1.35;
  letter-spacing: 0;
}

.concept-doc-section p,
.concept-doc-section li {
  color: #475569;
  font-size: 13px;
  line-height: 1.7;
}

.concept-doc-section p {
  margin: 5px 0 0;
}

.concept-doc-section ul {
  display: grid;
  gap: 4px;
  margin: 6px 0 0;
  padding-left: 18px;
}

.data-layer-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.data-layer-item {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.data-layer-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  background: #e8eef7;
  color: #334155;
  font-size: 12px;
  font-weight: 650;
  line-height: 1;
}

.data-layer-copy {
  min-width: 0;
}

.data-layer-copy strong {
  color: #0f172a;
  font-size: 14px;
}

.data-layer-copy p,
.data-layer-principle {
  margin: 4px 0 0;
  color: #475569;
  font-size: 13px;
  line-height: 1.65;
}

.data-layer-principle {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #edf1f6;
}

.interpret-rule-dialog-body {
  display: grid;
  gap: 14px;
}

.interpret-rule-section {
  min-width: 0;
}

.interpret-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.interpret-section-title strong {
  color: #0f172a;
  font-size: 14px;
}

.interpret-section-title span {
  flex: 1;
  min-width: 0;
  color: #64748b;
  font-size: 12px;
}

.built-in-rule-list,
.interpret-rule-list {
  display: grid;
  gap: 6px;
}

.built-in-rule-group {
  display: grid;
  gap: 2px;
}

.built-in-rule-group + .built-in-rule-group {
  margin-top: 4px;
}

.built-in-rule-group-title {
  color: #0f172a;
  font-size: 12px;
  font-weight: 600;
  line-height: 22px;
}

.built-in-rule-row,
.interpret-rule-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  border-bottom: 1px solid #edf1f6;
}

.built-in-rule-row {
  padding: 5px 0 5px 14px;
  color: #475569;
  font-size: 12px;
}

.built-in-rule-row strong {
  flex: 0 0 96px;
  color: #0f172a;
}

.built-in-rule-row :deep(.el-checkbox) {
  height: 20px;
  margin-right: 0;
}

.built-in-rule-row span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.built-in-rule-matcher {
  flex: 1 1 auto;
}

.built-in-rule-result {
  flex: 0 0 190px;
  color: #334155;
}

.interpret-rule-row {
  padding: 6px 0;
}

.interpret-rule-name {
  width: 120px;
}

.interpret-rule-kind {
  width: 76px;
}

.interpret-rule-field {
  width: 112px;
}

.interpret-rule-op {
  width: 92px;
}

.interpret-rule-value {
  width: 160px;
}

.interpret-rule-value.is-wide {
  width: 372px;
}

.interpret-rule-result {
  width: 92px;
}

.interpret-rule-static {
  width: 372px;
  color: #64748b;
  font-size: 12px;
}

.interpret-rule-count {
  margin-left: auto;
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.interpret-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.hidden-file-input {
  display: none;
}

.program-title-action {
  height: 24px;
  padding: 0 4px;
}

.backend-program,
.frontend-program {
  margin-bottom: 12px;
}

.panel-title span,
.category-row span,
.empty-inline {
  color: #64748b;
  font-size: 12px;
}

.analytics-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}

.trend-panel,
.category-panel,
.sheet-panel {
  min-width: 0;
  border: 1px solid #dfe5ee;
  background: #fff;
}

.panel-title {
  justify-content: space-between;
  gap: 12px;
  padding: 11px 12px;
  border-bottom: 1px solid #edf1f6;
}

.panel-heading {
  min-width: 0;
  gap: 8px;
}

.trend-title {
  flex-wrap: wrap;
}

.trend-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.trend-nature-select {
  width: 84px;
  flex-shrink: 0;
}

.trend-granularity {
  flex-shrink: 0;
}

.trend-granularity :deep(.el-radio-button__inner) {
  padding: 5px 10px;
}

.category-list {
  display: grid;
  gap: 2px;
  background: transparent;
}

.category-tree-list {
  padding: 8px 0;
}

.category-matrix-scroll {
  overflow-x: auto;
  padding: 8px 12px 10px;
}

.category-matrix-table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  table-layout: fixed;
}

.category-matrix-table th,
.category-matrix-table td {
  border: 1px solid #e6edf5;
  vertical-align: top;
}

.category-matrix-type-header,
.category-matrix-direction-header {
  padding: 6px 8px;
  background: #f7f9fc;
  color: #334155;
  font-size: 12px;
  font-weight: 650;
  text-align: left;
}

.category-matrix-type-header,
.category-matrix-type-cell {
  width: 180px;
}

.category-matrix-type-cell {
  padding: 6px;
  background: #fbfcfe;
}

.category-matrix-cell {
  min-width: 260px;
  padding: 5px 6px;
}

.category-matrix-type-track,
.category-matrix-cell-summary {
  margin-bottom: 4px;
}

.category-cell-entry + .category-cell-entry {
  margin-top: 4px;
}

.category-matrix-empty {
  padding: 3px 0;
  color: #94a3b8;
  font-size: 12px;
}

.category-cell-tree {
  gap: 2px;
  padding-top: 2px;
}

.category-cell-tree .category-row {
  padding: 1px 0;
}

.category-cell-tree .category-track {
  height: 18px;
}

.category-row {
  min-width: 0;
  padding: 1px 12px;
  background: transparent;
}

.category-title {
  align-items: center;
}

.category-title-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.category-title-actions > span {
  color: #64748b;
  font-size: 12px;
  white-space: nowrap;
}

.category-order-toggle {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 8px;
  border: 1px solid #d7dfeb;
  border-radius: 4px;
  background: #fff;
  color: #334155;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
}

.category-order-toggle:hover,
.category-order-toggle.is-active {
  border-color: #93c5fd;
  color: #1d4ed8;
}

.category-dimension-editor {
  padding: 0 12px 8px;
}

.category-dimension-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.category-dimension-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 3px 8px 3px 3px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #334155;
  font-size: 12px;
}

.category-tree-row {
  align-items: center;
  gap: 6px;
}

.category-toggle,
.category-toggle-placeholder {
  flex: 0 0 18px;
  width: 18px;
  height: 18px;
}

.category-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #cbd5e1;
  border-radius: 3px;
  background: #f8fafc;
  color: #334155;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
}

.category-toggle:hover {
  border-color: #93c5fd;
  color: #1d4ed8;
}

.trend-chart-scroll {
  overflow-x: auto;
  overscroll-behavior: contain;
  padding: 12px;
}

.trend-chart {
  height: 236px;
  min-width: 360px;
}

.category-track {
  position: relative;
  overflow: hidden;
  border-radius: 2px;
  --category-bar-color: #94a3b8;
  --category-bar-width: 0%;
  background: #edf1f6;
  cursor: pointer;
}

.category-row.is-selected .category-track {
  outline: 1px solid #3b82f6;
  outline-offset: 1px;
}

.category-track.is-selected {
  outline: 1px solid #3b82f6;
  outline-offset: 1px;
}

.category-bar {
  position: absolute;
  inset: 0 auto 0 0;
  display: block;
  z-index: 0;
  width: var(--category-bar-width);
  height: 100%;
  background: var(--category-bar-color);
}

.category-track {
  flex: 1;
  min-width: 0;
  height: 20px;
}

.category-track .category-name-label {
  position: absolute;
  inset: 3px 5px;
  display: flex;
  align-items: center;
  gap: 10px;
  z-index: 1;
  min-width: 0;
  pointer-events: none;
}

.category-direction-swatch {
  flex: 0 0 9px;
  width: 9px;
  height: 9px;
  border-radius: 2px;
}

.category-track .category-name-text {
  min-width: 0;
  overflow: hidden;
  padding: 0 2px;
  flex: 0 1 auto;
  color: #0f172a;
  font-size: 12px;
  font-weight: 500;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.category-track .category-value-text {
  flex: 0 0 auto;
  padding: 0 2px;
  color: #111827;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  line-height: 14px;
  white-space: nowrap;
}

.category-detail-panel {
  margin: 8px 12px 12px;
  padding-top: 8px;
  border-top: 1px solid #edf1f6;
}

.category-detail-panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.category-detail-panel-title > div {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.category-detail-panel-title strong {
  min-width: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.category-detail-panel-title span {
  color: #64748b;
  font-size: 12px;
}

.category-detail-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 22px;
  width: 22px;
  height: 22px;
  border: 1px solid transparent;
  border-radius: 3px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}

.category-detail-close:hover {
  border-color: #dbe3ee;
  color: #1f2937;
}

.category-detail-status {
  padding: 10px 0;
  color: #64748b;
  font-size: 12px;
}

.category-detail-status.is-error {
  color: #b91c1c;
}

.category-detail-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
  font-size: 12px;
}

.category-detail-fixed-table {
  width: auto;
  max-width: 100%;
}

.category-detail-table th,
.category-detail-table td {
  max-width: 320px;
  overflow: hidden;
  padding: 5px 8px;
  border-bottom: 1px solid #eef2f7;
  text-align: left;
  text-overflow: ellipsis;
  vertical-align: top;
  white-space: nowrap;
}

.category-detail-table th {
  background: #f8fafc;
  color: #475569;
  font-weight: 650;
}

.category-detail-sort-button {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border: 0;
  background: transparent;
  padding: 0;
  color: inherit;
  cursor: pointer;
  font: inherit;
  line-height: inherit;
}

.category-detail-sort-button:hover,
.category-detail-sort-button.is-active {
  color: #1d4ed8;
}

.category-detail-sort-button:disabled {
  cursor: default;
  opacity: 0.65;
}

.category-detail-sort-button span {
  display: inline-block;
  min-width: 8px;
  font-size: 11px;
  line-height: 1;
}

.category-detail-table .amount-cell {
  color: #991b1b;
  font-variant-numeric: tabular-nums;
  font-weight: 650;
}

.category-detail-row {
  cursor: context-menu;
}

.category-detail-row.has-manual-override td:first-child {
  box-shadow: inset 3px 0 0 #409eff;
}

.category-detail-table th:last-child,
.category-detail-table td:last-child {
  max-width: none;
}

.category-detail-pagination {
  display: flex;
  justify-content: flex-start;
  margin-top: 8px;
}

.record-edit-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: -4px 0 12px;
  color: #64748b;
  font-size: 12px;
}

.record-edit-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
}

.record-edit-field {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.record-edit-field > span {
  color: #475569;
  font-size: 12px;
  white-space: nowrap;
}

.record-edit-field :deep(.el-input-number) {
  width: 100%;
}

.branch-batch-edit-field {
  grid-template-columns: 118px minmax(0, 1fr);
}

.branch-batch-edit-field :deep(.el-checkbox) {
  height: 24px;
  margin-right: 0;
}

.record-edit-footer {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  gap: 8px;
  align-items: center;
}

.empty-inline {
  padding: 18px 12px;
}

.sheet-title > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.freebill-sheet-tabs {
  min-width: 0;
}

.freebill-sheet-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 12px;
}

.freebill-sheet-tabs :deep(.el-tabs__content) {
  min-height: 0;
  overflow: visible;
}

.freebill-sheet-tabs :deep(.el-tab-pane) {
  min-height: 0;
}

.freebill-sheet-tab-label {
  display: inline-flex;
  align-items: center;
  height: 100%;
  margin: 0 -20px;
  padding: 0 20px;
}

.freebill-sheet-tab-context-menu {
  position: fixed;
  z-index: 3000;
  box-sizing: border-box;
  min-width: 136px;
  padding: 4px 0;
  border: 1px solid #d8dce5;
  border-radius: 4px;
  background: #fff;
  box-shadow: 0 8px 20px rgb(15 23 42 / 16%);
}

.sheet-tab-context-menu-item {
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 7px 16px;
  color: #1f2937;
  font-size: 14px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
}

.sheet-tab-context-menu-item:hover {
  background: #f5f7fa;
}

.sheet-view-program {
  margin: 10px 12px;
}

@media (max-width: 980px) {
  .page-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar-actions {
    justify-content: flex-start;
  }

  .analytics-layout {
    grid-template-columns: 1fr;
  }

}
</style>

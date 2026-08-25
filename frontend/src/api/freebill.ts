import api from '@/api'

export type FreebillImportSource = 'alipay' | 'wechat' | 'ccb'
export type FreebillTrendGranularity = 'day' | 'week' | 'month' | 'year'
export type FreebillStandardNature = '常规' | '借贷' | '理财' | '转账' | '流水'
export type FreebillStandardDirection = '支出' | '收支' | '收入'
export type FreebillCategoryBranchSortBy = 'amount' | 'create_time' | 'source' | 'product_name' | 'remark'
export type FreebillSortOrder = 'asc' | 'desc'
export type FreebillCategoryDimension = 'standard_direction' | 'standard_nature' | 'type' | 'counterparty'
export type FreebillProgramMatcherKind = 'all' | 'none' | 'field' | 'full_text_contains'
export type FreebillInterpretRuleMatcherKind = 'all' | 'none' | 'field' | 'full_text_contains'
export type FreebillProgramRuleAction = 'include' | 'exclude' | 'filter'
export type FreebillProgramOperator =
  | 'eq'
  | 'neq'
  | 'in'
  | 'not_in'
  | 'contains'
  | 'not_contains'
  | 'gte'
  | 'lte'
  | 'between'
  | 'year'
export type FreebillInterpretRuleOperator =
  | 'eq'
  | 'neq'
  | 'in'
  | 'not_in'
  | 'contains'
  | 'not_contains'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'

export interface FreebillStatus {
  exists: boolean
  work_dir: string
  db_path: string
  total_records: number
  min_date: string | null
  max_date: string | null
  last_imported_at: number | null
  raw_file_count: number
}

export interface FreebillSummary {
  total_income: number
  total_expense: number
  total_ignore: number
  total_other: number
  total_count: number
  balance: number
}

export interface FreebillSourceBreakdown {
  source: string
  count: number
  income: number
  expense: number
}

export interface FreebillCategoryStat {
  name: string
  value: number
  count: number
  group_count?: number
  is_remainder?: boolean
  remainder_items?: FreebillCategoryStat[]
  dimension?: FreebillCategoryDimension | null
  path?: FreebillCategoryPathItem[]
  children?: FreebillCategoryStat[]
}

export interface FreebillCategoryPathItem {
  dimension: FreebillCategoryDimension
  value: string
}

export interface FreebillMonthlyTrendItem {
  month: string
  income: number
  expense: number
  other?: number
  income_count: number
  expense_count: number
  ignore_count?: number
  other_count?: number
  count: number
}

export interface FreebillDashboard {
  summary: FreebillSummary
  sources: FreebillSourceBreakdown[]
  expense_categories: FreebillCategoryStat[]
  income_categories: FreebillCategoryStat[]
  category_tree?: FreebillCategoryStat[]
  category_dimensions?: FreebillCategoryDimension[]
  monthly_trend: FreebillMonthlyTrendItem[]
  trend_granularity?: FreebillTrendGranularity
}

export interface FreebillRecord {
  id: number
  source: string | null
  trade_no: string | null
  merchant_order_no: string | null
  create_time: string | null
  pay_time: string | null
  modify_time: string | null
  location: string | null
  type: string | null
  counterparty: string | null
  product_name: string | null
  amount: number | null
  direction: string | null
  status: string | null
  service_fee: number | null
  refund_amount: number | null
  remark: string | null
  fund_status: string | null
  account_no: string | null
  currency: string | null
  cash_type: string | null
  account_balance: number | null
  raw_sequence: string | null
  standard_nature?: string | null
  standard_direction?: string | null
  raw_direction?: string | null
  raw_type?: string | null
  raw_values?: Record<string, unknown>
  manual_overrides?: Record<string, unknown>
  imported_at: number | null
  has_record_override?: boolean | number | null
}

export interface FreebillRecordPage {
  total: number
  items: FreebillRecord[]
}

export interface FreebillFilterOptions {
  sources: string[]
  directions: string[]
  types: string[]
  categories: string[]
}

export interface FreebillInterpretRuleField {
  value: string
  label: string
  mode: 'text' | 'number'
}

export interface FreebillInterpretRuleOperatorOption {
  value: FreebillInterpretRuleOperator
  label: string
}

export interface FreebillBuiltInInterpretRule {
  key: string
  name: string
  enabled: boolean
  target_nature?: FreebillStandardNature | null
  matcher_text: string
  result_text: string
  note?: string | null
}

export interface FreebillInterpretRuleMatcher {
  kind: FreebillInterpretRuleMatcherKind
  field?: string | null
  op?: FreebillInterpretRuleOperator | null
  value?: unknown
  values?: unknown[]
  ignore_case?: boolean
}

export interface FreebillInterpretRule {
  id?: number | null
  name: string
  enabled: boolean
  order_index: number
  matcher: FreebillInterpretRuleMatcher
  set_direction?: FreebillStandardDirection | null
  set_nature?: FreebillStandardNature | null
  note?: string | null
  match_count?: number
}

export interface FreebillInterpretRuleSettings {
  signed_category_values: boolean
  built_in_rules: Record<string, boolean>
}

export interface FreebillInterpretRulesPayload {
  settings: FreebillInterpretRuleSettings
  fields: FreebillInterpretRuleField[]
  operators: FreebillInterpretRuleOperatorOption[]
  directions: FreebillStandardDirection[]
  natures: FreebillStandardNature[]
  built_in_rules: FreebillBuiltInInterpretRule[]
  rules: FreebillInterpretRule[]
}

export interface FreebillInterpretRecomputeResult {
  total: number
  updated: number
  rules: FreebillInterpretRule[]
  recomputed_at: number
}

export interface FreebillQueryParams {
  start_date?: string
  end_date?: string
  source?: string
  direction?: string
  category?: string
  q?: string
  trend_granularity?: FreebillTrendGranularity
  limit?: number
  offset?: number
}

export interface FreebillProgramMatcher {
  kind: FreebillProgramMatcherKind
  field?: string | null
  op?: FreebillProgramOperator | null
  value?: unknown
  values?: unknown[]
  ignore_case?: boolean
}

export interface FreebillProgramRule {
  action: FreebillProgramRuleAction
  matcher: FreebillProgramMatcher
}

export interface FreebillProgramChannel {
  default: boolean
  rules: FreebillProgramRule[]
}

export interface FreebillDashboardProgramRequest {
  program: FreebillProgramChannel
  programs?: FreebillProgramChannel[]
  trend_granularity?: FreebillTrendGranularity
  trend_standard_nature?: FreebillStandardNature | null
  category_dimensions?: FreebillCategoryDimension[]
}

export interface FreebillCategoryBranchRecordsRequest {
  program: FreebillProgramChannel
  programs?: FreebillProgramChannel[]
  path?: FreebillCategoryPathItem[]
  direction?: string | null
  category?: string | null
  counterparty?: string | null
  limit?: number
  offset?: number
  sort_by?: FreebillCategoryBranchSortBy
  sort_order?: FreebillSortOrder
}

export interface FreebillImportResultItem {
  status: 'success' | 'error'
  filename: string
  processed: number
  inserted: number
  updated: number
  skipped: number
  error?: string
}

export interface FreebillImportResult {
  source: FreebillImportSource
  results: FreebillImportResultItem[]
  processed: number
  inserted: number
  updated: number
  skipped: number
  error_count: number
}

export interface FreebillWechatLocalSyncResult {
  filename: string
  format: string
  processed: number
  inserted: number
  updated: number
  skipped: number
  scanned: number
  parsed: number
  ignored: number
  source_modified_at: number
  source_sync_error?: string | null
  payment: {
    scanned: number
    parsed: number
    ignored: number
    reset_tables: number
  }
  chat_transfer: {
    scanned: number
    parsed: number
    ignored: number
    reset_tables: number
    advanced_tables: number
  }
}

export interface FreebillSheetWorkbookSheet {
  key: string
  title: string
  sheet_id: number
  row_count: number
  updated_at: number
}

export interface FreebillSheetWorkbook {
  workbook: {
    id: number
    title: string
    updated_at: number
  }
  sheets: FreebillSheetWorkbookSheet[]
  refreshed_at: number
}

export async function fetchFreebillStatus() {
  const response = await api.get<FreebillStatus>('/freebill/status')
  return response.data
}

export async function fetchFreebillDashboard(params: FreebillQueryParams = {}) {
  const response = await api.get<FreebillDashboard>('/freebill/dashboard', { params })
  return response.data
}

export async function fetchFreebillDashboardByProgram(payload: FreebillDashboardProgramRequest) {
  const response = await api.post<FreebillDashboard>('/freebill/dashboard-program', payload)
  return response.data
}

export async function fetchFreebillRecords(params: FreebillQueryParams = {}) {
  const response = await api.get<FreebillRecordPage>('/freebill/records', { params })
  return response.data
}

export async function fetchFreebillCategoryBranchRecords(payload: FreebillCategoryBranchRecordsRequest) {
  const response = await api.post<FreebillRecordPage>('/freebill/category-branch-records', payload)
  return response.data
}

export async function fetchFreebillFilterOptions() {
  const response = await api.get<FreebillFilterOptions>('/freebill/filter-options')
  return response.data
}

export async function fetchFreebillInterpretRules() {
  const response = await api.get<FreebillInterpretRulesPayload>('/freebill/interpret-rules')
  return response.data
}

export async function saveFreebillInterpretRules(rules: FreebillInterpretRule[], settings?: FreebillInterpretRuleSettings) {
  const response = await api.put<FreebillInterpretRulesPayload>('/freebill/interpret-rules', { rules, settings })
  return response.data
}

export async function recomputeFreebillInterpretRules() {
  const response = await api.post<FreebillInterpretRecomputeResult>('/freebill/interpret-rules/recompute', {}, {
    timeout: 120000,
  })
  return response.data
}

export async function saveFreebillRecordManualOverrides(
  tradeNo: string,
  overrides: Record<string, unknown>,
  note?: string,
) {
  const response = await api.put('/freebill/record-manual-overrides', {
    trade_no: tradeNo,
    overrides,
    note,
  })
  return response.data as { trade_no: string; updated: number; overrides: Record<string, unknown> }
}

export async function saveFreebillCategoryBranchManualOverrides(payload: {
  program: FreebillProgramChannel
  programs?: FreebillProgramChannel[]
  path: FreebillCategoryPathItem[]
  overrides: Record<string, unknown>
  note?: string
}) {
  const response = await api.put('/freebill/category-branch-manual-overrides', payload)
  return response.data as { matched: number; updated: number; overrides: Record<string, unknown> }
}

export async function clearFreebillRecordOverrides(tradeNos: string[]) {
  const response = await api.post('/freebill/record-overrides/clear', { trade_nos: tradeNos })
  return response.data as { requested: number; cleared: number; missing_trade_nos: string[] }
}

export async function importFreebillFiles(source: FreebillImportSource, files: File[]) {
  const formData = new FormData()
  files.forEach((file) => formData.append('files', file))
  const response = await api.post<FreebillImportResult>(`/freebill/import/${source}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
  return response.data
}

export async function syncFreebillFromLocalWechat() {
  const response = await api.post<FreebillWechatLocalSyncResult>('/freebill/sources/wechat-local/sync', {}, {
    timeout: 120000,
  })
  return response.data
}

export async function fetchFreebillSheetWorkbook() {
  const response = await api.get<FreebillSheetWorkbook | null>('/freebill/sheet-workbook')
  return response.data
}

export async function refreshFreebillSheetWorkbook() {
  const response = await api.post<FreebillSheetWorkbook>('/freebill/sheet-workbook/refresh', {}, {
    timeout: 120000,
  })
  return response.data
}

export function createFreebillProgramMatcher(kind: FreebillProgramMatcherKind = 'all'): FreebillProgramMatcher {
  return {
    kind,
    field: kind === 'field' ? 'create_time' : null,
    op: kind === 'field' ? 'between' : null,
    value: kind === 'full_text_contains' ? '' : undefined,
    values: [],
    ignore_case: true,
  }
}

export function createFreebillProgramRule(
  action: FreebillProgramRuleAction = 'include',
  kind: FreebillProgramMatcherKind = 'all',
): FreebillProgramRule {
  return {
    action,
    matcher: createFreebillProgramMatcher(kind),
  }
}

export function createFreebillIncludeAllProgram(): FreebillProgramChannel {
  return {
    default: false,
    rules: [
      createFreebillProgramRule('include', 'all'),
    ],
  }
}

export function normalizeFreebillProgramMatcher(value?: Partial<FreebillProgramMatcher> | null): FreebillProgramMatcher {
  const kind = value?.kind === 'none' || value?.kind === 'field' || value?.kind === 'full_text_contains'
    ? value.kind
    : 'all'
  const base = createFreebillProgramMatcher(kind)
  return {
    ...base,
    ...value,
    kind,
    field: typeof value?.field === 'string' ? value.field : base.field,
    op: value?.op ?? base.op,
    values: Array.isArray(value?.values) ? [...value.values] : [],
    ignore_case: typeof value?.ignore_case === 'boolean' ? value.ignore_case : true,
  }
}

export function normalizeFreebillProgramRule(value?: Partial<FreebillProgramRule> | null): FreebillProgramRule {
  return {
    action: value?.action === 'exclude' || value?.action === 'filter' ? value.action : 'include',
    matcher: normalizeFreebillProgramMatcher(value?.matcher),
  }
}

export function normalizeFreebillProgramChannel(value?: Partial<FreebillProgramChannel> | null): FreebillProgramChannel {
  return {
    default: typeof value?.default === 'boolean' ? value.default : false,
    rules: Array.isArray(value?.rules) ? value.rules.map((rule) => normalizeFreebillProgramRule(rule)) : [],
  }
}

export function cloneFreebillProgramChannel(value?: Partial<FreebillProgramChannel> | null): FreebillProgramChannel {
  return JSON.parse(JSON.stringify(normalizeFreebillProgramChannel(value))) as FreebillProgramChannel
}

export function createFreebillDateRangeRule(field: string, startDate: string, endDate: string): FreebillProgramRule {
  return {
    action: 'filter',
    matcher: {
      kind: 'field',
      field,
      op: 'between',
      values: [startDate, endDate],
      ignore_case: true,
    },
  }
}

export function upsertFreebillDateRangeRule(
  program: Partial<FreebillProgramChannel> | null | undefined,
  field: string,
  startDate: string,
  endDate: string,
): FreebillProgramChannel {
  const draft = cloneFreebillProgramChannel(program)
  const index = draft.rules.findIndex((rule) => (
    rule.matcher.kind === 'field'
    && rule.matcher.field === field
  ))
  const nextRule = createFreebillDateRangeRule(field, startDate, endDate)
  let keepIndex = index
  if (index >= 0) {
    draft.rules[index] = nextRule
  } else {
    draft.rules.push(nextRule)
    keepIndex = draft.rules.length - 1
  }
  draft.rules = draft.rules.filter((rule, ruleIndex) => (
    ruleIndex === keepIndex
    || rule.matcher.kind !== 'field'
    || rule.matcher.field !== field
  ))
  return normalizeFreebillProgramChannel(draft)
}

export function removeFreebillDateRangeRules(
  program: Partial<FreebillProgramChannel> | null | undefined,
  field: string,
): FreebillProgramChannel {
  const draft = cloneFreebillProgramChannel(program)
  draft.rules = draft.rules.filter((rule) => !(rule.matcher.kind === 'field' && rule.matcher.field === field))
  if (!draft.rules.length) {
    return createFreebillIncludeAllProgram()
  }
  return normalizeFreebillProgramChannel(draft)
}

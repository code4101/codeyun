import api from '@/api'

export interface EastmoneyTable {
  title: string
  columns: string[]
  rows: Record<string, string>[]
}

export interface EastmoneyTradeSnapshot {
  captured_at: number
  start_date: string
  end_date: string
  account_label: string
  login_required: boolean
  page_title: string
  page_url: string
  summary: Record<string, string>
  positions: EastmoneyTable
  hk_positions: EastmoneyTable
  sgt_positions: EastmoneyTable
  history_deals: EastmoneyTable
  hk_history_deals: EastmoneyTable
}

export interface FetchTradeSnapshotParams {
  start_date?: string
  end_date?: string
}

export interface EastmoneySyncRun {
  id: string
  account_label: string
  start_date: string
  end_date: string
  status: string
  captured_at: number | null
  inserted_count: number
  updated_count: number
  trade_record_count: number
  position_count: number
  asset_summary_json: Record<string, string>
  error_message: string | null
  started_at: number
  finished_at: number | null
  sheet_workbook?: EastmoneySheetWorkbook
}

export interface EastmoneyTradeRecord {
  id: string
  sync_run_id: string
  account_label: string
  source: string
  source_key: string
  market: string
  trade_date: string
  trade_time: string
  security_code: string
  security_name: string
  direction: string
  quantity: string
  price: string
  occurrence_date: string
  occurrence_time: string
  occurrence_amount: string
  amount: string
  fee: string
  commission: string
  stamp_tax: string
  transfer_fee: string
  other_fee: string
  currency: string
  deal_id: string
  shareholder_account: string
  share_balance: string
  fund_balance: string
  extended_name: string
  raw_json: Record<string, string>
  quantity_value: number | null
  price_value: number | null
  occurrence_amount_value: number | null
  amount_value: number | null
  fee_value: number | null
  commission_value: number | null
  stamp_tax_value: number | null
  transfer_fee_value: number | null
  other_fee_value: number | null
  share_balance_value: number | null
  fund_balance_value: number | null
  first_seen_at: number
  last_seen_at: number
  created_at: number
  updated_at: number
}

export interface EastmoneyTradeRecordPage {
  total: number
  items: EastmoneyTradeRecord[]
}

export interface EastmoneyFundFlowRecord {
  id: string
  statement_import_id: string
  sync_run_id: string
  account_label: string
  source: string
  source_key: string
  flow_date: string
  flow_category: string
  market: string
  security_code: string
  security_name: string
  quantity: string
  price: string
  occurrence_amount: string
  fee: string
  stamp_tax: string
  transfer_fee: string
  fund_balance: string
  currency: string
  raw_json: Record<string, string>
  quantity_value: number | null
  price_value: number | null
  occurrence_amount_value: number | null
  fee_value: number | null
  stamp_tax_value: number | null
  transfer_fee_value: number | null
  fund_balance_value: number | null
  first_seen_at: number
  last_seen_at: number
  created_at: number
  updated_at: number
}

export interface EastmoneyFundFlowRecordPage {
  total: number
  items: EastmoneyFundFlowRecord[]
}

export interface EastmoneyPositionRecord {
  id: string
  sync_run_id: string
  account_label: string
  source: string
  market: string
  captured_at: number
  security_code: string
  security_name: string
  quantity: string
  available_quantity: string
  cost_price: string
  current_price: string
  market_value: string
  pnl: string
  pnl_ratio: string
  currency: string
  raw_json: Record<string, string>
  created_at: number
}

export interface EastmoneyPositionRecordPage {
  total: number
  items: EastmoneyPositionRecord[]
}

export interface EastmoneyMarketQuote {
  provider: string
  market: string
  symbol: string
  provider_code: string
  name: string
  price: number | null
  open_price: number | null
  high_price: number | null
  low_price: number | null
  prev_close_price: number | null
  volume: number | null
  turnover: number | null
  update_time: string
  fetched_at: number
  error: string
}

export interface EastmoneyMarketQuotePage {
  items: EastmoneyMarketQuote[]
}

export interface EastmoneyMarketQuoteRefreshResult {
  provider: string
  database_path: string
  target_count: number
  refreshed_count: number
  error_count: number
  error: string
  items: EastmoneyMarketQuote[]
}

export interface EastmoneyAkshareHistoryItem {
  date: string
  symbol: string
  open: number | null
  close: number | null
  high: number | null
  low: number | null
  volume: number | null
  amount: number | null
  amplitude: number | null
  change_percent: number | null
  change_amount: number | null
  turnover_rate: number | null
}

export interface EastmoneyAkshareHistoryPage {
  provider: string
  market: string
  symbol: string
  name: string
  period: string
  adjust: string
  start_date: string
  end_date: string
  error?: string
  items: EastmoneyAkshareHistoryItem[]
}

export interface EastmoneyAkshareIntradayItem {
  time: string
  symbol: string
  open: number | null
  close: number | null
  high: number | null
  low: number | null
  volume: number | null
  amount: number | null
  average_price: number | null
}

export interface EastmoneyAkshareIntradayPage {
  provider: string
  market: string
  symbol: string
  name: string
  period: string
  trade_date: string
  target_trade_date?: string
  display_trade_date?: string
  error?: string
  items: EastmoneyAkshareIntradayItem[]
}

export interface EastmoneyQlibExportItem {
  market: string
  symbol: string
  name: string
  qlib_symbol: string
  csv_path: string
  row_count: number
  source: string
  error: string
}

export interface EastmoneyQlibExportResult {
  qlib_repo_path: string
  source_dir: string
  qlib_dir: string
  dump_command: string
  exported_count: number
  items: EastmoneyQlibExportItem[]
}

export interface EastmoneyQlibAnalysis {
  market: string
  symbol: string
  name: string
  qlib_symbol: string
  row_count: number
  source: string
  start_date: string
  end_date: string
  latest_close: number | null
  latest_change_percent: number | null
  return_5: number | null
  return_20: number | null
  return_60: number | null
  ma_5: number | null
  ma_20: number | null
  ma_60: number | null
  ma_20_distance: number | null
  volatility_20: number | null
  max_drawdown: number | null
  volume_ratio_5_20: number | null
  score: number | null
  signal: string
  model_status: string
  scoring_rules?: string[]
  error: string
}

export type EastmoneyTradeAdviceAction = 'hold' | 'sell_plan' | 'buy_watch' | 'buy' | 'risk_reduce' | 'no_data'

export interface EastmoneyTradeAdviceStep {
  label: string
  value: string
  note: string
}

export interface EastmoneyTradeAdvicePosition {
  quantity: number
  cost_price: number | null
  current_price: number | null
  market_value: number | null
  quantity_text: string
  cost_price_text: string
  current_price_text: string
  market_value_text: string
}

export interface EastmoneyTradeAdviceAccount {
  total_asset: number | null
  cash_available: number | null
  position_weight_percent: number | null
  max_single_position_percent: number | null
  first_lot_budget: number | null
  summary: string
}

export interface EastmoneyTradeAdviceBacktest {
  strategy_id: string
  strategy_name: string
  start_date: string
  end_date: string
  total_return_percent: number | null
  benchmark_name: string
  benchmark_return_percent: number | null
  excess_return_percent: number | null
  trade_count: number
  summary: string
  error: string
}

export interface EastmoneyTradeEventEvidence {
  title: string
  event_date: string
  source: string
  url: string
  impact: 'support' | 'risk' | 'neutral' | string
  summary: string
}

export interface EastmoneyTradeOperation {
  intent: string
  side: string
  order_type: string
  price: number | null
  price_text: string
  trigger_price: number | null
  trigger_price_text: string
  quantity: number
  quantity_text: string
  amount: number | null
  amount_text: string
  cash_budget: number | null
  cash_budget_text: string
  stop_price: number | null
  stop_price_text: string
  recovery_price: number | null
  recovery_price_text: string
  lot_size: number
  summary: string
  guardrail_text: string
}

export interface EastmoneyTradeAdvice {
  market: string
  symbol: string
  name: string
  action: EastmoneyTradeAdviceAction
  action_text: string
  headline: string
  primary_order: string
  next_trigger: string
  risk_line: string
  recovery_line: string
  operation: EastmoneyTradeOperation
  evidence: string[]
  steps: EastmoneyTradeAdviceStep[]
  event_evidence: EastmoneyTradeEventEvidence[]
  strategy_status: string
  strategy_score: number | null
  strategy_rules: string[]
  backtests: EastmoneyTradeAdviceBacktest[]
  position: EastmoneyTradeAdvicePosition
  account: EastmoneyTradeAdviceAccount
  source: string
}

export interface EastmoneyTradeCandidateAdvice {
  market: string
  symbol: string
  name: string
  action: EastmoneyTradeAdviceAction
  action_text: string
  headline: string
  primary_order: string
  next_trigger: string
  risk_line: string
  operation: EastmoneyTradeOperation
  evidence: string[]
  steps: EastmoneyTradeAdviceStep[]
  event_evidence: EastmoneyTradeEventEvidence[]
  strategy_score: number | null
  backtests: EastmoneyTradeAdviceBacktest[]
  current_price: number | null
  rank_score: number
  account: EastmoneyTradeAdviceAccount
  source: string
}

export interface EastmoneyTradeCandidateResult {
  pool: string
  source: string
  total: number
  items: EastmoneyTradeCandidateAdvice[]
}

export interface EastmoneyTradeWorkbenchSummary {
  action_counts: Record<string, number>
  active_action_count: number
  headline: string
}

export interface EastmoneyTradeWorkbenchPolicy {
  key: string
  name: string
  max_single_position_percent: number
  first_lot_cash_percent: number
  first_lot_asset_percent: number
  max_first_lot_budget: number
  score_threshold: number
  score_profile: string
  take_profit_percent: number
  stop_loss_percent: number
  cost_rate_percent: number
  rules: string[]
}

export interface EastmoneyTradeCandidatePoolDefinition {
  key: string
  name: string
  source: string
  description: string
  start_date: string
  targets: Array<{
    market: string
    symbol: string
    name: string
    start_date: string
  }>
}

export interface EastmoneyTradeWorkbench {
  source: string
  candidate_pool: string
  policy: EastmoneyTradeWorkbenchPolicy
  candidate_pool_definition: EastmoneyTradeCandidatePoolDefinition
  account: {
    total_asset: number | null
    cash_available: number | null
    max_single_position_percent: number
    first_lot_cash_percent: number
    first_lot_asset_percent: number
    max_first_lot_budget: number
    captured_at: number | null
    account_label: string
  }
  holding_count: number
  candidate_count: number
  summary: EastmoneyTradeWorkbenchSummary
  holdings: EastmoneyTradeAdvice[]
  candidates: EastmoneyTradeCandidateAdvice[]
}

export interface EastmoneyTradeReport {
  markdown: string
  updated_at: number | null
}

export interface EastmoneyFundFlowFilterOptions {
  categories: string[]
  security_codes: string[]
  security_names: string[]
}

export interface EastmoneyTradeDetailOcrImportResponse {
  created: boolean
  record: EastmoneyTradeRecord
  run: EastmoneySyncRun
  row: Record<string, string>
  lines: string[]
  sheet_workbook?: EastmoneySheetWorkbook
}

export interface EastmoneyAssetSnapshot {
  id: string
  sync_run_id: string
  account_label: string
  captured_at: number
  total_asset: string
  market_value: string
  cash_available: string
  cash_balance: string
  withdrawable: string
  frozen: string
  pnl: string
  raw_json: Record<string, string>
  created_at: number
}

export interface EastmoneySheetWorkbookSheet {
  key: string
  title: string
  sheet_id: number
  row_count: number
  updated_at: number
}

export interface EastmoneySheetWorkbook {
  workbook: {
    id: number
    title: string
    updated_at: number
  }
  sheets: EastmoneySheetWorkbookSheet[]
  refreshed_at: number
}

export interface EastmoneyTradeAccountPageState {
  title: string
  url: string
  account_label: string
  login_required: boolean
  login_duration_preset: boolean
  captcha_ocr_text: string
  captcha_ocr_filled: boolean
  captcha_ocr_error: string
}

export async function fetchEastmoneyTradeSnapshot(params: FetchTradeSnapshotParams = {}) {
  const response = await api.get<EastmoneyTradeSnapshot>('/eastmoney/trade-snapshot', {
    params,
    timeout: 60000,
  })
  return response.data
}

export async function openEastmoneyTradeAccountPage() {
  const response = await api.post<EastmoneyTradeAccountPageState>('/eastmoney/trade-account/open', {}, {
    timeout: 120000,
  })
  return response.data
}

export async function syncEastmoneyTradeData(params: FetchTradeSnapshotParams = {}) {
  const response = await api.post<EastmoneySyncRun>('/eastmoney/sync', params, {
    timeout: 90000,
  })
  return response.data
}

export async function refreshEastmoneySheetWorkbook() {
  const response = await api.post<EastmoneySheetWorkbook>('/eastmoney/sheet-workbook/refresh')
  return response.data
}

export async function fetchEastmoneyTradeRecords(
  params: FetchTradeSnapshotParams & {
    source?: string
    security_code?: string
    limit?: number
    offset?: number
  } = {},
) {
  const response = await api.get<EastmoneyTradeRecordPage>('/eastmoney/trade-records', {
    params,
  })
  return response.data
}

export async function fetchEastmoneyFundFlowRecords(
  params: FetchTradeSnapshotParams & {
    flow_category?: string
    security_code?: string
    security_name?: string
    limit?: number
    offset?: number
  } = {},
) {
  const response = await api.get<EastmoneyFundFlowRecordPage>('/eastmoney/fund-flows', {
    params,
  })
  return response.data
}

export async function fetchEastmoneyFundFlowCategories() {
  const response = await api.get<{ items: string[] }>('/eastmoney/fund-flow-categories')
  return response.data
}

export async function fetchEastmoneyFundFlowFilterOptions() {
  const response = await api.get<EastmoneyFundFlowFilterOptions>('/eastmoney/fund-flow-filter-options')
  return response.data
}

export async function importEastmoneyTradeDetailFromOcr(image: File) {
  const formData = new FormData()
  formData.append('image', image)
  const response = await api.post<EastmoneyTradeDetailOcrImportResponse>(
    '/eastmoney/trade-detail/import/ocr',
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    },
  )
  return response.data
}

export async function fetchEastmoneySyncRuns(params: { limit?: number } = {}) {
  const response = await api.get<{ items: EastmoneySyncRun[] }>('/eastmoney/sync-runs', {
    params,
  })
  return response.data
}

export async function fetchLatestEastmoneyAssetSnapshot() {
  const response = await api.get<{ item: EastmoneyAssetSnapshot | null }>(
    '/eastmoney/asset-snapshot/latest',
  )
  return response.data
}

export async function fetchLatestEastmoneyPositions() {
  const response = await api.get<EastmoneyPositionRecordPage>('/eastmoney/positions/latest')
  return response.data
}

export async function fetchLatestEastmoneyMarketQuotes() {
  const response = await api.get<EastmoneyMarketQuotePage>('/eastmoney/market-quotes/latest')
  return response.data
}

export async function refreshEastmoneyMarketQuotes() {
  const response = await api.post<EastmoneyMarketQuoteRefreshResult>(
    '/eastmoney/market-quotes/refresh',
    {},
    { timeout: 30000 },
  )
  return response.data
}

export async function fetchEastmoneyAkshareHistory(params: {
  market?: string
  symbol?: string
  name?: string
  period?: string
  start_date?: string
  end_date?: string
  adjust?: string
  refresh?: boolean
} = {}) {
  const response = await api.get<EastmoneyAkshareHistoryPage>(
    '/eastmoney/market-history/akshare',
    {
      params,
      timeout: 60000,
    },
  )
  return response.data
}

export async function fetchEastmoneyAkshareIntraday(params: {
  market?: string
  symbol?: string
  name?: string
  trade_date?: string
  period?: string
  day_count?: number
  refresh?: boolean
} = {}) {
  const response = await api.get<EastmoneyAkshareIntradayPage>(
    '/eastmoney/market-intraday/akshare',
    {
      params,
      timeout: 30000,
    },
  )
  return response.data
}

export async function exportEastmoneyQlibDataset(params: { refresh?: boolean } = {}) {
  const response = await api.post<EastmoneyQlibExportResult>(
    '/eastmoney/qlib/export',
    {},
    {
      params,
      timeout: 120000,
    },
  )
  return response.data
}

export async function fetchEastmoneyQlibAnalysis(params: {
  market?: string
  symbol?: string
  name?: string
  start_date?: string
  refresh?: boolean
} = {}) {
  const response = await api.get<EastmoneyQlibAnalysis>(
    '/eastmoney/qlib/analysis',
    {
      params,
      timeout: 120000,
    },
  )
  return response.data
}

export async function fetchEastmoneyTradeAdvice(params: {
  market?: string
  symbol?: string
  name?: string
  start_date?: string
  refresh?: boolean
  quantity?: number
  cost_price?: number
  current_price?: number
  total_asset?: number
  cash_available?: number
  _ts?: number
} = {}) {
  const response = await api.get<EastmoneyTradeAdvice>(
    '/eastmoney/trade-advice',
    {
      params,
      timeout: 120000,
    },
  )
  return response.data
}

export async function fetchEastmoneyTradeCandidates(params: {
  pool?: string
  limit?: number
  screen_limit?: number
  refresh?: boolean
  exclude_positions?: boolean
} = {}) {
  const response = await api.get<EastmoneyTradeCandidateResult>(
    '/eastmoney/trade-candidates',
    {
      params,
      timeout: 120000,
    },
  )
  return response.data
}

export async function fetchEastmoneyTradeWorkbench(params: {
  candidate_pool?: string
  holding_limit?: number
  candidate_limit?: number
  screen_limit?: number
  refresh?: boolean
  focus_market?: string
  focus_symbol?: string
  focus_name?: string
  focus_quantity?: number
  focus_cost_price?: number
  focus_current_price?: number
  total_asset?: number
  cash_available?: number
  _ts?: number
} = {}) {
  const response = await api.get<EastmoneyTradeWorkbench>(
    '/eastmoney/trade-workbench',
    {
      params,
      timeout: 120000,
    },
  )
  return response.data
}

export async function fetchEastmoneyTradeReport() {
  const response = await api.get<EastmoneyTradeReport>('/eastmoney/trade-report')
  return response.data
}

export async function saveEastmoneyTradeReport(markdown: string) {
  const response = await api.put<EastmoneyTradeReport>('/eastmoney/trade-report', { markdown })
  return response.data
}

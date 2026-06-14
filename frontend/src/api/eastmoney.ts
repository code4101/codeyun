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

export interface EastmoneyEtfRotationHolding {
  market: string
  symbol: string
  name: string
  weight: number
  fast_momentum: number
  forward_return: number | null
}

export interface EastmoneyEtfRotationPeriod {
  date: string
  year: string
  cash_fraction: number
  return: number
  holdings: EastmoneyEtfRotationHolding[]
}

export interface EastmoneyEtfRotationBacktestResult {
  strategy_id: string
  source: string
  parameters: Record<string, unknown>
  annual_returns: Record<string, number>
  total_return: number
  latest_signal: EastmoneyEtfRotationPeriod | null
  period_count: number
  periods: EastmoneyEtfRotationPeriod[]
}

export type EastmoneyStrategyJsonValue =
  | string
  | number
  | boolean
  | null
  | EastmoneyStrategyJsonValue[]
  | { [key: string]: EastmoneyStrategyJsonValue }

export interface EastmoneyStrategyResearchSourceGroup {
  id: string
  name?: string
  title?: string
  summary?: string
  notes?: string
  urls?: string[]
}

export interface EastmoneyStrategyResearchItem {
  id: string
  title: string
  family: string[]
  market_scope: string[]
  instrument_scope: string[]
  timeframe: string
  status: string
  priority: number
  hypothesis: string
  rules: Record<string, EastmoneyStrategyJsonValue>
  data_requirements: string[]
  existing_mapping?: Record<string, EastmoneyStrategyJsonValue>
  validation_plan: string[]
  sources: string[]
  notes?: string
}

export interface EastmoneyStrategyResearchCatalog {
  schema_version: number
  updated_at: string
  purpose: string
  source_groups: EastmoneyStrategyResearchSourceGroup[]
  count: number
  items: EastmoneyStrategyResearchItem[]
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

export interface EastmoneyQlibScreenItem {
  pool: string
  market: string
  symbol: string
  name: string
  qlib_symbol: string
  score: number | null
  signal: string
  row_count: number
  source: string
  start_date: string
  end_date: string
  latest_close: number | null
  latest_change_percent: number | null
  return_5: number | null
  return_20: number | null
  return_60: number | null
  ma_20_distance: number | null
  volatility_20: number | null
  max_drawdown: number | null
  volume_ratio_5_20: number | null
  error: string
}

export interface EastmoneyQlibScreenResult {
  pool: string
  source: string
  target_count: number
  analyzed_count: number
  failed_count: number
  error: string
  scoring_rules?: string[]
  items: EastmoneyQlibScreenItem[]
}

export interface EastmoneyQlibBacktestPoint {
  date: string
  close: number
  score: number | null
  cash: number
  position_value: number
  equity: number
  action: string
}

export interface EastmoneyQlibBacktestTrade {
  trigger_date: string
  trigger_score: number
  buy_date: string
  buy_price: number
  sell_date: string
  sell_price: number | null
  lot_size: number
  shares: number
  buy_cost: number
  sell_proceeds: number | null
  realized_profit: number | null
  realized_return_percent: number | null
  holding_days: number
  status: string
}

export interface EastmoneyQlibBacktestResult {
  market: string
  symbol: string
  name: string
  start_date: string
  end_date: string
  lot_size: number
  score_threshold: number
  score_profile: string
  take_profit_percent: number
  stop_loss_percent: number
  max_holding_days: number
  cost_rate: number
  capital_mode: string
  initial_capital: number
  total_invested: number
  total_fee: number
  max_capital_used: number
  final_equity: number
  total_profit: number
  total_return_percent: number
  trade_count: number
  closed_trade_count: number
  open_position_shares: number
  source: string
  force_liquidate_end: boolean
  rules: string[]
  error: string
  points: EastmoneyQlibBacktestPoint[]
  trades: EastmoneyQlibBacktestTrade[]
}

export interface EastmoneyQlibPoolBacktestItem {
  market: string
  symbol: string
  name: string
  lot_size: number | null
  total_profit: number
  total_return_percent: number
  total_invested: number
  max_capital_used: number
  trade_count: number
  closed_trade_count: number
  open_position_shares: number
  start_date: string
  end_date: string
  error: string
}

export interface EastmoneyQlibBenchmark {
  market: string
  symbol: string
  name: string
  start_date: string
  end_date: string
  start_close: number | null
  end_close: number | null
  return_percent: number | null
  excess_return_percent: number | null
  source: string
  error: string
}

export interface EastmoneyQlibPoolBacktestResult {
  pool: string
  source: string
  target_count: number
  tested_count: number
  skipped_count: number
  start_date: string
  end_date: string
  score_threshold: number
  score_profile: string
  take_profit_percent: number
  stop_loss_percent: number
  max_holding_days: number
  cost_rate: number
  total_profit: number
  total_invested: number
  total_fee: number
  max_capital_used: number
  trade_count: number
  closed_trade_count: number
  open_position_count: number
  force_liquidate_end: boolean
  benchmarks: EastmoneyQlibBenchmark[]
  error: string
  items: EastmoneyQlibPoolBacktestItem[]
}

export interface EastmoneyQlibStrategyYearResult {
  year: number
  start_date: string
  end_date: string
  total_profit: number
  return_percent: number | null
  max_capital_used: number
  total_fee: number
  trade_count: number
  tested_count: number
  skipped_count: number
  benchmark_name: string
  benchmark_return_percent: number | null
  excess_return_percent: number | null
}

export interface EastmoneyQlibStrategySearchItem {
  key: string
  name: string
  score_threshold: number
  score_profile: string
  take_profit_percent: number
  stop_loss_percent: number
  max_holding_days: number
  cost_rate: number
  total_profit: number
  average_return_percent: number | null
  min_return_percent: number | null
  average_excess_return_percent: number | null
  min_excess_return_percent: number | null
  profitable_year_count: number
  beat_benchmark_year_count: number
  tested_year_count: number
  all_years_profitable: boolean
  all_years_beat_benchmark: boolean
  is_qualified: boolean
  qualification_note: string
  years: EastmoneyQlibStrategyYearResult[]
}

export interface EastmoneyQlibStrategySearchResult {
  pool: string
  source: string
  years: number[]
  limit: number | null
  benchmark_name: string
  min_annual_return_percent: number
  require_beat_benchmark: boolean
  qualified_count: number
  done_count: number
  candidate_count: number
  status: string
  error: string
  items: EastmoneyQlibStrategySearchItem[]
}

export interface EastmoneyQlibRotationStrategySearchItem {
  key: string
  name: string
  score_profile: string
  rank_metric: string
  market_filter: string
  score_threshold: number
  min_amount: number
  top_n: number
  rebalance: string
  cost_rate: number
  total_profit: number
  average_return_percent: number | null
  min_return_percent: number | null
  average_excess_return_percent: number | null
  min_excess_return_percent: number | null
  profitable_year_count: number
  beat_benchmark_year_count: number
  tested_year_count: number
  all_years_profitable: boolean
  all_years_beat_benchmark: boolean
  is_qualified: boolean
  qualification_note: string
  years: EastmoneyQlibStrategyYearResult[]
}

export interface EastmoneyQlibRotationStrategySearchResult {
  pool: string
  source: string
  years: number[]
  limit: number | null
  benchmark_name: string
  min_annual_return_percent: number
  require_beat_benchmark: boolean
  qualified_count: number
  done_count: number
  candidate_count: number
  status: string
  error: string
  items: EastmoneyQlibRotationStrategySearchItem[]
}

export interface EastmoneyHkConnectMomentumCandidate {
  rank: number
  market: string
  symbol: string
  name: string
  signal_score: number
  return_10_percent: number
  amount: number
  average_amount_20: number
  close: number
  lot_size: number
  lot_value: number
  budget_lots: number
  estimated_cash: number
  market_cap: number
  selected: boolean
}

export interface EastmoneyHkConnectMomentumReviewResult {
  strategy_key: string
  strategy_name: string
  source: string
  status: string
  generated_at: string
  signal_date: string
  hsi_date: string
  hsi_close: number | null
  hsi_ma60: number | null
  hsi_filter_passed: boolean
  action: string
  summary: string
  pool_count: number
  usable_count: number
  capital: number
  max_position_percent: number
  single_position_budget: number
  cost_rate: number
  universe_limit: number
  min_market_cap: number
  min_amount: number
  top_n: number
  lookback_days: number
  volume_window_days: number
  hold_days: number
  error: string
  candidates: EastmoneyHkConnectMomentumCandidate[]
  selected: EastmoneyHkConnectMomentumCandidate[]
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

export async function fetchEastmoneyQlibHkPoolScreen(params: {
  refresh?: boolean
  limit?: number
  start_date?: string
} = {}) {
  const response = await api.get<EastmoneyQlibScreenResult>(
    '/eastmoney/qlib/screen/hk-pool',
    {
      params,
      timeout: 120000,
    },
  )
  return response.data
}

export async function fetchEastmoneyQlibOneLotScoreBacktest(params: {
  market?: string
  symbol?: string
  name?: string
  start_date?: string
  end_date?: string
  lot_size?: number
  score_threshold?: number
  score_profile?: string
  take_profit_percent?: number
  stop_loss_percent?: number
  max_holding_days?: number
  cost_rate?: number
  force_liquidate_end?: boolean
  refresh?: boolean
} = {}) {
  const response = await api.get<EastmoneyQlibBacktestResult>(
    '/eastmoney/qlib/backtest/one-lot-score',
    {
      params,
      timeout: 120000,
    },
  )
  return response.data
}

export async function fetchEastmoneyQlibHkPoolOneLotScoreBacktest(params: {
  refresh?: boolean
  background?: boolean
  progress?: boolean
  limit?: number
  detail_limit?: number
  start_date?: string
  end_date?: string
  score_threshold?: number
  score_profile?: string
  take_profit_percent?: number
  stop_loss_percent?: number
  max_holding_days?: number
  cost_rate?: number
  force_liquidate_end?: boolean
} = {}) {
  const response = await api.get<EastmoneyQlibPoolBacktestResult>(
    '/eastmoney/qlib/backtest/hk-pool-one-lot-score',
    {
      params,
      timeout: 300000,
    },
  )
  return response.data
}

export async function fetchEastmoneyQlibHkPoolStrategySearch(params: {
  years?: string
  limit?: number
  score_thresholds?: string
  score_profiles?: string
  take_profit_percents?: string
  stop_loss_percents?: string
  max_holding_days?: string
  cost_rate?: number
  min_annual_return_percent?: number
  require_beat_benchmark?: boolean
  background?: boolean
  progress?: boolean
} = {}) {
  const response = await api.get<EastmoneyQlibStrategySearchResult>(
    '/eastmoney/qlib/backtest/hk-pool-strategy-search',
    {
      params,
      timeout: 300000,
    },
  )
  return response.data
}

export async function fetchEastmoneyQlibHkPoolRotationStrategySearch(params: {
  years?: string
  limit?: number
  score_profiles?: string
  rank_metrics?: string
  market_filters?: string
  score_thresholds?: string
  min_amounts?: string
  top_n_values?: string
  rebalances?: string
  cost_rate?: number
  min_annual_return_percent?: number
  require_beat_benchmark?: boolean
  background?: boolean
  progress?: boolean
} = {}) {
  const response = await api.get<EastmoneyQlibRotationStrategySearchResult>(
    '/eastmoney/qlib/backtest/hk-pool-rotation-strategy-search',
    {
      params,
      timeout: 300000,
    },
  )
  return response.data
}

export async function fetchEastmoneyCrossAssetEtfCanaryRotation(params: {
  refresh?: boolean
  progress?: boolean
  start_date?: string
  hold_days?: number
  top_n?: number
  cost?: number
  canary_threshold?: number
} = {}) {
  const response = await api.get<EastmoneyEtfRotationBacktestResult>(
    '/eastmoney/qlib/backtest/cross-asset-etf-canary-rotation',
    {
      params,
      timeout: 60000,
    },
  )
  return response.data
}

export async function fetchEastmoneyStrategyResearchCatalog(params: {
  family?: string
  status?: string
  market?: string
  min_priority?: number
} = {}) {
  const response = await api.get<EastmoneyStrategyResearchCatalog>(
    '/eastmoney/strategy-research',
    {
      params,
      timeout: 60000,
    },
  )
  return response.data
}

export async function fetchEastmoneyStrategyResearchItem(strategyId: string) {
  const response = await api.get<EastmoneyStrategyResearchItem>(
    `/eastmoney/strategy-research/${encodeURIComponent(strategyId)}`,
    {
      timeout: 60000,
    },
  )
  return response.data
}

export async function fetchEastmoneyHkConnectMomentumReview(params: {
  refresh?: boolean
  background?: boolean
  progress?: boolean
  end_date?: string
  capital?: number
  max_position_percent?: number
  universe_limit?: number
  min_market_cap?: number
  min_amount?: number
  top_n?: number
  lookback_days?: number
  volume_window_days?: number
  hold_days?: number
  cost_rate?: number
} = {}) {
  const response = await api.get<EastmoneyHkConnectMomentumReviewResult>(
    '/eastmoney/qlib/hk-connect-momentum-review',
    {
      params,
      timeout: 300000,
    },
  )
  return response.data
}

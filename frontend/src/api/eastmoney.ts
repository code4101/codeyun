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

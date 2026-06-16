import api from '@/api'

export interface MobileSmsMessage {
  id: string
  device_id: string
  sms_id: string
  thread_id: string
  address: string
  person: string
  body: string
  date_ms: number
  date_sent_ms?: number | null
  message_type: string
  read?: boolean | null
  seen?: boolean | null
  status?: number | null
  service_center: string
  subscription_id?: number | null
  sim_slot_index?: number | null
  sim_display_name: string
  sim_carrier_name: string
  raw_json: Record<string, unknown>
  source: string
  first_seen_at: number
  last_seen_at: number
  created_at: number
  updated_at: number
}

export interface MobileSmsListResponse {
  ok: boolean
  items: MobileSmsMessage[]
  total: number
  page: number
  page_size: number
}

export interface MobileSmsStatsResponse {
  ok: boolean
  total: number
  latest: MobileSmsMessage | null
  devices: Array<{
    device_id: string
    count: number
  }>
}

export interface MobileSmsListParams {
  page?: number
  page_size?: number
  device_id?: string
  keyword?: string
  address?: string
}

export const fetchMobileSmsMessages = async (
  params: MobileSmsListParams = {},
): Promise<MobileSmsListResponse> => {
  const response = await api.get('/mobile-sms/messages', { params })
  return response.data
}

export const fetchMobileSmsStats = async (): Promise<MobileSmsStatsResponse> => {
  const response = await api.get('/mobile-sms/stats')
  return response.data
}

import api from '@/api'

export interface UsageCategory {
  key: string
  title: string
  resource_count: number
  data_bytes: number
  file_bytes: number
  total_bytes: number
  retained_bytes: number
  external_reference_bytes: number
  unknown_external_size_count: number
  unavailable_count: number
  parts: { label: string; count: number; bytes: number; retained_bytes: number }[]
}

export interface AccountUsage {
  owner_id: number
  generated_at: number
  total_bytes: number
  categories: UsageCategory[]
  notes: string[]
}

export async function fetchAccountUsage(signal?: AbortSignal): Promise<AccountUsage> {
  return (await api.get<AccountUsage>('/resources/account-usage', { signal, timeout: 60000 })).data
}

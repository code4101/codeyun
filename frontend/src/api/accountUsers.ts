import api from '@/api'

export interface AccountUserOption {
  id: number
  username: string
  nickname: string
}

export interface AccountUserOptionsResponse {
  users: AccountUserOption[]
}

export async function fetchAccountUserOptions(query = '', limit = 30) {
  const response = await api.get<AccountUserOptionsResponse>('/auth/user-options', {
    params: { q: query, limit },
  })
  return response.data
}

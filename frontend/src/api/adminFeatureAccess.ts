import api from '@/api'
import type { FeatureAccessContext } from '@/api/access'

export const fetchAdminAnonymousFeatureAccessContext = async (): Promise<FeatureAccessContext> => {
  const response = await api.get('/admin/feature-access/subjects/anonymous')
  return response.data
}

export const fetchAdminUserFeatureAccessContext = async (
  userId: number,
): Promise<FeatureAccessContext> => {
  const response = await api.get(`/admin/feature-access/subjects/users/${userId}`)
  return response.data
}

export const updateAdminAnonymousFeatureAccessContext = async (
  overrides: Record<string, string>,
): Promise<FeatureAccessContext> => {
  const response = await api.put('/admin/feature-access/subjects/anonymous', { overrides })
  return response.data
}

export const updateAdminUserFeatureAccessContext = async (
  userId: number,
  overrides: Record<string, string>,
): Promise<FeatureAccessContext> => {
  const response = await api.put(`/admin/feature-access/subjects/users/${userId}`, { overrides })
  return response.data
}

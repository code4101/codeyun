let pageClientInstanceId = ''
const PAGE_CLIENT_INSTANCE_STORAGE_KEY = 'codeyun.save-client-instance.v1'

const randomId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export const getSaveClientInstanceId = () => {
  if (pageClientInstanceId) return pageClientInstanceId

  if (typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined') {
    try {
      const storedId = window.sessionStorage.getItem(PAGE_CLIENT_INSTANCE_STORAGE_KEY)
      if (storedId) {
        pageClientInstanceId = storedId
        return pageClientInstanceId
      }
      pageClientInstanceId = `page-${randomId()}`
      window.sessionStorage.setItem(PAGE_CLIENT_INSTANCE_STORAGE_KEY, pageClientInstanceId)
      return pageClientInstanceId
    } catch {
      // Storage may be disabled; the in-memory identity still isolates this page lifecycle.
    }
  }

  pageClientInstanceId = `page-${randomId()}`
  return pageClientInstanceId
}

export const createSaveMutationId = () => `mutation-${randomId()}`

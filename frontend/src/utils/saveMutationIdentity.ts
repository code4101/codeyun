let pageClientInstanceId = ''

const randomId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export const getSaveClientInstanceId = () => {
  if (!pageClientInstanceId) pageClientInstanceId = `page-${randomId()}`
  return pageClientInstanceId
}

export const createSaveMutationId = () => `mutation-${randomId()}`

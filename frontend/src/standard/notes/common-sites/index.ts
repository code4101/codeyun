import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesCommonSites',
  canonicalPath: '/notes/common-sites',
  component: () => import('./page.vue'),
  permissionKey: 'notes.common-sites',
  requiresAuth: true,
}

export default page

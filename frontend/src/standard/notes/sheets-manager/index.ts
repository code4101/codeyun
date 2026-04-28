import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesSheetManager',
  canonicalPath: '/notes/sheets',
  component: () => import('./page.vue'),
  permissionKey: 'notes.sheets',
  requiresAuth: true,
}

export default page

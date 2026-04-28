import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesSheetEditor',
  canonicalPath: '/notes/sheets/:sheetId',
  component: () => import('./page.vue'),
  permissionKey: 'notes.sheets',
  requiresAuth: true,
  menuPath: null,
}

export default page

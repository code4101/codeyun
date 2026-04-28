import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesWorkbookView',
  canonicalPath: '/notes/workbooks/:workbookId',
  component: () => import('./page.vue'),
  permissionKey: 'notes.sheets',
  requiresAuth: true,
  menuPath: null,
}

export default page

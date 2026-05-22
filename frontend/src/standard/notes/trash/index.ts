import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesTrash',
  canonicalPath: '/notes/trash',
  component: () => import('./page.vue'),
  permissionKey: 'notes.center',
  menuPath: null,
}

export default page

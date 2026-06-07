import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesTaskSystem',
  canonicalPath: '/notes/task-system',
  component: () => import('./page.vue'),
  permissionKey: 'notes.task-system',
  requiresAuth: true,
}

export default page

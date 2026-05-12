import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'Freebill',
  canonicalPath: '/notes/freebill',
  component: () => import('./page.vue'),
  permissionKey: 'notes.freebill',
  requiresAuth: true,
}

export default page

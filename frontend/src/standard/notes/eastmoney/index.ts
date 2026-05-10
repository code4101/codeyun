import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'Eastmoney',
  canonicalPath: '/notes/eastmoney',
  component: () => import('./page.vue'),
  permissionKey: 'notes.eastmoney',
  requiresAuth: true,
}

export default page

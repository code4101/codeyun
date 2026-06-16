import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'Eastmoney',
  canonicalPath: '/notes/eastmoney',
  component: () => import('./redirect.vue'),
  permissionKey: 'notes.eastmoney',
  requiresAuth: true,
}

export default page

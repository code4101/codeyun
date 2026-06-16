import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'EastmoneySync',
  canonicalPath: '/notes/eastmoney/sync',
  component: () => import('../page.vue'),
  permissionKey: 'notes.eastmoney',
  requiresAuth: true,
  menuPath: null,
}

export default page

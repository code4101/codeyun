import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'EastmoneyTrade',
  canonicalPath: '/notes/eastmoney/trade',
  component: () => import('./page.vue'),
  permissionKey: 'notes.eastmoney',
  requiresAuth: true,
}

export default page

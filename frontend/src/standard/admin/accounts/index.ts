import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AccountManager',
  canonicalPath: '/admin/accounts',
  component: () => import('./page.vue'),
  requiresAuth: true,
  requiresAdmin: true,
}

export default page

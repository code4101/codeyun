import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'RuntimeManagement',
  canonicalPath: '/cluster/runtime',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

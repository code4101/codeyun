import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ClusterServices',
  canonicalPath: '/cluster/services',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ClusterViewMn',
  canonicalPath: '/cluster/view-mn',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

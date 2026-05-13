import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ClusterRimeContextPrediction',
  canonicalPath: '/cluster/rime-context',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

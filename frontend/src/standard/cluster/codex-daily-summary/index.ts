import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ClusterCodexDailySummary',
  canonicalPath: '/cluster/codex/daily-summary',
  component: () => import('./page.vue'),
  permissionKey: 'cluster.codex',
  requiresAuth: true,
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ClusterStorageManager',
  canonicalPath: '/cluster/storage',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

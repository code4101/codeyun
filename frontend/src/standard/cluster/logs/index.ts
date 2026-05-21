import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'TaskLogs',
  canonicalPath: '/cluster/logs/:id',
  component: () => import('./page.vue'),
  requiresAuth: true,
  permissionKey: 'cluster.tasks',
  menuPath: '/cluster/runtime',
}

export default page

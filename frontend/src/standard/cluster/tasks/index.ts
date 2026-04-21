import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'DeviceTasks',
  canonicalPath: '/cluster/tasks',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

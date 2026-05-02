import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'BackgroundTasks',
  canonicalPath: '/admin/background-tasks',
  component: () => import('./page.vue'),
  requiresAuth: true,
  requiresAdmin: true,
}

export default page

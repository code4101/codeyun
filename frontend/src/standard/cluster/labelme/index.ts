import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'DeviceLabelmeBrowser',
  canonicalPath: '/cluster/labelme',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

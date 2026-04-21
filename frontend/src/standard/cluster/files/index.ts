import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'DeviceFileBrowser',
  canonicalPath: '/cluster/files',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

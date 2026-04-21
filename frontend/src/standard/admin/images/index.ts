import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'StorageManager',
  canonicalPath: '/admin/images',
  component: () => import('./page.vue'),
  requiresAuth: true,
  requiresAdmin: true,
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ZaohuaHerbCatalog',
  canonicalPath: '/zaohua/herbs',
  component: () => import('./page.vue'),
}

export default page

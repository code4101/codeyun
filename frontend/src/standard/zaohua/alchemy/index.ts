import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ZaohuaAlchemyCatalog',
  canonicalPath: '/zaohua/alchemy',
  component: () => import('./page.vue'),
}

export default page

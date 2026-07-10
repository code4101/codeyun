import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ZaohuaFurnaceCatalog',
  canonicalPath: '/zaohua/furnaces',
  component: () => import('./page.vue'),
}

export default page

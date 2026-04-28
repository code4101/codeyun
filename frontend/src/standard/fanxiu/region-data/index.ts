import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuRegionData',
  canonicalPath: '/fanxiu/region-data',
  component: () => import('./page.vue'),
}

export default page

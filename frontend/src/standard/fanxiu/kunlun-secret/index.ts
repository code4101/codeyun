import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuKunlunSecret',
  canonicalPath: '/fanxiu/activity-list/kunlun-secret',
  component: () => import('./page.vue'),
}

export default page

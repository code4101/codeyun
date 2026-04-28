import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuDivineResource',
  canonicalPath: '/fanxiu/activity-list/divine-resource',
  component: () => import('./page.vue'),
}

export default page

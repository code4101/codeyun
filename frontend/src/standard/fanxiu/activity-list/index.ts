import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuActivityList',
  canonicalPath: '/fanxiu/activity-list',
  component: () => import('./page.vue'),
}

export default page

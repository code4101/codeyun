import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuTopActivity',
  canonicalPath: '/fanxiu/activity-list/top-activity',
  component: () => import('./page.vue'),
}

export default page

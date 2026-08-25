import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuResourceRanking',
  canonicalPath: '/fanxiu/activity-list/resource-ranking',
  component: () => import('./page.vue'),
}

export default page

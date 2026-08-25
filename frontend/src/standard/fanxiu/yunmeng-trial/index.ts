import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuYunmengTrial',
  canonicalPath: '/fanxiu/activity-list/yunmeng-trial',
  component: () => import('./page.vue'),
}

export default page


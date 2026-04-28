import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuXianzhouMarathon',
  canonicalPath: '/fanxiu/activity-list/xianzhou-marathon',
  component: () => import('./page.vue'),
}

export default page

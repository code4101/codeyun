import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuShouyuanExploration',
  canonicalPath: '/fanxiu/activity-list/shouyuan-exploration',
  component: () => import('./page.vue'),
}

export default page

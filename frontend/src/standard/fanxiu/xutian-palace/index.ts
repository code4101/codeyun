import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuXutianPalace',
  canonicalPath: '/fanxiu/activity-list/xutian-palace',
  component: () => import('./page.vue'),
}

export default page

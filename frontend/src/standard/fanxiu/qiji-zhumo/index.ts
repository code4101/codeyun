import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuQijiZhumo',
  canonicalPath: '/fanxiu/activity-list/qiji-zhumo',
  component: () => import('./page.vue'),
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuRecharge',
  canonicalPath: '/fanxiu/recharge',
  component: () => import('./page.vue'),
}

export default page

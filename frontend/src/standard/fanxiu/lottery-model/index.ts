import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuLotteryModel',
  canonicalPath: '/fanxiu/lottery-model',
  component: () => import('./page.vue'),
}

export default page

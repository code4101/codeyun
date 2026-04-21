import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuDiscountGuide',
  canonicalPath: '/fanxiu/discount',
  component: () => import('./page.vue'),
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuMagicTreasureHall',
  canonicalPath: '/fanxiu/inventory/magic-treasure-hall',
  component: () => import('./page.vue'),
}

export default page

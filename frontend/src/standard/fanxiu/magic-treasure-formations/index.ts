import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuMagicTreasureFormations',
  canonicalPath: '/fanxiu/inventory/magic-treasure-hall/formations',
  component: () => import('./page.vue'),
}

export default page

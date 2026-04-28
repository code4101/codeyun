import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuSpiritBeastHall',
  canonicalPath: '/fanxiu/inventory/spirit-beast-hall',
  component: () => import('./page.vue'),
}

export default page

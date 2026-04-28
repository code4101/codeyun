import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuWardrobeHall',
  canonicalPath: '/fanxiu/inventory/wardrobe-hall',
  component: () => import('./page.vue'),
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuGameWindow',
  canonicalPath: '/fanxiu/game-window',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

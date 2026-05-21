import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuGameWindow2',
  canonicalPath: '/fanxiu/game-window2',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

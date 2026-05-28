import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuGameWindow3',
  canonicalPath: '/fanxiu/game-window3',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

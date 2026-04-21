import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuLabelmeBrowser',
  canonicalPath: '/fanxiu/labelme',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuDataAnnotation',
  canonicalPath: '/fanxiu/data-annotation',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

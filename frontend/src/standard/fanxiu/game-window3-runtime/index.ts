import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuDataAnnotationRuntime',
  canonicalPath: '/fanxiu/data-annotation/runtime',
  component: () => import('./page.vue'),
  permissionKey: 'fanxiu.behavior-tree',
  menuPath: '/fanxiu/data-annotation/runtime',
  requiresAuth: true,
}

export default page

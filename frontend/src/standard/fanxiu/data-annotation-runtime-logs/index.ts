import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuDataAnnotationRuntimeLogs',
  canonicalPath: '/fanxiu/data-annotation/runtime/logs',
  component: () => import('./page.vue'),
  permissionKey: 'fanxiu.behavior-tree',
  menuPath: null,
  requiresAuth: true,
}

export default page

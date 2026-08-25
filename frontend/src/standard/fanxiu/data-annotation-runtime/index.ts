import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuBehaviorTreeRuntime',
  // URL retained as a bookmark/permission compatibility contract. The page,
  // API types and user-facing terminology are Behavior Tree Runtime.
  canonicalPath: '/fanxiu/data-annotation/runtime',
  component: () => import('./page.vue'),
  permissionKey: 'fanxiu.behavior-tree',
  menuPath: '/fanxiu/data-annotation/runtime',
  requiresAuth: true,
}

export default page

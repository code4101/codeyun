import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuKernelScheduler',
  // URL retained as a bookmark/permission compatibility contract. The page,
  // API types and user-facing terminology are Kernel Scheduler.
  canonicalPath: '/fanxiu/kernel-scheduler',
  component: () => import('./page.vue'),
  permissionKey: 'fanxiu.kernel-scheduler',
  menuPath: '/fanxiu/kernel-scheduler',
  requiresAuth: true,
}

export default page

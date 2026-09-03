import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuKernelSchedulerLogs',
  canonicalPath: '/fanxiu/kernel-scheduler/logs',
  component: () => import('./page.vue'),
  permissionKey: 'fanxiu.kernel-scheduler',
  menuPath: null,
  requiresAuth: true,
}

export default page

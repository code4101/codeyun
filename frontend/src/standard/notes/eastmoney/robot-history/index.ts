import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'EastmoneyRobotHistory',
  canonicalPath: '/notes/eastmoney/robot-history',
  component: () => import('../redirect.vue'),
  permissionKey: 'notes.eastmoney',
  requiresAuth: true,
  menuPath: null,
}

export default page

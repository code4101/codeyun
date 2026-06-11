import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'EastmoneyRobotHistory',
  canonicalPath: '/notes/eastmoney/robot-history',
  component: () => import('./page.vue'),
  permissionKey: 'notes.eastmoney',
  requiresAuth: true,
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AttendanceHeaderTool',
  canonicalPath: '/attendance/header-tool',
  component: () => import('./page.vue'),
  permissionKey: 'attendance.header-tool',
  requiresAuth: true,
}

export default page

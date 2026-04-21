import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AttendanceWjxData',
  canonicalPath: '/attendance/questionnaire/data',
  component: () => import('./page.vue'),
  requiresAuth: true,
  permissionKey: 'attendance.wjx-data',
}

export default page

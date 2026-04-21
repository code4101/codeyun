import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AttendanceWjxCatalog',
  canonicalPath: '/attendance/questionnaire/catalog',
  component: () => import('./page.vue'),
  requiresAuth: true,
  permissionKey: 'attendance.wjx-templates',
}

export default page

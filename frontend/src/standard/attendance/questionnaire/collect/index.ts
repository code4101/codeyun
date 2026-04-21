import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AttendanceWjxCollect',
  canonicalPath: '/attendance/questionnaire/collect',
  component: () => import('./page.vue'),
  permissionKey: 'attendance.wjx-feedback',
}

export default page

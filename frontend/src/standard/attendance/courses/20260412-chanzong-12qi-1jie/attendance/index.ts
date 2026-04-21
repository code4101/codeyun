import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AttendanceCourse20260412Chanzong12qi1jieAttendance',
  canonicalPath: '/attendance/courses/20260412-chanzong-12qi-1jie/attendance',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

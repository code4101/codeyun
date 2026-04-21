import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AttendanceCourses',
  canonicalPath: '/attendance/courses',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

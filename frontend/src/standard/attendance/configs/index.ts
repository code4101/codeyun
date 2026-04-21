import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AttendanceConfigs',
  canonicalPath: '/attendance/configs',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

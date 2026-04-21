import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AttendanceOrders',
  canonicalPath: '/attendance/orders',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

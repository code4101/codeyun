import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'HardwareTemperature',
  canonicalPath: '/cluster/hardware-temperature',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

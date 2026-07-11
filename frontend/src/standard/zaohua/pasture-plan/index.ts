import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ZaohuaPasturePlanDemo',
  canonicalPath: '/zaohua/pasture/plan-demo',
  component: () => import('./page.vue'),
}

export default page

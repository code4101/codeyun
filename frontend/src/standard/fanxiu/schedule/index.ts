import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuSchedule',
  canonicalPath: '/fanxiu/schedule',
  component: () => import('./page.vue'),
}

export default page

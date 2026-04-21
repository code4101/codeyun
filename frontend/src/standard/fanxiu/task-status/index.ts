import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuTaskStatus',
  canonicalPath: '/fanxiu/task-status',
  component: () => import('./page.vue'),
}

export default page

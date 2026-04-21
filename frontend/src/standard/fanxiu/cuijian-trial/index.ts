import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'CuijianTrial',
  canonicalPath: '/fanxiu/cuijian-trial',
  component: () => import('./page.vue'),
}

export default page

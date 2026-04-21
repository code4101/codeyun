import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'DrawCalculator',
  canonicalPath: '/fanxiu/draw-calc',
  component: () => import('./page.vue'),
}

export default page

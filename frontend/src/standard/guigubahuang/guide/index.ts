import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'GuigubahuangGuide',
  canonicalPath: '/guigubahuang/guide',
  component: () => import('./page.vue'),
}

export default page

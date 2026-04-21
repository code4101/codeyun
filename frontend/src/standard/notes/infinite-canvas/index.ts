import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'InfiniteCanvas',
  canonicalPath: '/notes/infinite-canvas',
  component: () => import('./page.vue'),
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ImageBrowser',
  canonicalPath: '/tools/image-browser',
  component: () => import('./page.vue'),
}

export default page

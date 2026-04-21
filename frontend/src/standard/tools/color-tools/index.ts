import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ColorTools',
  canonicalPath: '/tools/color-tools',
  component: () => import('./page.vue'),
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'MystiaWiki',
  canonicalPath: '/mystia/wiki',
  component: () => import('./page.vue'),
}

export default page

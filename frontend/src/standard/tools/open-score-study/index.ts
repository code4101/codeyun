import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'OpenScoreStudy',
  canonicalPath: '/tools/open-score-study',
  component: () => import('./page.vue'),
}

export default page

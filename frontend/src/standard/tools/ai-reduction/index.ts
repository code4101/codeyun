import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiReduction',
  canonicalPath: '/tools/ai-reduction',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

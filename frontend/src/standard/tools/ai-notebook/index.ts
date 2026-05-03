import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiNotebook',
  canonicalPath: '/tools/ai-notebook',
  component: () => import('./page.vue'),
  requiresAuth: true,
  requiresAdmin: true,
}

export default page

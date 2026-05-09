import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiCodexSaver',
  canonicalPath: '/tools/ai-codex-saver',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AiGitCommit',
  canonicalPath: '/tools/ai-git-commit',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

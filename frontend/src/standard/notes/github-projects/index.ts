import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesGithubProjects',
  canonicalPath: '/notes/github-projects',
  component: () => import('./page.vue'),
  permissionKey: 'notes.github-projects',
  requiresAuth: true,
}

export default page

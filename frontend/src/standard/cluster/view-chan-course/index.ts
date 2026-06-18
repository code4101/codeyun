import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'ClusterViewChanCourse',
  canonicalPath: '/cluster/view-chan-course',
  component: () => import('./page.vue'),
  requiresAuth: true,
}

export default page

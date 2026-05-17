import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'AuthorContact',
  canonicalPath: '/contact-author',
  component: () => import('./page.vue'),
  permissionKey: 'author-contact',
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'NotesCenter',
  canonicalPath: '/notes/center',
  component: () => import('./page.vue'),
  menuPath: '/notes/star-map',
}

export default page

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'MusicTools',
  canonicalPath: '/tools/music-tools',
  component: () => import('./page.vue'),
}

export default page

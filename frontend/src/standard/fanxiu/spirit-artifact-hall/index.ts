import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'FanxiuSpiritArtifactHall',
  canonicalPath: '/fanxiu/inventory/spirit-artifact-hall',
  component: () => import('./page.vue'),
}

export default page

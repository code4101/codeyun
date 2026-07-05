import type { AppPageDefinition } from '@/router/pageRegistryTypes'

const page: AppPageDefinition = {
  routeName: 'PokemonTcgCatalog',
  canonicalPath: '/pokemon-tcg/catalog',
  component: () => import('./page.vue'),
}

export default page

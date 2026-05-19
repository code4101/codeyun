import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import accountManagerPage from './accounts'
import storageManagerPage from './images'

const pages: AppPageDefinition[] = [
  accountManagerPage,
  storageManagerPage,
]

export default pages

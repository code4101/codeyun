import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import accountManagerPage from './accounts'
import storageManagerPage from './images'
import backgroundTasksPage from './tasks'

const pages: AppPageDefinition[] = [
  accountManagerPage,
  storageManagerPage,
  backgroundTasksPage,
]

export default pages

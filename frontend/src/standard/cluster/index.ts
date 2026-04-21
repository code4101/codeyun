import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import deviceFileBrowserPage from './files'
import deviceLabelmeBrowserPage from './labelme'
import taskLogsPage from './logs'
import deviceTasksPage from './tasks'
import clusterViewMnPage from './view-mn'

const pages: AppPageDefinition[] = [
  deviceTasksPage,
  deviceFileBrowserPage,
  clusterViewMnPage,
  deviceLabelmeBrowserPage,
  taskLogsPage,
]

export default pages

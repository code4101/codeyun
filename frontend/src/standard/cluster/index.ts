import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import clusterCodexPage from './codex'
import deviceFileBrowserPage from './files'
import deviceLabelmeBrowserPage from './labelme'
import taskLogsPage from './logs'
import clusterStorageManagerPage from './storage'
import deviceTasksPage from './tasks'
import clusterViewMnPage from './view-mn'

const pages: AppPageDefinition[] = [
  deviceTasksPage,
  deviceFileBrowserPage,
  clusterStorageManagerPage,
  clusterCodexPage,
  clusterViewMnPage,
  deviceLabelmeBrowserPage,
  taskLogsPage,
]

export default pages

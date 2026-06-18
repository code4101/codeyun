import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import clusterCodexPage from './codex'
import deviceFileBrowserPage from './files'
import deviceLabelmeBrowserPage from './labelme'
import taskLogsPage from './logs'
import clusterRimeContextPredictionPage from './rime-context'
import clusterServicesPage from './services'
import clusterTreeSizePage from './storage'
import deviceTasksPage from './tasks'
import clusterViewChanCoursePage from './view-chan-course'
import clusterViewMnPage from './view-mn'

const pages: AppPageDefinition[] = [
  deviceTasksPage,
  clusterRimeContextPredictionPage,
  clusterServicesPage,
  deviceFileBrowserPage,
  clusterTreeSizePage,
  clusterCodexPage,
  clusterViewMnPage,
  clusterViewChanCoursePage,
  deviceLabelmeBrowserPage,
  taskLogsPage,
]

export default pages

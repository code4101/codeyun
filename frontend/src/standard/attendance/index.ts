import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import attendanceConfigsPage from './configs'
import attendanceHeaderToolPage from './header-tool'
import attendanceOrdersPage from './orders'
import attendanceWjxCollectPage from './questionnaire/collect'
import attendanceWjxDataPage from './questionnaire/data'

const pages: AppPageDefinition[] = [
  attendanceConfigsPage,
  attendanceHeaderToolPage,
  attendanceWjxCollectPage,
  attendanceWjxDataPage,
  attendanceOrdersPage,
]

export default pages

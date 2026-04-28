import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import attendanceConfigsPage from './configs'
import attendanceOrdersPage from './orders'
import attendanceWjxCatalogPage from './questionnaire/catalog'
import attendanceWjxCollectPage from './questionnaire/collect'
import attendanceWjxDataPage from './questionnaire/data'

const pages: AppPageDefinition[] = [
  attendanceConfigsPage,
  attendanceWjxCatalogPage,
  attendanceWjxCollectPage,
  attendanceWjxDataPage,
  attendanceOrdersPage,
]

export default pages

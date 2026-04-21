import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import attendanceConfigsPage from './configs'
import attendanceCoursesPage from './courses'
import attendanceCourse20260412Chanzong12qi1jiePage from './courses/20260412-chanzong-12qi-1jie'
import attendanceCourse20260412Chanzong12qi1jieAttendancePage from './courses/20260412-chanzong-12qi-1jie/attendance'
import attendanceCourse20260412Chanzong12qi1jieRegistrationPage from './courses/20260412-chanzong-12qi-1jie/registration'
import attendanceOrdersPage from './orders'
import attendanceWjxCatalogPage from './questionnaire/catalog'
import attendanceWjxCollectPage from './questionnaire/collect'
import attendanceWjxDataPage from './questionnaire/data'

const pages: AppPageDefinition[] = [
  attendanceConfigsPage,
  attendanceCoursesPage,
  attendanceCourse20260412Chanzong12qi1jiePage,
  attendanceCourse20260412Chanzong12qi1jieRegistrationPage,
  attendanceCourse20260412Chanzong12qi1jieAttendancePage,
  attendanceWjxCatalogPage,
  attendanceWjxCollectPage,
  attendanceWjxDataPage,
  attendanceOrdersPage,
]

export default pages

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import zaohuaAlchemyPage from './alchemy'
import zaohuaHerbPage from './herbs'
import zaohuaFurnacePage from './furnaces'
import zaohuaPasturePage from './pasture'
import zaohuaPasturePlanPage from './pasture-plan'

const pages: AppPageDefinition[] = [zaohuaAlchemyPage, zaohuaFurnacePage, zaohuaHerbPage, zaohuaPasturePage, zaohuaPasturePlanPage]

export default pages

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import zaohuaAlchemyPage from './alchemy'
import zaohuaHerbPage from './herbs'
import zaohuaFurnacePage from './furnaces'
import zaohuaPasturePage from './pasture'
import zaohuaGradePage from './grades'

const pages: AppPageDefinition[] = [zaohuaAlchemyPage, zaohuaFurnacePage, zaohuaHerbPage, zaohuaPasturePage, zaohuaGradePage]

export default pages

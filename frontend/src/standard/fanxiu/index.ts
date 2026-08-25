import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import fanxiuActivityListPage from './activity-list'
import beastSoulCalculatorPage from './calculator'
import cuijianTrialPage from './cuijian-trial'
import fanxiuDivineResourcePage from './divine-resource'
import discountGuidePage from './discount'
import drawCalculatorPage from './draw-calc'
import fanxiuKunlunSecretPage from './kunlun-secret'
import fanxiuLabelmePage from './labelme'
import fanxiuDataAnnotationPage from './data-annotation'
import fanxiuBehaviorTreeRuntimeLogsPage from './data-annotation-runtime-logs'
import fanxiuBehaviorTreeRuntimePage from './data-annotation-runtime'
import fanxiuLotteryModelPage from './lottery-model'
import fanxiuGongfaAtlasPage from './gongfa-atlas'
import magicTreasureHallPage from './magic-treasure-hall'
import magicTreasureFormationsPage from './magic-treasure-formations'
import fanxiuQijiZhumoPage from './qiji-zhumo'
import fanxiuRechargePage from './recharge'
import fanxiuResourceRankingPage from './resource-ranking'
import fanxiuTopActivityPage from './top-activity'
import spiritArtifactHallPage from './spirit-artifact-hall'
import spiritBeastHallPage from './spirit-beast-hall'
import wardrobeHallPage from './wardrobe-hall'
import fanxiuWikiPage from './wiki'
import xianzhouRacePage from './xianzhou-race'

const pages: AppPageDefinition[] = [
  fanxiuWikiPage,
  fanxiuDataAnnotationPage,
  fanxiuBehaviorTreeRuntimePage,
  fanxiuBehaviorTreeRuntimeLogsPage,
  fanxiuActivityListPage,
  fanxiuKunlunSecretPage,
  fanxiuTopActivityPage,
  fanxiuResourceRankingPage,
  fanxiuDivineResourcePage,
  fanxiuQijiZhumoPage,
  fanxiuLabelmePage,
  wardrobeHallPage,
  spiritBeastHallPage,
  fanxiuGongfaAtlasPage,
  magicTreasureHallPage,
  magicTreasureFormationsPage,
  spiritArtifactHallPage,
  beastSoulCalculatorPage,
  drawCalculatorPage,
  fanxiuLotteryModelPage,
  discountGuidePage,
  fanxiuRechargePage,
  xianzhouRacePage,
  cuijianTrialPage,
]

export default pages

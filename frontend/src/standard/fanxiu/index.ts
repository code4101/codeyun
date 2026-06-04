import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import fanxiuActivityListPage from './activity-list'
import beastSoulCalculatorPage from './calculator'
import cuijianTrialPage from './cuijian-trial'
import fanxiuDivineResourcePage from './divine-resource'
import discountGuidePage from './discount'
import drawCalculatorPage from './draw-calc'
import fanxiuKunlunSecretPage from './kunlun-secret'
import fanxiuLabelmePage from './labelme'
import fanxiuGameWindow3Page from './game-window3'
import fanxiuGameWindow3RuntimeLogsPage from './game-window3-runtime-logs'
import fanxiuGameWindow3RuntimePage from './game-window3-runtime'
import fanxiuLotteryModelPage from './lottery-model'
import magicTreasureHallPage from './magic-treasure-hall'
import magicTreasureFormationsPage from './magic-treasure-formations'
import fanxiuModaoInvasionPage from './modao-invasion'
import fanxiuQijiZhumoPage from './qiji-zhumo'
import fanxiuRechargePage from './recharge'
import fanxiuShouyuanExplorationPage from './shouyuan-exploration'
import spiritArtifactHallPage from './spirit-artifact-hall'
import spiritBeastHallPage from './spirit-beast-hall'
import wardrobeHallPage from './wardrobe-hall'
import fanxiuWikiPage from './wiki'
import xianzhouRacePage from './xianzhou-race'

const pages: AppPageDefinition[] = [
  fanxiuWikiPage,
  fanxiuGameWindow3Page,
  fanxiuGameWindow3RuntimePage,
  fanxiuGameWindow3RuntimeLogsPage,
  fanxiuActivityListPage,
  fanxiuKunlunSecretPage,
  fanxiuModaoInvasionPage,
  fanxiuShouyuanExplorationPage,
  fanxiuDivineResourcePage,
  fanxiuQijiZhumoPage,
  fanxiuLabelmePage,
  wardrobeHallPage,
  spiritBeastHallPage,
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

import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import beastSoulCalculatorPage from './calculator'
import cuijianTrialPage from './cuijian-trial'
import discountGuidePage from './discount'
import drawCalculatorPage from './draw-calc'
import fanxiuLabelmePage from './labelme'
import fanxiuRechargePage from './recharge'
import fanxiuTaskStatusPage from './task-status'
import xianzhouRacePage from './xianzhou-race'

const pages: AppPageDefinition[] = [
  fanxiuTaskStatusPage,
  fanxiuLabelmePage,
  beastSoulCalculatorPage,
  drawCalculatorPage,
  discountGuidePage,
  fanxiuRechargePage,
  xianzhouRacePage,
  cuijianTrialPage,
]

export default pages

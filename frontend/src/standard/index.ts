import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import adminPages from './admin'
import authorContactPage from './author-contact'
import attendancePages from './attendance'
import clusterPages from './cluster'
import dspPages from './dsp'
import fanxiuPages from './fanxiu'
import homePages from './home'
import magicCraftPages from './magic-craft'
import mystiaPages from './mystia'
import notesPages from './notes'
import toolsPages from './tools'

export const standardPageRegistry: AppPageDefinition[] = [
  ...homePages,
  authorContactPage,
  ...fanxiuPages,
  ...dspPages,
  ...mystiaPages,
  ...magicCraftPages,
  ...notesPages,
  ...clusterPages,
  ...toolsPages,
  ...attendancePages,
  ...adminPages,
]

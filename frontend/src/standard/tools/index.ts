import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import aiChatPage from './ai-chat'
import aiConfigPage from './ai-config'
import aiGitCommitPage from './ai-git-commit'
import aiReductionPage from './ai-reduction'
import aiWechatPage from './ai-wechat'
import colorToolsPage from './color-tools'
import imageBrowserPage from './image-browser'
import musicToolsPage from './music-tools'
import openScoreStudyPage from './open-score-study'
import passwordGeneratorPage from './password-generator'

const pages: AppPageDefinition[] = [
  passwordGeneratorPage,
  colorToolsPage,
  imageBrowserPage,
  musicToolsPage,
  openScoreStudyPage,
  aiConfigPage,
  aiChatPage,
  aiReductionPage,
  aiGitCommitPage,
  aiWechatPage,
]

export default pages

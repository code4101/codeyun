import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import aiChatPage from './ai-chat'
import aiConfigPage from './ai-config'
import aiEvoMindPage from './ai-evomind'
import aiGitCommitPage from './ai-git-commit'
import aiNotebookPage from './ai-notebook'
import aiReductionPage from './ai-reduction'
import aiWechatPage from './ai-wechat'
import colorToolsPage from './color-tools'
import imageBrowserPage from './image-browser'
import musicToolsPage from './music-tools'
import passwordGeneratorPage from './password-generator'

const pages: AppPageDefinition[] = [
  passwordGeneratorPage,
  colorToolsPage,
  imageBrowserPage,
  musicToolsPage,
  aiConfigPage,
  aiEvoMindPage,
  aiChatPage,
  aiReductionPage,
  aiGitCommitPage,
  aiNotebookPage,
  aiWechatPage,
]

export default pages

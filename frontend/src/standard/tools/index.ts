import type { AppPageDefinition } from '@/router/pageRegistryTypes'

import aiChatPage from './ai-chat'
import aiCodexSaverPage from './ai-codex-saver'
import aiConfigPage from './ai-config'
import aiGitCommitPage from './ai-git-commit'
import aiNotebookPage from './ai-notebook'
import aiReductionPage from './ai-reduction'
import aiWechatPage from './ai-wechat'
import colorToolsPage from './color-tools'
import imageBrowserPage from './image-browser'
import passwordGeneratorPage from './password-generator'

const pages: AppPageDefinition[] = [
  passwordGeneratorPage,
  colorToolsPage,
  imageBrowserPage,
  aiConfigPage,
  aiCodexSaverPage,
  aiChatPage,
  aiReductionPage,
  aiGitCommitPage,
  aiNotebookPage,
  aiWechatPage,
]

export default pages

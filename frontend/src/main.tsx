import 'vite/modulepreload-polyfill'
import { StrictMode, type ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { I18nProvider } from 'react-aria-components'
import './index.css'
import './styles/consoleShell.css'
import { AgentChatPage } from './screens/AgentChatPage'
import { DiagnosticsScreen } from './screens/DiagnosticsScreen'
import { McpServersScreen } from './screens/McpServersScreen'
import { UsageScreen } from './screens/UsageScreen'

const mountNode = document.getElementById('gobii-frontend-root')

if (!mountNode) {
  throw new Error('Gobii frontend mount element not found')
}

const appName = mountNode.dataset.app ?? 'agent-chat'

const agentId = mountNode.dataset.agentId || null
const agentName = mountNode.dataset.agentName || null

let screen: ReactElement

switch (appName) {
  case 'agent-chat':
    if (!agentId) {
      throw new Error('Agent identifier is required for the chat experience')
    }
    screen = <AgentChatPage agentId={agentId} agentName={agentName} />
    break
  case 'diagnostics':
    screen = <DiagnosticsScreen />
    break
  case 'usage':
    screen = <UsageScreen />
    break
  case 'mcp-servers': {
    const listUrl = mountNode.dataset.listUrl
    if (!listUrl) {
      throw new Error('MCP server list URL is required')
    }
    const detailTemplate = mountNode.dataset.detailUrlTemplate
    if (!detailTemplate) {
      throw new Error('MCP server detail URL template is required')
    }
    const oauthStartUrl = mountNode.dataset.oauthStartUrl
    const oauthMetadataUrl = mountNode.dataset.oauthMetadataUrl
    const oauthCallbackPath = mountNode.dataset.oauthCallbackPath
    if (!oauthStartUrl || !oauthMetadataUrl || !oauthCallbackPath) {
      throw new Error('MCP OAuth endpoints are required')
    }

    screen = (
      <McpServersScreen
        listUrl={listUrl}
        detailUrlTemplate={detailTemplate}
        ownerScope={mountNode.dataset.ownerScope}
        ownerLabel={mountNode.dataset.ownerLabel}
        oauthStartUrl={oauthStartUrl}
        oauthMetadataUrl={oauthMetadataUrl}
        oauthCallbackPath={oauthCallbackPath}
      />
    )
    break
  }
  default:
    throw new Error(`Unsupported console React app: ${appName}`)
}

const queryClient = new QueryClient()
const locale = typeof navigator !== 'undefined' ? navigator.language : 'en-US'

createRoot(mountNode).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider locale={locale}>{screen}</I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
)

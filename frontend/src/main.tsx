import 'vite/modulepreload-polyfill'
import { StrictMode, type ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import './styles/consoleShell.css'
import { AgentChatPage } from './screens/AgentChatPage'
import { DiagnosticsScreen } from './screens/DiagnosticsScreen'
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
  default:
    throw new Error(`Unsupported console React app: ${appName}`)
}

const queryClient = new QueryClient()

createRoot(mountNode).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>{screen}</QueryClientProvider>
  </StrictMode>,
)

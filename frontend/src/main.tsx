import 'vite/modulepreload-polyfill'
import { StrictMode, type ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './styles/consoleShell.css'
import { AgentChatShellScreen } from './screens/AgentChatShellScreen'
import { DiagnosticsScreen } from './screens/DiagnosticsScreen'

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
    screen = <AgentChatShellScreen agentId={agentId} agentName={agentName} />
    break
  case 'diagnostics':
    screen = <DiagnosticsScreen />
    break
  default:
    throw new Error(`Unsupported console React app: ${appName}`)
}

createRoot(mountNode).render(<StrictMode>{screen}</StrictMode>)

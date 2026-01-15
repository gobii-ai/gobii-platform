import 'vite/modulepreload-polyfill'
import { StrictMode, lazy, Suspense, type ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { I18nProvider } from 'react-aria-components'
import { Loader2 } from 'lucide-react'
import type { PersistentAgentsScreenProps } from './screens/PersistentAgentsScreen'
import './index.css'
import './styles/consoleShell.css'
import { installViewportCssVars } from './util/viewport'

const AgentChatPage = lazy(async () => ({ default: (await import('./screens/AgentChatPage')).AgentChatPage }))
const AgentDetailScreen = lazy(async () => ({ default: (await import('./screens/AgentDetailScreen')).AgentDetailScreen }))
const DiagnosticsScreen = lazy(async () => ({ default: (await import('./screens/DiagnosticsScreen')).DiagnosticsScreen }))
const McpServersScreen = lazy(async () => ({ default: (await import('./screens/McpServersScreen')).McpServersScreen }))
const UsageScreen = lazy(async () => ({ default: (await import('./screens/UsageScreen')).UsageScreen }))
const PersistentAgentsScreen = lazy(async () => ({ default: (await import('./screens/PersistentAgentsScreen')).PersistentAgentsScreen }))
const LlmConfigScreen = lazy(async () => ({ default: (await import('./screens/LlmConfigScreen')).LlmConfigScreen }))
const EvalsScreen = lazy(async () => ({ default: (await import('./screens/EvalsScreen')).EvalsScreen }))
const EvalsDetailScreen = lazy(async () => ({ default: (await import('./screens/EvalsDetailScreen')).EvalsDetailScreen }))
const AgentAuditScreen = lazy(async () => ({ default: (await import('./screens/AgentAuditScreen')).AgentAuditScreen }))
const AgentFilesScreen = lazy(async () => ({ default: (await import('./screens/AgentFilesScreen')).AgentFilesScreen }))
const ImmersiveApp = lazy(async () => ({ default: (await import('./screens/ImmersiveApp')).ImmersiveApp }))

const LoadingFallback = () => (
  <div className="app-loading" role="status" aria-live="polite" aria-label="Loading">
    <Loader2 size={56} className="app-loading__spinner" aria-hidden="true" />
  </div>
)

const mountNode = document.getElementById('gobii-frontend-root')

if (!mountNode) {
  throw new Error('Gobii frontend mount element not found')
}

installViewportCssVars()

const appName = mountNode.dataset.app ?? 'agent-chat'
const isStaff = mountNode.dataset.isStaff === 'true'

const agentId = mountNode.dataset.agentId || null
const agentName = mountNode.dataset.agentName || null
const agentColor = mountNode.dataset.agentColor || null
const agentAvatarUrl = mountNode.dataset.agentAvatarUrl || null

let screen: ReactElement

function readJsonScript<T>(scriptId?: string): T {
  if (!scriptId) {
    throw new Error('JSON script identifier is required')
  }
  const script = document.getElementById(scriptId)
  if (!script || !script.textContent) {
    throw new Error(`JSON script ${scriptId} was not found`)
  }
  return JSON.parse(script.textContent) as T
}

// Check if we're embedded in an iframe (immersive overlay)
const isEmbedded = new URLSearchParams(window.location.search).get('embed') === '1'

// Create close handler for embedded mode - posts message to parent to close overlay
const handleEmbeddedClose = isEmbedded
  ? () => {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'gobii-immersive-close' }, window.location.origin)
      }
    }
  : undefined

switch (appName) {
  case 'agent-chat':
    if (!agentId) {
      throw new Error('Agent identifier is required for the chat experience')
    }
    screen = (
      <AgentChatPage
        agentId={agentId}
        agentName={agentName}
        agentColor={agentColor}
        agentAvatarUrl={agentAvatarUrl}
        onClose={handleEmbeddedClose}
      />
    )
    break
  case 'agent-detail':
    const propsId = mountNode.dataset.propsJsonId
    const initialData = readJsonScript<import('./screens/AgentDetailScreen').AgentDetailScreenProps['initialData']>(propsId)
    screen = <AgentDetailScreen initialData={initialData} />
    break
  case 'agent-files': {
    const propsId = mountNode.dataset.propsJsonId
    const initialData = readJsonScript<import('./screens/AgentFilesScreen').AgentFilesScreenProps['initialData']>(propsId)
    screen = <AgentFilesScreen initialData={initialData} />
    break
  }
  case 'diagnostics':
    screen = <DiagnosticsScreen />
    break
  case 'usage':
    screen = <UsageScreen />
    break
  case 'persistent-agents': {
    const propsId = mountNode.dataset.propsJsonId
    const initialData = readJsonScript<PersistentAgentsScreenProps['initialData']>(propsId)
    screen = <PersistentAgentsScreen initialData={initialData} />
    break
  }
  case 'mcp-servers': {
    const listUrl = mountNode.dataset.listUrl
    if (!listUrl) {
      throw new Error('MCP server list URL is required')
    }
    const detailTemplate = mountNode.dataset.detailUrlTemplate
    if (!detailTemplate) {
      throw new Error('MCP server detail URL template is required')
    }
    const assignTemplate = mountNode.dataset.assignUrlTemplate
    if (!assignTemplate) {
      throw new Error('MCP server assignment URL template is required')
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
        assignmentUrlTemplate={assignTemplate}
        ownerScope={mountNode.dataset.ownerScope}
        ownerLabel={mountNode.dataset.ownerLabel}
        oauthStartUrl={oauthStartUrl}
        oauthMetadataUrl={oauthMetadataUrl}
        oauthCallbackPath={oauthCallbackPath}
      />
    )
    break
  }
  case 'llm-config':
    screen = <LlmConfigScreen />
    break
  case 'evals':
    screen = <EvalsScreen />
    break
  case 'evals-detail': {
    const suiteRunId = mountNode.dataset.suiteRunId
    if (!suiteRunId) {
      throw new Error('Suite run identifier is required for evals detail screen')
    }
    screen = <EvalsDetailScreen suiteRunId={suiteRunId} isStaff={isStaff} />
    break
  }
  case 'agent-audit':
    if (!agentId) {
      throw new Error('Agent identifier is required for audit screen')
    }
    screen = <AgentAuditScreen agentId={agentId} agentName={agentName} agentColor={agentColor} />
    break
  case 'immersive-app':
    screen = <ImmersiveApp />
    break
  default:
    throw new Error(`Unsupported console React app: ${appName}`)
}

const queryClient = new QueryClient()
const locale = typeof navigator !== 'undefined' ? navigator.language : 'en-US'

createRoot(mountNode).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider locale={locale}>
        <Suspense fallback={<LoadingFallback />}>{screen}</Suspense>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
)

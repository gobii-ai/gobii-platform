import 'vite/modulepreload-polyfill'
import { StrictMode, lazy, Suspense, type ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { I18nProvider } from 'react-aria-components'
import { Loader2 } from 'lucide-react'
import type { LibraryAgentsPayload } from './api/library'
import { createAppStore } from './store/appStore'
import { hydrateSubscriptionFromMountElement } from './store/subscriptionSlice'
import { storeConsoleContextFromUrlSearch } from './util/consoleContextStorage'
import './index.css'
import './styles/consoleShell.css'

const McpServersScreen = lazy(async () => ({ default: (await import('./screens/McpServersScreen')).McpServersScreen }))
const SystemStatusScreen = lazy(async () => ({ default: (await import('./screens/SystemStatusScreen')).SystemStatusScreen }))
const StaffUsersScreen = lazy(async () => ({ default: (await import('./screens/StaffUsersScreen')).StaffUsersScreen }))
const LlmConfigScreen = lazy(async () => ({ default: (await import('./screens/LlmConfigScreen')).LlmConfigScreen }))
const SystemSettingsScreen = lazy(async () => ({ default: (await import('./screens/SystemSettingsScreen')).SystemSettingsScreen }))
const EvalsScreen = lazy(async () => ({ default: (await import('./screens/EvalsScreen')).EvalsScreen }))
const EvalsDetailScreen = lazy(async () => ({ default: (await import('./screens/EvalsDetailScreen')).EvalsDetailScreen }))
const AgentAuditScreen = lazy(async () => ({ default: (await import('./screens/AgentAuditScreen')).AgentAuditScreen }))
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

const rootNode = mountNode
const appName = mountNode.dataset.app ?? 'immersive-app'
const shouldInitializeSubscriptionStore = appName !== 'library'
const queryClient = new QueryClient()
const appStore = createAppStore({ queryClient })

storeConsoleContextFromUrlSearch()

if (shouldInitializeSubscriptionStore) {
  // Initialize subscription state from data attributes
  appStore.dispatch(hydrateSubscriptionFromMountElement(mountNode))
}
const isStaff = mountNode.dataset.isStaff === 'true'

const agentId = mountNode.dataset.agentId || null
const agentName = mountNode.dataset.agentName || null
const maxChatUploadSizeBytesRaw = mountNode.dataset.maxChatUploadSizeBytes
const maxChatUploadSizeBytesValue = maxChatUploadSizeBytesRaw ? Number.parseInt(maxChatUploadSizeBytesRaw, 10) : null
const maxChatUploadSizeBytes =
  typeof maxChatUploadSizeBytesValue === 'number' && Number.isFinite(maxChatUploadSizeBytesValue) && maxChatUploadSizeBytesValue > 0
    ? maxChatUploadSizeBytesValue
    : null
const selectedUserIdRaw = mountNode.dataset.userId
const selectedUserIdValue = selectedUserIdRaw ? Number.parseInt(selectedUserIdRaw, 10) : null
const selectedUserId = typeof selectedUserIdValue === 'number' && Number.isFinite(selectedUserIdValue) ? selectedUserIdValue : null
const selectedOrgId = mountNode.dataset.orgId || null

let screen: ReactElement | Promise<ReactElement>

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

const pipedreamAppsUrl = mountNode.dataset.pipedreamAppsUrl || null
const pipedreamAppSearchUrl = mountNode.dataset.pipedreamAppSearchUrl || null
const pipedreamAppsEnabled = Boolean(pipedreamAppsUrl && pipedreamAppSearchUrl)
const nativeIntegrationsUrl = mountNode.dataset.nativeIntegrationsUrl || null

switch (appName) {
  case 'system-status':
    screen = <SystemStatusScreen />
    break
  case 'staff-users':
    screen = <StaffUsersScreen selectedUserId={selectedUserId} selectedOrgId={selectedOrgId} />
    break
  case 'library': {
    const listUrl = mountNode.dataset.libraryListUrl
    const likeUrl = mountNode.dataset.libraryLikeUrl
    const canLike = mountNode.dataset.libraryCanLike === 'true'
    if (!listUrl || !likeUrl) {
      throw new Error('Library API URLs are required')
    }
    const propsId = mountNode.dataset.propsJsonId
    const initialData = propsId ? readJsonScript<LibraryAgentsPayload>(propsId) : undefined
    const initialCategory = mountNode.dataset.libraryInitialCategory || null
    const initialOfficialOnly = mountNode.dataset.libraryInitialOfficialOnly === 'true'
    screen = import('./screens/LibraryScreen').then(({ LibraryScreen }) => (
      <LibraryScreen
        listUrl={listUrl}
        likeUrl={likeUrl}
        canLike={canLike}
        initialCategory={initialCategory}
        initialOfficialOnly={initialOfficialOnly}
        initialData={initialData}
      />
    ))
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
    const testTemplate = mountNode.dataset.testUrlTemplate
    if (!testTemplate) {
      throw new Error('MCP server test URL template is required')
    }
    const oauthStartUrl = mountNode.dataset.oauthStartUrl
    const oauthMetadataUrl = mountNode.dataset.oauthMetadataUrl
    const oauthCallbackPath = mountNode.dataset.oauthCallbackPath
    const allowCommands = mountNode.dataset.allowCommands === 'true'
    if (!oauthStartUrl || !oauthMetadataUrl || !oauthCallbackPath) {
      throw new Error('MCP OAuth endpoints are required')
    }

    screen = (
      <McpServersScreen
        listUrl={listUrl}
        detailUrlTemplate={detailTemplate}
        assignmentUrlTemplate={assignTemplate}
        testUrlTemplate={testTemplate}
        ownerScope={mountNode.dataset.ownerScope}
        ownerLabel={mountNode.dataset.ownerLabel}
        allowCommands={allowCommands}
        nativeIntegrationsUrl={nativeIntegrationsUrl}
        pipedreamAppsUrl={pipedreamAppsEnabled ? pipedreamAppsUrl : null}
        pipedreamAppSearchUrl={pipedreamAppsEnabled ? pipedreamAppSearchUrl : null}
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
  case 'system-settings':
    screen = <SystemSettingsScreen />
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
    screen = (
      <AgentAuditScreen
        agentId={agentId}
        agentName={agentName}
        adminAgentUrl={mountNode.dataset.adminAgentUrl}
      />
    )
    break
  case 'immersive-app':
    screen = (
      <ImmersiveApp
        maxChatUploadSizeBytes={maxChatUploadSizeBytes}
        pipedreamAppsSettingsUrl={pipedreamAppsEnabled ? pipedreamAppsUrl : null}
        pipedreamAppSearchUrl={pipedreamAppsEnabled ? pipedreamAppSearchUrl : null}
        nativeIntegrationsUrl={nativeIntegrationsUrl}
      />
    )
    break
  default:
    throw new Error(`Unsupported console React app: ${appName}`)
}

const locale = typeof navigator !== 'undefined' ? navigator.language : 'en-US'

async function renderApp() {
  const resolvedScreen = await screen

  createRoot(rootNode).render(
    <StrictMode>
      <Provider store={appStore}>
        <QueryClientProvider client={queryClient}>
          <I18nProvider locale={locale}>
            <Suspense fallback={<LoadingFallback />}>{resolvedScreen}</Suspense>
          </I18nProvider>
        </QueryClientProvider>
      </Provider>
    </StrictMode>,
  )
}

void renderApp()

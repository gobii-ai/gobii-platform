import 'vite/modulepreload-polyfill'
import { StrictMode, lazy, Suspense, type ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { I18nProvider } from 'react-aria-components'
import { Loader2 } from 'lucide-react'
import type { PersistentAgentsScreenProps } from './screens/PersistentAgentsScreen'
import { initializeSubscriptionStore } from './stores/subscriptionStore'
import './index.css'
import './styles/consoleShell.css'

const AgentChatPage = lazy(async () => ({ default: (await import('./screens/AgentChatPage')).AgentChatPage }))
const AgentDetailScreen = lazy(async () => ({ default: (await import('./screens/AgentDetailScreen')).AgentDetailScreen }))
const AgentDashboardsScreen = lazy(async () => ({ default: (await import('./screens/AgentDashboardsScreen')).AgentDashboardsScreen }))
const DiagnosticsScreen = lazy(async () => ({ default: (await import('./screens/DiagnosticsScreen')).DiagnosticsScreen }))
const McpServersScreen = lazy(async () => ({ default: (await import('./screens/McpServersScreen')).McpServersScreen }))
const UsageScreen = lazy(async () => ({ default: (await import('./screens/UsageScreen')).UsageScreen }))
const SystemStatusScreen = lazy(async () => ({ default: (await import('./screens/SystemStatusScreen')).SystemStatusScreen }))
const StaffUsersScreen = lazy(async () => ({ default: (await import('./screens/StaffUsersScreen')).StaffUsersScreen }))
const PersistentAgentsScreen = lazy(async () => ({ default: (await import('./screens/PersistentAgentsScreen')).PersistentAgentsScreen }))
const LibraryScreen = lazy(async () => ({ default: (await import('./screens/LibraryScreen')).LibraryScreen }))
const LlmConfigScreen = lazy(async () => ({ default: (await import('./screens/LlmConfigScreen')).LlmConfigScreen }))
const SystemSettingsScreen = lazy(async () => ({ default: (await import('./screens/SystemSettingsScreen')).SystemSettingsScreen }))
const BillingScreen = lazy(async () => ({ default: (await import('./screens/BillingScreen')).BillingScreen }))
const EvalsScreen = lazy(async () => ({ default: (await import('./screens/EvalsScreen')).EvalsScreen }))
const EvalsDetailScreen = lazy(async () => ({ default: (await import('./screens/EvalsDetailScreen')).EvalsDetailScreen }))
const AgentAuditScreen = lazy(async () => ({ default: (await import('./screens/AgentAuditScreen')).AgentAuditScreen }))
const AgentFilesScreen = lazy(async () => ({ default: (await import('./screens/AgentFilesScreen')).AgentFilesScreen }))
const AgentEmailSettingsScreen = lazy(async () => ({ default: (await import('./screens/AgentEmailSettingsScreen')).AgentEmailSettingsScreen }))
const GlobalSecretsScreen = lazy(async () => ({ default: (await import('./screens/GlobalSecretsScreen')).GlobalSecretsScreen }))
const SystemSkillProfilesScreen = lazy(async () => ({ default: (await import('./screens/SystemSkillProfilesScreen')).SystemSkillProfilesScreen }))
const AgentSecretsScreen = lazy(async () => ({ default: (await import('./screens/AgentSecretsScreen')).AgentSecretsScreen }))
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

const appName = mountNode.dataset.app ?? 'agent-chat'
const shouldInitializeSubscriptionStore = appName !== 'library'

if (shouldInitializeSubscriptionStore) {
  // Initialize subscription state from data attributes
  initializeSubscriptionStore(mountNode)
}
const isStaff = mountNode.dataset.isStaff === 'true'

const agentId = mountNode.dataset.agentId || null
const agentName = mountNode.dataset.agentName || null
const agentColor = mountNode.dataset.agentColor || null
const agentAvatarUrl = mountNode.dataset.agentAvatarUrl || null
const agentEmail = mountNode.dataset.agentEmail || null
const agentSms = mountNode.dataset.agentSms || null
const collaboratorInviteUrl = mountNode.dataset.collaboratorInviteUrl || null
const auditUrl = mountNode.dataset.auditUrl || null
const auditUrlTemplate = mountNode.dataset.auditUrlTemplate || null
const maxChatUploadSizeBytesRaw = mountNode.dataset.maxChatUploadSizeBytes
const maxChatUploadSizeBytesValue = maxChatUploadSizeBytesRaw ? Number.parseInt(maxChatUploadSizeBytesRaw, 10) : null
const maxChatUploadSizeBytes =
  typeof maxChatUploadSizeBytesValue === 'number' && Number.isFinite(maxChatUploadSizeBytesValue) && maxChatUploadSizeBytesValue > 0
    ? maxChatUploadSizeBytesValue
    : null
const viewerUserIdRaw = mountNode.dataset.viewerUserId
const viewerUserIdValue = viewerUserIdRaw ? Number(viewerUserIdRaw) : null
const viewerUserId = Number.isFinite(viewerUserIdValue) ? viewerUserIdValue : null
const viewerEmail = mountNode.dataset.viewerEmail || null
const selectedUserIdRaw = mountNode.dataset.userId
const selectedUserIdValue = selectedUserIdRaw ? Number.parseInt(selectedUserIdRaw, 10) : null
const selectedUserId = typeof selectedUserIdValue === 'number' && Number.isFinite(selectedUserIdValue) ? selectedUserIdValue : null
const canManageCollaboratorsRaw = mountNode.dataset.canManageCollaborators
const canManageCollaborators =
  canManageCollaboratorsRaw === 'true'
    ? true
    : canManageCollaboratorsRaw === 'false'
      ? false
      : null
const isCollaboratorRaw = mountNode.dataset.isCollaborator
const isCollaborator =
  isCollaboratorRaw === 'true'
    ? true
    : isCollaboratorRaw === 'false'
      ? false
      : null

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
const pipedreamAppsUrl = mountNode.dataset.pipedreamAppsUrl || null
const pipedreamAppSearchUrl = mountNode.dataset.pipedreamAppSearchUrl || null
const pipedreamAppsEnabled = Boolean(pipedreamAppsUrl && pipedreamAppSearchUrl)

switch (appName) {
  case 'agent-chat': {
    if (!agentId) {
      throw new Error('Agent identifier is required for the chat experience')
    }
    screen = (
      <AgentChatPage
        agentId={agentId}
        agentName={agentName}
        agentColor={agentColor}
        agentAvatarUrl={agentAvatarUrl}
        agentEmail={agentEmail}
        agentSms={agentSms}
        collaboratorInviteUrl={collaboratorInviteUrl}
        isStaff={isStaff}
        auditUrl={auditUrl}
        auditUrlTemplate={auditUrlTemplate}
        maxChatUploadSizeBytes={maxChatUploadSizeBytes}
        canManageCollaborators={canManageCollaborators}
        isCollaborator={isCollaborator}
        viewerUserId={viewerUserId}
        viewerEmail={viewerEmail}
        pipedreamAppsSettingsUrl={pipedreamAppsEnabled ? pipedreamAppsUrl : null}
        pipedreamAppSearchUrl={pipedreamAppsEnabled ? pipedreamAppSearchUrl : null}
        onClose={handleEmbeddedClose}
      />
    )
    break
  }
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
  case 'agent-dashboards': {
    const propsId = mountNode.dataset.propsJsonId
    const initialData = readJsonScript<import('./screens/AgentDashboardsScreen').AgentDashboardsScreenProps['initialData']>(propsId)
    screen = <AgentDashboardsScreen initialData={initialData} />
    break
  }
  case 'agent-email-settings': {
    if (!agentId) {
      throw new Error('Agent identifier is required for email settings')
    }
    const emailSettingsUrl = mountNode.dataset.emailSettingsUrl
    const ensureAccountUrl = mountNode.dataset.emailSettingsEnsureUrl
    const testUrl = mountNode.dataset.emailSettingsTestUrl
    if (!emailSettingsUrl || !ensureAccountUrl || !testUrl) {
      throw new Error('Email settings API endpoints are required')
    }
    screen = (
      <AgentEmailSettingsScreen
        agentId={agentId}
        emailSettingsUrl={emailSettingsUrl}
        ensureAccountUrl={ensureAccountUrl}
        testUrl={testUrl}
      />
    )
    break
  }
  case 'diagnostics':
    screen = <DiagnosticsScreen />
    break
  case 'usage':
    screen = <UsageScreen />
    break
  case 'system-status':
    screen = <SystemStatusScreen />
    break
  case 'staff-users':
    screen = <StaffUsersScreen selectedUserId={selectedUserId} />
    break
  case 'persistent-agents': {
    const propsId = mountNode.dataset.propsJsonId
    const initialData = readJsonScript<PersistentAgentsScreenProps['initialData']>(propsId)
    screen = <PersistentAgentsScreen initialData={initialData} />
    break
  }
  case 'library': {
    const listUrl = mountNode.dataset.libraryListUrl
    const likeUrl = mountNode.dataset.libraryLikeUrl
    const canLike = mountNode.dataset.libraryCanLike === 'true'
    if (!listUrl || !likeUrl) {
      throw new Error('Library API URLs are required')
    }
    screen = <LibraryScreen listUrl={listUrl} likeUrl={likeUrl} canLike={canLike} />
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
    const allowCommands = mountNode.dataset.allowCommands === 'true'
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
        allowCommands={allowCommands}
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
  case 'billing': {
    const propsId = mountNode.dataset.propsJsonId
    const initialData = readJsonScript<import('./screens/BillingScreen').BillingScreenProps['initialData']>(propsId)
    screen = <BillingScreen initialData={initialData} />
    break
  }
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
        agentColor={agentColor}
        adminAgentUrl={mountNode.dataset.adminAgentUrl}
      />
    )
    break
  case 'global-secrets': {
    const listUrl = mountNode.dataset.listUrl
    if (!listUrl) {
      throw new Error('Global secrets list URL is required')
    }
    screen = (
      <GlobalSecretsScreen
        listUrl={listUrl}
        ownerScope={mountNode.dataset.ownerScope}
      />
    )
    break
  }
  case 'system-skill-profiles': {
    const listUrl = mountNode.dataset.listUrl
    if (!listUrl) {
      throw new Error('System skill profile list URL is required')
    }
    screen = (
      <SystemSkillProfilesScreen
        listUrl={listUrl}
        ownerScope={mountNode.dataset.ownerScope}
        skillKey={mountNode.dataset.skillKey}
      />
    )
    break
  }
  case 'agent-secrets': {
    if (!agentId) {
      throw new Error('Agent identifier is required for secrets screen')
    }
    const listUrl = mountNode.dataset.listUrl
    const detailUrlTemplate = mountNode.dataset.detailUrlTemplate
    const promoteUrlTemplate = mountNode.dataset.promoteUrlTemplate
    const agentDetailUrl = mountNode.dataset.agentDetailUrl
    const globalSecretsUrl = mountNode.dataset.globalSecretsUrl
    const requestUrl = mountNode.dataset.requestUrl
    if (!listUrl || !detailUrlTemplate || !promoteUrlTemplate || !agentDetailUrl || !globalSecretsUrl || !requestUrl) {
      throw new Error('Agent secrets API endpoints are required')
    }
    screen = (
      <AgentSecretsScreen
        agentId={agentId}
        agentName={agentName || 'Agent'}
        listUrl={listUrl}
        detailUrlTemplate={detailUrlTemplate}
        promoteUrlTemplate={promoteUrlTemplate}
        agentDetailUrl={agentDetailUrl}
        globalSecretsUrl={globalSecretsUrl}
        requestUrl={requestUrl}
      />
    )
    break
  }
  case 'immersive-app':
    screen = (
      <ImmersiveApp
        maxChatUploadSizeBytes={maxChatUploadSizeBytes}
        pipedreamAppsSettingsUrl={pipedreamAppsEnabled ? pipedreamAppsUrl : null}
        pipedreamAppSearchUrl={pipedreamAppsEnabled ? pipedreamAppSearchUrl : null}
      />
    )
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

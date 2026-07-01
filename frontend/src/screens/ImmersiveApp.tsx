import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Plus, Zap } from 'lucide-react'
import type { ConsoleContext } from '../api/context'
import { jsonFetch } from '../api/http'
import { SubscriptionUpgradeModal } from '../components/common/SubscriptionUpgradeModal'
import type { SelectionShellPage } from '../components/agentChat/SelectionShellPageSwitcher'
import { AnalyticsEvent } from '../constants/analyticsEvents'
import { useAgentRoster } from '../hooks/useAgentRoster'
import { AgentChatPage } from './AgentChatPage'
import { AgentCollaboratorInviteResponsePage } from './agentCollaborators/AgentCollaboratorInviteResponsePage'
import { ImmersiveApiKeysPage } from './apiKeys/ImmersiveApiKeysPage'
import { ImmersiveBillingPage } from './billing/ImmersiveBillingPage'
import { ImmersiveMcpServersPage } from './integrations/ImmersiveMcpServersPage'
import { ImmersiveOrganizationPage } from './organization/ImmersiveOrganizationPage'
import { OrganizationInviteAcceptPage } from './organization/OrganizationInviteAcceptPage'
import { ImmersiveProfilePage } from './profile/ImmersiveProfilePage'
import { ImmersiveSecretsPage } from './secrets/ImmersiveSecretsPage'
import { ImmersiveUsagePage } from './usage/ImmersiveUsagePage'
import { type PlanTier, useSubscriptionStore } from '../stores/subscriptionStore'
import { track } from '../util/analytics'
import { APP_NAVIGATE_EVENT } from '../util/appNavigation'
import { appendReturnTo } from '../util/returnTo'
import '../styles/immersiveApp.css'

const APP_BASE = '/app'
const RETURN_TO_STORAGE_KEY = 'gobii:immersive:return_to'
const DEFAULT_CLOSE_PATH = '/app/agents'
const UPGRADE_MODAL_QUERY_PARAM = 'upgrade'

type AppRoute =
  | { kind: 'command-center' }
  | { kind: 'agent-select' }
  | { kind: 'billing' }
  | { kind: 'profile' }
  | { kind: 'organization' }
  | { kind: 'organization-invite-accept'; token: string }
  | { kind: 'agent-collaborator-invite'; token: string; action: 'accept' | 'decline' }
  | { kind: 'secrets' }
  | { kind: 'usage' }
  | { kind: 'integrations' }
  | { kind: 'api-keys' }
  | { kind: 'agent-chat'; agentId: string | null }
  | { kind: 'not-found' }

type AppAnalyticsRoute = 'command_center' | 'agent_select' | 'billing' | 'profile' | 'organization' | 'organization_invite_accept' | 'agent_collaborator_invite' | 'secrets' | 'usage' | 'integrations' | 'api_keys' | 'agent_new' | 'agent_chat' | 'not_found'

type LocationSnapshot = {
  pathname: string
  search: string
  hash: string
}

type ConsoleSessionPayload = {
  user_id?: string
  email?: string
}

type ImmersiveAppProps = {
  maxChatUploadSizeBytes?: number | null
  pipedreamAppsSettingsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
  nativeIntegrationsUrl?: string | null
}

type AgentShellPage = Extract<SelectionShellPage, 'billing' | 'profile' | 'organization' | 'secrets' | 'usage' | 'integrations' | 'api-keys'>
type AgentShellLayout = 'main' | 'sidebar-shell'
type AgentShellPageRenderContext = {
  layout: AgentShellLayout
  refreshKey: number
  pipedreamAppsSettingsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
  nativeIntegrationsUrl?: string | null
}
type AgentShellPageConfig = {
  path: string
  render: (context: AgentShellPageRenderContext) => ReactNode
}

const AGENT_SHELL_PRESERVED_QUERY_KEYS = ['embed', 'return_to'] as const
const AGENT_SHELL_PAGE_CONFIG: Record<AgentShellPage, AgentShellPageConfig> = {
  billing: {
    path: '/app/billing',
    render: ({ layout, refreshKey }) => <ImmersiveBillingPage layout={layout} refreshKey={refreshKey} />,
  },
  profile: {
    path: '/app/profile',
    render: ({ layout, refreshKey }) => <ImmersiveProfilePage layout={layout} refreshKey={refreshKey} />,
  },
  organization: {
    path: '/app/organization',
    render: ({ layout, refreshKey }) => <ImmersiveOrganizationPage layout={layout} refreshKey={refreshKey} />,
  },
  secrets: {
    path: '/app/secrets',
    render: ({ layout, refreshKey }) => <ImmersiveSecretsPage layout={layout} refreshKey={refreshKey} />,
  },
  usage: {
    path: '/app/usage',
    render: ({ layout, refreshKey }) => <ImmersiveUsagePage layout={layout} refreshKey={refreshKey} />,
  },
  integrations: {
    path: '/app/integrations',
    render: ({ layout, refreshKey, pipedreamAppsSettingsUrl, pipedreamAppSearchUrl, nativeIntegrationsUrl }) => (
      <ImmersiveMcpServersPage
        layout={layout}
        refreshKey={refreshKey}
        nativeIntegrationsUrl={nativeIntegrationsUrl}
        pipedreamAppsUrl={pipedreamAppsSettingsUrl}
        pipedreamAppSearchUrl={pipedreamAppSearchUrl}
      />
    ),
  },
  'api-keys': {
    path: '/app/api-keys',
    render: ({ layout, refreshKey }) => <ImmersiveApiKeysPage layout={layout} refreshKey={refreshKey} />,
  },
}

function readLocation(): LocationSnapshot {
  return {
    pathname: window.location.pathname,
    search: window.location.search,
    hash: window.location.hash,
  }
}

function useAppLocation(): LocationSnapshot {
  const [location, setLocation] = useState<LocationSnapshot>(() => readLocation())

  useEffect(() => {
    const handleChange = () => setLocation(readLocation())
    window.addEventListener('popstate', handleChange)
    window.addEventListener('hashchange', handleChange)
    return () => {
      window.removeEventListener('popstate', handleChange)
      window.removeEventListener('hashchange', handleChange)
    }
  }, [])

  return location
}

function normalizeAppPath(pathname: string): string {
  if (pathname === APP_BASE) {
    return ''
  }
  if (pathname.startsWith(`${APP_BASE}/`)) {
    return pathname.slice(APP_BASE.length + 1)
  }
  return ''
}

function parseRoute(pathname: string): AppRoute {
  const path = normalizeAppPath(pathname)
  if (!path) {
    return { kind: 'command-center' }
  }

  const parts = path.split('/').filter(Boolean)
  if (parts[0] === 'agents' && parts[1] === 'new') {
    return { kind: 'agent-chat', agentId: null }
  }

  if (
    parts[0] === 'agent-collaborator-invites'
    && parts[1]
    && (parts[2] === 'accept' || parts[2] === 'decline')
  ) {
    return { kind: 'agent-collaborator-invite', token: parts[1], action: parts[2] }
  }

  if (parts[0] === 'agents' && parts[1]) {
    return { kind: 'agent-chat', agentId: parts[1] }
  }

  if (parts[0] === 'agents') {
    return { kind: 'agent-select' }
  }

  if (parts[0] === 'billing') {
    return { kind: 'billing' }
  }

  if (parts[0] === 'profile') {
    return { kind: 'profile' }
  }

  if (parts[0] === 'organization') {
    return { kind: 'organization' }
  }

  if (parts[0] === 'organizations' && parts[1] === 'invites' && parts[2] && parts[3] === 'accept') {
    return { kind: 'organization-invite-accept', token: parts[2] }
  }

  if (parts[0] === 'secrets') {
    return { kind: 'secrets' }
  }

  if (parts[0] === 'usage') {
    return { kind: 'usage' }
  }

  if (parts[0] === 'integrations') {
    return { kind: 'integrations' }
  }

  if (parts[0] === 'api-keys') {
    return { kind: 'api-keys' }
  }

  return { kind: 'not-found' }
}

function getAnalyticsRoute(route: AppRoute): AppAnalyticsRoute {
  if (route.kind === 'command-center') {
    return 'command_center'
  }
  if (route.kind === 'agent-select') {
    return 'agent_select'
  }
  if (route.kind === 'billing') {
    return 'billing'
  }
  if (route.kind === 'profile') {
    return 'profile'
  }
  if (route.kind === 'organization') {
    return 'organization'
  }
  if (route.kind === 'organization-invite-accept') {
    return 'organization_invite_accept'
  }
  if (route.kind === 'agent-collaborator-invite') {
    return 'agent_collaborator_invite'
  }
  if (route.kind === 'secrets') {
    return 'secrets'
  }
  if (route.kind === 'usage') {
    return 'usage'
  }
  if (route.kind === 'integrations') {
    return 'integrations'
  }
  if (route.kind === 'api-keys') {
    return 'api_keys'
  }
  if (route.kind === 'agent-chat') {
    return route.agentId ? 'agent_chat' : 'agent_new'
  }
  return 'not_found'
}

function getAnalyticsPath(route: AppRoute, pathname: string): string {
  if (route.kind === 'command-center') {
    return '/app'
  }
  if (route.kind === 'agent-select') {
    return '/app/agents'
  }
  if (route.kind === 'billing') {
    return '/app/billing'
  }
  if (route.kind === 'profile') {
    return '/app/profile'
  }
  if (route.kind === 'organization') {
    return '/app/organization'
  }
  if (route.kind === 'organization-invite-accept') {
    return '/app/organizations/invites/:token/accept'
  }
  if (route.kind === 'agent-collaborator-invite') {
    return `/app/agent-collaborator-invites/:token/${route.action}`
  }
  if (route.kind === 'secrets') {
    return '/app/secrets'
  }
  if (route.kind === 'usage') {
    return '/app/usage'
  }
  if (route.kind === 'integrations') {
    return '/app/integrations'
  }
  if (route.kind === 'api-keys') {
    return '/app/api-keys'
  }
  if (route.kind === 'agent-chat') {
    return route.agentId ? '/app/agents/:id' : '/app/agents/new'
  }
  return pathname
}

function getAnalyticsTitle(route: AppRoute): string {
  if (route.kind === 'command-center') {
    return 'Command Center · Gobii'
  }
  if (route.kind === 'agent-select') {
    return 'My Agents · Gobii'
  }
  if (route.kind === 'billing') {
    return 'Billing · Gobii'
  }
  if (route.kind === 'profile') {
    return 'Profile · Gobii'
  }
  if (route.kind === 'organization') {
    return 'Organization · Gobii'
  }
  if (route.kind === 'organization-invite-accept') {
    return 'Organization Invite · Gobii'
  }
  if (route.kind === 'agent-collaborator-invite') {
    return route.action === 'accept' ? 'Agent Invite · Gobii' : 'Decline Agent Invite · Gobii'
  }
  if (route.kind === 'secrets') {
    return 'Secrets · Gobii'
  }
  if (route.kind === 'usage') {
    return 'Usage · Gobii'
  }
  if (route.kind === 'integrations') {
    return 'Integrations · Gobii'
  }
  if (route.kind === 'api-keys') {
    return 'API Keys · Gobii'
  }
  if (route.kind === 'agent-chat') {
    return route.agentId ? 'Agent · Gobii' : 'New Agent · Gobii'
  }
  return 'Not found · Gobii'
}

function cleanQueryForTracking(search: string): string {
  const params = new URLSearchParams(search)
  params.delete('embed')
  params.delete('return_to')
  const cleaned = params.toString()
  return cleaned ? `?${cleaned}` : ''
}

function parseAgentShellPage(search: string): AgentShellPage | 'agents' {
  const page = new URLSearchParams(search).get('shell')
  return isAgentShellPage(page) ? page : 'agents'
}

function isAgentShellPage(value: unknown): value is AgentShellPage {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(AGENT_SHELL_PAGE_CONFIG, value)
}

function isAgentShellRoute(route: AppRoute): route is Extract<AppRoute, { kind: AgentShellPage }> {
  return isAgentShellPage(route.kind)
}

function buildAgentShellPath(agentId: string, page: SelectionShellPage, currentSearch = ''): string {
  const basePath = `/app/agents/${agentId}`
  const currentParams = new URLSearchParams(currentSearch)
  const nextParams = new URLSearchParams()
  for (const key of AGENT_SHELL_PRESERVED_QUERY_KEYS) {
    const value = currentParams.get(key)
    if (value !== null) {
      nextParams.set(key, value)
    }
  }
  if (isAgentShellPage(page)) {
    nextParams.set('shell', page)
  }
  const query = nextParams.toString()
  return query ? `${basePath}?${query}` : basePath
}

function buildAgentSelectionPath(currentSearch = ''): string {
  const currentParams = new URLSearchParams(currentSearch)
  const nextParams = new URLSearchParams()
  for (const key of AGENT_SHELL_PRESERVED_QUERY_KEYS) {
    const value = currentParams.get(key)
    if (value !== null) {
      nextParams.set(key, value)
    }
  }
  const query = nextParams.toString()
  return query ? `/app/agents?${query}` : '/app/agents'
}

function parseBooleanFlag(value: string | null): boolean {
  if (!value) {
    return false
  }
  return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase())
}

function hasUpgradeModalRequest(search: string): boolean {
  return parseBooleanFlag(new URLSearchParams(search).get(UPGRADE_MODAL_QUERY_PARAM))
}

function stripQueryParams(pathname: string, search: string, hash: string, keys: readonly string[]): string {
  const params = new URLSearchParams(search)
  for (const key of keys) {
    params.delete(key)
  }
  const query = params.toString()
  return `${pathname}${query ? `?${query}` : ''}${hash}`
}

function isAppPath(pathname: string): boolean {
  return pathname === APP_BASE || pathname.startsWith(`${APP_BASE}/`)
}

function buildCleanPath(pathname: string, search: string): string {
  const params = new URLSearchParams(search)
  params.delete('embed')
  params.delete('return_to')
  const cleaned = params.toString()
  return cleaned ? `${pathname}?${cleaned}` : pathname
}

function sanitizeSameOriginPath(value: string | null): string | null {
  if (!value) {
    return null
  }
  try {
    const url = new URL(value, window.location.origin)
    if (url.origin !== window.location.origin) {
      return null
    }
    return `${url.pathname}${url.search}${url.hash}`
  } catch {
    return null
  }
}

function stripSpawnParam(url: URL): string {
  url.searchParams.delete('spawn')
  const query = url.searchParams.toString()
  return `${url.pathname}${query ? `?${query}` : ''}${url.hash}`
}

function normalizeClosePath(value: string): string {
  let currentValue = value
  const visited = new Set<string>()

  while (true) {
    const url = new URL(currentValue, window.location.origin)
    const currentPath = `${url.pathname}${url.search}${url.hash}`
    if (visited.has(currentPath)) {
      return DEFAULT_CLOSE_PATH
    }
    visited.add(currentPath)

    const nestedCloseTarget = (
      sanitizeSameOriginPath(url.searchParams.get('return_to'))
      || sanitizeSameOriginPath(url.searchParams.get('next'))
    )
    if (nestedCloseTarget && !visited.has(nestedCloseTarget)) {
      currentValue = nestedCloseTarget
      continue
    }

    if (!isAppPath(url.pathname)) {
      return stripSpawnParam(url)
    }
    return DEFAULT_CLOSE_PATH
  }
}

function readReturnToFromSearch(search: string): string | null {
  const params = new URLSearchParams(search)
  return sanitizeSameOriginPath(params.get('return_to'))
}

function hasSubscribeSuccess(search: string): boolean {
  const params = new URLSearchParams(search)
  return params.get('subscribe_success') === '1'
}

function resolveReturnTo(search: string): string {
  const subscribeSuccess = hasSubscribeSuccess(search)
  const fromQuery = readReturnToFromSearch(search)
  if (fromQuery) {
    const normalizedQuery = normalizeClosePath(fromQuery)
    if (normalizedQuery !== DEFAULT_CLOSE_PATH) {
      sessionStorage.setItem(RETURN_TO_STORAGE_KEY, normalizedQuery)
      return normalizedQuery
    }
  }

  const stored = sanitizeSameOriginPath(sessionStorage.getItem(RETURN_TO_STORAGE_KEY))
  if (stored && subscribeSuccess) {
    const normalizedStored = normalizeClosePath(stored)
    if (normalizedStored !== DEFAULT_CLOSE_PATH) {
      return normalizedStored
    }
    sessionStorage.removeItem(RETURN_TO_STORAGE_KEY)
  }

  if (subscribeSuccess) {
    return DEFAULT_CLOSE_PATH
  }

  const fromReferrer = sanitizeSameOriginPath(document.referrer)
  if (fromReferrer) {
    const normalizedReferrer = normalizeClosePath(fromReferrer)
    if (normalizedReferrer !== DEFAULT_CLOSE_PATH) {
      sessionStorage.setItem(RETURN_TO_STORAGE_KEY, normalizedReferrer)
      return normalizedReferrer
    }
  }

  if (stored) {
    const normalizedStored = normalizeClosePath(stored)
    if (normalizedStored !== DEFAULT_CLOSE_PATH) {
      return normalizedStored
    }
    sessionStorage.removeItem(RETURN_TO_STORAGE_KEY)
  }

  return DEFAULT_CLOSE_PATH
}

type CommandCenterProps = {
  hasAgents: boolean
  isLoading: boolean
  onCreateAgent: () => void
}

function CommandCenter({ hasAgents, isLoading, onCreateAgent }: CommandCenterProps) {
  if (isLoading) {
    return (
      <section className="immersive-command">
        <p className="immersive-command__eyebrow">Gobii Command Center</p>
        <h1 className="immersive-command__title">Loading...</h1>
      </section>
    )
  }

  if (!hasAgents) {
    return (
      <section className="immersive-command">
        <div className="mb-8 flex size-20 items-center justify-center rounded-full bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-xl">
          <Zap className="size-8" aria-hidden="true" />
        </div>
        <p className="immersive-command__eyebrow">Gobii Command Center</p>
        <h1 className="immersive-command__title">No agents yet</h1>
        <p className="immersive-command__subtitle">
          Create your first AI agent to get started. Agents can automate tasks, monitor changes, send notifications, and much more.
        </p>
        <button
          type="button"
          onClick={onCreateAgent}
          className="group mt-6 inline-flex items-center justify-center gap-x-2 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-3 font-semibold text-white shadow-lg transition-all duration-300 hover:from-blue-700 hover:to-indigo-700 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
        >
          <Plus className="size-5 shrink-0 transition-transform duration-300 group-hover:rotate-12" aria-hidden="true" />
          Create Your First Agent
        </button>
      </section>
    )
  }

  return (
    <section className="immersive-command">
      <p className="immersive-command__eyebrow">Gobii Command Center</p>
      <h1 className="immersive-command__title">Your agents run here.</h1>
      <p className="immersive-command__subtitle">
        Jump into an agent chat to get started. We will expand this space with switching and ops controls next.
      </p>
      <button
        type="button"
        onClick={onCreateAgent}
        className="group mt-6 inline-flex items-center justify-center gap-x-2 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-3 font-semibold text-white shadow-lg transition-all duration-300 hover:from-blue-700 hover:to-indigo-700 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
      >
        <Plus className="size-5 shrink-0 transition-transform duration-300 group-hover:rotate-12" aria-hidden="true" />
        Create Agent
      </button>
    </section>
  )
}

function NotFound() {
  return (
    <section className="immersive-command">
      <p className="immersive-command__eyebrow">Not found</p>
      <h1 className="immersive-command__title">That workspace does not exist.</h1>
      <p className="immersive-command__subtitle">Head back to the command center and pick an agent.</p>
    </section>
  )
}

function navigateTo(path: string) {
  const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (currentPath === path) {
    window.history.replaceState({}, '', path)
    window.dispatchEvent(new PopStateEvent('popstate'))
    return
  }
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function isAppNavigationEvent(event: Event): event is CustomEvent<{ path: string }> {
  return event instanceof CustomEvent && typeof event.detail?.path === 'string'
}

function getSelectionPageForRoute(
  route: AppRoute,
  activeAgentShellPage: AgentShellPage | 'agents',
): SelectionShellPage {
  if (route.kind === 'agent-chat') {
    return activeAgentShellPage
  }
  if (isAgentShellRoute(route)) {
    return route.kind
  }
  return 'agents'
}

function getSelectionPanels({
  route,
  selectionPage,
  renderContext,
}: {
  route: AppRoute
  selectionPage: SelectionShellPage
  renderContext: Omit<AgentShellPageRenderContext, 'layout'>
}): {
  selectionShellPanel: ReactNode
  selectionMainPanel: ReactNode
} {
  if (!isAgentShellPage(selectionPage)) {
    return { selectionShellPanel: null, selectionMainPanel: null }
  }
  const config = AGENT_SHELL_PAGE_CONFIG[selectionPage]
  return {
    selectionShellPanel: config.render({ ...renderContext, layout: 'sidebar-shell' }),
    selectionMainPanel: isAgentShellRoute(route)
      ? config.render({ ...renderContext, layout: 'main' })
      : null,
  }
}

export function ImmersiveApp({
  maxChatUploadSizeBytes = null,
  pipedreamAppsSettingsUrl = null,
  pipedreamAppSearchUrl = null,
  nativeIntegrationsUrl = null,
}: ImmersiveAppProps) {
  const location = useAppLocation()
  const route = useMemo(() => parseRoute(location.pathname), [location.pathname])

  useEffect(() => {
    const handleAppNavigate = (event: Event) => {
      if (!isAppNavigationEvent(event)) {
        return
      }
      navigateTo(event.detail.path)
    }
    window.addEventListener(APP_NAVIGATE_EVENT, handleAppNavigate)
    return () => window.removeEventListener(APP_NAVIGATE_EVENT, handleAppNavigate)
  }, [])
  const activeAgentShellPage = useMemo(() => parseAgentShellPage(location.search), [location.search])
  const embed = useMemo(() => {
    if (parseBooleanFlag(new URLSearchParams(location.search).get('embed'))) {
      return true
    }
    if (typeof window === 'undefined') {
      return false
    }
    return window.parent !== window
  }, [location.search])
  const [returnTo, setReturnTo] = useState(() => resolveReturnTo(location.search))
  const [viewerUserId, setViewerUserId] = useState<number | null>(null)
  const [viewerEmail, setViewerEmail] = useState<string | null>(null)
  const [selectionRefreshKey, setSelectionRefreshKey] = useState(0)
  const hasSkippedInitialSegmentPage = useRef(false)
  const rosterQuery = useAgentRoster()
  const openUpgradeModal = useSubscriptionStore((state) => state.openUpgradeModal)
  const closeUpgradeModal = useSubscriptionStore((state) => state.closeUpgradeModal)
  const ensureAuthenticated = useSubscriptionStore((state) => state.ensureAuthenticated)
  const isUpgradeModalOpen = useSubscriptionStore((state) => state.isUpgradeModalOpen)
  const upgradeModalSource = useSubscriptionStore((state) => state.upgradeModalSource)
  const upgradeModalDismissible = useSubscriptionStore((state) => state.upgradeModalDismissible)
  const currentPlan = useSubscriptionStore((state) => state.currentPlan)
  const isProprietaryMode = useSubscriptionStore((state) => state.isProprietaryMode)
  const hasAgents = (rosterQuery.data?.agents?.length ?? 0) > 0

  useEffect(() => {
    setReturnTo(resolveReturnTo(location.search))
  }, [location.search])

  useEffect(() => {
    if (!hasUpgradeModalRequest(location.search)) {
      return
    }
    let shouldOpen = true

    const openRequestedUpgradeModal = async () => {
      const authenticated = await ensureAuthenticated()
      if (!shouldOpen || !authenticated) {
        return
      }
      openUpgradeModal('unknown')
      const nextPath = stripQueryParams(
        location.pathname,
        location.search,
        location.hash,
        [UPGRADE_MODAL_QUERY_PARAM],
      )
      window.history.replaceState({}, '', nextPath)
      window.dispatchEvent(new PopStateEvent('popstate'))
    }

    void openRequestedUpgradeModal()
    return () => {
      shouldOpen = false
    }
  }, [ensureAuthenticated, location.hash, location.pathname, location.search, openUpgradeModal])

  useEffect(() => {
    if (!embed || window.parent === window) {
      return
    }
    const cleanedPath = buildCleanPath(location.pathname, location.search)
    window.parent.postMessage(
      {
        type: 'gobii-immersive-path',
        path: cleanedPath,
      },
      window.location.origin,
    )
  }, [embed, location.pathname, location.search])

  useEffect(() => {
    const analyticsRoute = getAnalyticsRoute(route)
    const analyticsPath = `${getAnalyticsPath(route, location.pathname)}${cleanQueryForTracking(location.search)}`
    const analyticsUrl = `${window.location.origin}${analyticsPath}`
    const analyticsTitle = getAnalyticsTitle(route)

    window.gtag?.('event', 'page_view', {
      page_title: analyticsTitle,
      page_path: analyticsPath,
      page_location: analyticsUrl,
      app_route: analyticsRoute,
      app_embed: embed ? 'true' : 'false',
    })

    if (!hasSkippedInitialSegmentPage.current) {
      hasSkippedInitialSegmentPage.current = true
      return
    }

    window.analytics?.page('App', analyticsRoute, {
      path: analyticsPath,
      url: analyticsUrl,
      app_route: analyticsRoute,
      embed,
    })
  }, [route, location.pathname, location.search, embed])

  useEffect(() => {
    if (route.kind === 'agent-chat') {
      return () => undefined
    }
    const controller = new AbortController()
    void jsonFetch('/console/api/session/', { signal: controller.signal }).catch(() => undefined)
    return () => controller.abort()
  }, [route.kind])

  useEffect(() => {
    const controller = new AbortController()
    const loadViewer = async () => {
      try {
        const payload = await jsonFetch<ConsoleSessionPayload>('/console/api/session/', { signal: controller.signal })
        const raw = payload?.user_id ?? null
        const numeric = raw ? Number(raw) : null
        setViewerUserId(Number.isFinite(numeric) ? numeric : null)
        setViewerEmail(payload?.email ? payload.email : null)
      } catch (err) {
        if (controller.signal.aborted) {
          return
        }
        setViewerUserId(null)
        setViewerEmail(null)
      }
    }
    void loadViewer()
    return () => controller.abort()
  }, [])

  const handleClose = useCallback(() => {
    const destination = normalizeClosePath(returnTo)
    window.location.assign(destination)
  }, [returnTo])

  const handleEmbeddedClose = useCallback(() => {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'gobii-immersive-close' }, window.location.origin)
      return
    }
    handleClose()
  }, [handleClose])

  const handleNavigateToNewAgent = useCallback(() => {
    navigateTo('/app/agents/new')
  }, [])

  const handleAgentCreated = useCallback((agentId: string) => {
    navigateTo(`/app/agents/${agentId}`)
  }, [])

  const handleContextSwitch = useCallback((context: ConsoleContext) => {
    setSelectionRefreshKey((current) => current + 1)
    if (route.kind === 'agent-chat') {
      navigateTo(buildAgentSelectionPath(location.search))
      return
    }
    if (route.kind === 'profile' || route.kind === 'organization') {
      navigateTo(
        context.type === 'organization'
          ? AGENT_SHELL_PAGE_CONFIG.organization.path
          : AGENT_SHELL_PAGE_CONFIG.profile.path,
      )
      return
    }
    if (isAgentShellRoute(route)) {
      navigateTo(AGENT_SHELL_PAGE_CONFIG[route.kind].path)
      return
    }
    navigateTo('/app/agents')
  }, [location.search, route])

  const navigateToShellPage = useCallback((page: AgentShellPage) => {
    if (route.kind === 'agent-chat' && route.agentId) {
      navigateTo(buildAgentShellPath(route.agentId, page, location.search))
      return
    }
    navigateTo(AGENT_SHELL_PAGE_CONFIG[page].path)
  }, [location.search, route])

  const handleSelectionPageChange = useCallback((page: SelectionShellPage) => {
    if (route.kind === 'agent-chat' && route.agentId) {
      navigateTo(buildAgentShellPath(route.agentId, page, location.search))
      return
    }
    if (isAgentShellPage(page)) {
      navigateToShellPage(page)
      return
    }
    navigateTo('/app/agents')
  }, [location.search, navigateToShellPage, route])

  const handleOpenBilling = useCallback(() => navigateToShellPage('billing'), [navigateToShellPage])
  const handleOpenProfile = useCallback(() => navigateToShellPage('profile'), [navigateToShellPage])
  const handleOpenOrganization = useCallback(() => navigateToShellPage('organization'), [navigateToShellPage])
  const handleOpenSecrets = useCallback(() => navigateToShellPage('secrets'), [navigateToShellPage])
  const handleOpenUsage = useCallback(() => navigateToShellPage('usage'), [navigateToShellPage])
  const handleOpenIntegrations = useCallback(() => navigateToShellPage('integrations'), [navigateToShellPage])
  const handleOpenApiKeys = useCallback(() => navigateToShellPage('api-keys'), [navigateToShellPage])

  const handleUpgradeModalDismiss = useCallback(() => {
    if (!upgradeModalDismissible) {
      return
    }
    track(AnalyticsEvent.UPGRADE_MODAL_DISMISSED, {
      currentPlan,
      source: upgradeModalSource ?? 'unknown',
    })
    closeUpgradeModal()
  }, [closeUpgradeModal, currentPlan, upgradeModalDismissible, upgradeModalSource])

  const handleUpgradeSelection = useCallback(async (plan: PlanTier) => {
    const source = upgradeModalSource ?? 'unknown'
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    track(AnalyticsEvent.UPGRADE_CHECKOUT_REDIRECTED, {
      plan,
      source,
    })
    closeUpgradeModal()
    const checkoutPath = plan === 'startup' ? '/subscribe/startup/' : '/subscribe/scale/'
    window.open(appendReturnTo(checkoutPath), '_top')
  }, [closeUpgradeModal, ensureAuthenticated, upgradeModalSource])

  const showShellUpgradeModal = (
    route.kind !== 'agent-chat'
    && route.kind !== 'billing'
    && isUpgradeModalOpen
    && isProprietaryMode
  )
  const selectionPage = getSelectionPageForRoute(route, activeAgentShellPage)
  const shellPanelRenderContext = useMemo(() => ({
    refreshKey: selectionRefreshKey,
    pipedreamAppsSettingsUrl,
    pipedreamAppSearchUrl,
    nativeIntegrationsUrl,
  }), [nativeIntegrationsUrl, pipedreamAppSearchUrl, pipedreamAppsSettingsUrl, selectionRefreshKey])
  const { selectionShellPanel, selectionMainPanel } = useMemo(
    () => getSelectionPanels({ route, selectionPage, renderContext: shellPanelRenderContext }),
    [route, selectionPage, shellPanelRenderContext],
  )
  const shouldRenderAgentChatPage = route.kind === 'agent-chat' || route.kind === 'agent-select' || isAgentShellRoute(route)
  const baseAgentChatPageProps = {
    maxChatUploadSizeBytes,
    viewerUserId,
    viewerEmail,
    pipedreamAppsSettingsUrl,
    pipedreamAppSearchUrl,
    nativeIntegrationsUrl,
    onClose: embed ? handleEmbeddedClose : handleClose,
    onCreateAgent: handleNavigateToNewAgent,
    onAgentCreated: handleAgentCreated,
    showContextSwitcher: true,
    onContextSwitch: handleContextSwitch,
    selectionPage,
    selectionShellPanel,
    selectionMainPanel,
    onSelectionPageChange: handleSelectionPageChange,
    onOpenBilling: handleOpenBilling,
    onOpenUsage: handleOpenUsage,
    onOpenProfile: handleOpenProfile,
    onOpenOrganization: handleOpenOrganization,
    onOpenSecrets: handleOpenSecrets,
    onOpenIntegrations: handleOpenIntegrations,
    onOpenApiKeys: handleOpenApiKeys,
  }

  return (
    <div className="immersive-shell">
      <div className="immersive-shell__content">
        {shouldRenderAgentChatPage ? (
          <AgentChatPage
            agentId={route.kind === 'agent-chat' ? route.agentId : undefined}
            appLocationSearch={location.search}
            {...baseAgentChatPageProps}
          />
        ) : null}
        {route.kind === 'organization-invite-accept' ? (
          <OrganizationInviteAcceptPage token={route.token} onNavigate={navigateTo} />
        ) : null}
        {route.kind === 'agent-collaborator-invite' ? (
          <AgentCollaboratorInviteResponsePage
            token={route.token}
            action={route.action}
            onNavigate={navigateTo}
          />
        ) : null}
        {route.kind === 'command-center' ? (
          <CommandCenter
            hasAgents={hasAgents}
            isLoading={rosterQuery.isLoading}
            onCreateAgent={handleNavigateToNewAgent}
          />
        ) : null}
        {route.kind === 'not-found' ? <NotFound /> : null}
      </div>
      {showShellUpgradeModal ? (
        <SubscriptionUpgradeModal
          currentPlan={currentPlan}
          onClose={handleUpgradeModalDismiss}
          onUpgrade={handleUpgradeSelection}
          source={upgradeModalSource ?? undefined}
          dismissible={upgradeModalDismissible}
        />
      ) : null}
    </div>
  )
}

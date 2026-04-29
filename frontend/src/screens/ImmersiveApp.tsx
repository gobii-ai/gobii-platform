import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Plus, Zap } from 'lucide-react'
import type { ConsoleContext } from '../api/context'
import { jsonFetch } from '../api/http'
import { useAgentRoster } from '../hooks/useAgentRoster'
import { AgentChatPage } from './AgentChatPage'
import { ImmersiveBillingPage } from './billing/ImmersiveBillingPage'
import '../styles/immersiveApp.css'

const APP_BASE = '/app'
const RETURN_TO_STORAGE_KEY = 'gobii:immersive:return_to'
const DEFAULT_CLOSE_PATH = '/console/agents/'

type AppRoute =
  | { kind: 'command-center' }
  | { kind: 'agent-select' }
  | { kind: 'billing' }
  | { kind: 'agent-chat'; agentId: string | null }
  | { kind: 'not-found' }

type AppAnalyticsRoute = 'command_center' | 'agent_select' | 'billing' | 'agent_new' | 'agent_chat' | 'not_found'

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

  if (parts[0] === 'agents' && parts[1]) {
    return { kind: 'agent-chat', agentId: parts[1] }
  }

  if (parts[0] === 'agents') {
    return { kind: 'agent-select' }
  }

  if (parts[0] === 'billing') {
    return { kind: 'billing' }
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

function parseBooleanFlag(value: string | null): boolean {
  if (!value) {
    return false
  }
  return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase())
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

export function ImmersiveApp({
  maxChatUploadSizeBytes = null,
  pipedreamAppsSettingsUrl = null,
  pipedreamAppSearchUrl = null,
}: ImmersiveAppProps) {
  const location = useAppLocation()
  const route = useMemo(() => parseRoute(location.pathname), [location.pathname])
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
  const hasAgents = (rosterQuery.data?.agents?.length ?? 0) > 0

  useEffect(() => {
    setReturnTo(resolveReturnTo(location.search))
  }, [location.search])

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

  const handleContextSwitch = useCallback((_context: ConsoleContext) => {
    setSelectionRefreshKey((current) => current + 1)
    if (route.kind === 'billing') {
      navigateTo('/app/billing')
      return
    }
    navigateTo('/app/agents')
  }, [route.kind])

  const handleSelectionPageChange = useCallback((page: 'agents' | 'billing') => {
    navigateTo(page === 'billing' ? '/app/billing' : '/app/agents')
  }, [])

  const handleOpenBilling = useCallback(() => {
    navigateTo('/app/billing')
  }, [])

  return (
    <div className="immersive-shell">
      <div className="immersive-shell__content">
        {route.kind === 'agent-chat' ? (
          <AgentChatPage
            agentId={route.agentId}
            maxChatUploadSizeBytes={maxChatUploadSizeBytes}
            viewerUserId={viewerUserId}
            viewerEmail={viewerEmail}
            pipedreamAppsSettingsUrl={pipedreamAppsSettingsUrl}
            pipedreamAppSearchUrl={pipedreamAppSearchUrl}
            onClose={embed ? handleEmbeddedClose : handleClose}
            onCreateAgent={handleNavigateToNewAgent}
            onAgentCreated={handleAgentCreated}
            showContextSwitcher
            persistContextSession={false}
            onContextSwitch={handleContextSwitch}
            selectionPage="agents"
            onSelectionPageChange={handleSelectionPageChange}
            onOpenBilling={handleOpenBilling}
          />
        ) : null}
        {route.kind === 'agent-select' ? (
          <AgentChatPage
            maxChatUploadSizeBytes={maxChatUploadSizeBytes}
            viewerUserId={viewerUserId}
            viewerEmail={viewerEmail}
            pipedreamAppsSettingsUrl={pipedreamAppsSettingsUrl}
            pipedreamAppSearchUrl={pipedreamAppSearchUrl}
            onClose={embed ? handleEmbeddedClose : handleClose}
            onCreateAgent={handleNavigateToNewAgent}
            onAgentCreated={handleAgentCreated}
            showContextSwitcher
            persistContextSession={false}
            onContextSwitch={handleContextSwitch}
            selectionPage="agents"
            onSelectionPageChange={handleSelectionPageChange}
            onOpenBilling={handleOpenBilling}
          />
        ) : null}
        {route.kind === 'billing' ? (
          <AgentChatPage
            maxChatUploadSizeBytes={maxChatUploadSizeBytes}
            viewerUserId={viewerUserId}
            viewerEmail={viewerEmail}
            pipedreamAppsSettingsUrl={pipedreamAppsSettingsUrl}
            pipedreamAppSearchUrl={pipedreamAppSearchUrl}
            onClose={embed ? handleEmbeddedClose : handleClose}
            onCreateAgent={handleNavigateToNewAgent}
            onAgentCreated={handleAgentCreated}
            showContextSwitcher
            persistContextSession={false}
            onContextSwitch={handleContextSwitch}
            selectionPage="billing"
            selectionShellPanel={<ImmersiveBillingPage layout="sidebar-shell" refreshKey={selectionRefreshKey} />}
            onSelectionPageChange={handleSelectionPageChange}
            onOpenBilling={handleOpenBilling}
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
    </div>
  )
}

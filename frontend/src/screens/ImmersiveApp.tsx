import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, Zap } from 'lucide-react'
import type { ConsoleContext } from '../api/context'
import { jsonFetch } from '../api/http'
import { useAgentRoster } from '../hooks/useAgentRoster'
import { AgentChatPage } from './AgentChatPage'
import '../styles/immersiveApp.css'

const APP_BASE = '/app'
const RETURN_TO_STORAGE_KEY = 'gobii:immersive:return_to'

type AppRoute =
  | { kind: 'command-center' }
  | { kind: 'agent-select' }
  | { kind: 'agent-chat'; agentId: string | null }
  | { kind: 'not-found' }

type LocationSnapshot = {
  pathname: string
  search: string
  hash: string
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

  return { kind: 'not-found' }
}

function parseBooleanFlag(value: string | null): boolean {
  if (!value) {
    return false
  }
  return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase())
}

function buildCleanPath(pathname: string, search: string): string {
  const params = new URLSearchParams(search)
  params.delete('embed')
  params.delete('return_to')
  const cleaned = params.toString()
  return cleaned ? `${pathname}?${cleaned}` : pathname
}

function sanitizeReturnTo(value: string | null): string | null {
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

function readReturnToFromSearch(search: string): string | null {
  const params = new URLSearchParams(search)
  return sanitizeReturnTo(params.get('return_to'))
}

function resolveReturnTo(search: string): string {
  const fromQuery = readReturnToFromSearch(search)
  if (fromQuery) {
    sessionStorage.setItem(RETURN_TO_STORAGE_KEY, fromQuery)
    return fromQuery
  }

  const stored = sanitizeReturnTo(sessionStorage.getItem(RETURN_TO_STORAGE_KEY))
  if (stored) {
    return stored
  }

  const fromReferrer = sanitizeReturnTo(document.referrer)
  if (fromReferrer) {
    return fromReferrer
  }

  return '/'
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
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function ImmersiveApp() {
  const location = useAppLocation()
  const route = useMemo(() => parseRoute(location.pathname), [location.pathname])
  const embed = useMemo(() => parseBooleanFlag(new URLSearchParams(location.search).get('embed')), [location.search])
  const [returnTo, setReturnTo] = useState(() => resolveReturnTo(location.search))
  const rosterQuery = useAgentRoster()
  const hasAgents = (rosterQuery.data?.length ?? 0) > 0

  useEffect(() => {
    const fromQuery = readReturnToFromSearch(location.search)
    if (fromQuery) {
      sessionStorage.setItem(RETURN_TO_STORAGE_KEY, fromQuery)
      setReturnTo(fromQuery)
    }
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
    if (route.kind === 'agent-chat') {
      return () => undefined
    }
    const controller = new AbortController()
    void jsonFetch('/console/api/session/', { signal: controller.signal }).catch(() => undefined)
    return () => controller.abort()
  }, [route.kind])

  const handleClose = useCallback(() => {
    window.location.assign(returnTo)
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
    navigateTo('/app/agents')
  }, [])

  return (
    <div className="immersive-shell">
      <div className="immersive-shell__content">
        {route.kind === 'agent-chat' ? (
          <AgentChatPage
            agentId={route.agentId}
            onClose={embed ? handleEmbeddedClose : handleClose}
            onCreateAgent={handleNavigateToNewAgent}
            onAgentCreated={handleAgentCreated}
            showContextSwitcher
            persistContextSession={false}
            onContextSwitch={handleContextSwitch}
          />
        ) : null}
        {route.kind === 'agent-select' ? (
          <AgentChatPage
            onClose={embed ? handleEmbeddedClose : handleClose}
            onCreateAgent={handleNavigateToNewAgent}
            onAgentCreated={handleAgentCreated}
            showContextSwitcher
            persistContextSession={false}
            onContextSwitch={handleContextSwitch}
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

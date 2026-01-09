import { useEffect, useMemo, useState } from 'react'
import { jsonFetch } from '../api/http'
import { AgentChatPage } from './AgentChatPage'
import '../styles/immersiveApp.css'

const APP_BASE = '/app'
const RETURN_TO_STORAGE_KEY = 'gobii:immersive:return_to'

type AppRoute =
  | { kind: 'command-center' }
  | { kind: 'agent-chat'; agentId: string }
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
  if (parts[0] === 'agents' && parts[1]) {
    return { kind: 'agent-chat', agentId: parts[1] }
  }

  if (parts[0] === 'agents') {
    return { kind: 'command-center' }
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

function CommandCenter() {
  return (
    <section className="immersive-command">
      <p className="immersive-command__eyebrow">Gobii Command Center</p>
      <h1 className="immersive-command__title">Your agents run here.</h1>
      <p className="immersive-command__subtitle">
        Jump into an agent chat to get started. We will expand this space with switching and ops controls next.
      </p>
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

export function ImmersiveApp() {
  const location = useAppLocation()
  const route = useMemo(() => parseRoute(location.pathname), [location.pathname])
  const embed = useMemo(() => parseBooleanFlag(new URLSearchParams(location.search).get('embed')), [location.search])
  const [returnTo, setReturnTo] = useState(() => resolveReturnTo(location.search))

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

  const handleClose = () => window.location.assign(returnTo)

  return (
    <div className="immersive-shell">
      <div className="immersive-shell__content">
        {route.kind === 'agent-chat' ? (
          <AgentChatPage agentId={route.agentId} onClose={!embed ? handleClose : undefined} />
        ) : null}
        {route.kind === 'command-center' ? <CommandCenter /> : null}
        {route.kind === 'not-found' ? <NotFound /> : null}
      </div>
    </div>
  )
}

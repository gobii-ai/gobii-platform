import { useCallback, useEffect, useRef, useState } from 'react'

import {
  heartbeatAgentWebSession,
  startAgentWebSession,
  endAgentWebSession,
  type AgentWebSessionSnapshot,
} from '../api/agentChat'
import { HttpError } from '../api/http'
import { usePageLifecycle, type PageLifecycleSuspendReason } from './usePageLifecycle'

const MIN_HEARTBEAT_INTERVAL_MS = 15_000
const START_RETRY_BASE_DELAY_MS = 2_000
const START_RETRY_MAX_DELAY_MS = 60_000
const RESUME_THROTTLE_MS = 4000
const SESSION_IDLE_TIMEOUT_MS = 60_000

type WebSessionStatus = 'idle' | 'starting' | 'active' | 'error'

function describeError(error: unknown): string {
  if (error instanceof HttpError) {
    if (typeof error.body === 'string' && error.body) {
      return error.body
    }
    if (error.body && typeof error.body === 'object' && 'error' in error.body) {
      const { error: bodyError } = error.body as { error?: unknown }
      if (typeof bodyError === 'string' && bodyError) {
        return bodyError
      }
    }
    return `${error.status} ${error.statusText}`
  }
  if (error instanceof TypeError) {
    return 'Network connection lost. Retrying…'
  }
  if (error instanceof Error && error.name === 'AbortError') {
    return 'Request was interrupted. Retrying…'
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'Web session error'
}

function shouldRetry(error: unknown): boolean {
  if (error instanceof HttpError) {
    if (error.status >= 500) {
      return true
    }
    return [408, 425, 429].includes(error.status)
  }
  if (error instanceof Error) {
    if (error.name === 'AbortError') {
      return true
    }
    // TypeError is raised by fetch for generic network failures in most browsers.
    return error instanceof TypeError
  }
  return false
}

function requireValidTtlSeconds(snapshot: AgentWebSessionSnapshot): number {
  const ttl = snapshot.ttl_seconds
  if (typeof ttl !== 'number' || !Number.isFinite(ttl) || ttl <= 0) {
    throw new Error('Web session expired. Please refresh the page.')
  }
  return ttl
}

function isPageActive(): boolean {
  if (typeof document === 'undefined') {
    return true
  }
  if (document.visibilityState !== 'visible') {
    return false
  }
  return true
}

export function useAgentWebSession(agentId: string | null) {
  const [session, setSession] = useState<AgentWebSessionSnapshot | null>(null)
  const [status, setStatus] = useState<WebSessionStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const heartbeatTimerRef = useRef<number | null>(null)
  const startRetryTimerRef = useRef<number | null>(null)
  const idleTimerRef = useRef<number | null>(null)
  const snapshotRef = useRef<AgentWebSessionSnapshot | null>(null)
  const agentIdRef = useRef<string | null>(agentId)
  const unmountedRef = useRef(false)
  const startRetryAttemptsRef = useRef(0)
  const requestIdRef = useRef(0)
  const idleTriggeredRef = useRef(false)

  useEffect(() => {
    agentIdRef.current = agentId
  }, [agentId])

  useEffect(() => {
    snapshotRef.current = session
  }, [session])

  useEffect(() => {
    unmountedRef.current = false
    return () => {
      unmountedRef.current = true
    }
  }, [])

  const clearHeartbeat = useCallback(() => {
    if (heartbeatTimerRef.current !== null) {
      window.clearTimeout(heartbeatTimerRef.current)
      heartbeatTimerRef.current = null
    }
  }, [])

  const clearStartRetry = useCallback(() => {
    if (startRetryTimerRef.current !== null) {
      window.clearTimeout(startRetryTimerRef.current)
      startRetryTimerRef.current = null
    }
  }, [])

  const clearIdleTimeout = useCallback(() => {
    if (idleTimerRef.current !== null) {
      window.clearTimeout(idleTimerRef.current)
      idleTimerRef.current = null
    }
    idleTriggeredRef.current = false
  }, [])

  const performHeartbeatRef = useRef<() => Promise<void>>(async () => {})
  const performStartRef = useRef<() => Promise<void>>(async () => {})

  const scheduleNextHeartbeat = useCallback((ttlSeconds: number) => {
    const interval = Math.max(MIN_HEARTBEAT_INTERVAL_MS, Math.floor(ttlSeconds * 1000 * 0.5))
    clearHeartbeat()
    heartbeatTimerRef.current = window.setTimeout(() => {
      void performHeartbeatRef.current()
    }, interval)
  }, [clearHeartbeat])

  const scheduleStartRetry = useCallback(
    (delayMs: number) => {
      clearStartRetry()
      startRetryTimerRef.current = window.setTimeout(() => {
        void performStartRef.current()
      }, delayMs)
    },
    [clearStartRetry],
  )

  const scheduleIdleTimeout = useCallback(() => {
    if (idleTimerRef.current !== null) {
      return
    }
    idleTimerRef.current = window.setTimeout(() => {
      idleTimerRef.current = null
      if (isPageActive()) {
        return
      }
      idleTriggeredRef.current = true
      requestIdRef.current += 1
      clearHeartbeat()
      clearStartRetry()
      const currentAgentId = agentIdRef.current
      const snapshot = snapshotRef.current
      snapshotRef.current = null
      setSession(null)
      setStatus('idle')
      setError(null)
      if (currentAgentId && snapshot) {
        void endAgentWebSession(currentAgentId, snapshot.session_key, { keepalive: true }).catch(() => undefined)
      }
    }, SESSION_IDLE_TIMEOUT_MS)
  }, [clearHeartbeat, clearStartRetry])

  const performStart = useCallback(async () => {
    const currentAgentId = agentIdRef.current
    if (!currentAgentId) {
      return
    }

    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    setStatus('starting')

    try {
      const created = await startAgentWebSession(currentAgentId)
      if (unmountedRef.current || requestId !== requestIdRef.current || agentIdRef.current !== currentAgentId) {
        return
      }

      startRetryAttemptsRef.current = 0
      clearStartRetry()

      const ttlSeconds = requireValidTtlSeconds(created)
      setSession(created)
      setStatus('active')
      setError(null)
      clearIdleTimeout()
      scheduleNextHeartbeat(ttlSeconds)
    } catch (startError) {
      if (unmountedRef.current || requestId !== requestIdRef.current || agentIdRef.current !== currentAgentId) {
        return
      }

      const message = describeError(startError)

      if (shouldRetry(startError)) {
        startRetryAttemptsRef.current += 1
        const attempt = startRetryAttemptsRef.current
        const delay = Math.min(
          START_RETRY_BASE_DELAY_MS * 2 ** Math.max(0, attempt - 1),
          START_RETRY_MAX_DELAY_MS,
        )
        setError(message)
        scheduleStartRetry(delay)
        return
      }

      setStatus('error')
      setError(message)
      clearStartRetry()
      clearHeartbeat()
    }
  }, [clearHeartbeat, clearIdleTimeout, clearStartRetry, scheduleNextHeartbeat, scheduleStartRetry])

  const performHeartbeat = useCallback(async () => {
    const currentAgentId = agentIdRef.current
    const snapshot = snapshotRef.current
    if (!currentAgentId || !snapshot) {
      return
    }

    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId

    try {
      const next = await heartbeatAgentWebSession(currentAgentId, snapshot.session_key)
      if (unmountedRef.current || requestId !== requestIdRef.current || agentIdRef.current !== currentAgentId) {
        return
      }
      startRetryAttemptsRef.current = 0
      setStatus('active')
      setError(null)
      const ttlSeconds = requireValidTtlSeconds(next)
      setSession(next)
      clearIdleTimeout()
      scheduleNextHeartbeat(ttlSeconds)
    } catch (heartbeatError) {
      if (unmountedRef.current || requestId !== requestIdRef.current || agentIdRef.current !== currentAgentId) {
        return
      }

      clearHeartbeat()

      if (heartbeatError instanceof HttpError && heartbeatError.status === 400) {
        startRetryAttemptsRef.current = 0
        await performStartRef.current()
        return
      }

      const message = describeError(heartbeatError)

      if (shouldRetry(heartbeatError)) {
        startRetryAttemptsRef.current = 0
        setStatus('starting')
        setError(message)
        scheduleStartRetry(START_RETRY_BASE_DELAY_MS)
        return
      }

      setStatus('error')
      setError(message)
      clearStartRetry()
    }
  }, [clearHeartbeat, clearIdleTimeout, clearStartRetry, scheduleNextHeartbeat, scheduleStartRetry])

  useEffect(() => {
    performHeartbeatRef.current = performHeartbeat
  }, [performHeartbeat])

  useEffect(() => {
    performStartRef.current = performStart
  }, [performStart])

  useEffect(() => {
    if (!agentId) {
      clearHeartbeat()
      clearStartRetry()
      clearIdleTimeout()
      requestIdRef.current += 1
      setSession(null)
      setStatus('idle')
      setError(null)
      snapshotRef.current = null
      idleTriggeredRef.current = false
      return
    }

    setError(null)
    setSession(null)
    snapshotRef.current = null
    clearIdleTimeout()
    idleTriggeredRef.current = false

    startRetryAttemptsRef.current = 0
    void performStart()

    return () => {
      clearHeartbeat()
      clearStartRetry()
      clearIdleTimeout()
      const previous = snapshotRef.current
      snapshotRef.current = null
      if (previous) {
        void endAgentWebSession(agentId, previous.session_key, { keepalive: true }).catch(() => undefined)
      }
    }
  }, [agentId, clearHeartbeat, clearIdleTimeout, clearStartRetry, performStart])

  useEffect(() => {
    if (!agentId) {
      return
    }

    const handleBeforeUnload = () => {
      const currentAgentId = agentIdRef.current
      const snapshot = snapshotRef.current
      if (!currentAgentId || !snapshot) {
        return
      }
      const url = `${window.location.origin}/console/api/agents/${currentAgentId}/web-sessions/end/`
      const payload = JSON.stringify({ session_key: snapshot.session_key })
      if (navigator.sendBeacon) {
        const blob = new Blob([payload], { type: 'application/json' })
        navigator.sendBeacon(url, blob)
      } else {
        void fetch(url, {
          method: 'POST',
          body: payload,
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          keepalive: true,
        })
      }
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    window.addEventListener('pagehide', handleBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
      window.removeEventListener('pagehide', handleBeforeUnload)
    }
  }, [agentId])

  const requestResume = useCallback(() => {
    clearIdleTimeout()
    idleTriggeredRef.current = false
    if (!agentIdRef.current) {
      return
    }
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return
    }
    if (!isPageActive()) {
      return
    }
    if (snapshotRef.current) {
      void performHeartbeatRef.current()
      return
    }
    void performStartRef.current()
  }, [clearIdleTimeout])

  const handleSuspend = useCallback((reason: PageLifecycleSuspendReason) => {
    clearHeartbeat()
    clearStartRetry()
    if (reason !== 'offline') {
      scheduleIdleTimeout()
    }
  }, [clearHeartbeat, clearStartRetry, scheduleIdleTimeout])

  usePageLifecycle(
    {
      onResume: requestResume,
      onSuspend: handleSuspend,
    },
    { resumeThrottleMs: RESUME_THROTTLE_MS },
  )

  return {
    session,
    status,
    error,
  }
}

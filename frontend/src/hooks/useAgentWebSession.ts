import { useCallback, useEffect, useRef, useState } from 'react'

import {
  heartbeatAgentWebSession,
  startAgentWebSession,
  endAgentWebSession,
  type AgentWebSessionSnapshot,
} from '../api/agentChat'
import { HttpError } from '../api/http'

const MIN_HEARTBEAT_INTERVAL_MS = 15_000
const START_RETRY_BASE_DELAY_MS = 2_000
const START_RETRY_MAX_DELAY_MS = 60_000

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

export function useAgentWebSession(agentId: string | null) {
  const [session, setSession] = useState<AgentWebSessionSnapshot | null>(null)
  const [status, setStatus] = useState<WebSessionStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const heartbeatTimerRef = useRef<number | null>(null)
  const startRetryTimerRef = useRef<number | null>(null)
  const snapshotRef = useRef<AgentWebSessionSnapshot | null>(null)
  const agentIdRef = useRef<string | null>(agentId)
  const unmountedRef = useRef(false)
  const startRetryAttemptsRef = useRef(0)

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

  const performStart = useCallback(async () => {
    const currentAgentId = agentIdRef.current
    if (!currentAgentId) {
      return
    }

    setStatus('starting')

    try {
      const created = await startAgentWebSession(currentAgentId)
      if (unmountedRef.current) {
        return
      }

      startRetryAttemptsRef.current = 0
      clearStartRetry()

      const ttlSeconds = requireValidTtlSeconds(created)
      setSession(created)
      setStatus('active')
      setError(null)
      scheduleNextHeartbeat(ttlSeconds)
    } catch (startError) {
      if (unmountedRef.current) {
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
  }, [clearHeartbeat, clearStartRetry, scheduleNextHeartbeat, scheduleStartRetry])

  const performHeartbeat = useCallback(async () => {
    const currentAgentId = agentIdRef.current
    const snapshot = snapshotRef.current
    if (!currentAgentId || !snapshot) {
      return
    }

    try {
      const next = await heartbeatAgentWebSession(currentAgentId, snapshot.session_key)
      if (unmountedRef.current) {
        return
      }
      startRetryAttemptsRef.current = 0
      setStatus('active')
      setError(null)
      const ttlSeconds = requireValidTtlSeconds(next)
      setSession(next)
      scheduleNextHeartbeat(ttlSeconds)
    } catch (heartbeatError) {
      if (unmountedRef.current) {
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
  }, [clearHeartbeat, clearStartRetry, scheduleNextHeartbeat, scheduleStartRetry])

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
      setSession(null)
      setStatus('idle')
      setError(null)
      snapshotRef.current = null
      return
    }

    setError(null)
    setSession(null)
    snapshotRef.current = null

    startRetryAttemptsRef.current = 0
    void performStart()

    return () => {
      clearHeartbeat()
      clearStartRetry()
      const previous = snapshotRef.current
      snapshotRef.current = null
      if (previous) {
        void endAgentWebSession(agentId, previous.session_key, { keepalive: true }).catch(() => undefined)
      }
    }
  }, [agentId, clearHeartbeat, clearStartRetry, performStart])

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
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [agentId])

  return {
    session,
    status,
    error,
  }
}

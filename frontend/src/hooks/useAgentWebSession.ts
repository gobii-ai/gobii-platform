import { useCallback, useEffect, useRef, useState } from 'react'

import {
  heartbeatAgentWebSession,
  startAgentWebSession,
  endAgentWebSession,
  type AgentWebSessionSnapshot,
} from '../api/agentChat'
import { HttpError } from '../api/http'

const MIN_HEARTBEAT_INTERVAL_MS = 15_000

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
  if (error instanceof Error) {
    return error.message
  }
  return 'Web session error'
}

export function useAgentWebSession(agentId: string | null) {
  const [session, setSession] = useState<AgentWebSessionSnapshot | null>(null)
  const [status, setStatus] = useState<WebSessionStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  const heartbeatTimerRef = useRef<number | null>(null)
  const snapshotRef = useRef<AgentWebSessionSnapshot | null>(null)
  const agentIdRef = useRef<string | null>(agentId)
  const unmountedRef = useRef(false)

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

  const performHeartbeatRef = useRef<() => Promise<void>>(async () => {})

  const scheduleNextHeartbeat = useCallback((ttlSeconds: number) => {
    const interval = Math.max(MIN_HEARTBEAT_INTERVAL_MS, Math.floor(ttlSeconds * 1000 * 0.5))
    clearHeartbeat()
    heartbeatTimerRef.current = window.setTimeout(() => {
      void performHeartbeatRef.current()
    }, interval)
  }, [clearHeartbeat])

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
      setStatus('active')
      setError(null)
      setSession(next)
      scheduleNextHeartbeat(next.ttl_seconds)
    } catch (heartbeatError) {
      if (unmountedRef.current) {
        return
      }

      let message = describeError(heartbeatError)

      if (heartbeatError instanceof HttpError && heartbeatError.status === 400) {
        try {
          const restarted = await startAgentWebSession(currentAgentId)
          if (unmountedRef.current) {
            return
          }
          setSession(restarted)
          setStatus('active')
          setError(null)
          scheduleNextHeartbeat(restarted.ttl_seconds)
          return
        } catch (restartError) {
          message = describeError(restartError)
        }
      }

      setStatus('error')
      setError(message)
      clearHeartbeat()
    }
  }, [clearHeartbeat, scheduleNextHeartbeat])

  useEffect(() => {
    performHeartbeatRef.current = performHeartbeat
  }, [performHeartbeat])

  useEffect(() => {
    if (!agentId) {
      clearHeartbeat()
      setSession(null)
      setStatus('idle')
      setError(null)
      snapshotRef.current = null
      return
    }

    let cancelled = false
    setStatus('starting')
    setError(null)
    setSession(null)
    snapshotRef.current = null

    startAgentWebSession(agentId)
      .then((created) => {
        if (cancelled || unmountedRef.current) {
          return
        }
        setSession(created)
        setStatus('active')
        setError(null)
        scheduleNextHeartbeat(created.ttl_seconds)
      })
      .catch((startError) => {
        if (cancelled || unmountedRef.current) {
          return
        }
        setStatus('error')
        setError(describeError(startError))
      })

    return () => {
      cancelled = true
      clearHeartbeat()
      const previous = snapshotRef.current
      snapshotRef.current = null
      if (previous) {
        void endAgentWebSession(agentId, previous.session_key, { keepalive: true }).catch(() => undefined)
      }
    }
  }, [agentId, clearHeartbeat, scheduleNextHeartbeat])

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

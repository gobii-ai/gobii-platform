import { useCallback, useEffect, useRef, useState } from 'react'

import { scheduleLoginRedirect } from '../api/http'
import type { ProcessingSnapshot, TimelineEvent } from '../types/agentChat'
import { useAgentChatStore } from '../stores/agentChatStore'
import { usePageLifecycle, type PageLifecycleResumeReason, type PageLifecycleSuspendReason } from './usePageLifecycle'
import { readStoredConsoleContext } from '../util/consoleContextStorage'

const RECONNECT_BASE_DELAY_MS = 1000
const RECONNECT_MAX_DELAY_MS = 15000
const RESYNC_THROTTLE_MS = 4000
const BACKGROUND_SYNC_INTERVAL_MS = 30000
const PING_INTERVAL_MS = 20000
const PONG_TIMEOUT_MS = 8000
const SOCKET_IDLE_TIMEOUT_MS = 60000

export type AgentChatSocketStatus = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'offline' | 'error'

export type AgentChatSocketSnapshot = {
  status: AgentChatSocketStatus
  lastConnectedAt: number | null
  lastError: string | null
}

function describeCloseEvent(event: CloseEvent): string | null {
  if (event.code === 1000) {
    return null
  }
  if (event.code === 4401) {
    return 'Authentication required.'
  }
  if (event.reason) {
    return event.reason
  }
  return `WebSocket closed (code ${event.code}).`
}

function isAuthErrorMessage(message: string | null | undefined): boolean {
  if (!message) {
    return false
  }
  const normalized = message.toLowerCase()
  return normalized.includes('authentication') || normalized.includes('sign in') || normalized.includes('login')
}

function computeReconnectDelay(attempt: number): number {
  const exponent = Math.min(attempt, 6)
  const base = Math.min(RECONNECT_MAX_DELAY_MS, RECONNECT_BASE_DELAY_MS * 2 ** exponent)
  const jitter = Math.round(base * 0.2 * Math.random())
  return base + jitter
}

function isPageActive(): boolean {
  if (typeof document === 'undefined') {
    return true
  }
  if (document.visibilityState !== 'visible') {
    return false
  }
  if (typeof document.hasFocus === 'function') {
    return document.hasFocus()
  }
  return true
}

export function useAgentChatSocket(agentId: string | null): AgentChatSocketSnapshot {
  const receiveEventRef = useRef(useAgentChatStore.getState().receiveRealtimeEvent)
  const updateProcessingRef = useRef(useAgentChatStore.getState().updateProcessing)
  const receiveStreamRef = useRef(useAgentChatStore.getState().receiveStreamEvent)
  const refreshLatestRef = useRef(useAgentChatStore.getState().refreshLatest)
  const refreshProcessingRef = useRef(useAgentChatStore.getState().refreshProcessing)

  useEffect(() =>
    useAgentChatStore.subscribe((state) => {
      receiveEventRef.current = state.receiveRealtimeEvent
      updateProcessingRef.current = state.updateProcessing
      receiveStreamRef.current = state.receiveStreamEvent
      refreshLatestRef.current = state.refreshLatest
      refreshProcessingRef.current = state.refreshProcessing
    }),
  [])

  const retryRef = useRef(0)
  const socketRef = useRef<WebSocket | null>(null)
  const timeoutRef = useRef<number | null>(null)
  const syncIntervalRef = useRef<number | null>(null)
  const pingIntervalRef = useRef<number | null>(null)
  const pongTimeoutRef = useRef<number | null>(null)
  const idleTimeoutRef = useRef<number | null>(null)
  const scheduleConnectRef = useRef<(delay: number) => void>(() => undefined)
  const closeSocketRef = useRef<() => void>(() => undefined)
  const closingSocketRef = useRef<WebSocket | null>(null)
  const pauseReasonRef = useRef<'offline' | 'hidden' | null>(null)
  const lastSyncAtRef = useRef(0)
  const lastActivityAtRef = useRef(0)
  const idleTriggeredRef = useRef(false)
  const agentIdRef = useRef<string | null>(agentId)
  const subscribedAgentIdRef = useRef<string | null>(null)
  const [snapshot, setSnapshot] = useState<AgentChatSocketSnapshot>({
    status: 'idle',
    lastConnectedAt: null,
    lastError: null,
  })

  useEffect(() => {
    agentIdRef.current = agentId
  }, [agentId])

  const updateSnapshot = useCallback((updates: Partial<AgentChatSocketSnapshot>) => {
    setSnapshot((current) => ({ ...current, ...updates }))
  }, [])

  const sendSocketMessage = useCallback((payload: Record<string, unknown>) => {
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false
    }
    try {
      socket.send(JSON.stringify(payload))
      return true
    } catch (error) {
      console.warn('Failed to send agent chat socket message', error)
      return false
    }
  }, [])

  const markActivity = useCallback(() => {
    lastActivityAtRef.current = Date.now()
  }, [])

  const clearPingTimers = useCallback(() => {
    if (pingIntervalRef.current !== null) {
      clearInterval(pingIntervalRef.current)
      pingIntervalRef.current = null
    }
    if (pongTimeoutRef.current !== null) {
      clearTimeout(pongTimeoutRef.current)
      pongTimeoutRef.current = null
    }
  }, [])

  const schedulePongTimeout = useCallback(
    (sentAt: number) => {
      if (pongTimeoutRef.current !== null) {
        clearTimeout(pongTimeoutRef.current)
      }
      pongTimeoutRef.current = window.setTimeout(() => {
        const socket = socketRef.current
        if (!socket || socket.readyState !== WebSocket.OPEN) {
          return
        }
        if (lastActivityAtRef.current >= sentAt) {
          return
        }
        updateSnapshot({ status: 'reconnecting', lastError: 'WebSocket keepalive timed out.' })
        socket.close()
      }, PONG_TIMEOUT_MS)
    },
    [updateSnapshot],
  )

  const sendPing = useCallback(() => {
    if (pauseReasonRef.current !== null || !isPageActive()) {
      return
    }
    const sentAt = Date.now()
    if (sendSocketMessage({ type: 'ping' })) {
      schedulePongTimeout(sentAt)
    }
  }, [schedulePongTimeout, sendSocketMessage])

  const startPingLoop = useCallback(() => {
    if (pingIntervalRef.current !== null) {
      return
    }
    pingIntervalRef.current = window.setInterval(() => {
      sendPing()
    }, PING_INTERVAL_MS)
    sendPing()
  }, [sendPing])

  const stopPingLoop = useCallback(() => {
    clearPingTimers()
  }, [clearPingTimers])

  const clearIdleTimeout = useCallback(() => {
    if (idleTimeoutRef.current !== null) {
      clearTimeout(idleTimeoutRef.current)
      idleTimeoutRef.current = null
    }
    idleTriggeredRef.current = false
  }, [])

  const scheduleIdleTimeout = useCallback(() => {
    if (idleTimeoutRef.current !== null) {
      return
    }
    idleTimeoutRef.current = window.setTimeout(() => {
      idleTimeoutRef.current = null
      if (isPageActive()) {
        return
      }
      idleTriggeredRef.current = true
      stopPingLoop()
      closeSocketRef.current()
      updateSnapshot({ status: 'idle', lastError: null })
    }, SOCKET_IDLE_TIMEOUT_MS)
  }, [stopPingLoop, updateSnapshot])

  const syncNow = useCallback(() => {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return
    }
    if (!agentIdRef.current) {
      return
    }
    const now = Date.now()
    if (now - lastSyncAtRef.current < RESYNC_THROTTLE_MS) {
      return
    }
    lastSyncAtRef.current = now
    void refreshLatestRef.current()
    void refreshProcessingRef.current()
  }, [])

  const updateSubscription = useCallback((nextAgentId: string | null) => {
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return
    }

    const currentAgentId = subscribedAgentIdRef.current
    if (currentAgentId && currentAgentId !== nextAgentId) {
      if (!sendSocketMessage({ type: 'unsubscribe', agent_id: currentAgentId })) {
        updateSnapshot({ status: 'reconnecting', lastError: 'WebSocket send failed.' })
        socket.close()
        return
      }
      subscribedAgentIdRef.current = null
    }

    if (!nextAgentId || currentAgentId === nextAgentId) {
      return
    }

    const contextOverride = readStoredConsoleContext()
    const payload: Record<string, unknown> = { type: 'subscribe', agent_id: nextAgentId }
    if (contextOverride?.type && contextOverride?.id) {
      payload.context = { type: contextOverride.type, id: contextOverride.id }
    }
    if (!sendSocketMessage(payload)) {
      updateSnapshot({ status: 'reconnecting', lastError: 'WebSocket send failed.' })
      socket.close()
      return
    }
    subscribedAgentIdRef.current = nextAgentId
  }, [sendSocketMessage, updateSnapshot])

  useEffect(() => {
    updateSubscription(agentId)
  }, [agentId, updateSubscription])

  const handleResume = useCallback((reason: PageLifecycleResumeReason) => {
    clearIdleTimeout()
    idleTriggeredRef.current = false
    if (!isPageActive()) {
      if (pauseReasonRef.current !== 'offline') {
        pauseReasonRef.current = 'hidden'
      }
      return
    }
    if (pauseReasonRef.current === 'offline' && reason !== 'online') {
      return
    }
    pauseReasonRef.current = null
    retryRef.current = 0
    const existingSocket = socketRef.current
    if (existingSocket?.readyState === WebSocket.OPEN) {
      updateSnapshot({ status: 'connected', lastError: null })
      startPingLoop()
      syncNow()
      return
    }
    if (existingSocket?.readyState === WebSocket.CONNECTING) {
      updateSnapshot({ status: retryRef.current > 0 ? 'reconnecting' : 'connecting', lastError: null })
      syncNow()
      return
    }
    updateSnapshot({ status: 'connecting', lastError: null })
    scheduleConnectRef.current(0)
    syncNow()
  }, [clearIdleTimeout, startPingLoop, syncNow, updateSnapshot])

  const handleSuspend = useCallback((reason: PageLifecycleSuspendReason) => {
    if (reason === 'offline') {
      pauseReasonRef.current = 'offline'
      retryRef.current = 0
      updateSnapshot({ status: 'offline', lastError: 'Network connection lost.' })
      stopPingLoop()
      clearIdleTimeout()
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
      closeSocketRef.current()
      return
    }
    if (pauseReasonRef.current !== 'offline') {
      pauseReasonRef.current = 'hidden'
    }
    stopPingLoop()
    scheduleIdleTimeout()
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current)
      timeoutRef.current = null
    }
  }, [clearIdleTimeout, scheduleIdleTimeout, stopPingLoop, updateSnapshot])

  usePageLifecycle({ onResume: handleResume, onSuspend: handleSuspend })

  useEffect(() => {
    retryRef.current = 0
    lastSyncAtRef.current = 0

    const scheduleConnect = (delay: number) => {
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current)
      }
      timeoutRef.current = window.setTimeout(() => {
        openSocket()
      }, delay)
    }
    scheduleConnectRef.current = scheduleConnect

    const closeSocket = () => {
      if (socketRef.current) {
        stopPingLoop()
        closingSocketRef.current = socketRef.current
        try {
          socketRef.current.close()
        } catch (error) {
          closingSocketRef.current = null
          console.warn('Failed to close agent chat socket', error)
        }
        socketRef.current = null
        subscribedAgentIdRef.current = null
      }
    }
    closeSocketRef.current = closeSocket

    const openSocket = () => {
      if (pauseReasonRef.current !== null || !isPageActive()) {
        return
      }
      const existing = socketRef.current
      if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
        return
      }
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/agents/chat/`)
      const socketInstance = socket
      socketRef.current = socket
      updateSnapshot({
        status: retryRef.current > 0 ? 'reconnecting' : 'connecting',
        lastError: null,
      })

      socket.onopen = () => {
        if (socketRef.current !== socketInstance) {
          return
        }
        retryRef.current = 0
        markActivity()
        updateSnapshot({
          status: 'connected',
          lastConnectedAt: Date.now(),
          lastError: null,
        })
        subscribedAgentIdRef.current = null
        updateSubscription(agentIdRef.current)
        startPingLoop()
        syncNow()
      }

      socket.onmessage = (event) => {
        if (socketRef.current !== socketInstance) {
          return
        }
        try {
          const payload = JSON.parse(event.data)
          markActivity()
          if (payload?.type === 'pong') {
            return
          }
          if (payload?.type === 'subscription.error') {
            const message = typeof payload?.message === 'string' ? payload.message : 'Subscription error.'
            const payloadAgentId = typeof payload?.agent_id === 'string' ? payload.agent_id : null
            if (!payloadAgentId || payloadAgentId === agentIdRef.current) {
              subscribedAgentIdRef.current = null
              updateSnapshot({ status: 'error', lastError: message })
              if (isAuthErrorMessage(message)) {
                scheduleLoginRedirect()
              }
              syncNow()
            }
            return
          }
          if (payload?.type === 'timeline.event' && payload.payload) {
            receiveEventRef.current(payload.payload as TimelineEvent)
          } else if (payload?.type === 'processing' && payload.payload) {
            updateProcessingRef.current(payload.payload as Partial<ProcessingSnapshot>)
          } else if (payload?.type === 'stream.event' && payload.payload) {
            receiveStreamRef.current(payload.payload)
          }
        } catch (error) {
          console.error('Failed to process websocket message', error)
        }
      }

      socket.onclose = (event) => {
        if (socketRef.current !== socketInstance) {
          if (closingSocketRef.current === socketInstance) {
            closingSocketRef.current = null
          }
          return
        }
        socketRef.current = null
        subscribedAgentIdRef.current = null
        stopPingLoop()
        if (closingSocketRef.current === socketInstance) {
          closingSocketRef.current = null
          return
        }
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
          pauseReasonRef.current = 'offline'
          retryRef.current = 0
          updateSnapshot({ status: 'offline', lastError: 'Network connection lost.' })
          return
        }
        if (pauseReasonRef.current !== null) {
          return
        }
        const errorMessage = describeCloseEvent(event)
        if (event.code === 4401) {
          updateSnapshot({
            status: 'error',
            lastError: errorMessage || 'Authentication required.',
          })
          scheduleLoginRedirect()
          return
        }
        if (event.code >= 4400 && event.code < 4500) {
          updateSnapshot({
            status: 'error',
            lastError: errorMessage || 'WebSocket authorization failed.',
          })
          return
        }
        updateSnapshot({
          status: 'reconnecting',
          lastError: errorMessage,
        })
        const delay = computeReconnectDelay(retryRef.current)
        retryRef.current += 1
        scheduleConnect(delay)
      }

      socket.onerror = () => {
        if (socketRef.current !== socketInstance) {
          return
        }
        updateSnapshot({
          status: 'reconnecting',
          lastError: 'WebSocket connection error.',
        })
        socket.close()
      }
    }

    pauseReasonRef.current = null
    if (!isPageActive()) {
      pauseReasonRef.current = 'hidden'
    }
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      pauseReasonRef.current = 'offline'
      updateSnapshot({ status: 'offline', lastError: 'Network connection lost.' })
    } else if (pauseReasonRef.current === null) {
      scheduleConnect(0)
    }

    if (syncIntervalRef.current === null) {
      syncIntervalRef.current = window.setInterval(() => {
        if (pauseReasonRef.current !== null) {
          return
        }
        if (!isPageActive()) {
          return
        }
        syncNow()
      }, BACKGROUND_SYNC_INTERVAL_MS)
    }

    return () => {
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
      if (syncIntervalRef.current !== null) {
        clearInterval(syncIntervalRef.current)
        syncIntervalRef.current = null
      }
      clearIdleTimeout()
      stopPingLoop()
      closeSocket()
    }
  }, [clearIdleTimeout, markActivity, startPingLoop, stopPingLoop, syncNow, updateSnapshot, updateSubscription])

  return snapshot
}

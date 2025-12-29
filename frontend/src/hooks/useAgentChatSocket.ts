import { useCallback, useEffect, useRef, useState } from 'react'

import type { ProcessingSnapshot, TimelineEvent } from '../types/agentChat'
import { useAgentChatStore } from '../stores/agentChatStore'
import { usePageLifecycle, type PageLifecycleResumeReason, type PageLifecycleSuspendReason } from './usePageLifecycle'

const RECONNECT_BASE_DELAY_MS = 1000
const RECONNECT_MAX_DELAY_MS = 15000
const RESYNC_THROTTLE_MS = 4000
const BACKGROUND_SYNC_INTERVAL_MS = 30000

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

function computeReconnectDelay(attempt: number): number {
  const exponent = Math.min(attempt, 6)
  const base = Math.min(RECONNECT_MAX_DELAY_MS, RECONNECT_BASE_DELAY_MS * 2 ** exponent)
  const jitter = Math.round(base * 0.2 * Math.random())
  return base + jitter
}

function isPageVisible(): boolean {
  if (typeof document === 'undefined') {
    return true
  }
  return document.visibilityState === 'visible'
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
  const scheduleConnectRef = useRef<(delay: number) => void>(() => undefined)
  const closeSocketRef = useRef<() => void>(() => undefined)
  const closingSocketRef = useRef<WebSocket | null>(null)
  const pauseReasonRef = useRef<'offline' | 'hidden' | null>(null)
  const lastSyncAtRef = useRef(0)
  const [snapshot, setSnapshot] = useState<AgentChatSocketSnapshot>({
    status: 'idle',
    lastConnectedAt: null,
    lastError: null,
  })

  const updateSnapshot = useCallback((updates: Partial<AgentChatSocketSnapshot>) => {
    setSnapshot((current) => ({ ...current, ...updates }))
  }, [])

  const syncNow = useCallback(() => {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
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

  const handleResume = useCallback((reason: PageLifecycleResumeReason) => {
    if (!agentId) {
      return
    }
    if (!isPageVisible()) {
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
    updateSnapshot({ status: 'connecting', lastError: null })
    scheduleConnectRef.current(0)
    syncNow()
  }, [agentId, syncNow, updateSnapshot])

  const handleSuspend = useCallback((reason: PageLifecycleSuspendReason) => {
    if (!agentId) {
      return
    }
    if (reason === 'offline') {
      pauseReasonRef.current = 'offline'
      retryRef.current = 0
      updateSnapshot({ status: 'offline', lastError: 'Network connection lost.' })
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
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current)
      timeoutRef.current = null
    }
  }, [agentId, updateSnapshot])

  usePageLifecycle({ onResume: handleResume, onSuspend: handleSuspend })

  useEffect(() => {
    if (!agentId) {
      updateSnapshot({ status: 'idle', lastError: null, lastConnectedAt: null })
      return () => undefined
    }

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
        closingSocketRef.current = socketRef.current
        try {
          socketRef.current.close()
        } catch (error) {
          closingSocketRef.current = null
          console.warn('Failed to close agent chat socket', error)
        }
        socketRef.current = null
      }
    }
    closeSocketRef.current = closeSocket

    const openSocket = () => {
      if (pauseReasonRef.current !== null || !isPageVisible()) {
        return
      }
      const existing = socketRef.current
      if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
        return
      }
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/agents/${agentId}/chat/`)
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
        updateSnapshot({
          status: 'connected',
          lastConnectedAt: Date.now(),
          lastError: null,
        })
        syncNow()
      }

      socket.onmessage = (event) => {
        if (socketRef.current !== socketInstance) {
          return
        }
        try {
          const payload = JSON.parse(event.data)
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
    if (!isPageVisible()) {
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
        if (!isPageVisible()) {
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
      closeSocket()
    }
  }, [agentId, syncNow, updateSnapshot])

  return snapshot
}

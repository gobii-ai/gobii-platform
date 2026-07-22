import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { scheduleLoginRedirect } from '../api/http'
import type { AgentMessageNotification } from '../types/agentChat'
import { useAppDispatch } from '../store/hooks'
import { applyPendingActionsSnapshot, chatActions, receiveRealtimeEvent, receiveStreamEvent, updateRealtimeProcessing } from '../store/chatSlice'
import { refreshTimelineLatestInCache } from './useTimelineCacheInjector'
import { usePageLifecycle, type PageLifecycleResumeReason, type PageLifecycleSuspendReason } from './usePageLifecycle'
import { TIMELINE_STALE_TIME_MS, timelineQueryKey } from './useAgentTimeline'
import {
  confirmAgentChatSocketSubscription,
  findActiveAgentChatSocketId,
  normalizeAgentChatSocketSubscriptions,
  syncAgentChatSocketSubscriptions,
  type AgentChatSocketContextOverride,
  type AgentChatSocketSubscription,
} from './agentChatSocketProtocol'
import { routeAgentChatSocketMessage } from './agentChatSocketMessageRouter'
import type { StaffViewContext } from '../api/context'

const RECONNECT_BASE_DELAY_MS = 1000
const RECONNECT_MAX_DELAY_MS = 15000
const RESYNC_THROTTLE_MS = 4000
const BACKGROUND_SYNC_INTERVAL_MS = 30000
const PING_INTERVAL_MS = 20000
const PONG_TIMEOUT_MS = 8000
const CONNECT_TIMEOUT_MS = 10000

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

function isPageVisible(): boolean {
  if (typeof document === 'undefined') {
    return true
  }
  return document.visibilityState === 'visible'
}

function useLatestRef<T>(value: T) {
  const ref = useRef(value)
  useLayoutEffect(() => {
    ref.current = value
  }, [value])
  return ref
}

export function useAgentChatSocket(
  desiredSubscriptionsInput: AgentChatSocketSubscription[],
  options: {
    contextOverride?: AgentChatSocketContextOverride
    staffContextOverride?: StaffViewContext | null
    developerMode?: boolean
    onCreditEvent?: (payload: Record<string, unknown>) => void
    onAgentProfileEvent?: (payload: Record<string, unknown>) => void
    onMessageNotificationEvent?: (payload: AgentMessageNotification) => void
    onDeveloperUpdate?: (agentId: string) => void
  } = {},
): AgentChatSocketSnapshot {
  const queryClient = useQueryClient()
  const dispatch = useAppDispatch()
  const desiredSubscriptions = useMemo(
    () => normalizeAgentChatSocketSubscriptions(desiredSubscriptionsInput),
    [desiredSubscriptionsInput],
  )
  const handlersRef = useLatestRef({
    receiveRealtimeEvent: (agentId: string, event: Parameters<typeof receiveRealtimeEvent>[1]) => {
      dispatch(receiveRealtimeEvent(agentId, event))
    },
    updateProcessing: (agentId: string, processing: Parameters<typeof updateRealtimeProcessing>[1]) => {
      dispatch(updateRealtimeProcessing(agentId, processing))
    },
    updateAgentIdentity: (update: Parameters<typeof chatActions.agentIdentityUpdated>[0]) => {
      dispatch(chatActions.agentIdentityUpdated(update))
    },
    updateUsageInsight: (agentId: string, metadata: Parameters<typeof chatActions.usageInsightUpdated>[0]['metadata']) => {
      dispatch(chatActions.usageInsightUpdated({ agentId, metadata }))
    },
    receiveStreamEvent: (agentId: string, payload: Parameters<typeof receiveStreamEvent>[1]) => {
      dispatch(receiveStreamEvent(agentId, payload))
    },
    replacePendingActions: (
      agentId: string,
      pendingActions: Parameters<typeof chatActions.pendingActionsSnapshotReceived>[0]['pendingActions'],
      stateOrder: number,
    ) => {
      dispatch(applyPendingActionsSnapshot(agentId, pendingActions, stateOrder))
    },
    onCreditEvent: options.onCreditEvent ?? null,
    onAgentProfileEvent: options.onAgentProfileEvent ?? null,
    onMessageNotificationEvent: options.onMessageNotificationEvent ?? null,
    onDeveloperUpdate: options.onDeveloperUpdate ?? null,
  })
  const currentRef = useLatestRef({
    desiredSubscriptions,
    activeAgentId: findActiveAgentChatSocketId(desiredSubscriptions),
    contextOverride: options.contextOverride,
    staffContext: options.staffContextOverride ?? null,
    developerMode: options.developerMode === true,
  })

  const retryRef = useRef(0)
  const socketRef = useRef<WebSocket | null>(null)
  const timeoutRef = useRef<number | null>(null)
  const syncIntervalRef = useRef<number | null>(null)
  const pingIntervalRef = useRef<number | null>(null)
  const pongTimeoutRef = useRef<number | null>(null)
  const connectTimeoutRef = useRef<number | null>(null)
  const scheduleConnectRef = useRef<(delay: number) => void>(() => undefined)
  const closeSocketRef = useRef<() => void>(() => undefined)
  const closingSocketRef = useRef<WebSocket | null>(null)
  const pauseReasonRef = useRef<'offline' | null>(null)
  const lastSyncAtRef = useRef(0)
  const lastActivityAtRef = useRef(0)
  const requestedSubscriptionsRef = useRef<Map<string, AgentChatSocketSubscription['mode']>>(new Map())
  const confirmedSubscriptionsRef = useRef<Map<string, AgentChatSocketSubscription['mode']>>(new Map())
  const [snapshot, setSnapshot] = useState<AgentChatSocketSnapshot>(() => (
    typeof navigator !== 'undefined' && navigator.onLine === false
      ? { status: 'offline', lastConnectedAt: null, lastError: 'Network connection lost.' }
      : { status: 'idle', lastConnectedAt: null, lastError: null }
  ))

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
    if (pauseReasonRef.current !== null) {
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

  const clearConnectTimeout = useCallback(() => {
    if (connectTimeoutRef.current !== null) {
      clearTimeout(connectTimeoutRef.current)
      connectTimeoutRef.current = null
    }
  }, [])

  const syncNow = useCallback((mode: 'fast' | 'contiguous' = 'fast') => {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return
    }
    if (!currentRef.current.desiredSubscriptions.length) {
      return
    }
    const now = Date.now()
    if (now - lastSyncAtRef.current < RESYNC_THROTTLE_MS) {
      return
    }
    lastSyncAtRef.current = now

    currentRef.current.desiredSubscriptions.forEach(({ agentId }) => {
      const { developerMode, staffContext } = currentRef.current
      const timelineState = queryClient.getQueryState(timelineQueryKey(agentId, developerMode, staffContext))
      if (!timelineState) {
        return
      }
      if (timelineState.fetchStatus === 'fetching') {
        return
      }
      if (timelineState.dataUpdatedAt && now - timelineState.dataUpdatedAt < TIMELINE_STALE_TIME_MS) {
        return
      }
      void refreshTimelineLatestInCache(queryClient, agentId, {
        mode,
        developerMode,
        staffContext,
      })
    })
  }, [currentRef, queryClient])

  const applySubscriptions = useCallback((nextSubscriptions: AgentChatSocketSubscription[]) => {
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return
    }

    const desiredModes = new Map(nextSubscriptions.map(({ agentId, mode }) => [agentId, mode]))
    for (const [agentId, confirmedMode] of confirmedSubscriptionsRef.current) {
      if (desiredModes.get(agentId) !== confirmedMode) {
        confirmedSubscriptionsRef.current.delete(agentId)
      }
    }

    syncAgentChatSocketSubscriptions({
      currentSubscriptions: requestedSubscriptionsRef.current,
      desiredSubscriptions: nextSubscriptions,
      contextOverride: currentRef.current.contextOverride,
      staffContextOverride: currentRef.current.staffContext,
      sendSocketMessage,
      handleSendFailure: () => {
        updateSnapshot({ status: 'reconnecting', lastError: 'WebSocket send failed.' })
        socket.close()
      },
    })
  }, [currentRef, sendSocketMessage, updateSnapshot])

  useEffect(() => {
    applySubscriptions(desiredSubscriptions)
  }, [applySubscriptions, desiredSubscriptions])

  const handleResume = useCallback((reason: PageLifecycleResumeReason) => {
    if (pauseReasonRef.current === 'offline' && reason !== 'online') {
      return
    }
    pauseReasonRef.current = null
    retryRef.current = 0
    const existingSocket = socketRef.current
    if (existingSocket?.readyState === WebSocket.OPEN) {
      updateSnapshot({ status: 'connected', lastError: null })
      startPingLoop()
      applySubscriptions(currentRef.current.desiredSubscriptions)
      syncNow('contiguous')
      return
    }
    if (existingSocket?.readyState === WebSocket.CONNECTING) {
      updateSnapshot({ status: retryRef.current > 0 ? 'reconnecting' : 'connecting', lastError: null })
      syncNow('contiguous')
      return
    }
    updateSnapshot({ status: 'connecting', lastError: null })
    scheduleConnectRef.current(0)
    syncNow('contiguous')
  }, [applySubscriptions, currentRef, startPingLoop, syncNow, updateSnapshot])

  const handleSuspend = useCallback((reason: PageLifecycleSuspendReason) => {
    if (reason === 'offline') {
      pauseReasonRef.current = 'offline'
      retryRef.current = 0
      updateSnapshot({ status: 'offline', lastError: 'Network connection lost.' })
      stopPingLoop()
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
      closeSocketRef.current()
      return
    }
  }, [stopPingLoop, updateSnapshot])

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
        clearConnectTimeout()
        closingSocketRef.current = socketRef.current
        try {
          socketRef.current.close()
        } catch (error) {
          closingSocketRef.current = null
          console.warn('Failed to close agent chat socket', error)
        }
        socketRef.current = null
      }
      requestedSubscriptionsRef.current = new Map()
      confirmedSubscriptionsRef.current = new Map()
    }
    closeSocketRef.current = closeSocket

    const openSocket = () => {
      if (pauseReasonRef.current !== null) {
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
      clearConnectTimeout()
      connectTimeoutRef.current = window.setTimeout(() => {
        if (socketRef.current !== socketInstance) {
          return
        }
        if (socketInstance.readyState === WebSocket.CONNECTING) {
          updateSnapshot({ status: 'reconnecting', lastError: 'WebSocket connection timed out.' })
          socketInstance.close()
        }
      }, CONNECT_TIMEOUT_MS)

      socket.onopen = () => {
        if (socketRef.current !== socketInstance) {
          return
        }
        clearConnectTimeout()
        retryRef.current = 0
        markActivity()
        updateSnapshot({
          status: 'connected',
          lastConnectedAt: Date.now(),
          lastError: null,
        })
        requestedSubscriptionsRef.current = new Map()
        confirmedSubscriptionsRef.current = new Map()
        applySubscriptions(currentRef.current.desiredSubscriptions)
        startPingLoop()
        syncNow('contiguous')
      }

      socket.onmessage = (event) => {
        if (socketRef.current !== socketInstance) {
          return
        }
        try {
          const payload = JSON.parse(event.data)
          markActivity()
          const handlers = handlersRef.current
          const outcome = routeAgentChatSocketMessage({
            payload,
            queryClient,
            activeAgentId: currentRef.current.activeAgentId,
            ...handlers,
          })
          if (outcome.type === 'subscription_error') {
            if (outcome.agentId) {
              requestedSubscriptionsRef.current.delete(outcome.agentId)
              confirmedSubscriptionsRef.current.delete(outcome.agentId)
            }
            if (!outcome.agentId || outcome.agentId === currentRef.current.activeAgentId) {
              updateSnapshot({ status: 'error', lastError: outcome.message })
              if (isAuthErrorMessage(outcome.message)) {
                scheduleLoginRedirect()
              }
              syncNow('contiguous')
            }
          } else if (outcome.type === 'subscription_ready') {
            const shouldBackfill = confirmAgentChatSocketSubscription({
              requestedSubscriptions: requestedSubscriptionsRef.current,
              confirmedSubscriptions: confirmedSubscriptionsRef.current,
              agentId: outcome.agentId,
              mode: outcome.mode,
            })
            if (shouldBackfill) {
              void refreshTimelineLatestInCache(queryClient, outcome.agentId, {
                mode: 'contiguous',
                developerMode: currentRef.current.developerMode,
                staffContext: currentRef.current.staffContext,
                allowDuringQueryFetch: true,
              })
            }
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
        clearConnectTimeout()
        socketRef.current = null
        requestedSubscriptionsRef.current = new Map()
        confirmedSubscriptionsRef.current = new Map()
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
        clearConnectTimeout()
        updateSnapshot({
          status: 'reconnecting',
          lastError: 'WebSocket connection error.',
        })
        socket.close()
      }
    }

    pauseReasonRef.current = null
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      pauseReasonRef.current = 'offline'
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
      clearConnectTimeout()
      stopPingLoop()
      closeSocket()
    }
  }, [
    clearConnectTimeout,
    currentRef,
    handlersRef,
    markActivity,
    queryClient,
    startPingLoop,
    stopPingLoop,
    syncNow,
    updateSnapshot,
    applySubscriptions,
  ])

  return snapshot
}

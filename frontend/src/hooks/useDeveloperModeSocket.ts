import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import type { StaffViewContext } from '../api/context'
import { refreshTimelineLatestInCache } from './useTimelineCacheInjector'

const PING_INTERVAL_MS = 20_000
const PONG_TIMEOUT_MS = 8_000
const CONNECT_TIMEOUT_MS = 10_000

export function useDeveloperModeSocket(agentId: string | null, enabled: boolean, staffContext?: StaffViewContext | null) {
  const queryClient = useQueryClient()
  const retryRef = useRef(0)
  const socketRef = useRef<WebSocket | null>(null)
  const timeoutRef = useRef<number | null>(null)
  const staffContextType = staffContext?.type
  const staffContextId = staffContext?.id

  useEffect(() => {
    if (!agentId || !enabled) {
      return () => undefined
    }

    let disposed = false
    let refreshRequested = false
    let refreshRunning = false
    let pingInterval: number | null = null
    let pongTimeout: number | null = null
    let connectTimeout: number | null = null

    const clearHeartbeat = () => {
      if (pingInterval !== null) window.clearInterval(pingInterval)
      if (pongTimeout !== null) window.clearTimeout(pongTimeout)
      if (connectTimeout !== null) window.clearTimeout(connectTimeout)
      pingInterval = null
      pongTimeout = null
      connectTimeout = null
    }

    const refreshDeveloperTimeline = async () => {
      refreshRequested = true
      if (refreshRunning) return
      refreshRunning = true
      try {
        while (refreshRequested && !disposed) {
          refreshRequested = false
          await refreshTimelineLatestInCache(queryClient, agentId, {
            mode: 'contiguous',
            developerMode: true,
            staffContext: staffContextType && staffContextId
              ? { type: staffContextType, id: staffContextId }
              : null,
            allowDuringQueryFetch: true,
          })
        }
      } finally {
        refreshRunning = false
      }
    }

    const sendPing = (socket: WebSocket) => {
      if (socket.readyState !== WebSocket.OPEN) return
      socket.send(JSON.stringify({ type: 'ping' }))
      if (pongTimeout !== null) window.clearTimeout(pongTimeout)
      pongTimeout = window.setTimeout(() => socket.close(), PONG_TIMEOUT_MS)
    }

    const scheduleConnect = (delay: number) => {
      if (timeoutRef.current !== null) clearTimeout(timeoutRef.current)
      timeoutRef.current = window.setTimeout(openSocket, delay)
    }
    const openSocket = () => {
      if (disposed) return
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const query = new URLSearchParams()
      if (staffContextType && staffContextId) {
        query.set('staff_context_type', staffContextType)
        query.set('staff_context_id', staffContextId)
      }
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/staff/agents/${agentId}/developer/${query.toString() ? `?${query}` : ''}`)
      socketRef.current = socket
      connectTimeout = window.setTimeout(() => socket.close(), CONNECT_TIMEOUT_MS)
      socket.onopen = () => {
        retryRef.current = 0
        if (connectTimeout !== null) window.clearTimeout(connectTimeout)
        connectTimeout = null
        sendPing(socket)
        pingInterval = window.setInterval(() => sendPing(socket), PING_INTERVAL_MS)
      }
      socket.onmessage = (message) => {
        try {
          const payload = JSON.parse(message.data)
          if (pongTimeout !== null) window.clearTimeout(pongTimeout)
          pongTimeout = null
          if (payload?.type === 'developer.event') {
            void refreshDeveloperTimeline()
          }
        } catch (error) {
          console.error('Failed to process developer websocket message', error)
        }
      }
      socket.onclose = () => {
        clearHeartbeat()
        socketRef.current = null
        if (disposed) return
        const delay = Math.min(1000 * 2 ** retryRef.current, 8000)
        retryRef.current += 1
        scheduleConnect(delay)
      }
      socket.onerror = () => socket.close()
    }

    scheduleConnect(0)
    return () => {
      disposed = true
      clearHeartbeat()
      if (timeoutRef.current !== null) clearTimeout(timeoutRef.current)
      timeoutRef.current = null
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [agentId, enabled, queryClient, staffContextId, staffContextType])
}

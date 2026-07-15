import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import type { StaffViewContext } from '../api/context'

const MAX_RETRIES = 5

export function useDeveloperModeSocket(agentId: string | null, enabled: boolean, staffContext?: StaffViewContext | null) {
  const queryClient = useQueryClient()
  const retryRef = useRef(0)
  const socketRef = useRef<WebSocket | null>(null)
  const timeoutRef = useRef<number | null>(null)

  useEffect(() => {
    if (!agentId || !enabled) {
      return () => undefined
    }

    let disposed = false
    const scheduleConnect = (delay: number) => {
      if (timeoutRef.current !== null) clearTimeout(timeoutRef.current)
      timeoutRef.current = window.setTimeout(openSocket, delay)
    }
    const openSocket = () => {
      if (disposed) return
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const query = new URLSearchParams()
      if (staffContext) {
        query.set('staff_context_type', staffContext.type)
        query.set('staff_context_id', staffContext.id)
      }
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/staff/agents/${agentId}/developer/${query.toString() ? `?${query}` : ''}`)
      socketRef.current = socket
      socket.onopen = () => { retryRef.current = 0 }
      socket.onmessage = (message) => {
        try {
          const payload = JSON.parse(message.data)
          if (payload?.type === 'developer.event') {
            void queryClient.invalidateQueries({ queryKey: ['agent-timeline', agentId, 'developer'] })
          }
        } catch (error) {
          console.error('Failed to process developer websocket message', error)
        }
      }
      socket.onclose = () => {
        socketRef.current = null
        if (disposed || retryRef.current >= MAX_RETRIES) return
        const delay = Math.min(1000 * 2 ** retryRef.current, 8000)
        retryRef.current += 1
        scheduleConnect(delay)
      }
      socket.onerror = () => socket.close()
    }

    scheduleConnect(0)
    return () => {
      disposed = true
      if (timeoutRef.current !== null) clearTimeout(timeoutRef.current)
      timeoutRef.current = null
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [agentId, enabled, queryClient, staffContext])
}

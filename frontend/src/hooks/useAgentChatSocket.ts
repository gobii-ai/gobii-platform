import { useEffect, useRef } from 'react'

import type { TimelineEvent } from '../types/agentChat'
import { useAgentChatStore } from '../stores/agentChatStore'

const MAX_RETRIES = 5

export function useAgentChatSocket(agentId: string | null) {
  const receiveEventRef = useRef(useAgentChatStore.getState().receiveRealtimeEvent)
  const updateProcessingRef = useRef(useAgentChatStore.getState().updateProcessing)

  useEffect(() =>
    useAgentChatStore.subscribe((state) => {
      receiveEventRef.current = state.receiveRealtimeEvent
      updateProcessingRef.current = state.updateProcessing
    }),
  [])

  const retryRef = useRef(0)
  const socketRef = useRef<WebSocket | null>(null)
  const timeoutRef = useRef<number | null>(null)

  useEffect(() => {
    if (!agentId) {
      return () => undefined
    }

    const scheduleConnect = (delay: number) => {
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current)
      }
      timeoutRef.current = window.setTimeout(() => {
        openSocket()
      }, delay)
    }

    const openSocket = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/agents/${agentId}/chat/`)
      socketRef.current = socket

      socket.onopen = () => {
        retryRef.current = 0
      }

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (payload?.type === 'timeline.event' && payload.payload) {
            receiveEventRef.current(payload.payload as TimelineEvent)
          } else if (payload?.type === 'processing' && payload.payload) {
            updateProcessingRef.current(Boolean(payload.payload.active))
          }
        } catch (error) {
          console.error('Failed to process websocket message', error)
        }
      }

      socket.onclose = () => {
        socketRef.current = null
        if (retryRef.current >= MAX_RETRIES) {
          return
        }
        const delay = Math.min(1000 * 2 ** retryRef.current, 8000)
        retryRef.current += 1
        scheduleConnect(delay)
      }

      socket.onerror = () => {
        socket.close()
      }
    }

    scheduleConnect(0)

    return () => {
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
      if (socketRef.current) {
        socketRef.current.close()
        socketRef.current = null
      }
    }
  }, [agentId])
}

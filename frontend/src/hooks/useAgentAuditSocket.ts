import { useEffect, useRef } from 'react'
import { auditActions } from '../store/auditSlice'
import { useAppDispatch } from '../store/hooks'

const MAX_RETRIES = 5

export function useAgentAuditSocket(agentId: string | null) {
  const dispatch = useAppDispatch()
  const receiveEventRef = useRef((payload: any) => {
    dispatch(auditActions.receiveRealtimeEvent(payload))
  })

  useEffect(() => {
    receiveEventRef.current = (payload: any) => {
      dispatch(auditActions.receiveRealtimeEvent(payload))
    }
  }, [dispatch])

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
      const socket = new WebSocket(`${protocol}://${window.location.host}/ws/staff/agents/${agentId}/audit/`)
      socketRef.current = socket

      socket.onopen = () => {
        retryRef.current = 0
      }

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (payload?.type === 'audit.event' && payload.payload) {
            receiveEventRef.current(payload.payload)
          }
        } catch (error) {
          console.error('Failed to process audit websocket message', error)
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

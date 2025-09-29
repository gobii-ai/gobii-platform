import { useCallback, useEffect, useState } from 'react'

import type { PingStatus } from './usePingProbe'

export type WebSocketSnapshot = {
  timestamp: number
  roundtripMs: number
  echoPayload: string
}

const DEFAULT_TIMEOUT_MS = 5000

function buildWebSocketUrl(): string {
  if (typeof window === 'undefined') {
    throw new Error('WebSocket probe requires a browser environment.')
  }

  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}/ws/echo/`
}

export function useWebSocketProbe(autoStart: boolean = true) {
  const [status, setStatus] = useState<PingStatus>('idle')
  const [snapshot, setSnapshot] = useState<WebSocketSnapshot | undefined>()
  const [errorMessage, setErrorMessage] = useState<string | undefined>()

  const runProbe = useCallback(async () => {
    setStatus('loading')
    setErrorMessage(undefined)

    try {
      const result = await new Promise<WebSocketSnapshot>((resolve, reject) => {
        if (typeof window === 'undefined' || !('WebSocket' in window)) {
          reject(new Error('WebSockets are not supported in this environment.'))
          return
        }

        const url = buildWebSocketUrl()
        const socket = new WebSocket(url)
        const start = performance.now()
        let settled = false

        const settle = (fn: () => void) => {
          if (settled) {
            return
          }
          settled = true
          window.clearTimeout(timeoutId)
          socket.removeEventListener('open', handleOpen)
          socket.removeEventListener('message', handleMessage)
          socket.removeEventListener('error', handleError)
          socket.removeEventListener('close', handleClose)
          fn()
          try {
            socket.close()
          } catch {
            /* ignore close errors */
          }
        }

        const timeoutId = window.setTimeout(() => {
          settle(() => reject(new Error('Timed out waiting for echo response.')))
        }, DEFAULT_TIMEOUT_MS)

        const handleOpen = () => {
          try {
            socket.send(JSON.stringify({ ping: 'diagnostics' }))
          } catch {
            settle(() => reject(new Error('Failed to send WebSocket payload.')))
          }
        }

        const handleMessage = (event: MessageEvent) => {
          let echoPayload = ''
          if (typeof event.data === 'string') {
            try {
              const parsed = JSON.parse(event.data)
              if (parsed && typeof parsed === 'object' && 'you_sent' in parsed) {
                echoPayload = JSON.stringify(parsed.you_sent)
              } else {
                echoPayload = event.data
              }
            } catch {
              echoPayload = event.data
            }
          } else {
            echoPayload = '[binary payload]'
          }

          settle(() =>
            resolve({
              timestamp: Date.now(),
              roundtripMs: Math.round(performance.now() - start),
              echoPayload,
            }),
          )
        }

        const handleError = () => {
          settle(() => reject(new Error('WebSocket connection error.')))
        }

        const handleClose = (event: CloseEvent) => {
          settle(() => {
            if (event.code === 4401) {
              reject(new Error('WebSocket closed (4401): please sign in and retry.'))
              return
            }

            if (event.reason) {
              reject(new Error(`WebSocket closed: ${event.reason}`))
              return
            }

            reject(new Error(`WebSocket closed unexpectedly (code ${event.code}).`))
          })
        }

        socket.addEventListener('open', handleOpen)
        socket.addEventListener('message', handleMessage)
        socket.addEventListener('error', handleError)
        socket.addEventListener('close', handleClose)
      })

      setSnapshot(result)
      setStatus('success')
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Unable to complete WebSocket test.'
      setErrorMessage(message)
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    if (!autoStart) {
      return
    }

    void runProbe()
  }, [autoStart, runProbe])

  return {
    status,
    snapshot,
    errorMessage,
    runProbe,
  }
}

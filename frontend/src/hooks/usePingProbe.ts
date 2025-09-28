import { useCallback, useEffect, useState } from 'react'

import { fetchPing, type PingResponse } from '../api/ping'

export type PingStatus = 'idle' | 'loading' | 'success' | 'error'

export type PingSnapshot = {
  timestamp: number
  payload: PingResponse
}

export function usePingProbe(autoStart: boolean = true) {
  const [status, setStatus] = useState<PingStatus>('idle')
  const [snapshot, setSnapshot] = useState<PingSnapshot | undefined>()
  const [errorMessage, setErrorMessage] = useState<string | undefined>()

  const runPing = useCallback(async () => {
    setStatus('loading')
    setErrorMessage(undefined)

    try {
      const payload = await fetchPing()
      setSnapshot({
        timestamp: Date.now(),
        payload,
      })
      setStatus('success')
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Something went wrong contacting the API.'
      setErrorMessage(message)
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    if (!autoStart) {
      return
    }

    void runPing()
  }, [autoStart, runPing])

  return {
    status,
    snapshot,
    errorMessage,
    runPing,
  }
}

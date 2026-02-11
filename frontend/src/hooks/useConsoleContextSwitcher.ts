import { useCallback, useEffect, useRef, useState } from 'react'

import {
  fetchConsoleContext,
  switchConsoleContext,
  type ConsoleContext,
  type ConsoleContextData,
} from '../api/context'
import { storeConsoleContext } from '../util/consoleContextStorage'

type UseConsoleContextSwitcherOptions = {
  enabled?: boolean
  forAgentId?: string
  onSwitched?: (context: ConsoleContext) => void
  persistSession?: boolean
}

type UseConsoleContextSwitcherResult = {
  data: ConsoleContextData | null
  isLoading: boolean
  isSwitching: boolean
  error: string | null
  switchContext: (context: ConsoleContext) => Promise<void>
  refresh: () => Promise<void>
}

export function useConsoleContextSwitcher({
  enabled = false,
  forAgentId,
  onSwitched,
  persistSession = true,
}: UseConsoleContextSwitcherOptions): UseConsoleContextSwitcherResult {
  const [data, setData] = useState<ConsoleContextData | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isSwitching, setIsSwitching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)
  const requestIdRef = useRef(0)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const refresh = useCallback(async () => {
    if (!enabled) {
      return
    }
    const requestId = ++requestIdRef.current
    setIsLoading(true)
    setError(null)
    try {
      const payload = await fetchConsoleContext({ forAgentId })
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return
      }
      setData(payload)
      setIsLoading(false)
      storeConsoleContext(payload.context)
    } catch (err) {
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return
      }
      console.error('Failed to load context switcher data:', err)
      setError('Unable to load workspace contexts.')
      setIsLoading(false)
    }
  }, [enabled, forAgentId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const switchContext = useCallback(
    async (context: ConsoleContext) => {
      if (!data || isSwitching) {
        return
      }
      const previousContext = data.context
      setIsSwitching(true)
      setError(null)
      setData({ ...data, context })
      storeConsoleContext(context)
      try {
        const updated = await switchConsoleContext(context, { persistSession })
        if (!mountedRef.current) {
          return
        }
        setData((prev) => (prev ? { ...prev, context: updated } : prev))
        storeConsoleContext(updated)
        onSwitched?.(updated)
      } catch (err) {
        if (!mountedRef.current) {
          return
        }
        console.error('Failed to switch context:', err)
        setData((prev) => (prev ? { ...prev, context: previousContext } : prev))
        storeConsoleContext(previousContext)
        setError('Unable to switch context.')
      } finally {
        if (mountedRef.current) {
          setIsSwitching(false)
        }
      }
    },
    [data, isSwitching, onSwitched, persistSession],
  )

  return {
    data,
    isLoading,
    isSwitching,
    error,
    switchContext,
    refresh,
  }
}

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  fetchConsoleContext,
  switchConsoleContext,
  type ConsoleContext,
  type ConsoleContextData,
} from '../api/context'

type UseConsoleContextSwitcherOptions = {
  enabled?: boolean
  onSwitched?: (context: ConsoleContext) => void
}

type UseConsoleContextSwitcherResult = {
  data: ConsoleContextData | null
  isLoading: boolean
  isSwitching: boolean
  error: string | null
  switchContext: (context: ConsoleContext) => Promise<void>
}

function storeContext(context: ConsoleContext) {
  if (typeof window === 'undefined') {
    return
  }
  localStorage.setItem('contextType', context.type)
  localStorage.setItem('contextId', context.id)
  localStorage.setItem('contextName', context.name)
}

export function useConsoleContextSwitcher({
  enabled = false,
  onSwitched,
}: UseConsoleContextSwitcherOptions): UseConsoleContextSwitcherResult {
  const [data, setData] = useState<ConsoleContextData | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isSwitching, setIsSwitching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      return
    }
    let active = true
    setIsLoading(true)
    setError(null)
    fetchConsoleContext()
      .then((payload) => {
        if (!active) return
        setData(payload)
        setIsLoading(false)
        storeContext(payload.context)
      })
      .catch((err) => {
        if (!active) return
        console.error('Failed to load context switcher data:', err)
        setError('Unable to load workspace contexts.')
        setIsLoading(false)
      })
    return () => {
      active = false
    }
  }, [enabled])

  const switchContext = useCallback(
    async (context: ConsoleContext) => {
      if (!data || isSwitching) {
        return
      }
      const previousContext = data.context
      setIsSwitching(true)
      setError(null)
      setData({ ...data, context })
      storeContext(context)
      try {
        const updated = await switchConsoleContext(context)
        if (!mountedRef.current) {
          return
        }
        setData((prev) => (prev ? { ...prev, context: updated } : prev))
        storeContext(updated)
        onSwitched?.(updated)
      } catch (err) {
        if (!mountedRef.current) {
          return
        }
        console.error('Failed to switch context:', err)
        setData((prev) => (prev ? { ...prev, context: previousContext } : prev))
        storeContext(previousContext)
        setError('Unable to switch context.')
      } finally {
        if (mountedRef.current) {
          setIsSwitching(false)
        }
      }
    },
    [data, isSwitching, onSwitched],
  )

  return {
    data,
    isLoading,
    isSwitching,
    error,
    switchContext,
  }
}

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  createOrganization,
  fetchConsoleContext,
  switchConsoleContext,
  type ConsoleContext,
  type ConsoleContextData,
  type ConsoleContextOption,
} from '../api/context'
import { readStoredConsoleContext, storeConsoleContext } from '../util/consoleContextStorage'

type UseConsoleContextSwitcherOptions = {
  enabled?: boolean
  forAgentId?: string
  onSwitched?: (context: ConsoleContext) => void
  persistSession?: boolean
}

type UseConsoleContextSwitcherResult = {
  data: ConsoleContextData | null
  resolvedForAgentId?: string
  isLoading: boolean
  isSwitching: boolean
  error: string | null
  switchContext: (context: ConsoleContext) => Promise<void>
  createOrganizationContext: (name: string) => Promise<ConsoleContext>
  refresh: () => Promise<void>
}

export function useConsoleContextSwitcher({
  enabled = false,
  forAgentId,
  onSwitched,
  persistSession = true,
}: UseConsoleContextSwitcherOptions): UseConsoleContextSwitcherResult {
  const [data, setData] = useState<ConsoleContextData | null>(null)
  const [resolvedForAgentId, setResolvedForAgentId] = useState<string | undefined>(undefined)
  const [isLoading, setIsLoading] = useState(false)
  const [isSwitching, setIsSwitching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)
  const requestIdRef = useRef(0)
  const dataRef = useRef<ConsoleContextData | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    dataRef.current = data
  }, [data])

  const refresh = useCallback(async () => {
    if (!enabled) {
      return
    }
    if (!forAgentId && dataRef.current) {
      setResolvedForAgentId(undefined)
      setIsLoading(false)
      setError(null)
      return
    }
    const requestId = ++requestIdRef.current
    const requestForAgentId = forAgentId
    setIsLoading(true)
    setError(null)
    try {
      const payload = await fetchConsoleContext({ forAgentId: requestForAgentId })
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return
      }
      setData(payload)
      setResolvedForAgentId(requestForAgentId)
      setIsLoading(false)
      const stored = readStoredConsoleContext()
      if (
        !stored
        || stored.type !== payload.context.type
        || stored.id !== payload.context.id
        || (stored.name ?? null) !== (payload.context.name ?? null)
      ) {
        storeConsoleContext(payload.context)
      }
    } catch (err) {
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return
      }
      console.error('Failed to load context switcher data:', err)
      setError('Unable to load workspace contexts.')
      setResolvedForAgentId(undefined)
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
      const previousResolvedForAgentId = resolvedForAgentId
      const requestId = ++requestIdRef.current
      setIsSwitching(true)
      setError(null)
      setData({ ...data, context })
      setResolvedForAgentId(undefined)
      storeConsoleContext(context)
      try {
        const updated = await switchConsoleContext(context, { persistSession })
        if (!mountedRef.current || requestId !== requestIdRef.current) {
          return
        }
        setData((prev) => (prev ? { ...prev, context: updated } : prev))
        setResolvedForAgentId(undefined)
        storeConsoleContext(updated)
        onSwitched?.(updated)
      } catch (err) {
        if (!mountedRef.current || requestId !== requestIdRef.current) {
          return
        }
        console.error('Failed to switch context:', err)
        setData((prev) => (prev ? { ...prev, context: previousContext } : prev))
        setResolvedForAgentId(previousResolvedForAgentId)
        storeConsoleContext(previousContext)
        setError('Unable to switch context.')
      } finally {
        if (mountedRef.current && requestId === requestIdRef.current) {
          setIsSwitching(false)
        }
      }
    },
    [data, isSwitching, onSwitched, persistSession, resolvedForAgentId],
  )

  const createOrganizationContext = useCallback(
    async (name: string) => {
      if (isSwitching) {
        throw new Error('Context switch already in progress.')
      }
      const requestId = ++requestIdRef.current
      setIsSwitching(true)
      setError(null)
      try {
        const created = await createOrganization(name)
        if (!mountedRef.current || requestId !== requestIdRef.current) {
          return created.context
        }
        const nextOrganization: ConsoleContextOption = created.organization
        setData((prev) => {
          if (!prev) {
            return prev
          }
          const organizations = [
            ...prev.organizations.filter((org) => org.id !== nextOrganization.id),
            nextOrganization,
          ].sort((left, right) => left.name.localeCompare(right.name))
          return {
            ...prev,
            context: created.context,
            organizations,
            organizationsEnabled: true,
          }
        })
        setResolvedForAgentId(undefined)
        storeConsoleContext(created.context)
        onSwitched?.(created.context)
        return created.context
      } catch (err) {
        if (mountedRef.current && requestId === requestIdRef.current) {
          console.error('Failed to create organization:', err)
          setError('Unable to create organization.')
        }
        throw err
      } finally {
        if (mountedRef.current && requestId === requestIdRef.current) {
          setIsSwitching(false)
        }
      }
    },
    [isSwitching, onSwitched],
  )

  return {
    data,
    resolvedForAgentId,
    isLoading,
    isSwitching,
    error,
    switchContext,
    createOrganizationContext,
    refresh,
  }
}

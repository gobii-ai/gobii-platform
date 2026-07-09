import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { createOrganization, fetchConsoleContext, switchConsoleContext, type ConsoleContext, type ConsoleContextData, type ConsoleContextOption } from '../api/context'
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

const CONSOLE_CONTEXT_QUERY_KEY = ['console-context'] as const

export function consoleContextQueryKey(forAgentId?: string) {
  return [...CONSOLE_CONTEXT_QUERY_KEY, forAgentId ?? null] as const
}

function notifyConsoleContextUpdated(context: ConsoleContext): void {
  if (typeof window === 'undefined') {
    return
  }
  window.dispatchEvent(new CustomEvent('gobii:console-context-updated', { detail: context }))
}

export function useConsoleContextSwitcher({
  enabled = false,
  forAgentId,
  onSwitched,
  persistSession = true,
}: UseConsoleContextSwitcherOptions): UseConsoleContextSwitcherResult {
  const [isSwitching, setIsSwitching] = useState(false)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const mountedRef = useRef(true)
  const queryKey = useMemo(() => consoleContextQueryKey(forAgentId), [forAgentId])
  const contextQuery = useQuery({
    queryKey,
    queryFn: () => fetchConsoleContext({ forAgentId }),
    enabled,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })
  const { data: queryData, error: queryError, isLoading, refetch } = contextQuery
  const data = queryData ?? null
  const resolvedForAgentId = data ? forAgentId : undefined
  const loadError = queryError ? 'Unable to load workspace contexts.' : null

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!queryError) {
      return
    }
    console.error('Failed to load context switcher data:', queryError)
  }, [queryError])

  useEffect(() => {
    if (!data) {
      return
    }
    const stored = readStoredConsoleContext()
    if (
      !stored
      || stored.type !== data.context.type
      || stored.id !== data.context.id
      || (stored.name ?? null) !== (data.context.name ?? null)
    ) {
      storeConsoleContext(data.context)
    }
  }, [data])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined
    }
    const handleContextUpdated = (event: Event) => {
      const detail = (event as CustomEvent<ConsoleContext>).detail
      if (!detail || !detail.type || !detail.id) {
        return
      }
      queryClient.setQueriesData<ConsoleContextData>(
        { queryKey: CONSOLE_CONTEXT_QUERY_KEY },
        (prev) => (
          prev
            ? {
                ...prev,
                context: detail,
                organizations: detail.type === 'organization'
                  ? prev.organizations.map((org) => (org.id === detail.id ? { ...org, name: detail.name } : org))
                  : prev.organizations,
              }
            : prev
        ),
      )
      storeConsoleContext(detail)
    }
    window.addEventListener('gobii:console-context-updated', handleContextUpdated)
    return () => {
      window.removeEventListener('gobii:console-context-updated', handleContextUpdated)
    }
  }, [queryClient])

  const refresh = useCallback(async () => {
    if (!enabled) {
      return
    }
    setMutationError(null)
    await refetch()
  }, [enabled, refetch])

  const switchContext = useCallback(
    async (context: ConsoleContext) => {
      if (!data || isSwitching) {
        return
      }
      const previousContext = data.context
      setIsSwitching(true)
      setMutationError(null)
      queryClient.setQueryData<ConsoleContextData>(queryKey, { ...data, context })
      storeConsoleContext(context)
      try {
        const updated = await switchConsoleContext(context, { persistSession })
        if (!mountedRef.current) {
          return
        }
        queryClient.setQueryData<ConsoleContextData>(
          queryKey,
          (prev) => (prev ? { ...prev, context: updated } : prev),
        )
        storeConsoleContext(updated)
        notifyConsoleContextUpdated(updated)
        onSwitched?.(updated)
      } catch (err) {
        if (!mountedRef.current) {
          return
        }
        console.error('Failed to switch context:', err)
        queryClient.setQueryData<ConsoleContextData>(
          queryKey,
          (prev) => (prev ? { ...prev, context: previousContext } : prev),
        )
        storeConsoleContext(previousContext)
        setMutationError('Unable to switch context.')
      } finally {
        if (mountedRef.current) {
          setIsSwitching(false)
        }
      }
    },
    [data, isSwitching, onSwitched, persistSession, queryClient, queryKey],
  )

  const createOrganizationContext = useCallback(
    async (name: string) => {
      if (isSwitching) {
        throw new Error('Context switch already in progress.')
      }
      setIsSwitching(true)
      setMutationError(null)
      try {
        const created = await createOrganization(name)
        if (!mountedRef.current) {
          return created.context
        }
        const nextOrganization: ConsoleContextOption = created.organization
        queryClient.setQueryData<ConsoleContextData>(queryKey, (prev) => {
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
        storeConsoleContext(created.context)
        notifyConsoleContextUpdated(created.context)
        onSwitched?.(created.context)
        return created.context
      } catch (err) {
        if (mountedRef.current) {
          console.error('Failed to create organization:', err)
          setMutationError('Unable to create organization.')
        }
        throw err
      } finally {
        if (mountedRef.current) {
          setIsSwitching(false)
        }
      }
    },
    [isSwitching, onSwitched, queryClient, queryKey],
  )

  return {
    data,
    resolvedForAgentId,
    isLoading,
    isSwitching,
    error: mutationError ?? loadError,
    switchContext,
    createOrganizationContext,
    refresh,
  }
}

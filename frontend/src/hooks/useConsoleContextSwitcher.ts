import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { createOrganization, fetchConsoleContext, switchConsoleContext, type ConsoleContext, type ConsoleContextData, type ConsoleContextOption, type StaffViewContext } from '../api/context'
import { applyAnalyticsBillingContext, type AnalyticsBillingContext } from '../util/analytics'
import { readStoredConsoleContext, storeConsoleContext } from '../util/consoleContextStorage'

type UseConsoleContextSwitcherOptions = {
  enabled?: boolean
  forAgentId?: string
  onSwitched?: (context: ConsoleContext) => void
  persistSession?: boolean
  staffContext?: StaffViewContext | null
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

export function consoleContextQueryKey(forAgentId?: string, staffContext?: StaffViewContext | null) {
  return [
    ...CONSOLE_CONTEXT_QUERY_KEY,
    forAgentId ?? null,
    staffContext?.type ?? null,
    staffContext?.id ?? null,
  ] as const
}

type ConsoleContextUpdatedDetail = ConsoleContext & {
  billingContext?: AnalyticsBillingContext
}

function notifyConsoleContextUpdated(
  context: ConsoleContext,
  billingContext: AnalyticsBillingContext,
): void {
  if (typeof window === 'undefined') {
    return
  }
  window.dispatchEvent(new CustomEvent('gobii:console-context-updated', {
    detail: { ...context, billingContext },
  }))
}

export function useConsoleContextSwitcher({
  enabled = false,
  forAgentId,
  onSwitched,
  persistSession = true,
  staffContext = null,
}: UseConsoleContextSwitcherOptions): UseConsoleContextSwitcherResult {
  const [isSwitching, setIsSwitching] = useState(false)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const mountedRef = useRef(true)
  const appliedBillingContextRef = useRef<AnalyticsBillingContext | null>(null)
  const queryKey = useMemo(
    () => consoleContextQueryKey(forAgentId, staffContext),
    [forAgentId, staffContext],
  )
  const contextQuery = useQuery({
    queryKey,
    queryFn: () => fetchConsoleContext({ forAgentId, staffContext }),
    enabled,
    staleTime: 60_000,
    // Everything downstream — timeline, subscriptions, web session — is gated on this
    // query, and a cold-session failure used to wedge the whole chat with no retry
    // path (bug #472). Keep retrying on focus/reconnect so the wedge self-heals.
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: 3,
  })
  const { data: queryData, error: queryError, isLoading, refetch } = contextQuery
  const data = queryData ?? null
  const activeBillingContext = data?.billingContext
  const resolvedForAgentId = data ? forAgentId : undefined
  const loadError = queryError ? 'Unable to load workspace contexts.' : null
  const applyBillingContext = useCallback((billingContext: AnalyticsBillingContext) => {
    if (appliedBillingContextRef.current === billingContext) {
      return
    }
    applyAnalyticsBillingContext(billingContext)
    appliedBillingContextRef.current = billingContext
  }, [])

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

  useLayoutEffect(() => {
    if (activeBillingContext) {
      applyBillingContext(activeBillingContext)
    }
  }, [activeBillingContext, applyBillingContext])

  useEffect(() => {
    if (!data || data.context.isStaffView) {
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
      const detail = (event as CustomEvent<ConsoleContextUpdatedDetail>).detail
      if (!detail || !detail.type || !detail.id) {
        return
      }
      const { billingContext, ...updatedContext } = detail
      queryClient.setQueriesData<ConsoleContextData>(
        { queryKey: CONSOLE_CONTEXT_QUERY_KEY },
        (prev) => {
          if (!prev) {
            return prev
          }
          const contextChanged = prev.context.type !== detail.type || prev.context.id !== detail.id
          return {
            ...prev,
            context: updatedContext,
            billingContext: billingContext ?? (contextChanged ? {} : prev.billingContext),
            organizations: detail.type === 'organization'
              ? prev.organizations.map((org) => (org.id === detail.id ? { ...org, name: detail.name } : org))
              : prev.organizations,
          }
        },
      )
      storeConsoleContext(updatedContext)
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
      setIsSwitching(true)
      setMutationError(null)
      try {
        const result = await switchConsoleContext(context, { persistSession })
        applyBillingContext(result.billingContext)
        if (!mountedRef.current) {
          return
        }
        const updated = result.context
        queryClient.setQueryData<ConsoleContextData>(
          queryKey,
          (prev) => (prev ? {
            ...prev,
            context: updated,
            billingContext: result.billingContext,
          } : prev),
        )
        storeConsoleContext(updated)
        notifyConsoleContextUpdated(updated, result.billingContext)
        onSwitched?.(updated)
      } catch (err) {
        if (!mountedRef.current) {
          return
        }
        console.error('Failed to switch context:', err)
        setMutationError('Unable to switch context.')
      } finally {
        if (mountedRef.current) {
          setIsSwitching(false)
        }
      }
    },
    [applyBillingContext, data, isSwitching, onSwitched, persistSession, queryClient, queryKey],
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
        applyBillingContext(created.billingContext)
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
            billingContext: created.billingContext,
            organizations,
            organizationsEnabled: true,
          }
        })
        storeConsoleContext(created.context)
        notifyConsoleContextUpdated(created.context, created.billingContext)
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
    [applyBillingContext, isSwitching, onSwitched, queryClient, queryKey],
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

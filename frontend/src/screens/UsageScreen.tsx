import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {parseDate} from '@internationalized/date'

import {
  UsagePeriodHeader,
  UsageTrendSection,
  UsageMetricsGrid,
  UsageToolChart,
  UsageAgentLeaderboard,
  useUsageStore,
} from '../components/usage'
import {fetchUsageAgents} from '../components/usage/api'
import type {
  DateRangeValue,
  PeriodInfo,
  UsageAgent,
  UsageSummaryQueryInput,
} from '../components/usage'
import {
  cloneRange,
  areRangesEqual,
  getRangeLengthInDays,
  getAnchorDay,
  shiftBillingRange,
  shiftCustomRangeByDays,
} from '../components/usage/utils'


type SelectionMode = 'billing' | 'custom'

const formatContextCaption = (contextName: string, timezone: string): string => {
  const tzLabel = timezone || 'UTC'
  return `Context: ${contextName} · Timezone: ${tzLabel}`
}

export function UsageScreen() {
  const [appliedRange, setAppliedRange] = useState<DateRangeValue | null>(null)
  const [calendarRange, setCalendarRange] = useState<DateRangeValue | null>(null)
  const [isPickerOpen, setPickerOpen] = useState(false)
  const [selectionMode, setSelectionMode] = useState<SelectionMode>('billing')
  const [selectedAgentIds, setSelectedAgentIds] = useState<Set<string>>(new Set())

  const initialPeriodRef = useRef<DateRangeValue | null>(null)
  const anchorDayRef = useRef<number | null>(null)

  // Persisted API data so the UI stays responsive while React Query loads.
  const summary = useUsageStore((state) => state.summary)
  const summaryStatus = useUsageStore((state) => state.summaryStatus)
  const summaryErrorMessage = useUsageStore((state) => state.summaryErrorMessage)

  const agents = useUsageStore((state) => state.agents)
  const agentsStatus = useUsageStore((state) => state.agentsStatus)
  const agentsErrorMessage = useUsageStore((state) => state.agentsErrorMessage)

  const setAgentsLoading = useUsageStore((state) => state.setAgentsLoading)
  const setAgentsData = useUsageStore((state) => state.setAgentsData)
  const setAgentsError = useUsageStore((state) => state.setAgentsError)

  // Only request summary data when a concrete range has been selected.
  const queryInput = useMemo<UsageSummaryQueryInput>(() => {
    if (appliedRange?.start && appliedRange?.end) {
      return {
        from: appliedRange.start.toString(),
        to: appliedRange.end.toString(),
      }
    }
    return {}
  }, [appliedRange])

  // Agents are stable enough that we cache them globally and reuse between tabs.
  const agentsQuery = useQuery({
    queryKey: ['usage-agents'],
    queryFn: ({signal}) => fetchUsageAgents(signal),
    refetchOnWindowFocus: false,
  })

  // Keep the local store aligned with the React Query lifecycle so components can react synchronously.
  useEffect(() => {
    if (agentsQuery.isPending) {
      setAgentsLoading()
    }
  }, [agentsQuery.isPending, setAgentsLoading])

  // When the agents list changes, drop any stale selections the user can no longer access.
  useEffect(() => {
    if (agentsQuery.data) {
      setAgentsData(agentsQuery.data.agents)
    }
  }, [agentsQuery.data, setAgentsData])

  useEffect(() => {
    if (agentsQuery.isError) {
      const message =
        agentsQuery.error instanceof Error
          ? agentsQuery.error.message
          : 'Unable to load agents or API data right now.'
      setAgentsError(message)
    }
  }, [agentsQuery.error, agentsQuery.isError, setAgentsError])

  useEffect(() => {
    if (!agents.length) {
      if (selectedAgentIds.size) {
        setSelectedAgentIds(new Set())
      }
      return
    }

    const allowed = new Set(agents.map((agent: UsageAgent) => agent.id))
    let changed = false
    const next = new Set<string>()
    for (const id of selectedAgentIds) {
      if (allowed.has(id)) {
        next.add(id)
      } else {
        changed = true
      }
    }
    if (changed) {
      setSelectedAgentIds(next)
    }
  }, [agents, selectedAgentIds])

  // Convert the string dates coming from the API into calendar-aware values so range math stays accurate.
  const summaryRange = useMemo<DateRangeValue | null>(() => {
    if (!summary) {
      return null
    }
    return {
      start: parseDate(summary.period.start),
      end: parseDate(summary.period.end),
    }
  }, [summary])

  // The effective range is whichever range is currently applied: either the user override or the server billing window.
  const effectiveRange = useMemo<DateRangeValue | null>(() => {
    if (appliedRange) {
      return appliedRange
    }
    if (summaryRange) {
      return summaryRange
    }
    return null
  }, [appliedRange, summaryRange])

  // On first load, prime the local state with the billing period so pagination buttons work immediately.
  useEffect(() => {
    if (!summaryRange) {
      return
    }

    if (!initialPeriodRef.current) {
      initialPeriodRef.current = cloneRange(summaryRange)
    }

    if (anchorDayRef.current == null) {
      anchorDayRef.current = getAnchorDay(summaryRange)
    }

    if (!appliedRange) {
      setAppliedRange(cloneRange(summaryRange))
      setSelectionMode('billing')
    }
  }, [appliedRange, summaryRange])

  const applyRange = useCallback((range: DateRangeValue, mode: SelectionMode) => {
    const nextRange = cloneRange(range)
    setAppliedRange(nextRange)
    setSelectionMode(mode)
    setCalendarRange(null)
    setPickerOpen(false)

    if (mode === 'billing') {
      anchorDayRef.current = anchorDayRef.current ?? getAnchorDay(nextRange)
    }
  }, [])

  // Shift either by full billing cycles or by the length of the custom range the user picked.
  const handleShift = useCallback((direction: 'previous' | 'next') => {
    if (!effectiveRange) {
      return
    }

    if (
      direction === 'next' &&
      selectionMode === 'billing' &&
      initialPeriodRef.current &&
      areRangesEqual(effectiveRange, initialPeriodRef.current)
    ) {
      return
    }

    if (selectionMode === 'billing') {
      const anchorDay = anchorDayRef.current ?? getAnchorDay(effectiveRange)
      anchorDayRef.current = anchorDay
      const monthDelta = direction === 'next' ? 1 : -1
      const shifted = shiftBillingRange(effectiveRange, anchorDay, monthDelta)
      applyRange(shifted, 'billing')
      return
    }

    const length = getRangeLengthInDays(effectiveRange)
    const dayDelta = direction === 'next' ? length : -length
    const shifted = shiftCustomRangeByDays(effectiveRange, dayDelta)
    applyRange(shifted, 'custom')
  }, [applyRange, effectiveRange, selectionMode])

  const handleResetToCurrent = useCallback(() => {
    if (!initialPeriodRef.current) {
      return
    }
    const anchorDay = anchorDayRef.current ?? getAnchorDay(initialPeriodRef.current)
    anchorDayRef.current = anchorDay
    applyRange(initialPeriodRef.current, 'billing')
  }, [applyRange])

  const handlePickerOpenChange = useCallback((open: boolean) => {
    setPickerOpen(open)
    if (!open) {
      setCalendarRange(null)
    }
  }, [])

  const handleCustomRangePress = useCallback(() => {
    if (effectiveRange) {
      setCalendarRange(cloneRange(effectiveRange))
    }
    setPickerOpen(true)
  }, [effectiveRange])

  const handleAgentSelectionChange = useCallback((ids: Set<string>) => {
    setSelectedAgentIds(new Set(ids))
  }, [])

  const hasEffectiveRange = Boolean(effectiveRange)
  const hasInitialRange = Boolean(initialPeriodRef.current)
  const isCurrentSelection = Boolean(
    effectiveRange &&
    initialPeriodRef.current &&
    areRangesEqual(effectiveRange, initialPeriodRef.current),
  )
  const isViewingCurrentBilling = selectionMode === 'billing' && isCurrentSelection

  // Format the header caption so it calls out the active context and timezone.
  const periodInfo = useMemo<PeriodInfo>(() => {
    if (summary) {
      return {
        label: 'Billing period',
        value: summary.period.label,
        caption: formatContextCaption(summary.context.name, summary.period.timezone),
      }
    }

    if (summaryStatus === 'error') {
      return {
        label: 'Billing period',
        value: 'Unavailable',
        caption: 'Refresh the page to try loading usage data again.',
      }
    }

    return {
      label: 'Billing period',
      value: 'Loading…',
      caption: 'Fetching the current billing window.',
    }
  }, [summary, summaryStatus])

  const selectedAgentArray = useMemo(() => Array.from(selectedAgentIds).sort(), [selectedAgentIds])

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 py-8">
      <header className="flex flex-col gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-gray-800">Usage</h1>
          <p className="mt-2 text-base text-slate-600">
            Monitor agent and API activity alongside metered consumption for the current billing cycle.
          </p>
        </div>
        <UsagePeriodHeader
          periodInfo={periodInfo}
          isPickerOpen={isPickerOpen}
          onOpenChange={handlePickerOpenChange}
          onCustomRangePress={handleCustomRangePress}
          calendarRange={calendarRange}
          effectiveRange={effectiveRange}
          onCalendarChange={(range) => setCalendarRange(range)}
          onRangeComplete={(range) => applyRange(range, 'custom')}
          onPrevious={() => handleShift('previous')}
          onNext={() => handleShift('next')}
          onResetCurrent={handleResetToCurrent}
          hasEffectiveRange={hasEffectiveRange}
          hasInitialRange={hasInitialRange}
          isCurrentSelection={isCurrentSelection}
          isViewingCurrentBilling={isViewingCurrentBilling}
          agentSelectorProps={{
            agents,
            status: agentsStatus,
            errorMessage: agentsErrorMessage,
            selectedAgentIds,
            onSelectionChange: handleAgentSelectionChange,
            variant: 'condensed',
          }}
        />
      </header>

      <UsageMetricsGrid queryInput={queryInput} agentIds={selectedAgentArray}/>

      <UsageTrendSection
        effectiveRange={effectiveRange}
        fallbackRange={summaryRange}
        timezone={summary?.period.timezone}
        agentIds={selectedAgentArray}
      />

      <UsageToolChart
        effectiveRange={effectiveRange}
        fallbackRange={summaryRange}
        agentIds={selectedAgentArray}
        timezone={summary?.period.timezone}
      />

      <UsageAgentLeaderboard
        effectiveRange={effectiveRange}
        fallbackRange={summaryRange}
        agentIds={selectedAgentArray}
      />

      {summaryStatus === 'error' && summaryErrorMessage ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {summaryErrorMessage}
        </div>
      ) : null}
    </div>
  )
}

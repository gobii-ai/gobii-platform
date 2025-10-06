import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { parseDate } from '@internationalized/date'

import {
  UsagePeriodHeader,
  UsageTrendSection,
  UsageMetricsGrid,
  UsageAgentSelector,
  UsageToolChart,
  useUsageStore,
} from '../components/usage'
import { fetchUsageAgents } from '../components/usage/api'
import type {
  DateRangeValue,
  PeriodInfo,
  UsageAgent,
  UsageSummaryQueryInput,
  UsageTrendMode,
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
  const [trendMode, setTrendMode] = useState<UsageTrendMode>('month')
  const [selectedAgentIds, setSelectedAgentIds] = useState<Set<string>>(new Set())

  const initialPeriodRef = useRef<DateRangeValue | null>(null)
  const anchorDayRef = useRef<number | null>(null)

  const summary = useUsageStore((state) => state.summary)
  const summaryStatus = useUsageStore((state) => state.summaryStatus)
  const summaryErrorMessage = useUsageStore((state) => state.summaryErrorMessage)

  const agents = useUsageStore((state) => state.agents)
  const agentsStatus = useUsageStore((state) => state.agentsStatus)
  const agentsErrorMessage = useUsageStore((state) => state.agentsErrorMessage)

  const setAgentsLoading = useUsageStore((state) => state.setAgentsLoading)
  const setAgentsData = useUsageStore((state) => state.setAgentsData)
  const setAgentsError = useUsageStore((state) => state.setAgentsError)

  const queryInput = useMemo<UsageSummaryQueryInput>(() => {
    if (appliedRange?.start && appliedRange?.end) {
      return {
        from: appliedRange.start.toString(),
        to: appliedRange.end.toString(),
      }
    }
    return {}
  }, [appliedRange])

  const agentsQuery = useQuery({
    queryKey: ['usage-agents'],
    queryFn: ({ signal }) => fetchUsageAgents(signal),
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    if (agentsQuery.isPending) {
      setAgentsLoading()
    }
  }, [agentsQuery.isPending, setAgentsLoading])

  useEffect(() => {
    if (agentsQuery.data) {
      setAgentsData(agentsQuery.data.agents)
    }
  }, [agentsQuery.data, setAgentsData])

  useEffect(() => {
    if (agentsQuery.isError) {
      const message = agentsQuery.error instanceof Error ? agentsQuery.error.message : 'Unable to load agents right now.'
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

  const summaryRange = useMemo<DateRangeValue | null>(() => {
    if (!summary) {
      return null
    }
    return {
      start: parseDate(summary.period.start),
      end: parseDate(summary.period.end),
    }
  }, [summary])

  const effectiveRange = useMemo<DateRangeValue | null>(() => {
    if (appliedRange) {
      return appliedRange
    }
    if (summaryRange) {
      return summaryRange
    }
    return null
  }, [appliedRange, summaryRange])

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
          <h1 className="text-3xl font-semibold text-slate-900">Usage</h1>
          <p className="mt-2 text-base text-slate-600">
            Monitor agent activity and metered consumption for the current billing cycle.
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
        />
        <UsageAgentSelector
          agents={agents}
          status={agentsStatus}
          errorMessage={agentsErrorMessage}
          selectedAgentIds={selectedAgentIds}
          onSelectionChange={handleAgentSelectionChange}
        />
      </header>

      <UsageMetricsGrid queryInput={queryInput} agentIds={selectedAgentArray} />

      <UsageTrendSection
        trendMode={trendMode}
        onTrendModeChange={setTrendMode}
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

      {summaryStatus === 'error' && summaryErrorMessage ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {summaryErrorMessage}
        </div>
      ) : null}
    </div>
  )
}

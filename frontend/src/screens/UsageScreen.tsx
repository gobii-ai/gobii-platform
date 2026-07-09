import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getLocalTimeZone, parseDate, today } from '@internationalized/date'

import { UsagePeriodHeader, UsageTrendSection, UsageMetricsGrid, UsageAgentLeaderboard } from '../components/usage'
import { fetchUsageAgents } from '../components/usage/api'
import type { DateRangeValue, PeriodInfo, UsageAgent, UsageStatus, UsageSummaryQueryInput, UsageSummaryResponse } from '../components/usage'
import { cloneRange, areRangesEqual, getRangeLengthInDays, getAnchorDay, shiftBillingRange, shiftCustomRangeByDays, clampRangeToMax } from '../components/usage/utils'
import { SettingsBanner } from '../components/agentSettings/SettingsBanner'
import { InlineStatusBanner } from '../components/common/InlineStatusBanner'


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
  const [summary, setSummary] = useState<UsageSummaryResponse | null>(null)
  const [summaryStatus, setSummaryStatus] = useState<UsageStatus>('idle')
  const [summaryErrorMessage, setSummaryErrorMessage] = useState<string | null>(null)
  const [agents, setAgents] = useState<UsageAgent[]>([])
  const [agentsStatus, setAgentsStatus] = useState<UsageStatus>('idle')
  const [agentsErrorMessage, setAgentsErrorMessage] = useState<string | null>(null)

  const initialPeriodRef = useRef<DateRangeValue | null>(null)
  const anchorDayRef = useRef<number | null>(null)

  // Agents are stable enough that we cache them globally and reuse between tabs.
  const agentsQuery = useQuery({
    queryKey: ['usage-agents'],
    queryFn: ({signal}) => fetchUsageAgents(signal),
    refetchOnWindowFocus: false,
  })

  // Keep local display state aligned with the React Query lifecycle so the page can retain prior data while refreshing.
  useEffect(() => {
    if (agentsQuery.isPending) {
      setAgentsStatus('loading')
      setAgentsErrorMessage(null)
    }
  }, [agentsQuery.isPending])

  // When the agents list changes, drop any stale selections the user can no longer access.
  useEffect(() => {
    if (agentsQuery.data) {
      setAgents(agentsQuery.data.agents)
      setAgentsStatus('success')
      setAgentsErrorMessage(null)
    }
  }, [agentsQuery.data])

  useEffect(() => {
    if (agentsQuery.isError) {
      const message =
        agentsQuery.error instanceof Error
          ? agentsQuery.error.message
          : 'Unable to load agents or API data right now.'
      setAgentsStatus('error')
      setAgentsErrorMessage(message)
    }
  }, [agentsQuery.error, agentsQuery.isError])

  const handleSummaryStatusChange = useCallback((status: UsageStatus, message: string | null = null) => {
    setSummaryStatus(status)
    setSummaryErrorMessage(message)
  }, [])

  const handleSummaryLoaded = useCallback((nextSummary: UsageSummaryResponse) => {
    setSummary(nextSummary)
    setSummaryStatus('success')
    setSummaryErrorMessage(null)
  }, [])

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

  const shouldClampToToday =
    selectionMode === 'billing' &&
    (isCurrentSelection || (!initialPeriodRef.current && !appliedRange && Boolean(summaryRange)))

  const maxCalendarValue = useMemo(() => {
    if (!shouldClampToToday) {
      return null
    }
    const timezone = summary?.period.timezone ?? getLocalTimeZone()
    return today(timezone)
  }, [shouldClampToToday, summary?.period.timezone])

  const boundedEffectiveRange = useMemo<DateRangeValue | null>(() => {
    if (!effectiveRange) {
      return null
    }
    if (!maxCalendarValue) {
      return effectiveRange
    }
    return clampRangeToMax(effectiveRange, maxCalendarValue)
  }, [effectiveRange, maxCalendarValue])

  const boundedSummaryRange = useMemo<DateRangeValue | null>(() => {
    if (!summaryRange) {
      return null
    }
    if (!maxCalendarValue) {
      return summaryRange
    }
    return clampRangeToMax(summaryRange, maxCalendarValue)
  }, [maxCalendarValue, summaryRange])

  const queryInput = useMemo<UsageSummaryQueryInput>(() => {
    if (boundedEffectiveRange?.start && boundedEffectiveRange?.end) {
      return {
        from: boundedEffectiveRange.start.toString(),
        to: boundedEffectiveRange.end.toString(),
      }
    }
    return {}
  }, [boundedEffectiveRange])

  const handleCustomRangePress = useCallback(() => {
    const sourceRange = boundedEffectiveRange ?? effectiveRange
    if (sourceRange) {
      setCalendarRange(cloneRange(sourceRange))
    }
    setPickerOpen(true)
  }, [boundedEffectiveRange, effectiveRange])

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
  const description = 'Monitor agent and API activity alongside metered consumption for the current billing cycle.'

  return (
    <div className="flex w-full flex-col gap-5">
      <header className="flex flex-col gap-3">
        <SettingsBanner
          variant="embedded"
          eyebrow="Workspace"
          title="Usage"
          subtitle={description}
        />
        <UsagePeriodHeader
          periodInfo={periodInfo}
          isPickerOpen={isPickerOpen}
          onOpenChange={handlePickerOpenChange}
          onCustomRangePress={handleCustomRangePress}
          calendarRange={calendarRange}
          effectiveRange={boundedEffectiveRange}
          onCalendarChange={(range) => setCalendarRange(range)}
          onRangeComplete={(range) => applyRange(range, 'custom')}
          onPrevious={() => handleShift('previous')}
          onNext={() => handleShift('next')}
          onResetCurrent={handleResetToCurrent}
          hasEffectiveRange={hasEffectiveRange}
          hasInitialRange={hasInitialRange}
          isCurrentSelection={isCurrentSelection}
          isViewingCurrentBilling={isViewingCurrentBilling}
          maxValue={maxCalendarValue}
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

      <UsageMetricsGrid
        queryInput={queryInput}
        agentIds={selectedAgentArray}
        agents={agents}
        agentsStatus={agentsStatus}
        agentsErrorMessage={agentsErrorMessage}
        onSummaryStatusChange={handleSummaryStatusChange}
        onSummaryLoaded={handleSummaryLoaded}
      />

      <UsageTrendSection
        effectiveRange={boundedEffectiveRange}
        fallbackRange={boundedSummaryRange}
        timezone={summary?.period.timezone}
        agentIds={selectedAgentArray}
      />

      <UsageAgentLeaderboard
        effectiveRange={boundedEffectiveRange}
        fallbackRange={boundedSummaryRange}
        agentIds={selectedAgentArray}
      />

      {summaryStatus === 'error' && summaryErrorMessage ? (
        <InlineStatusBanner variant="error" surface="embedded">
          {summaryErrorMessage}
        </InlineStatusBanner>
      ) : null}
    </div>
  )
}

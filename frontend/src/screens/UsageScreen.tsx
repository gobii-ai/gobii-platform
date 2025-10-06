import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Button as AriaButton,
  CalendarCell,
  CalendarGrid,
  Dialog,
  DialogTrigger,
  Heading,
  Popover,
  RangeCalendar,
} from 'react-aria-components'
import { parseDate, type DateValue } from '@internationalized/date'

type UsageSummaryResponse = {
  period: {
    start: string
    end: string
    label: string
    timezone: string
  }
  context: {
    type: 'personal' | 'organization'
    id: string
    name: string
  }
  metrics: {
    tasks: {
      count: number
      completed: number
      in_progress: number
      pending: number
      failed: number
      cancelled: number
    }
    credits: {
      total: number
      unit: string
    }
    quota: {
      available: number
      total: number
      used: number
      used_pct: number
    }
  }
}

type MetricDefinition = {
  id: 'tasks' | 'credits' | 'quota'
  label: string
  baseCaption: string
}

type MetricCard = {
  id: MetricDefinition['id']
  label: string
  value: string
  caption: string
  valueClasses: string
  progressPct?: number
  progressClass?: string
}

type DateRangeValue = { start: DateValue; end: DateValue }

type UsageSummaryQueryInput = { from?: string; to?: string }
type UsageSummaryQueryKey = ['usage-summary', UsageSummaryQueryInput]
type SelectionMode = 'billing' | 'custom'

const cloneRange = (range: DateRangeValue): DateRangeValue => ({
  start: range.start.copy(),
  end: range.end.copy(),
})

const areRangesEqual = (a: DateRangeValue, b: DateRangeValue): boolean =>
  a.start.compare(b.start) === 0 && a.end.compare(b.end) === 0

const getRangeLengthInDays = (range: DateRangeValue): number => {
  const startJulian = range.start.calendar.toJulianDay(range.start)
  const endJulian = range.end.calendar.toJulianDay(range.end)
  return endJulian - startJulian + 1
}

const setDayWithClamp = (date: DateValue, day: number): DateValue => {
  const maxDay = date.calendar.getDaysInMonth(date)
  return date.set({ day: Math.min(day, maxDay) })
}

const getAnchorDay = (range: DateRangeValue): number => {
  const nextStart = range.end.add({ days: 1 })
  return Math.max(range.start.day, nextStart.day)
}

const computeBillingRangeFromStart = (start: DateValue, anchorDay: number): DateRangeValue => {
  const normalizedStart = setDayWithClamp(start, anchorDay)
  const nextStart = setDayWithClamp(normalizedStart.add({ months: 1 }), anchorDay)
  const normalizedEnd = nextStart.subtract({ days: 1 })
  return {
    start: normalizedStart,
    end: normalizedEnd,
  }
}

const shiftBillingRange = (range: DateRangeValue, anchorDay: number, months: number): DateRangeValue => {
  const shiftedStart = setDayWithClamp(range.start.add({ months }), anchorDay)
  return computeBillingRangeFromStart(shiftedStart, anchorDay)
}

const shiftCustomRangeByDays = (range: DateRangeValue, days: number): DateRangeValue => ({
  start: range.start.add({ days }),
  end: range.end.add({ days }),
})

const metricDefinitions: MetricDefinition[] = [
  {
    id: 'tasks',
    label: 'Tasks run',
    baseCaption: 'Counts every agent task created in the selected billing period.',
  },
  {
    id: 'credits',
    label: 'Credits consumed',
    baseCaption: 'Sum of task credits charged during this billing period.',
  },
  {
    id: 'quota',
    label: 'Quota remaining',
    baseCaption: 'Shows remaining task credits before throttling applies.',
  },
]

const fetchUsageSummary = async (params: UsageSummaryQueryInput, signal: AbortSignal): Promise<UsageSummaryResponse> => {
  const search = new URLSearchParams()

  if (params.from) {
    search.set('from', params.from)
  }

  if (params.to) {
    search.set('to', params.to)
  }

  const suffix = search.toString()
  const response = await fetch(`/console/api/usage/summary/${suffix ? `?${suffix}` : ''}`, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
    },
    signal,
  })

  if (!response.ok) {
    throw new Error(`Usage summary request failed (${response.status})`)
  }

  return response.json()
}

const formatContextCaption = (contextName: string, timezone: string): string => {
  const tzLabel = timezone || 'UTC'
  return `Context: ${contextName} · Timezone: ${tzLabel}`
}

export function UsageScreen() {
  const [appliedRange, setAppliedRange] = useState<DateRangeValue | null>(null)
  const [calendarRange, setCalendarRange] = useState<DateRangeValue | null>(null)
  const [isPickerOpen, setPickerOpen] = useState(false)
  const [selectionMode, setSelectionMode] = useState<SelectionMode>('billing')
  const initialPeriodRef = useRef<DateRangeValue | null>(null)
  const anchorDayRef = useRef<number | null>(null)

  const queryInput = useMemo<UsageSummaryQueryInput>(() => {
    if (appliedRange?.start && appliedRange?.end) {
      return {
        from: appliedRange.start.toString(),
        to: appliedRange.end.toString(),
      }
    }
    return {}
  }, [appliedRange])

  const queryKey: UsageSummaryQueryKey = ['usage-summary', queryInput]

  const {
    data: summary,
    error,
    isError,
    isPending,
  } = useQuery<UsageSummaryResponse, Error>({
    queryKey,
    queryFn: ({ signal }) => fetchUsageSummary(queryInput, signal),
    refetchOnWindowFocus: false,
  })

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
  }, [summaryRange, appliedRange])

  const applyRange = (range: DateRangeValue, mode: SelectionMode) => {
    const nextRange = cloneRange(range)
    setAppliedRange(nextRange)
    setSelectionMode(mode)
    setCalendarRange(null)
    setPickerOpen(false)

    if (mode === 'billing') {
      anchorDayRef.current = anchorDayRef.current ?? getAnchorDay(nextRange)
    }
  }

  const handleShift = (direction: 'previous' | 'next') => {
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
  }

  const handleResetToCurrent = () => {
    if (!initialPeriodRef.current) {
      return
    }
    const anchorDay = anchorDayRef.current ?? getAnchorDay(initialPeriodRef.current)
    anchorDayRef.current = anchorDay
    applyRange(initialPeriodRef.current, 'billing')
  }

  const hasEffectiveRange = Boolean(effectiveRange)
  const hasInitialRange = Boolean(initialPeriodRef.current)
  const isCurrentSelection = Boolean(
    effectiveRange &&
      initialPeriodRef.current &&
      areRangesEqual(effectiveRange, initialPeriodRef.current),
  )
  const isViewingCurrentBilling = selectionMode === 'billing' && isCurrentSelection

  const integerFormatter = useMemo(() => new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }), [])
  const creditFormatter = useMemo(
    () => new Intl.NumberFormat(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 3 }),
    [],
  )

  const errorMessage = useMemo(() => {
    if (!isError) {
      return null
    }

    if (error instanceof Error) {
      return error.message
    }

    return 'Unable to load usage metrics right now.'
  }, [error, isError])

  const periodInfo = useMemo(() => {
    if (summary) {
      return {
        label: 'Billing period',
        value: summary.period.label,
        caption: formatContextCaption(summary.context.name, summary.period.timezone),
      }
    }

    if (isError) {
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
  }, [isError, summary])

  const cards = useMemo<MetricCard[]>(() => {
    return metricDefinitions.map((metric) => {
      let value = '—'
      let caption = metric.baseCaption
      let valueClasses = 'text-slate-900'
      let progressPct: number | undefined
      let progressClass: string | undefined

      if (isPending) {
        value = 'Loading…'
        valueClasses = 'text-slate-400 animate-pulse'
      } else if (isError) {
        value = '—'
        valueClasses = 'text-slate-500'
        caption = 'Unable to load this metric. Refresh to retry.'
      } else if (summary) {
        switch (metric.id) {
          case 'tasks': {
            const completed = summary.metrics.tasks.completed
            const active = summary.metrics.tasks.in_progress + summary.metrics.tasks.pending
            value = integerFormatter.format(summary.metrics.tasks.count)
            caption = `Completed ${integerFormatter.format(completed)} · Active ${integerFormatter.format(active)}`
            break
          }
          case 'credits': {
            value = creditFormatter.format(summary.metrics.credits.total)
            caption = 'Credits billed across all tasks in this billing period.'
            break
          }
          case 'quota': {
            const available = summary.metrics.quota.available
            const total = summary.metrics.quota.total
            const used = summary.metrics.quota.used
            const usedPct = Math.round(summary.metrics.quota.used_pct)

            value = total > 0 ? creditFormatter.format(available) : '0'

            caption =
              total > 0
                ? `${creditFormatter.format(used)} used of ${creditFormatter.format(total)} credits (${usedPct}% used)`
                : 'No active quota for this context. Consider upgrading your plan.'
            progressPct = Math.max(0, Math.min(100, usedPct))
            if (progressPct >= 100) {
              progressClass = 'bg-gradient-to-r from-red-400 to-red-500'
            } else if (progressPct >= 90) {
              progressClass = 'bg-gradient-to-r from-orange-400 to-orange-500'
            } else {
              progressClass = 'bg-gradient-to-r from-blue-500 to-sky-500'
            }
            break
          }
          default:
            break
        }
      }

      return {
        id: metric.id,
        label: metric.label,
        value,
        caption,
        valueClasses,
        progressPct,
        progressClass,
      }
    })
  }, [creditFormatter, integerFormatter, isError, isPending, summary])

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-8 py-8">
      <header className="flex flex-col gap-3">
        <div>
          <h1 className="text-3xl font-semibold text-slate-900">Usage</h1>
          <p className="mt-2 text-base text-slate-600">
            Monitor agent activity and metered consumption for the current billing cycle.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
          <div className="flex flex-col">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {periodInfo.label}
            </span>
            <span className="text-lg font-medium text-slate-900">{periodInfo.value}</span>
            <span className="text-xs text-slate-500">{periodInfo.caption}</span>
          </div>
          <div className="h-10 w-px bg-slate-200" aria-hidden="true" />
          <div className="flex flex-1 flex-wrap items-center gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <AriaButton
                className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 disabled:cursor-not-allowed disabled:border-slate-100 disabled:bg-slate-50 disabled:text-slate-300"
                isDisabled={!hasEffectiveRange}
                onPress={() => handleShift('previous')}
              >
                ‹ Previous
              </AriaButton>
              <AriaButton
                className="rounded-md border border-transparent bg-blue-50 px-3 py-2 text-sm font-medium text-blue-600 transition-colors hover:bg-blue-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-300"
                isDisabled={!hasInitialRange || isCurrentSelection}
                onPress={handleResetToCurrent}
              >
                Current period
              </AriaButton>
              <AriaButton
                className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 disabled:cursor-not-allowed disabled:border-slate-100 disabled:bg-slate-50 disabled:text-slate-300"
                isDisabled={!hasEffectiveRange || isViewingCurrentBilling}
                onPress={() => handleShift('next')}
              >
                Next ›
              </AriaButton>
            </div>
            <div className="hidden h-10 w-px bg-slate-200 sm:block" aria-hidden="true" />
            <DialogTrigger
              isOpen={isPickerOpen}
              onOpenChange={(open) => {
                setPickerOpen(open)
                if (!open) {
                  setCalendarRange(null)
                }
              }}
            >
              <AriaButton
                className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:border-slate-300 hover:text-slate-700"
                onPress={() => {
                  setCalendarRange(effectiveRange ? cloneRange(effectiveRange) : null)
                  setPickerOpen(true)
                }}
              >
                Custom range
              </AriaButton>
              <Popover className="z-50 mt-2 rounded-xl border border-slate-200 bg-white shadow-xl">
                <Dialog className="p-4">
                  <RangeCalendar
                    aria-label="Select billing period"
                    value={(calendarRange ?? effectiveRange) ?? undefined}
                    onChange={(range) => {
                      if (range?.start && range?.end) {
                        const nextRange = range as DateRangeValue
                        setCalendarRange(nextRange)
                        applyRange(nextRange, 'custom')
                      } else {
                        setCalendarRange(range as DateRangeValue | null)
                      }
                    }}
                    visibleDuration={{ months: 1 }}
                    className="flex flex-col gap-3"
                  >
                    <header className="flex items-center justify-between gap-2">
                      <AriaButton slot="previous" className="rounded-md px-2 py-1 text-sm text-slate-600 transition-colors hover:bg-slate-100">
                        ‹
                      </AriaButton>
                      <Heading className="text-sm font-medium text-slate-700" />
                      <AriaButton slot="next" className="rounded-md px-2 py-1 text-sm text-slate-600 transition-colors hover:bg-slate-100">
                        ›
                      </AriaButton>
                    </header>
                    <CalendarGrid className="border-spacing-1 border-separate gap-y-1 text-center text-xs font-medium uppercase text-slate-500">
                      {(date) => (
                        <CalendarCell
                          date={date}
                          className="m-0.5 flex h-8 w-8 items-center justify-center rounded-md text-sm text-slate-700 transition-colors hover:bg-blue-100 data-[disabled]:text-slate-300 data-[focused]:outline data-[focused]:outline-2 data-[focused]:outline-blue-400 data-[selected]:bg-blue-600 data-[selected]:text-white data-[range-selection]:bg-blue-100 data-[outside-month]:text-slate-300"
                        />
                      )}
                    </CalendarGrid>
                  </RangeCalendar>
                </Dialog>
              </Popover>
            </DialogTrigger>
          </div>
        </div>
      </header>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <article
            key={card.id}
            data-usage-metric={card.id}
            className="flex h-full flex-col justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-5 shadow-sm"
          >
            <div>
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {card.label}
              </span>
              <p className={`mt-2 text-2xl font-semibold ${card.valueClasses}`}>{card.value}</p>
              {typeof card.progressPct === 'number' ? (
                <div className="mt-3">
                  <div className="relative h-2 rounded-full bg-slate-200/80">
                    <div
                      className={`absolute inset-y-0 left-0 rounded-full ${card.progressClass}`}
                      style={{ width: `${card.progressPct}%` }}
                    />
                  </div>
                  <span className="sr-only">{card.progressPct}% of quota used</span>
                </div>
              ) : null}
            </div>
            <p className="text-sm text-slate-500">{card.caption}</p>
          </article>
        ))}
      </section>

      {isError && errorMessage ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {errorMessage}
        </div>
      ) : null}
    </div>
  )
}

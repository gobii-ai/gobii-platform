import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { parseDate } from '@internationalized/date'

import { fetchUsageSummary } from './api'
import { useUsageStore } from './store'
import type {
  MetricCard,
  MetricDefinition,
  UsageSummaryQueryInput,
  UsageSummaryResponse,
} from './types'

const metricDefinitions: MetricDefinition[] = [
  {
    id: 'credits',
    label: 'Credits consumed',
    baseCaption: 'Sum of task credits charged during this billing period.',
  },
  {
    id: 'tasks_per_day',
    label: 'Average credits per day',
    baseCaption: 'Average task credits billed per day in the selected billing period.',
  },
  {
    id: 'quota',
    label: 'Current billing quota',
    baseCaption: 'Remaining task credits for the active billing cycle (not affected by date filters).',
  },
]

type UsageMetricsGridProps = {
  queryInput: UsageSummaryQueryInput
  agentIds: string[]
  embedded?: boolean
}

export function UsageMetricsGrid({ queryInput, agentIds, embedded = false }: UsageMetricsGridProps) {
  const setSummaryLoading = useUsageStore((state) => state.setSummaryLoading)
  const setSummaryData = useUsageStore((state) => state.setSummaryData)
  const setSummaryError = useUsageStore((state) => state.setSummaryError)
  const summary = useUsageStore((state) => state.summary)

  const agentKey = agentIds.length ? agentIds.slice().sort().join(',') : 'all'

  const {
    data,
    isPending,
    isError,
    error,
  } = useQuery<UsageSummaryResponse, Error>({
    queryKey: ['usage-summary', queryInput.from ?? null, queryInput.to ?? null, agentKey],
    queryFn: ({ signal }) => fetchUsageSummary({ ...queryInput, agents: agentIds }, signal),
    placeholderData: (previousData) => previousData,
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    if (isPending) {
      setSummaryLoading()
    }
  }, [isPending, setSummaryLoading])

  useEffect(() => {
    if (data) {
      setSummaryData(data)
    }
  }, [data, setSummaryData])

  useEffect(() => {
    if (isError) {
      const message = error instanceof Error ? error.message : 'Unable to load usage metrics right now.'
      setSummaryError(message)
    }
  }, [error, isError, setSummaryError])

  const creditFormatter = useMemo(
    () => new Intl.NumberFormat(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 3 }),
    [],
  )

  const resolvedSummary = data ?? summary

  const periodDayCount = useMemo(() => {
    const from = queryInput.from ?? resolvedSummary?.period.start
    const to = queryInput.to ?? resolvedSummary?.period.end
    if (!from || !to) {
      return null
    }

    try {
      const startDate = parseDate(from)
      const endDate = parseDate(to)
      const startJulian = startDate.calendar.toJulianDay(startDate)
      const endJulian = endDate.calendar.toJulianDay(endDate)
      const span = endJulian - startJulian + 1
      return span > 0 ? span : null
    } catch (error) {
      console.error('Failed to compute period length in days', error)
      return null
    }
  }, [queryInput.from, queryInput.to, resolvedSummary])

  const cards = useMemo<MetricCard[]>(() => {
    return metricDefinitions.map((metric) => {
      let value = '—'
      let caption = metric.baseCaption
      let valueClasses = embedded ? 'text-slate-50' : 'text-slate-900'
      let progressPct: number | undefined
      let progressClass: string | undefined

      if (isPending) {
        value = 'Loading…'
        valueClasses = 'text-slate-400 animate-pulse'
      } else if (isError) {
        value = '—'
        valueClasses = 'text-slate-500'
        caption = 'Unable to load this metric. Refresh to retry.'
      } else if (resolvedSummary) {
        switch (metric.id) {
          case 'tasks': {
            const completed = resolvedSummary.metrics.tasks.completed
            const active = resolvedSummary.metrics.tasks.in_progress + resolvedSummary.metrics.tasks.pending
            value = creditFormatter.format(resolvedSummary.metrics.tasks.count)
            caption = `Completed ${creditFormatter.format(completed)} credits · Active ${creditFormatter.format(active)} credits`
            break
          }
          case 'tasks_per_day': {
            const totalTasks = resolvedSummary.metrics.tasks.count
            if (periodDayCount && periodDayCount > 0) {
              const average = totalTasks / periodDayCount
              value = creditFormatter.format(average)
              const pluralSuffix = periodDayCount === 1 ? '' : 's'
              caption = `${creditFormatter.format(totalTasks)} credits across ${periodDayCount} day${pluralSuffix}.`
            } else {
              value = '—'
              caption = 'Unable to determine the period length for this metric.'
            }
            break
          }
          case 'credits': {
            value = creditFormatter.format(resolvedSummary.metrics.credits.total)
            caption = 'Credits billed across all tasks in this billing period.'
            break
          }
          case 'quota': {
            const available = resolvedSummary.metrics.quota.available
            const total = resolvedSummary.metrics.quota.total
            const used = resolvedSummary.metrics.quota.used
            const usedPctRaw = resolvedSummary.metrics.quota.used_pct
            const usedPct = Number.isFinite(usedPctRaw) ? Math.round(usedPctRaw) : 0
            const unlimitedQuota = total < 0 || available < 0
            const quotaCaptionSuffix = 'Current billing cycle; date filters do not change this value.'

            if (unlimitedQuota) {
              value = '∞'
              caption = `Unlimited task credits. ${quotaCaptionSuffix}`
            } else if (total > 0) {
              value = creditFormatter.format(available)

              caption = `${creditFormatter.format(used)} used of ${creditFormatter.format(total)} credits (${usedPct}% used). ${quotaCaptionSuffix}`
              progressPct = Math.max(0, Math.min(100, usedPct))
              if (progressPct >= 100) {
                progressClass = 'bg-gradient-to-r from-red-400 to-red-500'
              } else if (progressPct >= 90) {
                progressClass = 'bg-gradient-to-r from-orange-400 to-orange-500'
              } else {
                progressClass = 'bg-gradient-to-r from-blue-500 to-sky-500'
              }
            } else {
              value = '0'
              caption = 'No active quota for this context. Consider upgrading your plan.'
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
  }, [creditFormatter, embedded, isError, isPending, periodDayCount, resolvedSummary])

  const cardClassName = embedded
    ? 'settings-card-surface settings-card-surface--embedded flex h-full flex-col justify-between gap-3 rounded-xl border border-slate-200/20 p-5'
    : 'gobii-card-base flex h-full flex-col justify-between gap-3 p-5'
  const labelClassName = embedded
    ? 'text-xs font-semibold uppercase tracking-wide text-slate-400'
    : 'text-xs font-semibold uppercase tracking-wide text-slate-500'
  const captionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-500'
  const progressTrackClassName = embedded ? 'relative h-2 rounded-full bg-slate-900/70' : 'relative h-2 rounded-full bg-white/50'

  return (
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {cards.map((card) => (
        <article
          key={card.id}
          data-usage-metric={card.id}
          className={cardClassName}
        >
          <div>
            <span className={labelClassName}>
              {card.label}
            </span>
            <p className={`mt-2 text-2xl font-semibold ${card.valueClasses}`}>{card.value}</p>
            {typeof card.progressPct === 'number' ? (
              <div className="mt-3">
                <div className={progressTrackClassName}>
                  <div
                    className={`absolute inset-y-0 left-0 rounded-full ${card.progressClass ?? ''}`}
                    style={{ width: `${card.progressPct}%` }}
                  />
                </div>
                <span className="sr-only">{card.progressPct}% of quota used</span>
              </div>
            ) : null}
          </div>
          <p className={captionClassName}>{card.caption}</p>
        </article>
      ))}
    </section>
  )
}

export type { UsageMetricsGridProps }

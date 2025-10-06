import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

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

type UsageMetricsGridProps = {
  queryInput: UsageSummaryQueryInput
  agentIds: string[]
}

export function UsageMetricsGrid({ queryInput, agentIds }: UsageMetricsGridProps) {
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

  const integerFormatter = useMemo(() => new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }), [])
  const creditFormatter = useMemo(
    () => new Intl.NumberFormat(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 3 }),
    [],
  )

  const resolvedSummary = data ?? summary

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
      } else if (resolvedSummary) {
        switch (metric.id) {
          case 'tasks': {
            const completed = resolvedSummary.metrics.tasks.completed
            const active = resolvedSummary.metrics.tasks.in_progress + resolvedSummary.metrics.tasks.pending
            value = integerFormatter.format(resolvedSummary.metrics.tasks.count)
            caption = `Completed ${integerFormatter.format(completed)} · Active ${integerFormatter.format(active)}`
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
            const usedPct = Math.round(resolvedSummary.metrics.quota.used_pct)

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
  }, [creditFormatter, integerFormatter, isError, isPending, resolvedSummary])

  return (
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
                    className={`absolute inset-y-0 left-0 rounded-full ${card.progressClass ?? ''}`}
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
  )
}

export type { UsageMetricsGridProps }

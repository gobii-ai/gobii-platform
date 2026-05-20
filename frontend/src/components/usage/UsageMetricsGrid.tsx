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
    id: 'today_credits',
    label: 'Credits used today',
    baseCaption: 'Credits consumed since today started.',
  },
  {
    id: 'month_credits',
    label: 'Credits used this month',
    baseCaption: 'Credits consumed in the current billing period.',
  },
  {
    id: 'credits_remaining',
    label: 'Credits remaining',
    baseCaption: 'Credits left in the active billing cycle.',
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

  const todayResetLabel = useMemo(() => {
    const resetAt = resolvedSummary?.metrics.todayCredits?.resetAt
    if (!resolvedSummary?.period.timezone || !resetAt) {
      return 'Resets at midnight'
    }
    const reset = new Date(resetAt)
    if (Number.isNaN(reset.getTime())) {
      return 'Resets at midnight'
    }
    const resetTime = new Intl.DateTimeFormat(undefined, {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: resolvedSummary.period.timezone,
    }).format(reset).replace('AM', 'am').replace('PM', 'pm')
    return `Resets ${resetTime}`
  }, [resolvedSummary?.metrics.todayCredits?.resetAt, resolvedSummary?.period.timezone])

  const billingResetLabel = useMemo(() => {
    const resetOn = resolvedSummary?.period.resetOn
    if (!resetOn) {
      return 'Reset date unavailable'
    }
    const [year, month, day] = resetOn.split('-').map((part) => Number(part))
    const resetDate = year && month && day
      ? new Date(year, month - 1, day, 12)
      : new Date(resetOn)
    if (Number.isNaN(resetDate.getTime())) {
      return 'Reset date unavailable'
    }
    return `Resets ${new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
    }).format(resetDate)}`
  }, [resolvedSummary?.period.resetOn])

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
          case 'today_credits': {
            value = creditFormatter.format(resolvedSummary.metrics.todayCredits?.total ?? 0)
            caption = todayResetLabel
            break
          }
          case 'month_credits': {
            value = creditFormatter.format(resolvedSummary.metrics.credits.total)
            const usedPct = resolvedSummary.metrics.quota.used_pct
            caption = Number.isFinite(usedPct)
              ? `${Math.round(usedPct)}% of billing credits used. ${billingResetLabel}.`
              : billingResetLabel
            break
          }
          case 'credits_remaining': {
            const available = resolvedSummary.metrics.quota.available
            const total = resolvedSummary.metrics.quota.total
            const used = resolvedSummary.metrics.quota.used
            const usedPctRaw = resolvedSummary.metrics.quota.used_pct
            const usedPct = Number.isFinite(usedPctRaw) ? Math.round(usedPctRaw) : 0
            const unlimitedQuota = total < 0 || available < 0

            if (unlimitedQuota) {
              value = '∞'
              caption = `Unlimited task credits. ${billingResetLabel}.`
            } else if (total > 0) {
              value = creditFormatter.format(available)

              caption = `${creditFormatter.format(used)} used of ${creditFormatter.format(total)} credits. ${billingResetLabel}.`
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
  }, [billingResetLabel, creditFormatter, embedded, isError, isPending, resolvedSummary, todayResetLabel])

  const cardClassName = embedded
    ? 'flex h-full flex-col justify-between gap-3 rounded-xl border border-slate-200/20 bg-slate-950/35 p-5'
    : 'gobii-card-base flex h-full flex-col justify-between gap-3 p-5'
  const labelClassName = embedded
    ? 'text-xs font-semibold uppercase tracking-wide text-slate-400'
    : 'text-xs font-semibold uppercase tracking-wide text-slate-500'
  const captionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-500'
  const progressTrackClassName = embedded ? 'relative h-2 rounded-full bg-slate-900/70' : 'relative h-2 rounded-full bg-white/50'

  return (
    <section className="grid gap-4 md:grid-cols-3">
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

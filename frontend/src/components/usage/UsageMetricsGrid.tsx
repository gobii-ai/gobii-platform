import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { parseDate } from '@internationalized/date'

import { InsightGauge } from '../common/InsightGauge'
import { fetchUsageSummary } from './api'
import { useUsageStore } from './store'
import type {
  UsageSummaryQueryInput,
  UsageSummaryResponse,
} from './types'

const API_AGENT_ID = 'api'

type MetricCard = {
  id: 'credits_used' | 'average_credits_per_day' | 'agent_count'
  label: string
  value: string
  caption: string
  valueClasses: string
  gaugePct?: number
}

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
  const agents = useUsageStore((state) => state.agents)
  const agentsStatus = useUsageStore((state) => state.agentsStatus)
  const agentsErrorMessage = useUsageStore((state) => state.agentsErrorMessage)

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
    () => new Intl.NumberFormat(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }),
    [],
  )

  const resolvedSummary = data ?? summary
  const activeAgentCount = useMemo(
    () => agents.filter((agent) => agent.id !== API_AGENT_ID && !agent.is_deleted).length,
    [agents],
  )

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
    const defaultValueClasses = embedded ? 'text-slate-50' : 'text-slate-900'
    const loadingValueClasses = 'text-slate-400 animate-pulse'
    const errorValueClasses = 'text-slate-500'

    const cards: MetricCard[] = [
      {
        id: 'credits_used',
        label: 'Credits Used',
        value: isPending ? 'Loading…' : '—',
        caption: 'Current billing cycle; date filters do not change this value.',
        valueClasses: isPending ? loadingValueClasses : defaultValueClasses,
      },
      {
        id: 'average_credits_per_day',
        label: 'Average credits/day',
        value: isPending ? 'Loading…' : '—',
        caption: 'Average credits billed per day in the selected period.',
        valueClasses: isPending ? loadingValueClasses : defaultValueClasses,
      },
      {
        id: 'agent_count',
        label: 'Number of agents',
        value: agentsStatus === 'loading' && activeAgentCount === 0 ? 'Loading…' : creditFormatter.format(activeAgentCount),
        caption: 'Active agents in this context, excluding API usage.',
        valueClasses: agentsStatus === 'loading' && activeAgentCount === 0 ? loadingValueClasses : defaultValueClasses,
      },
    ]

    if (isError) {
      cards[0] = {
        ...cards[0],
        value: '—',
        caption: 'Unable to load this metric. Refresh to retry.',
        valueClasses: errorValueClasses,
      }
      cards[1] = {
        ...cards[1],
        value: '—',
        caption: 'Unable to load this metric. Refresh to retry.',
        valueClasses: errorValueClasses,
      }
    } else if (resolvedSummary) {
      const available = resolvedSummary.metrics.quota.available
      const total = resolvedSummary.metrics.quota.total
      const used = resolvedSummary.metrics.quota.used
      const usedPctRaw = resolvedSummary.metrics.quota.used_pct
      const usedPct = Number.isFinite(usedPctRaw) ? Math.round(usedPctRaw) : 0
      const unlimitedQuota = Boolean(resolvedSummary.metrics.quota.unlimited) || total < 0 || available < 0

      cards[0] = {
        ...cards[0],
        value: creditFormatter.format(used),
        caption: unlimitedQuota
          ? 'Unlimited task credits in the current billing cycle.'
          : total > 0
            ? `${creditFormatter.format(used)} used of ${creditFormatter.format(total)} credits.`
            : 'No active quota for this context. Consider upgrading your plan.',
        valueClasses: defaultValueClasses,
        gaugePct: !unlimitedQuota && total > 0 ? Math.max(0, Math.min(100, usedPct)) : undefined,
      }

      if (periodDayCount && periodDayCount > 0) {
        const totalCredits = resolvedSummary.metrics.credits.total
        const average = totalCredits / periodDayCount
        const pluralSuffix = periodDayCount === 1 ? '' : 's'
        cards[1] = {
          ...cards[1],
          value: creditFormatter.format(average),
          caption: `${creditFormatter.format(totalCredits)} credits across ${periodDayCount} day${pluralSuffix}.`,
          valueClasses: defaultValueClasses,
        }
      } else {
        cards[1] = {
          ...cards[1],
          caption: 'Unable to determine the period length for this metric.',
          valueClasses: errorValueClasses,
        }
      }
    }

    if (agentsStatus === 'error') {
      cards[2] = {
        ...cards[2],
        value: '—',
        caption: agentsErrorMessage || 'Unable to load agents right now.',
        valueClasses: errorValueClasses,
      }
    }

    return cards
  }, [activeAgentCount, agentsErrorMessage, agentsStatus, creditFormatter, embedded, isError, isPending, periodDayCount, resolvedSummary])

  const cardClassName = embedded
    ? 'settings-card-surface settings-card-surface--embedded flex h-full flex-col justify-between gap-3 rounded-xl border border-slate-200/20 p-5'
    : 'gobii-card-base flex h-full flex-col justify-between gap-3 p-5'
  const labelClassName = embedded
    ? 'text-xs font-semibold uppercase tracking-wide text-slate-400'
    : 'text-xs font-semibold uppercase tracking-wide text-slate-500'
  const captionClassName = embedded ? 'text-sm text-slate-400' : 'text-sm text-slate-500'

  return (
    <section className="grid gap-4 md:grid-cols-3">
      {cards.map((card) => (
        <article
          key={card.id}
          data-usage-metric={card.id}
          className={cardClassName}
        >
          <div className={card.gaugePct == null ? undefined : 'flex items-start justify-between gap-4'}>
            <div>
              <span className={labelClassName}>
                {card.label}
              </span>
              <p className={`mt-2 text-2xl font-semibold ${card.valueClasses}`}>{card.value}</p>
            </div>
            {typeof card.gaugePct === 'number' ? (
              <>
                <div className="relative h-24 w-24 shrink-0" aria-hidden="true">
                  <InsightGauge
                    value={card.gaugePct}
                    max={100}
                    size={96}
                    gradientColors={['#AA74CE', '#7C4CA0']}
                    thickness={8}
                    radius="94%"
                    showGlow={false}
                    animate={false}
                    trackColor={embedded ? 'rgba(170, 116, 206, 0.18)' : 'rgba(170, 116, 206, 0.16)'}
                  />
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className={`text-lg font-semibold ${embedded ? 'text-slate-50' : 'text-slate-900'}`}>
                      {Math.round(card.gaugePct)}
                    </span>
                    <span className={`mt-1 text-[10px] font-semibold ${embedded ? 'text-slate-400' : 'text-slate-500'}`}>
                      %
                    </span>
                  </div>
                </div>
                <span className="sr-only">{card.gaugePct}% of quota used</span>
              </>
            ) : null}
          </div>
          <p className={captionClassName}>{card.caption}</p>
        </article>
      ))}
    </section>
  )
}

export type { UsageMetricsGridProps }

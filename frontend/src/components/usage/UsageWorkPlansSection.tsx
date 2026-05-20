import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, CheckCircle2, CirclePause, LoaderCircle } from 'lucide-react'

import { fetchUsageWorkPlans } from './api'
import type { DateRangeValue, UsageWorkPlan, UsageWorkPlansQueryInput, UsageWorkPlansResponse } from './types'

type UsageWorkPlansSectionProps = {
  effectiveRange: DateRangeValue | null
  fallbackRange: DateRangeValue | null
  agentIds: string[]
  embedded?: boolean
}

const creditFormatter = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 1,
})

function formatCredits(value: number): string {
  if (!Number.isFinite(value)) {
    return '0'
  }
  return creditFormatter.format(value)
}

function statusIcon(status: UsageWorkPlan['status']) {
  if (status === 'completed') {
    return <CheckCircle2 size={14} aria-hidden="true" />
  }
  if (status === 'superseded') {
    return <CirclePause size={14} aria-hidden="true" />
  }
  return <LoaderCircle size={14} aria-hidden="true" />
}

export function UsageWorkPlansSection({
  effectiveRange,
  fallbackRange,
  agentIds,
  embedded = false,
}: UsageWorkPlansSectionProps) {
  const baseRange = effectiveRange ?? fallbackRange
  const queryInput = useMemo<UsageWorkPlansQueryInput | null>(() => {
    if (!baseRange) {
      return null
    }
    return {
      from: baseRange.start.toString(),
      to: baseRange.end.toString(),
      agents: agentIds,
    }
  }, [agentIds, baseRange])
  const agentKey = agentIds.length ? agentIds.slice().sort().join(',') : 'all'
  const { data, isPending, isError, error } = useQuery<UsageWorkPlansResponse, Error>({
    queryKey: ['usage-work-plans', queryInput?.from ?? null, queryInput?.to ?? null, agentKey],
    queryFn: ({ signal }) => fetchUsageWorkPlans(queryInput!, signal),
    enabled: Boolean(queryInput),
    refetchOnWindowFocus: false,
    placeholderData: (previous) => previous,
  })

  const plans = data?.plans ?? []
  const sectionClassName = embedded
    ? 'rounded-xl border border-slate-200/20 bg-slate-950/30 p-5'
    : 'gobii-card-base p-5'
  const mutedClassName = embedded ? 'text-slate-400' : 'text-slate-500'
  const titleClassName = embedded ? 'text-slate-50' : 'text-slate-900'
  const itemClassName = embedded
    ? 'rounded-lg border border-slate-200/15 bg-slate-950/25 p-4'
    : 'rounded-lg border border-slate-200/70 bg-white/70 p-4'

  return (
    <section className={sectionClassName}>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className={`text-xs font-semibold uppercase tracking-wide ${mutedClassName}`}>Where credits went</div>
          <h2 className={`mt-1 text-lg font-semibold ${titleClassName}`}>Work runs</h2>
        </div>
        <Activity className={embedded ? 'text-slate-400' : 'text-slate-500'} size={18} aria-hidden="true" />
      </div>

      {isPending ? (
        <p className={`text-sm ${mutedClassName}`}>Loading work usage...</p>
      ) : isError ? (
        <p className="text-sm text-rose-500">
          {error instanceof Error ? error.message : 'Unable to load work usage.'}
        </p>
      ) : plans.length === 0 ? (
        <p className={`text-sm ${mutedClassName}`}>No plan-attributed work in this range.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {plans.map((plan) => (
            <article key={plan.id} className={itemClassName}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className={`flex items-center gap-2 text-xs font-semibold uppercase tracking-wide ${mutedClassName}`}>
                    {statusIcon(plan.status)}
                    <span>{plan.status}</span>
                    <span>{plan.agentName}</span>
                  </div>
                  <h3 className={`mt-1 truncate text-sm font-semibold ${titleClassName}`}>
                    {plan.steps[0]?.title || 'Untitled work run'}
                  </h3>
                </div>
                <div className={`shrink-0 text-right text-sm font-semibold ${titleClassName}`}>
                  {formatCredits(plan.creditsUsed)} credits
                </div>
              </div>
              {plan.steps.length > 0 ? (
                <div className="mt-3 flex flex-col gap-2">
                  {plan.steps.slice(0, 4).map((step) => (
                    <div key={step.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 text-xs">
                      <span className={`truncate ${embedded ? 'text-slate-300' : 'text-slate-700'}`}>{step.title}</span>
                      <span className={mutedClassName}>{formatCredits(step.creditsUsed)}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

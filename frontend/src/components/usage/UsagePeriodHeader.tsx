import { UsageRangeControls, type UsageRangeControlsProps } from './UsageRangeControls'
import { UsageAgentSelector, type UsageAgentSelectorProps } from './UsageAgentSelector'
import type { PeriodInfo } from './types'

type UsagePeriodHeaderProps = {
  periodInfo: PeriodInfo
  agentSelectorProps: UsageAgentSelectorProps
  embedded?: boolean
} & UsageRangeControlsProps

export function UsagePeriodHeader({ periodInfo, agentSelectorProps, embedded = false, ...rangeProps }: UsagePeriodHeaderProps) {
  const wrapperClassName = embedded
    ? 'settings-card-surface settings-card-surface--embedded flex flex-wrap items-center gap-4 rounded-xl border border-slate-200/20 px-5 py-4'
    : 'gobii-card-base flex flex-wrap items-center gap-4 px-5 py-4'
  const labelClassName = embedded
    ? 'text-xs font-semibold uppercase tracking-wide text-slate-400'
    : 'text-xs font-semibold uppercase tracking-wide text-slate-500'
  const valueClassName = embedded ? 'text-lg font-medium text-slate-50' : 'text-lg font-medium text-slate-900'
  const captionClassName = embedded ? 'text-xs text-slate-400' : 'text-xs text-slate-500'

  return (
    <div className={wrapperClassName}>
      <div className="flex flex-col">
        <span className={labelClassName}>
          {periodInfo.label}
        </span>
        <span className={valueClassName}>{periodInfo.value}</span>
        <span className={captionClassName}>{periodInfo.caption}</span>
      </div>
      {!embedded ? <div className="hidden h-10 w-px bg-white/50 sm:block" aria-hidden="true" /> : null}
      {!embedded ? <div className="h-px w-full bg-white/60 sm:hidden" aria-hidden="true" /> : null}
      <UsageRangeControls {...rangeProps} embedded={embedded} />
      {!embedded ? <div className="hidden h-10 w-px bg-white/50 sm:block" aria-hidden="true" /> : null}
      {!embedded ? <div className="h-px w-full bg-white/60 sm:hidden" aria-hidden="true" /> : null}
      <div className="w-full sm:w-auto sm:min-w-[10rem]">
        <UsageAgentSelector {...agentSelectorProps} />
      </div>
    </div>
  )
}

export type { UsagePeriodHeaderProps }

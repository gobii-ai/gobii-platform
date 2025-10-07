import { UsageRangeControls, type UsageRangeControlsProps } from './UsageRangeControls'
import { UsageAgentSelector, type UsageAgentSelectorProps } from './UsageAgentSelector'
import type { PeriodInfo } from './types'

type UsagePeriodHeaderProps = {
  periodInfo: PeriodInfo
  agentSelectorProps: UsageAgentSelectorProps
} & UsageRangeControlsProps

export function UsagePeriodHeader({ periodInfo, agentSelectorProps, ...rangeProps }: UsagePeriodHeaderProps) {
  return (
    <div className="flex flex-wrap items-center gap-4 rounded-xl border border-white/60 bg-white/80 px-5 py-4 shadow-xl backdrop-blur-sm">
      <div className="flex flex-col">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {periodInfo.label}
        </span>
        <span className="text-lg font-medium text-slate-900">{periodInfo.value}</span>
        <span className="text-xs text-slate-500">{periodInfo.caption}</span>
      </div>
      <div className="hidden h-10 w-px bg-white/50 sm:block" aria-hidden="true" />
      <div className="h-px w-full bg-white/60 sm:hidden" aria-hidden="true" />
      <UsageRangeControls {...rangeProps} />
      <div className="hidden h-10 w-px bg-white/50 sm:block" aria-hidden="true" />
      <div className="h-px w-full bg-white/60 sm:hidden" aria-hidden="true" />
      <div className="w-full sm:w-auto sm:min-w-[10rem]">
        <UsageAgentSelector {...agentSelectorProps} />
      </div>
    </div>
  )
}

export type { UsagePeriodHeaderProps }

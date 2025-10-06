import { UsageRangeControls, type UsageRangeControlsProps } from './UsageRangeControls'
import type { PeriodInfo } from './types'

type UsagePeriodHeaderProps = {
  periodInfo: PeriodInfo
} & UsageRangeControlsProps

export function UsagePeriodHeader({ periodInfo, ...rangeProps }: UsagePeriodHeaderProps) {
  return (
    <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <div className="flex flex-col">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {periodInfo.label}
        </span>
        <span className="text-lg font-medium text-slate-900">{periodInfo.value}</span>
        <span className="text-xs text-slate-500">{periodInfo.caption}</span>
      </div>
      <div className="h-10 w-px bg-slate-200" aria-hidden="true" />
      <UsageRangeControls {...rangeProps} />
    </div>
  )
}

export type { UsagePeriodHeaderProps }

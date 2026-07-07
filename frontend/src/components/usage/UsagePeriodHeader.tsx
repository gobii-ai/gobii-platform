import { getSettingsSurfaceClassName } from '../common/SettingsSurface'
import { UsageRangeControls, type UsageRangeControlsProps } from './UsageRangeControls'
import { UsageAgentSelector, type UsageAgentSelectorProps } from './UsageAgentSelector'
import type { PeriodInfo } from './types'

type UsagePeriodHeaderProps = {
  periodInfo: PeriodInfo
  agentSelectorProps: UsageAgentSelectorProps
} & UsageRangeControlsProps

export function UsagePeriodHeader({ periodInfo, agentSelectorProps, ...rangeProps }: UsagePeriodHeaderProps) {
  const wrapperClassName = getSettingsSurfaceClassName({
    variant: 'embedded',
    roundedClassName: 'rounded-xl',
    className: 'flex flex-wrap items-center gap-4 px-5 py-4',
  })

  return (
    <div className={wrapperClassName}>
      <div className="flex flex-col">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          {periodInfo.label}
        </span>
        <span className="text-lg font-medium text-slate-50">{periodInfo.value}</span>
        <span className="text-xs text-slate-400">{periodInfo.caption}</span>
      </div>
      <UsageRangeControls {...rangeProps} />
      <div className="w-full sm:w-auto sm:min-w-[10rem]">
        <UsageAgentSelector {...agentSelectorProps} />
      </div>
    </div>
  )
}

export type { UsagePeriodHeaderProps }

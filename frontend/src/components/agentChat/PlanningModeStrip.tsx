import { ListChecks } from 'lucide-react'

type PlanningModeStripProps = {
  canManageAgent?: boolean
  onSkipPlanning?: () => void | Promise<void>
  skipPlanningBusy?: boolean
  className?: string
}

export function PlanningModeStrip({
  canManageAgent = true,
  onSkipPlanning,
  skipPlanningBusy = false,
  className = '',
}: PlanningModeStripProps) {
  const disabled = !canManageAgent || !onSkipPlanning || skipPlanningBusy
  return (
    <div className={`flex flex-wrap items-center justify-between gap-3 bg-white px-4 py-2 text-sm text-slate-700 ${className}`}>
      <span className="inline-flex min-w-0 items-center gap-2 font-medium">
        <ListChecks className="h-4 w-4 shrink-0 text-sky-600" aria-hidden="true" />
        <span className="truncate">Planning mode</span>
      </span>
      <button
        type="button"
        className="inline-flex h-8 shrink-0 items-center justify-center rounded-full border border-sky-200 px-3 text-xs font-semibold text-sky-700 transition hover:border-sky-300 hover:text-sky-800 disabled:cursor-not-allowed disabled:opacity-60"
        onClick={() => void onSkipPlanning?.()}
        disabled={disabled}
        title={canManageAgent ? 'Skip Planning' : 'Only managers can skip planning'}
      >
        {skipPlanningBusy ? 'Skipping...' : 'Skip Planning'}
      </button>
    </div>
  )
}

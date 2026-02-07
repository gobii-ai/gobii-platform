import type { ReactNode } from 'react'

type StagedRowProps = {
  title: ReactNode
  subtitle?: ReactNode
  badge?: ReactNode
  actions?: ReactNode
}

export function StagedRow({ title, subtitle, badge, actions }: StagedRowProps) {
  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-sm font-semibold text-slate-900">{title}</div>
          {badge ? <div className="text-xs font-semibold">{badge}</div> : null}
        </div>
        {subtitle ? <div className="mt-1 text-xs text-slate-600">{subtitle}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}


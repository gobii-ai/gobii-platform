import type { ReactNode } from 'react'

type StatCardProps = {
  label: string
  value: string
  icon?: ReactNode
  hint?: string
}

export function StatCard({ label, value, icon, hint }: StatCardProps) {
  return (
    <div className="gobii-card-base flex items-start gap-3 border border-slate-100/80 px-5 py-4">
      {icon ? <div className="rounded-2xl bg-slate-50 p-3 text-slate-600">{icon}</div> : null}
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
        <p className="text-xl font-semibold text-slate-900/90">{value}</p>
        {hint ? <p className="text-xs text-slate-500">{hint}</p> : null}
      </div>
    </div>
  )
}

import type { ReactNode } from 'react'

type SectionCardProps = {
  title: string
  description?: string
  actions?: ReactNode
  children: ReactNode
  footer?: ReactNode
  className?: string
}

export function SectionCard({ title, description, actions, children, footer, className }: SectionCardProps) {
  return (
    <section className={`gobii-card-base space-y-4 px-6 py-6 ${className ?? ''}`}>
      <div className="flex flex-col gap-2 border-b border-slate-100 pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900/90">{title}</h2>
          {description ? <p className="text-sm text-slate-500">{description}</p> : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      <div className="space-y-4">{children}</div>
      {footer ? <div className="border-t border-slate-100 pt-4 text-sm text-slate-500">{footer}</div> : null}
    </section>
  )
}

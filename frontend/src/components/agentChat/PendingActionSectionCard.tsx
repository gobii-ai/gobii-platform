import type { ReactNode } from 'react'

type PendingActionSectionCardProps = {
  toneClass: string
  title: string
  meta?: string
  children: ReactNode
}

export function PendingActionSectionCard({
  toneClass,
  title,
  meta,
  children,
}: PendingActionSectionCardProps) {
  return (
    <section className={`rounded-2xl border px-3 py-3 ${toneClass}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[0.95rem] font-semibold leading-6 tracking-[-0.02em] text-slate-900">{title}</p>
          {meta ? <p className="mt-0.5 text-xs font-medium uppercase tracking-[0.14em] text-slate-500">{meta}</p> : null}
        </div>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  )
}

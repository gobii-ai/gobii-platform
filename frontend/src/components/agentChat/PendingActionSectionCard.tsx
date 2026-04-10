import type { ReactNode } from 'react'

type PendingActionSectionCardProps = {
  toneClass: string
  title: string
  children: ReactNode
}

export function PendingActionSectionCard({
  toneClass,
  title,
  children,
}: PendingActionSectionCardProps) {
  return (
    <section className={`rounded-2xl border px-3 py-3 ${toneClass}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[0.95rem] font-semibold leading-6 tracking-[-0.02em] text-slate-900">{title}</p>
        </div>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  )
}

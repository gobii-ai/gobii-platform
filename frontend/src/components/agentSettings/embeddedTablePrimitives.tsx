import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Trash2 } from 'lucide-react'

export const embeddedTableWrapperClassName = 'overflow-hidden rounded-xl border border-slate-200/20 bg-slate-950/35'
export const embeddedTableClassName = 'min-w-full divide-y divide-slate-200/15'
export const embeddedTableHeadClassName = 'bg-slate-950/45'
export const embeddedDarkTableHeadClassName = 'bg-slate-900/40'
export const embeddedTableBodyClassName = 'bg-transparent'
export const embeddedDividedTableBodyClassName = 'divide-y divide-slate-200/15 bg-transparent'
export const embeddedTableRowClassName = 'hover:bg-slate-900/30'
export const embeddedTableHeaderCellClassName = 'px-6 py-3 text-left text-xs font-semibold uppercase text-slate-300'
export const embeddedTableCellClassName = 'px-6 py-4 text-sm text-slate-300'
export const embeddedSecondaryActionButtonClassName =
  'inline-flex items-center gap-1 rounded border border-slate-300/70 bg-transparent px-2 py-1 text-xs font-medium text-slate-100 transition-colors hover:border-slate-200 hover:text-white disabled:opacity-50'
export const embeddedDestructiveButtonClassName =
  'inline-flex items-center gap-2 rounded-lg border border-rose-300/25 bg-rose-950/35 px-3 py-1.5 text-xs font-semibold text-rose-200 transition-colors hover:border-rose-200/40 hover:bg-rose-900/50 disabled:opacity-50'
export const embeddedCompactDestructiveButtonClassName =
  'inline-flex items-center gap-1 rounded border border-rose-300/40 bg-rose-950/20 px-2 py-1 text-xs font-medium text-rose-100 transition-colors hover:border-rose-200 hover:bg-rose-900/30'
export const embeddedPromoteActionButtonClassName =
  'inline-flex items-center gap-1 rounded border border-blue-300/40 bg-blue-950/20 px-2 py-1 text-xs font-medium text-blue-100 transition-colors hover:border-blue-200 hover:bg-blue-900/30'
export const embeddedBulkBannerClassName =
  'flex flex-col gap-3 rounded-xl border border-sky-300/20 bg-sky-950/30 px-4 py-3 sm:flex-row sm:items-center sm:justify-between'
export const embeddedBulkButtonClassName =
  'inline-flex items-center justify-center gap-2 rounded-lg border border-sky-300/25 bg-sky-900/55 px-3 py-2 text-sm font-semibold text-sky-100 transition-colors hover:border-sky-200/40 hover:bg-sky-900/75 disabled:opacity-50'

const statusClassNames = {
  active: 'inline-flex rounded-full border border-emerald-300/20 bg-emerald-950/35 px-2.5 py-1 text-xs font-semibold text-emerald-200',
  pending: 'inline-flex items-center gap-1 rounded-full border border-amber-300/20 bg-amber-950/35 px-2.5 py-1 text-xs font-semibold text-amber-200',
  danger: 'inline-flex rounded-full border border-rose-300/20 bg-rose-950/35 px-2.5 py-1 text-xs font-semibold text-rose-200',
}

export function EmbeddedTableFrame({
  children,
}: {
  children: ReactNode
}) {
  return (
    <div className={embeddedTableWrapperClassName}>
      <div className="overflow-x-auto">
        {children}
      </div>
    </div>
  )
}

export function EmbeddedTableHeader({ children }: { children: ReactNode }) {
  return <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{children}</span>
}

export function EmbeddedStatusBadge({
  tone,
  children,
}: {
  tone: keyof typeof statusClassNames
  children: ReactNode
}) {
  return <span className={statusClassNames[tone]}>{children}</span>
}

export function EmbeddedRemoveButton({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={embeddedDestructiveButtonClassName}
    >
      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
      {children}
    </button>
  )
}

export function EmbeddedTableActionButton({
  children,
  icon: Icon,
  disabled,
  onClick,
  title,
  className = embeddedSecondaryActionButtonClassName,
}: {
  children: ReactNode
  icon?: LucideIcon
  disabled?: boolean
  onClick: () => void
  title?: string
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={className}
    >
      {Icon ? <Icon className="h-3 w-3" aria-hidden="true" /> : null}
      {children}
    </button>
  )
}

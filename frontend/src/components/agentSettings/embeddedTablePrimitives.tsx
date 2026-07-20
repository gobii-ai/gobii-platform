import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Trash2 } from 'lucide-react'
import {
  getSettingsActionButtonClassName,
  SettingsStatusBadge,
} from './SettingsControls'

export const embeddedTableWrapperClassName = 'overflow-hidden rounded-xl border border-slate-200/20 bg-slate-950/35'
export const embeddedTableClassName = 'min-w-full divide-y divide-slate-200/15'
export const embeddedDarkTableHeadClassName = 'bg-slate-900/40'
export const embeddedDividedTableBodyClassName = 'divide-y divide-slate-200/15 bg-transparent'
export const embeddedTableRowClassName = 'hover:bg-slate-900/30'
export const embeddedTableHeaderCellClassName = 'px-6 py-3 text-left text-xs font-semibold uppercase text-slate-300'
export const embeddedTableCellClassName = 'px-6 py-4 text-sm text-slate-300'
export const embeddedSecondaryActionButtonClassName =
  getSettingsActionButtonClassName({ size: 'sm', className: 'px-2 py-1' })
export const embeddedDestructiveButtonClassName =
  getSettingsActionButtonClassName({ tone: 'danger', size: 'sm' })
export const embeddedCompactDestructiveButtonClassName =
  getSettingsActionButtonClassName({ tone: 'danger', size: 'sm', className: 'px-2 py-1' })
export const embeddedPromoteActionButtonClassName =
  getSettingsActionButtonClassName({ tone: 'primary', size: 'sm', className: 'px-2 py-1' })
export const embeddedBulkBannerClassName =
  'flex flex-col gap-3 rounded-xl border border-sky-300/20 bg-sky-950/30 px-4 py-3 sm:flex-row sm:items-center sm:justify-between'
export const embeddedBulkButtonClassName =
  getSettingsActionButtonClassName({ tone: 'primary' })

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
  tone: 'active' | 'pending' | 'danger'
  children: ReactNode
}) {
  const settingsTone = tone === 'active' ? 'success' : tone === 'pending' ? 'warning' : 'danger'
  return <SettingsStatusBadge tone={settingsTone}>{children}</SettingsStatusBadge>
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

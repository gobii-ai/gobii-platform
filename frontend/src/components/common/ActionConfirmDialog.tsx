import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Check, Loader2, XCircle } from 'lucide-react'

import { Modal } from './Modal'

type ActionConfirmDialogProps = {
  open: boolean
  title: string
  description?: ReactNode
  children?: ReactNode
  confirmLabel: string
  cancelLabel?: string
  confirmDisabled?: boolean
  busy?: boolean
  danger?: boolean
  icon?: LucideIcon | ReactNode
  onConfirm: () => void
  onClose: () => void
  footerNote?: ReactNode
  localError?: ReactNode
  widthClass?: string
}

const confirmBaseClassName = 'inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-60'
const cancelButtonClassName = 'inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-60'

function isLucideIcon(icon: ActionConfirmDialogProps['icon']): icon is LucideIcon {
  return typeof icon === 'function'
}

export function ActionConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  confirmDisabled = false,
  busy = false,
  danger = false,
  icon,
  onConfirm,
  onClose,
  footerNote,
  localError,
  widthClass = 'sm:max-w-lg',
}: ActionConfirmDialogProps) {
  if (!open) {
    return null
  }

  const confirmClassName = [
    confirmBaseClassName,
    danger ? 'bg-rose-600 hover:bg-rose-700 focus:ring-rose-500' : 'bg-blue-600 hover:bg-blue-700 focus:ring-blue-500',
  ].join(' ')
  const footer = (
    <div className="flex w-full flex-col gap-3 sm:flex-row-reverse sm:items-center sm:justify-between">
      <div className="flex flex-col gap-2 sm:flex-row-reverse sm:items-center">
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy || confirmDisabled}
          className={confirmClassName}
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          {confirmLabel}
        </button>
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          className={cancelButtonClassName}
        >
          {cancelLabel}
        </button>
      </div>
      {footerNote ? <div className="text-xs font-medium text-slate-500">{footerNote}</div> : null}
    </div>
  )
  const Icon = (
    isLucideIcon(icon)
      ? icon
      : icon
        ? (() => <>{icon}</>) as unknown as LucideIcon
        : null
  )

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={footer}
      widthClass={widthClass}
      icon={Icon}
      iconBgClass={danger ? 'bg-red-100' : 'bg-amber-100'}
      iconColorClass={danger ? 'text-red-600' : 'text-amber-700'}
      dismissible={!busy}
    >
      <div className="space-y-3">
        {description ? <div className="text-sm text-slate-600">{description}</div> : null}
        {children}
        {localError ? (
          <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{localError}</span>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}

import type { ConfirmDialogProps } from './types'
import { createPortal } from 'react-dom'
import { Check, Loader2, X } from 'lucide-react'

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancel',
  icon,
  busy = false,
  danger = false,
  onConfirm,
  onClose,
  footerNote,
  children,
}: ConfirmDialogProps) {
  if (!open || typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div className="fixed inset-0 z-50 overflow-y-auto" role="dialog" aria-modal="true">
      <div
        className="fixed inset-0 bg-slate-900/55 backdrop-blur-sm"
        aria-hidden="true"
        onClick={() => (busy ? null : onClose())}
      />
      <div className="flex min-h-full items-start justify-center p-4 pb-20 sm:items-center sm:p-6">
        <div className="relative z-10 w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div className="flex items-start gap-4 px-6 py-5 sm:px-7">
            {icon ? (
              <div className="mt-0.5 grid h-11 w-11 place-items-center rounded-2xl bg-amber-100 text-amber-700">
                {icon}
              </div>
            ) : null}
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
                <button
                  type="button"
                  onClick={onClose}
                  disabled={busy}
                  className="rounded-lg p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
                  aria-label="Close dialog"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
              {description ? <div className="mt-2 text-sm text-slate-600">{description}</div> : null}
            </div>
          </div>

          {children ? <div className="px-6 pb-2 sm:px-7">{children}</div> : null}

          <div className="flex flex-col gap-3 px-6 pb-6 pt-4 sm:flex-row-reverse sm:items-center sm:justify-between sm:px-7">
            <div className="flex flex-col gap-2 sm:flex-row-reverse sm:items-center">
              <button
                type="button"
                onClick={onConfirm}
                disabled={busy}
                className={[
                  'inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-60',
                  danger ? 'bg-rose-600 hover:bg-rose-700 focus:ring-rose-500' : 'bg-blue-600 hover:bg-blue-700 focus:ring-blue-500',
                ].join(' ')}
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                {confirmLabel}
              </button>
              <button
                type="button"
                onClick={onClose}
                disabled={busy}
                className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-60"
              >
                {cancelLabel}
              </button>
            </div>
            {footerNote ? <div className="text-xs font-medium text-slate-500">{footerNote}</div> : null}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}


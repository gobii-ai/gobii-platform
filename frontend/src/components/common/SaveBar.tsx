import { Check, Info, XCircle } from 'lucide-react'

import { sharedSettingsGlassFrameClassName, standaloneSettingsSurfaceClassName } from '../agentSettings/settingsSurfaceClasses'

type SaveBarVariant = 'standalone' | 'embedded'
type SaveBarPlacement = 'fixed' | 'sticky'

export type SaveBarProps = {
  visible: boolean
  onCancel: () => void
  onSave: () => Promise<void> | void
  busy?: boolean
  error?: string | null
  id?: string
  title?: string
  helperText?: string | null
  variant?: SaveBarVariant
  placement?: SaveBarPlacement
  showCancel?: boolean
  cancelLabel?: string
  saveLabel?: string
  busyLabel?: string
  showSaveIcon?: boolean
}

export function SaveBar({
  visible,
  onCancel,
  onSave,
  busy,
  error,
  id,
  title = 'You have unsaved changes',
  helperText = null,
  variant = 'standalone',
  placement = 'fixed',
  showCancel = true,
  cancelLabel = 'Cancel',
  saveLabel = 'Save Changes',
  busyLabel = 'Saving…',
  showSaveIcon = true,
}: SaveBarProps) {
  if (!visible) {
    return null
  }

  const isEmbedded = variant === 'embedded'
  const isFixed = placement === 'fixed'
  const rootClassName = isFixed ? 'fixed inset-x-0 bottom-0 z-40 pointer-events-none' : 'sticky bottom-4 z-20'
  const frameClassName = isFixed ? 'pointer-events-auto mx-auto w-full max-w-5xl px-4 pb-4' : 'w-full'
  const surfaceClassName = isEmbedded
    ? 'overflow-hidden rounded-2xl bg-slate-900/80 px-4 py-3 text-slate-100 backdrop-blur-xl'
    : `${sharedSettingsGlassFrameClassName} px-4 py-3 shadow-[0_18px_38px_rgba(15,23,42,0.18)] ${standaloneSettingsSurfaceClassName}`
  const copyClassName = isEmbedded ? 'text-sm text-slate-200' : 'text-sm text-gray-700'
  const helperClassName = isEmbedded ? 'mt-1 text-xs text-slate-300' : 'mt-1 text-xs text-gray-500'
  const errorClassName = isEmbedded ? 'mt-1 flex items-center gap-2 text-xs text-rose-300' : 'mt-1 flex items-center gap-2 text-xs text-red-600'
  const iconClassName = isEmbedded ? 'h-4 w-4 text-violet-200' : 'h-4 w-4 text-blue-600'
  const actionRowClassName = 'flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center'
  const cancelButtonClassName = isEmbedded
    ? 'inline-flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200/25 bg-slate-900/35 px-3 py-2 text-sm font-medium text-slate-100 transition-colors hover:border-slate-100/35 hover:bg-slate-900/55 hover:text-white sm:w-auto'
    : 'inline-flex w-full items-center justify-center gap-2 rounded-xl border border-gray-200/70 bg-white/80 px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-white sm:w-auto'
  const saveButtonClassName = isEmbedded
    ? 'inline-flex w-full items-center justify-center gap-2 rounded-xl border border-violet-300/25 bg-violet-500/20 px-4 py-2 text-sm font-medium text-violet-50 transition-colors hover:bg-violet-500/30 focus:outline-none focus:ring-2 focus:ring-violet-300/35 focus:ring-offset-0 disabled:opacity-60 sm:w-auto'
    : 'inline-flex w-full items-center justify-center gap-2 rounded-xl border border-transparent bg-blue-600/95 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60 sm:w-auto'

  return (
    <div id={id} className={rootClassName}>
      <div className={frameClassName}>
        <div className={surfaceClassName}>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className={copyClassName}>
              <div className="flex items-center gap-2">
                <Info className={iconClassName} aria-hidden="true" />
                <span className="font-medium">{title}</span>
              </div>
              {error ? (
                <div className={errorClassName}>
                  <XCircle className="h-4 w-4" aria-hidden="true" />
                  <span>{error}</span>
                </div>
              ) : helperText ? (
                <p className={helperClassName}>{helperText}</p>
              ) : null}
            </div>
            <div className={actionRowClassName}>
              {showCancel ? (
                <button
                  type="button"
                  onClick={onCancel}
                  className={cancelButtonClassName}
                >
                  {cancelLabel}
                </button>
              ) : null}
              <button
                type="button"
                onClick={onSave}
                disabled={busy}
                className={saveButtonClassName}
              >
                {showSaveIcon ? <Check className="h-4 w-4" aria-hidden="true" /> : null}
                {busy ? busyLabel : saveLabel}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

import { useEffect, useState, type FormEvent } from 'react'
import { Loader2, type LucideIcon } from 'lucide-react'

import { ImmersiveDialog } from './ImmersiveDialog'

type TextareaSubmitDialogProps = {
  open: boolean
  title: string
  subtitle: string
  icon: LucideIcon
  textareaId: string
  label: string
  placeholder: string
  maxLength: number
  minHeightClassName?: string
  valueResetKey?: string | null
  busy?: boolean
  error?: string | null
  successMessage?: string | null
  submitLabel: string
  busyLabel: string
  submitDisabledWhenEmpty?: boolean
  autoFocus?: boolean
  onClose: () => void
  onSubmit: (message: string) => void | Promise<void>
  onErrorClear?: () => void
}

export function TextareaSubmitDialog({
  open,
  title,
  subtitle,
  icon,
  textareaId,
  label,
  placeholder,
  maxLength,
  minHeightClassName = 'min-h-28',
  valueResetKey = null,
  busy = false,
  error = null,
  successMessage = null,
  submitLabel,
  busyLabel,
  submitDisabledWhenEmpty = false,
  autoFocus = false,
  onClose,
  onSubmit,
  onErrorClear,
}: TextareaSubmitDialogProps) {
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (open) {
      setMessage('')
    }
  }, [open, valueResetKey])

  if (!open) {
    return null
  }

  const trimmedMessage = message.trim()
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void onSubmit(trimmedMessage)
  }

  return (
    <ImmersiveDialog
      open={open}
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      icon={icon}
      desktopIconBgClass="bg-white"
      desktopIconColorClass="text-slate-700"
      desktopWidthClass="sm:max-w-lg"
      dismissible={!busy}
    >
      {successMessage ? (
        <div className="space-y-4">
          <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700">
            {successMessage}
          </p>
          <div className="flex justify-end">
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white transition hover:bg-slate-800"
              onClick={onClose}
            >
              Done
            </button>
          </div>
        </div>
      ) : (
        <form className="space-y-4" onSubmit={handleSubmit}>
          <label className="block text-sm font-medium text-slate-800" htmlFor={textareaId}>
            {label}
            <textarea
              id={textareaId}
              value={message}
              onChange={(event) => {
                setMessage(event.currentTarget.value.slice(0, maxLength))
                onErrorClear?.()
              }}
              className={`mt-2 block w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-6 text-slate-800 shadow-none outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-200 ${minHeightClassName}`}
              placeholder={placeholder}
              disabled={busy}
              autoFocus={autoFocus}
            />
          </label>
          <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
            <span>{message.length}/{maxLength}</span>
            {error ? <span className="font-medium text-rose-600">{error}</span> : null}
          </div>
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={busy || (submitDisabledWhenEmpty && !trimmedMessage)}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
              <span>{busy ? busyLabel : submitLabel}</span>
            </button>
          </div>
        </form>
      )}
    </ImmersiveDialog>
  )
}

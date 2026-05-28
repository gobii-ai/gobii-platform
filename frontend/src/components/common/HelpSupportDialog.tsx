import { useEffect, useState, type FormEvent } from 'react'
import { CircleHelp, Loader2 } from 'lucide-react'

import { HttpError } from '../../api/http'
import { sendAppSupportRequest } from '../../api/support'
import type { ConsoleContext } from '../../api/context'
import { Modal } from './Modal'

const SUPPORT_MESSAGE_MAX_LENGTH = 4000

type HelpSupportDialogProps = {
  open: boolean
  onClose: () => void
  agentId?: string | null
  agentName?: string | null
  workspaceContext?: ConsoleContext | null
}

function getSupportErrorMessage(error: unknown): string {
  if (error instanceof HttpError) {
    const body = error.body
    if (
      body
      && typeof body === 'object'
      && 'message' in body
      && typeof body.message === 'string'
      && body.message.trim()
    ) {
      return body.message
    }
  }
  return 'Unable to send your message. Please try again.'
}

export function HelpSupportDialog({
  open,
  onClose,
  agentId = null,
  agentName = null,
  workspaceContext = null,
}: HelpSupportDialogProps) {
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    if (open) {
      setMessage('')
      setError(null)
      setSubmitted(false)
      setBusy(false)
    }
  }, [open])

  if (!open) {
    return null
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmedMessage = message.trim()
    if (!trimmedMessage) {
      setError('Message is required.')
      return
    }

    setBusy(true)
    setError(null)
    try {
      await sendAppSupportRequest({
        message: trimmedMessage,
        pageUrl: typeof window === 'undefined' ? undefined : window.location.href,
        agentId,
        agentName,
        workspaceContext,
      })
      setSubmitted(true)
    } catch (err) {
      setError(getSupportErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Contact support"
      subtitle="Send us a note and we'll follow up by email."
      onClose={onClose}
      icon={CircleHelp}
      iconBgClass="bg-white"
      iconColorClass="text-slate-700"
      widthClass="sm:max-w-lg"
      dismissible={!busy}
    >
      {submitted ? (
        <div className="space-y-4">
          <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700">
            Thanks, we'll follow up by email.
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
          <label className="block text-sm font-medium text-slate-800" htmlFor="app-support-message">
            How can we help?
            <textarea
              id="app-support-message"
              value={message}
              onChange={(event) => {
                setMessage(event.currentTarget.value.slice(0, SUPPORT_MESSAGE_MAX_LENGTH))
                if (error) {
                  setError(null)
                }
              }}
              className="mt-2 block min-h-32 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-6 text-slate-800 shadow-none outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-200"
              placeholder="Tell us what happened or what you need help with."
              disabled={busy}
              autoFocus
            />
          </label>
          <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
            <span>{message.length}/{SUPPORT_MESSAGE_MAX_LENGTH}</span>
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
              disabled={busy || !message.trim()}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
              <span>{busy ? 'Sending...' : 'Send message'}</span>
            </button>
          </div>
        </form>
      )}
    </Modal>
  )
}

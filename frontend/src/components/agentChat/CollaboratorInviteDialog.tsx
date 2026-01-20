import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { Mail, X } from 'lucide-react'

import { getCsrfToken } from '../../api/http'

type CollaboratorInviteDialogProps = {
  open: boolean
  agentName?: string | null
  inviteUrl?: string | null
  canManage?: boolean
  onClose: () => void
}

export function CollaboratorInviteDialog({
  open,
  agentName,
  inviteUrl,
  canManage = true,
  onClose,
}: CollaboratorInviteDialogProps) {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const displayName = useMemo(() => (agentName || '').trim() || 'this agent', [agentName])
  const canInvite = Boolean(inviteUrl && canManage)

  useEffect(() => {
    if (!open) {
      return
    }
    setEmail('')
    setError(null)
    setSuccess(null)
  }, [open])

  useEffect(() => {
    if (!open || typeof document === 'undefined') {
      return undefined
    }
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('keydown', handleKey)
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKey)
      document.body.style.overflow = originalOverflow
    }
  }, [open, onClose])

  if (!open || typeof document === 'undefined') {
    return null
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmedEmail = email.trim().toLowerCase()
    if (!trimmedEmail) {
      setError('Enter an email address to continue.')
      return
    }
    if (!inviteUrl) {
      setError('Collaboration invites are unavailable right now.')
      return
    }
    if (!canManage) {
      setError('Only owners and organization admins can invite collaborators.')
      return
    }

    setBusy(true)
    setError(null)
    setSuccess(null)
    try {
      const csrfToken = getCsrfToken()
      const formData = new FormData()
      formData.append('action', 'add_collaborator')
      formData.append('email', trimmedEmail)
      if (csrfToken) {
        formData.append('csrfmiddlewaretoken', csrfToken)
      }
      const response = await fetch(inviteUrl, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
        },
        body: formData,
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || 'Unable to send invite. Please try again.')
      }
      setSuccess(`Invite sent to ${trimmedEmail}.`)
      setEmail('')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to send invite. Please try again.'
      setError(message)
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center px-4 pb-6 pt-8 sm:items-center sm:px-6">
      <div
        className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm"
        onClick={onClose}
        role="presentation"
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Invite collaborators"
        className="relative z-10 w-full max-w-lg overflow-hidden rounded-t-3xl bg-white shadow-xl sm:rounded-3xl"
      >
        <div className="flex items-start justify-between gap-4 bg-gradient-to-r from-sky-600 to-emerald-500 px-5 py-4 text-white">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-white/80">Collaborate</p>
            <h2 className="text-lg font-semibold">Invite someone to {displayName}</h2>
          </div>
          <button
            type="button"
            className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-white/20 text-white transition hover:bg-white/30"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        <div className="px-5 py-5">
          <p className="text-sm text-slate-600">
            Collaborators can chat with {displayName} and access shared files. They cannot change agent settings or
            billing.
          </p>
          {!canManage && (
            <p className="mt-3 text-sm text-amber-700">
              Only owners and organization admins can invite collaborators.
            </p>
          )}
          <form className="mt-4 space-y-3" onSubmit={handleSubmit}>
            <label className="text-xs font-semibold uppercase tracking-wide text-slate-500" htmlFor="collaborator-email">
              Collaborator email
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <Mail className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
                <input
                  id="collaborator-email"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.currentTarget.value)}
                  placeholder="name@company.com"
                  autoComplete="email"
                  disabled={!canManage || !inviteUrl || busy}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 pl-9 text-sm text-slate-700 focus:border-sky-500 focus:ring-sky-500 disabled:cursor-not-allowed disabled:bg-white"
                />
              </div>
              <button
                type="submit"
                disabled={!canInvite || !email.trim() || busy}
                className="inline-flex items-center justify-center rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy ? 'Sending...' : 'Send invite'}
              </button>
            </div>
            {error ? <p className="text-sm text-rose-600">{error}</p> : null}
            {success ? <p className="text-sm text-emerald-600">{success}</p> : null}
          </form>
        </div>
      </div>
    </div>,
    document.body,
  )
}

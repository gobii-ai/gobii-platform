import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Mail, UserPlus } from 'lucide-react'

import { getCsrfToken } from '../../api/http'
import { ImmersiveDialog } from '../common/ImmersiveDialog'

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
  const subtitle = 'Collaborators can view and send messages with this agent.'
  const title = `Invite someone to collaborate with ${displayName}`

  useEffect(() => {
    if (!open) {
      return
    }
    setEmail('')
    setError(null)
    setSuccess(null)
  }, [open])

  if (!open) {
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
        credentials: 'same-origin',
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

  const body = (
    <>
      {!canManage && (
        <p className="text-sm text-amber-700">
          Only owners and organization admins can invite collaborators.
        </p>
      )}
      <form className="space-y-3" onSubmit={handleSubmit}>
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
              className="w-full rounded-lg border border-slate-200 px-3 py-2 pl-9 text-sm text-slate-700 focus:border-emerald-500 focus:ring-emerald-500 disabled:cursor-not-allowed disabled:bg-white"
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
    </>
  )

  return (
    <ImmersiveDialog
      open={open}
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      icon={UserPlus}
      ariaLabel={title}
      desktopIconBgClass="bg-emerald-100"
      desktopIconColorClass="text-emerald-600"
      desktopWidthClass="sm:max-w-lg"
      desktopBodyClassName="space-y-4"
      mobileChildren={<div className="space-y-4">{body}</div>}
    >
      {body}
    </ImmersiveDialog>
  )
}
